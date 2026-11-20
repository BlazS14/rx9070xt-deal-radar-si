"""Tests for stacked-discount pricing, Slovenia landed costs, scoring, and alert deduplication."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from deal_radar.config import PromoRuleConfig, RadarConfig
from deal_radar.deal_engine import DealEngine
from deal_radar.models import Condition, DealTier, DetectedPromo, RawOffer


def test_mimovrste_stacked_open_box_black_friday_607_deal() -> None:
    """Reproduces the exact Mimovrste deal scenario:
    retail -10% -> open-box (~758.75 EUR) -> -20% Black Friday code = 607.00 EUR landed in SI!
    """
    cfg = RadarConfig()
    cfg.shipping.mimovrste_smart = True
    engine = DealEngine(cfg)

    raw = RawOffer(
        store="mimovrste",
        offer_key="mimovrste:49910453:open_box",
        title="Sapphire PURE AMD Radeon RX 9070 XT Gaming OC 16GB - odprta embalaža",
        url="https://www.mimovrste.com/graficne-kartice-amd/sapphire-pure-rx-9070-xt-odprta",
        listed_price_eur=758.75,
        original_price_eur=939.00,
        in_stock=True,
        condition=Condition.OPEN_BOX,
        fulfilled_by_store=True,
        ships_to_slovenia=True,
        detected_promos=[
            DetectedPromo(
                label="Black Friday Promo -20%",
                code="BF20",
                percent_off=20.0,
                source="page",
                requires_first_party=True,
                stacks_with_open_box=True,
            )
        ],
    )

    evaluated = engine.evaluate(raw)
    assert evaluated.effective_item_price_eur == 607.00
    assert evaluated.shipping_to_slovenia_eur == 0.0
    assert evaluated.effective_total_eur == 607.00
    assert evaluated.tier == DealTier.STEAL
    assert evaluated.is_stacked_deal is True
    assert evaluated.deal_score >= 92
    assert evaluated.applied_promo is not None
    assert evaluated.applied_promo.code == "BF20"

    alert = engine.should_alert(
        evaluated,
        previous_total_eur=None,
        all_time_low_eur=None,
        last_alerted_at=None,
        last_alerted_price_eur=None,
        was_in_stock=None,
    )
    assert alert is not None
    assert any("STEAL_PRICE" in r for r in alert.reasons)
    assert any("STACKED_DEAL" in r for r in alert.reasons)


def test_mimovrste_marketplace_does_not_get_first_party_promo_or_smart_shipping() -> None:
    cfg = RadarConfig()
    cfg.shipping.mimovrste_smart = True
    cfg.promos.append(
        PromoRuleConfig(
            name="Mimovrste BF -20%",
            store="mimovrste",
            code="BF20",
            percent_off=20.0,
            requires_first_party=True,
            enabled=True,
        )
    )
    engine = DealEngine(cfg)

    marketplace_offer = RawOffer(
        store="mimovrste",
        offer_key="mimovrste:mkt-1",
        title="Sapphire Pulse RX 9070 XT 16GB",
        url="https://www.mimovrste.com/graficne-kartice-amd/sapphire-pulse",
        listed_price_eur=780.00,
        in_stock=True,
        fulfilled_by_store=False,  # Marketplace seller!
        seller_name="External Partner",
    )

    ev = engine.evaluate(marketplace_offer)
    assert ev.applied_promo is None
    assert ev.effective_item_price_eur == 780.00
    assert ev.shipping_to_slovenia_eur == cfg.shipping.default_slovenia_shipping_eur


def test_amazon_warehouse_configured_promo_only_applies_to_open_box() -> None:
    cfg = RadarConfig()
    cfg.promos.append(
        PromoRuleConfig(
            name="Amazon Retourenkauf Extra -20%",
            store="amazon_de",
            code="AUTO_CHECKOUT",
            percent_off=20.0,
            only_conditions=("b_ware", "open_box", "used_like_new"),
            enabled=True,
        )
    )
    engine = DealEngine(cfg)

    new_card = RawOffer(
        store="amazon_de",
        offer_key="amazon_de:new",
        title="XFX Swift RX 9070 XT 16GB",
        url="https://www.amazon.de/dp/B000NEW",
        listed_price_eur=760.00,
        condition=Condition.NEW,
    )
    wh_card = RawOffer(
        store="amazon_de",
        offer_key="amazon_de:wh",
        title="XFX Swift RX 9070 XT 16GB (Retourenkauf)",
        url="https://www.amazon.de/dp/B000WH",
        listed_price_eur=740.00,
        condition=Condition.B_WARE,
    )

    ev_new = engine.evaluate(new_card)
    ev_wh = engine.evaluate(wh_card)
    assert ev_new.effective_total_eur == 760.00  # Free SI shipping >= 59, no warehouse promo
    assert ev_wh.effective_total_eur == 592.00   # 740 * 0.80 + 0 shipping!
    assert ev_wh.tier == DealTier.STEAL


def test_alert_cooldown_and_extra_drop_override() -> None:
    cfg = RadarConfig()
    engine = DealEngine(cfg)
    raw = RawOffer(
        store="caseking",
        offer_key="caseking:gcsp-266",
        title="Sapphire Pulse RX 9070 XT 16GB",
        url="https://www.caseking.de/en/sapphire-pulse",
        listed_price_eur=650.00,
        shipping_eur=15.99,
        in_stock=True,
    )
    ev = engine.evaluate(raw)  # 665.99 <= 680 target

    # Recently alerted at same price -> suppressed by cooldown
    suppressed = engine.should_alert(
        ev,
        previous_total_eur=665.99,
        all_time_low_eur=665.99,
        last_alerted_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        last_alerted_price_eur=665.99,
        was_in_stock=True,
    )
    assert suppressed is None

    # Price dropped by another €15 during cooldown -> alerts immediately!
    re_alert = engine.should_alert(
        ev,
        previous_total_eur=685.00,
        all_time_low_eur=685.00,
        last_alerted_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        last_alerted_price_eur=685.00,
        was_in_stock=True,
    )
    assert re_alert is not None


def test_overpriced_bware_with_inflated_msrp_never_alerts() -> None:
    """Regression for Alternate.de €987.99 B-Ware card with fake €1,228 crossed-out price."""
    cfg = RadarConfig()
    engine = DealEngine(cfg)
    raw = RawOffer(
        store="alternate_de",
        offer_key="alternate_de:100171225",
        title="SAPPHIRE Radeon RX 9070 XT NITRO+ GAMING OC 16GB - Outlet",
        url="https://www.alternate.de/SAPPHIRE/Radeon-RX-9070-XT-NITRO/html/product/100171225",
        listed_price_eur=971.00,
        original_price_eur=1228.00,
        shipping_eur=16.99,
        in_stock=True,
        condition=Condition.B_WARE,
    )
    ev = engine.evaluate(raw)
    assert ev.effective_total_eur == 987.99
    # Discount must be computed vs realistic market reference (€820), not inflated €1,228 MSRP
    assert ev.total_discount_pct == 0.0

    alert = engine.should_alert(
        ev,
        previous_total_eur=None,
        all_time_low_eur=None,
        last_alerted_at=None,
        last_alerted_price_eur=None,
        was_in_stock=None,
        market_best_eur=913.57,
        previous_market_best_eur=913.57,
    )
    assert alert is None


def test_secondary_triggers_respect_market_best_and_price_ceiling() -> None:
    """Non-target alerts only fire when competitive with the cheapest in-stock card."""
    cfg = RadarConfig()
    engine = DealEngine(cfg)

    open_box_730 = engine.evaluate(
        RawOffer(
            store="mimovrste",
            offer_key="mimovrste:ob-730",
            title="ASUS Prime Radeon RX 9070 XT 16GB - odprta embalaža",
            url="https://www.mimovrste.com/graficne-kartice-amd/asus-prime-9070-xt-ob",
            listed_price_eur=730.00,
            shipping_eur=0.0,
            in_stock=True,
            condition=Condition.OPEN_BOX,
        )
    )
    # Market already has a €680 in-stock card -> €730 is > €680 + €35 so do NOT spam!
    suppressed = engine.should_alert(
        open_box_730,
        previous_total_eur=None,
        all_time_low_eur=None,
        last_alerted_at=None,
        last_alerted_price_eur=None,
        was_in_stock=None,
        market_best_eur=680.00,
        previous_market_best_eur=680.00,
    )
    assert suppressed is None

    # Competitive open-box at €705 (within €35 of €680 best and <= €740 ceiling) DOES alert
    open_box_705 = engine.evaluate(
        RawOffer(
            store="mimovrste",
            offer_key="mimovrste:ob-705",
            title="ASUS Prime Radeon RX 9070 XT 16GB - odprta embalaža",
            url="https://www.mimovrste.com/graficne-kartice-amd/asus-prime-9070-xt-ob2",
            listed_price_eur=705.00,
            shipping_eur=0.0,
            in_stock=True,
            condition=Condition.OPEN_BOX,
        )
    )
    competitive = engine.should_alert(
        open_box_705,
        previous_total_eur=None,
        all_time_low_eur=None,
        last_alerted_at=None,
        last_alerted_price_eur=None,
        was_in_stock=None,
        market_best_eur=680.00,
        previous_market_best_eur=680.00,
    )
    assert competitive is not None
    assert any("OPEN_BOX_NEW" in r for r in competitive.reasons)

    # Target/steal tier ALWAYS alerts even if another store is even cheaper
    target_675 = engine.evaluate(
        RawOffer(
            store="caseking",
            offer_key="caseking:target-675",
            title="Sapphire Pulse Radeon RX 9070 XT 16GB",
            url="https://www.caseking.de/en/sapphire-pulse",
            listed_price_eur=659.01,
            shipping_eur=15.99,
            in_stock=True,
        )
    )
    target_alert = engine.should_alert(
        target_675,
        previous_total_eur=None,
        all_time_low_eur=None,
        last_alerted_at=None,
        last_alerted_price_eur=None,
        was_in_stock=None,
        market_best_eur=620.00,
        previous_market_best_eur=620.00,
    )
    assert target_alert is not None
    assert any("TARGET_PRICE" in r for r in target_alert.reasons)


def test_cheapest_overall_trigger_fires_when_beating_market_best() -> None:
    cfg = RadarConfig()
    engine = DealEngine(cfg)
    ev = engine.evaluate(
        RawOffer(
            store="amazon_de",
            offer_key="amazon_de:new-best",
            title="PowerColor Reaper Radeon RX 9070 XT 16GB",
            url="https://www.amazon.de/dp/B0NEWBEST",
            listed_price_eur=795.00,
            shipping_eur=0.0,
            in_stock=True,
        )
    )
    alert = engine.should_alert(
        ev,
        previous_total_eur=None,
        all_time_low_eur=None,
        last_alerted_at=None,
        last_alerted_price_eur=None,
        was_in_stock=None,
        market_best_eur=795.00,
        previous_market_best_eur=840.00,
    )
    assert alert is not None
    assert any("CHEAPEST_OVERALL" in r for r in alert.reasons)

