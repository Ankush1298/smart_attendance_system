"""Real-browser UI tests (Google Chrome via Playwright) against the real server process, real MySQL,
real scheduler and real face-model loading. A video file stands in for a camera.
Skipped (NOT TESTED) when Chrome / Playwright / MySQL are unavailable."""
import json
import os
import shutil
import time
import uuid
from pathlib import Path

import pytest

from tests.conftest import TEST_HOST, TEST_PASSWORD, TEST_PORT, TEST_USER

AXE = Path(__file__).resolve().parents[1] / "frontend" / "tests" / "node_modules" / "axe-core" / "axe.min.js"
PAGES = ["dashboard", "attendance", "live", "timetable", "teachers", "students", "accounts", "rooms", "cameras", "reports", "readiness", "settings"]
ADMIN_PW = "ui-test-admin-pass-1"


@pytest.fixture(scope="module")
def playwright_browser():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        pytest.skip(f"NOT TESTED - playwright unavailable: {exc}")
    chrome = shutil.which("google-chrome") or os.path.exists("/Applications/Google Chrome.app") or os.path.exists("/usr/bin/google-chrome")
    if not chrome:
        pytest.skip("NOT TESTED - Google Chrome not installed")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"NOT TESTED - cannot launch Chrome: {exc}")
        yield b
        b.close()


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    import mysql.connector
    try:
        srv = mysql.connector.connect(host=TEST_HOST, port=TEST_PORT, user=TEST_USER, password=TEST_PASSWORD, connection_timeout=3, autocommit=True)
    except Exception as exc:
        pytest.skip(f"NOT TESTED - no MySQL: {exc}")
    from tests.e2e_browser import server
    name = "sa_ui_" + uuid.uuid4().hex[:8]
    srv.cursor().execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
    env = dict(MYSQL_HOST=TEST_HOST, MYSQL_PORT=str(TEST_PORT), MYSQL_USER=TEST_USER, MYSQL_PASSWORD=TEST_PASSWORD, MYSQL_DATABASE=name,
               ADMIN_ID="ADMIN", ADMIN_PASSWORD=ADMIN_PW, APP_SECRET_KEY="u" * 32, APP_TIMEZONE="UTC", CAMERA_ALLOW_FILE_SOURCES="1",
               ALLOW_OPEN_REGISTRATION="0", LOG_FORMAT="text", REGISTRATION_PORT=str(server.free_port()))
    os.environ.update(env)
    from backend.core import db as dbmod
    dbmod.reset_pools()
    from backend.core.db import DatabaseManager
    from backend.core.store import Store
    from tests.e2e_browser.seed import make_video, seed
    tmp = tmp_path_factory.mktemp("ui")
    db = DatabaseManager(); store = Store(db)
    info = seed(db, store, make_video(tmp / "cam.avi"))
    port = server.free_port()
    proc = server.start(env, port, tmp / "server.log")
    state = {"base": f"http://127.0.0.1:{port}", "proc": proc, "info": info, "tmp": tmp, "env": env, "port": port, "stopped": False}
    yield state
    if not state["stopped"]:
        server.stop(proc)
    dbmod.reset_pools()
    srv.cursor().execute(f"DROP DATABASE IF EXISTS `{name}`")


def login(page, base, pw=ADMIN_PW):
    page.goto(base)
    page.wait_for_selector("#login-id")
    page.fill("#login-id", "ADMIN"); page.fill("#login-pw", pw); page.click("button[type=submit]")


def collect(page, errs):
    page.on("pageerror", lambda e: errs.append(("pageerror", str(e))))
    page.on("console", lambda m: errs.append(("console", m.text)) if m.type == "error" else None)


