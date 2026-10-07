"""Stateless signed bearer tokens + login throttling for the admin API."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Any, Dict, Optional

TOKEN_TTL_SEC = int(os.getenv("TOKEN_TTL_SEC", str(8 * 3600)))
_SECRET = (os.getenv("APP_SECRET_KEY") or "").encode() or secrets.token_bytes(32)   # random per process if unset


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue(sub: str, name: str, role: str, ttl: int = TOKEN_TTL_SEC) -> Dict[str, Any]:
    exp = int(time.time()) + ttl
    body = _b64(json.dumps({"sub": sub, "name": name, "role": role, "exp": exp}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_SECRET, body.encode(), hashlib.sha256).digest())
    return {"token": f"{body}.{sig}", "expires_at": exp}


def verify(token: str) -> Optional[Dict[str, Any]]:
    try:
        body, sig = token.split(".", 1)
        good = _b64(hmac.new(_SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        data = json.loads(_unb64(body))
        return data if data.get("exp", 0) > time.time() else None
    except Exception:
        return None


class LoginThrottle:
    """Lock a (client, id) pair after ``limit`` failures for ``window`` seconds."""

    def __init__(self, limit: int = 5, window: int = 300):
        self.limit, self.window = limit, window
        self._fails: Dict[str, list] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> int:
        now = time.time()
        with self._lock:
            hits = [t for t in self._fails.get(key, []) if now - t < self.window]
            self._fails[key] = hits
            return int(self.window - (now - hits[0])) if len(hits) >= self.limit else 0

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
