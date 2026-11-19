"""Multi-channel alert notifiers: OS desktop toast, sound, console, Discord, Telegram, ntfy.sh."""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil
import smtplib
import subprocess
import sys
from email.message import EmailMessage
from typing import Any

import httpx
from rich.console import Console
from rich.panel import Panel

from .config import NotificationsConfig
from .models import AlertEvent, DealTier

logger = logging.getLogger(__name__)
console = Console()


def format_alert_summary(event: AlertEvent) -> tuple[str, str]:
    """Return (short_title, multiline_body) for notifications."""
    offer = event.offer
    prefix = {
        DealTier.STEAL: "🔥 STEAL DEAL",
        DealTier.HOT: "⚡ HOT DEAL",
        DealTier.GOOD: "✅ GOOD DEAL",
        DealTier.TRACKED: "🔔 PRICE UPDATE",
    }[offer.tier]

    cond = f" [{offer.condition.display_label}]" if offer.condition.is_open_box_like else ""
    short_title = (
        f"{prefix}: €{offer.effective_total_eur:.2f} — "
        f"{offer.brand} {offer.variant} RX 9070 XT{cond}"
    )

    lines = [
        f"Store: {offer.store} ({offer.raw.seller_name or 'Direct'})",
        f"Product: {offer.title}",
        f"Effective Landed (SI): €{offer.effective_total_eur:.2f} "
        f"(item €{offer.effective_item_price_eur:.2f} + ship €{offer.shipping_to_slovenia_eur:.2f})",
        f"Listed Price: €{offer.raw.listed_price_eur:.2f}"
        + (f" (was €{offer.raw.original_price_eur:.2f})" if offer.raw.original_price_eur else ""),
        f"Stack / Discounts: {offer.discount_summary()}",
    ]
    if offer.applied_promo and offer.applied_promo.code:
        lines.append(f"Promo Code: {offer.applied_promo.code}")
    if event.previous_total_eur is not None:
        diff = offer.effective_total_eur - event.previous_total_eur
        lines.append(f"Change vs previous: {diff:+.2f} € (prev €{event.previous_total_eur:.2f})")
    lines.append(f"Triggers: {', '.join(event.reasons)}")
    lines.append(f"Score: {offer.deal_score}/100 | Link: {offer.url}")

    return short_title, "\n".join(lines)


def build_discord_payload(event: AlertEvent, *, mention: str = "") -> dict[str, Any]:
    """Build a rich Discord webhook embed payload with optional user/role ping."""
    offer = event.offer
    title, _ = format_alert_summary(event)
    color = {
        DealTier.STEAL: 0xEF4444,   # red/fire
        DealTier.HOT: 0xF59E0B,     # amber
        DealTier.GOOD: 0x10B981,    # emerald
        DealTier.TRACKED: 0x3B82F6, # blue
    }[offer.tier]

    fields = [
        {
            "name": "Effective Total (SI)",
            "value": f"**€{offer.effective_total_eur:.2f}**\n(item €{offer.effective_item_price_eur:.2f} + ship €{offer.shipping_to_slovenia_eur:.2f})",
            "inline": True,
        },
        {
            "name": "Condition & Store",
            "value": f"{offer.condition.display_label}\n`{offer.store}`",
            "inline": True,
        },
        {
            "name": "Deal Score",
            "value": f"**{offer.deal_score}/100** ({offer.tier.value})\n-{offer.total_discount_pct:.0f}% vs ref",
            "inline": True,
        },
        {
            "name": "Stacked Discount Breakdown",
            "value": offer.discount_summary(),
            "inline": False,
        },
        {
            "name": "Triggers",
            "value": ", ".join(event.reasons),
            "inline": False,
        },
    ]
    if offer.applied_promo and offer.applied_promo.code:
        fields.insert(
            3,
            {
                "name": "Promo Code",
                "value": f"`{offer.applied_promo.code}`",
                "inline": True,
            },
        )

    embed: dict[str, Any] = {
        "title": title[:256],
        "url": offer.url,
        "description": offer.title[:400],
        "color": color,
        "fields": fields,
        "footer": {"text": "RX 9070 XT Deal Radar (Slovenia)"},
        "timestamp": event.created_at.isoformat(),
    }
    if offer.raw.image_url:
        embed["thumbnail"] = {"url": offer.raw.image_url}

    payload: dict[str, Any] = {"username": "RX 9070 XT Deal Radar SI", "embeds": [embed]}
    if mention.strip():
        payload["content"] = f"{mention.strip()} {title}"
    return payload


