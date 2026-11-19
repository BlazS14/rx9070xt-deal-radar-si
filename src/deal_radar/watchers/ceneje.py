"""Ceneje.si price aggregator watcher — sweeps dozens of Slovenian stores in one pass."""

from __future__ import annotations

from bs4 import BeautifulSoup

from ..fetcher import PoliteFetcher
from ..models import RawOffer, WatcherResult
from .base import BaseWatcher, parse_generic_product_cards, parse_json_ld_products

_CENEJE_URLS = [
    # Direct AMD RX 9070 XT GPU category filter on Ceneje.si
    "https://www.ceneje.si/L3/115/racunalnistvo/komponente/graficne-kartice?filt=124:941614",
    "https://www.ceneje.si/Iskanje/Izdelki?q=rx+9070+xt",
]


def parse_ceneje_page(html: str, page_url: str) -> list[RawOffer]:
    soup = BeautifulSoup(html, "lxml")
    offers = parse_json_ld_products(
        soup,
        store="ceneje",
        page_url=page_url,
        ships_to_slovenia=True,
    )
    cards = parse_generic_product_cards(
        soup,
        store="ceneje",
        page_url=page_url,
        card_selectors=[
            ".productBox",
            ".product-box",
            "article[class*='product']",
            "[data-testid*='product']",
            ".c-product-card",
        ],
        ships_to_slovenia=True,
    )
    by_key = {o.offer_key: o for o in offers}
    for c in cards:
        by_key.setdefault(c.offer_key, c)
    return list(by_key.values())


class CenejeWatcher(BaseWatcher):
    """Watches Ceneje.si to surface the cheapest Slovenian store deals across SI retailers."""

    name = "ceneje"
    display_name = "Ceneje.si (SI Aggregator)"
    country = "SI"

    async def scan(self, fetcher: PoliteFetcher) -> WatcherResult:
        discovered: dict[str, RawOffer] = {}
        urls_checked = 0
        blocked = 0
        errors: list[str] = []

        for url in _CENEJE_URLS:
            urls_checked += 1
            resp = await fetcher.get(url)
            if resp.blocked:
                blocked += 1
                errors.append(resp.error)
                continue
            if not resp.ok:
                errors.append(f"HTTP {resp.status_code} {resp.error}".strip())
                continue

            for offer in parse_ceneje_page(resp.text, resp.url):
                existing = discovered.get(offer.offer_key)
                if existing is None or offer.listed_price_eur < existing.listed_price_eur:
                    discovered[offer.offer_key] = offer

        status = "ok"
        if blocked == len(_CENEJE_URLS) and not discovered:
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
