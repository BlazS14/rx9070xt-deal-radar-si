"""Caseking.de watcher — tracks all RX 9070 XT SKUs and Sale/B-Ware listings (ships to Slovenia)."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..fetcher import PoliteFetcher
from ..matching import is_rx_9070_xt
from ..models import Condition, RawOffer, WatcherResult
from .base import (
    BaseWatcher,
    make_offer_key,
    parse_generic_product_cards,
    parse_json_ld_products,
)

_CASEKING_CATEGORY_URL = (
    "https://www.caseking.de/en/pc-components/graphics-cards/amd/rx-9000/radeon-rx-9070-xt"
)
_CASEKING_SALE_URL = "https://www.caseking.de/en/search?q=9070+xt+b-ware"
_SKU_RE = re.compile(r"\b(GC[A-Z]{2}-\d{3,4})\b", re.IGNORECASE)


def parse_caseking_page(
    html: str,
    page_url: str,
    *,
    shipping_eur: float = 15.99,
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
        # Do NOT blindly force B-Ware on search fallback pages — rely on actual listing condition text
        default_condition=Condition.NEW,
    )

    by_key: dict[str, RawOffer] = {}
    for offer in [*offers, *cards]:
        if not is_rx_9070_xt(offer.title):
            continue
        sku_m = _SKU_RE.search(f"{offer.sku} {offer.title} {offer.url}")
        if sku_m:
            offer.sku = sku_m.group(1).upper()
        # Normalize offer_key consistently by SKU when available to prevent JSON-LD + DOM duplicates
        offer.offer_key = make_offer_key("caseking", offer.url, sku=offer.sku, condition=offer.condition)
        existing = by_key.get(offer.offer_key)
        if existing is None or offer.listed_price_eur < existing.listed_price_eur:
            by_key[offer.offer_key] = offer

    return list(by_key.values())


class CasekingWatcher(BaseWatcher):
    """Watches Caseking.de AMD Radeon RX 9070 XT catalog & B-Ware/Sale deals."""

    name = "caseking"
    display_name = "Caseking.de (EU -> SI)"
    country = "DE"

    async def scan(self, fetcher: PoliteFetcher) -> WatcherResult:
        shipping = float(self.options.get("shipping_to_slovenia_eur", 15.99))
        urls: list[str] = [_CASEKING_CATEGORY_URL]
        if self.options.get("scan_sale_outlet", True):
            urls.append(_CASEKING_SALE_URL)

        discovered: dict[str, RawOffer] = {}
        urls_checked = 0
        blocked = 0
        errors: list[str] = []

        for url in urls:
            urls_checked += 1
            resp = await fetcher.get(url, accept_language="en-US,en;q=0.9,de;q=0.8")
            if resp.blocked:
                blocked += 1
                errors.append(resp.error)
                continue
            if not resp.ok:
                errors.append(f"HTTP {resp.status_code} {resp.error}".strip())
                continue

            for offer in parse_caseking_page(resp.text, resp.url, shipping_eur=shipping):
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
            message=(
                f"Found {len(discovered)} RX 9070 XT offers"
                if discovered
                else (errors[0] if errors else "No offers found")
            ),
            urls_checked=urls_checked,
        )