class AlertDispatcher:
    """Sends deal pings across all enabled local and push channels."""

    def __init__(self, config: NotificationsConfig) -> None:
        self.config = config

    async def notify(self, event: AlertEvent) -> dict[str, bool]:
        """Dispatch an alert to every enabled channel; returns channel -> ok map."""
        results: dict[str, bool] = {}
        title, body = format_alert_summary(event)

        if self.config.console_enabled:
            self._notify_console(event, title, body)
            results["console"] = True

        if self.config.sound_enabled and event.offer.tier in {DealTier.STEAL, DealTier.HOT}:
            self._play_sound(is_steal=(event.offer.tier == DealTier.STEAL))

        if self.config.desktop_enabled:
            results["desktop"] = self._notify_desktop(title, body, event.offer.url)

        if self.config.email_smtp_host and self.config.email_to:
            results["email"] = await asyncio.to_thread(self._notify_email, title, body)

        has_remote = bool(
            self.config.discord_webhook_url
            or (self.config.telegram_bot_token and self.config.telegram_chat_id)
            or self.config.ntfy_url
            or (
                self.config.signal_api_url
                and self.config.signal_sender
                and self.config.signal_recipient
            )
        )
        if has_remote:
            async with httpx.AsyncClient(timeout=12.0) as client:
                if self.config.discord_webhook_url:
                    results["discord"] = await self._notify_discord(client, event)
                if self.config.telegram_bot_token and self.config.telegram_chat_id:
                    results["telegram"] = await self._notify_telegram(client, title, body)
                if self.config.ntfy_url:
                    results["ntfy"] = await self._notify_ntfy(client, event, title, body)
                if (
                    self.config.signal_api_url
                    and self.config.signal_sender
                    and self.config.signal_recipient
                ):
                    results["signal"] = await self._notify_signal(client, title, body)

        return results

    def _notify_console(self, event: AlertEvent, title: str, body: str) -> None:
        style = {
            DealTier.STEAL: "bold red",
            DealTier.HOT: "bold yellow",
            DealTier.GOOD: "bold green",
            DealTier.TRACKED: "cyan",
        }[event.offer.tier]
        console.print(Panel(body, title=title, border_style=style, expand=False))

    def _play_sound(self, *, is_steal: bool) -> None:
        system = platform.system().lower()
        if system == "windows":
            try:
                import winsound  # type: ignore[import-not-found]

                winsound.Beep(1200 if is_steal else 880, 220)
                if is_steal:
                    winsound.Beep(1600, 260)
                return
            except Exception:
                pass
        # Cross-platform terminal bell fallback
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass

    def _notify_desktop(self, title: str, body: str, url: str) -> bool:
        """Fire a native OS notification without brittle native C extensions."""
        system = platform.system().lower()
        compact_body = " | ".join(line for line in body.splitlines()[:4])[:240]

        try:
            if system == "windows":
                # Windows 10/11 native Toast via PowerShell WinRT
                safe_title = title.replace("'", "''")
                safe_body = compact_body.replace("'", "''")
                ps_script = (
                    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
                    "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
                    "$xml = [Windows.Data.Xml.Dom.XmlDocument]::new();"
                    f"$xml.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{safe_title}</text><text>{safe_body}</text></binding></visual></toast>');"
                    "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml);"
                    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('RX 9070 XT Deal Radar').Show($toast);"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=8,
                )
                return True

            if system == "darwin" and shutil.which("osascript"):
                safe_title = title.replace('"', '\\"')
                safe_body = compact_body.replace('"', '\\"')
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        f'display notification "{safe_body}" with title "{safe_title}" sound name "Glass"',
                    ],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                return True

            if system == "linux" and shutil.which("notify-send"):
                subprocess.run(
                    ["notify-send", "-u", "critical", title, f"{compact_body}\n{url}"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                return True
        except Exception as exc:
            logger.debug("Desktop notification unavailable: %s", exc)
        return False

    def _notify_email(self, title: str, body: str) -> bool:
        """Send an email alert via SMTP (works with Gmail App Passwords, Outlook, etc.)."""
        try:
            recipients = [r.strip() for r in self.config.email_to.split(",") if r.strip()]
            if not recipients:
                return False
            msg = EmailMessage()
            msg["Subject"] = title
            msg["From"] = self.config.email_from or self.config.email_username
            msg["To"] = ", ".join(recipients)
            msg.set_content(body)

            if self.config.email_smtp_port == 465:
                with smtplib.SMTP_SSL(
                    self.config.email_smtp_host, self.config.email_smtp_port, timeout=15
                ) as smtp:
                    if self.config.email_username and self.config.email_password:
                        smtp.login(self.config.email_username, self.config.email_password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(
                    self.config.email_smtp_host, self.config.email_smtp_port, timeout=15
                ) as smtp:
                    smtp.ehlo()
                    if self.config.email_use_tls:
                        smtp.starttls()
                        smtp.ehlo()
                    if self.config.email_username and self.config.email_password:
                        smtp.login(self.config.email_username, self.config.email_password)
                    smtp.send_message(msg)
            return True
        except Exception as exc:
            logger.warning("Email notification error: %s", exc)
            return False

    async def _notify_discord(self, client: httpx.AsyncClient, event: AlertEvent) -> bool:
        try:
            resp = await client.post(
                self.config.discord_webhook_url,
                json=build_discord_payload(event, mention=self.config.discord_mention),
            )
            return 200 <= resp.status_code < 300
        except Exception as exc:
            logger.warning("Discord webhook error: %s", exc)
            return False

    async def _notify_signal(
        self, client: httpx.AsyncClient, title: str, body: str
    ) -> bool:
        """Send a Signal message through a local signal-cli-rest-api instance."""
        try:
            recipients = [r.strip() for r in self.config.signal_recipient.split(",") if r.strip()]
            payload = {
                "message": f"{title}\n\n{body}",
                "number": self.config.signal_sender,
                "recipients": recipients,
            }
            resp = await client.post(self.config.signal_api_url, json=payload)
            return 200 <= resp.status_code < 300
        except Exception as exc:
            logger.warning("Signal notification error: %s", exc)
            return False

    async def _notify_telegram(
        self, client: httpx.AsyncClient, title: str, body: str
    ) -> bool:
        try:
            api_url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
            resp = await client.post(
                api_url,
                json={
                    "chat_id": self.config.telegram_chat_id,
                    "text": f"{title}\n\n{body}",
                    "disable_web_page_preview": False,
                },
            )
            return 200 <= resp.status_code < 300
        except Exception as exc:
            logger.warning("Telegram notification error: %s", exc)
            return False

    async def _notify_ntfy(
        self, client: httpx.AsyncClient, event: AlertEvent, title: str, body: str
    ) -> bool:
        try:
            priority = "urgent" if event.offer.tier == DealTier.STEAL else "high"
            resp = await client.post(
                self.config.ntfy_url,
                content=body.encode("utf-8"),
                headers={
                    "Title": title.encode("ascii", "ignore").decode("ascii") or "RX 9070 XT Deal",
                    "Priority": priority,
                    "Tags": "fire,shopping_cart",
                    "Click": event.offer.url,
                },
            )
            return 200 <= resp.status_code < 300
        except Exception as exc:
            logger.warning("ntfy notification error: %s", exc)
            return False
