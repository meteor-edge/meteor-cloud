"""Application factory and FastAPI entrypoint."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters import metrics_backend
from app.api.rest.health import router as health_router
from app.applications.internal_router import router as applications_internal_router
from app.applications.router import agent_router as applications_agent_router
from app.applications.router import router as applications_router
from app.artifacts.public_router import router as public_os_images_router
from app.artifacts.router import router as artifacts_router
from app.audit.denials import record_forbidden
from app.audit.router import router as audit_router
from app.authorization.router import router as authorization_router
from app.connectivity.internal_router import router as connectivity_internal_router
from app.connectivity.router import router as connectivity_router
from app.core.access_log import AccessLogMiddleware
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.metrics import BUILD_INFO, MetricsMiddleware, configure_metrics, metrics_enabled, metrics_response
from app.core.request_id import RequestIdMiddleware
from app.deployments.router import router as deployments_router
from app.deployments.sweeper import run_sweeper
from app.device_actions.router import router as device_actions_router
from app.devices.agent_router import router as agent_router
from app.devices.fleet_metrics import register_fleet_metrics
from app.devices.router import router as fleet_router
from app.endpoints.router import router as endpoints_router
from app.endpoints.routing import router as routing_internal_router
from app.identity.router import router as identity_router
from app.mqtt.internal_router import router as mqtt_internal_router
from app.mqtt.runtime import start_mqtt_runtime, stop_mqtt_runtime
from app.network.router import router as network_router
from app.releases.router import router as releases_router
from app.rules.internal_router import router as rules_internal_router
from app.rules.router import router as rules_router
from app.shell.router import router as shell_router
from app.tenancy.router import router as organizations_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    logger.info("Starting %s (%s)", settings.app_name, settings.app_env)
    start_mqtt_runtime(settings)
    sweeper: asyncio.Task[None] | None = None
    if settings.deploy_tick_interval_seconds > 0 and settings.deployment_provider == "native":
        sweeper = asyncio.create_task(run_sweeper(settings.deploy_tick_interval_seconds))
    yield
    if sweeper is not None:
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
    stop_mqtt_runtime()
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    configure_metrics(metrics_backend(settings))

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    if metrics_enabled():
        application.add_middleware(MetricsMiddleware)
        BUILD_INFO.labels(version=settings.app_version).set(1)
        register_fleet_metrics(settings)
    if settings.access_log_enabled:
        application.add_middleware(AccessLogMiddleware)
    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(application, on_forbidden=record_forbidden)
    application.include_router(health_router)
    application.include_router(identity_router)
    application.include_router(authorization_router)
    application.include_router(organizations_router)
    application.include_router(fleet_router)
    application.include_router(artifacts_router)
    application.include_router(public_os_images_router)
    application.include_router(releases_router)
    application.include_router(deployments_router)
    application.include_router(endpoints_router)
    application.include_router(connectivity_router)
    application.include_router(shell_router)
    application.include_router(applications_router)
    application.include_router(applications_agent_router)
    application.include_router(applications_internal_router)
    application.include_router(rules_router)
    application.include_router(rules_internal_router)
    application.include_router(network_router)
    application.include_router(device_actions_router)
    application.include_router(audit_router)
    application.include_router(agent_router)
    application.include_router(mqtt_internal_router)
    application.include_router(connectivity_internal_router)
    application.include_router(routing_internal_router)
    if metrics_enabled():
        application.add_api_route(
            "/metrics",
            metrics_response,
            methods=["GET"],
            include_in_schema=False,
        )

    return application


app = create_app()
