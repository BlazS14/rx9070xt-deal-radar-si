"""Registry of all 23 built-in Slovenian & EU store/aggregator watchers."""

from __future__ import annotations

from ..config import RadarConfig
from .base import BaseWatcher
from .caseking import CasekingWatcher
from .ceneje import CenejeWatcher
from .custom_url import CustomUrlsWatcher
from .eu_shops import (
    AlternateDeWatcher,
    AlzaWatcher,
    AmazonDeWatcher,
    AmazonEsWatcher,
    AmazonFrWatcher,
    AmazonItWatcher,
    BpmPowerWatcher,
    ComputerUniverseWatcher,
    CyberportWatcher,
    GalaxusWatcher,
    GeizhalsEuWatcher,
    GpuTrackerEuWatcher,
    IdealoDeWatcher,
    LdlcWatcher,
    NotebooksbilligerWatcher,
    ProshopWatcher,
    ReicheltWatcher,
)
from .mimovrste import MimovrsteWatcher
from .slovenian_shops import (
    AnniWatcher,
    BigBangWatcher,
    FuntechWatcher,
    MlacomWatcher,
)

ALL_WATCHER_CLASSES: list[type[BaseWatcher]] = [
    # Slovenian retailers & SI aggregator
    MimovrsteWatcher,
    CenejeWatcher,
    MlacomWatcher,
    FuntechWatcher,
    AnniWatcher,
    BigBangWatcher,
    # Pan-EU GPU & hardware aggregators
    GpuTrackerEuWatcher,
    GeizhalsEuWatcher,
    IdealoDeWatcher,
    # Amazon EU (all ship to Slovenia + Warehouse Deals)
    AmazonDeWatcher,
    AmazonItWatcher,
    AmazonFrWatcher,
    AmazonEsWatcher,
    # Major EU retailers shipping to Slovenia
    CasekingWatcher,
    AlternateDeWatcher,
    ComputerUniverseWatcher,
    ProshopWatcher,
    ReicheltWatcher,
    NotebooksbilligerWatcher,
    LdlcWatcher,
    BpmPowerWatcher,
    CyberportWatcher,
    GalaxusWatcher,
    AlzaWatcher,
]


def build_watchers(config: RadarConfig, only_names: list[str] | None = None) -> list[BaseWatcher]:
    """Instantiate enabled watchers (optionally filtered by name)."""
    filter_set = {n.strip().lower() for n in only_names} if only_names else None
    watchers: list[BaseWatcher] = []
    for cls in ALL_WATCHER_CLASSES:
        if filter_set is not None and cls.name not in filter_set:
            continue
        if filter_set is None and not config.is_watcher_enabled(cls.name):
            continue
        watchers.append(cls(config))

    if config.custom_urls and (filter_set is None or "custom_urls" in filter_set):
        watchers.append(CustomUrlsWatcher(config))

    return watchers
