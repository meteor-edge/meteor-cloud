"""MeteorCloud SDK.

Publish telemetry, events and inference results, subscribe to live data, query
history and exchange commands, from an application on a device or in the cloud.

.. code-block:: python

    from meteorcloud import MeteorClient

    client = MeteorClient.from_env()
    client.telemetry.publish("energy.metrics", {"power": 1200, "voltage": 230})
"""

from .aio import AsyncMeteorClient, AsyncSubscription
from .client import MeteorClient, Subscription
from .config import Config
from .exceptions import (
    AuthenticationError,
    ClientClosedError,
    CommandError,
    CommandTimeoutError,
    ConfigurationError,
    MeteorCloudError,
    NotFoundError,
    PermissionDeniedError,
    PublishError,
    RateLimitedError,
    UnavailableError,
    ValidationError,
)
from .models import Bucket, Command, Event, Identity, Message, Page, PublishResult

__version__ = "0.1.0"

__all__ = [
    "AsyncMeteorClient",
    "AsyncSubscription",
    "AuthenticationError",
    "Bucket",
    "ClientClosedError",
    "Command",
    "CommandError",
    "CommandTimeoutError",
    "Config",
    "ConfigurationError",
    "Event",
    "Identity",
    "Message",
    "MeteorClient",
    "MeteorCloudError",
    "NotFoundError",
    "Page",
    "PermissionDeniedError",
    "PublishError",
    "PublishResult",
    "RateLimitedError",
    "Subscription",
    "UnavailableError",
    "ValidationError",
    "__version__",
]
