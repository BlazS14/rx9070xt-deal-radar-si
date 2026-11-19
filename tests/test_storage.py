"""Tests for SQLite DealStorage, price history tracking, alert records, and Discord payload."""

from __future__ import annotations

from pathlib import Path

from deal_radar.config import RadarConfig
from deal_radar.deal_engine import DealEngine
from deal_radar.models import Condition, DetectedPromo, RawOffer, WatcherResult
from deal_radar.notifiers import build_discord_payload, format_alert_summary
from deal_radar.storage import DealStorage


def test_storage_tracks_all_time_low_and_history(tmp_path: Path) -> None:
    db = DealStorage(tmp_path / "test_deals.db")
    engine = DealEngine(RadarConfig())

    raw_high = RawOffer(
        store="mimovrste",
        offer_key="mimovrste:gpu-1:open_box",
        title="Sapphire PURE RX 9070 XT - odprta embalaža",
        url="https://www.mimovrste.com/gpu-1",
        listed_price_eur=780.00,
        shipping_eur=0.0,
        condition=Condition.OPEN_BOX,
        fulfilled_by_store=True,
    )
    ev1 = engine.evaluate(raw_high)
    db.upsert_offer(ev1)

    # Identical price poll should not create duplicate history row
    db.upsert_offer(ev1)
    assert len(db.get_price_history("mimovrste", "mimovrste:gpu-1:open_box")) == 1

    # Now Black Friday promo stacks on top -> €624
    raw_low = RawOffer(
        store="mimovrste",
        offer_key="mimovrste:gpu-1:open_box",
        title="Sapphire PURE RX 9070 XT - odprta embalaža",
        url="https://www.mimovrste.com/gpu-1",
        listed_price_eur=780.00,
        shipping_eur=0.0,
        condition=Condition.OPEN_BOX,
        fulfilled_by_store=True,
        detected_promos=[DetectedPromo(label="BF -20%", code="BF20", percent_off=20.0)],
    )
    ev2 = engine.evaluate(raw_low)
    state_before = db.get_offer_state("mimovrste", "mimovrste:gpu-1:open_box")
    assert state_before.previous_total_eur == 780.00
    assert state_before.all_time_low_eur == 780.00

    alert = engine.should_alert(
        ev2,
        previous_total_eur=state_before.previous_total_eur,
        all_time_low_eur=state_before.all_time_low_eur,
        last_alerted_at=state_before.last_alerted_at,
        last_alerted_price_eur=state_before.last_alerted_price_eur,
        was_in_stock=state_before.was_in_stock,
    )
    assert alert is not None
    db.upsert_offer(ev2)
    db.record_alert(alert)

    history = db.get_price_history("mimovrste", "mimovrste:gpu-1:open_box")
    assert len(history) == 2
    assert history[-1]["effective_total_eur"] == 624.00

    state_after = db.get_offer_state("mimovrste", "mimovrste:gpu-1:open_box")
    assert state_after.all_time_low_eur == 624.00
    assert state_after.last_alerted_price_eur == 624.00
    assert state_after.last_alerted_at is not None

    # Watcher health + best deals queries
    db.record_watcher_run(
        WatcherResult(watcher_name="mimovrste", offers=[raw_low], status="ok", message="OK", duration_ms=120, urls_checked=4)
    )
    assert len(db.list_watcher_health()) == 1
    best = db.list_best_deals(only_in_stock=True, only_open_box=True)
    assert len(best) == 1
    assert best[0]["effective_total_eur"] == 624.00

    # Notification formatting sanity check
    title, body = format_alert_summary(alert)
    assert "624.00" in title
    assert "BF20" in body
    discord_payload = build_discord_payload(alert)
    assert discord_payload["embeds"][0]["title"] == title
