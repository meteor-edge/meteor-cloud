"""Shared helpers for registering exception handlers."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError
from app.core.request_id import unhandled_error_response


def register_exception_handlers(
    app: FastAPI,
    *,
    on_forbidden: Callable[[Request, AppError], None] | None = None,
) -> None:
    """Attach consistent JSON error responses to the application.

    ``on_forbidden`` (sync, best effort) runs for every 403, e.g. to write an audit row.
    """

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        if on_forbidden is not None and exc.status_code == 403:
            await run_in_threadpool(on_forbidden, request, exc)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        messages = "; ".join(f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors())
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": messages or "Request validation failed.",
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        return unhandled_error_response(getattr(request.state, "request_id", None))
