"""Structured, credential-safe logging (console + rotating file)."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
from pathlib import Path

_URL_CRED = re.compile(r"(\w+://)[^/@\s:]+:[^/@\s]+@")
_SECRET_KV = re.compile(r"(?i)\b(password|passwd|secret|token|authorization)\b\s*[=:]\s*\S+")


class RedactFilter(logging.Filter):
    """Never let camera-URL credentials, passwords or tokens reach a log file."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        red = _SECRET_KV.sub(lambda m: m.group(1) + "=***", _URL_CRED.sub(r"\1***@", msg))
        if red != msg:
            record.msg, record.args = red, ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname,
                "logger": record.name, "message": record.getMessage()}
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup_logging(log_dir: Path | None = None, level: str | None = None) -> None:
    root = logging.getLogger()
    if getattr(root, "_sa_configured", False):
        return
    level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    fmt: logging.Formatter = JsonFormatter() if os.getenv("LOG_FORMAT", "text") == "json" else \
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        log_dir = Path(log_dir or os.getenv("LOG_DIR", Path(__file__).resolve().parents[2] / "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(log_dir / "attendance.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    except OSError:
        logging.getLogger(__name__).warning("log directory is not writable; logging to console only")
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactFilter())
        root.addHandler(h)
    root.setLevel(level)
    for noisy in ("urllib3", "PIL", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._sa_configured = True  # type: ignore[attr-defined]
