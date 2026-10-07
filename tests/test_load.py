"""Load / stability tests against the real server process (real MySQL, scheduler running)."""
import statistics
import threading
import time

import httpx
import mysql.connector
import pytest

from tests.conftest import TEST_HOST, TEST_PASSWORD, TEST_PORT, TEST_USER
from tests.test_ui_browser import ADMIN_PW, live_server  # noqa: F401  (shared fixture)

ENDPOINTS = ["/api/dashboard", "/api/readiness", "/api/live-sessions", "/api/cameras", "/api/timetable", "/api/users", "/api/sessions", "/api/rooms"]


@pytest.fixture()
def client(live_server):
    c = httpx.Client(base_url=live_server["base"], timeout=30)
    tok = c.post("/api/auth/login", json={"id": "ADMIN", "password": ADMIN_PW}).json()["token"]
    c.headers["Authorization"] = "Bearer " + tok
    yield c
    c.close()


def hammer(base, token, paths, n, out):
    with httpx.Client(base_url=base, headers={"Authorization": "Bearer " + token}, timeout=30) as c:
        for i in range(n):
            p = paths[i % len(paths)]
            t0 = time.perf_counter()
            try:
                r = c.get(p)
                out.append((p, r.status_code, time.perf_counter() - t0))
            except Exception as exc:  # pragma: no cover
                out.append((p, repr(exc), time.perf_counter() - t0))


def test_many_concurrent_clients_no_errors_and_bounded_latency(live_server, client):
    token = client.headers["Authorization"][7:]
    out = []
    threads = [threading.Thread(target=hammer, args=(live_server["base"], token, ENDPOINTS, 60, out)) for _ in range(20)]
    t0 = time.time()
    [t.start() for t in threads]; [t.join() for t in threads]
    assert len(out) == 1200
    bad = [o for o in out if o[1] != 200]
    assert bad == [], bad[:5]
    lat = sorted(o[2] for o in out)
    p95 = lat[int(len(lat) * 0.95)]
    print(f"\n1200 requests / 20 clients in {time.time() - t0:.1f}s; median {statistics.median(lat) * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms, max {lat[-1] * 1000:.0f} ms")
    assert p95 < 3.0


def test_rapid_refresh_of_one_page_leaves_server_healthy(live_server, client):
    for _ in range(150):
        assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/readiness").json()["checks"]


def test_parallel_camera_tests_and_previews_do_not_leak_or_crash(live_server, client):
    cams = client.get("/api/cameras").json()["items"]
    file_cams = [c for c in cams if c["camera_type"] == "file"]
    assert file_cams
    results = []

    def go(cid):
        with httpx.Client(base_url=live_server["base"], headers=client.headers, timeout=60) as c:
            results.append(c.post("/api/cameras/test", json={"camera_id": cid}).json().get("ok"))
            results.append(c.get(f"/api/cameras/{cid}/snapshot").status_code)
    ts = [threading.Thread(target=go, args=(file_cams[i % len(file_cams)]["camera_id"],)) for i in range(12)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count(True) == 12 and results.count(200) == 12
    assert client.get("/api/health").status_code == 200


def test_database_connections_killed_server_side_are_transparently_replaced(live_server, client):
    admin = mysql.connector.connect(host=TEST_HOST, port=TEST_PORT, user=TEST_USER, password=TEST_PASSWORD, autocommit=True)
    cur = admin.cursor()
    for round_ in range(3):
        cur.execute("SELECT id FROM information_schema.processlist WHERE db=%s AND id<>CONNECTION_ID()", (live_server["env"]["MYSQL_DATABASE"],))
        for (pid,) in cur.fetchall():
            cur.execute(f"KILL {int(pid)}")
        time.sleep(0.3)
        for p in ENDPOINTS:
            r = client.get(p)
            assert r.status_code == 200, (round_, p, r.status_code, r.text[:200])
    cur.close(); admin.close()
    time.sleep(6)                                                                   # let the scheduler run a few cycles too
    rd = client.get("/api/readiness").json()
    assert {c["id"]: c["status"] for c in rd["checks"]}["engine"] == "READY"
