"""Request/response header filtering for the API gateway proxy.

Kept free of httpx/FastAPI imports so the rules can be unit-tested in the
lightweight contract-test environment (which installs neither).
"""

HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
}

# httpx transparently decompresses the upstream body (gzip/deflate/br) but
# leaves the original Content-Encoding header on the response. Returning that
# header with the already-decoded bytes makes browsers try to decompress plain
# JSON (ERR_CONTENT_DECODING_FAILED). Drop it so the gateway only ever hands
# clients an uncompressed body.
RESPONSE_STRIP_HEADERS = HOP_BY_HOP_HEADERS | {"content-encoding"}


def forward_headers_from(request_headers):
    """Headers to send upstream.

    Drops hop-by-hop headers and the client's Accept-Encoding so httpx
    negotiates compression itself and transparently decodes the reply.
    """
    return {
        k: v for k, v in request_headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() != "accept-encoding"
    }


def response_headers_from(upstream_headers):
    """Headers to return to the client.

    httpx has already decompressed the body, so the upstream Content-Encoding
    must not be forwarded: labelling plain JSON as gzip makes browsers fail
    with ERR_CONTENT_DECODING_FAILED while the request still reads as 200.
    """
    return {
        k: v for k, v in upstream_headers.items()
        if k.lower() not in RESPONSE_STRIP_HEADERS
    }
