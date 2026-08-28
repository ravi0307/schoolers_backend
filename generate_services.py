#!/usr/bin/env python3
"""
One-time generator: turns each app/modules/<name>/ folder from the monolith
into a standalone services/<name>_service/ FastAPI application, rewriting
imports from app.core.*/app.shared.* to the shared `common` package.
"""
import re
import shutil
from pathlib import Path

MONOLITH_MODULES = Path("/home/claude/proto/backend/schoolers/app/modules")
SERVICES_ROOT = Path("/home/claude/proto/microservices/schoolers/services")

SERVICES = {
    "auth": ("auth_service", 8001),
    "schools": ("schools_service", 8002),
    "academics": ("academics_service", 8003),
    "people": ("people_service", 8004),
    "attendance": ("attendance_service", 8005),
    "marks": ("marks_service", 8006),
    "timetable": ("timetable_service", 8007),
    "transport": ("transport_service", 8008),
    "leave": ("leave_service", 8009),
    "communication": ("communication_service", 8010),
    "barter": ("barter_service", 8011),
    "activities": ("activities_service", 8012),
    "website": ("website_service", 8013),
    "notifications": ("notifications_service", 8014),
    "reports": ("reports_service", 8015),
}

IMPORT_REWRITES = [
    (re.compile(r"from app\.core\."), "from common."),
    (re.compile(r"from app\.shared\."), "from common."),
]


def rewrite_imports(text: str) -> str:
    for pattern, repl in IMPORT_REWRITES:
        text = pattern.sub(repl, text)
    return text


def rewrite_module_local_imports(text: str, module_name: str) -> str:
    text = re.sub(rf"from app\.modules\.{module_name}\.(\w+) import", r"from \1 import", text)
    text = re.sub(rf"from app\.modules\.{module_name} import (\w+) as (\w+)", r"import \1 as \2", text)
    text = re.sub(rf"from app\.modules\.{module_name} import (\w+)$", r"import \1", text, flags=re.MULTILINE)
    return text


def generate_main(module_name: str, app_title: str) -> str:
    extra_import = ""
    extra_include = ""
    if module_name == "website":
        extra_import = ", public_router"
        extra_include = "\napp.include_router(public_router, prefix=API_PREFIX)"

    return f'''"""
{app_title} — standalone microservice.
Reads all configuration from the single shared common/.env via common.config.
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from common.config import settings
from common.exceptions import AppError

from router import router{extra_import}

app = FastAPI(title="{app_title}", debug=settings.DEBUG)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={{"detail": exc.message}})


@app.get("/health")
def health():
    return {{"status": "ok", "service": "{app_title}", "env": settings.ENV}}


API_PREFIX = "/api/v1"
app.include_router(router, prefix=API_PREFIX){extra_include}
'''


def main():
    SERVICES_ROOT.mkdir(parents=True, exist_ok=True)
    for module_name, (service_dir, port) in SERVICES.items():
        src = MONOLITH_MODULES / module_name
        dst = SERVICES_ROOT / service_dir
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)

        for py_file in src.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            text = py_file.read_text()
            text = rewrite_imports(text)
            text = rewrite_module_local_imports(text, module_name)
            (dst / py_file.name).write_text(text)

        app_title = f"Schoolers {module_name.capitalize()} Service"
        (dst / "main.py").write_text(generate_main(module_name, app_title))

        (dst / "requirements.txt").write_text(
            "fastapi\nuvicorn[standard]\nsqlalchemy\npsycopg2-binary\n"
            "python-jose[cryptography]\npasslib[bcrypt]\npython-multipart\npydantic-settings\n"
        )

        (dst / "Dockerfile").write_text(f'''FROM python:3.12-slim
WORKDIR /app
COPY common /app/common
COPY services/{service_dir} /app/service
WORKDIR /app/service
RUN pip install --no-cache-dir -r requirements.txt
ENV PYTHONPATH=/app
EXPOSE {port}
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{port}"]
''')

        print(f"generated {service_dir} (port {port}) from module '{module_name}'")


if __name__ == "__main__":
    main()
