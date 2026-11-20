"""Base watcher and shared HTML/JSON-LD extraction helpers."""

from __future__ import annotations

import hashlib
import json
import re
import time
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from ..config import RadarConfig
from ..fetcher import PoliteFetcher
from ..matching import (
    detect_condition,
    extract_all_prices_eur,
    extract_promos_from_text,
    is_rx_9070_xt,
    parse_price_eur,
)
from ..models import Condition, RawOffer, WatcherResult

_OOS_PATTERNS = re.compile(
    r"(?i)trenutno\s+ni\s+na\s+zalogi|ni\s+na\s+zalogi|razprodano|ni\s+dobavljivo|"
    r"out\s+of\s+stock|not\s+in\s+stock|nicht\s+auf\s+lager|ausverkauft|derzeit\s+nicht\s+verfügbar|"
    r"sold\s+out|currently\s+unavailable|nedostupno|non\s+disponibile|rupture\s+de\s+stock|agotado"
)

_IN_STOCK_PATTERNS = re.compile(
    r"(?i)\bna\s+zalogi\b|v\s+košarico|dodaj\s+v\s+košarico|takoj\s+na\s+voljo|"
    r"\bin\s+stock\b|add\s+to\s+(?:basket|cart)|auf\s+lager|sofort\s+lieferbar|sofort\s+verfügbar|"
    r"versandfertig|na\s+voljo\s+\d|dostava\s+s\s+pošto|en\s+stock|disponibile"
)


def make_offer_key(store: str, url: str, sku: str = "", condition: Condition = Condition.NEW) -> str:
    """Stable deduplication key per (store, product, condition)."""
    base = sku.strip().lower() if sku.strip() else urlparse(url).path.rstrip("/").lower()
    raw = f"{store}:{base}:{condition.value}"
    if len(raw) <= 120:
        return raw
    return f"{store}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]}:{condition.value}"


