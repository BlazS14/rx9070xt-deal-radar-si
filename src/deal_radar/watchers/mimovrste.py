"""Flagship Mimovrste.com watcher — tracks regular listings, Open-Box ('Odprta embalaža'),
Black Friday / promo code lists, verified RX 9070 XT product pages, first-party fulfillment,
and linked open-box sibling variants."""

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

_MIMOVRSTE_SEARCH_URLS = [
    "https://www.mimovrste.com/iskanje?s=rx+9070+xt",
    "https://www.mimovrste.com/graficne-kartice-amd",
]

_MIMOVRSTE_OPEN_BOX_URLS = [
    "https://www.mimovrste.com/kampanja/odprta-embalaza?category=graficne-kartice-gaming",
    "https://www.mimovrste.com/kampanja/artikli-z-odprto-embalazo?category=pc-komponente",
]

_MIMOVRSTE_PROMO_URLS = [
    "https://www.mimovrste.com/seznami/koda-za-popust-20?s=9070+xt",
    "https://www.mimovrste.com/kampanja/black-friday?s=9070+xt",
]

# Verified live Mimovrste RX 9070 XT product URLs (checked for price drops, promo codes, & open-box siblings)
_MIMOVRSTE_SEED_PRODUCTS = [
    "https://www.mimovrste.com/graficne-kartice-amd/sapphire-pure-amd-radeon-rx-9070-xt-gaming-oc-graficna-kartica-16gb-gddr6-11348-02-20g",
    "https://www.mimovrste.com/graficne-kartice-amd/sapphire-pulse-amd-radeon-rx-9070-xt-gaming-graficna-kartica-16gb-gddr6-11348-03-20g",
    "https://www.mimovrste.com/graficne-kartice-amd/sapphire-pulse-gaming-radeon-rx-9070-xt-100161811702",
    "https://www.mimovrste.com/graficne-kartice-amd/xfx-swift-amd-radeon-rx-9070-xt-white-triple-fan-gaming-edition-graficna-kartica-16gb-gddr6-rx-97tswf3w9",
    "https://www.mimovrste.com/graficne-kartice-amd/xfx-quicksilver-amd-radeon-rx-9070-xt-gaming-edition-graficna-kartica-16gb-gddr6-rx-97tqickb9",
    "https://www.mimovrste.com/graficne-kartice-amd/xfx-mercury-radeon-graficna-kartica-rx-9070-xt-oc-gaming-edition-rgb-16gb-gddr6-pcie-50-rx-97trgbbb9",
    "https://www.mimovrste.com/graficne-kartice-amd/asus-prime-radeon-rx-9070-xt-gddr6-100161871627",
]

_SKU_RE = re.compile(r"Številka\s*:\s*(\d{5,14})", re.IGNORECASE)
_SAVE_RE = re.compile(r"Prihranite\s+(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?)\s*€", re.IGNORECASE)
_FIRST_PARTY_RE = re.compile(r"(?i)dostava\s+mimovrste|partner\s*:\s*mimovrste")
_MARKETPLACE_RE = re.compile(r"(?i)partnerska\s+dostava|dobavitelj\s*:\s*(?!mimovrste)")
_SLUG_9070_XT_RE = re.compile(r"(?i)9070-?xt")


