import json
import os
from pathlib import Path
import ssl
import unittest
from unittest.mock import MagicMock, Mock, patch

from tkmonitor.model import MonitorError, SourceError
from tkmonitor.network import NetworkResponse, NetworkTransport, _addresses_from_dns, load_network_config
from tkmonitor.sources import validate_url


DNS_URL = "https://dns.google/resolve"
HOST = "www.tiktok.com"
URL = f"https://{HOST}/dramas?private_query=keep_private"
PUBLIC = "93.184.216.34"
ALTERNATE = "1.2.3.4"


def answer(name, kind, data, period=30):
    return {"name": name, "type": kind, "data": data, "TTL": period}


def dns_payload(*answers):
    return json.dumps({"Status": 0, "TC": False, "Question": [{"name": HOST + ".", "type": 1}],
                       "Answer": list(answers)}).encode()


class ConfigTests(unittest.TestCase):
    def test_absent_config_does_not_enable_optional_transport(self):
        with patch.dict("os.environ", {"TKMONITOR_NETWORK_CONFIG": ""}), patch.object(Path, "exists", return_value=False):
            self.assertIsNone(load_network_config(Path("project")))

    def test_shared_config_and_explicit_override(self):
        config = {"mode": "proxy", "proxy": "http://localhost:7897", "dns_url": DNS_URL, "browser_fallback": True}
        with patch.dict(os.environ, {"TKMONITOR_NETWORK_CONFIG": ""}), patch.object(Path, "exists", return_value=True), \
                patch("tkmonitor.network.read_json", return_value=config) as read:
            root = Path("project")
            network = load_network_config(root)
            read.assert_called_once_with(root / "network.local.json")
            self.assertEqual((network.proxy_host, network.proxy_port, network.dns_url), ("127.0.0.1", 7897, DNS_URL))
            self.assertTrue(network.browser_fallback)
            requested = root / "test-settings.local.json"
            with patch.dict("os.environ", {"TKMONITOR_NETWORK_CONFIG": str(requested)}):
                self.assertTrue(load_network_config(root).browser_fallback)
                read.assert_called_with(requested)
                read.side_effect = MonitorError("Could not read config")
                with self.assertRaises(MonitorError):
                    load_network_config(root)

    def test_rejects_remote_proxies_credentials_unknown_dns_and_bad_bool(self):
        candidates = (
            {"proxy": "http://203.0.113.1:9999"},
            {"proxy": "http://secret@127.0.0.1:7897"},
            {"proxy": "http://127.0.0.1:0"},
            {"dns_url": "https://example.org/resolve"},
            {"browser_fallback": "true"},
        )
        with patch.dict(os.environ, {"TKMONITOR_NETWORK_CONFIG": ""}), patch.object(Path, "exists", return_value=True), \
                patch("tkmonitor.network.read_json") as read:
            for invalid in candidates:
                with self.subTest(invalid=invalid):
                    read.return_value = {"mode": "proxy", "proxy": "http://localhost:7897", "dns_url": DNS_URL, **invalid}
                    with self.assertRaises(MonitorError) as result:
                        load_network_config(Path("project"))
                    self.assertNotIn("secret", str(result.exception))


