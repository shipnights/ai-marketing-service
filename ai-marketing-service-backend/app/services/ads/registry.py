"""Connector registry — the single place that knows the concrete connectors.

Routes and the sync worker resolve connectors through here, so adding a platform
is one import + one entry.
"""

from app.services.ads.base import AdConnector, ConnectorConfigError
from app.services.ads.google import GoogleAdsConnector
from app.services.ads.meta import MetaAdsConnector
from app.services.ads.reddit import RedditAdsConnector
from app.services.ads.tiktok import TikTokAdsConnector

_INSTANCES: dict[str, AdConnector] = {
    c.key: c
    for c in (
        GoogleAdsConnector(),
        MetaAdsConnector(),
        TikTokAdsConnector(),
        RedditAdsConnector(),
    )
}


def get_connector(platform: str) -> AdConnector:
    connector = _INSTANCES.get(platform)
    if connector is None:
        raise ConnectorConfigError(f"Unknown ad platform: {platform}")
    return connector


def all_connectors() -> list[AdConnector]:
    return list(_INSTANCES.values())


def connector_status() -> list[dict]:
    """Lightweight list for the UI: which platforms exist and are configured."""
    return [
        {
            "platform": c.key,
            "label": c.label,
            "configured": c.is_configured,
            "supports_management": c.supports_management,
        }
        for c in _INSTANCES.values()
    ]
