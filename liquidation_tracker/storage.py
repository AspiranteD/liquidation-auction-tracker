"""SQLite persistence for auctions and their bid analysis.

Append-friendly history: every time an auction is seen its current bid and
analysis are upserted, so you keep the latest state plus a separate snapshot
log for trend analysis.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, List, Optional

from .calculator import CostBreakdown
from .models import Auction

_SCHEMA = """
CREATE TABLE IF NOT EXISTS auction (
    auction_id      INTEGER PRIMARY KEY,
    title           TEXT,
    url             TEXT,
    country         TEXT,
    lot_type        TEXT,
    retail_value    REAL,
    pieces          INTEGER,
    current_bid     REAL,
    end_time        TEXT,
    lot_id          TEXT,
    suggested_bid   REAL,
    estimated_total REAL,
    total_pct       REAL,
    first_seen      TEXT,
    last_seen       TEXT,
    alerted         INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bid_snapshot (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    auction_id   INTEGER,
    current_bid  REAL,
    captured_at  TEXT,
    FOREIGN KEY (auction_id) REFERENCES auction (auction_id)
);

CREATE TABLE IF NOT EXISTS alert_log (
    auction_id   INTEGER NOT NULL,
    stage        TEXT NOT NULL,
    sent_at      TEXT,
    PRIMARY KEY (auction_id, stage)
);

CREATE INDEX IF NOT EXISTS idx_auction_country ON auction (country);
CREATE INDEX IF NOT EXISTS idx_snapshot_auction ON bid_snapshot (auction_id);
"""

# Stage names: "t30"/"t15"/"t10"/"t5" for the reminder ladder, "call" for
# the voice-call escalation.
_STAGE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class Storage:
    def __init__(self, db_path: str = "data/auctions.db") -> None:
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert_auction(
        self, auction: Auction, breakdown: Optional[CostBreakdown] = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        suggested_bid = breakdown.bid if breakdown else None
        estimated_total = breakdown.total_cost if breakdown else None
        total_pct = breakdown.total_pct_of_retail if breakdown else None

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT auction_id FROM auction WHERE auction_id = ?",
                (auction.auction_id,),
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE auction SET
                        title=?, url=?, country=?, lot_type=?, retail_value=?,
                        pieces=?, current_bid=?, end_time=?, lot_id=?,
                        suggested_bid=?, estimated_total=?, total_pct=?, last_seen=?
                    WHERE auction_id=?
                    """,
                    (
                        auction.title, auction.url, auction.country, auction.lot_type,
                        auction.retail_value, auction.pieces, auction.current_bid,
                        auction.end_time.isoformat() if auction.end_time else None,
                        auction.lot_id, suggested_bid, estimated_total, total_pct,
                        now, auction.auction_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO auction (
                        auction_id, title, url, country, lot_type, retail_value,
                        pieces, current_bid, end_time, lot_id, suggested_bid,
                        estimated_total, total_pct, first_seen, last_seen, alerted
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                    """,
                    (
                        auction.auction_id, auction.title, auction.url, auction.country,
                        auction.lot_type, auction.retail_value, auction.pieces,
                        auction.current_bid,
                        auction.end_time.isoformat() if auction.end_time else None,
                        auction.lot_id, suggested_bid, estimated_total, total_pct,
                        now, now,
                    ),
                )

            conn.execute(
                "INSERT INTO bid_snapshot (auction_id, current_bid, captured_at) "
                "VALUES (?,?,?)",
                (auction.auction_id, auction.current_bid, now),
            )

    @staticmethod
    def _check_stage(stage: str) -> str:
        if not _STAGE_RE.match(stage):
            raise ValueError(f"Invalid alert stage: {stage!r}")
        return stage

    def was_alerted(self, auction_id: int, stage: str = "t30") -> bool:
        self._check_stage(stage)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM alert_log WHERE auction_id = ? AND stage = ?",
                (auction_id, stage),
            ).fetchone()
            return row is not None

    def mark_alerted(self, auction_id: int, stage: str = "t30") -> None:
        self._check_stage(stage)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO alert_log (auction_id, stage, sent_at) "
                "VALUES (?, ?, ?)",
                (auction_id, stage, datetime.now(timezone.utc).isoformat()),
            )

    def all_auctions(self) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM auction ORDER BY last_seen DESC"
            ).fetchall()

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM auction").fetchone()["n"]
