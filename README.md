# RX 9070 XT Deal Radar (Slovenia)

A fast, lightweight local PC deal watcher that monitors **Slovenian and EU-shipping-to-Slovenia** retailers for **AMD Radeon RX 9070 XT** graphics cards and pings you immediately when real steals appear.

Built specifically to catch **stacked deals** that normal price trackers miss — like a card that is:
1. Marked down on retail sale (e.g. `-10%`),
2. Listed as **Open-Box / B-Ware / Warehouse return** (`Odprta embalaža`, `Poškodovana embalaža`, `B-Ware`, `Retourenkauf`), and
3. Eligible for an extra **promo / Black Friday code** (e.g. `-20%` with code `BF20`), bringing the true checkout price down to **~€607 landed in Slovenia**.

---

## Key Features

- **Stacked-Discount Pricing Engine**: Tracks retail markdowns, open-box/B-Ware variants, and checkout promo codes (both auto-detected on product pages and configurable seasonal codes in `config.toml`), then adds realistic **shipping to Slovenia** to compute your true landed cost (`effective_total_eur`).
- **Flagship Mimovrste.com Watcher**:
  - Scans regular listings **plus** Mimovrste's dedicated Open-Box campaigns (`Odprta embalaža`, `Artikli z odprto embalažo`) and promo lists (`koda-za-popust-20`, `black-friday`).
  - Deep-inspects top candidate pages for `Prihranite X €` / 30-day lowest prices, on-page promo codes (`S kodo ... -20 %`), sibling open-box links, and **first-party fulfillment (`Dostava Mimovrste`)** vs Marketplace (since Mimovrste promo codes & SMART! free shipping apply to Mimovrste-fulfilled items).
- **Multi-Store Coverage (All Ship to Slovenia)**:
  - **Slovenia**: `mimovrste`, `ceneje` (aggregates Enaa, Shoppster, PCPlus, etc.), `mlacom`, `funtech`, `anni`, `bigbang` (+ Outlet)
  - **EU -> Slovenia**: `caseking` (+ B-Ware), `computeruniverse` (+ B-Ware), `amazon_de` (free shipping >= €59 + Amazon Retourenkauf / Warehouse Deals), `alza` (+ Open-Box), `idealo_de`
  - **Custom Watchlist**: Add any specific product or outlet URL under `[[custom_urls]]` in `config.toml`.
- **Zero False Positives**: Strictly filters for standalone **RX 9070 XT** GPUs — automatically ignores plain RX 9070, RX 9070 GRE, RX 9060 XT, waterblocks, prebuilt PCs, and RAM/CPU upgrade kits.
- **Smart Anti-Spam Alerts**: Pings on target price (`<= €680`), instant-buy steal price (`<= €630`), deep stacked discounts (`>= 18%`), new open-box/B-Ware restocks, significant price drops, and new all-time lows — with SQLite price history and cooldown deduplication.
- **Instant Multi-Channel Pings**:
  - Native desktop notifications (Windows 10/11 Toast, macOS Notification Center, Linux `notify-send`) + audible chime
  - **Discord Webhook** (rich color-coded embeds with stacked breakdown & promo code)
  - **Telegram Bot**
  - **ntfy.sh** (zero-signup phone + PC push)
- **Built-in Local Web Dashboard**: Run `deal-radar watch --dashboard` and open `http://127.0.0.1:8765` to browse live deals, filter Open-Box/B-Ware, check recent alerts, and inspect watcher health.

---

## Quick Start (Windows / macOS / Linux)

Requires **Python 3.10+**.

### Windows (PowerShell)

```powershell
git clone https://github.com/BlazS14/rx9070xt-deal-radar-si.git
cd rx9070xt-deal-radar-si

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

*(If PowerShell blocks script activation, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or run `.\.venv\Scripts\pip.exe install -e .` and `.\.venv\Scripts\deal-radar.exe` directly.)*

### macOS / Linux

```bash
git clone https://github.com/BlazS14/rx9070xt-deal-radar-si.git
cd rx9070xt-deal-radar-si

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 1. Create your local config

```bash
deal-radar init
```

Edit `config.toml` (gitignored so your webhooks stay private):
- Set your `[alerts]` thresholds (`target_price_eur = 680.0`, `steal_price_eur = 630.0`).
- Optionally paste a **Discord webhook URL**, **Telegram bot token + chat ID**, or **ntfy.sh topic URL**.
- During Black Friday / sales events, enable or add storewide promo codes under `[[promos]]` (e.g., Mimovrste `-20%` code or Amazon Warehouse `-20%` checkout event).

### 2. Test your notifications right away

```bash
deal-radar test-notify
```

This simulates a stacked €607 Mimovrste open-box + `-20%` Black Friday deal so you can confirm your desktop toast, sound, Discord, Telegram, or ntfy pings work immediately.

### 3. Run a one-shot scan or continuous background radar

```bash
# Single scan across all enabled watchers
deal-radar scan

# Scan only specific stores
deal-radar scan --watchers mimovrste,caseking,ceneje

# Continuous radar + local dashboard at http://127.0.0.1:8765
deal-radar watch --dashboard

# Query the cheapest deals stored in SQLite anytime
deal-radar deals --in-stock
deal-radar deals --open-box --max-price 720
deal-radar health
```

---

## Optional: Browser Fallback for JS-Rendered Pages

Some store pages render parts of their catalog dynamically with JavaScript. If you want Playwright headless Chromium fallback enabled locally:

```bash
pip install -e ".[browser]"
playwright install chromium
```

Then set `use_browser_fallback = true` in `config.toml`.

> Note: The fetcher uses polite per-domain rate limiting and random jitter. If a store presents a CAPTCHA or anti-bot challenge, the watcher marks itself as `blocked` in `deal-radar health` and backs off cleanly without attempting to bypass challenges.

---

## Running on PC Startup

- **Windows**: Create a Scheduled Task in **Task Scheduler** triggered *At log on* running:
  - Program: `C:\path\to\rx9070xt-deal-radar-si\.venv\Scripts\deal-radar.exe`
  - Arguments: `watch --dashboard`
  - Start in: `C:\path\to\rx9070xt-deal-radar-si`
- **Linux (`systemd --user`)**: Point `ExecStart` to `.venv/bin/deal-radar watch --dashboard` with `Restart=on-failure`.

---

## Development & Tests

```bash
pip install -e ".[dev]"
pytest -v
```

## License

MIT — see [LICENSE](LICENSE).