def parse_mimovrste_product_page(html: str, url: str) -> tuple[RawOffer | None, list[str]]:
    """Deep-parse a Mimovrste product detail page.

    Returns (offer_or_none, discovered_open_box_sibling_urls).
    Catches:
      - Current listed price AND crossed-out / 30-day lowest price ("Prihranite X €")
      - Open-box / damaged-box / like-new status ("Odprta embalaža", "Kot novo")
      - First-party fulfillment ("Dostava Mimovrste" -> eligible for promo codes & SMART!)
      - On-page promo codes ("S kodo BF20 dodatnih -20 %", "koda za popust")
      - Links to open-box variants of the same GPU
    """
    soup = BeautifulSoup(html, "lxml")
    full_text = soup.get_text(" ", strip=True)

    # Find sibling open-box links on the product page
    open_box_links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        anchor_text = a.get_text(" ", strip=True)
        if any(
            marker in f"{href} {anchor_text}".lower()
            for marker in ("odprta-embalaza", "odprta embalaža", "razpakirano", "poskodovana-embalaza")
        ):
            full_link = urljoin(url, href)
            if full_link != url and full_link not in open_box_links and _SLUG_9070_XT_RE.search(f"{href} {anchor_text}"):
                open_box_links.append(full_link)

    ld_offers = parse_json_ld_products(
        soup,
        store="mimovrste",
        page_url=url,
        ships_to_slovenia=True,
        page_text_for_promos=full_text,
    )

    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (ld_offers[0].title if ld_offers else "")
    if not is_rx_9070_xt(title, full_text[:2000]):
        return None, open_box_links

    sku_match = _SKU_RE.search(full_text)
    sku = sku_match.group(1) if sku_match else (ld_offers[0].sku if ld_offers else "")

    cond, cond_note = detect_condition(f"{title} {url} {full_text[:2500]}")

    fulfilled_by_store: bool | None = None
    seller_name = "Mimovrste"
    if _FIRST_PARTY_RE.search(full_text):
        fulfilled_by_store = True
        seller_name = "Mimovrste"
    elif _MARKETPLACE_RE.search(full_text):
        fulfilled_by_store = False
        seller_name = "Mimovrste Marketplace"

    listed_price = ld_offers[0].listed_price_eur if ld_offers else None
    original_price: float | None = None

    price_box_texts: list[str] = []
    for sel in (
        "[class*='price']",
        "[data-testid*='price']",
        ".product-price",
        ".price__wrap",
    ):
        for node in soup.select(sel)[:8]:
            t = node.get_text(" ", strip=True)
            if "€" in t:
                price_box_texts.append(t)

    all_prices = extract_all_prices_eur(" ".join(price_box_texts) or full_text[:4500])
    if listed_price is None and all_prices:
        listed_price = all_prices[0]

    if listed_price is None:
        return None, open_box_links

    for sel in ("del", "s", "[class*='old']", "[class*='rrp']", "[class*='cross']", "[class*='before']"):
        for node in soup.select(sel):
            p = parse_price_eur(node.get_text(" ", strip=True))
            if p is not None and p > listed_price + 2.0:
                original_price = max(original_price or 0.0, p)

    if original_price is None:
        save_m = _SAVE_RE.search(full_text)
        if save_m:
            saved = parse_price_eur(save_m.group(1), min_eur=1.0, max_eur=1500.0)
            if saved is not None:
                original_price = round(listed_price + saved, 2)
        elif len(all_prices) >= 2:
            for p in all_prices[1:4]:
                if listed_price + 5.0 < p <= listed_price * 1.6:
                    original_price = p
                    break

    in_stock, stock_text = infer_stock_from_text(full_text[:4500], default=True)
    if ld_offers and not ld_offers[0].in_stock and "v košarico" not in full_text[:3500].lower():
        in_stock = False
        stock_text = stock_text or ld_offers[0].stock_text

    promos = extract_promos_from_text(full_text, store="mimovrste")

    shipping_eur: float | None = None
    if "brezplačna dostava" in full_text[:4500].lower():
        shipping_eur = 0.0

    image_url = ld_offers[0].image_url if ld_offers else ""
    offer = RawOffer(
        store="mimovrste",
        offer_key=make_offer_key("mimovrste", url, sku=sku, condition=cond),
        title=title,
        url=url,
        listed_price_eur=listed_price,
        original_price_eur=original_price,
        shipping_eur=shipping_eur,
        in_stock=in_stock,
        stock_text=stock_text,
        condition=cond,
        condition_note=cond_note,
        fulfilled_by_store=fulfilled_by_store,
        seller_name=seller_name,
        ships_to_slovenia=True,
        detected_promos=promos,
        sku=sku,
        image_url=image_url,
    )
    return offer, open_box_links


def discover_mimovrste_product_links(html: str, page_url: str) -> list[str]:
    """Extract any Mimovrste product URLs whose slug or anchor text mentions 9070 XT."""
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        txt = a.get_text(" ", strip=True)
        if "/graficne-kartice" in href.lower() and (
            _SLUG_9070_XT_RE.search(href) or is_rx_9070_xt(txt)
        ):
            full = urljoin(page_url, href.split("?")[0])
            if full not in found and full.count("/") >= 4:
                found.append(full)
    return found


def parse_mimovrste_listing_page(
    html: str, page_url: str, *, default_condition: Condition = Condition.NEW
) -> list[RawOffer]:
    """Parse a Mimovrste search, category, open-box campaign, or promo list page."""
    soup = BeautifulSoup(html, "lxml")
    page_promos = extract_promos_from_text(soup.get_text(" ", strip=True)[:3000], store="mimovrste")

    offers = parse_json_ld_products(
        soup,
        store="mimovrste",
        page_url=page_url,
        ships_to_slovenia=True,
    )
    card_offers = parse_generic_product_cards(
        soup,
        store="mimovrste",
        page_url=page_url,
        card_selectors=[
            "article.pb-brief",
            "[data-testid='product-box']",
            ".product-box-category",
            ".pb-brief",
            "article[class*='product']",
        ],
        ships_to_slovenia=True,
        default_condition=default_condition,
        page_level_promos=page_promos,
    )

    by_key: dict[str, RawOffer] = {o.offer_key: o for o in offers}
    for co in card_offers:
        if co.offer_key not in by_key:
            by_key[co.offer_key] = co
        else:
            existing = by_key[co.offer_key]
            if existing.original_price_eur is None and co.original_price_eur is not None:
                existing.original_price_eur = co.original_price_eur
            if existing.condition == Condition.NEW and co.condition != Condition.NEW:
                existing.condition = co.condition
                existing.condition_note = co.condition_note
            if co.detected_promos:
                existing.detected_promos.extend(co.detected_promos)

    result = list(by_key.values())
    for item in result:
        if item.condition == Condition.NEW and default_condition != Condition.NEW:
            item.condition = default_condition
    return result


