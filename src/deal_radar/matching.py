"""Strict RX 9070 XT matching, European price parsing, condition & promo extraction."""

from __future__ import annotations

import re

from .models import Condition, DetectedPromo

# Must match RX 9070 XT (including 9070XT, 9070-XT), never plain RX 9070 or RX 9070 GRE.
_RX_9070_XT_RE = re.compile(
    r"(?i)(?:radeon\s+)?(?:rx\s*)?9070\s*[-]?\s*xt\b"
)

# Reject non-GPU items, prebuilt PCs, upgrade kits, waterblocks, accessories.
_EXCLUDE_PATTERNS = [
    re.compile(r"(?i)\b9070\s*gre\b"),
    re.compile(r"(?i)\b9060\s*xt\b"),
    re.compile(r"(?i)\b(?:water\s*block|waterblock|wasserkühler|vodni\s+blok|eisblock|ek-quantum|heatkiller|bykski|barrow)\b"),
    re.compile(r"(?i)\b(?:gaming\s+pc|pc\s+sistem|pc\s+system|namizni\s+računalnik|računalniški\s+sistem|workstation|prebuilt|desktop\s+pc|komplett-?pc)\b"),
    re.compile(r"(?i)\b(?:aufrüstkit|upgrade\s*-?\s*kit|bundle\s+mit|komplet\s+z\s+|\+\s*32\s*gb\s+ddr|\+\s*ryzen)\b"),
    re.compile(r"(?i)\b(?:prenosnik|laptop|notebook)\b"),
    re.compile(r"(?i)\b(?:riser\s+cable|gpu\s+holder|nosilec\s+za\s+grafič|support\s+bracket|backplate\s+only|replacement\s+fan|nadomestni\s+ventilator)\b"),
]

_BRAND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Sapphire", re.compile(r"(?i)\bsapphire\b")),
    ("XFX", re.compile(r"(?i)\bxfx\b")),
    ("PowerColor", re.compile(r"(?i)\bpower\s*color\b")),
    ("ASUS", re.compile(r"(?i)\basus\b")),
    ("Gigabyte", re.compile(r"(?i)\bgigabyte|aorus\b")),
    ("ASRock", re.compile(r"(?i)\basrock\b")),
    ("Acer", re.compile(r"(?i)\bacer|predator|nitro\b")),
    ("MSI", re.compile(r"(?i)\bmsi\b")),
    ("Yeston", re.compile(r"(?i)\byeston\b")),
]

_VARIANT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Nitro+", re.compile(r"(?i)\bnitro\s*\+|\bnitro\s+plus\b")),
    ("Pure", re.compile(r"(?i)\bpure\b")),
    ("Pulse", re.compile(r"(?i)\bpulse\b")),
    ("Mercury", re.compile(r"(?i)\bmercury\b")),
    ("Quicksilver", re.compile(r"(?i)\bquicksilver\b")),
    ("Swift", re.compile(r"(?i)\bswift\b")),
    ("Red Devil", re.compile(r"(?i)\bred\s*devil\b")),
    ("Hellhound", re.compile(r"(?i)\bhellhound\b")),
    ("Reaper", re.compile(r"(?i)\breaper\b")),
    ("TUF Gaming", re.compile(r"(?i)\btuf\b")),
    ("Prime", re.compile(r"(?i)\bprime\b")),
    ("AORUS Elite", re.compile(r"(?i)\baorus\b")),
    ("Gaming OC", re.compile(r"(?i)\bgaming\s*oc\b")),
    ("Taichi", re.compile(r"(?i)\btaichi\b")),
    ("Steel Legend", re.compile(r"(?i)\bsteel\s*legend\b")),
]

