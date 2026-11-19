"""EU retailers shipping directly to Slovenia: ComputerUniverse, Amazon.de (+Warehouse),
Alza (+Open-Box), and Idealo.de EU price benchmark."""

from __future__ import annotations

from bs4 import BeautifulSoup

from ..fetcher import PoliteFetcher
from ..models import Condition, RawOffer, WatcherResult
from .base import BaseWatcher, parse_generic_product_cards, parse_json_ld_products


class _EUVendorWatcher(BaseWatcher):
    country = "DE"
    default_shipping_eur = 15.99
    accept_language = "de-DE,de;q=0.9,en;q=0.8"
    card_selectors: tuple[str, ...] = ()

    def get_targets(self) -> list[tuple[str, Condition]]:
        return []

    def parse_html(
        self,
        html: str,
        page_url: str,
        *,
        shipping_eur: float,
        default_condition: Condition,
    ) -> list[RawOffer]:
        soup = BeautifulSoup(html, "lxml")
        ld = parse_json_ld_products(
            soup,
            store=self.name,
            page_url=page_url,
            shipping_eur=shipping_eur,
            ships_to_slovenia=True,
        )
        cards = parse_generic_product_cards(
            soup,
            store=self.name,
            page_url=page_url,
            card_selectors=list(self.card_selectors),
            shipping_eur=shipping_eur,
            ships_to_slovenia=True,
            default_condition=default_condition,
        )
        by_key = {o.offer_key: o for o in ld}
        for c in cards:
            by_key.setdefault(c.offer_key, c)
        return list(by_key.values())

    async def scan(self, fetcher: PoliteFetcher) -> WatcherResult:
        shipping = float(self.options.get("shipping_to_slovenia_eur", self.default_shipping_eur))
        targets = self.get_targets()
        discovered: dict[str, RawOffer] = {}
        urls_checked = 0
        blocked = 0
        errors: list[str] = []

        for url, default_cond in targets:
            urls_checked += 1
            resp = await fetcher.get(url, accept_language=self.accept_language)
            if resp.blocked:
                blocked += 1
                errors.append(resp.error)
                continue
            if not resp.ok:
                errors.append(f"HTTP {resp.status_code} {resp.error}".strip())
                continue

            for offer in self.parse_html(
                resp.text,
                resp.url,
                shipping_eur=shipping,
                default_condition=default_cond,
            ):
                existing = discovered.get(offer.offer_key)
                if existing is None or offer.listed_price_eur < existing.listed_price_eur:
                    discovered[offer.offer_key] = offer

        status = "ok"
        if blocked == len(targets) and not discovered:
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


class ComputerUniverseWatcher(_EUVendorWatcher):
    """ComputerUniverse.net — ships directly to Slovenia, great B-Ware / Outlet GPU drops."""

    name = "computeruniverse"
    display_name = "ComputerUniverse.net (DE -> SI)"
    default_shipping_eur = 16.90
    accept_language = "en-US,en;q=0.9,de;q=0.8"
    card_selectors = (
        "[data-testid='product-list-item']",
        ".c-productItem",
        "article[class*='product']",
        ".product-card",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        targets: list[tuple[str, Condition]] = [
            ("https://www.computeruniverse.net/en/search?query=rx+9070+xt", Condition.NEW),
        ]
        if self.options.get("scan_bware", True):
            targets.append(
                (
                    "https://www.computeruniverse.net/en/search?query=rx+9070+xt+b-ware",
                    Condition.B_WARE,
                )
            )
        return targets


class AmazonDeWatcher(_EUVendorWatcher):
    """Amazon.de — free shipping to Slovenia >= €59 + Amazon Retourenkauf (Warehouse Deals)."""

    name = "amazon_de"
    display_name = "Amazon.de + Warehouse (DE -> SI)"
    default_shipping_eur = 0.0
    accept_language = "de-DE,de;q=0.9,en;q=0.8"
    card_selectors = (
        "div[data-component-type='s-search-result']",
        ".s-result-item[data-asin]",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        targets: list[tuple[str, Condition]] = [
            ("https://www.amazon.de/s?k=rx+9070+xt&i=computers&s=price-asc-rank", Condition.NEW),
        ]
        if self.options.get("scan_warehouse_deals", True):
            targets.append(
                (
                    "https://www.amazon.de/s?k=rx+9070+xt&i=warehouse-deals&s=price-asc-rank",
                    Condition.B_WARE,
                )
            )
        return targets


class AlzaWatcher(_EUVendorWatcher):
    """Alza.de / Alzashop — ships to Slovenia and frequently lists Open-Box / Rozbaleno GPUs."""

    name = "alza"
    display_name = "Alza.de + Open-Box (EU -> SI)"
    country = "EU"
    default_shipping_eur = 9.90
    accept_language = "en-US,en;q=0.9,de;q=0.8"
    card_selectors = (
        ".box.browsingitem",
        "[data-testid='product-card']",
        ".browsingitem",
        "article[class*='product']",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        targets: list[tuple[str, Condition]] = [
            ("https://www.alza.de/search.htm?exps=rx+9070+xt", Condition.NEW),
        ]
        if self.options.get("scan_open_box", True):
            targets.append(
                ("https://www.alza.de/search.htm?exps=rx+9070+xt+ge%C3%B6ffnet", Condition.OPEN_BOX)
            )
        return targets


class IdealoDeWatcher(_EUVendorWatcher):
    """Idealo.de — tracks lowest German/EU RX 9070 XT prices (many shops ship to Slovenia)."""

    name = "idealo_de"
    display_name = "Idealo.de (EU Price Radar)"
    default_shipping_eur = 15.99
    accept_language = "de-DE,de;q=0.9,en;q=0.8"
    card_selectors = (
        ".sr-resultList__item",
        "[data-testid='resultItem']",
        "article[class*='offer']",
        ".offerList-item",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        return [
            (
                "https://www.idealo.de/preisvergleich/ProductCategory/16073F106595217.html?q=rx+9070+xt&sortKey=minPrice",
                Condition.NEW,
            ),
        ]
