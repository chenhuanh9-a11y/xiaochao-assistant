"""Real urllib redirect processing with an entirely in-memory HTTP transport."""
from email.message import Message
import hashlib
import io
from pathlib import Path
import sys
import unittest
from unittest import mock
from urllib import request, response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import route_plaza
from account_service import AccountError


class RedirectTests(unittest.TestCase):
    payload = b"synthetic archive"
    origin = "https://fixture.aliyuncs.com/start"

    def fetch(self, routes, *, root="https://api.xingchenmap.cn", url=None):
        self.calls = []
        self.responses = []

        def transport(_handler, req):
            self.calls.append((req.full_url, dict(req.header_items())))
            target = routes.get(req.full_url)
            headers = Message()
            if target is not None:
                headers["Location"] = target
            result = response.addinfourl(io.BytesIO(self.payload), headers, req.full_url,
                                         302 if target is not None else 200)
            self.responses.append(result)
            result.msg = "Found" if target is not None else "OK"
            return result

        # Neither an initial nor a redirected URL can reach a socket. Retain
        # urllib's production redirect/error processing, replacing only transport.
        with mock.patch.object(route_plaza, "api_root", return_value=root), \
                mock.patch.object(request, "_opener", None), \
                mock.patch.object(request.HTTPHandler, "http_open", transport), \
                mock.patch.object(request.HTTPSHandler, "https_open", transport), \
                mock.patch("socket.socket", side_effect=AssertionError("unexpected network")):
            return route_plaza.fetch_archive(url or self.origin, len(self.payload),
                                            hashlib.sha256(self.payload).hexdigest())

    def test_untrusted_redirect_rejected_before_request(self):
        with self.assertRaises(AccountError):
            self.fetch({self.origin: "https://untrusted.invalid/collect"})
        self.assertEqual([url for url, _ in self.calls], [self.origin])
        self.assertTrue(self.responses[0].closed)

    def test_every_redirect_hop_is_checked(self):
        second = "https://second.aliyuncs.com/archive"
        with self.assertRaises(AccountError):
            self.fetch({self.origin: second, second: "https://untrusted.invalid/collect"})
        self.assertEqual([url for url, _ in self.calls], [self.origin, second])

    def test_https_downgrade_rejected_before_request(self):
        with self.assertRaises(AccountError):
            self.fetch({self.origin: "http://fixture.aliyuncs.com/archive"})
        self.assertEqual(len(self.calls), 1)

    def test_allowed_relative_and_cross_host_redirects_keep_download_token_free(self):
        second = "https://fixture.aliyuncs.com/relative"
        third = "https://second.aliyuncs.com/archive"
        self.assertEqual(self.fetch({self.origin: "/relative", second: third}), self.payload)
        self.assertEqual([url for url, _ in self.calls], [self.origin, second, third])
        for _, headers in self.calls:
            self.assertNotIn("authorization", {key.lower() for key in headers})

    def test_local_test_origin_cannot_redirect_to_another_port(self):
        origin = "http://127.0.0.1:12345"
        with self.assertRaises(AccountError):
            self.fetch({origin + "/start": "http://127.0.0.1:12346/archive"},
                       root=origin, url=origin + "/start")
        self.assertEqual(len(self.calls), 1)

    def test_local_test_origin_can_redirect_within_origin(self):
        origin = "http://127.0.0.1:12345"
        self.assertEqual(self.fetch({origin + "/start": "/archive"}, root=origin,
                                    url=origin + "/start"), self.payload)
        self.assertEqual(len(self.calls), 2)

    def test_untrusted_initial_url_never_reaches_transport(self):
        with self.assertRaises(AccountError):
            self.fetch({}, url="https://aliyuncs.com.untrusted.invalid/archive")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