_CONDITION_RULES: list[tuple[Condition, re.Pattern[str], str]] = [
    (
        Condition.DAMAGED_BOX,
        re.compile(r"(?i)poškodovana\s+embalaža|damaged\s+box|verpackungsschaden|beschädigte\s+verpackung"),
        "Damaged packaging",
    ),
    (
        Condition.OPEN_BOX,
        re.compile(
            r"(?i)odprta\s+embalaža|odprto|razpakirano|razstavni\s+eksponat|"
            r"open[\s-]?box|geöffnete\s+verpackung|rozbaleno|zánovní|unboxed|vrnjeno"
        ),
        "Open-box / returned item",
    ),
    (
        Condition.B_WARE,
        re.compile(
            r"(?i)\bb[\s-]?ware\b|\bb[\s-]?stock\b|retourenkauf|versandrückläufer|"
            r"warehouse\s+deal|amazon\s+retoure|ausstellungsstück|outlet\s+izdelek"
        ),
        "B-Ware / Warehouse return",
    ),
    (
        Condition.USED_LIKE_NEW,
        re.compile(r"(?i)\bkot\s+novo\b|wie\s+neu|used\s*[-\u2013]\s*like\s+new|gebraucht\s*[-\u2013]\s*wie\s+neu|rabljeno"),
        "Used - Like New",
    ),
    (
        Condition.REFURBISHED,
        re.compile(r"(?i)refurbished|generalüberholt|obnovljeno|renewed|recertified"),
        "Refurbished",
    ),
]

# Price extraction supporting EU formats: "1.015,65 €", "867,40 €", "€ 607.20", "699 EUR", "1 049,00 €"
_PRICE_RE = re.compile(
    r"(?:€|EUR\b)\s*(\d{1,3}(?:[.\s]\d{3})+|\d{3,4})(?:[.,](\d{2}))?|"
    r"(\d{1,3}(?:[.\s]\d{3})+|\d{3,4})(?:[.,](\d{2}))?\s*(?:€|EUR\b)",
    re.IGNORECASE,
)

# Standalone decimal price (used when extracting from dedicated price tags without € symbol)
_BARE_PRICE_RE = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+|\d{2,4})(?:[.,](\d{2}))?")

# Promo code + discount patterns across Slovenian, German, and English
_PROMO_CODE_WITH_PCT_PATTERNS = [
    # "S kodo BF20 dodatnih -20 %" or "20 % ceneje s kodo CRNIPETEK20" or "koda za popust BF20 (-20%)"
    re.compile(
        r"(?i)(?:s\s+kodo|promocijsk\w+\s+kod\w+|koda\s+za\s+popust|kupon|gutschein(?:code)?|rabattcode|promo\s*code|code)\s*[:\-]?\s*"
        r"[\"„'«]?([A-Z0-9][A-Z0-9_-]{2,20})[\"“'»]?\s*(?:za|dodatnih|dodatno|prinaša|[:\-\u2013])?\s*[-]?\s*(\d{1,2})\s*%"
    ),
    re.compile(
        r"(?i)(?:dodatnih\s+|še\s+|extra\s+)?[-]?\s*(\d{1,2})\s*%\s*(?:ceneje|popust\w*|rabatt|off)?\s*"
        r"(?:s\s+kodo|ob\s+vnosu\s+kode|z\s+vnosom\s+kode|mit\s+(?:dem\s+)?(?:code|gutschein)|with\s+code)\s*[:\-]?\s*"
        r"[\"„'«]?([A-Z0-9][A-Z0-9_-]{2,20})[\"“'»]?"
    ),
]

_PROMO_CODE_WITH_EUR_PATTERNS = [
    re.compile(
        r"(?i)(?:s\s+kodo|promocijsk\w+\s+kod\w+|koda\s+za\s+popust|gutschein(?:code)?|rabattcode|promo\s*code|code)\s*[:\-]?\s*"
        r"[\"„'«]?([A-Z0-9][A-Z0-9_-]{2,20})[\"“'»]?\s*(?:za|dodatnih|[:\-\u2013])?\s*[-]?\s*(\d{1,3})\s*(?:€|EUR)"
    ),
    re.compile(
        r"(?i)(?:dodatnih\s+|extra\s+)?[-]?\s*(\d{1,3})\s*(?:€|EUR)\s*(?:ceneje|popust\w*|rabatt|off)?\s*"
        r"(?:s\s+kodo|mit\s+code|with\s+code)\s*[:\-]?\s*"
        r"[\"„'«]?([A-Z0-9][A-Z0-9_-]{2,20})[\"“'»]?"
    ),
]