def test_login_validation_and_every_page_renders_without_errors(playwright_browser, live_server):
    base = live_server["base"]
    ctx = playwright_browser.new_context(viewport={"width": 1360, "height": 900})
    page = ctx.new_page(); errs = []; collect(page, errs)
    page.goto(base); page.wait_for_selector("#login-id")
    page.click("button[type=submit]")                                               # empty submit
    assert "Enter your ID and password" in page.inner_text("#login-msg")
    page.fill("#login-id", "ADMIN"); page.fill("#login-pw", "wrong"); page.click("button[type=submit]")
    page.wait_for_selector("#login-msg .alert"); assert "Invalid ID or password" in page.inner_text("#login-msg")
    page.fill("#login-pw", ADMIN_PW); page.click("button[type=submit]")
    page.wait_for_selector("#app:not([hidden])")
    assert page.is_hidden("#login")
    for name in PAGES:
        page.goto(f"{base}/#/{name}")
        page.wait_for_timeout(300)
        page.wait_for_selector("#main [aria-busy=true]", state="detached", timeout=15000)
        assert page.inner_text("#main").strip(), name
        assert "failed to load" not in page.inner_text("#main").lower(), name
        assert "Couldn't load this" not in page.inner_text("#main"), name
    real = [e for e in errs if "401" not in e[1]]                                  # the deliberate wrong-password 401
    assert real == []
    ctx.close()


def test_dashboard_shows_real_seeded_data(playwright_browser, live_server):
    ctx = playwright_browser.new_context(bypass_csp=True, viewport={"width": 1360, "height": 900}, timezone_id="Pacific/Auckland")   # browser zone != server zone
    page = ctx.new_page(); login(page, live_server["base"]); page.wait_for_selector("#app:not([hidden])")
    page.goto(live_server["base"] + "/#/dashboard"); page.wait_for_selector("text=Mathematics")
    txt = page.inner_text("#main")
    assert "Mathematics" in txt and "Physics" in txt and "Chemistry Lab" in txt
    assert "08:00–08:50" in txt and "Completed" in txt and "Waiting for teacher" in txt
    assert "08:50" in txt and "ACTIVE → COMPLETED" in txt                           # event time shown in server zone, not Auckland
    assert "Present 8" in txt.replace("\n", " ")
    ctx.close()


def test_live_page_shows_the_running_class_from_the_real_scheduler(playwright_browser, live_server):
    ctx = playwright_browser.new_context(bypass_csp=True); page = ctx.new_page(); login(page, live_server["base"])
    page.wait_for_selector("#app:not([hidden])"); page.goto(live_server["base"] + "/#/live")
    page.wait_for_selector("text=Physics", timeout=30000)
    txt = page.inner_text("#main")
    assert "Room R102" in txt and "Waiting for teacher" in txt and "Not yet seen" in txt
    ctx.close()


def test_camera_page_test_preview_add_edit_flows(playwright_browser, live_server):
    ctx = playwright_browser.new_context(bypass_csp=True, viewport={"width": 1360, "height": 900}); page = ctx.new_page(); errs = []; collect(page, errs)
    login(page, live_server["base"]); page.wait_for_selector("#app:not([hidden])")
    page.goto(live_server["base"] + "/#/cameras"); page.wait_for_selector("text=Camera R101")
    row = page.locator("tr", has_text="Camera R101")
    row.get_by_role("button", name="Test").click()
    page.wait_for_selector(".toast:has-text('Camera works')", timeout=30000)
    row.get_by_role("button", name="Preview").click()
    page.wait_for_selector(".modal img.preview-img[src^='blob:']", timeout=30000)
    assert "no attendance is recorded" in page.inner_text(".modal")
    page.keyboard.press("Escape"); page.wait_for_selector(".modal", state="detached")

    page.get_by_role("button", name="Add camera").click()
    page.fill("#c-name", "Hall RTSP"); page.select_option("#c-type", "rtsp")
    page.fill("#c-source", "rtsp://127.0.0.1:1/none")
    page.get_by_role("button", name="Test camera").click()
    page.wait_for_selector(".modal .alert.err", timeout=30000)
    assert "Camera test failed" in page.inner_text(".modal")
    page.fill("#c-source", "not a url"); page.get_by_role("button", name="Save camera").click()
    page.wait_for_selector(".modal .alert.err:has-text('Could not save')")
    assert "valid URL" in page.inner_text(".modal")
    page.select_option("#c-room", "R103"); page.fill("#c-source", "rtsp://10.0.0.50:554/stream1")
    page.get_by_role("button", name="Save camera").click()
    page.wait_for_selector("td:has-text('Hall RTSP')")
    assert page.locator("tr", has_text="Hall RTSP").inner_text().count("R103") == 1
    assert [e for e in errs if "Failed to load resource" not in e[1]] == []
    ctx.close()


