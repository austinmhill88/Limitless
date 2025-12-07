import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict
from zoneinfo import ZoneInfo

from bot.config.settings import settings

TZ_ET = ZoneInfo("America/New_York")

def next_settlement_time_et(now_et: datetime) -> datetime:
    # T+1 settlement at ~09:00 ET next business day
    nxt = now_et + timedelta(days=1)
    while nxt.weekday() >= 5:  # weekend
        nxt += timedelta(days=1)
    return nxt.replace(hour=9, minute=0, second=0, microsecond=0)

@dataclass
class Lot:
    amount: float
    settles_at_iso: str

class BucketsLedger:
    def __init__(self, path: str = settings.bucket_file):
        self.path = path
        self.buckets: List[Dict] = []
        self.load()

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.buckets = json.load(f)
        else:
            # Initialize two buckets split by total
            total = settings.bucket_init_total_usd
            half = round(total / 2.0, 2)
            self.buckets = [
                {"name": "A", "settled_cash": half, "unsettled": []},
                {"name": "B", "settled_cash": half, "unsettled": []},
            ]
            self.save()

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.buckets, f, indent=2)

    def release_settled(self, now: datetime):
        changed = False
        for b in self.buckets:
            still_unsettled = []
            for lot in b.get("unsettled", []):
                settles_at = datetime.fromisoformat(lot["settles_at_iso"])
                if now >= settles_at:
                    b["settled_cash"] += lot["amount"]
                    changed = True
                else:
                    still_unsettled.append(lot)
            b["unsettled"] = still_unsettled
        if changed:
            self.save()

    def pick_bucket(self, needed_cash: float) -> Dict:
        # Return first bucket with sufficient settled cash
        for b in self.buckets:
            if b["settled_cash"] >= needed_cash:
                return b
        return {}

    def consume_on_buy(self, bucket_name: str, cash_used: float):
        for b in self.buckets:
            if b["name"] == bucket_name:
                if b["settled_cash"] < cash_used:
                    raise RuntimeError("Insufficient settled cash.")
                b["settled_cash"] -= cash_used
                self.save()
                return
        raise RuntimeError("Bucket not found.")

    def add_unsettled_on_sell(self, bucket_name: str, amount: float, now: datetime):
        for b in self.buckets:
            if b["name"] == bucket_name:
                settles_at = next_settlement_time_et(now)
                b.setdefault("unsettled", []).append({
                    "amount": amount,
                    "settles_at_iso": settles_at.isoformat(),
                })
                self.save()
                return
        raise RuntimeError("Bucket not found.")