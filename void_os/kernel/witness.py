"""Append-only, hash-chained audit log.

Every record includes the sha256 of the previous record, so anyone can
verify the log hasn't been edited or reordered after the fact by
replaying the chain (see `verify()`).
"""

import json
import hashlib
import threading
from datetime import datetime, timezone
from .paths import DATA
from .security import redact

LOG_PATH = DATA / "logs" / "witness.jsonl"
_lock = threading.Lock()


def _last_hash() -> str:
    if not LOG_PATH.exists():
        return "0" * 64
    last_line = None
    with LOG_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last_line = line
    if not last_line:
        return "0" * 64
    return json.loads(last_line).get("sha256", "0" * 64)


def log(event: str, detail: dict) -> dict:
    """Append one witness record and return it."""
    with _lock:
        prev_hash = _last_hash()
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "detail": redact(detail),
            "prev_hash": prev_hash,
        }
        raw = json.dumps(record, sort_keys=True)
        record["sha256"] = hashlib.sha256(raw.encode()).hexdigest()
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return record


def verify() -> tuple[bool, str]:
    """Replay the chain and confirm no record was altered or removed."""
    if not LOG_PATH.exists():
        return True, "No log yet."
    expected_prev = "0" * 64
    with LOG_PATH.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("prev_hash") != expected_prev:
                return False, f"Chain break at record {i}."
            claimed = rec["sha256"]
            raw = json.dumps(
                {k: v for k, v in rec.items() if k != "sha256"}, sort_keys=True
            )
            if hashlib.sha256(raw.encode()).hexdigest() != claimed:
                return False, f"Hash mismatch at record {i}."
            expected_prev = claimed
    return True, "Chain intact."