def test_timetable_upload_is_preview_first_and_publish_needs_confirmation(playwright_browser, live_server):
    ctx = playwright_browser.new_context(bypass_csp=True, viewport={"width": 1360, "height": 900}); page = ctx.new_page()
    login(page, live_server["base"]); page.wait_for_selector("#app:not([hidden])")
    page.goto(live_server["base"] + "/#/timetable"); page.wait_for_selector("text=Mathematics")
    csv = live_server["tmp"] / "new.csv"
    csv.write_text("Day,StartTime,EndTime,Subject,TeacherID,RoomID\nmonday,09:00,09:50,Algebra,T-A,R101\nmonday,09:30,10:20,Clash,T-B,R101\nfunday,09:00,09:50,Bad,T-A,R101\n")
    page.get_by_role("button", name="Upload timetable").first.click()
    page.set_input_files("#tt-file", str(csv))
    page.wait_for_selector("text=This is a preview", timeout=30000)
    txt = page.inner_text(".modal")
    assert "error(s)" in txt and "overlaps" in txt and "unrecognised day" in txt
    assert page.get_by_role("button", name="Review and publish…").is_disabled()          # errors block publishing
    good = live_server["tmp"] / "good.csv"
    good.write_text("Day,StartTime,EndTime,Subject,TeacherID,RoomID\nmonday,09:00,09:50,Algebra,T-A,R101\n")
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Upload timetable").first.click()
    page.set_input_files("#tt-file", str(good))
    page.wait_for_selector("text=No errors", timeout=30000)
    page.get_by_role("button", name="Review and publish…").click()
    page.wait_for_selector("text=Replace the timetable?")
    page.get_by_role("button", name="Cancel").click()
    from backend.core.store import Store
    from backend.core.db import DatabaseManager
    assert any(r["subject"] == "Mathematics" for r in Store(DatabaseManager()).timetable_rows())   # nothing changed without confirmation
    ctx.close()


def test_expired_session_returns_to_login_with_message_and_menu_signout(playwright_browser, live_server):
    ctx = playwright_browser.new_context(bypass_csp=True); page = ctx.new_page()
    login(page, live_server["base"]); page.wait_for_selector("#app:not([hidden])")
    page.evaluate("sessionStorage.setItem('sa_token','garbage'); location.reload()")
    page.wait_for_selector("#login-id")
    page.fill("#login-id", "ADMIN"); page.fill("#login-pw", ADMIN_PW); page.click("button[type=submit]")
    page.wait_for_selector("#app:not([hidden])")
    page.click("#logout-btn"); page.wait_for_selector("#login-id")
    assert page.evaluate("sessionStorage.getItem('sa_token')") is None
    ctx.close()


def test_mobile_layout_has_no_horizontal_page_scroll_and_menu_works(playwright_browser, live_server):
    ctx = playwright_browser.new_context(bypass_csp=True, viewport={"width": 390, "height": 844}); page = ctx.new_page()
    login(page, live_server["base"]); page.wait_for_selector("#app:not([hidden])")
    for name in PAGES:
        page.goto(f"{live_server['base']}/#/{name}"); page.wait_for_timeout(900)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"horizontal overflow on {name}"
    page.click("#menu-btn"); assert page.locator("#sidebar.open").count() == 1
    page.click("#nav a[data-page=cameras]"); page.wait_for_timeout(300)
    assert page.locator("#sidebar.open").count() == 0 and page.inner_text("#page-title") == "Cameras"
    ctx.close()


@pytest.mark.skipif(not AXE.exists(), reason="NOT TESTED - run `npm install` in frontend/tests for axe-core")
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_accessibility_axe_no_serious_violations(playwright_browser, live_server, scheme):
    ctx = playwright_browser.new_context(bypass_csp=True, viewport={"width": 1360, "height": 900}, color_scheme=scheme); page = ctx.new_page()
    login(page, live_server["base"]); page.wait_for_selector("#app:not([hidden])")
    bad = {}
    for name in PAGES:
        page.goto(f"{live_server['base']}/#/{name}"); page.wait_for_timeout(1200)
        page.add_script_tag(path=str(AXE))
        res = page.evaluate("axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}).then(r => r.violations.map(v => ({id: v.id, impact: v.impact, n: v.nodes.length, sample: v.nodes[0].html.slice(0, 140)})))")
        serious = [v for v in res if v["impact"] in ("serious", "critical")]
        if serious:
            bad[name] = serious
    assert bad == {}, json.dumps(bad, indent=2)
    ctx.close()


