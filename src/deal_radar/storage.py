"""SQLite storage for offer snapshots, price history, alert cooldowns, and watcher health."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AlertEvent, EvaluatedOffer, WatcherResult


@dataclass(slots=True)
class OfferHistoryState:
    previous_total_eur: float | None
    all_time_low_eur: float | None
    was_in_stock: bool | None
    last_alerted_at: datetime | None
    last_alerted_price_eur: float | None


class DealStorage:
    """Thread-safe SQLite store for RX 9070 XT offers and price history."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS offers (
                    store TEXT NOT NULL,
                    offer_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    brand TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    url TEXT NOT NULL,
                    sku TEXT NOT NULL DEFAULT '',
                    condition TEXT NOT NULL,
                    condition_note TEXT NOT NULL DEFAULT '',
                    in_stock INTEGER NOT NULL,
                    stock_text TEXT NOT NULL DEFAULT '',
                    fulfilled_by_store INTEGER,
                    seller_name TEXT NOT NULL DEFAULT '',
                    ships_to_slovenia INTEGER NOT NULL DEFAULT 1,
                    listed_price_eur REAL NOT NULL,
                    original_price_eur REAL,
                    effective_item_price_eur REAL NOT NULL,
                    shipping_to_slovenia_eur REAL NOT NULL,
                    effective_total_eur REAL NOT NULL,
                    all_time_low_eur REAL NOT NULL,
                    applied_promo_code TEXT,
                    discount_summary TEXT NOT NULL DEFAULT '',
                    discount_layers_json TEXT NOT NULL DEFAULT '[]',
                    total_discount_pct REAL NOT NULL DEFAULT 0.0,
                    deal_score INTEGER NOT NULL DEFAULT 0,
                    tier TEXT NOT NULL DEFAULT 'TRACKED',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (store, offer_key)
                );

                CREATE TABLE IF NOT EXISTS price_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store TEXT NOT NULL,
                    offer_key TEXT NOT NULL,
                    listed_price_eur REAL NOT NULL,
                    effective_total_eur REAL NOT NULL,
                    in_stock INTEGER NOT NULL,
                    applied_promo_code TEXT,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_price_history_offer
                    ON price_history (store, offer_key, recorded_at DESC);

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store TEXT NOT NULL,
                    offer_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    effective_total_eur REAL NOT NULL,
                    tier TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    discount_summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_offer
                    ON alerts (store, offer_key, created_at DESC);

                CREATE TABLE IF NOT EXISTS watcher_runs (
                    watcher_name TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    offers_found INTEGER NOT NULL DEFAULT 0,
                    urls_checked INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                """
            )

    def get_offer_state(self, store: str, offer_key: str) -> OfferHistoryState:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT effective_total_eur, all_time_low_eur, in_stock
                FROM offers
                WHERE store = ? AND offer_key = ?
                """,
                (store, offer_key),
            ).fetchone()

            alert_row = conn.execute(
                """
                SELECT effective_total_eur, created_at
                FROM alerts
                WHERE store = ? AND offer_key = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (store, offer_key),
            ).fetchone()

        last_alerted_at: datetime | None = None
        last_alerted_price: float | None = None
        if alert_row is not None:
            last_alerted_price = float(alert_row["effective_total_eur"])
            try:
                last_alerted_at = datetime.fromisoformat(alert_row["created_at"])
                if last_alerted_at.tzinfo is None:
                    last_alerted_at = last_alerted_at.replace(tzinfo=timezone.utc)
            except ValueError:
                last_alerted_at = None

        if row is None:
            return OfferHistoryState(
                previous_total_eur=None,
                all_time_low_eur=None,
                was_in_stock=None,
                last_alerted_at=last_alerted_at,
                last_alerted_price_eur=last_alerted_price,
            )

        return OfferHistoryState(
            previous_total_eur=float(row["effective_total_eur"]),
            all_time_low_eur=float(row["all_time_low_eur"]),
            was_in_stock=bool(row["in_stock"]),
            last_alerted_at=last_alerted_at,
            last_alerted_price_eur=last_alerted_price,
        )

    def upsert_offer(self, offer: EvaluatedOffer) -> None:
        now_iso = offer.seen_at.astimezone(timezone.utc).isoformat()
        promo_code = offer.applied_promo.code if offer.applied_promo else None
        layers_json = json.dumps(
            [
                {
                    "kind": layer.kind,
                    "label": layer.label,
                    "percent_off": layer.percent_off,
                    "amount_off_eur": layer.amount_off_eur,
                    "code": layer.code,
                    "source": layer.source,
                }
                for layer in offer.discount_layers
            ]
        )
        fulfilled_int = (
            None if offer.raw.fulfilled_by_store is None else int(offer.raw.fulfilled_by_store)
        )

        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT effective_total_eur, in_stock, applied_promo_code, all_time_low_eur, first_seen_at
                FROM offers
                WHERE store = ? AND offer_key = ?
                """,
                (offer.store, offer.offer_key),
            ).fetchone()

            if existing is None:
                all_time_low = offer.effective_total_eur
                first_seen_at = now_iso
                should_log_history = True
            else:
                prev_atl = float(existing["all_time_low_eur"])
                all_time_low = (
                    min(prev_atl, offer.effective_total_eur) if offer.in_stock else prev_atl
                )
                first_seen_at = str(existing["first_seen_at"])
                should_log_history = (
                    abs(float(existing["effective_total_eur"]) - offer.effective_total_eur) >= 0.01
                    or bool(existing["in_stock"]) != offer.in_stock
                    or existing["applied_promo_code"] != promo_code
                )

            conn.execute(
                """
                INSERT INTO offers (
                    store, offer_key, title, brand, variant, url, sku,
                    condition, condition_note, in_stock, stock_text,
                    fulfilled_by_store, seller_name, ships_to_slovenia,
                    listed_price_eur, original_price_eur,
                    effective_item_price_eur, shipping_to_slovenia_eur,
                    effective_total_eur, all_time_low_eur,
                    applied_promo_code, discount_summary, discount_layers_json,
                    total_discount_pct, deal_score, tier,
                    first_seen_at, last_seen_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?
                )
                ON CONFLICT(store, offer_key) DO UPDATE SET
                    title = excluded.title,
                    brand = excluded.brand,
                    variant = excluded.variant,
                    url = excluded.url,
                    sku = excluded.sku,
                    condition = excluded.condition,
                    condition_note = excluded.condition_note,
                    in_stock = excluded.in_stock,
                    stock_text = excluded.stock_text,
                    fulfilled_by_store = excluded.fulfilled_by_store,
                    seller_name = excluded.seller_name,
                    ships_to_slovenia = excluded.ships_to_slovenia,
                    listed_price_eur = excluded.listed_price_eur,
                    original_price_eur = excluded.original_price_eur,
                    effective_item_price_eur = excluded.effective_item_price_eur,
                    shipping_to_slovenia_eur = excluded.shipping_to_slovenia_eur,
                    effective_total_eur = excluded.effective_total_eur,
                    all_time_low_eur = excluded.all_time_low_eur,
                    applied_promo_code = excluded.applied_promo_code,
                    discount_summary = excluded.discount_summary,
                    discount_layers_json = excluded.discount_layers_json,
                    total_discount_pct = excluded.total_discount_pct,
                    deal_score = excluded.deal_score,
                    tier = excluded.tier,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    offer.store,
                    offer.offer_key,
                    offer.title,
                    offer.brand,
                    offer.variant,
                    offer.url,
                    offer.raw.sku,
                    offer.condition.value,
                    offer.raw.condition_note,
                    int(offer.in_stock),
                    offer.raw.stock_text,
                    fulfilled_int,
                    offer.raw.seller_name,
                    int(offer.raw.ships_to_slovenia),
                    offer.raw.listed_price_eur,
                    offer.raw.original_price_eur,
                    offer.effective_item_price_eur,
                    offer.shipping_to_slovenia_eur,
                    offer.effective_total_eur,
                    all_time_low,
                    promo_code,
                    offer.discount_summary(),
                    layers_json,
                    offer.total_discount_pct,
                    offer.deal_score,
                    offer.tier.value,
                    first_seen_at,
                    now_iso,
                ),
            )

            if should_log_history:
                conn.execute(
                    """
                    INSERT INTO price_history (
                        store, offer_key, listed_price_eur, effective_total_eur,
                        in_stock, applied_promo_code, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        offer.store,
                        offer.offer_key,
                        offer.raw.listed_price_eur,
                        offer.effective_total_eur,
                        int(offer.in_stock),
                        promo_code,
                        now_iso,
                    ),
                )

    def record_alert(self, event: AlertEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alerts (
                    store, offer_key, title, url, effective_total_eur,
                    tier, reasons_json, discount_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.offer.store,
                    event.offer.offer_key,
                    event.offer.title,
                    event.offer.url,
                    event.offer.effective_total_eur,
                    event.offer.tier.value,
                    json.dumps(event.reasons),
                    event.offer.discount_summary(),
                    event.created_at.astimezone(timezone.utc).isoformat(),
                ),
            )

    def record_watcher_run(self, result: WatcherResult) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO watcher_runs (
                    watcher_name, status, offers_found, urls_checked,
                    duration_ms, message, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(watcher_name) DO UPDATE SET
                    status = excluded.status,
                    offers_found = excluded.offers_found,
                    urls_checked = excluded.urls_checked,
                    duration_ms = excluded.duration_ms,
                    message = excluded.message,
                    updated_at = excluded.updated_at
                """,
                (
                    result.watcher_name,
                    result.status,
                    len(result.offers),
                    result.urls_checked,
                    result.duration_ms,
                    result.message,
                    now_iso,
                ),
            )

    def list_best_deals(
        self,
        *,
        limit: int = 50,
        only_in_stock: bool = False,
        only_open_box: bool = False,
        max_price_eur: float | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["ships_to_slovenia = 1"]
        params: list[Any] = []
        if only_in_stock:
            clauses.append("in_stock = 1")
        if only_open_box:
            clauses.append("condition != 'new'")
        if max_price_eur is not None:
            clauses.append("effective_total_eur <= ?")
            params.append(max_price_eur)

        where_sql = " AND ".join(clauses)
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM offers
                WHERE {where_sql}
                ORDER BY in_stock DESC, effective_total_eur ASC, deal_score DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def list_recent_alerts(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM alerts
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_watcher_health(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM watcher_runs
                ORDER BY watcher_name ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def get_cheapest_in_stock_eur(self) -> float | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MIN(effective_total_eur) AS min_price
                FROM offers
                WHERE ships_to_slovenia = 1
                  AND in_stock = 1
                  AND effective_total_eur >= 420.0
                """
            ).fetchone()
        if row is None or row["min_price"] is None:
            return None
        return float(row["min_price"])

    def get_price_history(
        self, store: str, offer_key: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT listed_price_eur, effective_total_eur, in_stock, applied_promo_code, recorded_at
                FROM price_history
                WHERE store = ? AND offer_key = ?
                ORDER BY recorded_at DESC
                LIMIT ?
                """,
                (store, offer_key, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]
