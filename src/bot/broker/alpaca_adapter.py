import os
import requests
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bot.config.settings import settings

import logging
logger = logging.getLogger("limitless.alpaca")

TZ_ET = ZoneInfo("America/New_York")

# Runtime-overridable creds and bases (default to settings/env on import)
_ALPACA_KEY_ID = (
    getattr(settings, "alpaca_key_id", None)
    or os.getenv("ALPACA_PAPER_KEY_ID")
    or os.getenv("ALPACA_KEY_ID")
    or ""
)
_ALPACA_SECRET_KEY = (
    getattr(settings, "alpaca_secret_key", None)
    or os.getenv("ALPACA_PAPER_SECRET_KEY")
    or os.getenv("ALPACA_SECRET_KEY")
    or ""
)
# Default base is paper unless settings overrides
_ALPACA_BASE = (
    getattr(settings, "alpaca_base", None)
    or os.getenv("ALPACA_PAPER_BASE", "https://paper-api.alpaca.markets")
)
# Market data REST base (if you use it); keep existing settings if present
_ALPACA_DATA_BASE = getattr(settings, "alpaca_data_base", "https://data.alpaca.markets")

def set_alpaca_creds(key_id: str, secret_key: str, base_url: str, data_base_url: Optional[str] = None):
    """
    Allow the server to switch between paper/live at runtime by setting REST creds/base.
    Optionally update the market data REST base if provided.
    """
    global _ALPACA_KEY_ID, _ALPACA_SECRET_KEY, _ALPACA_BASE, _ALPACA_DATA_BASE
    _ALPACA_KEY_ID = (key_id or "").strip()
    _ALPACA_SECRET_KEY = (secret_key or "").strip()
    _ALPACA_BASE = (base_url or _ALPACA_BASE).strip() or _ALPACA_BASE
    if data_base_url:
        _ALPACA_DATA_BASE = data_base_url

    # Debug: log masked key and base to verify creds are set
    masked = _ALPACA_KEY_ID[:6] + ("..." if _ALPACA_KEY_ID else "")
    logger.info("Alpaca REST creds set: key=%s base=%s", masked, _ALPACA_BASE)

def http_headers():
    return {
        "APCA-API-KEY-ID": _ALPACA_KEY_ID,
        "APCA-API-SECRET-KEY": _ALPACA_SECRET_KEY,
        "Content-Type": "application/json",
    }

@dataclass
class AccountInfo:
    equity: float
    buying_power: float
    is_paper: bool

def _assert_creds():
    if not _ALPACA_KEY_ID or not _ALPACA_SECRET_KEY:
        raise RuntimeError("Alpaca REST credentials not set. Call set_alpaca_creds() at startup or via /mode.")

def get_account() -> AccountInfo:
    _assert_creds()
    url = f"{_ALPACA_BASE}/v2/account"
    r = requests.get(url, headers=http_headers(), timeout=10)
    if r.status_code == 401:
        masked = _ALPACA_KEY_ID[:6] + ("..." if _ALPACA_KEY_ID else "")
        logger.error("Alpaca 401 Unauthorized calling %s (key=%s base=%s)", url, masked, _ALPACA_BASE)
    r.raise_for_status()
    data = r.json()
    return AccountInfo(
        equity=float(data.get("equity", 0)),
        buying_power=float(data.get("buying_power", 0)),
        is_paper=bool(data.get("paper", True)),
    )

def _select_feed() -> str:
    """
    Choose data feed for REST endpoints.
    - If ALPACA_DATA_FEED is set, use it (iex or sip).
    - Otherwise: iex for paper base, sip for live base.
    """
    feed = os.getenv("ALPACA_DATA_FEED")
    if feed:
        return feed.lower()
    return "iex" if "paper-api" in _ALPACA_BASE else "sip"

def get_bars(symbol: str, limit: int = 300) -> List[Dict[str, Any]]:
    _assert_creds()
    url = f"{_ALPACA_DATA_BASE}/v2/stocks/{symbol}/bars"
    feed = _select_feed()
    params = {
        "timeframe": "1Min",
        "limit": limit,
        "adjustment": "raw",
        "feed": feed,
    }
    r = requests.get(url, headers=http_headers(), params=params, timeout=10)
    r.raise_for_status()
    js = r.json().get("bars", [])
    return js

def latest_trade_price(symbol: str) -> Optional[float]:
    _assert_creds()
    url = f"{_ALPACA_DATA_BASE}/v2/stocks/{symbol}/trades/latest"
    feed = _select_feed()
    params = {"feed": feed}
    r = requests.get(url, headers=http_headers(), params=params, timeout=10)
    r.raise_for_status()
    p = r.json().get("trade", {}).get("p")
    return float(p) if p is not None else None

def place_buy_stop(symbol: str, qty: int, stop_price: float, tp_limit: float) -> Dict[str, Any]:
    payload = {
        "symbol": symbol,
        "side": "buy",
        "type": "stop_limit",
        "time_in_force": "day",
        "qty": str(qty),
        "limit_price": f"{stop_price:.2f}",
        "stop_price": f"{stop_price:.2f}",
        "order_class": "bracket",
        "take_profit": {"limit_price": f"{tp_limit:.2f}"},
        # No stop_loss per spec
    }
    if getattr(settings, "dry_run", False):
        return {"id": "paper-order", "payload": payload}
    _assert_creds()
    url = f"{_ALPACA_BASE}/v2/orders"
    r = requests.post(url, headers=http_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

def place_buy_limit(symbol: str, qty: int, limit_price: float, tp_limit: float) -> Dict[str, Any]:
    payload = {
        "symbol": symbol,
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "qty": str(qty),
        "limit_price": f"{limit_price:.2f}",
        "order_class": "bracket",
        "take_profit": {"limit_price": f"{tp_limit:.2f}"},
    }
    if getattr(settings, "dry_run", False):
        return {"id": "paper-order", "payload": payload}
    _assert_creds()
    url = f"{_ALPACA_BASE}/v2/orders"
    r = requests.post(url, headers=http_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

def cancel_order(order_id: str):
    if getattr(settings, "dry_run", False):
        return
    _assert_creds()
    url = f"{_ALPACA_BASE}/v2/orders/{order_id}"
    r = requests.delete(url, headers=http_headers(), timeout=10)
    r.raise_for_status()

def get_positions() -> List[Dict[str, Any]]:
    _assert_creds()
    url = f"{_ALPACA_BASE}/v2/positions"
    r = requests.get(url, headers=http_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_open_orders(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    _assert_creds()
    url = f"{_ALPACA_BASE}/v2/orders"
    params = {"status": "open"}
    if symbol:
        params["symbols"] = symbol
    r = requests.get(url, headers=http_headers(), params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def now_et():
    return datetime.now(timezone.utc).astimezone(TZ_ET)