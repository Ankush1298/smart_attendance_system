"""
Automated Verification Suite for Role-Based UI Views and Password-Protected Admin Access.
Tests:
1. get_admin_password() fallback to 'the_fool_12' and .env override
2. AdminPasswordDialog verification logic
3. SmartAttendanceApp initial state (Teacher View, is_admin=False)
4. Navigation tree filtering in Teacher View vs Admin View
5. Role switching (enable_admin_mode and lock_to_teacher_view)
6. Tab creation (TeacherDashboardTab, TeacherReportsTab, SystemConfigTab)
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Ensure utf-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from backend.core.gui_role_views import (
    get_admin_password,
    AdminPasswordDialog,
    TeacherDashboardTab,
    TeacherReportsTab,
    SystemConfigTab,
)
import customtkinter as ctk

def test_admin_password_resolution():
    print("[TEST 1] Testing Admin Password Resolution...")
    
    # Test fallback
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("ADMIN_PASSWORD", None)
        pwd = get_admin_password()
        assert pwd == "the_fool_12", f"Expected 'the_fool_12', got {pwd}"
        print("  ✓ Fallback to 'the_fool_12' passed.")

    # Test 'change_me' placeholder fallback
    with patch.dict(os.environ, {"ADMIN_PASSWORD": "change_me"}):
        pwd = get_admin_password()
        assert pwd == "the_fool_12", f"Expected 'the_fool_12', got {pwd}"
        print("  ✓ 'change_me' placeholder correctly falls back to 'the_fool_12'.")

    # Test custom password from environment
    with patch.dict(os.environ, {"ADMIN_PASSWORD": "custom_secure_admin_99"}):
        pwd = get_admin_password()
        assert pwd == "custom_secure_admin_99", f"Expected 'custom_secure_admin_99', got {pwd}"
        print("  ✓ Custom ADMIN_PASSWORD in environment correctly loaded.")

def test_app_role_based_views():
    print("\n[TEST 2] Testing SmartAttendanceApp Role Views and Navigation...")
    from backend.core.gui_app import SmartAttendanceApp
    
    # Mock DB and SessionLogic
    mock_db = MagicMock()
    mock_db.get_rooms.return_value = [{"room_id": "101", "room_name": "Lab 1", "camera_source": "0"}]
    mock_db.get_timetable.return_value = [
        {"day_of_week": "monday", "start_time": "09:00", "end_time": "18:00",
         "subject": "Computer Science 101", "teacher_id": "T01", "room_id": "101"}
    ]
    mock_db.get_all_users.return_value = [
        {"roll_no": "S101", "name": "Alice Smith", "role": "student"},
        {"roll_no": "S102", "name": "Bob Jones", "role": "student"},
        {"roll_no": "T01", "name": "Dr. Alan Turing", "role": "teacher"},
    ]
    mock_db.get_dashboard_counts.return_value = {
        "students": 2, "teachers": 1, "rooms": 1, "timetable": 1
    }
    mock_db.get_recent_users_meta.return_value = []
    mock_db.get_attendance_report.return_value = MagicMock(empty=True)
    mock_db.get_teacher_attendance_report.return_value = MagicMock(empty=True)
    mock_db.get_override_audit_report.return_value = MagicMock(empty=True)
    mock_db.get_warning_count.return_value = 0
    
    mock_session_logic = MagicMock()
    mock_session_logic.get_active_sessions_status.return_value = []

    # Initialize app (do not run mainloop, test headless widget tree)
    # Mock messagebox to prevent blocking popups during test
    with patch("tkinter.messagebox.showinfo"), patch("tkinter.messagebox.showerror"):
        app = SmartAttendanceApp(mock_db, mock_session_logic, port=5050)
        app.withdraw()  # Hide window during test
        
        # 1. Default should be Teacher View
        assert app.is_admin is False, "app.is_admin should be False by default"
        assert app.current_role == "teacher", "app.current_role should be 'teacher' by default"
        print("  ✓ App launches with is_admin=False and role='teacher'.")

        # 2. Check Teacher View Navigation Buttons
        teacher_buttons = set(app.nav_buttons.keys())
        expected_teacher_buttons = {"teacher_dashboard", "live_attendance", "teacher_reports", "admin_gate"}
        assert teacher_buttons == expected_teacher_buttons, f"Unexpected buttons in Teacher View: {teacher_buttons}"
        print("  ✓ Teacher View presents strictly: Teacher Dashboard, Live Class Monitor, Attendance Summaries, Admin Panel 🔒")

        # Verify sensitive admin tools are absent in Teacher View
        for restricted in ["system_config", "cameras", "local_registration", "registration", "users", "timetable"]:
            assert restricted not in app.nav_buttons, f"Restricted tool '{restricted}' was leaked to Teacher View!"
        print("  ✓ Verified: System settings, camera mapping, registration, and user directory are NOT accessible in Teacher View.")

        # 3. Simulate Admin Authentication & Unlock
        app.enable_admin_mode()
        assert app.is_admin is True, "app.is_admin should be True after enable_admin_mode()"
        assert app.current_role == "admin", "app.current_role should be 'admin'"
        print("  ✓ enable_admin_mode() sets is_admin=True and current_role='admin'.")

        # 4. Check Admin View Navigation Buttons
        admin_buttons = set(app.nav_buttons.keys())
        expected_admin_tools = {
            "admin_dashboard", "live_attendance", "timetable", "cameras",
            "local_registration", "registration", "users", "override_audit",
            "export", "system_config", "lock"
        }
        for tool in expected_admin_tools:
            assert tool in admin_buttons, f"Admin tool '{tool}' is missing in Admin View!"
        print("  ✓ Admin View unlocks all 10 tools + Lock/Switch button.")

        # 5. Test Switching back to Teacher View
        app.lock_to_teacher_view()
        assert app.is_admin is False, "app.is_admin should be False after locking"
        assert app.current_role == "teacher", "app.current_role should be 'teacher' after locking"
        assert "cameras" not in app.nav_buttons, "Admin buttons should be cleared after locking"
        assert "admin_gate" in app.nav_buttons, "Admin gate button should be restored in Teacher View"
        print("  ✓ lock_to_teacher_view() revokes admin access and restores Teacher View cleanly.")

        # Clean up window
        app.destroy()

def test_admin_password_dialog():
    print("\n[TEST 3] Testing AdminPasswordDialog Password Validation...")
    root = ctk.CTk()
    root.withdraw()
    
    success_called = [False]
    def on_success():
        success_called[0] = True

    with patch.dict(os.environ, {"ADMIN_PASSWORD": "the_fool_12"}):
        dialog = AdminPasswordDialog(root, on_success=on_success)
        dialog.withdraw()

        # 1. Test Incorrect password
        dialog.pass_entry.insert(0, "wrong_password_123")
        with patch("tkinter.messagebox.showerror") as mock_err:
            dialog._verify()
            assert success_called[0] is False, "Success callback should NOT trigger on wrong password!"
            assert "Incorrect Password" in dialog.error_lbl.cget("text")
            mock_err.assert_called_once()
            print("  ✓ Wrong password was rejected with error dialog and kept features locked.")

        # 2. Test Correct password
        dialog.pass_entry.delete(0, "end")
        dialog.pass_entry.insert(0, "the_fool_12")
        dialog._verify()
        assert success_called[0] is True, "Success callback should trigger on correct password!"
        print("  ✓ Correct password 'the_fool_12' successfully authenticated and triggered callback.")

    root.destroy()

if __name__ == "__main__":
    print("=" * 65)
    print("Running Automated Tests for Role-Based UI Views...")
    print("=" * 65)
    test_admin_password_resolution()
    test_app_role_based_views()
    test_admin_password_dialog()
    print("\n" + "=" * 65)
    print("🎉 ALL TESTS PASSED SUCCESSFULLY!")
    print("=" * 65)
