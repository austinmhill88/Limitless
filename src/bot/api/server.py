import asyncio
import logging
import os
import json
import re
from typing import Dict, Optional
from contextlib import suppress

from fastapi import FastAPI, WebSocket, HTTPException, Header, Query, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import websockets

from bot.engine.state_machine import Engine, in_entry_window
from bot.broker.alpaca_adapter import get_account, now_et, set_alpaca_creds
from bot.config.settings import settings
from bot.logging.events import get_queue as get_events_queue
from bot.logging.events import publish as publish_event

logger = logging.getLogger("limitless.server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Limitless Trading Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/ui", StaticFiles(directory="webui", html=True), name="webui")

engine = Engine()
engine_task: Optional[asyncio.Task] = None

_events_queue = get_events_queue()

def _extract_bearer_token(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None

def _require_token(token_param: Optional[str], auth_header: Optional[str]) -> None:
    control_token = getattr(settings, "control_token", "") or ""
    if not control_token:
        return
    candidate = token_param or _extract_bearer_token(auth_header)
    if candidate != control_token:
        raise HTTPException(status_code=401, detail="Unauthorized")

def _get_current_broker_config(mode: Optional[str] = None) -> Dict[str, str]:
    """
    Use paper/live-specific envs if present; otherwise fall back to generic settings.*
    This restores prior behavior.
    """
    m = (mode or getattr(engine, "mode", "paper") or "paper").lower()
    if m not in ("paper", "live"):
        m = "paper"

    if m == "live":
        key = settings.alpaca_live_key_id or settings.alpaca_key_id
        sec = settings.alpaca_live_secret_key or settings.alpaca_secret_key
        base = settings.alpaca_live_base or settings.alpaca_base or "https://api.alpaca.markets"
    else:
        key = settings.alpaca_paper_key_id or settings.alpaca_key_id
        sec = settings.alpaca_paper_secret_key or settings.alpaca_secret_key
        base = settings.alpaca_paper_base or settings.alpaca_base or "https://paper-api.alpaca.markets"

    data_ws = os.getenv("ALPACA_DATA_WS", "wss://stream.data.alpaca.markets/v2/iex")
    if m == "paper" and data_ws.endswith("/sip"):
        data_ws = data_ws.rsplit("/", 1)[0] + "/iex"

    return {"key": key or "", "secret": sec or "", "base": base or "", "data_ws": data_ws}

BROKER = _get_current_broker_config()
ALPACA_DATA_WS = BROKER.get("data_ws", "wss://stream.data.alpaca.markets/v2/iex")
ALPACA_KEY_ID = BROKER.get("key", "")
ALPACA_SECRET_KEY = BROKER.get("secret", "")

_prices_queue: "asyncio.Queue[dict]" = asyncio.Queue()
_prices_symbols: set[str] = set(["AAPL", "MSFT"])
_prices_task: Optional[asyncio.Task] = None
_prices_ws: Optional[websockets.WebSocketClientProtocol] = None
_prices_lock = asyncio.Lock()
_prices_connect_lock = asyncio.Lock()
_prices_reconnect_in_progress = False
_prices_connected = False

async def _prices_emit(ev: dict):
    try:
        await _prices_queue.put(ev)
    except Exception:
        pass

async def _prices_send(payload: dict) -> bool:
    async with _prices_lock:
        if _prices_ws:
            try:
                await _prices_ws.send(json.dumps(payload))
                return True
            except Exception as e:
                logger.warning("Failed to send to Alpaca WS: %s", e)
    return False

async def _prices_connect_loop():
    global _prices_ws, _prices_connected, _prices_reconnect_in_progress
    while True:
        try:
            async with _prices_connect_lock:
                _prices_reconnect_in_progress = True
                logger.info("Connecting to Alpaca data WS: %s", ALPACA_DATA_WS)
                _prices_ws = await websockets.connect(ALPACA_DATA_WS, ping_interval=20, ping_timeout=20)
                auth_msg = {"action": "auth", "key": ALPACA_KEY_ID, "secret": ALPACA_SECRET_KEY}
                await _prices_ws.send(json.dumps(auth_msg))
                sub_msg = {"action": "subscribe", "bars": list(_prices_symbols), "quotes": list(_prices_symbols), "trades": list(_prices_symbols)}
                await _prices_ws.send(json.dumps(sub_msg))
                _prices_connected = True
                _prices_reconnect_in_progress = False
                await _prices_emit({"T": "success", "msg": f"connected to Alpaca data ws ({ALPACA_DATA_WS})"})
                try:
                    from bot.logging.events import publish
                    ws_name = "IEX" if "/iex" in ALPACA_DATA_WS else "SIP"
                    asyncio.create_task(publish(f"Market data: connected ({ws_name})"))
                    asyncio.create_task(publish(f"Market data: subscribed — trades/quotes/bars for {', '.join(sorted(_prices_symbols))}"))
                except Exception:
                    pass

            async for raw in _prices_ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    msg = raw
                if isinstance(msg, list):
                    for ev in msg:
                        await _prices_emit(ev)
                else:
                    await _prices_emit(msg)
        except Exception as e:
            logger.warning("Prices WS error: %s", e)
        finally:
            _prices_connected = False
            with suppress(Exception):
                if _prices_ws:
                    await _prices_ws.close()
            _prices_ws = None
            await asyncio.sleep(1.0)

@app.websocket("/events")
async def events_stream(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            line = await _events_queue.get()
            try:
                await ws.send_text(line)
            except WebSocketDisconnect:
                break
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("Events WS error: %s", e)
    finally:
        with suppress(Exception):
            await ws.close()

@app.get("/status")
async def status():
    acct = get_account()
    soft, hard = engine.daily_caps_state()
    return {
        "mode": getattr(engine, "mode", "paper"),
        "equity": getattr(acct, "equity", None),
        "buying_power": getattr(acct, "buying_power", None),
        "ts": now_et().isoformat(),
        "windows": {"is_in_window": in_entry_window()},
        "daily_caps": {
            "soft_hit": soft,
            "hard_hit": hard,
            "realized_usd": getattr(engine, "daily_realized_usd", 0.0),
            "soft_cap_pct": settings.soft_cap_pct,
            "hard_cap_pct": settings.hard_cap_pct,
        },
        "concurrency": {"open_positions": len(engine.positions), "cap": settings.concurrency_cap},
        "cooldowns": {
            "global_last_entry": getattr(engine, "global_last_entry", None).isoformat() if getattr(engine, "global_last_entry", None) else None
        },
    }

@app.get("/positions")
async def positions():
    rows = []
    for ps in engine.positions:
        rows.append({
            "symbol": ps.symbol,
            "entry_price": ps.entry_price,
            "target_price": ps.target_price,
            "qty": ps.qty,
            "opened_at": ps.opened_at,
            "bucket": ps.bucket,
        })
    return rows

@app.post("/control")
async def control(action: str = Query(...), token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
    _require_token(token, authorization)
    global engine_task
    if action == "start_bot":
        if engine_task and not engine_task.done():
            asyncio.create_task(publish_event("system: Bot already running"))
            return {"ok": True, "msg": "already running"}
        loop = asyncio.get_event_loop()
        engine.set_event_loop(loop)  # Pass the event loop to the engine
        engine_task = loop.create_task(asyncio.to_thread(engine.loop))
        asyncio.create_task(publish_event("system: Bot started"))
        return {"ok": True, "msg": "started"}
    elif action == "stop_bot":
        if engine_task and not engine_task.done():
            engine_task.cancel()
        asyncio.create_task(publish_event("system: Bot stopped"))
        return {"ok": True, "msg": "stopped"}
    else:
        raise HTTPException(status_code=400, detail="unknown action")

@app.post("/mode")
async def set_mode(mode: str = Query(...), token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
    _require_token(token, authorization)
    m = mode.lower()
    if m not in ("paper", "live"):
        raise HTTPException(status_code=400, detail="mode must be paper or live")
    cfg = _get_current_broker_config(m)

    globals().update({
        "BROKER": cfg,
        "ALPACA_DATA_WS": cfg.get("data_ws", ALPACA_DATA_WS),
        "ALPACA_KEY_ID": cfg.get("key", ALPACA_KEY_ID),
        "ALPACA_SECRET_KEY": cfg.get("secret", ALPACA_SECRET_KEY),
    })

    try:
        set_alpaca_creds(cfg.get("key", ""), cfg.get("secret", ""), cfg.get("base", ""))
    except Exception as e:
        logger.warning("Failed to set Alpaca REST creds on mode change: %s", e)

    asyncio.create_task(publish_event(f"system: Data mode set to {m}"))
    return {"ok": True, "mode": m}

@app.websocket("/stream")
async def heartbeat(ws: WebSocket, token: Optional[str] = Query(None)):
    try:
        _require_token(token, None)
    except HTTPException:
        await ws.close(code=403)
        return
    await ws.accept()
    try:
        while True:
            await ws.send_json({"type": "heartbeat", "ts": now_et().isoformat(), "in_window": in_entry_window()})
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("Heartbeat WS error: %s", e)
    finally:
        with suppress(Exception):
            await ws.close()

@app.get("/prices/status")
async def prices_status():
    return {
        "connected": _prices_connected,
        "reconnecting": _prices_reconnect_in_progress,
        "endpoint": ALPACA_DATA_WS,
        "subscribed": sorted(list(_prices_symbols)),
    }

@app.post("/prices/reconnect")
async def prices_reconnect():
    with suppress(Exception):
        if _prices_ws:
            await _prices_ws.close()
    asyncio.create_task(publish_event("system: Prices reconnect requested"))
    return {"ok": True}

@app.post("/prices/subscribe/{symbol}")
async def prices_sub(symbol: str):
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    # Allow alphanumeric plus dots and hyphens for symbols like BRK.A
    if not re.match(r'^[A-Z0-9.-]+$', sym):
        raise HTTPException(status_code=400, detail="invalid symbol format")
    _prices_symbols.add(sym)
    await _prices_send({"action": "subscribe", "bars": [sym], "quotes": [sym], "trades": [sym]})
    asyncio.create_task(publish_event(f"Market data: subscribed {sym}"))
    return {"ok": True, "subscribed": sorted(list(_prices_symbols))}

@app.post("/prices/unsubscribe/{symbol}")
async def prices_unsub(symbol: str):
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    # Allow alphanumeric plus dots and hyphens for symbols like BRK.A
    if not re.match(r'^[A-Z0-9.-]+$', sym):
        raise HTTPException(status_code=400, detail="invalid symbol format")
    with suppress(KeyError):
        _prices_symbols.remove(sym)
    await _prices_send({"action": "subscribe", "bars": list(_prices_symbols), "quotes": list(_prices_symbols), "trades": list(_prices_symbols)})
    asyncio.create_task(publish_event(f"Market data: unsubscribed {sym}"))
    return {"ok": True, "subscribed": sorted(list(_prices_symbols))}

@app.websocket("/prices")
async def prices_stream(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            ev = await _prices_queue.get()
            try:
                await ws.send_text(json.dumps(ev))
            except WebSocketDisconnect:
                break
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("Prices UI WS error: %s", e)
    finally:
        with suppress(Exception):
            await ws.close()

@app.get("/settings")
async def get_settings():
    """Get current trading parameters from settings object."""
    return {
        # Trading windows
        "morning_start": settings.morning_start,
        "morning_end": settings.morning_end,
        "power_start": settings.power_start,
        "power_end": settings.power_end,
        "friday_flatten_time": settings.friday_flatten_time,
        
        # Risk management
        "concurrency_cap": settings.concurrency_cap,
        "soft_cap_pct": settings.soft_cap_pct,
        "hard_cap_pct": settings.hard_cap_pct,
        "per_symbol_cooldown_sec": settings.per_symbol_cooldown_sec,
        "global_cooldown_sec": settings.global_cooldown_sec,
        
        # Strategy parameters
        "confirm_vwap_reclaim": settings.confirm_vwap_reclaim,
        "confirm_higher_low": settings.confirm_higher_low,
        "confirm_timeframe_minutes": settings.confirm_timeframe_minutes,
        "atr_len": settings.atr_len,
        "atr_take_profit_k": settings.atr_take_profit_k,
        "atr_trail_k": settings.atr_trail_k,
        "exit_in_power_window_only": settings.exit_in_power_window_only,
        "rvol_min": settings.rvol_min,
        "spread_max_pct": settings.spread_max_pct,
        "slippage_max_pct": settings.slippage_max_pct,
        "require_vwap_retest": settings.require_vwap_retest,
        "vwap_retest_lookback": settings.vwap_retest_lookback,
        "mae_k_atr": settings.mae_k_atr,
        
        # Other
        "target_pct": settings.target_pct,
        "watchlist": settings.watchlist,
        "dry_run": settings.dry_run,
    }

@app.post("/settings")
async def update_settings(
    data: dict,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None)
):
    """Update trading parameters at runtime (does not persist to .env)."""
    _require_token(token, authorization)
    
    updated = []
    
    # Update trading windows
    if "morning_start" in data:
        settings.morning_start = str(data["morning_start"])
        updated.append("morning_start")
    if "morning_end" in data:
        settings.morning_end = str(data["morning_end"])
        updated.append("morning_end")
    if "power_start" in data:
        settings.power_start = str(data["power_start"])
        updated.append("power_start")
    if "power_end" in data:
        settings.power_end = str(data["power_end"])
        updated.append("power_end")
    if "friday_flatten_time" in data:
        settings.friday_flatten_time = str(data["friday_flatten_time"])
        updated.append("friday_flatten_time")
    
    # Update risk management
    if "concurrency_cap" in data:
        settings.concurrency_cap = int(data["concurrency_cap"])
        updated.append("concurrency_cap")
    if "soft_cap_pct" in data:
        settings.soft_cap_pct = float(data["soft_cap_pct"])
        updated.append("soft_cap_pct")
    if "hard_cap_pct" in data:
        settings.hard_cap_pct = float(data["hard_cap_pct"])
        updated.append("hard_cap_pct")
    if "per_symbol_cooldown_sec" in data:
        settings.per_symbol_cooldown_sec = int(data["per_symbol_cooldown_sec"])
        updated.append("per_symbol_cooldown_sec")
    if "global_cooldown_sec" in data:
        settings.global_cooldown_sec = int(data["global_cooldown_sec"])
        updated.append("global_cooldown_sec")
    
    # Update strategy parameters
    if "confirm_vwap_reclaim" in data:
        settings.confirm_vwap_reclaim = bool(data["confirm_vwap_reclaim"])
        updated.append("confirm_vwap_reclaim")
    if "confirm_higher_low" in data:
        settings.confirm_higher_low = bool(data["confirm_higher_low"])
        updated.append("confirm_higher_low")
    if "confirm_timeframe_minutes" in data:
        settings.confirm_timeframe_minutes = int(data["confirm_timeframe_minutes"])
        updated.append("confirm_timeframe_minutes")
    if "atr_len" in data:
        settings.atr_len = int(data["atr_len"])
        updated.append("atr_len")
    if "atr_take_profit_k" in data:
        settings.atr_take_profit_k = float(data["atr_take_profit_k"])
        updated.append("atr_take_profit_k")
    if "atr_trail_k" in data:
        settings.atr_trail_k = float(data["atr_trail_k"])
        updated.append("atr_trail_k")
    if "exit_in_power_window_only" in data:
        settings.exit_in_power_window_only = bool(data["exit_in_power_window_only"])
        updated.append("exit_in_power_window_only")
    if "rvol_min" in data:
        settings.rvol_min = float(data["rvol_min"])
        updated.append("rvol_min")
    if "spread_max_pct" in data:
        settings.spread_max_pct = float(data["spread_max_pct"])
        updated.append("spread_max_pct")
    if "slippage_max_pct" in data:
        settings.slippage_max_pct = float(data["slippage_max_pct"])
        updated.append("slippage_max_pct")
    if "require_vwap_retest" in data:
        settings.require_vwap_retest = bool(data["require_vwap_retest"])
        updated.append("require_vwap_retest")
    if "vwap_retest_lookback" in data:
        settings.vwap_retest_lookback = int(data["vwap_retest_lookback"])
        updated.append("vwap_retest_lookback")
    if "mae_k_atr" in data:
        settings.mae_k_atr = float(data["mae_k_atr"])
        updated.append("mae_k_atr")
    if "target_pct" in data:
        settings.target_pct = float(data["target_pct"])
        updated.append("target_pct")
    
    asyncio.create_task(publish_event(f"Settings updated: {', '.join(updated)}"))
    
    return {"ok": True, "updated": updated, "message": "Settings updated successfully (changes will be lost on restart unless updated in .env)"}

@app.on_event("startup")
async def on_startup():
    global _prices_task
    try:
        cfg = BROKER
        set_alpaca_creds(cfg.get("key", ""), cfg.get("secret", ""), cfg.get("base", "https://paper-api.alpaca.markets"))
        logger.info("Initialized Alpaca REST creds at startup (base=%s)", cfg.get("base", ""))
    except Exception as e:
        logger.warning("Failed to initialize Alpaca REST creds at startup: %s", e)

    if not _prices_task or _prices_task.done():
        _prices_task = asyncio.create_task(_prices_connect_loop())
    logger.info("Server startup complete")

@app.on_event("shutdown")
async def on_shutdown():
    with suppress(Exception):
        if _prices_ws:
            await _prices_ws.close()
    with suppress(Exception):
        if _prices_task:
            _prices_task.cancel()
    logger.info("Server shutdown complete")