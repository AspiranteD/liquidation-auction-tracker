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


class RecordingCaller:
    def __init__(self):
        self.calls = []

    def call_auction_alert(self, auction, decision, minutes_left=None):
        self.calls.append(auction.auction_id)
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
    caller = RecordingCaller()
    pipeline.caller = caller
    return pipeline, recorder, caller


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
    pipeline, recorder, caller = _pipeline(tmp_path, [_auction(minutes_to_close=25)])
    pipeline.run()
    assert recorder.calls == [(1, "t30")]
    pipeline.run()  # same window, already alerted -> no duplicate
    assert recorder.calls == [(1, "t30")]
    assert caller.calls == []


def test_ladder_fires_every_stage_as_close_approaches(tmp_path):
    pipeline, recorder, caller = _pipeline(tmp_path, [])
    for minutes in (25, 14, 9, 4):
        pipeline.client = FakeClient([_auction(minutes_to_close=minutes)])
        pipeline.run()
    assert recorder.calls == [(1, "t30"), (1, "t15"), (1, "t10"), (1, "t5")]
    assert caller.calls == [1]  # call escalation at <= 5 min, once


def test_auction_seen_late_starts_at_tightest_stage(tmp_path):
    # First seen with 12 min left -> stage 15 fires (not 30), then 10, then 5.
    pipeline, recorder, caller = _pipeline(tmp_path, [])
    for minutes in (12, 8, 3):
        pipeline.client = FakeClient([_auction(minutes_to_close=minutes)])
        pipeline.run()
    assert recorder.calls == [(1, "t15"), (1, "t10"), (1, "t5")]
    assert caller.calls == [1]


def test_no_reminder_far_from_close(tmp_path):
    pipeline, recorder, caller = _pipeline(tmp_path, [_auction(minutes_to_close=120)])
    pipeline.run()
    assert recorder.calls == []
    assert caller.calls == []


def test_no_reminder_after_close(tmp_path):
    pipeline, recorder, caller = _pipeline(tmp_path, [_auction(minutes_to_close=-3)])
    pipeline.run()
    assert recorder.calls == []
    assert caller.calls == []


def test_call_escalation_only_once(tmp_path):
    pipeline, recorder, caller = _pipeline(tmp_path, [])
    for minutes in (4, 2, 1):
        pipeline.client = FakeClient([_auction(minutes_to_close=minutes)])
        pipeline.run()
    assert recorder.calls == [(1, "t5")]
    assert caller.calls == [1]


def test_over_threshold_never_alerts_nor_calls(tmp_path):
    # bid 2600 on 25k -> total >12%: excluded even right before close.
    pipeline, recorder, caller = _pipeline(
        tmp_path, [_auction(minutes_to_close=4, bid=2600.0)]
    )
    pipeline.run()
    assert recorder.calls == []
    assert caller.calls == []


def test_storage_migrates_old_schema(tmp_path):
    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    # The original pre-reminder schema (no alert_log table).
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

    storage = Storage(db_path)  # must create the alert_log table in place
    assert storage.was_alerted(1, "t30") is False
    storage.mark_alerted(1, "t30")
    storage.mark_alerted(1, "t30")  # idempotent
    assert storage.was_alerted(1, "t30") is True
    assert storage.was_alerted(1, "call") is False
