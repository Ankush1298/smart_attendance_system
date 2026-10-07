"""Shared fixtures. DB tests need a real MySQL server (never mocked).

Point them at a throwaway server with TEST_MYSQL_HOST/PORT/USER/PASSWORD (defaults:
127.0.0.1:3306 root/empty). Each test run uses its own freshly created database which is
dropped afterwards. If no server answers, DB tests are reported as skipped with the reason.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_HOST = os.getenv("TEST_MYSQL_HOST", "127.0.0.1")
TEST_PORT = int(os.getenv("TEST_MYSQL_PORT", "3306"))
TEST_USER = os.getenv("TEST_MYSQL_USER", "root")
TEST_PASSWORD = os.getenv("TEST_MYSQL_PASSWORD", "")


def _server():
    import mysql.connector
    return mysql.connector.connect(host=TEST_HOST, port=TEST_PORT, user=TEST_USER,
                                   password=TEST_PASSWORD, connection_timeout=3, autocommit=True)


def _set_env(dbname):
    os.environ.update(MYSQL_HOST=TEST_HOST, MYSQL_PORT=str(TEST_PORT), MYSQL_USER=TEST_USER,
                      MYSQL_PASSWORD=TEST_PASSWORD, MYSQL_DATABASE=dbname,
                      ADMIN_ID="ADMIN", ADMIN_PASSWORD="test-admin-pass-123",
                      APP_SECRET_KEY="test-secret-key-for-tokens-only",
                      ALLOW_OPEN_REGISTRATION="0")


@pytest.fixture()
def fresh_db_name():
    try:
        srv = _server()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"NOT TESTED - no MySQL server at {TEST_HOST}:{TEST_PORT}: {exc}")
    name = "sa_test_" + uuid.uuid4().hex[:10]
    cur = srv.cursor()
    cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    _set_env(name)
    yield name
    from backend.core import db as dbmod
    dbmod.reset_pools()
    cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
    cur.close(); srv.close()


@pytest.fixture()
def db(fresh_db_name):
    from backend.core.db import DatabaseManager
    return DatabaseManager()


@pytest.fixture()
def store(db):
    from backend.core.store import Store
    return Store(db)
