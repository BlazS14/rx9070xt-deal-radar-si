"""Dedicated watchers for major Slovenian PC hardware shops: Mlacom, Funtech, Anni, Big Bang."""

from __future__ import annotations

from bs4 import BeautifulSoup

from ..fetcher import PoliteFetcher
from ..models import RawOffer, WatcherResult
from .base import BaseWatcher, parse_generic_product_cards, parse_json_ld_products


class _SlovenianShopWatcher(BaseWatcher):
    country = "SI"
    urls: tuple[str, ...] = ()
    card_selectors: tuple[str, ...] = ()

    async def scan(self, fetcher: PoliteFetcher) -> WatcherResult:
        discovered: dict[str, RawOffer] = {}
        urls_checked = 0
        blocked = 0
        errors: list[str] = []

        for url in self.urls:
            urls_checked += 1
            resp = await fetcher.get(url)
            if resp.blocked:
                blocked += 1
                errors.append(resp.error)
                continue
            if not resp.ok:
                errors.append(f"HTTP {resp.status_code} {resp.error}".strip())
                continue

            for offer in self.parse_html(resp.text, resp.url):
                existing = discovered.get(offer.offer_key)
                if existing is None or offer.listed_price_eur < existing.listed_price_eur:
                    discovered[offer.offer_key] = offer

        status = "ok"
        if blocked == len(self.urls) and not discovered:
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

    def parse_html(self, html: str, page_url: str) -> list[RawOffer]:
        soup = BeautifulSoup(html, "lxml")
        ld = parse_json_ld_products(
            soup,
            store=self.name,
            page_url=page_url,
            ships_to_slovenia=True,
        )
        cards = parse_generic_product_cards(
            soup,
            store=self.name,
            page_url=page_url,
            card_selectors=list(self.card_selectors),
            ships_to_slovenia=True,
        )
        by_key = {o.offer_key: o for o in ld}
        for c in cards:
            by_key.setdefault(c.offer_key, c)
        return list(by_key.values())


class MlacomWatcher(_SlovenianShopWatcher):
    name = "mlacom"
    display_name = "Mlacom.si (SI)"
    urls = (
        "https://www.mlacom.si/iskanje?q=9070+xt",
        "https://www.mlacom.si/komponente/graficne-kartice?q=9070+xt",
    )
    card_selectors = (
        ".product-item",
        ".product-card",
        ".item-box",
        "article.product",
    )


class FuntechWatcher(_SlovenianShopWatcher):
    name = "funtech"
    display_name = "Funtech.si (SI)"
    urls = (
        "https://funtech.si/search/?q=9070+xt",
        "https://funtech.si/category/vrh/racunalnistvo/komponente/graficne-kartice/?search=9070+xt",
    )
    card_selectors = (
        ".product-card",
        ".product-item",
        ".product",
        "article[class*='product']",
    )


class AnniWatcher(_SlovenianShopWatcher):
    name = "anni"
    display_name = "Anni.si (SI)"
    urls = (
        "https://anni.si/iskanje?q=9070+xt",
        "https://anni.si/graficne-kartice?q=9070+xt",
    )
    card_selectors = (
        ".product-card",
        ".product-item",
        ".product-box",
        "article[class*='product']",
    )


class BigBangWatcher(_SlovenianShopWatcher):
    name = "bigbang"
    display_name = "BigBang.si (SI)"
    urls = (
        "https://www.bigbang.si/iskanje/?q=rx+9070+xt",
        "https://www.bigbang.si/outlet/?q=9070+xt",
    )
    card_selectors = (
        ".cp-card",
        ".product-card",
        "article[class*='product']",
        ".product-item",
    )