class MimovrsteWatcher(BaseWatcher):
    """Watches Mimovrste regular, Open-Box ('Odprta embalaža'), promo lists, and verified product pages."""

    name = "mimovrste"
    display_name = "Mimovrste.com (SI)"
    country = "SI"

    async def scan(self, fetcher: PoliteFetcher) -> WatcherResult:
        urls_to_scan: list[tuple[str, Condition]] = [(u, Condition.NEW) for u in _MIMOVRSTE_SEARCH_URLS]

        if self.options.get("scan_open_box_campaign", True):
            urls_to_scan.extend((u, Condition.OPEN_BOX) for u in _MIMOVRSTE_OPEN_BOX_URLS)

        if self.options.get("scan_promo_lists", True):
            urls_to_scan.extend((u, Condition.NEW) for u in _MIMOVRSTE_PROMO_URLS)

        discovered: dict[str, RawOffer] = {}
        product_urls_to_inspect: list[str] = []
        urls_checked = 0
        blocked_count = 0
        errors: list[str] = []

        for url, default_cond in urls_to_scan:
            urls_checked += 1
            resp = await fetcher.get(url)
            if resp.blocked:
                blocked_count += 1
                errors.append(f"{url}: {resp.error}")
                continue
            if not resp.ok:
                errors.append(f"{url}: HTTP {resp.status_code} {resp.error}".strip())
                continue

            for offer in parse_mimovrste_listing_page(resp.text, resp.url, default_condition=default_cond):
                existing = discovered.get(offer.offer_key)
                if existing is None or offer.listed_price_eur < existing.listed_price_eur:
                    discovered[offer.offer_key] = offer

            for link in discover_mimovrste_product_links(resp.text, resp.url):
                if link not in product_urls_to_inspect:
                    product_urls_to_inspect.append(link)

        # Always include verified Mimovrste RX 9070 XT product pages as reliable deep-inspect seeds
        for seed in _MIMOVRSTE_SEED_PRODUCTS:
            if seed not in product_urls_to_inspect:
                product_urls_to_inspect.append(seed)

        deep_limit = int(self.options.get("deep_inspect_limit", 12))
        extra_open_box_urls: list[str] = []
        inspected_urls: set[str] = set()

        for cand_url in product_urls_to_inspect[:deep_limit]:
            if cand_url in inspected_urls:
                continue
            inspected_urls.add(cand_url)
            urls_checked += 1
            resp = await fetcher.get(cand_url)
            if not resp.ok:
                continue
            detailed, sibling_links = parse_mimovrste_product_page(resp.text, resp.url)
            if detailed is not None:
                discovered[detailed.offer_key] = detailed
            for link in sibling_links:
                if link not in inspected_urls and link not in extra_open_box_urls:
                    extra_open_box_urls.append(link)

        # Follow any discovered sibling open-box URLs
        for ob_url in extra_open_box_urls[:6]:
            urls_checked += 1
            resp = await fetcher.get(ob_url)
            if not resp.ok:
                continue
            detailed, _ = parse_mimovrste_product_page(resp.text, resp.url)
            if detailed is not None:
                if detailed.condition == Condition.NEW:
                    detailed.condition = Condition.OPEN_BOX
                    detailed.condition_note = "Linked open-box variant"
                discovered[detailed.offer_key] = detailed

        status = "ok"
        msg = f"Found {len(discovered)} RX 9070 XT offers"
        if blocked_count == len(urls_to_scan) and not discovered:
            status = "blocked"
            msg = errors[0] if errors else "Blocked by Mimovrste anti-bot"
        elif errors and discovered:
            status = "partial"
            msg = f"Found {len(discovered)} offers ({len(errors)} endpoints skipped)"
        elif errors and not discovered:
            status = "error"
            msg = errors[0]

        return WatcherResult(
            watcher_name=self.name,
            offers=list(discovered.values()),
            status=status,
            message=msg,
            urls_checked=urls_checked,
        )
