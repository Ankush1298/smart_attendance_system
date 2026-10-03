"""Environment-backed settings for the structured backend."""
from dataclasses import dataclass
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class Settings:
    db_backend: str = "mysql"
    mysql_host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_database: str = os.getenv("MYSQL_DATABASE", "smart_attendance")
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "")
    api_host: str = os.getenv("API_HOST", "127.0.0.1")
    api_port: int = int(os.getenv("API_PORT", "8000"))

settings = Settings()
