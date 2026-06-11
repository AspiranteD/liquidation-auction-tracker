# liquidation-auction-tracker

A self-contained pipeline that monitors **Amazon EU liquidation auctions** on
[B-Stock](https://bstock.com/amazoneu/), downloads the lot manifests, runs a
profitability analysis and alerts you by email and/or WhatsApp when an auction
matches your buying criteria.

Built from a real problem: B-Stock liquidation truckloads close fast, the
headline bid hides the true landed cost (transport, VAT, marketplace fee, the
Spanish "recargo de equivalencia"), and there's no way to get notified when a
genuinely profitable lot appears. This tool scrapes the auctions, computes the
**maximum bid** you can afford for a target margin, and alerts you.

> Standalone showcase project. It uses SQLite and has no dependencies beyond the
> public B-Stock site and (optionally) an SMTP account. It does **not** place
> bids — it monitors and advises.

## How it works

```
B-Stock listing page (per country)
        │  requests + BeautifulSoup
        ▼
┌──────────────────────┐
│  Parser               │ ── auction id, title, retail, pieces, lot type,
│                       │     current bid, end time
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Bid calculator       │ ── reverse-solves the landed-cost model to give the
│                       │     max bid for a target % of retail
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Manifest analyzer    │ ── (optional) downloads the lot CSV, aggregates by
│                       │     category / condition, finds top-value items
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Rule engine          │ ── min retail, max landed %, country, piece count
└──────────┬───────────┘
           │ key auction?
           ▼
┌──────────────────────┐
│  SQLite + Email alert │ ── upsert + bid-history snapshots, one email per lot
└──────────────────────┘
```

## The bid calculator

The core of the project. Given a lot's retail value and a target landed cost
(as a % of retail), it reverse-solves the cost model to tell you the maximum
bid you can place:

```
total_cost = bid + transport + VAT + bstock_fee + RE

VAT        = (transport + bid) × 21%
bstock_fee = bid × 4%
RE         = total_cost × 5.2%      (recargo de equivalencia)
```

Solving for `bid`:

```
max_bid = (total_cost − transport×1.21 − 0.052×total_cost) / (1 + 0.04 + 0.21)
```

```bash
$ python -m liquidation_tracker.cli bid --retail 16670 --type "Small Truckload" --pct 0.25
Lot type      : Small Truckload
Retail value  : EUR 16,670.00
Target landed : 25% of retail
----------------------------------------
Max bid       : EUR 2,741.38
Transport     : EUR 433.11
VAT (21%)     : EUR 666.64
B-Stock fee   : EUR 109.66
RE (5.2%)     : EUR 216.71
Total landed  : EUR 4,167.50
Bid % retail  : 16.4%
```

Transport is a flat rate per lot type (`Truckload`, `Small Truckload`,
`4 Pallets DE/PL/IT`, …) and is fully configurable.

## Quick start

```bash
git clone https://github.com/AspiranteD/liquidation-auction-tracker.git
cd liquidation-auction-tracker
pip install -r requirements.txt
cp .env.example .env        # optional: configure email alerts and thresholds
```

Try it offline (no network) with the bundled sample manifest:

```bash
python examples/demo.py
```

## CLI

```bash
# Compute the max bid for a lot
python -m liquidation_tracker.cli bid --retail 16670 --type "Small Truckload" --pct 0.25

# List active auctions for a country, with suggested bids (live)
python -m liquidation_tracker.cli list --country ES

# Analyze a manifest CSV
python -m liquidation_tracker.cli analyze data/sample_manifest.csv

# Full pipeline: scrape -> evaluate -> store in SQLite -> email key auctions
python -m liquidation_tracker.cli monitor --country ES
```

## Alerts (email + WhatsApp)

Two channels, independently switchable in `.env`:

- **Email**: set the SMTP variables and `EMAIL_ALERTS_ENABLED=true`.
- **WhatsApp** (via the free [CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/)
  API): add the CallMeBot number on WhatsApp, send it
  `I allow callmebot to send me messages`, copy the apikey it replies with into
  `CALLMEBOT_APIKEY`, set `CALLMEBOT_PHONE` to your number in international
  format and `WHATSAPP_ALERTS_ENABLED=true`.

Alerts are **reminders tied to the auction close**, evaluated with the bid as
it stands at that moment (so run the monitor every ~5 minutes):

- **T-30**: first reminder inside the last `REMINDER_WINDOW_MINUTES` (30) before
  close, if the auction still qualifies.
- **T-5 last call**: a second reminder inside the last
  `FINAL_REMINDER_WINDOW_MINUTES` (5) when the lot is still a very good deal
  (total landed cost ≤ `ALERT_VERY_GOOD_TOTAL_PCT`, default 10%).

An auction qualifies when it passes every rule:

| Rule | Env var | Default |
|------|---------|---------|
| Country in monitor list | `MONITOR_COUNTRIES` | `ES` |
| Lot family monitored, retail ≥ per-type minimum | `ALERT_MIN_RETAIL_4_PALLETS` / `_SMALL_TRUCKLOAD` / `_TRUCKLOAD` | `20000` / `50000` / `100000` |
| Current bid still lands ≤ ceiling (of retail) | `ALERT_MAX_TOTAL_PCT`, or `ALERT_ELECTRONICS_MAX_TOTAL_PCT` when the title matches `ELECTRONICS_KEYWORDS` | `0.12` / `0.15` |
| Pieces ≥ threshold | `ALERT_MIN_PIECES` | `0` |

The ceilings apply to the **total landed cost** (bid + transport + VAT + fee +
RE) — a 12% total ceiling puts the bid itself around 5-10% of retail. The
suggested max bid in each alert is computed against the applicable ceiling.
Each reminder stage fires at most once per auction.

## Storage

SQLite (`data/auctions.db`):

- `auction` — latest state per auction plus the computed suggested bid.
- `bid_snapshot` — append-only log of the current bid each time the auction is
  seen, so you can chart how bids evolve toward close.

## Anti-bot note

B-Stock sits behind Cloudflare. A plain `requests` session with a browser
User-Agent works from most residential IPs (and is what this project uses). If
you hit a challenge page, the network layer (`client.py`) is isolated behind
three methods (`list_auctions`, `fetch_lot_id`, `download_manifest`) so it can
be swapped for a Playwright-backed client without touching the rest of the
pipeline.

## Project layout

```
liquidation_tracker/
├── client.py       # B-Stock network layer (requests session)
├── parser.py       # HTML -> Auction models (unit-testable)
├── calculator.py   # the bid calculator (landed-cost model)
├── analyzer.py     # manifest CSV -> aggregate stats
├── alerts.py       # rule engine: is this auction key?
├── notifier.py     # email (SMTP) + WhatsApp (CallMeBot) alerts
├── storage.py      # SQLite persistence + bid history
├── config.py       # env-driven settings
├── pipeline.py     # orchestration
└── cli.py          # command-line interface
tests/              # pytest (calculator invariants, analyzer)
data/               # sample manifest (anonymized)
examples/demo.py    # offline end-to-end demo
```

## Tests

```bash
pytest -q
```

## License

MIT
