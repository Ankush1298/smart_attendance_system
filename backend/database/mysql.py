"""MySQL connection layer.

Production MySQL connection layer for the Smart Attendance application.
All persistent application data is stored in MySQL. Set MYSQL_* variables in .env.
"""
from contextlib import contextmanager
from typing import Any, Iterable

try:
    import mysql.connector
except ImportError:
    mysql = None
else:
    mysql = mysql.connector

from backend.config.settings import settings

class MySQLDatabase:
    def __init__(self):
        if mysql is None:
            raise RuntimeError("mysql-connector-python is not installed. Run: pip install -r requirements-mysql.txt")

    def connect(self):
        return mysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database,
            autocommit=False,
        )

    @contextmanager
    def cursor(self):
        con = self.connect()
        cur = con.cursor(dictionary=True)
        try:
            yield con, cur
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            cur.close(); con.close()

    def fetch_all(self, sql: str, params: Iterable[Any] = ()):
        with self.cursor() as (_, cur):
            cur.execute(sql, tuple(params))
            return cur.fetchall()

    def fetch_one(self, sql: str, params: Iterable[Any] = ()):
        with self.cursor() as (_, cur):
            cur.execute(sql, tuple(params))
            return cur.fetchone()
