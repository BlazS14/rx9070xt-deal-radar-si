"""Tests for RX 9070 XT matching, European price parsing, condition, and promo extraction."""

from __future__ import annotations

import pytest

from deal_radar.matching import (
    detect_condition,
    extract_all_prices_eur,
    extract_brand_and_variant,
    extract_promos_from_text,
    is_rx_9070_xt,
    parse_price_eur,
)
from deal_radar.models import Condition


@pytest.mark.parametrize(
    "title",
    [
        "Sapphire PURE AMD Radeon RX 9070 XT Gaming OC grafična kartica, 16GB GDDR6 (11348-02-20G)",
        "XFX Swift Radeon RX 9070 XT White Triple Fan Gaming Edition, 16384 MB GDDR6",
        "ASUS Prime Radeon RX 9070 XT OC Edition 16GB - odprta embalaža",
        "GIGABYTE Radeon RX 9070XT GAMING OC 16G",
        "PowerColor Reaper Radeon RX 9070-XT 16GB",
    ],
)
def test_accepts_true_rx_9070_xt_cards(title: str) -> None:
    assert is_rx_9070_xt(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "Sapphire NITRO+ AMD Radeon RX 9070 Gaming OC 16GB GDDR6",
        "ASUS Prime Radeon RX 9070 OC 16GB",
        "AMD Radeon RX 9070 GRE 12GB",
        "Sapphire Pulse Radeon RX 9060 XT 16GB",
        "ALTERNATE SAPPHIRE Radeon RX 9070 XT Pulse + 32GB DDR5-6000 PC-Aufrüstkit",
        "Gaming PC Ryzen 7 9800X3D + Radeon RX 9070 XT 16GB",
        "Alphacool Eisblock Aurora Waterblock for RX 9070 XT",
        "EK-Quantum Vector RX 9070 XT vodni blok",
        "Namizni računalnik Gaming RX 9070 XT 32GB",
    ],
)
def test_rejects_non_xt_and_accessories(title: str) -> None:
    assert is_rx_9070_xt(title) is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("867,40 €", 867.40),
        ("1.015,65 €", 1015.65),
        ("1 049,00 EUR", 1049.00),
        ("€ 607.20", 607.20),
        ("607 €", 607.0),
        ("699,99 €", 699.99),
    ],
)
def test_parse_european_prices(raw: str, expected: float) -> None:
    assert parse_price_eur(raw) == expected


def test_detect_open_box_and_bware_conditions() -> None:
    cond, _ = detect_condition("Sapphire PURE RX 9070 XT - odprta embalaža")
    assert cond == Condition.OPEN_BOX

    cond2, _ = detect_condition("ASUS TUF RX 9070 XT Poškodovana embalaža")
    assert cond2 == Condition.DAMAGED_BOX

    cond3, _ = detect_condition("PowerColor Reaper RX 9070 XT B-Ware / Retourenkauf")
    assert cond3 == Condition.B_WARE

    cond4, _ = detect_condition("XFX Mercury RX 9070 XT (Kot novo)")
    assert cond4 == Condition.USED_LIKE_NEW

    cond5, _ = detect_condition("Sapphire Pulse RX 9070 XT 16GB GDDR6")
    assert cond5 == Condition.NEW


def test_extract_slovenian_and_german_promo_codes() -> None:
    promos_si = extract_promos_from_text(
        "Črni petek! Dodatnih 20 % ceneje s kodo BF20 na izbrane artikle.", store="mimovrste"
    )
    assert len(promos_si) == 1
    assert promos_si[0].code == "BF20"
    assert promos_si[0].percent_off == 20.0
    assert promos_si[0].requires_first_party is True

    promos_de = extract_promos_from_text(
        "Amazon Retourenkauf: Sparen Sie 20% an der Kasse!", store="amazon_de"
    )
    assert len(promos_de) == 1
    assert promos_de[0].percent_off == 20.0

    promos_eur = extract_promos_from_text(
        "Promocijska koda: GPU50 -50 € ob nakupu nad 500 €.", store="bigbang"
    )
    assert len(promos_eur) == 1
    assert promos_eur[0].code == "GPU50"
    assert promos_eur[0].fixed_off_eur == 50.0


def test_brand_and_variant_extraction() -> None:
    assert extract_brand_and_variant("Sapphire PURE AMD Radeon RX 9070 XT Gaming OC") == (
        "Sapphire",
        "Pure",
    )
    assert extract_brand_and_variant("XFX Mercury Radeon RX 9070 XT OC RGB") == (
        "XFX",
        "Mercury",
    )


def test_ignores_ex_vat_ddv_prices() -> None:
    prices = extract_all_prices_eur("1.097,16 € 899,31 € + DDV")
    assert prices == [1097.16]

