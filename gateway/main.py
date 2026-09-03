"""
Schoolers API Gateway.

The single public entry point — same host:port the frontend has always
pointed at (see common/config.py GATEWAY_PORT). Every request under
/api/v1/* is forwarded to the owning microservice based on its first path
segment, using the SERVICE_HOSTS registry in the shared common config.
The frontend needs ZERO changes to work against the microservices split.
"""
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from common.config import settings

app = FastAPI(title="Schoolers API Gateway", debug=settings.DEBUG)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# First path segment (after /api/v1/) -> service registry key.
# Most services own a segment matching their module name; a few modules
# expose routes under different top-level names, so those are mapped
# explicitly below (verified against each service's actual router prefix).
ROUTE_MAP = {
    "auth": "auth",
    "schools": "schools",
    "classes": "academics",
    "subjects": "academics",
    "periods": "academics",
    "holidays": "academics",
    "teachers": "people",
    "staff": "people",
    "parents": "people",
    "students": "people",
    "attendance": "attendance",
    "marks": "marks",
    "timetable": "timetable",
    "routes": "transport",
    "vehicles": "transport",
    "pilots": "transport",
    "leave": "leave",
    "broadcasts": "communication",
    "media": "communication",
    "barter": "barter",
    "activities": "activities",
    "website": "website",
    "public": "website",       # /api/v1/public/sites/{id}
    "notifications": "notifications",
    "reports": "reports",
}

# Service-to-service calls must use the Compose network directly, not host
# proxy settings inherited by the container.
client = httpx.AsyncClient(timeout=30.0, trust_env=False)

HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Schoolers API Gateway", "env": settings.ENV}


@app.get("/health/services")
async def health_services():
    """Pings every registered service's own /health — useful to see which
    services are up without hitting each port individually."""
    results = {}
    for name, base_url in settings.SERVICE_HOSTS.items():
        try:
            r = await client.get(f"{base_url}/health", timeout=3.0)
            results[name] = {"status": "up", "detail": r.json()}
        except Exception as exc:
            results[name] = {"status": "down", "detail": str(exc)}
    return results


@app.api_route("/api/v1/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(full_path: str, request: Request):
    first_segment = full_path.split("/", 1)[0]
    service_key = ROUTE_MAP.get(first_segment)

    if not service_key:
        return Response(
            content=f'{{"detail":"No service registered for path segment \'{first_segment}\'"}}',
            status_code=404,
            media_type="application/json",
        )

    target_base = settings.SERVICE_HOSTS[service_key]
    upstream_path = f"routes/{full_path}" if first_segment in {"vehicles", "pilots"} else full_path
    target_url = f"{target_base}/api/v1/{upstream_path}"

    body = await request.body()
    forward_headers = {
        k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
    }

    try:
        upstream = await client.request(
            request.method,
            target_url,
            params=request.query_params,
            headers=forward_headers,
            content=body,
        )
    except httpx.ConnectError:
        return Response(
            content=f'{{"detail":"Service \'{service_key}\' is unreachable"}}',
            status_code=503,
            media_type="application/json",
        )

    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )
