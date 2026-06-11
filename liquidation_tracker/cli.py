"""Command-line interface.

Examples
--------
    # Scrape monitored countries, store results, send alerts for key auctions
    python -m liquidation_tracker.cli monitor

    # List active auctions for a country (no DB writes)
    python -m liquidation_tracker.cli list --country ES

    # Compute the max bid for a lot
    python -m liquidation_tracker.cli bid --retail 16670 --type "Small Truckload" --pct 0.25

    # Analyze a manifest CSV
    python -m liquidation_tracker.cli analyze data/sample_manifest.csv
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from . import analyzer, insights
from .calculator import BidCalculator
from .client import BStockClient, CloudflareChallenge
from .config import Settings
from .pipeline import MonitorPipeline


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )


def cmd_monitor(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    if args.country:
        settings.countries = [args.country]
        settings.rules.countries = [args.country]
    pipeline = MonitorPipeline(settings)
    auctions = pipeline.run(fetch_lot_ids=args.lot_ids)
    print(f"Processed {len(auctions)} auctions across {settings.countries}.")
    print(f"Database: {settings.db_path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    client = BStockClient()
    try:
        auctions = client.list_auctions(country=args.country, limit=args.limit)
    except CloudflareChallenge as exc:
        print(f"Cloudflare challenge: {exc}", file=sys.stderr)
        return 2

    if not auctions:
        print(f"No active auctions for {args.country}.")
        return 0

    calc = BidCalculator()
    for a in auctions:
        print(f"\n#{a.auction_id}  {a.title}")
        print(f"  type={a.lot_type}  retail={a.retail_value}  pieces={a.pieces}  bid={a.current_bid}")
        print(f"  ends={a.end_time}  url={a.url}")
        if a.retail_value and a.lot_type:
            b = calc.max_bid_for_retail_pct(a.retail_value, args.pct, a.lot_type)
            print(f"  suggested max bid @ {args.pct:.0%} landed: EUR {b.bid:,.2f} "
                  f"(total EUR {b.total_cost:,.2f})")
    return 0


def cmd_bid(args: argparse.Namespace) -> int:
    calc = BidCalculator()
    b = calc.max_bid_for_retail_pct(args.retail, args.pct, args.type)
    print(f"Lot type      : {args.type}")
    print(f"Retail value  : EUR {args.retail:,.2f}")
    print(f"Target landed : {args.pct:.0%} of retail")
    print("-" * 40)
    print(f"Max bid       : EUR {b.bid:,.2f}")
    print(f"Transport     : EUR {b.transport:,.2f}")
    print(f"VAT (21%)     : EUR {b.vat:,.2f}")
    print(f"B-Stock fee   : EUR {b.bstock_fee:,.2f}")
    print(f"RE (5.2%)     : EUR {b.re:,.2f}")
    print(f"Total landed  : EUR {b.total_cost:,.2f}")
    if b.bid_pct_of_retail is not None:
        print(f"Bid % retail  : {b.bid_pct_of_retail:.1%}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    if not os.path.exists(args.csv):
        print(f"File not found: {args.csv}", file=sys.stderr)
        return 2
    items = analyzer.parse_manifest(args.csv)
    stats = analyzer.analyze(items)
    print(f"Manifest: {args.csv}")
    print(f"  Items (lines) : {stats.total_items}")
    print(f"  Units         : {stats.total_units}")
    print(f"  Total retail  : EUR {stats.total_retail:,.2f}")
    print(f"  Avg unit value: EUR {stats.avg_unit_retail:,.2f}")
    print("\n  Top categories by retail:")
    for cat, value in list(stats.categories.items())[:8]:
        print(f"    {cat:<35} EUR {value:,.2f}")
    print("\n  Conditions:")
    for cond, count in stats.conditions.items():
        print(f"    {cond:<35} {count}")
    print("\n  Highest-value items:")
    for item in stats.top_items[:5]:
        print(f"    EUR {item['line_retail']:>10,.2f}  {item['description']}")
    return 0


def _write_report(report: str, dest_dir: str, stem: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"{stem}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(report)
    return path


def _print_insights_summary(result: insights.ManifestInsights) -> None:
    print(f"\n=== {result.label} ===")
    print(f"  Lineas/uds    : {result.total_lines} / {result.total_units}")
    print(f"  Retail        : EUR {result.total_retail:,.2f}")
    print(f"  TVs (perdida) : {result.tv_units} uds, EUR {result.tv_loss_retail:,.2f}")
    print(f"  Retail efectivo: EUR {result.effective_retail:,.2f}")
    sure = sum(1 for g in result.giveaways if g.tier == "seguro")
    doubt = sum(1 for g in result.giveaways if g.tier == "dudoso")
    print(f"  Regalados     : {sure} seguros, {doubt} dudosos")
    print(
        f"  Contenedores  : {len(result.boxes)} cajas "
        f"({len(result.suspicious_boxes)} sospechosas), {len(result.pallets)} pallets "
        f"({len(result.suspicious_pallets)} sospechosos)"
    )


def cmd_inspect(args: argparse.Namespace) -> int:
    if not os.path.exists(args.csv):
        print(f"File not found: {args.csv}", file=sys.stderr)
        return 2
    items = analyzer.parse_manifest(args.csv)
    if not items:
        print(f"No rows parsed from {args.csv}", file=sys.stderr)
        return 2
    stem = os.path.splitext(os.path.basename(args.csv))[0]
    result = insights.deep_analyze(items, label=stem, verify_prices=args.verify)
    _print_insights_summary(result)
    path = _write_report(insights.render_report(result), args.report_dir, stem)
    print(f"\nInforme completo: {path}")
    return 0


def cmd_manifests(args: argparse.Namespace) -> int:
    """Download + deep-analyze the manifests of every active auction."""
    settings = Settings.from_env()
    client = BStockClient()
    try:
        auctions = client.list_auctions(country=args.country)
    except CloudflareChallenge as exc:
        print(f"Cloudflare challenge: {exc}", file=sys.stderr)
        return 2

    os.makedirs(settings.manifest_dir, exist_ok=True)
    summary_lines = [f"# Manifiestos {args.country} — resumen", ""]
    analyzed = 0
    for auction in auctions:
        label = f"{auction.auction_id} — {auction.title[:70]}"
        try:
            lot_id = client.fetch_lot_id(auction)
            if not lot_id:
                raise RuntimeError("lot_id no encontrado en la pagina de detalle")
            csv_path = os.path.join(
                settings.manifest_dir, f"{auction.auction_id}_{lot_id}.csv"
            )
            if not os.path.exists(csv_path):
                client.download_manifest(lot_id, csv_path)
            items = analyzer.parse_manifest(csv_path)
            result = insights.deep_analyze(
                items, label=label, verify_prices=args.verify
            )
            _print_insights_summary(result)
            stem = f"{auction.auction_id}_{lot_id}"
            path = _write_report(insights.render_report(result), args.report_dir, stem)
            summary_lines.append(
                f"- **#{auction.auction_id}** retail efectivo EUR "
                f"{result.effective_retail:,.0f} (TVs -EUR {result.tv_loss_retail:,.0f}), "
                f"{len(result.giveaways)} regalados, "
                f"{len(result.suspicious_boxes)} cajas sospechosas -> [{stem}.md]({stem}.md)"
            )
            analyzed += 1
        except Exception as exc:  # noqa: BLE001 - keep going per auction
            print(f"\n=== {label} ===\n  SIN MANIFIESTO: {exc}")
            summary_lines.append(f"- **#{auction.auction_id}** sin manifiesto: {exc}")

    summary_path = _write_report(
        "\n".join(summary_lines) + "\n", args.report_dir, f"resumen_{args.country}"
    )
    print(f"\n{analyzed}/{len(auctions)} manifiestos analizados.")
    print(f"Resumen: {summary_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="liquidation_tracker",
        description="Monitor Amazon EU liquidation auctions and compute max bids.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_monitor = sub.add_parser("monitor", help="Run the full scrape/alert pipeline")
    p_monitor.add_argument("--country", help="Override monitored country (e.g. ES)")
    p_monitor.add_argument("--lot-ids", action="store_true", dest="lot_ids",
                           help="Also resolve manifest lot_ids (extra requests)")
    p_monitor.set_defaults(func=cmd_monitor)

    p_list = sub.add_parser("list", help="List active auctions for a country")
    p_list.add_argument("--country", default="ES")
    p_list.add_argument("--limit", type=int, default=48)
    p_list.add_argument("--pct", type=float, default=0.12,
                        help="Target landed cost as fraction of retail")
    p_list.set_defaults(func=cmd_list)

    p_bid = sub.add_parser("bid", help="Compute max bid for a lot")
    p_bid.add_argument("--retail", type=float, required=True)
    p_bid.add_argument("--type", required=True,
                       help='Lot type, e.g. "Truckload" or "Small Truckload"')
    p_bid.add_argument("--pct", type=float, default=0.25,
                       help="Target landed cost as fraction of retail")
    p_bid.set_defaults(func=cmd_bid)

    p_analyze = sub.add_parser("analyze", help="Analyze a manifest CSV (quick stats)")
    p_analyze.add_argument("csv")
    p_analyze.set_defaults(func=cmd_analyze)

    p_inspect = sub.add_parser(
        "inspect", help="Deep-analyze a manifest CSV (TVs, regalados, cajas/pallets)"
    )
    p_inspect.add_argument("csv")
    p_inspect.add_argument("--verify", action="store_true",
                           help="Try a live Amazon price check on doubtful giveaways")
    p_inspect.add_argument("--report-dir", default="data/reports")
    p_inspect.set_defaults(func=cmd_inspect)

    p_manifests = sub.add_parser(
        "manifests", help="Download + deep-analyze manifests of all active auctions"
    )
    p_manifests.add_argument("--country", default="ES")
    p_manifests.add_argument("--verify", action="store_true",
                             help="Try a live Amazon price check on doubtful giveaways")
    p_manifests.add_argument("--report-dir", default="data/reports")
    p_manifests.set_defaults(func=cmd_manifests)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