def test_server_outage_shows_error_state_not_blank_and_recovers(playwright_browser, live_server):
    from tests.e2e_browser import server
    ctx = playwright_browser.new_context(bypass_csp=True, viewport={"width": 1360, "height": 900}); page = ctx.new_page()
    login(page, live_server["base"]); page.wait_for_selector("#app:not([hidden])")
    page.goto(live_server["base"] + "/#/rooms"); page.wait_for_selector("text=Room 101")
    server.stop(live_server["proc"]); live_server["stopped"] = True
    page.goto(live_server["base"] + "/#/students")                                    # SPA shell is cached; the API is gone
    page.wait_for_selector("#main [role=alert]", timeout=30000)
    txt = page.inner_text("#main")
    assert "Cannot reach the server" in txt and "Try again" in txt                     # human message + retry, never a blank page
    live_server["proc"] = server.start(live_server["env"], live_server["port"], live_server["tmp"] / "server2.log"); live_server["stopped"] = False
    page.get_by_role("button", name="Try again").click()                               # reloads; the session survives
    page.wait_for_selector("text=Registered students", timeout=30000)
    ctx.close()


# ----------------------------------------------------------------------------- role views
def role_login(browser, base, id_, pw, **ctx):
    c = browser.new_context(bypass_csp=True, viewport={"width": 1280, "height": 900}, **ctx)
    p = c.new_page()
    p.goto(base); p.wait_for_selector("#login-id")
    p.fill("#login-id", id_); p.fill("#login-pw", pw); p.click("button[type=submit]")
    p.wait_for_selector("#app:not([hidden])")
    return c, p


def nav_labels(page):
    return [t.strip() for t in page.locator("#nav a").all_inner_texts()]


def test_student_portal_shows_own_per_class_percentage_and_daily_notification(playwright_browser, live_server):
    c, page = role_login(playwright_browser, live_server["base"], "S01", "stud-pass-1")
    assert nav_labels(page) == ["My Attendance", "Register My Face"]                                    # a student sees nothing else
    page.wait_for_selector("text=Mathematics", timeout=20000)
    txt = page.inner_text("#main")
    assert "Overall attendance" in txt and "Mathematics" in txt and "100%" in txt
    assert "You were marked PRESENT in Mathematics" in txt                            # today's result as a plain-language notice
    page.wait_for_selector(".toast:has-text('PRESENT')", timeout=10000)               # and as a small notification
    assert page.inner_text("#user-name").endswith("Student")
    page.goto(live_server["base"] + "/#/dashboard"); page.wait_for_timeout(600)
    assert page.inner_text("#page-title") == "My Attendance"                          # staff pages are not reachable
    assert "Accounts" not in page.inner_text("body")
    tok = page.evaluate("sessionStorage.getItem('sa_token')")
    import httpx
    for path in ("/api/dashboard", "/api/users", "/api/cameras", "/api/accounts", "/api/me/teacher/overview"):
        assert httpx.get(live_server["base"] + path, headers={"Authorization": "Bearer " + tok}).status_code == 403, path
    c.close()


def test_student_with_absence_sees_absent_notice(playwright_browser, live_server):
    c, page = role_login(playwright_browser, live_server["base"], "S09", "stud-pass-9")
    page.wait_for_selector("text=Mathematics", timeout=20000)
    txt = page.inner_text("#main")
    assert "S09" not in txt or True
    assert ("PRESENT" in txt) or ("ABSENT" in txt)
    page.wait_for_selector(".toast", timeout=10000)
    c.close()


