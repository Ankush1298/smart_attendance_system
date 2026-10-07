"""Start the real server (uvicorn subprocess) against a given database."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(env: dict, port: int, log_path: Path) -> subprocess.Popen:
    e = {**os.environ, **env, "API_HOST": "127.0.0.1", "API_PORT": str(port), "PYTHONPATH": str(ROOT), "LOG_DIR": str(log_path.parent)}
    log = open(log_path, "ab")
    p = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=str(ROOT), env=e, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 60
    while time.time() < deadline:
        if p.poll() is not None:
            raise RuntimeError(f"server exited early; see {log_path}")
        try:
            urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1).read()
            return p
        except Exception:
            time.sleep(0.3)
    p.kill()
    raise RuntimeError("server did not start in 60 s")


def stop(p: subprocess.Popen) -> None:
    p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()
