import asyncio
from typing import Optional

# Global async queue for human-readable operator log lines
_EVENTS_QUEUE: "asyncio.Queue[str]" = asyncio.Queue()

async def publish(line: str):
    """
    Publish a single-line operator message.
    """
    try:
        await _EVENTS_QUEUE.put(line)
    except Exception:
        pass

def get_queue() -> "asyncio.Queue[str]":
    """
    Expose the queue for server streaming.
    """
    return _EVENTS_QUEUE

# --- Plain-English format helpers ---

def fmt_skip(symbol: str, reason: str, details: Optional[dict] = None) -> str:
    suffix = ""
    if details:
        kv = ", ".join(f"{k}={v:.4f}" if isinstance(v, (float, int)) else f"{k}={v}" for k, v in details.items())
        suffix = f" ({kv})"
    return f"{symbol}: Skipped — {reason}{suffix}"

def fmt_info(symbol: str, note: str, details: Optional[dict] = None) -> str:
    suffix = ""
    if details:
        kv = ", ".join(f"{k}={v:.4f}" if isinstance(v, (float, int)) else f"{k}={v}" for k, v in details.items())
        suffix = f" ({kv})"
    return f"{symbol}: {note}{suffix}"

def fmt_entry(symbol: str, qty: int, entry: float, target: float, mode: str, label: Optional[str] = None) -> str:
    prefix = f"{symbol}: Placed entry"
    if label:
        prefix = f"{symbol}: {label} — entry placed"
    return f"{prefix} — qty={qty}, entry={entry:.2f}, target={target:.2f}, mode={mode}"

def fmt_open(symbol: str, qty: int, entry: float, target: float) -> str:
    return f"{symbol}: Position opened — bought {qty} at {entry:.2f}, target {target:.2f}"

def fmt_close(symbol: str, price: float, realized: float, reason: str) -> str:
    # Map internal reasons to plain-English
    reason_map = {
        "target_hit": "Took profit",
        "mae_cut": "Cut loss early — price fell too far",
        "atr_trail_stop": "Trailing stop hit — locking gains",
        "friday_flatten": "Friday close — flattening before weekend",
    }
    human = reason_map.get(reason, reason)
    pnl = f"+{realized:.2f}" if realized >= 0 else f"{realized:.2f}"
    return f"{symbol}: {human} — sold at {price:.2f}, P&L {pnl}"

# Backwards-compatible aliases for prior imports
format_skip = fmt_skip
format_info = fmt_info
format_entry = fmt_entry
format_close = fmt_close