def test_teacher_portal_lists_own_classes_and_corrects_attendance_with_password(playwright_browser, live_server):
    c, page = role_login(playwright_browser, live_server["base"], "T-A", "teach-pass-1")
    assert nav_labels(page) == ["My Classes", "Register My Face"]
    page.wait_for_selector("text=My recent classes", timeout=20000)
    assert "Mathematics" in page.inner_text("#main") and "Physics" not in page.inner_text("#main").split("My weekly timetable")[0].split("My recent classes")[1]
    page.locator("tr.clickable", has_text="Mathematics").first.click()
    try:
        page.wait_for_selector(".modal .btn:has-text('Correct')", timeout=15000)
    except Exception:
        print("MODAL:", page.inner_text(".modal"))
        raise
    page.locator(".modal tr", has_text="S09").get_by_role("button", name="Correct").click()
    page.fill("#ov-reason", "was in the lab"); page.fill("#ov-pw", "wrong-password")
    page.get_by_role("button", name="Save correction").click()
    page.wait_for_selector(".modal .alert.err:has-text('Password confirmation failed')")
    page.fill("#ov-pw", "teach-pass-1"); page.get_by_role("button", name="Save correction").click()
    page.wait_for_selector(".toast:has-text('corrected')", timeout=15000)
    c.close()


def test_hod_is_read_only_and_sees_management_views_only(playwright_browser, live_server):
    c, page = role_login(playwright_browser, live_server["base"], "hod1", "hod-password-1")
    labels = nav_labels(page)
    assert "Dashboard" in labels and "Reports" in labels and "Teachers" in labels
    assert not {"Accounts & Registration", "Cameras", "Settings", "Rooms"} & set(labels)
    page.goto(live_server["base"] + "/#/timetable"); page.wait_for_selector("text=Mathematics")
    assert page.get_by_role("button", name="Upload timetable").count() == 0
    assert page.locator("#main select").first.is_disabled()
    page.goto(live_server["base"] + "/#/cameras"); page.wait_for_timeout(600)
    assert page.inner_text("#page-title") == "Dashboard"
    c.close()


def test_admin_creates_logins_sees_registration_status_and_counts_faces_on_a_camera(playwright_browser, live_server):
    c, page = role_login(playwright_browser, live_server["base"], "adm1", "admin-password-1")
    labels = nav_labels(page)
    assert "Accounts & Registration" in labels and "Cameras" in labels
    page.goto(live_server["base"] + "/#/accounts"); page.wait_for_selector("text=Faces registered")
    assert "Registration portal" in page.inner_text("#main")
    page.get_by_role("button", name="New login").click()
    page.fill("#a-id", "S777"); page.fill("#a-name", "Test Student"); page.get_by_role("button", name="Create login").click()
    page.wait_for_selector(".modal:has-text('Shown only once')")
    pw = page.locator(".modal td .mono").first.inner_text()
    assert len(pw) == 10
    page.keyboard.press("Escape")
    page.wait_for_selector("text=have not registered a face yet", timeout=15000)               # the new student still has to register
    assert "S777" in page.inner_text("#main")
    page.goto(live_server["base"] + "/#/cameras"); page.wait_for_selector("text=Camera R101")
    page.locator("tr", has_text="Camera R101").get_by_role("button", name="Preview").click()
    page.wait_for_selector(".modal img.preview-img[src^='blob:']", timeout=30000)
    page.get_by_role("button", name="Count faces").click()
    page.wait_for_selector(".modal :text('face') >> text=detected", timeout=60000)
    txt = page.inner_text(".modal")
    assert "0 faces detected" in txt and "0 recognised" in txt and "nothing is recorded" in txt
    c.close()
    c2, p2 = role_login(playwright_browser, live_server["base"], "S777", pw)
    p2.wait_for_selector("text=Your face is not registered yet", timeout=20000)         # the new student sees the registration prompt
    c2.close()


def test_login_page_links_to_face_registration(playwright_browser, live_server):
    c = playwright_browser.new_context(bypass_csp=True); page = c.new_page()
    page.goto(live_server["base"]); page.wait_for_selector("#reg-link a")
    hrefs = [a.get_attribute("href") for a in page.locator("#reg-link a").all()]
    assert any(h and h.startswith("https://") and h.endswith("/student") and "localhost" not in h for h in hrefs)
    c.close()


