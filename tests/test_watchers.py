"""Fixture-based HTML/JSON-LD parser tests for Mimovrste, Caseking, and Ceneje watchers."""

from __future__ import annotations

from deal_radar.config import RadarConfig
from deal_radar.deal_engine import DealEngine
from deal_radar.models import Condition
from deal_radar.watchers.caseking import parse_caseking_page
from deal_radar.watchers.ceneje import parse_ceneje_page
from deal_radar.watchers.mimovrste import (
    parse_mimovrste_listing_page,
    parse_mimovrste_product_page,
)

_MIMOVRSTE_PRODUCT_HTML = """
<!doctype html>
<html>
<head>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "Sapphire PURE AMD Radeon RX 9070 XT Gaming OC grafična kartica, 16GB GDDR6 - odprta embalaža",
    "sku": "49910453",
    "offers": {
      "@type": "Offer",
      "price": "758.75",
      "priceCurrency": "EUR",
      "availability": "https://schema.org/InStock",
      "itemCondition": "https://schema.org/UsedCondition"
    }
  }
  </script>
</head>
<body>
  <h1>Sapphire PURE AMD Radeon RX 9070 XT Gaming OC grafična kartica, 16GB GDDR6 - odprta embalaža</h1>
  <div class="badge">Odprta embalaža</div>
  <div class="price__wrap">
    <span class="save">Prihranite 180,25 €</span>
    <span class="current">758,75 €</span>
    <del class="old">939,00 €</del>
  </div>
  <div class="promo-banner">
    Črni petek! Dodatnih -20 % ceneje s kodo BF20 na artikle z oznako Dostava Mimovrste.
  </div>
  <div class="delivery">Dostava Mimovrste · Brezplačna dostava · Na zalogi (1 kos)</div>
  <button>V košarico</button>
  <div>Številka: 49910453</div>
  <a href="/graficne-kartice-amd/sapphire-pure-rx-9070-xt-odprta-embalaza-2">Odprta embalaža alternativa</a>
</body>
</html>
"""

_CASEKING_CATEGORY_HTML = """
<html><body>
  <div class="product-tile">
    <a href="/en/powercolor-reaper-rx-9070-xt/GCPC-075.html">PowerColor Reaper Radeon RX 9070 XT Graphics Card, 16384 MB GDDR6</a>
    <span class="sku">GCPC-075</span>
    <span class="price">699,00 €</span>
    <span class="stock">In Stock</span>
  </div>
  <div class="product-tile">
    <a href="/en/sapphire-rx-9070-non-xt/GCSP-999.html">Sapphire Pulse Radeon RX 9070 Graphics Card, 16384 MB GDDR6</a>
    <span class="price">599,00 €</span>
    <span class="stock">In Stock</span>
  </div>
  <div class="product-tile">
    <a href="/en/sapphire-nitro-rx-9070-xt/GCSP-264.html">Sapphire Nitro+ Radeon RX 9070 XT Gaming OC, 16384 MB GDDR6</a>
    <span class="price">849,00 €</span>
    <span class="stock">Out of Stock</span>
  </div>
</body></html>
"""

_CENEJE_HTML = """
<html><body>
  <div class="productBox">
    <a href="/Izdelek/55692368/sapphire-pulse-rx-9070-xt">SAPPHIRE PULSE Gaming Radeon RX 9070 XT 16GB GDDR6 OC</a>
    <span class="price">od 719,90 €</span>
  </div>
  <div class="productBox">
    <a href="/Izdelek/55692399/xfx-swift-rx-9070-xt">XFX Swift AMD Radeon RX 9070 XT 16GB</a>
    <span class="price">od 689,00 €</span>
  </div>
</body></html>
"""


def test_mimovrste_deep_page_parser_and_engine_e2e() -> None:
    offer, siblings = parse_mimovrste_product_page(
        _MIMOVRSTE_PRODUCT_HTML,
        "https://www.mimovrste.com/graficne-kartice-amd/sapphire-pure-rx-9070-xt",
    )
    assert offer is not None
    assert offer.sku == "49910453"
    assert offer.condition == Condition.OPEN_BOX
    assert offer.fulfilled_by_store is True
    assert offer.in_stock is True
    assert offer.listed_price_eur == 758.75
    assert offer.original_price_eur == 939.00
    assert offer.shipping_eur == 0.0
    assert len(offer.detected_promos) == 1
    assert offer.detected_promos[0].code == "BF20"
    assert len(siblings) == 1

    evaluated = DealEngine(RadarConfig()).evaluate(offer)
    assert evaluated.effective_total_eur == 607.00
    assert evaluated.is_stacked_deal is True


def test_mimovrste_open_box_campaign_listing_parser() -> None:
    html = """
    <html><body>
      <article class="pb-brief">
        <a href="/graficne-kartice-amd/xfx-swift-rx-9070-xt-odprta">
          XFX Swift AMD Radeon RX 9070 XT 16GB - odprta embalaža
        </a>
        <del>859,00 €</del>
        <span class="price">699,00 €</span>
        <span>Na zalogi</span>
      </article>
    </body></html>
    """
    offers = parse_mimovrste_listing_page(
        html,
        "https://www.mimovrste.com/kampanja/odprta-embalaza",
        default_condition=Condition.OPEN_BOX,
    )
    assert len(offers) == 1
    assert offers[0].condition == Condition.OPEN_BOX
    assert offers[0].listed_price_eur == 699.00
    assert offers[0].original_price_eur == 859.00


def test_caseking_parser_filters_non_xt_and_tracks_stock() -> None:
    offers = parse_caseking_page(
        _CASEKING_CATEGORY_HTML,
        "https://www.caseking.de/en/pc-components/graphics-cards/amd/rx-9000/radeon-rx-9070-xt",
        shipping_eur=15.99,
    )
    assert len(offers) == 2  # Non-XT RX 9070 excluded!
    by_sku = {o.sku: o for o in offers}
    assert by_sku["GCPC-075"].listed_price_eur == 699.00
    assert by_sku["GCPC-075"].in_stock is True
    assert by_sku["GCSP-264"].in_stock is False


def test_ceneje_parser_extracts_slovenian_listings() -> None:
    offers = parse_ceneje_page(
        _CENEJE_HTML,
        "https://www.ceneje.si/L3/115/racunalnistvo/komponente/graficne-kartice?filt=124:941614",
    )
    assert len(offers) == 2
    prices = sorted(o.listed_price_eur for o in offers)
    assert prices == [689.00, 719.90]
