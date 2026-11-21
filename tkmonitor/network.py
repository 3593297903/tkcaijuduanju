"""Optional project-local HTTPS transport over an explicit loopback proxy."""

from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException
import ipaddress
import json
import os
from pathlib import Path
import ssl
from threading import Lock
import time
from urllib.parse import urlencode, urljoin, urlsplit

from .config import read_json
from .model import MonitorError, SourceError


_DNS_SERVERS = {
    "https://dns.google/resolve": ("8.8.8.8", "8.8.4.4"),
    "https://cloudflare-dns.com/dns-query": ("1.1.1.1", "1.0.0.1"),
}
_FOLLOW_REDIRECTS = {301, 302, 303, 307, 308}
_DNS_MAX_BYTES = 64 * 1024
_DNS_TTL_LIMIT = 300
_ADDRESS_ATTEMPTS = 3


@dataclass(frozen=True)
class NetworkResponse:
    status: int
    headers: dict
    body: bytes
    url: str


def load_network_config(project_root):
    override = os.environ.get("TKMONITOR_NETWORK_CONFIG")
    path = Path(override) if override else Path(project_root) / "network.local.json"
    if not override and not path.exists():
        return None
    data = read_json(path)
    allowed_keys = {"mode", "proxy", "dns_url", "browser_fallback"}
    if not isinstance(data, dict) or set(data) - allowed_keys or data.get("mode") != "proxy":
        raise MonitorError("The project network config must select proxy mode and use supported keys.")
    proxy = data.get("proxy")
    if not isinstance(proxy, str):
        raise MonitorError("Configure an HTTP loopback proxy with an explicit port.")
    try:
        endpoint = urlsplit(proxy)
        port = endpoint.port
        host = endpoint.hostname
        # Use a literal loopback socket address even when the local config says localhost.
        host = "127.0.0.1" if host == "localhost" else host
        address = ipaddress.ip_address(host)
    except (ValueError, TypeError) as exc:
        raise MonitorError("Configure an HTTP loopback proxy with an explicit port.") from exc
    if (endpoint.scheme != "http" or port is None or port == 0 or not address.is_loopback
            or endpoint.username is not None or endpoint.password is not None or endpoint.path not in ("", "/")
            or endpoint.query or endpoint.fragment):
        raise MonitorError("Configure an HTTP loopback proxy with an explicit port.")
    dns_url = data.get("dns_url")
    if not isinstance(dns_url, str) or dns_url not in _DNS_SERVERS:
        raise MonitorError("Configure a supported DNS over HTTPS endpoint for the project.")
    browser_fallback = data.get("browser_fallback", False)
    if type(browser_fallback) is not bool:
        raise MonitorError("Project network browser_fallback must be true or false.")
    return NetworkTransport(str(address), port, dns_url, browser_fallback)


def _dns_error():
    return SourceError("Project DNS could not verify a globally routable IPv4 address.", "DNS_PUBLIC_UNAVAILABLE")


def _addresses_from_dns(body, host):
    try:
        data = json.loads(body)
        question = data["Question"]
        answers = data["Answer"]
        if (type(data["Status"]) is not int or data["Status"] != 0 or data.get("TC", False) is not False or not isinstance(question, list)
                or len(question) != 1 or question[0]["name"].rstrip(".").lower() != host
                or question[0]["type"] != 1 or not isinstance(answers, list)):
            raise ValueError
        aliases = {}
        for answer in answers:
            if answer["type"] == 5:
                name = answer["name"].rstrip(".").lower()
                alias = answer["data"].rstrip(".").lower()
                if not alias or aliases.setdefault(name, alias) != alias:
                    raise ValueError
        names = {host}
        canonical = host
        while canonical in aliases:
            canonical = aliases[canonical]
            if canonical in names:
                raise ValueError
            names.add(canonical)
        addresses = []
        ttl = _DNS_TTL_LIMIT
        for answer in answers:
            kind = answer["type"]
            if kind not in (1, 5) or answer["name"].rstrip(".").lower() not in names:
                continue
            period = answer["TTL"]
            if type(period) is not int or period < 0:
                raise ValueError
            ttl = min(ttl, period)
            if kind == 1:
                if not isinstance(answer["data"], str):
                    raise ValueError
                address = ipaddress.IPv4Address(answer["data"])
                if not address.is_global or address.is_multicast:
                    raise ValueError
                addresses.append(str(address))
        if not addresses:
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise _dns_error() from exc
    return tuple(dict.fromkeys(addresses)), ttl


