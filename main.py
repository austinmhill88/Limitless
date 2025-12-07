import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import uvicorn

print("Starting server on http://127.0.0.1:8000 ...")
uvicorn.run("bot.api.server:app", host="127.0.0.1", port=8000, reload=False, log_level="debug")