import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from bot.broker.alpaca_data import AlpacaDataStream

router = APIRouter()

_stream = AlpacaDataStream(symbols={"AAPL", "MSFT"})  # default watchlist
_queue = asyncio.Queue()

async def _listener(msg):
    await _queue.put(msg)

@router.on_event("startup")
async def startup():
    _stream.on_message(_listener)
    asyncio.create_task(_stream.connect_and_run())

@router.on_event("shutdown")
async def shutdown():
    await _stream.stop()

@router.websocket("/prices")
async def prices_ws(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await _queue.get()
            try:
                await ws.send_json(msg)
            except WebSocketDisconnect:
                break
            except Exception:
                pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass

@router.post("/prices/subscribe/{symbol}")
async def subscribe_symbol(symbol: str):
    _stream.add_symbol(symbol)
    return {"ok": True, "symbols": sorted(_stream.symbols)}

@router.post("/prices/unsubscribe/{symbol}")
async def unsubscribe_symbol(symbol: str):
    _stream.remove_symbol(symbol)
    return {"ok": True, "symbols": sorted(_stream.symbols)}