# Checkout / event automatic discounts (e.g. Amazon Warehouse -20% at checkout, Mimovrste Black Friday extra -20%)
_CHECKOUT_PCT_PATTERNS = [
    re.compile(
        r"(?i)(?:dodatnih|dodatni|extra)\s*[-]?\s*(\d{1,2})\s*%\s*(?:popust\w*|ceneje|v\s+košarici|na\s+blagajni|at\s+checkout|an\s+der\s+kasse)"
    ),
    re.compile(
        r"(?i)(?:save|sparen\s+sie|prihranite)\s+(?:an\s+extra\s+)?(\d{1,2})\s*%\s*(?:at\s+checkout|an\s+der\s+kasse|v\s+košarici|ob\s+nakupu)"
    ),
    re.compile(
        r"(?i)(\d{1,2})\s*%\s*(?:rabatt\s+an\s+der\s+kasse|off\s+at\s+checkout|popust\w*\s+v\s+košarici|ceneje\s+v\s+košarici)"
    ),
    re.compile(
        r"(?i)(?:black\s+friday|črni\s+petek|cyber\s+monday)[^.\n]{0,40}?(?:dodatnih|extra|koda|code)[^.\n]{0,20}?[-]?\s*(\d{1,2})\s*%"
    ),
]

_STOP_CODES = {
    "KODA",
    "CODE",
    "POPUST",
    "RABATT",
    "BLACK",
    "FRIDAY",
    "DODATNIH",
    "CENEJE",
    "PROMO",
    "GUTSCHEIN",
    "CHECKOUT",
    "MIMOVRSTE",
}


def is_rx_9070_xt(title: str, extra_text: str = "") -> bool:
    """Return True strictly for standalone AMD Radeon RX 9070 XT graphics cards."""
    if not title:
        return False
    combined = f"{title} {extra_text}".strip()
    if not _RX_9070_XT_RE.search(title):
        return False
    for pattern in _EXCLUDE_PATTERNS:
        if pattern.search(title):
            return False
    # Also check extra_text for waterblock/prebuilt indicators if title is terse
    if extra_text:
        for pattern in _EXCLUDE_PATTERNS[2:5]:
            if pattern.search(combined) and "grafična kartica" not in title.lower() and "graphics card" not in title.lower():
                return False
    return True


def extract_brand_and_variant(title: str) -> tuple[str, str]:
    """Identify AIB partner brand and cooler/edition line."""
    brand = "AMD"
    for name, pattern in _BRAND_PATTERNS:
        if pattern.search(title):
            brand = name
            break

    variant = "Standard"
    for name, pattern in _VARIANT_PATTERNS:
        if pattern.search(title):
            variant = name
            break

    return brand, variant


def detect_condition(text: str) -> tuple[Condition, str]:
    """Detect whether an item is New, Open-Box, Damaged Box, B-Ware, or Refurbished."""
    if not text:
        return Condition.NEW, ""
    for cond, pattern, note in _CONDITION_RULES:
        match = pattern.search(text)
        if match:
            return cond, f"{note} ({match.group(0).strip()})"
    return Condition.NEW, ""


def _digits_to_float(whole: str, frac: str | None) -> float | None:
    cleaned_whole = re.sub(r"[.\s]", "", whole)
    if not cleaned_whole.isdigit():
        return None
    if frac and frac.isdigit():
        return float(f"{cleaned_whole}.{frac}")
    return float(cleaned_whole)


def parse_price_eur(
    text: str,
    *,
    min_eur: float = 200.0,
    max_eur: float = 3500.0,
    require_currency: bool = False,
) -> float | None:
    """Parse a European or US-formatted EUR price from text."""
    if not text:
        return None
    normalized = text.replace("\xa0", " ").replace("\u202f", " ").strip()

    for match in _PRICE_RE.finditer(normalized):
        whole = match.group(1) or match.group(3)
        frac = match.group(2) or match.group(4)
        val = _digits_to_float(whole, frac)
        if val is not None and min_eur <= val <= max_eur:
            return round(val, 2)

    if require_currency:
        return None

    # Fallback for clean price fields like "867,40" or "1.049,00" or "607.20"
    # Handle US format "867.40" where "." is decimal (2 digits after dot)
    us_dec = re.search(r"\b(\d{3,4})\.(\d{2})\b", normalized)
    if us_dec:
        val = float(f"{us_dec.group(1)}.{us_dec.group(2)}")
        if min_eur <= val <= max_eur:
            return round(val, 2)

    for match in _BARE_PRICE_RE.finditer(normalized):
        val = _digits_to_float(match.group(1), match.group(2))
        if val is not None and min_eur <= val <= max_eur:
            return round(val, 2)

    return None


