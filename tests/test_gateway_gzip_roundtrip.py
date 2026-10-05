"""End-to-end proof that a gzipped upstream reaches the browser readable.

The header unit tests in test_gateway_proxy.py check the filtering rules in
isolation. They cannot catch the failure that actually happened in the running
stack: a service compressed a response, httpx decoded it for the gateway, the
gateway handed the decoded bytes back, and the response still said
`Content-Encoding: gzip`. The status was 200, curl read it fine, and every
browser refused it with ERR_CONTENT_DECODING_FAILED -- which axios surfaces as
a bare "Network Error" with no hint that the bytes were mislabelled.

So this drives the whole chain the way a browser does:

    client --Accept-Encoding: gzip--> gateway.main.app --> httpx --> gzip ASGI app

httpx does the real decompression, exactly as in production, and the
assertion decodes the gateway's reply the way a browser would: honour
Content-Encoding if it is present, otherwise take the bytes as they are. That
decode is the thing that must not raise.
"""
import asyncio
import gzip
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import gateway.main as gateway_main  # noqa: E402
from gateway.proxy_headers import RESPONSE_STRIP_HEADERS  # noqa: E402

# Comfortably past the 500-byte minimum GZipMiddleware uses, so the upstream
# really does compress and the mismatch has a chance to appear.
PAYLOAD = {
    "items": [
        {
            "trip_id": 1000 + i,
            "route_name": "Indiranagar Route",
            "status": "completed",
            "note": "a padded value so the response clears the compression threshold" * 2,
        }
        for i in range(12)
    ],
    "total": 12,
}


def make_gzip_upstream() -> FastAPI:
    """A service that compresses, the way the microservices all do."""
    upstream = FastAPI()
    upstream.add_middleware(GZipMiddleware, minimum_size=500)

    @upstream.get("/{full_path:path}")
    def anything(full_path: str):
        return PAYLOAD

    return upstream


async def _raw_asgi_call(app, path):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1234),
        "headers": [(b"host", b"test"), (b"accept-encoding", b"gzip")],
    }
    start = {}
    body = b""

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        nonlocal body
        if message["type"] == "http.response.start":
            start["headers"] = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            body += message.get("body", b"")

    await app(scope, receive, send)
    return start["headers"], body


def raw_asgi_call(app, path):
    return asyncio.run(_raw_asgi_call(app, path))


def decode_like_a_browser(response) -> object:
    """Decode a response body the way a browser does.

    A browser trusts Content-Encoding. If the header says gzip, it inflates the
    bytes, and plain JSON makes it fail outright. Returning the parsed JSON
    here is therefore the assertion that matters.
    """
    encoding = response.headers.get("content-encoding")
    body = response.content
    if encoding == "gzip":
        body = gzip.decompress(body)
    elif encoding in ("br", "zstd"):
        raise AssertionError(f"gateway handed the client an undecoded {encoding} body")
    return json.loads(body)


class GatewayGzipRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.real_client = gateway_main.client
        # ASGITransport keeps the request inside the process while still going
        # through httpx's real decompression, which is where the mismatch is
        # created. Pointing at a socket instead would just retest uvicorn.
        gateway_main.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=make_gzip_upstream()),
            base_url="http://upstream",
            trust_env=False,
        )
        self.addCleanup(self._restore)

    def _restore(self):
        gateway_main.client = self.real_client

    def test_a_gzipped_upstream_reaches_the_client_decodable(self):
        client = TestClient(gateway_main.app)
        response = client.get(
            "/api/v1/routes/trips", headers={"Accept-Encoding": "gzip"}
        )

        self.assertEqual(response.status_code, 200)
        decoded = decode_like_a_browser(response)
        self.assertEqual(decoded["total"], PAYLOAD["total"])
        self.assertEqual(len(decoded["items"]), len(PAYLOAD["items"]))
        self.assertEqual(decoded["items"][0]["route_name"], "Indiranagar Route")

    def test_the_gateway_does_not_relabel_decoded_bytes_as_compressed(self):
        client = TestClient(gateway_main.app)
        response = client.get(
            "/api/v1/routes/trips", headers={"Accept-Encoding": "gzip"}
        )

        self.assertNotIn("content-encoding", response.headers)

    def test_a_gzipped_upstream_reaches_the_client_decodable_without_asking(self):
        # Same result when the client did not advertise gzip: httpx negotiates
        # on its own, and the client must still get readable bytes.
        client = TestClient(gateway_main.app)
        response = client.get("/api/v1/routes/trips")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(decode_like_a_browser(response)["total"], PAYLOAD["total"])

    def test_the_upstream_really_did_compress(self):
        # Guards the tests above from passing because nothing was ever
        # compressed. Driven at the raw ASGI level on purpose: httpx decodes
        # transparently, so going through a client here would hide the very
        # bytes being asserted on.
        headers, body = raw_asgi_call(make_gzip_upstream(), "/api/v1/routes/trips")
        self.assertEqual(headers.get("content-encoding"), "gzip")
        self.assertEqual(body[:2], b"\x1f\x8b")
        # And it is a real gzip member, so a browser could inflate it.
        self.assertEqual(json.loads(gzip.decompress(body))["total"], PAYLOAD["total"])

    def test_the_gateway_returns_bodies_httpx_already_decoded(self):
        # httpx is the component that decodes; the gateway must therefore never
        # claim the bytes it forwards are still compressed.
        self.assertIn("content-encoding", RESPONSE_STRIP_HEADERS)
        source = (ROOT / "gateway" / "main.py").read_text()
        self.assertIn("response_headers_from(upstream.headers)", source)
        # A response built straight from upstream.headers instead would pass the
        # helper-only unit tests and still ship the broken header.
        self.assertNotIn("headers=upstream.headers", source)


if __name__ == "__main__":
    unittest.main()