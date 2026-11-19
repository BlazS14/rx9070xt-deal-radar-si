"""Core data models for RX 9070 XT Deal Radar."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Condition(str, Enum):
    NEW = "new"
    OPEN_BOX = "open_box"
    DAMAGED_BOX = "damaged_box"
    B_WARE = "b_ware"
    USED_LIKE_NEW = "used_like_new"
    REFURBISHED = "refurbished"

    @property
    def is_open_box_like(self) -> bool:
        return self != Condition.NEW

    @property
    def display_label(self) -> str:
        return {
            Condition.NEW: "New",
            Condition.OPEN_BOX: "Open-Box",
            Condition.DAMAGED_BOX: "Damaged Box",
            Condition.B_WARE: "B-Ware / Outlet",
            Condition.USED_LIKE_NEW: "Used - Like New",
            Condition.REFURBISHED: "Refurbished",
        }[self]


class DealTier(str, Enum):
    STEAL = "STEAL"
    HOT = "HOT"
    GOOD = "GOOD"
    TRACKED = "TRACKED"


@dataclass(slots=True)
class DetectedPromo:
    """A promo code or checkout discount detected on a store page or config."""

    label: str
    code: str | None = None
    percent_off: float = 0.0
    fixed_off_eur: float = 0.0
    source: str = "page"  # "page" | "config"
    requires_first_party: bool = False
    stacks_with_open_box: bool = True
    stacks_with_sale: bool = True
    only_conditions: tuple[str, ...] = ()
    min_price_eur: float = 0.0
    confidence: str = "high"  # "high" | "medium"


@dataclass(slots=True)
class DiscountLayer:
    """One layer in a stacked discount breakdown."""

    kind: str  # "retail_markdown" | "open_box" | "promo_code" | "shipping_perk"
    label: str
    percent_off: float = 0.0
    amount_off_eur: float = 0.0
    code: str | None = None
    source: str = "page"


@dataclass(slots=True)
class RawOffer:
    """Raw GPU offer scraped from a store before deal scoring."""

    store: str
    offer_key: str
    title: str
    url: str
    listed_price_eur: float
    original_price_eur: float | None = None
    shipping_eur: float | None = None
    in_stock: bool = True
    stock_text: str = ""
    condition: Condition = Condition.NEW
    condition_note: str = ""
    fulfilled_by_store: bool | None = None
    seller_name: str = ""
    ships_to_slovenia: bool = True
    detected_promos: list[DetectedPromo] = field(default_factory=list)
    sku: str = ""
    image_url: str = ""
    extra_tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvaluatedOffer:
    """Enriched and scored RX 9070 XT offer with Slovenia landed price."""

    raw: RawOffer
    brand: str
    variant: str
    effective_item_price_eur: float
    shipping_to_slovenia_eur: float
    effective_total_eur: float
    discount_layers: list[DiscountLayer]
    applied_promo: DetectedPromo | None
    total_discount_pct: float
    savings_vs_reference_eur: float
    deal_score: int
    tier: DealTier
    seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def store(self) -> str:
        return self.raw.store

    @property
    def offer_key(self) -> str:
        return self.raw.offer_key

    @property
    def title(self) -> str:
        return self.raw.title

    @property
    def url(self) -> str:
        return self.raw.url

    @property
    def condition(self) -> Condition:
        return self.raw.condition

    @property
    def in_stock(self) -> bool:
        return self.raw.in_stock

    @property
    def is_stacked_deal(self) -> bool:
        kinds = {layer.kind for layer in self.discount_layers}
        has_promo = "promo_code" in kinds
        has_open_box = self.condition.is_open_box_like or "open_box" in kinds
        has_markdown = "retail_markdown" in kinds
        return (has_promo and (has_open_box or has_markdown)) or (has_open_box and has_markdown)

    def discount_summary(self) -> str:
        if not self.discount_layers:
            return "Standard retail price"
        parts: list[str] = []
        for layer in self.discount_layers:
            if layer.code:
                if layer.percent_off > 0:
                    parts.append(f"{layer.label} ({layer.code}: -{layer.percent_off:.0f}%)")
                elif layer.amount_off_eur > 0:
                    parts.append(f"{layer.label} ({layer.code}: -€{layer.amount_off_eur:.0f})")
                else:
                    parts.append(f"{layer.label} ({layer.code})")
            elif layer.percent_off > 0:
                parts.append(f"{layer.label} (-{layer.percent_off:.0f}%)")
            elif layer.amount_off_eur > 0:
                parts.append(f"{layer.label} (-€{layer.amount_off_eur:.0f})")
            else:
                parts.append(layer.label)
        return " + ".join(parts)


@dataclass(slots=True)
class AlertEvent:
    """An alert triggered by the deal engine for a high-value offer."""

    offer: EvaluatedOffer
    reasons: list[str]
    previous_total_eur: float | None = None
    all_time_low_eur: float | None = None
    is_new_listing: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def primary_reason(self) -> str:
        return self.reasons[0] if self.reasons else "DEAL_ALERT"


@dataclass(slots=True)
class WatcherResult:
    """Outcome of a single store watcher scan."""

    watcher_name: str
    offers: list[RawOffer] = field(default_factory=list)
    status: str = "ok"  # "ok" | "partial" | "blocked" | "error"
    message: str = ""
    duration_ms: int = 0
    urls_checked: int = 0
