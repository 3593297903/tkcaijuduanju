"""Exercise the official CLI with a shared stand-in list and cover transport."""

from io import BytesIO
import json
import unittest
from unittest.mock import patch

from PIL import Image

from tkmonitor.model import SourceError, today
from tkmonitor.onboarding import atomic_json
from tkmonitor.sources import HttpResponse

from tests import test_onboarding


class SharedNetworkTests(unittest.TestCase):
    setUp = test_onboarding.OnboardingTests.setUp
    invoke = test_onboarding.OnboardingTests.invoke
    store_count = test_onboarding.OnboardingTests.store_count

    def test_list_and_missing_cover_share_one_configured_transport(self):
        data = test_onboarding.page()
        cover_url = "https://p16-common-sign.tiktokcdn-us.com/image?private=synthetic"
        data["dramaList"][0]["cover"] = cover_url
        image = BytesIO()
        Image.new("RGB", (2, 2)).save(image, format="PNG")
        requests = []

        class Network:
            def get(self, url, headers, timeout, *, validate_host, max_bytes):
                host = validate_host(url)
                requests.append((host, dict(headers)))
                if url == cover_url:
                    return HttpResponse(200, {}, image.getvalue(), url)
                return HttpResponse(200, {"Content-Type": "application/json"}, json.dumps(data).encode(), url)

        with patch("tkmonitor.cli.load_network_config", return_value=Network()) as load:
            code, _ = self.invoke("onboard", "--url", "https://www.tiktok.com/@old", "--finalize")

        self.assertEqual(code, 0, self.stderr)
        load.assert_called_once_with(self.root.resolve())
        self.assertEqual([host for host, _ in requests], ["www.tiktok.com", "p16-common-sign.tiktokcdn-us.com"])
        self.assertNotEqual(requests[0][1], requests[1][1])
        output = self.root / "state/account-reports/old" / today() / "frontend/media_validation.json"
        validation = json.loads(output.read_bytes())
        self.assertEqual(validation["status"], "verified")
        self.assertEqual(validation["downloads"], [{"account": "old", "id": test_onboarding.ONE, "status": "downloaded"}])

    def test_fallback_is_opt_in_and_does_not_treat_dns_as_authentication(self):
        def fail(*_):
            raise SourceError("DNS failed in fixture", "DNS_PUBLIC_UNAVAILABLE")

        code, _ = self.invoke("collect", "--recover-request", transport=fail)
        self.assertEqual(code, 2)
        self.assertIn("DNS_PUBLIC_UNAVAILABLE", self.stderr)
        code, output = self.invoke("collect", "--browser-fallback", transport=fail)
        self.assertEqual(code, 4, self.stderr)
        self.assertEqual(output[-1]["status"], "NEEDS_BROWSER")
        self.assertEqual(self.store_count(), 0)