_EX_VAT_PRICE_RE = re.compile(
    r"(?:\b\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d{2})?\s*(?:€|EUR)|(?<!\d\s)(?:€|EUR)\s*\d{1,3}(?:[.\s]\d{3})*(?:[.,]\d{2})?)"
    r"\s*(?:\+\s*DDV|brez\s+DDV|exkl\.?\s*MwSt\.?|zzgl\.?\s*MwSt\.?|excl\.?\s*VAT)",
    re.IGNORECASE,
)


def extract_all_prices_eur(
    text: str,
    *,
    min_eur: float = 200.0,
    max_eur: float = 3500.0,
    ignore_ex_vat: bool = True,
) -> list[float]:
    """Return all currency-tagged EUR prices found in text, preserving order."""
    if not text:
        return []
    normalized = text.replace("\xa0", " ").replace("\u202f", " ")
    if ignore_ex_vat:
        normalized = _EX_VAT_PRICE_RE.sub(" ", normalized)
    prices: list[float] = []
    for match in _PRICE_RE.finditer(normalized):
        whole = match.group(1) or match.group(3)
        frac = match.group(2) or match.group(4)
        val = _digits_to_float(whole, frac)
        if val is not None and min_eur <= val <= max_eur:
            prices.append(round(val, 2))
    return prices


def extract_promos_from_text(text: str, *, store: str = "") -> list[DetectedPromo]:
    """Extract promo codes & stackable checkout discounts from product/page text."""
    if not text:
        return []
    promos: list[DetectedPromo] = []
    seen_keys: set[tuple[str | None, float, float]] = set()
    normalized = re.sub(r"\s+", " ", text)

    def _add(promo: DetectedPromo) -> None:
        key = (promo.code, promo.percent_off, promo.fixed_off_eur)
        if key not in seen_keys:
            seen_keys.add(key)
            promos.append(promo)

    # 1. Explicit code + percentage
    for idx, pattern in enumerate(_PROMO_CODE_WITH_PCT_PATTERNS):
        for m in pattern.finditer(normalized):
            if idx == 0:
                code_raw, pct_raw = m.group(1), m.group(2)
            else:
                pct_raw, code_raw = m.group(1), m.group(2)
            code = code_raw.upper().strip()
            pct = float(pct_raw)
            if code in _STOP_CODES or not (3.0 <= pct <= 50.0):
                continue
            _add(
                DetectedPromo(
                    label=f"Promo code -{pct:.0f}%",
                    code=code,
                    percent_off=pct,
                    source="page",
                    requires_first_party=(store == "mimovrste"),
                    confidence="high",
                )
            )

    # 2. Explicit code + fixed EUR off
    for idx, pattern in enumerate(_PROMO_CODE_WITH_EUR_PATTERNS):
        for m in pattern.finditer(normalized):
            if idx == 0:
                code_raw, eur_raw = m.group(1), m.group(2)
            else:
                eur_raw, code_raw = m.group(1), m.group(2)
            code = code_raw.upper().strip()
            eur = float(eur_raw)
            if code in _STOP_CODES or not (5.0 <= eur <= 300.0):
                continue
            _add(
                DetectedPromo(
                    label=f"Promo code -€{eur:.0f}",
                    code=code,
                    fixed_off_eur=eur,
                    source="page",
                    requires_first_party=(store == "mimovrste"),
                    confidence="high",
                )
            )

    # 3. Checkout / Black Friday extra discount without explicit code string
    if not promos:
        for pattern in _CHECKOUT_PCT_PATTERNS:
            for m in pattern.finditer(normalized):
                pct = float(m.group(1))
                if not (5.0 <= pct <= 45.0):
                    continue
                _add(
                    DetectedPromo(
                        label=f"Checkout/Campaign extra -{pct:.0f}%",
                        code="CHECKOUT",
                        percent_off=pct,
                        source="page",
                        requires_first_party=(store == "mimovrste"),
                        confidence="medium",
                    )
                )

    return promos
