import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd
from zoneinfo import ZoneInfo
import asyncio

from bot.config.settings import settings
from bot.broker.alpaca_adapter import (
    get_account, get_bars, latest_trade_price, place_buy_stop, place_buy_limit,
    get_positions, get_open_orders, cancel_order, now_et,
)
from bot.data.finnhub_earnings import earnings
from bot.storage.buckets_ledger import BucketsLedger
from bot.strategy.rules import build_indicators, opening_range, qualify_entry, qualifies_all
from bot.logging.audit import Auditor
from bot.logging.events import publish, format_skip, format_info, format_entry, format_close

TZ_ET = ZoneInfo("America/New_York")

def parse_time_et(hhmm: str, base_date: Optional[datetime] = None) -> datetime:
    t = now_et() if base_date is None else base_date
    h, m = [int(x) for x in hhmm.split(":")]
    return t.replace(hour=h, minute=m, second=0, microsecond=0)

def in_entry_window() -> bool:
    t = now_et()
    if t.weekday() >= 5:  # weekend
        return False
    return (
        parse_time_et(settings.morning_start) <= t <= parse_time_et(settings.morning_end)
        or parse_time_et(settings.power_start) <= t <= parse_time_et(settings.power_end)
    )

def in_power_window() -> bool:
    t = now_et()
    if t.weekday() >= 5:
        return False
    return parse_time_et(settings.power_start) <= t <= parse_time_et(settings.power_end)

def friday_flatten_due() -> bool:
    t = now_et()
    if t.weekday() == 4:  # Friday
        return t >= parse_time_et(settings.friday_flatten_time)
    return False

@dataclass
class PositionState:
    symbol: str
    entry_price: float
    target_price: float
    qty: int
    opened_at: str
    bucket: Optional[str] = None  # only for cash mode
    max_price: Optional[float] = None
    trail_stop: Optional[float] = None

def _calc_atr(df: pd.DataFrame, length: int) -> float:
    if df is None or df.empty or len(df) < max(3, length):
        return 0.0
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=length, min_periods=length).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else 0.0

def _has_higher_low(df: pd.DataFrame, lookback: int = 3) -> bool:
    if df is None or len(df) < (lookback + 1):
        return False
    lows = df["low"].tail(lookback + 1).tolist()
    return lows[-1] > lows[-2] and min(lows[-3:-1]) <= lows[-2]

def _vwap_reclaim(df: pd.DataFrame) -> bool:
    if df is None or df.empty or "vwap" not in df.columns:
        return False
    last = df.iloc[-1]
    return bool(last["close"] > last["vwap"])

def _vwap_retest(df: pd.DataFrame, lookback: int) -> bool:
    """
    Require a pullback that holds above VWAP within the last `lookback` bars.
    """
    if df is None or df.empty or "vwap" not in df.columns:
        return False
    window = df.tail(max(lookback, 2))
    closes_above = window["close"] > window["vwap"]
    lows_above = window["low"] >= window["vwap"]
    return bool((closes_above & lows_above).any())

def _estimate_spread_pct(df: pd.DataFrame) -> float:
    """
    Approximate bid-ask spread using bar high/low proxy if quotes aren't available.
    """
    if df is None or df.empty:
        return 0.0
    last = df.iloc[-1]
    if last["close"] <= 0:
        return 0.0
    spread = abs(last["high"] - last["low"])
    return float(spread / max(1e-6, last["close"]))

def _estimate_rvol(df: pd.DataFrame, base_len: int = 50) -> float:
    """
    Relative volume = current bar volume / average of recent bars.
    """
    if df is None or df.empty or "volume" not in df.columns:
        return 1.0
    recent = df["volume"].tail(base_len)
    if len(recent) < 5:
        return 1.0
    avg = recent[:-1].mean() if len(recent) > 1 else recent.mean()
    cur = recent.iloc[-1]
    if avg <= 0:
        return 1.0
    return float(cur / avg)

