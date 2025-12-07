import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.strategy.rules import build_indicators, opening_range, qualify_entry, qualifies_all

TZ_ET = ZoneInfo("America/New_York")

def make_df(prices, vols):
    times = pd.date_range(start=pd.Timestamp(datetime(2025, 1, 7, 9, 30), tz=TZ_ET), periods=len(prices), freq="1min")
    df = pd.DataFrame({
        "t_et": times,
        "open": prices,
        "high": [p * 1.001 for p in prices],
        "low": [p * 0.999 for p in prices],
        "close": prices,
        "volume": vols,
    })
    return df

def test_orh_and_entry_qualify():
    prices = [100 + i*0.05 for i in range(60)]  # light uptrend
    vols = [100000]*60
    df = make_df(prices, vols)
    orh, _ = opening_range(df, df["t_et"].iloc[0])
    df = build_indicators(df)
    info = qualify_entry(df, orh)
    assert info["uptrend"]
    assert info["above_vwap"]
    assert info["above_orh"] == (df["close"].iloc[-1] > orh)
    assert qualifies_all(info) in (True, False)  # ensure no crash