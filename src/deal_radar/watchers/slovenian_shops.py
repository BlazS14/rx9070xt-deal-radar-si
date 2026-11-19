"""Dedicated watchers for major Slovenian PC hardware shops: Mlacom, Funtech, Anni, Big Bang.

Uses verified category URLs, clearance/outlet feeds, and verified direct product page seeds.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..fetcher import PoliteFetcher
from ..matching import (
    detect_condition,
    extract_all_prices_eur,
    extract_promos_from_text,
    is_rx_9070_xt,
)
from ..models import Condition, RawOffer, WatcherResult
from .base import (
    BaseWatcher,
    infer_stock_from_text,
    make_offer_key,
    parse_generic_product_cards,
    parse_json_ld_products,
)

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
        if _FILTER_9070_XT_RE.search(text) and len(text) <= 45 and "gddr6" not in text.lower():
            full = urljoin(page_url, href)
            if full != page_url and full not in found:
                found.append(full)
                if len(found) >= limit:
                    break
    return found


def parse_single_product_fallback(
    html: str, page_url: str, *, store: str, default_condition: Condition = Condition.NEW
) -> RawOffer | None:
    """Parse an individual product page via JSON-LD or h1 + price extraction."""
    soup = BeautifulSoup(html, "lxml")
    full_text = soup.get_text(" ", strip=True)
    ld = parse_json_ld_products(
        soup,
        store=store,
        page_url=page_url,
        ships_to_slovenia=True,
        page_text_for_promos=full_text,
    )
    if ld:
        return ld[0]

    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    if not is_rx_9070_xt(title, full_text[:2000]):
        return None
    prices = extract_all_prices_eur(full_text[:5000])
    if not prices:
        return None
    listed = prices[0]
    original = next((p for p in prices[1:4] if listed + 5.0 < p <= listed * 1.6), None)
    cond, cond_note = detect_condition(f"{title} {page_url} {full_text[:2000]}")
    if cond == Condition.NEW and default_condition != Condition.NEW:
        cond = default_condition
    in_stock, stock_text = infer_stock_from_text(full_text[:4500], default=True)
    return RawOffer(
        store=store,
        offer_key=make_offer_key(store, page_url, condition=cond),
        title=title,
        url=page_url,
        listed_price_eur=listed,
        original_price_eur=original,
        in_stock=in_stock,
        stock_text=stock_text,
        condition=cond,
        condition_note=cond_note,
        ships_to_slovenia=True,
        detected_promos=extract_promos_from_text(full_text, store=store),
    )


class _SlovenianShopWatcher(BaseWatcher):
    country = "SI"
    urls: tuple[str, ...] = ()
    open_box_urls: tuple[str, ...] = ()
    seed_product_urls: tuple[str, ...] = ()
    card_selectors: tuple[str, ...] = ()
    follow_filter_links: bool = True

    async def scan(self, fetcher: PoliteFetcher) -> WatcherResult:
        discovered: dict[str, RawOffer] = {}
        urls_checked = 0
        blocked = 0
        errors: list[str] = []
        visited: set[str] = set()
        queue: list[tuple[str, Condition, bool, bool]] = (
            [(u, Condition.NEW, self.follow_filter_links, False) for u in self.urls]
            + [(u, Condition.OPEN_BOX, False, False) for u in self.open_box_urls]
            + [(u, Condition.NEW, False, True) for u in self.seed_product_urls]
        )

        total_initial = len(queue)
        while queue:
            url, default_cond, should_follow, is_single = queue.pop(0)
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

            if is_single:
                single = parse_single_product_fallback(
                    resp.text, resp.url, store=self.name, default_condition=default_cond
                )
                if single is not None:
                    existing = discovered.get(single.offer_key)
                    if existing is None or single.listed_price_eur < existing.listed_price_eur:
                        discovered[single.offer_key] = single
                continue

            for offer in self.parse_html(resp.text, resp.url, default_condition=default_cond):
                existing = discovered.get(offer.offer_key)
                if existing is None or offer.listed_price_eur < existing.listed_price_eur:
                    discovered[offer.offer_key] = offer

            if should_follow:
                for filter_url in discover_9070_xt_filter_links(resp.text, resp.url):
                    if filter_url not in visited:
                        queue.append((filter_url, default_cond, False, False))

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
    seed_product_urls = (
        "https://www.mlacom.si/komponente/graficne-kartice/i_3032030_sapphire-nitro-amd-radeon-rx-9070-xt-16gb-11348-01-20g-graficna-kartica",
        "https://www.mlacom.si/komponente/graficne-kartice/i_3032045_gigabyte-radeon-rx-9070-xt-gaming-oc-16g-gv-r9070xtgaming-oc-16gd-graficna-kartica",
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
    display_name = "Anni.si (SI + Odprodaja)"
    urls = (
        "https://anni.si/racunalnistvo/racunalniske-komponente/graficne-kartice/",
    )
    # Verified live clearance page (carries RX 9070 XT clearance stock!)
    open_box_urls = (
        "https://anni.si/odprodaja",
    )
    seed_product_urls = (
        "https://anni.si/graficne-kartice/graficna-kartica-gigabyte-radeon-rx-9070-xt-gaming-oc-16gb-gddr6",
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
    seed_product_urls = (
        "https://www.bigbang.si/asrock-radeon-rx-9070-xt-taichi-16gb-oc-izdelek-5000319354/",
        "https://www.bigbang.si/tuf-gaming-radeon-rx-9070-asus-xt-gddr6-oc-izdelek-21833359/",
    )
    card_selectors = (
        ".cp-card",
        ".product-card",
        "article[class*='product']",
        ".product-item",
    )
