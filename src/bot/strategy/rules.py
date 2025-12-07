import math
from typing import Dict, Any, Tuple
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.config.settings import settings

TZ_ET = ZoneInfo("America/New_York")

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def compute_vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    cum_vp = (tp * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum().replace(0, math.nan)
    return cum_vp / cum_vol

def opening_range(df: pd.DataFrame, start_et: datetime) -> Tuple[float, float]:
    end = start_et + timedelta(minutes=15)
    mask = (df["t_et"] >= start_et) & (df["t_et"] < end)
    cut = df[mask]
    if cut.empty:
        return float("nan"), float("nan")
    return float(cut["high"].max()), float(cut["low"].min())

def build_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["vwap"] = compute_vwap(df)
    return df

def qualify_entry(df: pd.DataFrame, orh: float) -> Dict[str, Any]:
    last = df.iloc[-1]
    prev3 = df.tail(3)
    uptrend = last["ema20"] > last["ema50"]
    above_vwap = last["close"] > last["vwap"]
    above_orh = last["close"] > orh
    # VWAP touch or near-touch within tolerance
    vwap_touch = (prev3["low"] <= prev3["vwap"]).any()
    near_touch = (abs(prev3["low"] - prev3["vwap"]) / prev3["vwap"]).abs().min() <= settings.vwap_touch_tolerance_pct
    touched = bool(vwap_touch or near_touch)
    # Close back above vwap on current bar
    close_back_above = last["close"] > last["vwap"]
    extension = (last["close"] - last["vwap"]) / last["vwap"]
    not_extended = extension <= settings.vwap_extension_max_pct

    return {
        "uptrend": bool(uptrend),
        "above_vwap": bool(above_vwap),
        "above_orh": bool(above_orh),
        "touched_vwap_recently": bool(touched),
        "close_back_above": bool(close_back_above),
        "not_extended": bool(not_extended),
        "price": float(last["close"]),
        "signal_bar_high": float(last["high"]),
    }

def qualifies_all(entry_info: Dict[str, Any]) -> bool:
    return all([
        entry_info["uptrend"],
        entry_info["above_vwap"],
        entry_info["above_orh"],
        entry_info["touched_vwap_recently"],
        entry_info["close_back_above"],
        entry_info["not_extended"],
    ])