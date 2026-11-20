"""Stacked-discount pricing engine, Slovenia landed-cost calculator, deal scoring & alert logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import RadarConfig
from .matching import extract_brand_and_variant
from .models import (
    AlertEvent,
    DealTier,
    DetectedPromo,
    DiscountLayer,
    EvaluatedOffer,
    RawOffer,
)

SLOVENIAN_STORES = {
    "mimovrste",
    "ceneje",
    "mlacom",
    "funtech",
    "anni",
    "bigbang",
    "enaa",
    "pcplus",
    "shoppster",
}


class DealEngine:
    """Computes effective checkout price to Slovenia, stacks promos, and decides when to ping."""

    def __init__(self, config: RadarConfig) -> None:
        self.config = config

    def _resolve_shipping_eur(self, raw: RawOffer) -> float:
        store = raw.store.lower()
        if raw.shipping_eur is not None:
            return max(0.0, round(raw.shipping_eur, 2))

        watcher_opts = self.config.watcher_options(store)
        if "shipping_to_slovenia_eur" in watcher_opts:
            return max(0.0, round(float(watcher_opts["shipping_to_slovenia_eur"]), 2))

        if store == "mimovrste":
            if self.config.shipping.mimovrste_smart and raw.fulfilled_by_store is not False:
                return 0.0
            return self.config.shipping.default_slovenia_shipping_eur

        if store == "amazon_de":
            return 0.0 if raw.listed_price_eur >= 59.0 else 7.99

        if store in SLOVENIAN_STORES:
            return self.config.shipping.default_slovenia_shipping_eur

        return self.config.shipping.default_eu_shipping_eur

    def _is_promo_eligible(self, promo: DetectedPromo, raw: RawOffer) -> bool:
        if raw.listed_price_eur < promo.min_price_eur:
            return False

        if promo.requires_first_party and raw.fulfilled_by_store is False:
            return False

        if raw.condition.is_open_box_like and not promo.stacks_with_open_box:
            return False

        if raw.original_price_eur and raw.original_price_eur > raw.listed_price_eur:
            if not promo.stacks_with_sale:
                return False

        if promo.only_conditions:
            allowed = {c.lower() for c in promo.only_conditions}
            if raw.condition.value.lower() not in allowed:
                return False

        return promo.percent_off > 0 or promo.fixed_off_eur > 0

    def _promo_savings(self, promo: DetectedPromo, base_price: float) -> float:
        pct_savings = base_price * (promo.percent_off / 100.0) if promo.percent_off > 0 else 0.0
        total = pct_savings + promo.fixed_off_eur
        # Cap sanity: promo cannot discount more than 55% of base price
        return round(min(total, base_price * 0.55), 2)

    def evaluate(self, raw: RawOffer) -> EvaluatedOffer:
        """Compute stacked discounts, effective Slovenia landed price, and deal score."""
        brand, variant = extract_brand_and_variant(raw.title)
        layers: list[DiscountLayer] = []

        # Layer 1: Retail markdown / open-box price reduction already reflected in listed price
        if raw.original_price_eur and raw.original_price_eur > raw.listed_price_eur + 1.0:
            diff = round(raw.original_price_eur - raw.listed_price_eur, 2)
            pct = round((diff / raw.original_price_eur) * 100.0, 1)
            layers.append(
                DiscountLayer(
                    kind="retail_markdown",
                    label="Retail markdown",
                    percent_off=pct,
                    amount_off_eur=diff,
                    source="page",
                )
            )

        # Layer 2: Open-box / B-Ware / Warehouse condition badge
        if raw.condition.is_open_box_like:
            layers.append(
                DiscountLayer(
                    kind="open_box",
                    label=raw.condition.display_label,
                    source="page",
                )
            )

        # Layer 3: Best eligible checkout promo code (page-detected + user-configured promos)
        candidate_promos = list(raw.detected_promos) + self.config.active_promos_for_store(raw.store)
        best_promo: DetectedPromo | None = None
        best_savings = 0.0
        for promo in candidate_promos:
            if not self._is_promo_eligible(promo, raw):
                continue
            savings = self._promo_savings(promo, raw.listed_price_eur)
            if savings > best_savings:
                best_savings = savings
                best_promo = promo

        effective_item_price = round(max(1.0, raw.listed_price_eur - best_savings), 2)
        if best_promo is not None and best_savings > 0:
            layers.append(
                DiscountLayer(
                    kind="promo_code",
                    label=best_promo.label,
                    percent_off=best_promo.percent_off,
                    amount_off_eur=best_savings,
                    code=best_promo.code,
                    source=best_promo.source,
                )
            )

        shipping = self._resolve_shipping_eur(raw)
        effective_total = round(effective_item_price + shipping, 2)

        # Discount vs realistic market reference price (prevents inflated €1228 MSRPs from faking "-20% deals")
        ref_price = self.config.alerts.reference_market_price_eur
        savings_vs_ref = round(max(0.0, ref_price - effective_total), 2)
        total_discount_pct = round((savings_vs_ref / ref_price) * 100.0, 1) if ref_price > 0 else 0.0

        deal_score = self._compute_score(
            effective_total=effective_total,
            total_discount_pct=total_discount_pct,
            raw=raw,
            layers=layers,
            shipping=shipping,
        )
        tier = self._classify_tier(effective_total=effective_total, deal_score=deal_score)

        return EvaluatedOffer(
            raw=raw,
            brand=brand,
            variant=variant,
            effective_item_price_eur=effective_item_price,
            shipping_to_slovenia_eur=shipping,
            effective_total_eur=effective_total,
            discount_layers=layers,
            applied_promo=best_promo,
            total_discount_pct=total_discount_pct,
            savings_vs_reference_eur=savings_vs_ref,
            deal_score=deal_score,
            tier=tier,
        )

    def _compute_score(
        self,
        *,
        effective_total: float,
        total_discount_pct: float,
        raw: RawOffer,
        layers: list[DiscountLayer],
        shipping: float,
    ) -> int:
        alerts = self.config.alerts
        # Base score from landed price relative to steal/target/reference anchors
        if effective_total <= alerts.steal_price_eur:
            price_pts = 85.0 + min(15.0, (alerts.steal_price_eur - effective_total) * 0.25)
        elif effective_total <= alerts.target_price_eur:
            span = max(1.0, alerts.target_price_eur - alerts.steal_price_eur)
            ratio = (alerts.target_price_eur - effective_total) / span
            price_pts = 68.0 + ratio * 17.0
        elif effective_total <= alerts.reference_market_price_eur:
            span = max(1.0, alerts.reference_market_price_eur - alerts.target_price_eur)
            ratio = (alerts.reference_market_price_eur - effective_total) / span
            price_pts = 35.0 + ratio * 33.0
        else:
            over = effective_total - alerts.reference_market_price_eur
            price_pts = max(5.0, 35.0 - over * 0.12)

        # Bonus for genuine stacked deals (e.g. open-box + promo code)
        layer_kinds = {layer.kind for layer in layers}
        stack_bonus = 0.0
        if "promo_code" in layer_kinds:
            stack_bonus += 5.0
        if "open_box" in layer_kinds:
            stack_bonus += 3.0
        if "retail_markdown" in layer_kinds:
            stack_bonus += 2.0
        if len(layer_kinds) >= 2:
            stack_bonus += 4.0

        # Small bonus for Slovenian warranty/free local shipping
        if raw.store.lower() in SLOVENIAN_STORES and shipping <= 5.0:
            stack_bonus += 2.0

        # Penalty if out of stock
        if not raw.in_stock:
            price_pts *= 0.5

        return round(max(0.0, min(100.0, price_pts + stack_bonus)))

    def _classify_tier(self, *, effective_total: float, deal_score: int) -> DealTier:
        if effective_total <= self.config.alerts.steal_price_eur or deal_score >= 90:
            return DealTier.STEAL
        if effective_total <= self.config.alerts.target_price_eur or deal_score >= 75:
            return DealTier.HOT
        if deal_score >= 58:
            return DealTier.GOOD
        return DealTier.TRACKED

    def should_alert(
        self,
        offer: EvaluatedOffer,
        *,
        previous_total_eur: float | None,
        all_time_low_eur: float | None,
        last_alerted_at: datetime | None,
        last_alerted_price_eur: float | None,
        was_in_stock: bool | None,
        market_best_eur: float | None = None,
        previous_market_best_eur: float | None = None,
    ) -> AlertEvent | None:
        """Determine whether an evaluated offer deserves an immediate user ping."""
        if not offer.raw.ships_to_slovenia:
            return None
        if self.config.general.only_in_stock and not offer.in_stock:
            return None
        if not self.config.general.include_open_box and offer.condition.is_open_box_like:
            return None

        alerts = self.config.alerts
        reasons: list[str] = []
        hit_target = False

        # 1. Steal / Instant-Buy tier
        if offer.effective_total_eur <= alerts.steal_price_eur:
            reasons.append(f"STEAL_PRICE (<= €{alerts.steal_price_eur:.0f})")
            hit_target = True
        # 2. Target price hit
        elif offer.effective_total_eur <= alerts.target_price_eur:
            reasons.append(f"TARGET_PRICE (<= €{alerts.target_price_eur:.0f})")
            hit_target = True

        # Secondary triggers are ONLY allowed if the card is below max_alert_price_eur
        # AND competitive with the cheapest in-stock RX 9070 XT currently available!
        within_ceiling = offer.effective_total_eur <= alerts.max_alert_price_eur
        competitive_with_market = (
            market_best_eur is None
            or offer.effective_total_eur <= market_best_eur + alerts.max_above_cheapest_eur
        )

        # 3. New cheapest RX 9070 XT across ALL stores (if below reference price and meaningfully cheaper)
        if (
            previous_market_best_eur is not None
            and offer.effective_total_eur <= alerts.reference_market_price_eur
            and offer.effective_total_eur <= previous_market_best_eur - 15.0
            and (market_best_eur is None or offer.effective_total_eur <= market_best_eur + 1.0)
        ):
            reasons.append(f"CHEAPEST_OVERALL (prev best €{previous_market_best_eur:.0f})")

        is_new_listing = previous_total_eur is None
        restocked = was_in_stock is False and offer.in_stock

        if within_ceiling and competitive_with_market:
            # 4. Stacked deal (e.g., Open-Box + Promo) with genuine discount vs market reference
            if offer.is_stacked_deal and offer.total_discount_pct >= alerts.stacked_discount_alert_pct:
                reasons.append(f"STACKED_DEAL (-{offer.total_discount_pct:.0f}% vs market)")

            # 5. Brand-new Open-Box / B-Ware / Warehouse listing below open-box ceiling
            if (
                offer.condition.is_open_box_like
                and (is_new_listing or restocked)
                and offer.effective_total_eur <= alerts.open_box_alert_ceiling_eur
            ):
                tag = "RESTOCK" if restocked else "NEW"
                reasons.append(f"OPEN_BOX_{tag} ({offer.condition.display_label})")

            # 6. Significant price drop vs previous observation
            if previous_total_eur is not None and previous_total_eur > offer.effective_total_eur:
                drop_eur = round(previous_total_eur - offer.effective_total_eur, 2)
                drop_pct = (drop_eur / previous_total_eur) * 100.0
                if drop_eur >= alerts.min_price_drop_eur or drop_pct >= alerts.min_price_drop_pct:
                    reasons.append(f"PRICE_DROP (-€{drop_eur:.0f} / -{drop_pct:.1f}%)")

            # 7. New all-time low
            if all_time_low_eur is not None and offer.effective_total_eur < all_time_low_eur - 5.0:
                reasons.append(f"ALL_TIME_LOW (prev €{all_time_low_eur:.0f})")

        if not reasons:
            return None

        # Even if a card hit a non-target trigger, suppress if something much cheaper is in stock
        if not hit_target and not competitive_with_market:
            return None

        # Cooldown / deduplication guard
        if last_alerted_at is not None and last_alerted_price_eur is not None:
            now = datetime.now(timezone.utc)
            cooldown = timedelta(hours=alerts.cooldown_hours)
            extra_drop = last_alerted_price_eur - offer.effective_total_eur
            if (
                (now - last_alerted_at) < cooldown
                and extra_drop < alerts.re_alert_extra_drop_eur
                and not restocked
            ):
                return None

        return AlertEvent(
            offer=offer,
            reasons=reasons,
            previous_total_eur=previous_total_eur,
            all_time_low_eur=all_time_low_eur,
            is_new_listing=is_new_listing,
        )