class NetworkTransport:
    """Tie each original HTTPS hostname to the public IPv4 address tunneled by the proxy."""

    def __init__(self, proxy_host, proxy_port, dns_url, browser_fallback=False):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.dns_url = dns_url
        self.browser_fallback = browser_fallback
        self._tls = ssl.create_default_context()
        self._dns_cache = {}
        self._cache_lock = Lock()

    def _request(self, url, host, address, headers, timeout, maximum):
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname != host or parts.port not in (None, 443)
                or parts.username is not None or parts.password is not None or parts.fragment):
            raise MonitorError("Project network requires a valid HTTPS URL for the selected hostname.")
        if (url != url.strip() or any(ord(char) < 32 or ord(char) == 127 for char in url)
                or parts.path.startswith("//")):
            raise MonitorError("Project network requires a valid HTTPS URL for the selected hostname.")
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        blocked_headers = {"host", "proxy-authorization", "proxy-connection", "connection", "transfer-encoding",
                           "content-length", "upgrade", "te", "trailer", "keep-alive"}
        request_headers = {key: value for key, value in headers.items() if key.lower() not in blocked_headers}
        request_headers["Host"] = host
        request_headers["Connection"] = "close"
        connection = HTTPConnection(self.proxy_host, self.proxy_port, timeout=timeout)
        connection.set_tunnel(address, 443, headers={"Host": f"{address}:443"})
        try:
            try:
                connection.connect()
            except (HTTPException, OSError) as exc:
                raise SourceError("Could not establish the configured project proxy tunnel.", "PROXY_UNAVAILABLE") from exc
            try:
                connection.sock = self._tls.wrap_socket(connection.sock, server_hostname=host)
            except ssl.SSLCertVerificationError as exc:
                raise SourceError("HTTPS certificate verification failed on the project route.", "TLS_INVALID") from exc
            connection.request("GET", target, headers=request_headers)
            with connection.getresponse() as response:
                result_headers = dict(response.getheaders())
                body = b"" if response.status in _FOLLOW_REDIRECTS else response.read(maximum + 1)
                if len(body) > maximum:
                    raise MonitorError("HTTP response exceeds the safe size limit.")
                return NetworkResponse(response.status, result_headers, body, url)
        except (HTTPException, OSError) as exc:
            raise SourceError("The project HTTPS request failed or timed out.", "NETWORK_ERROR") from exc
        except (ValueError, UnicodeError) as exc:
            raise MonitorError("The project HTTPS request has an invalid target or headers.") from exc
        finally:
            connection.close()

    def _resolve(self, host, timeout):
        with self._cache_lock:
            cached = self._dns_cache.get(host)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        dns_host = urlsplit(self.dns_url).hostname
        query = urlencode({"name": host, "type": "A"})
        error = _dns_error()
        for address in _DNS_SERVERS[self.dns_url]:
            try:
                response = self._request(self.dns_url + "?" + query, dns_host, address,
                                         {"Accept": "application/dns-json"}, min(timeout, 8), _DNS_MAX_BYTES)
                if response.status != 200:
                    continue
                addresses, ttl = _addresses_from_dns(response.body, host)
                with self._cache_lock:
                    self._dns_cache[host] = (time.monotonic() + ttl, addresses)
                return addresses
            except SourceError as exc:
                error = exc
        raise error

    def _get_resolved(self, url, host, headers, timeout, max_bytes):
        addresses = self._resolve(host, timeout)[:_ADDRESS_ATTEMPTS]
        deadline = time.monotonic() + timeout
        for index, address in enumerate(addresses):
            remaining = deadline - time.monotonic()
            per_address_timeout = max(0.01, remaining / (len(addresses) - index))
            try:
                response = self._request(url, host, address, headers, per_address_timeout, max_bytes)
            except SourceError as exc:
                if (exc.code not in {"PROXY_UNAVAILABLE", "NETWORK_ERROR"}
                        or index == len(addresses) - 1 or time.monotonic() >= deadline):
                    raise
                continue
            if index:
                with self._cache_lock:
                    cached = self._dns_cache.get(host)
                    if cached and address in cached[1]:
                        self._dns_cache[host] = (cached[0], (address,) + tuple(x for x in cached[1] if x != address))
            return response

    def get(self, url, headers, timeout, *, validate_host, max_bytes):
        host = validate_host(url)
        seen = {url}
        for hop in range(6):
            response = self._get_resolved(url, host, headers, timeout, max_bytes)
            if response.status not in _FOLLOW_REDIRECTS:
                return response
            if hop == 5:
                break
            next_url = next((value for key, value in response.headers.items() if key.lower() == "location"), None)
            if not next_url:
                raise MonitorError("HTTPS redirect did not include a target.")
            url = urljoin(url, next_url)
            if validate_host(url) != host or url in seen:
                raise MonitorError("HTTPS redirect changed hostname or repeated a previous target.")
            seen.add(url)
        raise MonitorError("HTTPS redirect exceeded the project redirect limit.")
