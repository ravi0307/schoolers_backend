import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.main import (  # noqa: E402
    HOP_BY_HOP_HEADERS,
    RESPONSE_STRIP_HEADERS,
    forward_headers_from,
    response_headers_from,
)


class GatewayProxyHeaderTests(unittest.TestCase):
    """The gateway proxies every service call through httpx.

    httpx transparently decodes a gzipped upstream body but keeps the original
    Content-Encoding header. Forwarding that header with the already-decoded
    bytes makes browsers try to gunzip plain JSON and fail with
    ERR_CONTENT_DECODING_FAILED -- a 200 that surfaces to the app as a
    "Network Error". These tests pin the header filtering that prevents it.
    """

    def test_client_accept_encoding_is_not_forwarded(self):
        headers = {
            "Accept-Encoding": "gzip, deflate, br",
            "Authorization": "Bearer token",
            "Content-Type": "application/json",
        }
        forwarded = forward_headers_from(headers)
        self.assertNotIn("Accept-Encoding", forwarded)
        self.assertEqual(forwarded["Authorization"], "Bearer token")
        self.assertEqual(forwarded["Content-Type"], "application/json")

    def test_forward_headers_drop_hop_by_hop(self):
        headers = {
            "Connection": "keep-alive",
            "Keep-Alive": "timeout=5",
            "Host": "localhost:5173",
            "Content-Length": "10",
            "Transfer-Encoding": "chunked",
            "Upgrade": "websocket",
            "Authorization": "Bearer token",
        }
        forwarded = forward_headers_from(headers)
        for name in ("Connection", "Keep-Alive", "Host", "Content-Length",
                     "Transfer-Encoding", "Upgrade"):
            self.assertNotIn(name, forwarded)
        self.assertEqual(forwarded["Authorization"], "Bearer token")

    def test_upstream_content_encoding_is_stripped(self):
        headers = {
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
            "Content-Length": "908",
            "Vary": "Accept-Encoding",
        }
        returned = response_headers_from(headers)
        self.assertNotIn("Content-Encoding", returned)
        self.assertNotIn("Content-Length", returned)
        self.assertEqual(returned["Content-Type"], "application/json")
        self.assertEqual(returned["Vary"], "Accept-Encoding")

    def test_decoded_body_is_never_labelled_as_compressed(self):
        # A real 908-byte response (e.g. a support ticket detail) is large
        # enough for the service's GZipMiddleware to compress it; the client
        # must not be told the decoded bytes are still compressed.
        upstream_headers = {"content-encoding": "gzip", "content-type": "application/json"}
        self.assertNotIn(
            "content-encoding",
            {k.lower() for k in response_headers_from(upstream_headers)},
        )

    def test_response_strip_set_covers_encoding_and_hop_by_hop(self):
        self.assertIn("content-encoding", RESPONSE_STRIP_HEADERS)
        self.assertTrue(HOP_BY_HOP_HEADERS.issubset(RESPONSE_STRIP_HEADERS))


if __name__ == "__main__":
    unittest.main()
