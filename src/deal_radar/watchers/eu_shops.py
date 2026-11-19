"""EU retailers shipping directly to Slovenia: ComputerUniverse, Amazon.de (+Warehouse),
Alza (+Open-Box), Alternate.de (+Outlet), and Idealo.de EU price benchmark."""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..fetcher import PoliteFetcher
from ..matching import (
    detect_condition,
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


def parse_amazon_de_page(
    html: str,
    page_url: str,
    *,
    shipping_eur: float = 0.0,
    default_condition: Condition = Condition.NEW,
) -> list[RawOffer]:
    """Dedicated parser for Amazon.de regular and Retourenkauf / Warehouse search results."""
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

        # Amazon puts product titles in <h2> (either <h2><a><span>Title</span></a></h2> or <a><h2><span>Title</span></h2></a>)
        h2 = card.find("h2")
        title = h2.get_text(" ", strip=True) if h2 else ""
        if not is_rx_9070_xt(title):
            continue

        link = (h2.find("a", href=True) if h2 else None) or (
            h2.find_parent("a", href=True) if h2 else None
        ) or card.find("a", href=True)
        href = str(link["href"]) if link else f"/dp/{asin}"
        offer_url = urljoin("https://www.amazon.de", href.split("ref=")[0])

        # Price: Amazon puts clean formatted price inside <span class="a-price"><span class="a-offscreen">
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

        # Fallback for secondary / used buying options text ("Gebraucht ab 649,00 €")
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
            cond_note = "Amazon Retourenkauf / Warehouse Deals"

        in_stock, stock_text = infer_stock_from_text(card_text, default=True)
        promos = extract_promos_from_text(card_text, store="amazon_de")

        img = card.select_one("img.s-image")
        image_url = str(img.get("src") or "") if img else ""

        key = make_offer_key("amazon_de", offer_url, sku=asin, condition=cond)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        offers.append(
            RawOffer(
                store="amazon_de",
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
                seller_name="Amazon Retourenkauf" if cond.is_open_box_like else "Amazon.de",
                ships_to_slovenia=True,
                detected_promos=promos,
                sku=asin,
                image_url=image_url,
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

    def parse_html(
        self,
        html: str,
        page_url: str,
        *,
        shipping_eur: float,
        default_condition: Condition,
    ) -> list[RawOffer]:
        return parse_amazon_de_page(
            html,
            page_url,
            shipping_eur=shipping_eur,
            default_condition=default_condition,
        )


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
