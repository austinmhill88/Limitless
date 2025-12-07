from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.storage.buckets_ledger import BucketsLedger, next_settlement_time_et

TZ_ET = ZoneInfo("America/New_York")

def test_buckets_settlement():
    ledger = BucketsLedger(path="test_buckets.json")
    now = datetime(2025, 1, 7, 10, 0, tzinfo=TZ_ET)
    bucket = ledger.pick_bucket(needed_cash=1.0)
    assert bucket
    ledger.consume_on_buy(bucket["name"], 10.0)
    ledger.add_unsettled_on_sell(bucket["name"], 10.0, now)
    # Before settlement
    ledger.release_settled(now)
    b = [b for b in ledger.buckets if b["name"] == bucket["name"]][0]
    unsettled_count = len(b["unsettled"])
    assert unsettled_count >= 1
    # After settlement time
    later = next_settlement_time_et(now) + timedelta(minutes=1)
    ledger.release_settled(later)
    b = [b for b in ledger.buckets if b["name"] == bucket["name"]][0]
    assert len(b["unsettled"]) == 0