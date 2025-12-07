import asyncio
import json
import os
import websockets

ALPACA_DATA_WS = os.getenv("ALPACA_DATA_WS", "wss://stream.data.alpaca.markets/v2/sip")
ALPACA_KEY_ID = os.getenv("ALPACA_KEY_ID", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")

class AlpacaDataStream:
    def __init__(self, symbols=None):
        self.symbols = set(symbols or [])
        self._ws = None
        self._listeners = set()
        self._running = False

    def add_symbol(self, symbol: str):
        s = symbol.strip().upper()
        if s:
            self.symbols.add(s)

    def remove_symbol(self, symbol: str):
        self.symbols.discard(symbol.strip().upper())

    def on_message(self, callback):
        self._listeners.add(callback)

    async def _emit(self, msg):
        for cb in list(self._listeners):
            try:
                await cb(msg)
            except Exception:
                pass

    async def connect_and_run(self):
        if not ALPACA_KEY_ID or not ALPACA_SECRET_KEY:
            raise RuntimeError("Missing ALPACA_KEY_ID/ALPACA_SECRET_KEY for data stream.")
        self._running = True

        while self._running:
            try:
                async with websockets.connect(ALPACA_DATA_WS, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    # Authenticate
                    await ws.send(json.dumps({"action": "auth", "key": ALPACA_KEY_ID, "secret": ALPACA_SECRET_KEY}))
                    auth_resp = await ws.recv()
                    # Subscribe (bars by default; you can change to trades/quotes)
                    if self.symbols:
                        await ws.send(json.dumps({"action": "subscribe", "bars": sorted(self.symbols)}))

                    # Loop
                    while self._running:
                        raw = await ws.recv()
                        data = json.loads(raw)
                        if isinstance(data, list):
                            for ev in data:
                                await self._emit(ev)
                        else:
                            await self._emit(data)
            except asyncio.CancelledError:
                break
            except Exception:
                # backoff and retry
                await asyncio.sleep(1)

    async def stop(self):
        self._running = False
        try:
            if self._ws:
                await self._ws.close()
        except Exception:
            pass