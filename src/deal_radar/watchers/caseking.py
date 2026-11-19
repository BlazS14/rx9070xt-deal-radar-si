"""Caseking.de watcher — tracks all RX 9070 XT SKUs and Sale/B-Ware listings (ships to Slovenia)."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..fetcher import PoliteFetcher
from ..matching import is_rx_9070_xt
from ..models import Condition, RawOffer, WatcherResult
from .base import (
    BaseWatcher,
    parse_generic_product_cards,
    parse_json_ld_products,
)

_CASEKING_CATEGORY_URL = (
    "https://www.caseking.de/en/pc-components/graphics-cards/amd/rx-9000/radeon-rx-9070-xt"
)
_CASEKING_SALE_URL = "https://www.caseking.de/en/search?q=9070+xt+b-ware"
_SKU_RE = re.compile(r"\b(GC[A-Z]{2}-\d{3,4})\b")


def parse_caseking_page(
    html: str,
    page_url: str,
    *,
    shipping_eur: float = 15.99,
    default_condition: Condition = Condition.NEW,
) -> list[RawOffer]:
    soup = BeautifulSoup(html, "lxml")
    offers = parse_json_ld_products(
        soup,
        store="caseking",
        page_url=page_url,
        shipping_eur=shipping_eur,
        ships_to_slovenia=True,
    )
    cards = parse_generic_product_cards(
        soup,
        store="caseking",
        page_url=page_url,
        card_selectors=[
            ".product-tile",
            "[data-testid='product-tile']",
            ".product-grid-item",
            "article[class*='product']",
            ".artbox",
        ],
        shipping_eur=shipping_eur,
        ships_to_slovenia=True,
        default_condition=default_condition,
    )
    by_key = {o.offer_key: o for o in offers}
    for card_offer in cards:
        sku_m = _SKU_RE.search(f"{card_offer.title} {card_offer.url}")
        if sku_m and not card_offer.sku:
            card_offer.sku = sku_m.group(1)
        by_key.setdefault(card_offer.offer_key, card_offer)

    # Filter strictly
    return [o for o in by_key.values() if is_rx_9070_xt(o.title)]


class CasekingWatcher(BaseWatcher):
    """Watches Caseking.de AMD Radeon RX 9070 XT catalog & B-Ware/Sale deals."""

    name = "caseking"
    display_name = "Caseking.de (EU -> SI)"
    country = "DE"

    async def scan(self, fetcher: PoliteFetcher) -> WatcherResult:
        shipping = float(self.options.get("shipping_to_slovenia_eur", 15.99))
        urls: list[tuple[str, Condition]] = [(_CASEKING_CATEGORY_URL, Condition.NEW)]
        if self.options.get("scan_sale_outlet", True):
            urls.append((_CASEKING_SALE_URL, Condition.B_WARE))

        discovered: dict[str, RawOffer] = {}
        urls_checked = 0
        blocked = 0
        errors: list[str] = []

        for url, default_cond in urls:
            urls_checked += 1
            resp = await fetcher.get(url, accept_language="en-US,en;q=0.9,de;q=0.8")
            if resp.blocked:
                blocked += 1
                errors.append(resp.error)
                continue
            if not resp.ok:
                errors.append(f"HTTP {resp.status_code} {resp.error}".strip())
                continue

            for offer in parse_caseking_page(
                resp.text,
                resp.url,
                shipping_eur=shipping,
                default_condition=default_cond,
            ):
                existing = discovered.get(offer.offer_key)
                if existing is None or offer.listed_price_eur < existing.listed_price_eur:
                    discovered[offer.offer_key] = offer

        status = "ok"
        if blocked == len(urls) and not discovered:
            status = "blocked"
        elif errors and not discovered:
            status = "error"
        elif errors:
            status = "partial"

        return WatcherResult(
            watcher_name=self.name,
            offers=list(discovered.values()),
            status=status,
            message=f"Found {len(discovered)} RX 9070 XT offers" if discovered else (errors[0] if errors else "No offers found"),
            urls_checked=urls_checked,
        )
