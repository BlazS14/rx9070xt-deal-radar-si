"""Polite async HTTP fetcher with per-host throttling, challenge detection, and optional Playwright."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
]

# Signatures of anti-bot / CAPTCHA pages. When encountered, we report "blocked" cleanly
# and back off instead of hammering or attempting to bypass challenges.
_CHALLENGE_MARKERS = (
    "cf-turnstile",
    "challenge-platform",
    "just a moment...",
    "verify you are human",
    "g-recaptcha",
    "h-captcha",
    "robot check",
    "validatecaptcha",
    "enter the characters you see below",
    "access denied | cloudflare",
    "attention required! | cloudflare",
)


@dataclass(slots=True)
class FetchResponse:
    url: str
    status_code: int
    text: str
    blocked: bool = False
    error: str = ""
    via_browser: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400 and not self.blocked and not self.error


def looks_like_challenge(status_code: int, html: str) -> bool:
    """Detect whether a response is an anti-bot or CAPTCHA gate."""
    if status_code in {403, 429, 503} and len(html) < 25_000:
        low = html.lower()
        if any(marker in low for marker in _CHALLENGE_MARKERS):
            return True
    low_head = html[:12_000].lower()
    if any(marker in low_head for marker in _CHALLENGE_MARKERS):
        return True
    return False


class PoliteFetcher:
    """Async HTTP/2 client with per-domain rate limiting and optional Playwright fallback."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        min_domain_interval: float = 1.2,
        use_browser_fallback: bool = False,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.min_domain_interval = min_domain_interval
        self.use_browser_fallback = use_browser_fallback
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._domain_last_ts: dict[str, float] = {}
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> PoliteFetcher:
        self._client = httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=httpx.Timeout(self.timeout_seconds),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        lock = self._domain_locks.setdefault(host, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            last = self._domain_last_ts.get(host, 0.0)
            wait = self.min_domain_interval - (now - last)
            if wait > 0:
                await asyncio.sleep(wait + random.uniform(0.1, 0.4))
            self._domain_last_ts[host] = time.monotonic()

    def _headers(self, accept_language: str = "sl-SI,sl;q=0.9,en;q=0.8,de;q=0.7") -> dict[str, str]:
        return {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": accept_language,
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Upgrade-Insecure-Requests": "1",
        }

    async def get(
        self,
        url: str,
        *,
        accept_language: str = "sl-SI,sl;q=0.9,en;q=0.8,de;q=0.7",
        extra_headers: dict[str, str] | None = None,
        allow_browser_fallback: bool = True,
    ) -> FetchResponse:
        if self._client is None:
            raise RuntimeError("PoliteFetcher must be used as an async context manager.")

        await self._throttle(url)
        headers = self._headers(accept_language)
        if extra_headers:
            headers.update(extra_headers)

        last_err = ""
        for attempt in range(2):
            try:
                resp = await self._client.get(url, headers=headers)
                text = resp.text
                if looks_like_challenge(resp.status_code, text):
                    return FetchResponse(
                        url=str(resp.url),
                        status_code=resp.status_code,
                        text=text,
                        blocked=True,
                        error=f"Anti-bot/CAPTCHA challenge detected (HTTP {resp.status_code})",
                    )
                if resp.status_code == 429:
                    return FetchResponse(
                        url=str(resp.url),
                        status_code=429,
                        text=text,
                        blocked=True,
                        error="Rate limited (HTTP 429)",
                    )
                if resp.status_code >= 500 and attempt == 0:
                    await asyncio.sleep(1.5)
                    continue

                if (
                    resp.status_code == 200
                    and allow_browser_fallback
                    and self.use_browser_fallback
                    and self._needs_js_render(text)
                ):
                    rendered = await self._fetch_with_playwright(url)
                    if rendered is not None:
                        return rendered

                return FetchResponse(
                    url=str(resp.url),
                    status_code=resp.status_code,
                    text=text,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                if attempt == 0:
                    await asyncio.sleep(1.0)
                    continue

        return FetchResponse(url=url, status_code=0, text="", error=last_err or "Request failed")

    @staticmethod
    def _needs_js_render(html: str) -> bool:
        """Heuristic for empty SPA shells that require JS rendering."""
        if len(html) < 2500 and ("__nuxt" in html.lower() or "id=\"app\"" in html.lower()):
            return True
        return False

    async def _fetch_with_playwright(self, url: str) -> FetchResponse | None:
        """Render a JS-driven page with Playwright if installed locally."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.debug("Playwright not installed; skipping browser fallback for %s", url)
            return None

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    locale="sl-SI",
                    user_agent=random.choice(_USER_AGENTS),
                )
                page = await context.new_page()
                resp = await page.goto(
                    url, wait_until="domcontentloaded", timeout=int(self.timeout_seconds * 1000)
                )
                await page.wait_for_timeout(2000)
                html = await page.content()
                status = resp.status if resp else 200
                final_url = page.url
                await browser.close()

            if looks_like_challenge(status, html):
                return FetchResponse(
                    url=final_url,
                    status_code=status,
                    text=html,
                    blocked=True,
                    error=f"Anti-bot/CAPTCHA challenge detected in browser (HTTP {status})",
                    via_browser=True,
                )
            return FetchResponse(
                url=final_url,
                status_code=status,
                text=html,
                via_browser=True,
            )
        except Exception as exc:
            logger.warning("Playwright fallback failed for %s: %s", url, exc)
            return None
