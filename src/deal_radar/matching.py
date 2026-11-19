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

# Price extraction supporting BOTH EU ("1.249,99 €", "1 249,99 €", "867,40 €")
# AND EN/US ("1,249.99 €", "€1,205.37", "607.20 €") formats.
# Negative lookbehind (?<![\d.,']) guarantees we NEVER match "209.99 €" inside "1,209.99 €".
_NUM_TOKEN = (
    r"(?:"
    r"\d{1,3}(?:[.\s'\u00a0\u202f]\d{3})+(?:,\d{1,2})?"  # EU thousands: 1.249,99 / 1 249,99 / 1.200
    r"|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"                  # EN thousands: 1,249.99 / 1,200
    r"|\d{3,4}(?:[.,]\d{1,2})?"                           # Plain 3-4 digits: 867,40 / 607.20 / 607
    r")"
)

_PRICE_RE = re.compile(
    rf"(?:€|EUR\b)\s*(?<![\d.,'])({_NUM_TOKEN})(?![\d.,]\d)|"
    rf"(?<![\d.,'])({_NUM_TOKEN})\s*(?:€|EUR\b)",
    re.IGNORECASE,
)

# Standalone number fallback (used only on dedicated price attributes/tags without € symbol)
_BARE_PRICE_RE = re.compile(rf"(?<![\d.,'])({_NUM_TOKEN})(?![\d.,]\d)")

# Strip non-product prices before parsing: ex-VAT, savings amounts, and monthly financing/installments
_EX_VAT_PRICE_RE = re.compile(
    rf"(?:\b{_NUM_TOKEN}\s*(?:€|EUR\b)|(?<!\d\s)(?:€|EUR\b)\s*{_NUM_TOKEN})"
    r"\s*(?:\+\s*DDV|brez\s+DDV|exkl\.?\s*MwSt\.?|zzgl\.?\s*MwSt\.?|excl\.?\s*VAT)",
    re.IGNORECASE,
)
_SAVINGS_PRICE_RE = re.compile(
    rf"(?:prihranite|prihranek|uštedite|sparen\s+sie|spare|save|rabatt|popust|you\s+save)\s*[:\-]?\s*"
    rf"(?:€|EUR\b)?\s*{_NUM_TOKEN}\s*(?:€|EUR\b)?",
    re.IGNORECASE,
)
_INSTALLMENT_PRICE_RE = re.compile(
    rf"(?:(?:od|ab|from|à|x)\s*)?(?:€|EUR\b)?\s*{_NUM_TOKEN}\s*(?:€|EUR\b)?\s*"
    r"(?:/\s*(?:mesec|mes\.?|monat|mtl\.?|month|mo\b)|na\s+mesec|pro\s+monat|per\s+month|raten?\b)",
    re.IGNORECASE,
)

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

# Explicit checkout-only automatic discounts (e.g. Amazon Warehouse "Sparen Sie 20% an der Kasse")
# Intentionally strict: requires explicit checkout/cart wording so sitewide "Black Friday -20%" headers
# don't fabricate fake discounts on items already discounted!
_CHECKOUT_PCT_PATTERNS = [
    re.compile(
        r"(?i)(?:dodatnih|dodatni|extra)\s*[-]?\s*(\d{1,2})\s*%\s*(?:v\s+košarici|na\s+blagajni|at\s+checkout|an\s+der\s+kasse)"
    ),
    re.compile(
        r"(?i)(?:save|sparen\s+sie|prihranite)\s+(?:an\s+extra\s+)?(\d{1,2})\s*%\s*(?:at\s+checkout|an\s+der\s+kasse|v\s+košarici)"
    ),
    re.compile(
        r"(?i)(\d{1,2})\s*%\s*(?:rabatt\s+an\s+der\s+kasse|off\s+at\s+checkout|popust\w*\s+v\s+košarici|ceneje\s+v\s+košarici)"
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


def _parse_number_token(token: str) -> float | None:
    """Convert EU ('1.249,99', '1 200') or EN ('1,249.99', '1,200') numeric string to float."""
    s = token.replace("\xa0", "").replace("\u202f", "").replace("'", "").replace(" ", "").strip()
    if not s:
        return None

    has_dot = "." in s
    has_comma = "," in s

    if has_dot and has_comma:
        # Whichever separator comes LAST is the decimal separator
        if s.rfind(",") > s.rfind("."):
            # EU: 1.249,99
            s = s.replace(".", "").replace(",", ".")
        else:
            # EN: 1,249.99
            s = s.replace(",", "")
    elif has_comma:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) in {1, 2}:
            # Decimal comma: 867,40
            s = s.replace(",", ".")
        elif all(len(p) == 3 for p in parts[1:]):
            # Thousands comma: 1,200
            s = s.replace(",", "")
        else:
            return None
    elif has_dot:
        parts = s.split(".")
        if len(parts) == 2 and len(parts[1]) in {1, 2}:
            # Decimal dot: 607.20
            pass
        elif all(len(p) == 3 for p in parts[1:]):
            # Thousands dot: 1.200
            s = s.replace(".", "")
        else:
            return None

    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _clean_price_noise(text: str, *, ignore_ex_vat: bool = True) -> str:
    normalized = text.replace("\xa0", " ").replace("\u202f", " ").strip()
    # Rejoin split thousands/decimals like "1 . 249 , 99" that come from nested HTML spans
    normalized = re.sub(r"(\d)\s*([.,])\s*(\d)", r"\1\2\3", normalized)
    if ignore_ex_vat:
        normalized = _EX_VAT_PRICE_RE.sub(" ", normalized)
    normalized = _SAVINGS_PRICE_RE.sub(" ", normalized)
    normalized = _INSTALLMENT_PRICE_RE.sub(" ", normalized)
    return normalized


def parse_price_eur(
    text: str,
    *,
    min_eur: float = 420.0,
    max_eur: float = 3500.0,
    require_currency: bool = False,
) -> float | None:
    """Parse a European or EN-formatted EUR GPU price from text."""
    if not text:
        return None
    cleaned = _clean_price_noise(text)

    for match in _PRICE_RE.finditer(cleaned):
        tok = match.group(1) or match.group(2)
        val = _parse_number_token(tok)
        if val is not None and min_eur <= val <= max_eur:
            return val

    if require_currency:
        return None

    for match in _BARE_PRICE_RE.finditer(cleaned):
        val = _parse_number_token(match.group(1))
        if val is not None and min_eur <= val <= max_eur:
            return val

    return None


def extract_all_prices_eur(
    text: str,
    *,
    min_eur: float = 420.0,
    max_eur: float = 3500.0,
    ignore_ex_vat: bool = True,
) -> list[float]:
    """Return all currency-tagged EUR GPU prices found in text, preserving order."""
    if not text:
        return []
    cleaned = _clean_price_noise(text, ignore_ex_vat=ignore_ex_vat)
    prices: list[float] = []
    for match in _PRICE_RE.finditer(cleaned):
        tok = match.group(1) or match.group(2)
        val = _parse_number_token(tok)
        if val is not None and min_eur <= val <= max_eur:
            prices.append(val)
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
