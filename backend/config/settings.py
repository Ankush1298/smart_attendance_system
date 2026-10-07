"""Environment-backed settings (values come from the process environment / .env)."""
from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    db_backend: str = "mysql"
    mysql_host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = _int("MYSQL_PORT", 3306)
    mysql_database: str = os.getenv("MYSQL_DATABASE", "smart_attendance")
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "")
    api_host: str = os.getenv("API_HOST", "127.0.0.1")
    api_port: int = _int("API_PORT", 8000)
    models_dir: Path = Path(os.getenv("MODELS_DIR", str(ROOT_DIR / "models")))
    log_dir: Path = Path(os.getenv("LOG_DIR", str(ROOT_DIR / "logs")))
    frontend_dir: Path = ROOT_DIR / "frontend" / "admin"
    cors_origins: tuple = tuple(o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip())


settings = Settings()
