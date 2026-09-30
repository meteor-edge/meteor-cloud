"""Application factory and FastAPI entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rest.health import router as health_router
from app.audit.router import router as audit_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.metrics import MetricsMiddleware, metrics_response
from app.core.request_id import RequestIdMiddleware
from app.devices.agent_router import router as agent_router
from app.devices.router import router as fleet_router
from app.identity.router import router as identity_router
from app.mqtt.internal_router import router as mqtt_internal_router
from app.mqtt.runtime import start_mqtt_runtime, stop_mqtt_runtime
from app.tenancy.router import router as organizations_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    logger.info("Starting %s (%s)", settings.app_name, settings.app_env)
    start_mqtt_runtime(settings)
    yield
    stop_mqtt_runtime()
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    application.add_middleware(MetricsMiddleware)
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(identity_router)
    application.include_router(organizations_router)
    application.include_router(fleet_router)
    application.include_router(audit_router)
    application.include_router(agent_router)
    application.include_router(mqtt_internal_router)
    application.add_api_route(
        "/metrics",
        metrics_response,
        methods=["GET"],
        include_in_schema=False,
    )

    return application


app = create_app()
