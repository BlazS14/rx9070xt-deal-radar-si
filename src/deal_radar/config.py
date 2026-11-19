"""TOML configuration loader and defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

from .models import DetectedPromo


@dataclass(slots=True)
class GeneralConfig:
    poll_interval_seconds: int = 240
    poll_jitter_seconds: int = 25
    db_path: str = "data/deals.db"
    max_concurrency: int = 4
    request_timeout_seconds: float = 20.0
    use_browser_fallback: bool = False
    only_in_stock: bool = True
    include_open_box: bool = True


@dataclass(slots=True)
class AlertsConfig:
    target_price_eur: float = 680.0
    steal_price_eur: float = 630.0
    reference_market_price_eur: float = 820.0
    min_price_drop_eur: float = 25.0
    min_price_drop_pct: float = 5.0
    open_box_alert_ceiling_eur: float = 740.0
    stacked_discount_alert_pct: float = 18.0
    cooldown_hours: float = 6.0
    re_alert_extra_drop_eur: float = 10.0


@dataclass(slots=True)
class ShippingConfig:
    default_slovenia_shipping_eur: float = 4.90
    default_eu_shipping_eur: float = 15.99
    mimovrste_smart: bool = True


@dataclass(slots=True)
class NotificationsConfig:
    desktop_enabled: bool = True
    sound_enabled: bool = True
    console_enabled: bool = True
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    ntfy_url: str = ""


@dataclass(slots=True)
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(slots=True)
class CustomUrlConfig:
    name: str
    url: str
    store: str = "custom"
    shipping_to_slovenia_eur: float = 4.90
    ships_to_slovenia: bool = True


@dataclass(slots=True)
class PromoRuleConfig:
    name: str
    store: str
    code: str
    percent_off: float = 0.0
    fixed_off_eur: float = 0.0
    requires_first_party: bool = False
    stacks_with_sale: bool = True
    stacks_with_open_box: bool = True
    only_conditions: tuple[str, ...] = ()
    min_price_eur: float = 0.0
    enabled: bool = True

    def to_detected_promo(self) -> DetectedPromo:
        return DetectedPromo(
            label=self.name,
            code=self.code or None,
            percent_off=self.percent_off,
            fixed_off_eur=self.fixed_off_eur,
            source="config",
            requires_first_party=self.requires_first_party,
            stacks_with_open_box=self.stacks_with_open_box,
            stacks_with_sale=self.stacks_with_sale,
            only_conditions=self.only_conditions,
            min_price_eur=self.min_price_eur,
            confidence="high",
        )


@dataclass(slots=True)
class RadarConfig:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    shipping: ShippingConfig = field(default_factory=ShippingConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    watchers: dict[str, dict[str, Any]] = field(default_factory=dict)
    promos: list[PromoRuleConfig] = field(default_factory=list)
    custom_urls: list[CustomUrlConfig] = field(default_factory=list)

    def watcher_options(self, name: str) -> dict[str, Any]:
        return dict(self.watchers.get(name, {}))

    def is_watcher_enabled(self, name: str) -> bool:
        opts = self.watchers.get(name)
        if opts is None:
            return True
        return bool(opts.get("enabled", True))

    def active_promos_for_store(self, store: str) -> list[DetectedPromo]:
        store_l = store.lower()
        result: list[DetectedPromo] = []
        for rule in self.promos:
            if not rule.enabled:
                continue
            if rule.store.lower() in {store_l, "*", "all"}:
                result.append(rule.to_detected_promo())
        return result


def _merge_section(cls: type, data: dict[str, Any] | None) -> Any:
    if not data:
        return cls()
    allowed = {f for f in cls.__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in allowed}
    return cls(**filtered)


def load_config(path: str | Path | None = None) -> RadarConfig:
    """Load config from TOML file, falling back to sensible defaults."""
    if path is None:
        default_path = Path("config.toml")
        if default_path.exists():
            path = default_path
        else:
            return RadarConfig()

    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")

    with cfg_path.open("rb") as fh:
        raw = tomllib.load(fh)

    promos: list[PromoRuleConfig] = []
    for entry in raw.get("promos", []):
        promos.append(
            PromoRuleConfig(
                name=str(entry.get("name", "Configured Promo")),
                store=str(entry.get("store", "*")),
                code=str(entry.get("code", "")),
                percent_off=float(entry.get("percent_off", 0.0)),
                fixed_off_eur=float(entry.get("fixed_off_eur", 0.0)),
                requires_first_party=bool(entry.get("requires_first_party", False)),
                stacks_with_sale=bool(entry.get("stacks_with_sale", True)),
                stacks_with_open_box=bool(entry.get("stacks_with_open_box", True)),
                only_conditions=tuple(str(c) for c in entry.get("only_conditions", [])),
                min_price_eur=float(entry.get("min_price_eur", 0.0)),
                enabled=bool(entry.get("enabled", True)),
            )
        )

    custom_urls: list[CustomUrlConfig] = []
    for entry in raw.get("custom_urls", []):
        if not entry.get("url"):
            continue
        custom_urls.append(
            CustomUrlConfig(
                name=str(entry.get("name", entry["url"])),
                url=str(entry["url"]),
                store=str(entry.get("store", "custom")),
                shipping_to_slovenia_eur=float(entry.get("shipping_to_slovenia_eur", 4.90)),
                ships_to_slovenia=bool(entry.get("ships_to_slovenia", True)),
            )
        )

    return RadarConfig(
        general=_merge_section(GeneralConfig, raw.get("general")),
        alerts=_merge_section(AlertsConfig, raw.get("alerts")),
        shipping=_merge_section(ShippingConfig, raw.get("shipping")),
        notifications=_merge_section(NotificationsConfig, raw.get("notifications")),
        dashboard=_merge_section(DashboardConfig, raw.get("dashboard")),
        watchers=dict(raw.get("watchers", {})),
        promos=promos,
        custom_urls=custom_urls,
    )
