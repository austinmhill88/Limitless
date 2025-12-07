import asyncio
import logging
import os
import json
from typing import Dict, Any, Optional, List
from contextlib import suppress

from fastapi import FastAPI, WebSocket, HTTPException, Header, Query, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import websockets  # pip install websockets

from bot.engine.state_machine import Engine, in_entry_window
from bot.broker.alpaca_adapter import get_account, now_et
from bot.config.settings import settings
from bot.logging.events import get_queue as get_events_queue  # NEW

logger = logging.getLogger("limitless.server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Limitless Trading Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the simple web UI
app.mount("/ui", StaticFiles(directory="webui", html=True), name="webui")

engine = Engine()
engine_task: Optional[asyncio.Task] = None

# --- Operator events queue (human-readable) ---
_events_queue = get_events_queue()

def _extract_bearer_token(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None

def _require_token(token_param: Optional[str], auth_header: Optional[str]) -> None:
    """
    Protect ONLY control endpoints. Streams (/events, /prices) remain open.
    """
    control_token = getattr(settings, "control_token", None) or ""
    if not control_token:
        return
    candidate = token_param or _extract_bearer_token(auth_header)
    if candidate != control_token:
        raise HTTPException(status_code=401, detail="Unauthorized")

def _get_current_broker_config(mode: Optional[str] = None) -> Dict[str, str]:
    """
    Returns Alpaca REST/data credentials based on the given mode ("paper" or "live").
    Data WS endpoint comes from ALPACA_DATA_WS env; default to IEX stream unless SIP is available.
    """
    m = (mode or getattr(engine, "mode", "paper") or "paper").lower()
    if m not in ("paper", "live"):
        m = "paper"

    if m == "live":
        key = getattr(settings, "ALPACA_LIVE_KEY_ID", "") or os.getenv("ALPACA_LIVE_KEY_ID", "")
        sec = getattr(settings, "ALPACA_LIVE_SECRET_KEY", "") or os.getenv("ALPACA_LIVE_SECRET_KEY", "")
        base = getattr(settings, "ALPACA_LIVE_BASE", "") or os.getenv("ALPACA_LIVE_BASE", "https://api.alpaca.markets")
    else:
        key = getattr(settings, "ALPACA_PAPER_KEY_ID", "") or os.getenv("ALPACA_PAPER_KEY_ID", "")
        sec = getattr(settings, "ALPACA_PAPER_SECRET_KEY", "") or os.getenv("ALPACA_PAPER_SECRET_KEY", "")
        base = getattr(settings, "ALPACA_PAPER_BASE", "") or os.getenv("ALPACA_PAPER_BASE", "https://paper-api.alpaca.markets")

    data_ws = getattr(settings, "ALPACA_DATA_WS", "") or os.getenv("ALPACA_DATA_WS", "wss://stream.data.alpaca.markets/v2/iex")
    return {"key": key, "secret": sec, "base": base, "data_ws": data_ws}

BROKER = _get_current_broker_config()
ALPACA_DATA_WS = BROKER.get("data_ws", "wss://stream.data.alpaca.markets/v2/iex")
ALPACA_KEY_ID = BROKER.get("key", "")
ALPACA_SECRET_KEY = BROKER.get("secret", "")

# -------------------------
# Prices (upstream Alpaca WS) and downstream fanout
# -------------------------
_prices_queue: "asyncio.Queue[dict]" = asyncio.Queue()
_prices_symbols: set[str] = set(["AAPL", "MSFT"])  # default symbols
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
                # Auth
                auth_msg = {"action": "auth", "key": ALPACA_KEY_ID, "secret": ALPACA_SECRET_KEY}
                await _prices_ws.send(json.dumps(auth_msg))
                # Subscribe to bars, quotes, trades for current symbols
                sub_msg = {"action": "subscribe", "bars": list(_prices_symbols), "quotes": list(_prices_symbols), "trades": list(_prices_symbols)}
                await _prices_ws.send(json.dumps(sub_msg))
                _prices_connected = True
                _prices_reconnect_in_progress = False
                await _prices_emit({"T": "success", "msg": f"connected to Alpaca data ws ({ALPACA_DATA_WS})"})
            # Receive loop
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
            await asyncio.sleep(1.0)  # small backoff

# --- Operator /events stream (as you had it; no token required) ---
@app.websocket("/events")
async def events_stream(ws: WebSocket):
    """
    Streams human-readable operator log messages in real time.
    """
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

# -------------------------
# Public status and positions
# -------------------------
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

# -------------------------
# Control and mode (token-protected)
# -------------------------
@app.post("/control")
async def control(action: str = Query(...), token: Optional[str] = Query(None), authorization: Optional[str] = Header(None)):
    _require_token(token, authorization)
    global engine_task
    if action == "start_bot":
        if engine_task and not engine_task.done():
            return {"ok": True, "msg": "already running"}
        loop = asyncio.get_event_loop()
        engine_task = loop.create_task(asyncio.to_thread(engine.loop))
        return {"ok": True, "msg": "started"}
    elif action == "stop_bot":
        if engine_task and not engine_task.done():
            engine_task.cancel()
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
    return {"ok": True, "mode": m}

# -------------------------
# Heartbeat stream (token-protected)
# -------------------------
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

# -------------------------
# Prices endpoints (no token required)
# -------------------------
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
    return {"ok": True}

@app.post("/prices/subscribe/{symbol}")
async def prices_sub(symbol: str):
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    _prices_symbols.add(sym)
    await _prices_send({"action": "subscribe", "bars": [sym], "quotes": [sym], "trades": [sym]})
    return {"ok": True, "subscribed": sorted(list(_prices_symbols))}

@app.post("/prices/unsubscribe/{symbol}")
async def prices_unsub(symbol: str):
    sym = symbol.upper().strip()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    with suppress(KeyError):
        _prices_symbols.remove(sym)
    await _prices_send({"action": "subscribe", "bars": list(_prices_symbols), "quotes": list(_prices_symbols), "trades": list(_prices_symbols)})
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

# -------------------------
# Startup/shutdown hooks
# -------------------------
@app.on_event("startup")
async def on_startup():
    global _prices_task
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