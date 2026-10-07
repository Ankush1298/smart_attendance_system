#!/usr/bin/env python3
"""Start Smart Attendance: web UI + REST API + automatic attendance scheduler (one process).

    python server.py            # http://127.0.0.1:8000
"""
import uvicorn

from backend.config.settings import settings
from backend.core.logging_setup import setup_logging


def main() -> None:
    setup_logging()
    uvicorn.run("backend.api.app:app", host=settings.api_host, port=settings.api_port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