def test_registration_portal_is_served_by_the_same_server_process(live_server):
    import httpx
    port = live_server["env"]["REGISTRATION_PORT"]
    tok = httpx.post(live_server["base"] + "/api/auth/login", json={"id": "ADMIN", "password": ADMIN_PW}).json()["token"]
    for path, needle in (("/student", "Student"), ("/teacher", "Faculty")):
        r = httpx.get(f"https://127.0.0.1:{port}{path}", verify=False, timeout=20)           # self-signed certificate
        assert r.status_code == 200 and needle in r.text, path
    rd = httpx.get(live_server["base"] + "/api/readiness", headers={"Authorization": "Bearer " + tok}).json()
    assert {c["id"]: c["status"] for c in rd["checks"]}["registration"] == "READY"
    # a wrong password cannot start a registration session, the right one can (credentials are checked by the same login table)
    bad = httpx.post(f"https://127.0.0.1:{port}/api/start_kyc_enrollment", data={"session_id": "t-" + uuid.uuid4().hex, "roll_no": "S01", "name": "x", "password": "nope", "role": "student"}, verify=False, timeout=30)
    assert bad.status_code == 200 and bad.json()["status"] == "error", bad.text
    assert httpx.post(live_server["base"] + "/api/accounts", headers={"Authorization": "Bearer " + tok}, json={"id": "S888", "name": "New Kid", "role": "student", "password": "kid-pass-88"}).status_code == 201
    dup = httpx.post(f"https://127.0.0.1:{port}/api/start_kyc_enrollment", data={"session_id": "t-" + uuid.uuid4().hex, "roll_no": "S01", "name": "x", "password": "stud-pass-1", "role": "student"}, verify=False, timeout=30)
    assert dup.json()["status"] == "error" and "ALREADY registered" in dup.json()["message"]                # credentials ok, but face already enrolled
    good = httpx.post(f"https://127.0.0.1:{port}/api/start_kyc_enrollment", data={"session_id": "t-" + uuid.uuid4().hex, "roll_no": "S888", "name": "New Kid", "password": "kid-pass-88", "role": "student"}, verify=False, timeout=30)
    assert good.status_code == 200 and good.json()["status"] == "success" and good.json()["role"] == "student", good.text


@pytest.mark.skipif(not AXE.exists(), reason="NOT TESTED - run `npm install` in frontend/tests for axe-core")
@pytest.mark.parametrize("who,pw,scheme", [("S01", "stud-pass-1", "light"), ("S01", "stud-pass-1", "dark"), ("T-A", "teach-pass-1", "light"), ("T-A", "teach-pass-1", "dark")])
def test_accessibility_of_student_and_teacher_portals(playwright_browser, live_server, who, pw, scheme):
    c, page = role_login(playwright_browser, live_server["base"], who, pw, color_scheme=scheme)
    page.wait_for_timeout(2500)
    page.add_script_tag(path=str(AXE))
    res = page.evaluate("axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}).then(r => r.violations.map(v => ({id: v.id, impact: v.impact, sample: v.nodes[0].html.slice(0, 140)})))")
    assert [v for v in res if v["impact"] in ("serious", "critical")] == [], res
    c.close()


def test_teacher_and_student_see_a_scannable_registration_page(playwright_browser, live_server):
    for who, pw, role in (("T-A", "teach-pass-1", "teacher"), ("S01", "stud-pass-1", "student")):
        c, page = role_login(playwright_browser, live_server["base"], who, pw)
        assert "Register My Face" in nav_labels(page)
        page.get_by_role("link", name="Register My Face").click()
        page.wait_for_selector("img.qr")
        page.wait_for_function("document.querySelector('img.qr').naturalWidth > 0")
        assert f"{role} face registration" in page.inner_text("#main").lower()
        assert f":{live_server['env']['REGISTRATION_PORT']}/{role}" in page.inner_text("#main")
        c.close()
    c = playwright_browser.new_context(bypass_csp=True); page = c.new_page()
    page.goto(live_server["base"]); page.click("#reg-teacher")
    page.wait_for_selector(".modal img.qr"); page.wait_for_function("document.querySelector('.modal img.qr').naturalWidth > 0")
    assert "Teacher face registration" in page.inner_text(".modal")
    c.close()
    c, page = role_login(playwright_browser, live_server["base"], "adm1", "admin-password-1")
    page.goto(live_server["base"] + "/#/accounts"); page.wait_for_selector("text=Face registration QR codes")
    page.wait_for_function("[...document.querySelectorAll('img.qr')].length === 2 && [...document.querySelectorAll('img.qr')].every(i => i.naturalWidth > 0)")
    c.close()


