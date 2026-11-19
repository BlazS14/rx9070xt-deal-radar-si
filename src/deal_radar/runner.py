"""Scan orchestrator and continuous background watch loop."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

from .config import RadarConfig
from .deal_engine import DealEngine
from .fetcher import PoliteFetcher
from .models import AlertEvent, EvaluatedOffer, WatcherResult
from .notifiers import AlertDispatcher
from .storage import DealStorage
from .watchers import build_watchers

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ScanSummary:
    watcher_results: list[WatcherResult] = field(default_factory=list)
    evaluated_offers: list[EvaluatedOffer] = field(default_factory=list)
    alerts_triggered: list[AlertEvent] = field(default_factory=list)


class RadarRunner:
    """Runs all enabled store watchers, scores deals, updates SQLite, and fires pings."""

    def __init__(
        self,
        config: RadarConfig,
        *,
        storage: DealStorage | None = None,
        dispatcher: AlertDispatcher | None = None,
    ) -> None:
        self.config = config
        self.storage = storage or DealStorage(config.general.db_path)
        self.engine = DealEngine(config)
        self.dispatcher = dispatcher or AlertDispatcher(config.notifications)

    async def scan_once(
        self,
        *,
        only_watchers: list[str] | None = None,
        send_notifications: bool = True,
    ) -> ScanSummary:
        watchers = build_watchers(self.config, only_names=only_watchers)
        semaphore = asyncio.Semaphore(max(1, self.config.general.max_concurrency))
        summary = ScanSummary()

        async with PoliteFetcher(
            timeout_seconds=self.config.general.request_timeout_seconds,
            use_browser_fallback=self.config.general.use_browser_fallback,
        ) as fetcher:

            async def _run_one(watcher):
                async with semaphore:
                    return await watcher.run(fetcher)

            results = await asyncio.gather(*(_run_one(w) for w in watchers))

        summary.watcher_results = list(results)

        for res in results:
            self.storage.record_watcher_run(res)
            for raw in res.offers:
                evaluated = self.engine.evaluate(raw)
                history = self.storage.get_offer_state(evaluated.store, evaluated.offer_key)

                event = self.engine.should_alert(
                    evaluated,
                    previous_total_eur=history.previous_total_eur,
                    all_time_low_eur=history.all_time_low_eur,
                    last_alerted_at=history.last_alerted_at,
                    last_alerted_price_eur=history.last_alerted_price_eur,
                    was_in_stock=history.was_in_stock,
                )

                self.storage.upsert_offer(evaluated)
                summary.evaluated_offers.append(evaluated)

                if event is not None:
                    self.storage.record_alert(event)
                    summary.alerts_triggered.append(event)
                    if send_notifications:
                        await self.dispatcher.notify(event)

        summary.evaluated_offers.sort(
            key=lambda o: (0 if o.in_stock else 1, o.effective_total_eur, -o.deal_score)
        )
        return summary

    async def watch_forever(
        self,
        *,
        only_watchers: list[str] | None = None,
        on_cycle_done=None,
    ) -> None:
        """Continuous polling loop with polite random jitter."""
        cycle = 0
        while True:
            cycle += 1
            summary = await self.scan_once(
                only_watchers=only_watchers,
                send_notifications=True,
            )
            if on_cycle_done is not None:
                on_cycle_done(cycle, summary)

            base = max(30, int(self.config.general.poll_interval_seconds))
            jitter = max(0, int(self.config.general.poll_jitter_seconds))
            sleep_for = max(15, base + random.randint(-jitter, jitter))
            await asyncio.sleep(sleep_for)
