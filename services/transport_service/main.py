"""
Schoolers Transport Service — standalone microservice.
Reads all configuration from the single shared common/.env via common.config.
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from common.config import settings
from common.exceptions import AppError

from router import router

app = FastAPI(title="Schoolers Transport Service", debug=settings.DEBUG)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health")
def health():
    return {"status": "ok", "service": "Schoolers Transport Service", "env": settings.ENV}


API_PREFIX = "/api/v1"
app.include_router(router, prefix=API_PREFIX)
