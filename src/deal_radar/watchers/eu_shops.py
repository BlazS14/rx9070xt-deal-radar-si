"""EU retailers & pan-EU aggregators shipping to Slovenia:
- GPUTracker.eu (pan-EU aggregator: 800+ RX 9070 XT listings across 40+ EU shops!)
- Geizhals.eu / Geizhals.at / Geizhals.de (AT/DE/EU lowest-price benchmark)
- Amazon.de, Amazon.it, Amazon.fr, Amazon.es (all ship to SI + Warehouse/Retourenkauf!)
- Alternate.de (+ Outlet)
- ComputerUniverse.net (+ B-Ware)
- Alza.de (+ Open-Box)
- Proshop.de / Proshop.at
- Reichelt.com / Reichelt.de (DE -> SI ~€9.95)
- Notebooksbilliger.de (NBB)
- LDLC.com (FR -> SI)
- BPM-Power.com (IT -> SI)
- Cyberport.at (AT -> SI)
- Galaxus.at / Galaxus.de (AT/DE)
- Idealo.de (filtered cleanly to OffersOfProduct)
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
    parse_price_eur,
)
from ..models import Condition, RawOffer, WatcherResult
from .base import (
    BaseWatcher,
    infer_stock_from_text,
    make_offer_key,
    parse_generic_product_cards,
    parse_json_ld_products,
)

_GPUTRACKER_SHOP_RE = re.compile(
    r"\b([a-z0-9-]+\.(?:de|at|fr|it|es|nl|be|eu|com|co\.uk|cz|hu|pl|pt|dk|fi))\b",
    re.IGNORECASE,
)


def parse_amazon_eu_page(
    html: str,
    page_url: str,
    *,
    store: str = "amazon_de",
    base_domain: str = "https://www.amazon.de",
    shipping_eur: float = 0.0,
    default_condition: Condition = Condition.NEW,
) -> list[RawOffer]:
    """Dedicated parser for Amazon EU (DE/IT/FR/ES) regular & Warehouse/Retourenkauf pages."""
    soup = BeautifulSoup(html, "lxml")
    offers: list[RawOffer] = []
    seen_keys: set[str] = set()

    cards = soup.select("div[data-component-type='s-search-result'][data-asin]")
    if not cards:
        cards = soup.select(".s-result-item[data-asin]")

    for card in cards:
        asin = str(card.get("data-asin") or "").strip()
        if not asin:
            continue

        h2 = card.find("h2")
        title = h2.get_text(" ", strip=True) if h2 else ""
        if not is_rx_9070_xt(title):
            continue

        link = (
            (h2.find("a", href=True) if h2 else None)
            or (h2.find_parent("a", href=True) if h2 else None)
            or card.find("a", href=True)
        )
        href = str(link["href"]) if link else f"/dp/{asin}"
        offer_url = urljoin(base_domain, href.split("ref=")[0])

        listed_price: float | None = None
        original_price: float | None = None

        for price_wrap in card.select("span.a-price"):
            classes = set(price_wrap.get("class") or [])
            offscreen = price_wrap.select_one("span.a-offscreen")
            raw_p = offscreen.get_text(" ", strip=True) if offscreen else ""
            if not raw_p:
                whole = price_wrap.select_one("span.a-price-whole")
                frac = price_wrap.select_one("span.a-price-fraction")
                if whole:
                    w_txt = whole.get_text("", strip=True).rstrip(",.")
                    f_txt = frac.get_text("", strip=True) if frac else "00"
                    raw_p = f"{w_txt},{f_txt} €"

            val = parse_price_eur(raw_p)
            if val is None:
                continue

            if "a-text-price" in classes or price_wrap.get("data-a-strike") == "true":
                original_price = max(original_price or 0.0, val)
            elif listed_price is None or val < listed_price:
                listed_price = val

        card_text = card.get_text(" ", strip=True)
        if listed_price is None:
            listed_price = parse_price_eur(card_text, require_currency=True)
        if listed_price is None:
            continue

        if original_price is not None and original_price <= listed_price:
            original_price = None

        cond, cond_note = detect_condition(f"{title} {card_text}")
        if cond == Condition.NEW and default_condition != Condition.NEW:
            cond = default_condition
            cond_note = "Amazon Warehouse / Second-hand"

        in_stock, stock_text = infer_stock_from_text(card_text, default=True)
        promos = extract_promos_from_text(card_text, store=store)

        img = card.select_one("img.s-image")
        image_url = str(img.get("src") or "") if img else ""

        key = make_offer_key(store, offer_url, sku=asin, condition=cond)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        offers.append(
            RawOffer(
                store=store,
                offer_key=key,
                title=title,
                url=offer_url,
                listed_price_eur=listed_price,
                original_price_eur=original_price,
                shipping_eur=shipping_eur,
                in_stock=in_stock,
                stock_text=stock_text,
                condition=cond,
                condition_note=cond_note,
                seller_name=f"{store} Warehouse" if cond.is_open_box_like else store,
                ships_to_slovenia=True,
                detected_promos=promos,
                sku=asin,
                image_url=image_url,
            )
        )

    return offers


# Backwards-compatible alias for existing tests/imports
parse_amazon_de_page = parse_amazon_eu_page


def parse_gputracker_page(
    html: str, page_url: str, *, shipping_eur: float = 15.99
) -> list[RawOffer]:
    """Parse GPUTracker.eu RX 9070 XT search results across 40+ European shops."""
    soup = BeautifulSoup(html, "lxml")
    offers: list[RawOffer] = []
    seen_keys: set[str] = set()

    cards = soup.select(".stock-card, .card, article, [class*='offer'], [class*='product-row']")
    if not cards:
        # Fallback: find every "View offer" link and inspect its parent container
        for a in soup.find_all("a", href=True):
            if "view offer" in a.get_text(" ", strip=True).lower():
                parent = a.find_parent(["div", "article", "li"])
                if parent:
                    cards.append(parent)

    for card in cards:
        text = card.get_text(" ", strip=True)
        if not is_rx_9070_xt(text):
            continue

        prices = extract_all_prices_eur(text)
        if not prices:
            continue
        listed_price = min(prices)

        # Find best product title and external/offer link
        title = ""
        offer_url = page_url
        for h in card.find_all(["h2", "h3", "h4", "h5", "a"]):
            t = h.get_text(" ", strip=True)
            if 18 <= len(t) <= 180 and is_rx_9070_xt(t) and "view offer" not in t.lower() and "create an alert" not in t.lower():
                title = t
                if h.name == "a" and h.get("href"):
                    offer_url = urljoin(page_url, str(h["href"]))
                break

        if not title:
            continue

        for a in card.find_all("a", href=True):
            if "view offer" in a.get_text(" ", strip=True).lower():
                offer_url = urljoin(page_url, str(a["href"]))
                break

        shop_m = _GPUTRACKER_SHOP_RE.search(text)
        seller = shop_m.group(1).lower() if shop_m else "gputracker.eu"
        in_stock, stock_text = infer_stock_from_text(text, default=True)
        cond, cond_note = detect_condition(f"{title} {text}")

        # Dedup by seller + title + price path
        key = make_offer_key("gputracker_eu", f"{seller}/{title[:60]}", condition=cond)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        offers.append(
            RawOffer(
                store="gputracker_eu",
                offer_key=key,
                title=f"{title} [{seller}]" if seller not in title.lower() else title,
                url=offer_url,
                listed_price_eur=listed_price,
                shipping_eur=shipping_eur,
                in_stock=in_stock,
                stock_text=stock_text,
                condition=cond,
                condition_note=cond_note,
                seller_name=seller,
                ships_to_slovenia=True,
                detected_promos=extract_promos_from_text(text, store="gputracker_eu"),
            )
        )

    return offers


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
            message=(
                f"Found {len(discovered)} RX 9070 XT offers"
                if discovered
                else (errors[0] if errors else "No offers found")
            ),
            urls_checked=urls_checked,
        )


class GpuTrackerEuWatcher(_EUVendorWatcher):
    """GPUTracker.eu — Pan-EU GPU aggregator tracking 800+ RX 9070 XT listings across 40+ EU stores."""

    name = "gputracker_eu"
    display_name = "GPUTracker.eu (40+ EU Stores)"
    country = "EU"
    default_shipping_eur = 15.99
    accept_language = "en-US,en;q=0.9"

    def get_targets(self) -> list[tuple[str, Condition]]:
        return [
            (
                "https://www.gputracker.eu/en/search/category/1/graphics-cards/facet/2/graphics-chip/amd-rx-9070-xt?onlyInStock=true",
                Condition.NEW,
            ),
            (
                "https://www.gputracker.eu/en/search/category/1/graphics-cards/facet/2/graphics-chip/amd-rx-9070-xt",
                Condition.NEW,
            ),
        ]

    def parse_html(
        self,
        html: str,
        page_url: str,
        *,
        shipping_eur: float,
        default_condition: Condition,
    ) -> list[RawOffer]:
        return parse_gputracker_page(html, page_url, shipping_eur=shipping_eur)


class GeizhalsEuWatcher(_EUVendorWatcher):
    """Geizhals.eu / AT / DE — EU hardware price aggregator sorted by lowest price."""

    name = "geizhals_eu"
    display_name = "Geizhals AT/DE/EU Aggregator"
    country = "AT"
    default_shipping_eur = 14.99
    accept_language = "de-AT,de;q=0.9,en;q=0.8"
    card_selectors = (
        ".listview__item",
        ".productlist__product",
        "article.product",
        "[data-testid='product-item']",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        return [
            ("https://geizhals.eu/?fs=rx+9070+xt&sort=p&hloc=at&hloc=de&hloc=eu", Condition.NEW),
            ("https://geizhals.de/xfx-radeon-rx-9070-xt-v191876.html", Condition.NEW),
            ("https://geizhals.de/asus-radeon-rx-9070-xt-v191864.html", Condition.NEW),
            ("https://geizhals.at/powercolor-radeon-rx-9070-xt-v188670.html", Condition.NEW),
        ]


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
                ("https://www.computeruniverse.net/en/search?query=rx+9070+xt+b-ware", Condition.NEW)
            )
        return targets


class _AmazonRegionalWatcher(_EUVendorWatcher):
    base_domain = "https://www.amazon.de"
    warehouse_index = "warehouse-deals"

    def get_targets(self) -> list[tuple[str, Condition]]:
        targets: list[tuple[str, Condition]] = [
            (f"{self.base_domain}/s?k=rx+9070+xt&i=computers&s=price-asc-rank", Condition.NEW),
        ]
        if self.options.get("scan_warehouse_deals", True):
            targets.append(
                (
                    f"{self.base_domain}/s?k=rx+9070+xt&i={self.warehouse_index}&s=price-asc-rank",
                    Condition.B_WARE,
                )
            )
        return targets

    def parse_html(
        self,
        html: str,
        page_url: str,
        *,
        shipping_eur: float,
        default_condition: Condition,
    ) -> list[RawOffer]:
        return parse_amazon_eu_page(
            html,
            page_url,
            store=self.name,
            base_domain=self.base_domain,
            shipping_eur=shipping_eur,
            default_condition=default_condition,
        )


class AmazonDeWatcher(_AmazonRegionalWatcher):
    """Amazon.de — free shipping to Slovenia >= €59 + Amazon Retourenkauf (Warehouse Deals)."""

    name = "amazon_de"
    display_name = "Amazon.de + Warehouse (DE -> SI)"
    country = "DE"
    default_shipping_eur = 0.0
    accept_language = "de-DE,de;q=0.9,en;q=0.8"
    base_domain = "https://www.amazon.de"


class AmazonItWatcher(_AmazonRegionalWatcher):
    """Amazon.it — ships directly to Slovenia + Amazon Seconda Mano (Warehouse Deals)."""

    name = "amazon_it"
    display_name = "Amazon.it + Warehouse (IT -> SI)"
    country = "IT"
    default_shipping_eur = 9.99
    accept_language = "it-IT,it;q=0.9,en;q=0.8"
    base_domain = "https://www.amazon.it"


class AmazonFrWatcher(_AmazonRegionalWatcher):
    """Amazon.fr — ships directly to Slovenia + Amazon Seconde Main (Warehouse Deals)."""

    name = "amazon_fr"
    display_name = "Amazon.fr + Warehouse (FR -> SI)"
    country = "FR"
    default_shipping_eur = 11.99
    accept_language = "fr-FR,fr;q=0.9,en;q=0.8"
    base_domain = "https://www.amazon.fr"


class AmazonEsWatcher(_AmazonRegionalWatcher):
    """Amazon.es — ships directly to Slovenia + Amazon Segunda Mano (Warehouse Deals)."""

    name = "amazon_es"
    display_name = "Amazon.es + Warehouse (ES -> SI)"
    country = "ES"
    default_shipping_eur = 12.99
    accept_language = "es-ES,es;q=0.9,en;q=0.8"
    base_domain = "https://www.amazon.es"


class AlternateDeWatcher(_EUVendorWatcher):
    """Alternate.de — dedicated RX 9070 XT catalog (30+ models) & Outlet."""

    name = "alternate_de"
    display_name = "Alternate.de + Outlet (DE -> SI)"
    default_shipping_eur = 16.99
    accept_language = "de-DE,de;q=0.9,en;q=0.8"
    card_selectors = (
        "a.productBox",
        ".productBox",
        ".card.align-content-center",
        "article[class*='product']",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        targets: list[tuple[str, Condition]] = [
            ("https://www.alternate.de/Grafikkarten/RX-9070-XT", Condition.NEW),
        ]
        if self.options.get("scan_outlet", True):
            targets.append(
                ("https://www.alternate.de/Outlet/Grafikkarten?q=9070+xt", Condition.B_WARE)
            )
        return targets


class ProshopWatcher(_EUVendorWatcher):
    """Proshop.de / Proshop.at — dedicated RX 9070 XT catalog + Demo/Open-Box cards."""

    name = "proshop"
    display_name = "Proshop.de / AT (EU)"
    country = "DE"
    default_shipping_eur = 14.99
    accept_language = "de-DE,de;q=0.9,en;q=0.8"
    card_selectors = (
        "li.row.toggle",
        ".site-productlist-item",
        "[data-testid='product-item']",
        "article[class*='product']",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        return [
            ("https://www.proshop.de/AMD-Radeon-RX-9070-XT", Condition.NEW),
            ("https://www.proshop.at/AMD-Radeon-RX-9070-XT", Condition.NEW),
        ]


class ReicheltWatcher(_EUVendorWatcher):
    """Reichelt.com / Reichelt.de — ships directly to Slovenia (~€9.95)."""

    name = "reichelt"
    display_name = "Reichelt.de (DE -> SI)"
    country = "DE"
    default_shipping_eur = 9.95
    accept_language = "en-US,en;q=0.9,de;q=0.8"
    card_selectors = (
        ".al_gallery_article",
        ".al_article",
        "[data-testid='product-tile']",
        "article[class*='product']",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        return [
            ("https://www.reichelt.com/de/en/shop/search/rx%209070%20xt", Condition.NEW),
            ("https://www.reichelt.de/de/de/shop/suche/rx%209070%20xt", Condition.NEW),
        ]


class NotebooksbilligerWatcher(_EUVendorWatcher):
    """Notebooksbilliger.de (NBB) — verified RX 9070 XT category + B-Ware deals."""

    name = "notebooksbilliger"
    display_name = "Notebooksbilliger.de (DE -> SI)"
    country = "DE"
    default_shipping_eur = 14.99
    accept_language = "de-DE,de;q=0.9,en;q=0.8"
    card_selectors = (
        ".product-card",
        ".js-product-card",
        "[data-testid='product-card']",
        "article[class*='product']",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        return [
            (
                "https://www.notebooksbilliger.de/pc+hardware/grafikkarten+pc+hardware/amdati/rx+9700+xt+amdati",
                Condition.NEW,
            ),
        ]


class LdlcWatcher(_EUVendorWatcher):
    """LDLC.com (EN) — major French PC retailer shipping directly to Slovenia."""

    name = "ldlc"
    display_name = "LDLC.com (FR -> SI)"
    country = "FR"
    default_shipping_eur = 15.95
    accept_language = "en-US,en;q=0.9,fr;q=0.8"
    card_selectors = (
        "li.pdt-item",
        ".pdt-item",
        "article[class*='product']",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        return [
            ("https://www.ldlc.com/en/search/rx%209070%20xt/", Condition.NEW),
        ]


class BpmPowerWatcher(_EUVendorWatcher):
    """BPM-Power.com — Italian hardware store shipping to Slovenia, often cheapest in Southern EU."""

    name = "bpm_power"
    display_name = "BPM-Power.com (IT -> SI)"
    country = "IT"
    default_shipping_eur = 14.90
    accept_language = "it-IT,it;q=0.9,en;q=0.8"
    card_selectors = (
        ".product-card",
        ".item-product",
        ".boxProdotto",
        "article[class*='product']",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        return [
            ("https://www.bpm-power.com/it/ricerca?k=rx+9070+xt", Condition.NEW),
        ]


class CyberportWatcher(_EUVendorWatcher):
    """Cyberport.at / Cyberport.de — Austrian/German store close to Slovenia + B-Ware."""

    name = "cyberport"
    display_name = "Cyberport.at (AT -> SI)"
    country = "AT"
    default_shipping_eur = 14.99
    accept_language = "de-AT,de;q=0.9,en;q=0.8"
    card_selectors = (
        "article.productArticle",
        "[data-testid='product-card']",
        ".product-card",
        "article[class*='product']",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        return [
            ("https://www.cyberport.at/tools/search-results.html?q=rx+9070+xt", Condition.NEW),
        ]


class GalaxusWatcher(_EUVendorWatcher):
    """Galaxus.at / Galaxus.de — AT/DE retailer with live stock + Secondhand/Returned GPUs."""

    name = "galaxus"
    display_name = "Galaxus.at / DE (AT/DE)"
    country = "AT"
    default_shipping_eur = 14.99
    accept_language = "en-US,en;q=0.9,de;q=0.8"
    card_selectors = (
        "article[data-test='product-tile']",
        "article",
        "[class*='product']",
    )

    def get_targets(self) -> list[tuple[str, Condition]]:
        return [
            ("https://www.galaxus.at/en/search?q=rx+9070+xt", Condition.NEW),
            ("https://www.galaxus.de/en/search?q=rx+9070+xt", Condition.NEW),
        ]


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
    """Idealo.de — tracks lowest German/EU RX 9070 XT offers (filtered cleanly to real products)."""

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

    def parse_html(
        self,
        html: str,
        page_url: str,
        *,
        shipping_eur: float,
        default_condition: Condition,
    ) -> list[RawOffer]:
        offers = super().parse_html(
            html, page_url, shipping_eur=shipping_eur, default_condition=default_condition
        )
        # Filter out editorial/sidebar category snippets like bare "RX 9070 XT"
        return [
            o
            for o in offers
            if "/OffersOfProduct/" in o.url or len(o.title) >= 22
        ]
