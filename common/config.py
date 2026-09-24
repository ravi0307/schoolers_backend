"""
Single shared application configuration for every microservice.

Every service imports `from common.config import settings` — regardless of
which service's directory it's run from, the .env file is always resolved
relative to THIS file's own location (the `common/` folder), not the
caller's working directory. That's the "one config file in one local
folder" requirement: there is exactly one .env, living next to this file,
and every service reads the same values from it.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

COMMON_DIR = Path(__file__).resolve().parent
ENV_FILE = COMMON_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    APP_NAME: str = "Schoolers"
    ENV: str = "development"
    DEBUG: bool = True

    # Database — every service connects to the same relational schema.
    # (Splitting the DB per-service isn't practical here: attendance,
    # marks, timetable etc. all have foreign keys into people/academics.)
    # Default updated to the local DB used by the test helper.
    DATABASE_URL: str = "postgresql://ravi@localhost:5432/schoolersdb"

    # Auth / JWT — shared secret so any service can independently verify a
    # token issued by the auth service, with no per-request call between them.
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 14

    # CORS
    CORS_ORIGINS: list[str] = ["*"]

    # Outbound email (notifications, etc.)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_TLS: bool = True

    # File uploads — local disk in dev; swap UPLOAD_BACKEND to "s3" later.
    UPLOAD_BACKEND: str = "local"
    # Keep local uploads inside the backend by default so development works
    # consistently across Windows, macOS, and Linux.
    UPLOAD_LOCAL_PATH: str = str(COMMON_DIR.parent / "uploads")
    UPLOAD_MAX_BYTES: int = 5 * 1024 * 1024

    # Gateway <-> service registry. The gateway is the single public entry
    # point (same host:port the frontend already points at); it forwards
    # each request to the right internal service based on path prefix.
    GATEWAY_PORT: int = 8000
    SERVICE_HOSTS: dict[str, str] = {
        "auth": "http://127.0.0.1:8001",
        "schools": "http://127.0.0.1:8002",
        "academics": "http://127.0.0.1:8003",
        "people": "http://127.0.0.1:8004",
        "attendance": "http://127.0.0.1:8005",
        "marks": "http://127.0.0.1:8006",
        "timetable": "http://127.0.0.1:8007",
        "transport": "http://127.0.0.1:8008",
        "leave": "http://127.0.0.1:8009",
        "media": "http://127.0.0.1:8016",
        "communication": "http://127.0.0.1:8010",
        "barter": "http://127.0.0.1:8011",
        "activities": "http://127.0.0.1:8012",
        "website": "http://127.0.0.1:8013",
        "notifications": "http://127.0.0.1:8014",
        "reports": "http://127.0.0.1:8015",
    }


settings = Settings()
