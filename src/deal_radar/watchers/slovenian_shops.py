"""Dedicated watchers for major Slovenian PC hardware shops: Mlacom, Funtech, Anni, Big Bang.

Uses verified category URLs and automatically follows RX 9070 XT sidebar filter links
and open-box/outlet pages when discovered.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..fetcher import PoliteFetcher
from ..models import Condition, RawOffer, WatcherResult
from .base import BaseWatcher, parse_generic_product_cards, parse_json_ld_products

_FILTER_9070_XT_RE = re.compile(r"(?i)9070\s*-?\s*xt")


def discover_9070_xt_filter_links(html: str, page_url: str, *, limit: int = 3) -> list[str]:
    """Find sidebar/category filter URLs specifically targeting RX 9070 XT."""
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        text = a.get_text(" ", strip=True)
        # Match sidebar filter links like "Radeon RX 9070 XT (31)" or "AMD Radeon RX 9070 XT"
        # while avoiding individual product pages (which usually contain "16GB" / "GDDR6" / SKU).
        if _FILTER_9070_XT_RE.search(text) and len(text) <= 45 and "gddr6" not in text.lower():
            full = urljoin(page_url, href)
            if full != page_url and full not in found:
                found.append(full)
                if len(found) >= limit:
                    break
    return found


class _SlovenianShopWatcher(BaseWatcher):
    country = "SI"
    urls: tuple[str, ...] = ()
    open_box_urls: tuple[str, ...] = ()
    card_selectors: tuple[str, ...] = ()
    follow_filter_links: bool = True

    async def scan(self, fetcher: PoliteFetcher) -> WatcherResult:
        discovered: dict[str, RawOffer] = {}
        urls_checked = 0
        blocked = 0
        errors: list[str] = []
        visited: set[str] = set()
        queue: list[tuple[str, Condition, bool]] = [
            (u, Condition.NEW, self.follow_filter_links) for u in self.urls
        ] + [(u, Condition.OPEN_BOX, False) for u in self.open_box_urls]

        total_initial = len(queue)
        while queue:
            url, default_cond, should_follow = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            urls_checked += 1
            resp = await fetcher.get(url)
            if resp.blocked:
                blocked += 1
                errors.append(f"{url}: {resp.error}")
                continue
            if not resp.ok:
                errors.append(f"{url}: HTTP {resp.status_code} {resp.error}".strip())
                continue

            for offer in self.parse_html(resp.text, resp.url, default_condition=default_cond):
                existing = discovered.get(offer.offer_key)
                if existing is None or offer.listed_price_eur < existing.listed_price_eur:
                    discovered[offer.offer_key] = offer

            if should_follow:
                for filter_url in discover_9070_xt_filter_links(resp.text, resp.url):
                    if filter_url not in visited:
                        queue.append((filter_url, default_cond, False))

        status = "ok"
        if blocked >= total_initial and not discovered:
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

    def parse_html(
        self,
        html: str,
        page_url: str,
        *,
        default_condition: Condition = Condition.NEW,
    ) -> list[RawOffer]:
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
            default_condition=default_condition,
        )
        by_key = {o.offer_key: o for o in ld}
        for c in cards:
            by_key.setdefault(c.offer_key, c)
        return list(by_key.values())


class MlacomWatcher(_SlovenianShopWatcher):
    name = "mlacom"
    display_name = "Mlacom.si (SI)"
    urls = (
        "https://www.mlacom.si/komponente/graficne-kartice",
    )
    card_selectors = (
        ".product-item",
        ".product-card",
        ".item-box",
        "article.product",
        "[class*='product']",
    )


class FuntechWatcher(_SlovenianShopWatcher):
    name = "funtech"
    display_name = "Funtech.si (SI)"
    urls = (
        "https://funtech.si/category/vrh/racunalnistvo/komponente/graficne-kartice/",
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
        "https://anni.si/racunalnistvo/racunalniske-komponente/graficne-kartice/",
    )
    open_box_urls = (
        "https://anni.si/odprta-embalaza/",
        "https://anni.si/odprodaja/",
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
        "https://www.bigbang.si/izdelki/racunalnistvo/komponente/graficne-kartice/",
    )
    card_selectors = (
        ".cp-card",
        ".product-card",
        "article[class*='product']",
        ".product-item",
    )
