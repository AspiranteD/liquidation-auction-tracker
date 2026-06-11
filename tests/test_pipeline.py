import sqlite3
from datetime import datetime, timedelta, timezone

from liquidation_tracker.config import Settings
from liquidation_tracker.models import Auction
from liquidation_tracker.pipeline import MonitorPipeline
from liquidation_tracker.storage import Storage


class FakeClient:
    def __init__(self, auctions):
        self._auctions = auctions

    def list_auctions(self, country="ES", limit=48):
        return list(self._auctions)

    def fetch_lot_id(self, auction):
        return None


class RecordingNotifier:
    def __init__(self):
        self.calls = []

    def send_auction_alert(self, auction, decision, stage="t30", minutes_left=None):
        self.calls.append((auction.auction_id, stage))
        return True


def _pipeline(tmp_path, auctions):
    settings = Settings(
        db_path=str(tmp_path / "test.db"),
        manifest_dir=str(tmp_path / "manifests"),
    )
    pipeline = MonitorPipeline(settings)
    pipeline.client = FakeClient(auctions)
    recorder = RecordingNotifier()
    pipeline.notifiers = [recorder]
    return pipeline, recorder


def _auction(minutes_to_close, bid=500.0, auction_id=1):
    return Auction(
        auction_id=auction_id,
        title="4 Pallets of Home Goods, 100 Pieces, Total Retail €25,000, ES Stock",
        url="https://example.test/a",
        country="ES",
        lot_type="4 Pallets",
        retail_value=25000.0,
        pieces=100,
        current_bid=bid,
        end_time=datetime.now(timezone.utc) + timedelta(minutes=minutes_to_close),
    )


def test_t30_reminder_fires_once_inside_window(tmp_path):
    pipeline, recorder = _pipeline(tmp_path, [_auction(minutes_to_close=25)])
    pipeline.run()
    assert recorder.calls == [(1, "t30")]
    pipeline.run()  # same window, already alerted -> no duplicate
    assert recorder.calls == [(1, "t30")]


def test_no_reminder_far_from_close(tmp_path):
    pipeline, recorder = _pipeline(tmp_path, [_auction(minutes_to_close=120)])
    pipeline.run()
    assert recorder.calls == []


def test_no_reminder_after_close(tmp_path):
    pipeline, recorder = _pipeline(tmp_path, [_auction(minutes_to_close=-3)])
    pipeline.run()
    assert recorder.calls == []


def test_t5_last_call_when_very_good(tmp_path):
    # bid 500 on 25k -> total ~4% of retail: very good at 4 min to close.
    pipeline, recorder = _pipeline(tmp_path, [_auction(minutes_to_close=4)])
    pipeline.run()
    assert recorder.calls == [(1, "t5")]
    pipeline.run()  # no duplicate, and t30 was superseded
    assert recorder.calls == [(1, "t5")]


def test_t5_window_falls_back_to_t30_when_not_very_good(tmp_path):
    # bid 1800 on 25k -> total ~11%: key (<=12%) but not very good (>10%),
    # so inside the final window it gets the (late) t30 reminder instead.
    pipeline, recorder = _pipeline(
        tmp_path, [_auction(minutes_to_close=4, bid=1800.0)]
    )
    pipeline.run()
    assert recorder.calls == [(1, "t30")]


def test_over_threshold_never_alerts(tmp_path):
    # bid 2600 on 25k -> total >12%: excluded even inside the window.
    pipeline, recorder = _pipeline(
        tmp_path, [_auction(minutes_to_close=20, bid=2600.0)]
    )
    pipeline.run()
    assert recorder.calls == []


def test_storage_migrates_old_schema(tmp_path):
    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    # The original pre-reminder schema (no alerted_t30/alerted_t5 columns).
    conn.execute(
        """
        CREATE TABLE auction (
            auction_id INTEGER PRIMARY KEY, title TEXT, url TEXT,
            country TEXT, lot_type TEXT, retail_value REAL, pieces INTEGER,
            current_bid REAL, end_time TEXT, lot_id TEXT, suggested_bid REAL,
            estimated_total REAL, total_pct REAL, first_seen TEXT,
            last_seen TEXT, alerted INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()

    Storage(db_path)  # must add the alerted_t30/alerted_t5 columns in place

    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(auction)")}
    conn.close()
    assert {"alerted_t30", "alerted_t5"} <= columns
