import os
from dataclasses import dataclass, field
from typing import List, Dict
from pathlib import Path

# Robustly load the root .env (Limitless/.env), then optionally api/.env without overriding.
try:
    from dotenv import load_dotenv

    here = Path(__file__).resolve()
    root_env = None
    # Walk up to 5 levels to find the first .env (repo root typically)
    for p in [here.parent, *here.parents[:5]]:
        candidate = p / ".env"
        if candidate.exists():
            root_env = candidate
            break

    if root_env:
        load_dotenv(str(root_env), override=False)
        # If there's an api/.env under the same root, load it without overriding root values
        api_env = root_env.parent / "api" / ".env"
        if api_env.exists():
            load_dotenv(str(api_env), override=False)
except Exception:
    pass


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val is not None else default


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val is not None else default


def _env_str(name: str, default: str) -> str:
    val = os.getenv(name)
    return val if val is not None else default


def _env_watchlist() -> List[str]:
    raw = os.getenv("WATCHLIST", "TSLA,NVDA,AAPL,MSFT,QQQ,SPY")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _env_tier_sizes() -> Dict[str, float]:
    return {
        "TSLA": _env_float("SIZE_TSLA", 10000.0),
        "NVDA": _env_float("SIZE_NVDA", 10000.0),
        "AAPL": _env_float("SIZE_AAPL", 10000.0),
        "MSFT": _env_float("SIZE_MSFT", 10000.0),
        "QQQ": _env_float("SIZE_QQQ", 5000.0),
        "SPY": _env_float("SIZE_SPY", 5000.0),
    }


@dataclass
class Settings:
    # Broker/API (generic keys as before)
    alpaca_base: str = _env_str("ALPACA_BASE", "https://paper-api.alpaca.markets")
    alpaca_data_base: str = _env_str("ALPACA_DATA_BASE", "https://data.alpaca.markets")
    alpaca_key_id: str = _env_str("ALPACA_KEY_ID", "")
    alpaca_secret_key: str = _env_str("ALPACA_SECRET_KEY", "")
    finnhub_api_key: str = _env_str("FINNHUB_API_KEY", "")

    # Optional paper/live specific
    alpaca_paper_base: str = _env_str("ALPACA_PAPER_BASE", "")
    alpaca_paper_key_id: str = _env_str("ALPACA_PAPER_KEY_ID", "")
    alpaca_paper_secret_key: str = _env_str("ALPACA_PAPER_SECRET_KEY", "")
    alpaca_live_base: str = _env_str("ALPACA_LIVE_BASE", "")
    alpaca_live_key_id: str = _env_str("ALPACA_LIVE_KEY_ID", "")
    alpaca_live_secret_key: str = _env_str("ALPACA_LIVE_SECRET_KEY", "")

    # Control/auth
    control_token: str = _env_str("CONTROL_TOKEN", "")

    # Mode
    dry_run: bool = _env_bool("DRY_RUN", True)

    # Symbols and priority
    watchlist: List[str] = field(default_factory=_env_watchlist)
    symbol_priority: List[str] = field(default_factory=_env_watchlist)

    # Windows (ET)
    morning_start: str = _env_str("WINDOW_MORNING_START", "09:45")
    morning_end: str = _env_str("WINDOW_MORNING_END", "11:15")
    power_start: str = _env_str("WINDOW_POWER_START", "15:00")
    power_end: str = _env_str("WINDOW_POWER_END", "15:55")
    friday_flatten_time: str = _env_str("FRIDAY_FLATTEN_TIME", "15:45")

    # Strategy tolerances
    target_pct: float = _env_float("TARGET_PCT", 0.005)
    vwap_touch_tolerance_pct: float = _env_float("VWAP_TOLERANCE_PCT", 0.0015)
    vwap_extension_max_pct: float = _env_float("VWAP_EXTENSION_MAX_PCT", 0.01)
    or_width_skip_pct: float = _env_float("OR_WIDTH_SKIP_PCT", 0.01)
    entry_cancel_minutes: int = _env_int("ENTRY_CANCEL_MINUTES", 2)
    entry_order_type: str = _env_str("ENTRY_ORDER_TYPE", "buy_stop")

    # Buckets (cash mode)
    bucket_file: str = _env_str("BUCKETS_FILE", "buckets.json")
    bucket_init_total_usd: float = _env_float("BUCKET_INIT_TOTAL_USD", 4000.0)
    bucket_utilization_pct: float = _env_float("BUCKET_UTILIZATION_PCT", 0.93)

    # Margin mode
    concurrency_cap: int = _env_int("CONCURRENCY_CAP", 3)
    per_symbol_cooldown_sec: int = _env_int("PER_SYMBOL_COOLDOWN_SEC", 600)
    global_cooldown_sec: int = _env_int("GLOBAL_COOLDOWN_SEC", 300)

    # Tiered fixed notional sizing
    tier_sizes: Dict[str, float] = field(default_factory=_env_tier_sizes)

    # Daily caps
    soft_cap_pct: float = _env_float("DAILY_SOFT_CAP_PCT", 0.01)
    hard_cap_pct: float = _env_float("DAILY_HARD_CAP_PCT", 0.015)
    stretch_cutoff_time: str = _env_str("STRETCH_CUTOFF_TIME", "15:30")

    # Earnings skip
    earnings_skip_next_day: bool = _env_bool("EARNINGS_SKIP_NEXT_DAY", True)

    # Confirmation and exit
    confirm_vwap_reclaim: bool = _env_bool("CONFIRM_VWAP_RECLAIM", True)
    confirm_higher_low: bool = _env_bool("CONFIRM_HIGHER_LOW", True)
    confirm_timeframe_minutes: int = _env_int("CONFIRM_TIMEFRAME_MIN", 5)
    atr_len: int = _env_int("ATR_LEN", 14)
    atr_take_profit_k: float = _env_float("ATR_TP_K", 0.5)
    atr_trail_k: float = _env_float("ATR_TRAIL_K", 1.0)
    exit_in_power_window_only: bool = _env_bool("EXIT_IN_POWER_WINDOW_ONLY", True)

    # Hard guardrails
    rvol_min: float = _env_float("RVOL_MIN", 1.1)
    spread_max_pct: float = _env_float("SPREAD_MAX_PCT", 0.0015)
    slippage_max_pct: float = _env_float("SLIPPAGE_MAX_PCT", 0.003)
    require_vwap_retest: bool = _env_bool("REQUIRE_VWAP_RETEST", True)
    vwap_retest_lookback: int = _env_int("VWAP_RETEST_LOOKBACK", 5)
    mae_k_atr: float = _env_float("MAE_K_ATR", 1.2)

settings = Settings()