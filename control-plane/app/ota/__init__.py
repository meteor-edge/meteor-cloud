"""OTA application facade.

Depends on ``app.ports.ota.OTAProvider``. No provider is wired
(``ota_provider=none``).
"""

from app.adapters import ota_provider
from app.ports.ota import OTAProvider

__all__ = ["OTAProvider", "ota_provider"]
