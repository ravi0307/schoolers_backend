"""
Schoolers Website Service — standalone microservice.
Reads all configuration from the single shared common/.env via common.config.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common.config import settings
from common.exception_handlers import register_exception_handlers

from router import router, public_router

app = FastAPI(title="Schoolers Website Service", debug=settings.DEBUG)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


register_exception_handlers(app)

@app.get("/health")
def health():
    return {"status": "ok", "service": "Schoolers Website Service", "env": settings.ENV}


API_PREFIX = "/api/v1"
app.include_router(router, prefix=API_PREFIX)
app.include_router(public_router, prefix=API_PREFIX)