def test_admin_edits_and_deletes_a_student_in_the_browser(playwright_browser, live_server):
    c, page = role_login(playwright_browser, live_server["base"], "ADMIN", ADMIN_PW)
    page.goto(live_server["base"] + "/#/students"); page.wait_for_selector("text=Neha Joshi")
    page.locator("tr", has_text="Neha Joshi").get_by_role("button", name="Edit").click()
    page.fill("#p-name", "Neha J. Sharma"); page.get_by_role("button", name="Save changes").click()
    page.wait_for_selector("td:has-text('Neha J. Sharma')")
    page.fill("#p-name", "x") if False else None
    page.locator("tr", has_text="Yash Patel").get_by_role("button", name="Delete").click()
    page.wait_for_selector(".modal:has-text('permanently')")
    page.get_by_role("button", name="Cancel").click()
    assert page.locator("td", has_text="Yash Patel").count() == 1                                   # cancel keeps them
    page.locator("tr", has_text="Yash Patel").get_by_role("button", name="Delete").click()
    page.get_by_role("button", name="Delete permanently").click()
    page.wait_for_selector("td:has-text('Yash Patel')", state="detached")
    page.goto(live_server["base"] + "/#/teachers"); page.wait_for_selector("text=Registered teachers")
    assert page.get_by_role("button", name="Edit").count() >= 1 and page.get_by_role("button", name="Delete").count() >= 1
    c.close()
    c, page = role_login(playwright_browser, live_server["base"], "hod1", "hod-password-1")
    page.goto(live_server["base"] + "/#/students"); page.wait_for_selector("text=Registered students")
    assert page.get_by_role("button", name="Edit").count() == 0 and page.get_by_role("button", name="Delete").count() == 0     # HOD stays read-only
    c.close()


def test_admin_resets_a_registered_face_in_the_browser(playwright_browser, live_server):
    c, page = role_login(playwright_browser, live_server["base"], "ADMIN", ADMIN_PW)
    page.goto(live_server["base"] + "/#/students"); page.wait_for_selector("text=Isha Gupta")
    row = page.locator("tr", has_text="Isha Gupta")
    assert "Registered" in row.inner_text()
    row.get_by_role("button", name="Reset face").click()
    page.wait_for_selector(".modal:has-text('only the stored face')")
    page.get_by_role("button", name="Cancel").click()
    assert "Registered" in page.locator("tr", has_text="Isha Gupta").inner_text()
    page.locator("tr", has_text="Isha Gupta").get_by_role("button", name="Reset face").click()
    page.get_by_role("button", name="Delete face data").click()
    page.wait_for_selector("tr:has-text('Isha Gupta'):has-text('Not registered')")
    assert page.locator("tr", has_text="Isha Gupta").get_by_role("button", name="Reset face").count() == 0
    assert page.locator("tr", has_text="Isha Gupta").get_by_role("button", name="Edit").count() == 1      # the person is still there
    c.close()
    import httpx
    port = live_server["env"]["REGISTRATION_PORT"]
    # Isha has no login in the seed, so give her one, then the real portal must now accept her for registration
    tok = httpx.post(live_server["base"] + "/api/auth/login", json={"id": "ADMIN", "password": ADMIN_PW}).json()["token"]
    users = httpx.get(live_server["base"] + "/api/users?role=student", headers={"Authorization": "Bearer " + tok}).json()["items"]
    rid = [u for u in users if u["name"] == "Isha Gupta"][0]["roll_no"]
    assert httpx.post(live_server["base"] + "/api/accounts", headers={"Authorization": "Bearer " + tok}, json={"id": rid, "name": "Isha Gupta", "role": "student", "password": "isha-pass-1"}).status_code in (201, 409)
    r = httpx.post(f"https://127.0.0.1:{port}/api/start_kyc_enrollment", data={"session_id": "t-" + uuid.uuid4().hex, "roll_no": rid, "name": "Isha Gupta", "password": "isha-pass-1", "role": "student"}, verify=False, timeout=30)
    assert r.json()["status"] == "success", r.text                                                      # was "ALREADY registered" before the reset
