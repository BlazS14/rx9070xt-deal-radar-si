"""Tests for Discord mention pings, SMTP email, Signal REST API, and interval parsing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from deal_radar.cli import parse_interval_seconds
from deal_radar.config import NotificationsConfig, RadarConfig
from deal_radar.deal_engine import DealEngine
from deal_radar.models import Condition, DetectedPromo, RawOffer
from deal_radar.notifiers import AlertDispatcher, build_discord_payload


def _sample_event():
    engine = DealEngine(RadarConfig())
    raw = RawOffer(
        store="mimovrste",
        offer_key="mimovrste:demo:open_box",
        title="Sapphire PURE RX 9070 XT - odprta embalaža",
        url="https://www.mimovrste.com/demo",
        listed_price_eur=758.75,
        original_price_eur=939.00,
        shipping_eur=0.0,
        condition=Condition.OPEN_BOX,
        fulfilled_by_store=True,
        detected_promos=[DetectedPromo(label="BF -20%", code="BF20", percent_off=20.0)],
    )
    ev = engine.evaluate(raw)
    alert = engine.should_alert(
        ev,
        previous_total_eur=None,
        all_time_low_eur=None,
        last_alerted_at=None,
        last_alerted_price_eur=None,
        was_in_stock=None,
    )
    assert alert is not None
    return alert


def test_discord_payload_includes_mention_ping() -> None:
    event = _sample_event()
    payload = build_discord_payload(event, mention="<@123456789>")
    assert payload["content"].startswith("<@123456789>")
    assert "607.00" in payload["content"]
    assert len(payload["embeds"]) == 1


def test_email_notifier_sends_via_smtp() -> None:
    cfg = NotificationsConfig(
        console_enabled=False,
        desktop_enabled=False,
        sound_enabled=False,
        email_smtp_host="smtp.gmail.com",
        email_smtp_port=587,
        email_username="sender@gmail.com",
        email_password="app-password-16ch",
        email_to="buyer@gmail.com",
        email_use_tls=True,
    )
    dispatcher = AlertDispatcher(cfg)

    mock_smtp = MagicMock()
    mock_smtp.__enter__.return_value = mock_smtp
    with patch("deal_radar.notifiers.smtplib.SMTP", return_value=mock_smtp) as smtp_cls:
        ok = dispatcher._notify_email("STEAL DEAL €607", "Buy link: https://example.com")

    assert ok is True
    smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
    mock_smtp.starttls.assert_called_once()
    mock_smtp.login.assert_called_once_with("sender@gmail.com", "app-password-16ch")
    mock_smtp.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_signal_notifier_posts_expected_payload() -> None:
    cfg = NotificationsConfig(
        console_enabled=False,
        desktop_enabled=False,
        sound_enabled=False,
        signal_api_url="http://127.0.0.1:8080/v2/send",
        signal_sender="+38640111222",
        signal_recipient="+38640333444",
    )
    dispatcher = AlertDispatcher(cfg)

    class _FakeResp:
        status_code = 201

    captured: dict = {}

    class _FakeClient:
        async def post(self, url: str, json: dict):
            captured["url"] = url
            captured["json"] = json
            return _FakeResp()

    ok = await dispatcher._notify_signal(_FakeClient(), "STEAL €607", "Body text")  # type: ignore[arg-type]
    assert ok is True
    assert captured["url"] == "http://127.0.0.1:8080/v2/send"
    assert captured["json"]["number"] == "+38640111222"
    assert captured["json"]["recipients"] == ["+38640333444"]


def test_parse_interval_seconds() -> None:
    assert parse_interval_seconds("1h") == 3600
    assert parse_interval_seconds("30m") == 1800
    assert parse_interval_seconds("3600") == 3600
    assert parse_interval_seconds("10s") == 30  # floored to polite 30s min
