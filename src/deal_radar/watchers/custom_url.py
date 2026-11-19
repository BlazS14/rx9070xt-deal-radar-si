"""Custom user-configured URL watcher — lets users track any GPU page or outlet URL."""

from __future__ import annotations

from bs4 import BeautifulSoup

from ..fetcher import PoliteFetcher
from ..matching import (
    detect_condition,
    extract_all_prices_eur,
    extract_promos_from_text,
    is_rx_9070_xt,
)
from ..models import RawOffer, WatcherResult
from .base import (
    BaseWatcher,
    infer_stock_from_text,
    make_offer_key,
    parse_generic_product_cards,
    parse_json_ld_products,
)
from .mimovrste import parse_mimovrste_product_page


class CustomUrlsWatcher(BaseWatcher):
    """Tracks arbitrary URLs listed under [[custom_urls]] in config.toml."""

    name = "custom_urls"
    display_name = "Custom Watchlist URLs"
    country = "EU"

    async def scan(self, fetcher: PoliteFetcher) -> WatcherResult:
        if not self.config.custom_urls:
            return WatcherResult(
                watcher_name=self.name,
                offers=[],
                status="ok",
                message="No custom URLs configured",
                urls_checked=0,
            )

        discovered: dict[str, RawOffer] = {}
        urls_checked = 0
        errors: list[str] = []

        for entry in self.config.custom_urls:
            urls_checked += 1
            resp = await fetcher.get(entry.url)
            if not resp.ok:
                errors.append(f"{entry.name}: {resp.error or f'HTTP {resp.status_code}'}")
                continue

            if "mimovrste.com" in resp.url.lower():
                detailed, _ = parse_mimovrste_product_page(resp.text, resp.url)
                if detailed is not None:
                    detailed.shipping_eur = entry.shipping_to_slovenia_eur
                    detailed.ships_to_slovenia = entry.ships_to_slovenia
                    discovered[detailed.offer_key] = detailed
                    continue

            soup = BeautifulSoup(resp.text, "lxml")
            full_text = soup.get_text(" ", strip=True)
            offers = parse_json_ld_products(
                soup,
                store=entry.store,
                page_url=resp.url,
                shipping_eur=entry.shipping_to_slovenia_eur,
                ships_to_slovenia=entry.ships_to_slovenia,
                page_text_for_promos=full_text,
            )
            if not offers:
                offers = parse_generic_product_cards(
                    soup,
                    store=entry.store,
                    page_url=resp.url,
                    card_selectors=["article", ".product", ".product-card"],
                    shipping_eur=entry.shipping_to_slovenia_eur,
                    ships_to_slovenia=entry.ships_to_slovenia,
                )

            if not offers:
                # Single-product fallback
                h1 = soup.find("h1")
                title = h1.get_text(" ", strip=True) if h1 else entry.name
                prices = extract_all_prices_eur(full_text[:5000])
                if prices and is_rx_9070_xt(title, full_text[:1500]):
                    cond, cond_note = detect_condition(f"{title} {resp.url} {full_text[:2000]}")
                    in_stock, stock_text = infer_stock_from_text(full_text[:4000], default=True)
                    offers.append(
                        RawOffer(
                            store=entry.store,
                            offer_key=make_offer_key(entry.store, resp.url, condition=cond),
                            title=title,
                            url=resp.url,
                            listed_price_eur=min(prices[:3]),
                            shipping_eur=entry.shipping_to_slovenia_eur,
                            in_stock=in_stock,
                            stock_text=stock_text,
                            condition=cond,
                            condition_note=cond_note,
                            ships_to_slovenia=entry.ships_to_slovenia,
                            detected_promos=extract_promos_from_text(full_text, store=entry.store),
                        )
                    )

            for off in offers:
                discovered[off.offer_key] = off

        status = "ok" if not errors else ("partial" if discovered else "error")
        return WatcherResult(
            watcher_name=self.name,
            offers=list(discovered.values()),
            status=status,
            message=f"Found {len(discovered)} offers across {urls_checked} custom URLs"
            if not errors
            else errors[0],
            urls_checked=urls_checked,
        )
