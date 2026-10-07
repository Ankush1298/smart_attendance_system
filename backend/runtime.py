"""Process-wide wiring: database, camera manager, face engine and the attendance scheduler."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from backend.config.settings import settings
from backend.core.camera_manager import CameraManager
from backend.core.db import DatabaseManager
from backend.core.session_logic import SessionLogic
from backend.core.store import Store

log = logging.getLogger("smart_attendance.runtime")


@dataclass
class Runtime:
    db: DatabaseManager
    store: Store
    cameras: CameraManager
    logic: SessionLogic
    engine: object = None
    started_at: float = 0.0

    def stop(self) -> None:
        try:
            self.logic.stop()
        finally:
            self.cameras.stop_all()


def build_runtime(start_scheduler: bool = True, load_face: bool = True) -> Runtime:
    """Create the real runtime. Face-model loading runs in the background so the API is
    available immediately; readiness reports it as WARNING until it finishes."""
    db = DatabaseManager()
    store = Store(db)
    engine = None
    if load_face:
        from backend.core.face_core import FaceEngine
        engine = FaceEngine(settings.models_dir)
        engine.load_async()
    holder: dict = {}
    cameras = CameraManager(on_health=lambda spec, st, err, rec: holder["logic"]._camera_health(spec, st, err, rec))
    logic = SessionLogic(db, engine, cameras=cameras, store=store)
    holder["logic"] = logic
    rt = Runtime(db, store, cameras, logic, engine, time.time())
    if start_scheduler:
        logic.start()
    start_registration_portal(db)
    log.info("runtime started (scheduler=%s, face=%s)", start_scheduler, load_face)
    return rt


def start_registration_portal(db: DatabaseManager) -> None:
    """Students and teachers register their face through the guided KYC portal (HTTPS, port
    REGISTRATION_PORT, default 5050) - phones need HTTPS to open the camera. Same MySQL database."""
    import os
    if os.getenv("DISABLE_REGISTRATION", "0") == "1":
        log.info("registration portal disabled (DISABLE_REGISTRATION=1)")
        return
    try:
        from register import run_registration_server_in_thread
        run_registration_server_in_thread(db, port=int(os.getenv("REGISTRATION_PORT", "5050")), ssl=os.getenv("REGISTRATION_TLS", "1") == "1")
        log.info("registration portal started on port %s", os.getenv("REGISTRATION_PORT", "5050"))
    except Exception:  # noqa: BLE001 - the attendance engine must not depend on the portal
        log.exception("registration portal could not start; people cannot register faces until it does")
