"""Command-line interface for RX 9070 XT Deal Radar (Slovenia)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .config import RadarConfig, load_config
from .dashboard import create_dashboard_server, start_dashboard_in_background
from .deal_engine import DealEngine
from .models import Condition, DetectedPromo, RawOffer
from .notifiers import AlertDispatcher
from .runner import RadarRunner, ScanSummary
from .storage import DealStorage
from .watchers import ALL_WATCHER_CLASSES

console = Console()


def _print_deals_table(rows: list[dict], title: str = "Cheapest RX 9070 XT Deals (Landed in Slovenia)") -> None:
    if not rows:
        console.print("[yellow]No RX 9070 XT deals found matching your filter yet.[/yellow]")
        return

    table = Table(title=title, show_lines=False)
    table.add_column("Tier", style="bold", width=8)
    table.add_column("Landed (SI)", justify="right", style="bold green", width=11)
    table.add_column("Item + Ship", justify="right", width=15)
    table.add_column("Store", width=16)
    table.add_column("Cond", width=11)
    table.add_column("Model", overflow="fold", min_width=24)
    table.add_column("Stacked Discounts / Promo", overflow="fold", min_width=22)
    table.add_column("Score", justify="right", width=6)

    for row in rows:
        tier = str(row["tier"])
        tier_fmt = {
            "STEAL": "[bold red]STEAL[/bold red]",
            "HOT": "[bold yellow]HOT[/bold yellow]",
            "GOOD": "[green]GOOD[/green]",
        }.get(tier, "[dim]TRACKED[/dim]")

        cond = str(row["condition"])
        cond_fmt = f"[magenta]{cond}[/magenta]" if cond != "new" else "new"
        stock_suffix = "" if row["in_stock"] else " [dim](OOS)[/dim]"
        table.add_row(
            tier_fmt,
            f"€{row['effective_total_eur']:.2f}",
            f"€{row['effective_item_price_eur']:.0f} + €{row['shipping_to_slovenia_eur']:.0f}",
            str(row["store"]),
            cond_fmt,
            f"{row['title'][:58]}{stock_suffix}",
            str(row["discount_summary"]),
            str(row["deal_score"]),
        )
    console.print(table)


def _print_watcher_health(rows: list[dict]) -> None:
    table = Table(title="Watcher Health")
    table.add_column("Watcher", style="bold")
    table.add_column("Status")
    table.add_column("Offers", justify="right")
    table.add_column("URLs", justify="right")
    table.add_column("Time", justify="right")
    table.add_column("Details", overflow="fold")

    for r in rows:
        status = str(r["status"])
        st_fmt = {
            "ok": "[green]ok[/green]",
            "partial": "[yellow]partial[/yellow]",
            "blocked": "[red]blocked[/red]",
            "error": "[red]error[/red]",
        }.get(status, status)
        table.add_row(
            str(r["watcher_name"]),
            st_fmt,
            str(r["offers_found"]),
            str(r["urls_checked"]),
            f"{r['duration_ms']} ms",
            str(r["message"]),
        )
    console.print(table)


def _cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.output)
    if dest.exists() and not args.force:
        console.print(f"[yellow]{dest} already exists. Use --force to overwrite.[/yellow]")
        return 0
    example = Path(__file__).resolve().parents[2] / "config.example.toml"
    if example.exists():
        shutil.copyfile(example, dest)
    else:
        dest.write_text(
            "[general]\npoll_interval_seconds = 240\n\n[alerts]\ntarget_price_eur = 680.0\nsteal_price_eur = 630.0\n",
            encoding="utf-8",
        )
    console.print(f"[green]Created {dest}.[/green] Edit it to add Discord/Telegram/ntfy webhooks or active promo codes!")
    return 0


def _cmd_scan(config: RadarConfig, args: argparse.Namespace) -> int:
    only = [w.strip() for w in args.watchers.split(",") if w.strip()] if args.watchers else None
    runner = RadarRunner(config)
    console.print("[cyan]Scanning RX 9070 XT watchers for Slovenia...[/cyan]")
    summary = asyncio.run(
        runner.scan_once(only_watchers=only, send_notifications=not args.no_notify)
    )
    _print_watcher_health(runner.storage.list_watcher_health())
    deals = runner.storage.list_best_deals(limit=args.limit, only_in_stock=args.in_stock)
    _print_deals_table(deals)
    console.print(
        f"[bold]Scan complete:[/bold] {len(summary.evaluated_offers)} offers evaluated, "
        f"{len(summary.alerts_triggered)} alerts triggered."
    )
    return 0


def _cmd_watch(config: RadarConfig, args: argparse.Namespace) -> int:
    only = [w.strip() for w in args.watchers.split(",") if w.strip()] if args.watchers else None
    runner = RadarRunner(config)

    server = None
    if args.dashboard:
        server = start_dashboard_in_background(config, runner.storage)
        console.print(
            f"[bold green]Local Dashboard running at:[/bold green] "
            f"http://{config.dashboard.host}:{config.dashboard.port}"
        )

    console.print(
        f"[cyan]Starting continuous RX 9070 XT Deal Radar (every ~{config.general.poll_interval_seconds}s). "
        f"Press Ctrl+C to stop.[/cyan]"
    )

    def _on_cycle(cycle: int, summary: ScanSummary) -> None:
        in_stock = [o for o in summary.evaluated_offers if o.in_stock]
        cheapest = f"€{in_stock[0].effective_total_eur:.2f} ({in_stock[0].store})" if in_stock else "none"
        console.print(
            f"[dim]Cycle #{cycle}:[/dim] {len(summary.evaluated_offers)} offers scanned | "
            f"cheapest in-stock: [bold green]{cheapest}[/bold green] | "
            f"alerts sent: [bold]{len(summary.alerts_triggered)}[/bold]"
        )

    try:
        asyncio.run(runner.watch_forever(only_watchers=only, on_cycle_done=_on_cycle))
    except KeyboardInterrupt:
        console.print("\n[yellow]Deal Radar stopped.[/yellow]")
    finally:
        if server is not None:
            server.shutdown()
    return 0


def _cmd_deals(config: RadarConfig, args: argparse.Namespace) -> int:
    storage = DealStorage(config.general.db_path)
    rows = storage.list_best_deals(
        limit=args.limit,
        only_in_stock=args.in_stock,
        only_open_box=args.open_box,
        max_price_eur=args.max_price,
    )
    _print_deals_table(rows)
    return 0


def _cmd_health(config: RadarConfig) -> int:
    storage = DealStorage(config.general.db_path)
    _print_watcher_health(storage.list_watcher_health())
    return 0


def _cmd_dashboard(config: RadarConfig) -> int:
    storage = DealStorage(config.general.db_path)
    server = create_dashboard_server(config, storage)
    console.print(
        f"[bold green]Local Dashboard running at:[/bold green] "
        f"http://{config.dashboard.host}:{config.dashboard.port} (Press Ctrl+C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped.[/yellow]")
    finally:
        server.shutdown()
    return 0


def _cmd_test_notify(config: RadarConfig) -> int:
    """Simulate the exact €607 Mimovrste stacked deal to test all notification channels."""
    engine = DealEngine(config)
    sample_raw = RawOffer(
        store="mimovrste",
        offer_key="mimovrste:demo-open-box-bf20",
        title="Sapphire PURE AMD Radeon RX 9070 XT Gaming OC 16GB GDDR6 - odprta embalaža",
        url="https://www.mimovrste.com/kampanja/odprta-embalaza?category=graficne-kartice-gaming",
        listed_price_eur=758.75,
        original_price_eur=939.00,
        shipping_eur=0.0,
        in_stock=True,
        stock_text="Na zalogi (1 kos)",
        condition=Condition.OPEN_BOX,
        condition_note="Open-box (odprta embalaža)",
        fulfilled_by_store=True,
        seller_name="Mimovrste",
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
    evaluated = engine.evaluate(sample_raw)
    event = engine.should_alert(
        evaluated,
        previous_total_eur=845.0,
        all_time_low_eur=799.0,
        last_alerted_at=None,
        last_alerted_price_eur=None,
        was_in_stock=None,
    )
    assert event is not None
    dispatcher = AlertDispatcher(config.notifications)
    results = asyncio.run(dispatcher.notify(event))
    console.print(f"[bold]Test notification dispatch results:[/bold] {results}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deal-radar",
        description="Local PC watcher for the cheapest AMD Radeon RX 9070 XT deals shipping to Slovenia.",
    )
    parser.add_argument("-c", "--config", default=None, help="Path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create a local config.toml from config.example.toml")
    p_init.add_argument("-o", "--output", default="config.toml")
    p_init.add_argument("--force", action="store_true")

    p_scan = sub.add_parser("scan", help="Run one full scan across enabled store watchers")
    p_scan.add_argument("--watchers", default="", help="Comma-separated subset of watchers (e.g. mimovrste,caseking)")
    p_scan.add_argument("--no-notify", action="store_true", help="Skip sending alerts during this scan")
    p_scan.add_argument("--in-stock", action="store_true", help="Show only in-stock offers")
    p_scan.add_argument("--limit", type=int, default=30)

    p_watch = sub.add_parser("watch", help="Run continuously in the background and ping on hot deals")
    p_watch.add_argument("--watchers", default="", help="Comma-separated subset of watchers")
    p_watch.add_argument("--dashboard", action="store_true", help="Also run local web dashboard on http://127.0.0.1:8765")

    p_deals = sub.add_parser("deals", help="Show cheapest stored RX 9070 XT deals from local SQLite DB")
    p_deals.add_argument("--in-stock", action="store_true")
    p_deals.add_argument("--open-box", action="store_true")
    p_deals.add_argument("--max-price", type=float, default=None)
    p_deals.add_argument("--limit", type=int, default=30)

    sub.add_parser("health", help="Show watcher health and last scan status")
    sub.add_parser("dashboard", help="Launch local web dashboard UI")
    sub.add_parser("test-notify", help="Send a realistic €607 stacked-deal test ping to verify notifications")
    sub.add_parser("list-watchers", help="List all built-in store watchers")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "init":
        return _cmd_init(args)

    if args.command == "list-watchers":
        table = Table(title="Built-in Store Watchers")
        table.add_column("ID", style="bold cyan")
        table.add_column("Store")
        table.add_column("Region")
        for cls in ALL_WATCHER_CLASSES:
            table.add_row(cls.name, cls.display_name, cls.country)
        console.print(table)
        return 0

    config = load_config(args.config)
    if args.command == "scan":
        return _cmd_scan(config, args)
    if args.command == "watch":
        return _cmd_watch(config, args)
    if args.command == "deals":
        return _cmd_deals(config, args)
    if args.command == "health":
        return _cmd_health(config)
    if args.command == "dashboard":
        return _cmd_dashboard(config)
    if args.command == "test-notify":
        return _cmd_test_notify(config)
    return 1


if __name__ == "__main__":
    sys.exit(main())