class DnsTests(unittest.TestCase):
    def test_checks_alias_chain_even_if_out_of_order_and_ignores_unrelated_records(self):
        payload = dns_payload(answer("end.example.", 1, PUBLIC), answer("middle.example.", 5, "end.example.", 7),
                              answer(HOST + ".", 5, "middle.example."), answer("other.example.", 1, "0.0.0.0"))
        self.assertEqual(_addresses_from_dns(payload, HOST), ((PUBLIC,), 7))

    def test_rejects_nonpublic_addresses_even_if_dns_also_returned_public(self):
        for blocked in ("198.18.0.36", "127.0.0.1", "10.1.2.3", "224.0.0.1"):
            with self.subTest(blocked=blocked):
                payload = dns_payload(answer(HOST, 1, PUBLIC), answer(HOST, 1, blocked))
                with self.assertRaises(SourceError) as result:
                    _addresses_from_dns(payload, HOST)
                self.assertEqual(result.exception.code, "DNS_PUBLIC_UNAVAILABLE")


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.network = NetworkTransport("127.0.0.1", 7897, DNS_URL)

    def get(self, headers=None):
        return self.network.get(URL, headers or {}, 12, validate_host=validate_url, max_bytes=15)

    def test_uses_checked_address_with_original_tls_host_and_never_sends_private_headers_to_dns(self):
        with patch.object(self.network, "_request") as request:
            request.side_effect = [NetworkResponse(200, {}, dns_payload(answer(HOST, 1, PUBLIC)), DNS_URL),
                                   NetworkResponse(200, {}, b"ok", URL)]
            self.assertEqual(self.get({"X-Private": "testing"}).body, b"ok")
            resolver, request_site = [call.args for call in request.call_args_list]
            self.assertIn("name=www.tiktok.com", resolver[0])
            self.assertEqual(resolver[1:4], ("dns.google", "8.8.8.8", {"Accept": "application/dns-json"}))
            self.assertEqual(request_site[:4], (URL, HOST, PUBLIC, {"X-Private": "testing"}))

    def test_uses_literal_address_in_connect_and_original_host_in_tls_and_get(self):
        with patch("tkmonitor.network.HTTPConnection") as make, patch.object(self.network, "_tls") as tls:
            connection = make.return_value
            socket = Mock()
            connection.sock = socket
            response = Mock(status=200)
            response.getheaders.return_value = [("Content-Type", "application/json")]
            response.read.return_value = b"ok"
            connection.getresponse.return_value = MagicMock()
            connection.getresponse.return_value.__enter__.return_value = response
            result = self.network._request(URL, HOST, PUBLIC, {"Host": "ignored", "Proxy-Authorization": "ignored"}, 5, 15)
            self.assertEqual(result.body, b"ok")
            make.assert_called_once_with("127.0.0.1", 7897, timeout=5)
            connection.set_tunnel.assert_called_once_with(PUBLIC, 443, headers={"Host": PUBLIC + ":443"})
            tls.wrap_socket.assert_called_once_with(socket, server_hostname=HOST)
            connection.request.assert_called_once_with("GET", "/dramas?private_query=keep_private",
                                                       headers={"Host": HOST, "Connection": "close"})
            connection.close.assert_called_once_with()

    def test_reuses_cached_resolution_for_same_host_redirect_and_rejects_cross_host(self):
        same_host = f"https://{HOST}/next"
        with patch.object(self.network, "_request") as request:
            request.side_effect = [NetworkResponse(200, {}, dns_payload(answer(HOST, 1, PUBLIC)), DNS_URL),
                                   NetworkResponse(302, {"Location": "/next"}, b"", URL),
                                   NetworkResponse(302, {"Location": "https://other.tiktok.com/next"}, b"", same_host)]
            with self.assertRaisesRegex(MonitorError, "changed hostname"):
                self.get()
            self.assertEqual([call.args[0] for call in request.call_args_list][1:], [URL, same_host])

    def test_tries_another_checked_address_after_connection_failure(self):
        with patch.object(self.network, "_resolve", return_value=(PUBLIC, ALTERNATE)), patch.object(self.network, "_request") as req:
            req.side_effect = [SourceError("temporary", "PROXY_UNAVAILABLE"), NetworkResponse(200, {}, b"valid", URL)]
            self.assertEqual(self.get().body, b"valid")
            self.assertEqual([item.args[2] for item in req.call_args_list], [PUBLIC, ALTERNATE])

    def test_certificate_failure_never_sends_origin_headers(self):
        with patch("tkmonitor.network.HTTPConnection") as make, patch.object(self.network, "_tls") as tls:
            tls.wrap_socket.side_effect = ssl.SSLCertVerificationError("private diagnostic")
            with self.assertRaises(SourceError) as result:
                self.network._request(URL, HOST, PUBLIC, {}, 5, 15)
            self.assertEqual(result.exception.code, "TLS_INVALID")
            self.assertNotIn("private diagnostic", str(result.exception))
            make.return_value.request.assert_not_called()

    def test_tunnel_error_is_distinct_from_origin_authentication(self):
        with patch("tkmonitor.network.HTTPConnection") as make:
            make.return_value.connect.side_effect = OSError("Tunnel connection failed: 407 secret")
            with self.assertRaises(SourceError) as result:
                self.network._request(URL, HOST, PUBLIC, {}, 5, 15)
            self.assertEqual(result.exception.code, "PROXY_UNAVAILABLE")
            self.assertNotIn("secret", str(result.exception))
            make.return_value.request.assert_not_called()

    def test_response_larger_than_limit_is_rejected(self):
        with patch("tkmonitor.network.HTTPConnection") as make, patch.object(self.network, "_tls"):
            response = make.return_value.getresponse.return_value.__enter__.return_value
            response.status = 200
            response.getheaders.return_value = []
            response.read.return_value = b"x" * 16
            with self.assertRaisesRegex(MonitorError, "safe size limit"):
                self.network._request(URL, HOST, PUBLIC, {}, 5, 15)
            response.read.assert_called_once_with(16)
