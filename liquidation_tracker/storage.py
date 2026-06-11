"""SQLite persistence for auctions and their bid analysis.

Append-friendly history: every time an auction is seen its current bid and
analysis are upserted, so you keep the latest state plus a separate snapshot
log for trend analysis.
"""
from __future__ import annotations

import os
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
    alerted         INTEGER DEFAULT 0,
    alerted_t30     INTEGER DEFAULT 0,
    alerted_t5      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bid_snapshot (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    auction_id   INTEGER,
    current_bid  REAL,
    captured_at  TEXT,
    FOREIGN KEY (auction_id) REFERENCES auction (auction_id)
);

CREATE INDEX IF NOT EXISTS idx_auction_country ON auction (country);
CREATE INDEX IF NOT EXISTS idx_snapshot_auction ON bid_snapshot (auction_id);
"""

# Reminder stages: "t30" (~30 min before close) and "t5" (last call, ~5 min).
ALERT_STAGES = ("t30", "t5")

# Columns added after the initial release; applied to pre-existing databases.
_MIGRATIONS = (
    "ALTER TABLE auction ADD COLUMN alerted_t30 INTEGER DEFAULT 0",
    "ALTER TABLE auction ADD COLUMN alerted_t5 INTEGER DEFAULT 0",
)


class Storage:
    def __init__(self, db_path: str = "data/auctions.db") -> None:
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            for statement in _MIGRATIONS:
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError:
                    pass  # column already exists

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
    def _stage_column(stage: str) -> str:
        if stage not in ALERT_STAGES:
            raise ValueError(f"Unknown alert stage: {stage!r}")
        return f"alerted_{stage}"

    def was_alerted(self, auction_id: int, stage: str = "t30") -> bool:
        column = self._stage_column(stage)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {column} FROM auction WHERE auction_id = ?", (auction_id,)
            ).fetchone()
            return bool(row and row[column])

    def mark_alerted(self, auction_id: int, stage: str = "t30") -> None:
        column = self._stage_column(stage)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE auction SET {column} = 1 WHERE auction_id = ?",
                (auction_id,),
            )

    def all_auctions(self) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM auction ORDER BY last_seen DESC"
            ).fetchall()

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM auction").fetchone()["n"]
