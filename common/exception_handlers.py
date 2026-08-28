"""Shared FastAPI exception handlers for all Schoolers services."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from common.exceptions import AppError

logger = logging.getLogger(__name__)


def _json_response(status_code: int, detail: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": jsonable_encoder(detail)},
    )


def _safe_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    return [
        {
            "type": error.get("type", "validation_error"),
            "loc": error.get("loc", ()),
            "msg": error.get("msg", "Invalid request"),
        }
        for error in exc.errors()
    ]


def register_exception_handlers(app: FastAPI) -> None:
    """Register consistent, safe JSON responses for application failures."""

    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return _json_response(exc.status_code, exc.message)

    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _json_response(422, _safe_validation_errors(exc))

    async def integrity_error_handler(
        request: Request, exc: IntegrityError
    ) -> JSONResponse:
        logger.exception("Database integrity error while handling %s", request.url.path)
        return _json_response(409, "Database constraint violation")

    async def sqlalchemy_error_handler(
        request: Request, exc: SQLAlchemyError
    ) -> JSONResponse:
        logger.exception("Database error while handling %s", request.url.path)
        return _json_response(500, "Database error")

    async def unexpected_error_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled exception while handling %s", request.url.path)
        return _json_response(500, "Internal server error")

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