class Engine:
    def __init__(self):
        self.aud = Auditor()
        self.ledger = BucketsLedger()
        self.positions: List[PositionState] = []
        self.pending_orders: Dict[str, Dict] = {}  # symbol -> {"id", "placed_at"}
        self.mode: str = "cash"  # cash | margin
        self.daily_realized_usd: float = 0.0
        self.daily_start_equity: float = 0.0
        self.per_symbol_last_exit: Dict[str, datetime] = {}
        self.global_last_entry: Optional[datetime] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        for sym in settings.watchlist:
            earnings.refresh_symbol(sym)
    
    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the event loop to use for publishing events from the thread."""
        self._event_loop = loop
    
    def _publish(self, message: str):
        """Safely publish a message to the event queue from the engine thread."""
        if self._event_loop and self._event_loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(publish(message), self._event_loop)
            except Exception:
                pass  # Silently fail if event loop is not available

    def refresh_mode(self):
        acct = get_account()
        if acct.equity >= 25000:
            self.mode = "margin"
        else:
            self.mode = "cash"
        if self.daily_start_equity == 0.0:
            self.daily_start_equity = acct.equity

    def earnings_skip(self, symbol: str) -> bool:
        today_iso = now_et().strftime("%Y-%m-%d")
        return earnings.is_skip_day(symbol, today_iso)

    def can_open_new_position(self, symbol: str) -> bool:
        if not in_entry_window():
            return False
        last_exit = self.per_symbol_last_exit.get(symbol)
        if last_exit and (now_et() - last_exit).total_seconds() < settings.per_symbol_cooldown_sec:
            return False
        if self.global_last_entry and (now_et() - self.global_last_entry).total_seconds() < settings.global_cooldown_sec:
            return False
        if self.mode == "margin":
            if len(self.positions) >= settings.concurrency_cap:
                return False
            soft_hit, hard_hit = self.daily_caps_state()
            if hard_hit:
                return False
            if soft_hit:
                if len(self.positions) > 0:
                    return False
                cutoff = parse_time_et(settings.stretch_cutoff_time)
                if now_et() > cutoff:
                    return False
        else:
            if len(self.positions) > 0 or len(self.pending_orders) > 0:
                return False
        return True

    def daily_caps_state(self) -> Tuple[bool, bool]:
        if self.daily_start_equity <= 0:
            return False, False
        realized_pct = self.daily_realized_usd / self.daily_start_equity
        soft_hit = realized_pct >= settings.soft_cap_pct
        hard_hit = realized_pct >= settings.hard_cap_pct
        return soft_hit, hard_hit

    def compute_size_cash_mode(self, price: float) -> Tuple[int, Optional[str]]:
        self.ledger.release_settled(now_et())
        need = price
        b = self.ledger.pick_bucket(needed_cash=need)
        if not b:
            return 0, None
        spend = b["settled_cash"] * settings.bucket_utilization_pct
        qty = int(max(1, spend // price))
        if qty <= 0:
            return 0, None
        return qty, b["name"]

    def compute_size_margin_mode(self, symbol: str, price: float) -> int:
        notional = settings.tier_sizes.get(symbol, 5000.0)
        qty = int(max(1, notional // price))
        return qty

    def _apply_entry_confirmations(self, df: pd.DataFrame) -> bool:
        if df is None or df.empty:
            return False
        ok = True
        if settings.confirm_higher_low:
            ok = ok and _has_higher_low(df, lookback=3)
        if settings.confirm_vwap_reclaim:
            ok = ok and _vwap_reclaim(df)
        if settings.require_vwap_retest:
            ok = ok and _vwap_retest(df, lookback=settings.vwap_retest_lookback)
        return ok

    def _apply_pretrade_guardrails(self, symbol: str, df: pd.DataFrame) -> bool:
        """
        Hard guardrails before sizing/ordering: RVOL, spread.
        """
        rvol = _estimate_rvol(df)
        if rvol < settings.rvol_min:
            self.aud.log("entry_skipped_rvol", {"rvol": rvol, "min": settings.rvol_min})
            self._publish(format_skip(symbol, "insufficient relative volume", {"rvol": rvol, "min": settings.rvol_min}))
            return False
        spread_pct = _estimate_spread_pct(df)
        if spread_pct > settings.spread_max_pct:
            self.aud.log("entry_skipped_spread", {"spread_pct": spread_pct, "max": settings.spread_max_pct})
            self._publish(format_skip(symbol, "spread too wide", {"spread_pct": spread_pct, "max": settings.spread_max_pct}))
            return False
        return True

    def scan_and_enter(self):
        self.refresh_mode()
        for symbol in settings.symbol_priority:
            if self.earnings_skip(symbol):
                self._publish(format_skip(symbol, "earnings lockout"))
                continue
            if not self.can_open_new_position(symbol):
                continue

            bars = get_bars(symbol, limit=300)
            if not bars:
                continue
            df = pd.DataFrame(bars)
            df["t"] = pd.to_datetime(df["t"], utc=True)
            df["t_et"] = df["t"].dt.tz_convert(TZ_ET)
            df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
            df = df[["t_et", "open", "high", "low", "close", "volume"]].reset_index(drop=True)

            orh, _ = opening_range(df, parse_time_et("09:30"))
            df = build_indicators(df)
            info = qualify_entry(df, orh)

            if not qualifies_all(info):
                self._publish(format_skip(symbol, "setup invalid against entry criteria"))
                continue

            # Confirmations
            if not self._apply_entry_confirmations(df):
                self.aud.log("entry_rejected_confirmation", {"symbol": symbol})
                self._publish(format_skip(symbol, "confirmation not satisfied (VWAP/Higher-low/Retest)"))
                continue

            # Guardrails
            if not self._apply_pretrade_guardrails(symbol, df):
                continue

            price = info["price"]
            signal_high = info["signal_bar_high"]

            # Slippage guard: if current price has already run too far past signal_high, skip
            if settings.slippage_max_pct > 0 and signal_high > 0:
                run_pct = (price - signal_high) / signal_high
                if run_pct > settings.slippage_max_pct:
                    self.aud.log("entry_skipped_slippage", {"symbol": symbol, "run_pct": run_pct, "max": settings.slippage_max_pct})
                    self._publish(format_skip(symbol, "signal exceeded — slippage limit breached", {"run_pct": run_pct, "max": settings.slippage_max_pct}))
                    continue

            atr = _calc_atr(df, settings.atr_len)
            entry_price = signal_high if settings.entry_order_type == "buy_stop" else price
            tp = settings.target_pct
            if settings.atr_take_profit_k > 0 and atr > 0:
                alt_target = entry_price + settings.atr_take_profit_k * atr
                target = round(max(entry_price * (1.0 + tp), alt_target), 2)
            else:
                target = round(entry_price * (1.0 + tp), 2)

            if self.mode == "cash":
                qty, bucket = self.compute_size_cash_mode(entry_price)
                if qty <= 0 or not bucket:
                    self._publish(format_skip(symbol, "insufficient settled cash"))
                    continue
                try:
                    self.ledger.consume_on_buy(bucket, qty * entry_price)
                except Exception:
                    self._publish(format_skip(symbol, "ledger consume failed"))
                    continue
            else:
                qty = self.compute_size_margin_mode(symbol, entry_price)
                bucket = None

            if settings.entry_order_type == "buy_stop":
                order = place_buy_stop(symbol, qty, stop_price=signal_high, tp_limit=target)
            else:
                order = place_buy_limit(symbol, qty, limit_price=price, tp_limit=target)

            oid = order.get("id", f"paper-{symbol}-{int(time.time())}")
            self.pending_orders[symbol] = {
                "id": oid,
                "placed_at": now_et(),
                "qty": qty,
                "entry_price": entry_price,
                "target": target,
                "bucket": bucket,
            }
            self.global_last_entry = now_et()
            self.aud.log("entry_order_placed", {"symbol": symbol, "qty": qty, "entry": entry_price, "target": target, "mode": self.mode})
            self._publish(format_entry(symbol, qty, entry_price, target, self.mode))
            break

    def cancel_stale_entries(self):
        to_cancel = []
        for symbol, po in self.pending_orders.items():
            placed_at = po["placed_at"]
            if (now_et() - placed_at) > timedelta(minutes=settings.entry_cancel_minutes):
                to_cancel.append(symbol)

        for symbol in to_cancel:
            oid = self.pending_orders[symbol]["id"]
            try:
                cancel_order(oid)
            except Exception:
                pass
            po = self.pending_orders[symbol]
            if po.get("bucket"):
                for b in self.ledger.buckets:
                    if b["name"] == po["bucket"]:
                        b["settled_cash"] += po["qty"] * po["entry_price"]
                        self.ledger.save()
                        break
            self.aud.log("entry_order_cancelled", {"symbol": symbol, "order_id": oid})
            self._publish(format_info(symbol, "entry cancelled — time expired", {"minutes": settings.entry_cancel_minutes}))
            del self.pending_orders[symbol]

    def reconcile_positions(self):
        pos_list = [] if settings.dry_run else get_positions()

        for symbol, po in list(self.pending_orders.items()):
            filled = settings.dry_run or any(p.get("symbol") == symbol for p in pos_list)
            if filled:
                ps = PositionState(
                    symbol=symbol,
                    entry_price=po["entry_price"],
                    target_price=po["target"],
                    qty=po["qty"],
                    opened_at=now_et().isoformat(),
                    bucket=po.get("bucket"),
                )
                self.positions.append(ps)
                self.aud.log("position_opened", {"symbol": symbol, "entry": ps.entry_price, "target": ps.target_price, "qty": ps.qty, "mode": self.mode})
                self._publish(format_info(symbol, "position opened", {"entry": ps.entry_price, "target": ps.target_price, "qty": ps.qty}))
                del self.pending_orders[symbol]

        for ps in list(self.positions):
            lp = latest_trade_price(ps.symbol)
            if lp is None:
                continue

            if ps.max_price is None:
                ps.max_price = lp
            else:
                ps.max_price = max(ps.max_price, lp)

            if friday_flatten_due():
                exit_price = ps.target_price
                proceeds = ps.qty * exit_price
                if ps.bucket:
                    self.ledger.add_unsettled_on_sell(ps.bucket, proceeds, now_et())
                realized = (exit_price - ps.entry_price) * ps.qty
                self.daily_realized_usd += realized
                self.per_symbol_last_exit[ps.symbol] = now_et()
                self.positions.remove(ps)
                self.aud.log("position_closed", {"symbol": ps.symbol, "exit_price": exit_price, "realized": realized, "reason": "friday_flatten"})
                self._publish(format_close(ps.symbol, exit_price, realized, "friday_flatten"))
                continue

            if lp >= ps.target_price:
                exit_price = ps.target_price
                proceeds = ps.qty * exit_price
                if ps.bucket:
                    self.ledger.add_unsettled_on_sell(ps.bucket, proceeds, now_et())
                realized = (exit_price - ps.entry_price) * ps.qty
                self.daily_realized_usd += realized
                self.per_symbol_last_exit[ps.symbol] = now_et()
                self.positions.remove(ps)
                self.aud.log("position_closed", {"symbol": ps.symbol, "exit_price": exit_price, "realized": realized, "reason": "target_hit"})
                self._publish(format_close(ps.symbol, exit_price, realized, "target_hit"))
                continue

            # MAE early cut: if price drops more than k*ATR below entry before target, exit early
            if settings.mae_k_atr > 0:
                bars = get_bars(ps.symbol, limit=max(50, settings.atr_len + 2)) or []
                if bars:
                    dfx = pd.DataFrame(bars).rename(columns={"h": "high", "l": "low", "c": "close"})
                    atr = _calc_atr(dfx[["high", "low", "close"]], settings.atr_len)
                    if atr > 0 and lp < (ps.entry_price - settings.mae_k_atr * atr):
                        exit_price = lp
                        proceeds = ps.qty * exit_price
                        if ps.bucket:
                            self.ledger.add_unsettled_on_sell(ps.bucket, proceeds, now_et())
                        realized = (exit_price - ps.entry_price) * ps.qty
                        self.daily_realized_usd += realized
                        self.per_symbol_last_exit[ps.symbol] = now_et()
                        self.positions.remove(ps)
                        self.aud.log("position_closed", {"symbol": ps.symbol, "exit_price": exit_price, "realized": realized, "reason": "mae_cut"})
                        self._publish(format_close(ps.symbol, exit_price, realized, "mae_cut"))
                        continue

            # ATR trailing stop (optional, mostly in power window)
            if settings.atr_trail_k > 0:
                if not settings.exit_in_power_window_only or in_power_window():
                    bars = get_bars(ps.symbol, limit=max(50, settings.atr_len + 2)) or []
                    if bars:
                        dfx = pd.DataFrame(bars).rename(columns={"h": "high", "l": "low", "c": "close"})
                        atr = _calc_atr(dfx[["high", "low", "close"]], settings.atr_len)
                        if atr > 0:
                            proposed = ps.max_price - settings.atr_trail_k * atr
                            ps.trail_stop = (proposed if ps.trail_stop is None else max(ps.trail_stop, proposed))
                            if ps.trail_stop is not None and lp < ps.trail_stop and lp > ps.entry_price:
                                exit_price = lp
                                proceeds = ps.qty * exit_price
                                if ps.bucket:
                                    self.ledger.add_unsettled_on_sell(ps.bucket, proceeds, now_et())
                                realized = (exit_price - ps.entry_price) * ps.qty
                                self.daily_realized_usd += realized
                                self.per_symbol_last_exit[ps.symbol] = now_et()
                                self.positions.remove(ps)
                                self.aud.log("position_closed", {"symbol": ps.symbol, "exit_price": exit_price, "realized": realized, "reason": "atr_trail_stop"})
                                self._publish(format_close(ps.symbol, exit_price, realized, "atr_trail_stop"))
                                continue

    def loop(self):
        self.refresh_mode()
        for sym in settings.watchlist:
            earnings.refresh_symbol(sym)

        while True:
            try:
                self.cancel_stale_entries()
                self.reconcile_positions()
                self.scan_and_enter()
                time.sleep(5)
            except KeyboardInterrupt:
                break
            except Exception as e:
                self.aud.log("engine_error", {"msg": str(e)})
                self._publish(f"engine: error — {e}")
                time.sleep(2)