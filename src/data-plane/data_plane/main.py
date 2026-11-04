"""Data-plane FastAPI app: MQTT publish/watch and health."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException

from data_plane.config import Settings, get_settings
from data_plane.logging_config import configure_logging
from data_plane.metrics import PUBLISH, MetricsMiddleware, metrics_response
from data_plane.runtime import get_mqtt_client, start_mqtt_runtime, stop_mqtt_runtime
from data_plane.schemas import PublishRequest, SubscriptionRequest


def _require_internal_token(
    settings: Annotated[Settings, Depends(get_settings)],
    x_mqtt_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.mqtt_internal_token or x_mqtt_internal_token != settings.mqtt_internal_token:
        raise HTTPException(status_code=401, detail="Invalid MQTT internal token.")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    start_mqtt_runtime(settings)
    yield
    stop_mqtt_runtime()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title="MeteorCloud data-plane", lifespan=lifespan)
    if settings.metrics_enabled:
        application.add_middleware(MetricsMiddleware)
        application.add_api_route("/metrics", metrics_response, methods=["GET"], include_in_schema=False)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/v1/publish")
    def publish(
        payload: PublishRequest,
        _: Annotated[None, Depends(_require_internal_token)],
    ) -> dict[str, bool]:
        try:
            get_mqtt_client().publish(
                payload.topic,
                payload.payload,
                qos=payload.qos,
                retain=payload.retain,
            )
        except RuntimeError as exc:
            PUBLISH.labels(result="error").inc()
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        PUBLISH.labels(result="ok").inc()
        return {"ok": True}

    @application.post("/v1/subscriptions")
    def watch(
        payload: SubscriptionRequest,
        _: Annotated[None, Depends(_require_internal_token)],
    ) -> dict[str, bool]:
        try:
            get_mqtt_client().watch_topic(payload.topic)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True}

    @application.post("/v1/subscriptions/unwatch")
    def unwatch(
        payload: SubscriptionRequest,
        _: Annotated[None, Depends(_require_internal_token)],
    ) -> dict[str, bool]:
        try:
            get_mqtt_client().unwatch_topic(payload.topic)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True}

    return application


app = create_app()


def run() -> None:
    import uvicorn

    host, port = get_settings().http_bind()
    uvicorn.run("data_plane.main:app", host=host, port=port)


if __name__ == "__main__":
    run()
