import json
from datetime import datetime
from typing import Dict, Any

class Auditor:
    def __init__(self, path: str = "bot_audit.log"):
        self.path = path

    def log(self, event: str, payload: Dict[str, Any]):
        rec = {"ts": datetime.utcnow().isoformat() + "Z", "event": event, "payload": payload}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")