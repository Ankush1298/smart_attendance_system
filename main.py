from pathlib import Path
from backend.core.db import DatabaseManager
from backend.core.face_core import FaceEngine
from backend.core.session_logic import SessionLogic
from register import run_registration_server_in_thread
from backend.core.gui_app import SmartAttendanceApp
import threading

def start_engine(engine: FaceEngine):
    engine.load_async()

if __name__ == "__main__":
    BASE_DIR = Path(__file__).parent
    MODELS_DIR = BASE_DIR / "models"
    DB_PATH = BASE_DIR  # DatabaseManager uses MySQL; retained only for API compatibility.
    
    MODELS_DIR.mkdir(exist_ok=True)
    
    db = DatabaseManager(DB_PATH)
    engine = FaceEngine(MODELS_DIR)
    
    # Load face engine in background
    threading.Thread(target=start_engine, args=(engine,), daemon=True).start()
    
    # Session orchestrator handles the core logic and timetables
    session_logic = SessionLogic(db, engine)
    session_logic.start()

    # Fast, secure KYC Registration Server (Student & Faculty Portals)
    run_registration_server_in_thread(db, port=5050, ssl=True)

    # Main UI loop
    app = SmartAttendanceApp(db, session_logic, port=5050)
    app.mainloop()

    # Cleanup when window closed
    session_logic.stop()