def extract_json_ld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Parse all JSON-LD blocks on a page, flattening @graph lists."""
    objects: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        text = script.string or script.get_text()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        stack = parsed if isinstance(parsed, list) else [parsed]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    stack.extend(item["@graph"])
                else:
                    objects.append(item)
            elif isinstance(item, list):
                stack.extend(item)
    return objects


def _schema_condition(raw_cond: str) -> Condition:
    low = (raw_cond or "").lower()
    if "damaged" in low:
        return Condition.DAMAGED_BOX
    if "refurbished" in low:
        return Condition.REFURBISHED
    if "used" in low:
        return Condition.OPEN_BOX
    return Condition.NEW


def parse_json_ld_products(
    soup: BeautifulSoup,
    *,
    store: str,
    page_url: str,
    shipping_eur: float | None = None,
    ships_to_slovenia: bool = True,
    page_text_for_promos: str = "",
) -> list[RawOffer]:
    """Extract RX 9070 XT offers from standard schema.org Product / ItemList JSON-LD."""
    offers: list[RawOffer] = []
    page_promos = extract_promos_from_text(page_text_for_promos, store=store)

    def _handle_product(prod: dict[str, Any]) -> None:
        title = str(prod.get("name") or "").strip()
        desc = str(prod.get("description") or "")
        if not is_rx_9070_xt(title, desc):
            return

        url = urljoin(page_url, str(prod.get("url") or page_url))
        sku = str(prod.get("sku") or prod.get("mpn") or prod.get("gtin13") or "")
        image = prod.get("image")
        image_url = image[0] if isinstance(image, list) and image else (str(image) if isinstance(image, str) else "")

        raw_offers = prod.get("offers")
        offer_list = raw_offers if isinstance(raw_offers, list) else ([raw_offers] if isinstance(raw_offers, dict) else [])
        for off in offer_list:
            if not isinstance(off, dict):
                continue
            price_val = off.get("price") or off.get("lowPrice")
            price = parse_price_eur(str(price_val or ""))
            if price is None:
                continue

            avail = str(off.get("availability") or "")
            in_stock = "outofstock" not in avail.lower() and "discontinued" not in avail.lower()
            cond_from_schema = _schema_condition(str(off.get("itemCondition") or ""))
            cond_from_text, cond_note = detect_condition(f"{title} {desc} {url}")
            condition = cond_from_text if cond_from_text != Condition.NEW else cond_from_schema

            seller = ""
            if isinstance(off.get("seller"), dict):
                seller = str(off["seller"].get("name") or "")

            offer_url = urljoin(page_url, str(off.get("url") or url))
            offers.append(
                RawOffer(
                    store=store,
                    offer_key=make_offer_key(store, offer_url, sku, condition),
                    title=title,
                    url=offer_url,
                    listed_price_eur=price,
                    shipping_eur=shipping_eur,
                    in_stock=in_stock,
                    stock_text=avail.rsplit("/", 1)[-1] if avail else "",
                    condition=condition,
                    condition_note=cond_note,
                    seller_name=seller,
                    ships_to_slovenia=ships_to_slovenia,
                    detected_promos=list(page_promos),
                    sku=sku,
                    image_url=image_url,
                )
            )

    for obj in extract_json_ld_objects(soup):
        otype = obj.get("@type")
        types = otype if isinstance(otype, list) else [otype]
        types_l = {str(t).lower() for t in types if t}
        if "product" in types_l:
            _handle_product(obj)
        elif "itemlist" in types_l:
            for elem in obj.get("itemListElement", []):
                if isinstance(elem, dict):
                    item = elem.get("item") if isinstance(elem.get("item"), dict) else elem
                    if isinstance(item, dict):
                        _handle_product(item)

    return offers


def infer_stock_from_text(text: str, default: bool = True) -> tuple[bool, str]:
    """Determine stock status from multilingual card/page text."""
    if not text:
        return default, ""
    m_oos = _OOS_PATTERNS.search(text)
    if m_oos:
        return False, m_oos.group(0).strip()
    m_in = _IN_STOCK_PATTERNS.search(text)
    if m_in:
        return True, m_in.group(0).strip()
    return default, ""


def extract_prices_from_card(card: Tag) -> tuple[float | None, float | None]:
    """Extract (listed_price, original_strikethrough_price) from a product card DOM node."""
    # Look for explicit strikethrough / old-price elements first
    old_price: float | None = None
    for sel in (
        "del",
        "s",
        "[class*='old']",
        "[class*='strike']",
        "[class*='was']",
        "[class*='prev']",
        "[class*='regular']",
    ):
        for node in card.select(sel):
            val = parse_price_eur(node.get_text("", strip=True)) or parse_price_eur(
                node.get_text(" ", strip=True)
            )
            if val is not None:
                old_price = max(old_price or 0.0, val)

    # Prefer dedicated price elements with compact text (prevents <span>1</span>.<span>249</span> splitting)
    dedicated_prices: list[float] = []
    for node in card.select("[class*='price'], [itemprop='price'], [data-price]"):
        # Skip strike-through nodes already captured above
        if node.name in {"del", "s"}:
            continue
        raw_attr = node.get("content") or node.get("data-price")
        if raw_attr:
            val = parse_price_eur(str(raw_attr))
            if val is not None:
                dedicated_prices.append(val)
                continue
        compact = node.get_text("", strip=True)
        spaced = node.get_text(" ", strip=True)
        found = extract_all_prices_eur(compact) or extract_all_prices_eur(spaced)
        if found:
            dedicated_prices.extend(found)

    all_prices = dedicated_prices or extract_all_prices_eur(card.get_text(" ", strip=True))
    if not all_prices:
        return None, old_price

    # If a container has >4 distinct GPU prices, it is likely a multi-product grid wrapper — reject!
    unique_prices = sorted(set(all_prices))
    if len(unique_prices) > 4:
        return None, None

    listed = min(unique_prices)
    highest = max(unique_prices)
    if old_price is None and highest > listed + 5.0 and highest <= listed * 1.65:
        old_price = highest
    if old_price is not None and old_price <= listed:
        old_price = None

    return listed, old_price


def parse_generic_product_cards(
    soup: BeautifulSoup,
    *,
    store: str,
    page_url: str,
    card_selectors: list[str],
    shipping_eur: float | None = None,
    ships_to_slovenia: bool = True,
    default_condition: Condition = Condition.NEW,
    page_level_promos: list[Any] | None = None,
) -> list[RawOffer]:
    """Generic card extractor using CSS selectors + safe heuristic fallback."""
    offers: list[RawOffer] = []
    seen_keys: set[str] = set()

    cards: list[Tag] = []
    for sel in card_selectors:
        found = soup.select(sel)
        if found:
            cards.extend(found)
            break

    # Safe fallback: only attach to tight product-sized containers (never whole grids/sidebars)
    if not cards:
        for a in soup.find_all("a", href=True):
            txt = a.get_text(" ", strip=True)
            if is_rx_9070_xt(txt) and len(txt) >= 18:
                parent = a.find_parent(["article", "li", "div"])
                if parent is not None and len(parent.get_text(" ", strip=True)) <= 1200:
                    cards.append(parent)

    for card in cards:
        card_text = card.get_text(" ", strip=True)
        # Reject huge wrapper containers that mix multiple products/prices
        if len(card_text) > 2200:
            continue
        link = None
        title = ""

        # Collect candidate anchors (including the card itself if the card IS an <a> tag, e.g. Alternate.de)
        anchors: list[Tag] = []
        if card.name == "a" and card.get("href"):
            anchors.append(card)
        anchors.extend(card.find_all("a", href=True))

        # For <a>-root cards (like Alternate.de), check compact inner name/heading/img-alt first
        # so the title doesn't dump the entire spec sheet + prices!
        if card.name == "a" and card.get("href"):
            for sel in (
                "[class*='product-name']",
                "[class*='productName']",
                "[class*='title']",
                "h2",
                "h3",
                "h4",
            ):
                node = card.select_one(sel)
                if node:
                    cand = node.get_text(" ", strip=True)
                    if 16 <= len(cand) <= 160 and is_rx_9070_xt(cand):
                        title = cand
                        link = card
                        break
            if not title:
                img_alt = card.find("img", alt=True)
                if img_alt:
                    cand = str(img_alt.get("alt") or "").strip()
                    if 16 <= len(cand) <= 160 and is_rx_9070_xt(cand):
                        title = cand
                        link = card

        if link is None:
            for a in anchors:
                candidate = a.get("title") or a.get_text(" ", strip=True)
                candidate_str = str(candidate).strip()
                if 16 <= len(candidate_str) <= 180 and is_rx_9070_xt(candidate_str):
                    link = a
                    title = candidate_str
                    break

        if link is None:
            heading = card.find(["h1", "h2", "h3", "h4"])
            if heading:
                candidate_str = heading.get_text(" ", strip=True)
                if is_rx_9070_xt(candidate_str) and len(candidate_str) >= 16:
                    title = candidate_str
                    link = anchors[0] if anchors else None

        # Sibling-description fallback (e.g. Funtech puts "Sapphire Radeon RX9070XT ..." in a <p>/<div> next to <a>)
        if link is None and anchors and is_rx_9070_xt(card_text):
            for node in card.find_all(["h2", "h3", "h4", "p", "div", "span"]):
                txt = node.get_text(" ", strip=True)
                if 16 <= len(txt) <= 160 and is_rx_9070_xt(txt):
                    title = txt
                    link = anchors[0]
                    break

        if not title or link is None:
            continue

        if not is_rx_9070_xt(title, card_text):
            continue

        listed_price, original_price = extract_prices_from_card(card)
        if listed_price is None:
            continue

        href = str(link.get("href") or "")
        offer_url = urljoin(page_url, href)
        cond, cond_note = detect_condition(f"{title} {card_text} {offer_url}")
        if cond == Condition.NEW and default_condition != Condition.NEW:
            cond = default_condition
            cond_note = f"Listed in {default_condition.display_label} feed"

        in_stock, stock_text = infer_stock_from_text(card_text, default=True)
        promos = extract_promos_from_text(card_text, store=store)
        if page_level_promos:
            promos.extend(page_level_promos)

        img = card.find("img")
        image_url = ""
        if img:
            src = img.get("src") or img.get("data-src") or ""
            if src:
                image_url = urljoin(page_url, str(src))

        key = make_offer_key(store, offer_url, condition=cond)
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
                ships_to_slovenia=ships_to_slovenia,
                detected_promos=promos,
                image_url=image_url,
            )
        )

    return offers


class BaseWatcher(ABC):
    """Abstract base class for all store deal watchers."""

    name: str = "base"
    display_name: str = "Base"
    country: str = "EU"

    def __init__(self, config: RadarConfig) -> None:
        self.config = config
        self.options = config.watcher_options(self.name)

    async def run(self, fetcher: PoliteFetcher) -> WatcherResult:
        started = time.perf_counter()
        try:
            result = await self.scan(fetcher)
        except Exception as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return WatcherResult(
                watcher_name=self.name,
                status="error",
                message=f"{type(exc).__name__}: {exc}",
                duration_ms=elapsed,
            )
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    @abstractmethod
    async def scan(self, fetcher: PoliteFetcher) -> WatcherResult:
        """Fetch store endpoints and return parsed RX 9070 XT offers."""
