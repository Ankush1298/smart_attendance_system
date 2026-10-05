"""
Role-Based UI Views and Admin Password Gate for Smart Class Attendance System.

Provides:
- get_admin_password(): Resolves admin password from .env (ADMIN_PASSWORD) or fallback 'the_fool_12'
- AdminPasswordDialog: CustomTkinter modal dialog for password gating with masked input and error dialogs
- TeacherDashboardTab: Default public view displaying active sessions, live camera preview, real-time student attendance grid, summaries, and absence reports
- TeacherReportsTab: Read-only attendance summaries and student absence reports
- SystemConfigTab: Protected admin panel for .env parameters, MySQL configuration, connection testing, and server settings
"""
from __future__ import annotations

import os
import sys
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

import customtkinter as ctk
from tkinter import messagebox, filedialog
import cv2
from PIL import Image, ImageTk
import pandas as pd
from dotenv import load_dotenv

from backend.core.db import DatabaseManager, normalize_day
from backend.core.session_logic import SessionLogic, normalize_camera_source
from backend.core.web_portal import get_local_ip

# Root directory of the smart attendance system
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


def get_admin_password() -> str:
    """
    Returns the configured Admin password.
    Loads from .env variable ADMIN_PASSWORD if present and non-empty (not 'change_me'),
    falling back to 'the_fool_12'.
    """
    # Ensure latest .env is loaded
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)

    env_pwd = os.getenv("ADMIN_PASSWORD")
    if env_pwd is not None:
        cleaned = env_pwd.strip()
        if cleaned and cleaned != "change_me":
            return cleaned
    return "the_fool_12"


# ==============================================================================
# 1. ADMIN AUTHENTICATION PASSWORD GATE MODAL
# ==============================================================================
class AdminPasswordDialog(ctk.CTkToplevel):
    """
    Modal password dialog protecting the Admin View.
    Masked entry, eye toggle, and CustomTkinter error handling.
    """
    def __init__(self, master, on_success: Callable[[], None]):
        super().__init__(master)
        self.on_success = on_success
        self.password_visible = False

        self.title("🔒 Admin Authentication Gate")
        self.geometry("460x340")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.transient(master)

        # Center on parent window
        try:
            self.update_idletasks()
            master_x = master.winfo_rootx()
            master_y = master.winfo_rooty()
            master_w = master.winfo_width()
            master_h = master.winfo_height()
            x = master_x + (master_w - 460) // 2
            y = master_y + (master_h - 340) // 2
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        self._build_ui()
        self.grab_set()
        self.lift()
        self.pass_entry.focus_set()

        # Keyboard bindings
        self.bind("<Return>", lambda e: self._verify())
        self.bind("<Escape>", lambda e: self.destroy())

    def _build_ui(self):
        container = ctk.CTkFrame(self, corner_radius=12)
        container.pack(fill="both", expand=True, padx=16, pady=16)

        # Header
        hdr = ctk.CTkFrame(container, fg_color="transparent")
        hdr.pack(fill="x", pady=(12, 6), padx=16)

        ctk.CTkLabel(
            hdr,
            text="🔒 Administrator Verification",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w")

        ctk.CTkLabel(
            container,
            text="Enter the administrator password to unlock full administrative access, "
                 "camera mapping, registration controls, timetable OCR, and system settings.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
            wraplength=390,
            justify="left"
        ).pack(anchor="w", padx=16, pady=(0, 14))

        # Password Entry Container with Eye Toggle
        entry_box = ctk.CTkFrame(container, fg_color="transparent")
        entry_box.pack(fill="x", padx=16, pady=(0, 6))

        self.pass_entry = ctk.CTkEntry(
            entry_box,
            placeholder_text="Enter Admin Password",
            show="*",
            width=320,
            height=38,
            font=ctk.CTkFont(size=14)
        )
        self.pass_entry.pack(side="left", padx=(0, 8))

        self.btn_eye = ctk.CTkButton(
            entry_box,
            text="👁️",
            width=45,
            height=38,
            fg_color=("gray80", "gray25"),
            hover_color=("gray70", "gray35"),
            command=self._toggle_visibility
        )
        self.btn_eye.pack(side="left")

        # In-dialog error label
        self.error_lbl = ctk.CTkLabel(
            container,
            text="",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#ef4444"
        )
        self.error_lbl.pack(anchor="w", padx=18, pady=(2, 8))

        # Security note
        ctk.CTkLabel(
            container,
            text="💡 Default master password is 'the_fool_12' (configurable via ADMIN_PASSWORD in .env).",
            font=ctk.CTkFont(size=10),
            text_color="#64748b"
        ).pack(anchor="w", padx=16, pady=(0, 14))

        # Action Buttons
        btn_row = ctk.CTkFrame(container, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(6, 12))

        ctk.CTkButton(
            btn_row,
            text="Cancel",
            width=100,
            height=34,
            fg_color="transparent",
            border_width=1,
            text_color=("gray30", "gray80"),
            command=self.destroy
        ).pack(side="left")

        self.btn_submit = ctk.CTkButton(
            btn_row,
            text="🔓 Unlock Admin Mode",
            font=ctk.CTkFont(weight="bold"),
            fg_color="#0284c7",
            hover_color="#0369a1",
            height=34,
            command=self._verify
        )
        self.btn_submit.pack(side="right")

    def _toggle_visibility(self):
        self.password_visible = not self.password_visible
        if self.password_visible:
            self.pass_entry.configure(show="")
            self.btn_eye.configure(text="🙈")
        else:
            self.pass_entry.configure(show="*")
            self.btn_eye.configure(text="👁️")

    def _verify(self):
        entered = self.pass_entry.get().strip()
        expected = get_admin_password()

        if entered == expected:
            self.error_lbl.configure(text="")
            self.destroy()
            if self.on_success:
                self.on_success()
        else:
            self.error_lbl.configure(text="❌ Incorrect Password! Admin features remain locked.")
            messagebox.showerror(
                "Access Denied",
                "Incorrect Admin Password!\n\nAdministrative features and configurations remain locked."
            )
            self.pass_entry.delete(0, "end")
            self.pass_entry.focus_set()


# ==============================================================================
# 2. TEACHER DASHBOARD VIEW (DEFAULT / PUBLIC VIEW)
# ==============================================================================
class TeacherDashboardTab(ctk.CTkScrollableFrame):
    """
    Teacher View (Default/Public View):
    - Displays current active classroom sessions and active camera feed preview.
    - Displays live student attendance status/grid for current ongoing class session.
    - Displays class attendance summaries and student absence reports.
    - Removes access to database configurations, system settings, camera mapping, and biometric registration.
    """
    def __init__(self, master, db: DatabaseManager, session_logic: SessionLogic,
                 on_navigate_live: Optional[Callable[[Optional[str]], None]] = None):
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.session_logic = session_logic
        self.on_navigate_live = on_navigate_live

        # Preview camera state
        self._preview_cap = None
        self._preview_running = False
        self._preview_after_id = None
        self._current_room_id = None
        self.roster_data = []

        self.grid_columnconfigure(0, weight=1)

        self._build_ui()
        self._load_active_session_data()
        self.start_preview()

    def stop_camera(self):
        """Safely stops any active camera preview loop."""
        self._preview_running = False
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None
        if self._preview_cap is not None:
            try:
                self._preview_cap.release()
            except Exception:
                pass
            self._preview_cap = None

    def _build_ui(self):
        # 1. Top Header Row
        header_row = ctk.CTkFrame(self, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, 14))

        title_box = ctk.CTkFrame(header_row, fg_color="transparent")
        title_box.pack(side="left")

        ctk.CTkLabel(
            title_box,
            text="👨‍🏫 Teacher Dashboard",
            font=ctk.CTkFont(size=24, weight="bold")
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_box,
            text="Public Classroom View • Active Sessions, Camera Feed Preview & Attendance Roster",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8"
        ).pack(anchor="w")

        btn_box = ctk.CTkFrame(header_row, fg_color="transparent")
        btn_box.pack(side="right")

        ctk.CTkButton(
            btn_box,
            text="🔄 Refresh Data",
            width=110,
            height=30,
            fg_color=("gray80", "gray25"),
            hover_color=("gray70", "gray35"),
            command=self._refresh_all
        ).pack(side="right", padx=4)

        now_str = datetime.now().strftime("%A, %d %B %Y")
        ctk.CTkLabel(
            btn_box,
            text=f"📅 {now_str}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8"
        ).pack(side="right", padx=12)

        # 2. Main Two-Column Row: Active Session Card (Left) + Camera Feed Preview (Right)
        top_grid = ctk.CTkFrame(self, fg_color="transparent")
        top_grid.pack(fill="x", pady=(0, 16))
        top_grid.grid_columnconfigure(0, weight=3)
        top_grid.grid_columnconfigure(1, weight=2)

        # Left: Active Classroom Session Info Card
        self.session_card = ctk.CTkFrame(top_grid, corner_radius=14)
        self.session_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=4)

        s_hdr = ctk.CTkFrame(self.session_card, fg_color="transparent")
        s_hdr.pack(fill="x", padx=18, pady=(16, 8))

        ctk.CTkLabel(
            s_hdr,
            text="🕒 Current Active Classroom Session",
            font=ctk.CTkFont(size=17, weight="bold")
        ).pack(side="left")

        self.lbl_session_badge = ctk.CTkLabel(
            s_hdr,
            text="● SCANNING SCHEDULE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#f59e0b",
            fg_color=("gray85", "gray20"),
            corner_radius=6,
            padx=8,
            pady=3
        )
        self.lbl_session_badge.pack(side="right")

        self.session_body = ctk.CTkFrame(self.session_card, fg_color="transparent")
        self.session_body.pack(fill="both", expand=True, padx=18, pady=(0, 14))

        # Right: Active Camera Feed Preview Card
        preview_card = ctk.CTkFrame(top_grid, corner_radius=14)
        preview_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=4)

        p_hdr = ctk.CTkFrame(preview_card, fg_color="transparent")
        p_hdr.pack(fill="x", padx=14, pady=(14, 6))

        ctk.CTkLabel(
            p_hdr,
            text="📹 Active Camera Feed Preview",
            font=ctk.CTkFont(size=15, weight="bold")
        ).pack(side="left")

        self.lbl_cam_status = ctk.CTkLabel(
            p_hdr,
            text="● Connecting...",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.lbl_cam_status.pack(side="right")

        self.cam_viewport = ctk.CTkLabel(
            preview_card,
            text="Camera feed preview loading...\nEnsure classroom camera is connected.",
            font=ctk.CTkFont(size=12),
            text_color="gray",
            height=200,
            fg_color=("gray90", "gray18"),
            corner_radius=8
        )
        self.cam_viewport.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        p_footer = ctk.CTkFrame(preview_card, fg_color="transparent")
        p_footer.pack(fill="x", padx=14, pady=(0, 12))

        self.btn_preview_toggle = ctk.CTkButton(
            p_footer,
            text="⏹ Pause Preview",
            width=110,
            height=28,
            fg_color=("gray80", "gray25"),
            hover_color=("gray70", "gray35"),
            command=self._toggle_preview
        )
        self.btn_preview_toggle.pack(side="left")

        ctk.CTkButton(
            p_footer,
            text="▶ Open Live Monitor",
            width=140,
            height=28,
            font=ctk.CTkFont(weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            command=self._jump_to_live
        ).pack(side="right")

        # 3. Class Attendance Summaries (4 KPI Cards)
        self.summary_container = ctk.CTkFrame(self, fg_color="transparent")
        self.summary_container.pack(fill="x", pady=(0, 16))
        self.summary_container.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.kpi_cards = {}
        kpi_defs = [
            ("enrolled", "Total Enrolled", "0", "#38bdf8", "👥 Registered in Class"),
            ("present", "Present Today", "0 (0%)", "#22c55e", "🟢 Verified ≥ 80% Dwell"),
            ("warning", "Warnings", "0", "#f59e0b", "🟡 Borderline (60–79%)"),
            ("absent", "Absent Students", "0", "#ef4444", "🔴 Missing / < 60%"),
        ]
        for col_idx, (kpi_id, title, init_val, color, note) in enumerate(kpi_defs):
            card = ctk.CTkFrame(self.summary_container, corner_radius=12)
            card.grid(row=0, column=col_idx, padx=4, pady=4, sticky="nsew")

            val_lbl = ctk.CTkLabel(card, text=init_val, font=ctk.CTkFont(size=22, weight="bold"), text_color=color)
            val_lbl.pack(pady=(12, 1))

            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(0, 1))
            ctk.CTkLabel(card, text=note, font=ctk.CTkFont(size=10), text_color="gray").pack(pady=(0, 10))

            self.kpi_cards[kpi_id] = val_lbl

        # 4. Live Student Attendance Status Grid (Current Session Roster)
        roster_card = ctk.CTkFrame(self, corner_radius=14)
        roster_card.pack(fill="x", pady=(0, 16))

        r_hdr = ctk.CTkFrame(roster_card, fg_color="transparent")
        r_hdr.pack(fill="x", padx=18, pady=(16, 10))

        ctk.CTkLabel(
            r_hdr,
            text="📋 Live Student Attendance Grid (Current Class Session)",
            font=ctk.CTkFont(size=17, weight="bold")
        ).pack(side="left")

        # Search bar
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render_roster())
        search_entry = ctk.CTkEntry(
            r_hdr,
            textvariable=self.search_var,
            placeholder_text="🔍 Search student name or ID...",
            width=230,
            height=30
        )
        search_entry.pack(side="right", padx=(8, 0))

        # Filter option menu
        self.filter_var = ctk.StringVar(value="All Statuses")
        filter_menu = ctk.CTkOptionMenu(
            r_hdr,
            values=["All Statuses", "Present Only 🟢", "Warnings Only 🟡", "Absent Only 🔴"],
            variable=self.filter_var,
            command=lambda _: self._render_roster(),
            width=140,
            height=30
        )
        filter_menu.pack(side="right")

        self.roster_content = ctk.CTkFrame(roster_card, fg_color="transparent")
        self.roster_content.pack(fill="x", padx=18, pady=(0, 16))

        # 5. Student Absence Reports & Alerts Card
        absence_card = ctk.CTkFrame(self, corner_radius=14)
        absence_card.pack(fill="x", pady=(0, 20))

        ab_hdr = ctk.CTkFrame(absence_card, fg_color="transparent")
        ab_hdr.pack(fill="x", padx=18, pady=(16, 10))

        ctk.CTkLabel(
            ab_hdr,
            text="⚠️ Student Absence & At-Risk Alert Report",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#f87171"
        ).pack(side="left")

        ctk.CTkButton(
            ab_hdr,
            text="📥 Export Absence List (CSV)",
            width=180,
            height=30,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self._export_absences_csv
        ).pack(side="right")

        ctk.CTkLabel(
            absence_card,
            text="Immediate institutional report of students absent or at risk for this ongoing session.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8"
        ).pack(anchor="w", padx=18, pady=(0, 10))

        self.absence_content = ctk.CTkFrame(absence_card, fg_color="transparent")
        self.absence_content.pack(fill="x", padx=18, pady=(0, 16))

    def _refresh_all(self):
        self._load_active_session_data()

    def _load_active_session_data(self):
        """Discovers current active classroom session from timetable / session_logic."""
        now = datetime.now()
        current_day = now.strftime("%A").lower()
        current_time = now.strftime("%H:%M")

        timetable = self.db.get_timetable()
        active_slot = None
        upcoming_slot = None

        # Look for active lecture slot right now
        for t in timetable:
            if str(t.get("day_of_week", "")).strip().lower() == current_day:
                st = str(t.get("start_time", ""))
                et = str(t.get("end_time", ""))
                if st <= current_time <= et:
                    active_slot = t
                    break
                elif current_time < st and (upcoming_slot is None or st < str(upcoming_slot.get("start_time", ""))):
                    upcoming_slot = t

        # Render Active Session info
        for w in self.session_body.winfo_children():
            w.destroy()

        if active_slot:
            self._current_room_id = active_slot.get("room_id")
            subj = active_slot.get("subject", "General Lecture")
            room_name = active_slot.get("room_name") or active_slot.get("room_id")
            teacher = active_slot.get("teacher_name") or active_slot.get("teacher_id") or "Instructor"
            time_str = f"{active_slot.get('start_time')} - {active_slot.get('end_time')}"

            self.lbl_session_badge.configure(
                text="● IN PROGRESS 🟢",
                text_color="#22c55e",
                fg_color=("#dcfce7", "#14532d")
            )

            # Details
            row1 = ctk.CTkFrame(self.session_body, fg_color="transparent")
            row1.pack(fill="x", pady=2)
            ctk.CTkLabel(row1, text=f"📚 Subject:", font=ctk.CTkFont(size=13, weight="bold"), width=90, anchor="w").pack(side="left")
            ctk.CTkLabel(row1, text=subj, font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").pack(side="left")

            row2 = ctk.CTkFrame(self.session_body, fg_color="transparent")
            row2.pack(fill="x", pady=2)
            ctk.CTkLabel(row2, text=f"📍 Classroom:", font=ctk.CTkFont(size=13, weight="bold"), width=90, anchor="w").pack(side="left")
            ctk.CTkLabel(row2, text=f"{room_name} (ID: {active_slot.get('room_id')})", font=ctk.CTkFont(size=13)).pack(side="left")

            row3 = ctk.CTkFrame(self.session_body, fg_color="transparent")
            row3.pack(fill="x", pady=2)
            ctk.CTkLabel(row3, text=f"👨‍🏫 Instructor:", font=ctk.CTkFont(size=13, weight="bold"), width=90, anchor="w").pack(side="left")
            ctk.CTkLabel(row3, text=teacher, font=ctk.CTkFont(size=13), text_color="#c084fc").pack(side="left")

            row4 = ctk.CTkFrame(self.session_body, fg_color="transparent")
            row4.pack(fill="x", pady=2)
            ctk.CTkLabel(row4, text=f"⏰ Period:", font=ctk.CTkFont(size=13, weight="bold"), width=90, anchor="w").pack(side="left")
            ctk.CTkLabel(row4, text=time_str, font=ctk.CTkFont(size=13)).pack(side="left")

            # Engine status
            ctk.CTkLabel(
                self.session_body,
                text="⚡ Automated Heartbeat Active: Face recognition runs periodically throughout this period.\n"
                     "   Students with ≥80% dwell time will be marked Present.",
                font=ctk.CTkFont(size=11),
                text_color="#94a3b8",
                justify="left"
            ).pack(anchor="w", pady=(8, 2))

        else:
            self.lbl_session_badge.configure(
                text="⚪ IDLE / NO CLASS",
                text_color="gray",
                fg_color=("gray85", "gray20")
            )
            ctk.CTkLabel(
                self.session_body,
                text="No class session is scheduled for this classroom right now.",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color="gray"
            ).pack(anchor="w", pady=(4, 2))

            if upcoming_slot:
                u_subj = upcoming_slot.get("subject", "-")
                u_room = upcoming_slot.get("room_name") or upcoming_slot.get("room_id")
                u_time = f"{upcoming_slot.get('start_time')} - {upcoming_slot.get('end_time')}"
                ctk.CTkLabel(
                    self.session_body,
                    text=f"Next Upcoming Class Today:\n• {u_subj} in {u_room} at {u_time}",
                    font=ctk.CTkFont(size=12),
                    text_color="#38bdf8",
                    justify="left"
                ).pack(anchor="w", pady=(2, 6))
            else:
                ctk.CTkLabel(
                    self.session_body,
                    text="No further classes scheduled for today.",
                    font=ctk.CTkFont(size=12),
                    text_color="gray"
                ).pack(anchor="w", pady=(2, 6))

            # Pick default room if none
            rooms = self.db.get_rooms()
            if rooms and not self._current_room_id:
                self._current_room_id = rooms[0].get("room_id")

        self._load_student_attendance()

    def _load_student_attendance(self):
        """Loads live attendance roster and computes KPI summaries."""
        all_users = self.db.get_all_users()
        students = [u for u in all_users if u.get("role") == "student"]
        today_str = datetime.now().strftime("%Y-%m-%d")

        # Fetch today's attendance logs from Database
        try:
            with self.db._conn() as con:
                rows = con.execute("""
                    SELECT a.roll_no, a.status, a.confidence, a.timestamp, a.is_override, a.override_reason,
                           s.subject, s.session_id
                    FROM attendance_log a
                    LEFT JOIN sessions s ON a.session_id = s.session_id
                    WHERE DATE(a.timestamp) = ? OR s.date = ?
                    ORDER BY a.timestamp DESC
                """, (today_str, today_str)).fetchall()
                log_map = {}
                for r in rows:
                    rn = r["roll_no"]
                    if rn not in log_map:
                        log_map[rn] = dict(r)
        except Exception:
            log_map = {}

        self.roster_data = []
        present_count = 0
        warning_count = 0
        absent_count = 0

        for s in students:
            rn = s.get("roll_no")
            name = s.get("name")
            warn_cnt = self.db.get_warning_count(rn)

            if rn in log_map:
                st = log_map[rn].get("status", "absent").lower()
                conf = float(log_map[rn].get("confidence", 0.0))
                last_seen = log_map[rn].get("timestamp", "-")
                is_ovr = bool(log_map[rn].get("is_override", 0))
            else:
                st = "absent"
                conf = 0.0
                last_seen = "Not detected today"
                is_ovr = False

            if st == "present":
                present_count += 1
            elif st == "warning":
                warning_count += 1
            else:
                absent_count += 1

            self.roster_data.append({
                "roll_no": rn,
                "name": name,
                "status": st,
                "confidence": conf,
                "last_seen": last_seen,
                "is_override": is_ovr,
                "warnings": warn_cnt
            })

        total = len(students)
        pct = (present_count / max(1, total)) * 100.0

        # Update KPI Cards
        self.kpi_cards["enrolled"].configure(text=str(total))
        self.kpi_cards["present"].configure(text=f"{present_count} ({pct:.1f}%)")
        self.kpi_cards["warning"].configure(text=str(warning_count))
        self.kpi_cards["absent"].configure(text=str(absent_count))

        self._render_roster()
        self._render_absences()

    def _render_roster(self):
        for w in self.roster_content.winfo_children():
            w.destroy()

        q = self.search_var.get().strip().lower()
        filter_val = self.filter_var.get()

        filtered = self.roster_data
        if filter_val == "Present Only 🟢":
            filtered = [r for r in filtered if r["status"] == "present"]
        elif filter_val == "Warnings Only 🟡":
            filtered = [r for r in filtered if r["status"] == "warning"]
        elif filter_val == "Absent Only 🔴":
            filtered = [r for r in filtered if r["status"] == "absent"]

        if q:
            filtered = [
                r for r in filtered
                if q in str(r.get("roll_no", "")).lower() or q in str(r.get("name", "")).lower()
            ]

        if not filtered:
            ctk.CTkLabel(
                self.roster_content,
                text="No students found matching current filter.",
                font=ctk.CTkFont(size=13),
                text_color="gray"
            ).pack(pady=20)
            return

        # Table Header
        hdr = ctk.CTkFrame(self.roster_content, fg_color=("gray85", "gray22"), corner_radius=6)
        hdr.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(hdr, text="Student Name", width=220, anchor="w", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=12, pady=6)
        ctk.CTkLabel(hdr, text="ID / Roll No", width=120, anchor="w", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=4)
        ctk.CTkLabel(hdr, text="Status", width=120, anchor="w", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=4)
        ctk.CTkLabel(hdr, text="Last Detected", width=160, anchor="w", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=4)
        ctk.CTkLabel(hdr, text="Warning History", width=120, anchor="w", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=4)

        # Show up to 35 rows in preview
        for item in filtered[:35]:
            row = ctk.CTkFrame(self.roster_content, fg_color=("gray92", "gray17"), corner_radius=6)
            row.pack(fill="x", pady=2)

            st = item["status"]
            st_color = "#22c55e" if st == "present" else ("#f59e0b" if st == "warning" else "#ef4444")
            st_badge = "PRESENT 🟢" if st == "present" else ("WARNING 🟡" if st == "warning" else "ABSENT 🔴")

            ctk.CTkLabel(row, text=item["name"], width=220, anchor="w", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=12, pady=6)
            ctk.CTkLabel(row, text=item["roll_no"], width=120, anchor="w", font=ctk.CTkFont(size=12)).pack(side="left", padx=4)

            pill = ctk.CTkLabel(row, text=st_badge, width=110, anchor="w", font=ctk.CTkFont(size=11, weight="bold"), text_color=st_color)
            pill.pack(side="left", padx=4)

            ctk.CTkLabel(row, text=str(item["last_seen"])[:19], width=160, anchor="w", font=ctk.CTkFont(size=11), text_color="gray").pack(side="left", padx=4)

            warn_txt = f"{item['warnings']} warning(s)" if item["warnings"] > 0 else "Clean Record"
            warn_col = "#f59e0b" if item["warnings"] > 0 else "gray"
            ctk.CTkLabel(row, text=warn_txt, width=120, anchor="w", font=ctk.CTkFont(size=11), text_color=warn_col).pack(side="left", padx=4)

        if len(filtered) > 35:
            ctk.CTkLabel(
                self.roster_content,
                text=f"Showing 35 of {len(filtered)} students. Use search to narrow down.",
                font=ctk.CTkFont(size=11),
                text_color="gray"
            ).pack(pady=6)

    def _render_absences(self):
        for w in self.absence_content.winfo_children():
            w.destroy()

        absent_list = [r for r in self.roster_data if r["status"] in ("absent", "warning")]

        if not absent_list:
            ctk.CTkLabel(
                self.absence_content,
                text="🎉 Excellent! All enrolled students are currently marked Present for this class.",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#22c55e"
            ).pack(pady=14)
            return

        for item in absent_list[:15]:
            c = ctk.CTkFrame(self.absence_content, fg_color=("gray90", "gray18"), corner_radius=8)
            c.pack(fill="x", pady=3)

            left = ctk.CTkFrame(c, fg_color="transparent")
            left.pack(side="left", padx=12, pady=6)

            tag = "🔴 ABSENT" if item["status"] == "absent" else "🟡 AT-RISK / WARNING"
            col = "#ef4444" if item["status"] == "absent" else "#f59e0b"

            ctk.CTkLabel(
                left,
                text=f"{item['name']} ({item['roll_no']}) — {tag}",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=col
            ).pack(anchor="w")

            ctk.CTkLabel(
                left,
                text=f"Total historical warnings: {item['warnings']} | Last detection: {item['last_seen']}",
                font=ctk.CTkFont(size=10),
                text_color="gray"
            ).pack(anchor="w")

    def _export_absences_csv(self):
        absent_list = [r for r in self.roster_data if r["status"] in ("absent", "warning")]
        if not absent_list:
            messagebox.showinfo("Export", "No absent or at-risk students to export.")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            initialfile=f"absence_report_{datetime.now().strftime('%Y%m%d')}.csv"
        )
        if filepath:
            df = pd.DataFrame(absent_list)
            df.to_csv(filepath, index=False)
            messagebox.showinfo("Export Successful", f"Saved {len(absent_list)} student absence records to:\n{filepath}")

    # Camera Preview Loop
    def start_preview(self):
        if self._preview_running:
            return
        rooms = self.db.get_rooms()
        cam_src = "0"
        for r in rooms:
            if r.get("room_id") == self._current_room_id:
                cam_src = str(r.get("camera_source", "0")).strip() or "0"
                break
        else:
            if rooms:
                cam_src = str(rooms[0].get("camera_source", "0")).strip() or "0"

        source = normalize_camera_source(cam_src)
        try:
            self._preview_cap = cv2.VideoCapture(source)
        except Exception:
            self._preview_cap = None

        self._preview_running = True
        self.btn_preview_toggle.configure(text="⏹ Pause Preview")
        self.lbl_cam_status.configure(text=f"● Feed Active ({cam_src})", text_color="#22c55e")
        self._update_preview_tick()

    def _toggle_preview(self):
        if self._preview_running:
            self.stop_camera()
            self.btn_preview_toggle.configure(text="▶ Resume Preview")
            self.lbl_cam_status.configure(text="● Paused", text_color="gray")
            self.cam_viewport.configure(image="", text="Camera preview paused.\nClick 'Resume Preview' to reconnect.")
        else:
            self.start_preview()

    def _update_preview_tick(self):
        if not self._preview_running or not self.winfo_exists():
            return

        if self._preview_cap and self._preview_cap.isOpened():
            ret, frame = self._preview_cap.read()
            if ret and frame is not None:
                h, w = frame.shape[:2]
                disp_w = 340
                disp_h = max(180, int(h * disp_w / max(1, w)))
                frame_rgb = cv2.cvtColor(cv2.resize(frame, (disp_w, disp_h)), cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame_rgb)
                imgtk = ctk.CTkImage(img, size=(disp_w, disp_h))
                try:
                    self.cam_viewport.configure(image=imgtk, text="")
                    self.cam_viewport.image = imgtk
                except Exception:
                    pass

        if self._preview_running and self.winfo_exists():
            try:
                self._preview_after_id = self.after(100, self._update_preview_tick)
            except Exception:
                pass

    def _jump_to_live(self):
        self.stop_camera()
        if self.on_navigate_live:
            self.on_navigate_live(self._current_room_id)


# ==============================================================================
# 3. TEACHER REPORTS TAB (READ-ONLY)
# ==============================================================================
class TeacherReportsTab(ctk.CTkScrollableFrame):
    """
    Teacher View: Class Attendance Summaries & Student Absence Reports.
    Read-only view without system configurations or database modification controls.
    """
    def __init__(self, master, db: DatabaseManager):
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.grid_columnconfigure(0, weight=1)
        self._build_ui()

    def _build_ui(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(
            hdr,
            text="📋 Class Attendance Summaries & Absence Reports",
            font=ctk.CTkFont(size=22, weight="bold")
        ).pack(side="left")

        # Export Button
        ctk.CTkButton(
            hdr,
            text="📥 Export to Excel",
            fg_color="#16a34a",
            hover_color="#15803d",
            command=self._export_excel
        ).pack(side="right", padx=6)

        ctk.CTkButton(
            hdr,
            text="📄 Export to CSV",
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self._export_csv
        ).pack(side="right", padx=6)

        # Attendance Report Preview
        df = self.db.get_attendance_report()

        preview_card = ctk.CTkFrame(self, corner_radius=12)
        preview_card.pack(fill="x", pady=10)

        ctk.CTkLabel(
            preview_card,
            text=f"Verified Attendance Records ({len(df)} entries)",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=20, pady=(16, 10))

        if df.empty:
            ctk.CTkLabel(
                preview_card,
                text="No attendance records recorded yet.",
                font=ctk.CTkFont(size=13),
                text_color="gray"
            ).pack(padx=20, pady=(0, 20))
        else:
            for _, row in df.tail(25).iterrows():
                r_frame = ctk.CTkFrame(preview_card, fg_color="transparent")
                r_frame.pack(fill="x", padx=20, pady=4)

                st = str(row.get("Status", "present")).lower()
                status_col = "#22c55e" if st == "present" else ("#f59e0b" if st == "warning" else "#ef4444")
                name = row.get("StudentName", "Student")
                roll = row.get("RollNo", "-")
                subj = row.get("Subject", "-")
                ts = row.get("Timestamp", "-")

                ctk.CTkLabel(
                    r_frame,
                    text=f"🎓 {name} ({roll})",
                    font=ctk.CTkFont(size=13, weight="bold")
                ).pack(side="left")

                ctk.CTkLabel(
                    r_frame,
                    text=f"Status: {st.upper()} | Class: {subj}",
                    font=ctk.CTkFont(size=12),
                    text_color=status_col
                ).pack(side="left", padx=14)

                ctk.CTkLabel(
                    r_frame,
                    text=f"{ts}",
                    font=ctk.CTkFont(size=11),
                    text_color="gray"
                ).pack(side="right")

            ctk.CTkLabel(preview_card, text="").pack(pady=4)

    def _export_excel(self):
        df = self.db.get_attendance_report()
        if df.empty:
            messagebox.showinfo("Export", "No attendance records to export.")
            return
        filepath = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
        if filepath:
            df.to_excel(filepath, index=False)
            messagebox.showinfo("Success", f"Exported {len(df)} attendance records to Excel!")

    def _export_csv(self):
        df = self.db.get_attendance_report()
        if df.empty:
            messagebox.showinfo("Export", "No attendance records to export.")
            return
        filepath = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if filepath:
            df.to_csv(filepath, index=False)
            messagebox.showinfo("Success", f"Exported {len(df)} attendance records to CSV!")


# ==============================================================================
# 4. SYSTEM & DATABASE CONFIGURATION TAB (ADMIN ONLY)
# ==============================================================================
class SystemConfigTab(ctk.CTkScrollableFrame):
    """
    Admin View: System & Database Configuration (.env parameters).
    Allows configuring MySQL parameters, testing database connection,
    admin credentials, server ports, and updating the .env file.
    """
    def __init__(self, master, db: DatabaseManager, port: int = 5050):
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.port = port
        self.grid_columnconfigure(0, weight=1)
        self._build_ui()

    def _build_ui(self):
        # Header
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(
            hdr,
            text="⚙️ System & Database Configuration",
            font=ctk.CTkFont(size=24, weight="bold")
        ).pack(side="left")

        ctk.CTkLabel(
            hdr,
            text="👑 MASTER ADMIN ONLY",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#fbbf24",
            fg_color=("#fef3c7", "#78350f"),
            corner_radius=6,
            padx=10,
            pady=4
        ).pack(side="right")

        ctk.CTkLabel(
            self,
            text="Manage core MySQL database credentials, application authentication parameters, and server settings.",
            font=ctk.CTkFont(size=13),
            text_color="#94a3b8"
        ).pack(anchor="w", pady=(0, 16))

        # 1. Database Configuration Card
        db_card = ctk.CTkFrame(self, corner_radius=14)
        db_card.pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(
            db_card,
            text="🗄️ MySQL Database Parameters",
            font=ctk.CTkFont(size=17, weight="bold")
        ).pack(anchor="w", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            db_card,
            text="Controls the relational backend storing students, face embeddings, timetable slots, and audit logs.",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(anchor="w", padx=20, pady=(0, 12))

        form_db = ctk.CTkFrame(db_card, fg_color="transparent")
        form_db.pack(fill="x", padx=20, pady=(0, 14))

        # Host & Port
        row1 = ctk.CTkFrame(form_db, fg_color="transparent")
        row1.pack(fill="x", pady=4)
        ctk.CTkLabel(row1, text="Host:", width=130, anchor="w", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.entry_db_host = ctk.CTkEntry(row1, width=220)
        self.entry_db_host.insert(0, os.getenv("MYSQL_HOST", "127.0.0.1"))
        self.entry_db_host.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(row1, text="Port:", width=50, anchor="w", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.entry_db_port = ctk.CTkEntry(row1, width=100)
        self.entry_db_port.insert(0, os.getenv("MYSQL_PORT", "3306"))
        self.entry_db_port.pack(side="left")

        # Database Name
        row2 = ctk.CTkFrame(form_db, fg_color="transparent")
        row2.pack(fill="x", pady=4)
        ctk.CTkLabel(row2, text="Database Name:", width=130, anchor="w", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.entry_db_name = ctk.CTkEntry(row2, width=386)
        self.entry_db_name.insert(0, os.getenv("MYSQL_DATABASE", "smart_attendance"))
        self.entry_db_name.pack(side="left")

        # User & Password
        row3 = ctk.CTkFrame(form_db, fg_color="transparent")
        row3.pack(fill="x", pady=4)
        ctk.CTkLabel(row3, text="User:", width=130, anchor="w", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.entry_db_user = ctk.CTkEntry(row3, width=220)
        self.entry_db_user.insert(0, os.getenv("MYSQL_USER", "root"))
        self.entry_db_user.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(row3, text="Password:", width=70, anchor="w", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.entry_db_pass = ctk.CTkEntry(row3, width=160, show="*")
        self.entry_db_pass.insert(0, os.getenv("MYSQL_PASSWORD", ""))
        self.entry_db_pass.pack(side="left")

        # Test Connection button & status
        db_actions = ctk.CTkFrame(db_card, fg_color="transparent")
        db_actions.pack(fill="x", padx=20, pady=(6, 16))

        self.btn_test_db = ctk.CTkButton(
            db_actions,
            text="🔌 Test Database Connection",
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self._test_database_connection
        )
        self.btn_test_db.pack(side="left", padx=(0, 12))

        self.lbl_db_test_status = ctk.CTkLabel(
            db_actions,
            text="",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.lbl_db_test_status.pack(side="left")

        # 2. Application & Authentication Parameters Card
        auth_card = ctk.CTkFrame(self, corner_radius=14)
        auth_card.pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(
            auth_card,
            text="🔑 Master Admin & Security Parameters",
            font=ctk.CTkFont(size=17, weight="bold")
        ).pack(anchor="w", padx=20, pady=(16, 4))

        form_auth = ctk.CTkFrame(auth_card, fg_color="transparent")
        form_auth.pack(fill="x", padx=20, pady=(0, 14))

        # Admin Password
        a_row1 = ctk.CTkFrame(form_auth, fg_color="transparent")
        a_row1.pack(fill="x", pady=4)
        ctk.CTkLabel(a_row1, text="Admin Password:", width=150, anchor="w", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.entry_admin_pass = ctk.CTkEntry(a_row1, width=240, show="*")
        self.entry_admin_pass.insert(0, get_admin_password())
        self.entry_admin_pass.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            a_row1,
            text="(Default: the_fool_12)",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        ).pack(side="left")

        # Admin User ID & Name
        a_row2 = ctk.CTkFrame(form_auth, fg_color="transparent")
        a_row2.pack(fill="x", pady=4)
        ctk.CTkLabel(a_row2, text="Admin ID:", width=150, anchor="w", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.entry_admin_id = ctk.CTkEntry(a_row2, width=160)
        self.entry_admin_id.insert(0, os.getenv("ADMIN_ID", "ADMIN"))
        self.entry_admin_id.pack(side="left", padx=(0, 16))

        ctk.CTkLabel(a_row2, text="Display Name:", width=100, anchor="w", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.entry_admin_name = ctk.CTkEntry(a_row2, width=180)
        self.entry_admin_name.insert(0, os.getenv("ADMIN_NAME", "Administrator"))
        self.entry_admin_name.pack(side="left")

        # 3. Server & Networking Parameters Card
        net_card = ctk.CTkFrame(self, corner_radius=14)
        net_card.pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(
            net_card,
            text="🌐 Server & Network Configuration",
            font=ctk.CTkFont(size=17, weight="bold")
        ).pack(anchor="w", padx=20, pady=(16, 4))

        form_net = ctk.CTkFrame(net_card, fg_color="transparent")
        form_net.pack(fill="x", padx=20, pady=(0, 14))

        n_row1 = ctk.CTkFrame(form_net, fg_color="transparent")
        n_row1.pack(fill="x", pady=4)
        ctk.CTkLabel(n_row1, text="KYC Server Port:", width=150, anchor="w", font=ctk.CTkFont(weight="bold")).pack(side="left")
        self.entry_srv_port = ctk.CTkEntry(n_row1, width=120)
        self.entry_srv_port.insert(0, str(self.port))
        self.entry_srv_port.pack(side="left", padx=(0, 16))

        ip = get_local_ip()
        ctk.CTkLabel(
            n_row1,
            text=f"LAN IP: https://{ip}:{self.port} (SSL Enabled)",
            font=ctk.CTkFont(size=11),
            text_color="#34d399"
        ).pack(side="left")

        # 4. Save & Actions Button Row
        act_row = ctk.CTkFrame(self, fg_color="transparent")
        act_row.pack(fill="x", pady=(10, 20))

        ctk.CTkButton(
            act_row,
            text="💾 Save Configuration to .env",
            font=ctk.CTkFont(weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            height=40,
            command=self._save_to_env
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            act_row,
            text="🔄 Reload Current Values",
            height=40,
            fg_color=("gray80", "gray25"),
            hover_color=("gray70", "gray35"),
            command=self._reload_env
        ).pack(side="left")

    def _test_database_connection(self):
        """Tests live connectivity to MySQL using provided credentials."""
        host = self.entry_db_host.get().strip()
        port = int(self.entry_db_port.get().strip() or "3306")
        db_name = self.entry_db_name.get().strip()
        user = self.entry_db_user.get().strip()
        password = self.entry_db_pass.get().strip()

        self.lbl_db_test_status.configure(text="Connecting...", text_color="#f59e0b")
        self.update_idletasks()

        def worker():
            try:
                import mysql.connector
                t0 = time.time()
                conn = mysql.connector.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=db_name,
                    connection_timeout=4
                )
                latency = int((time.time() - t0) * 1000)
                conn.close()
                self.after(0, lambda: self.lbl_db_test_status.configure(
                    text=f"✅ Connected successfully! ({latency}ms latency)",
                    text_color="#22c55e"
                ))
            except Exception as e:
                self.after(0, lambda: self.lbl_db_test_status.configure(
                    text=f"❌ Connection failed: {str(e)[:45]}...",
                    text_color="#ef4444"
                ))
                self.after(0, lambda: messagebox.showerror("MySQL Connection Failed", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _save_to_env(self):
        """Saves values into project's .env file and updates os.environ."""
        host = self.entry_db_host.get().strip()
        port = self.entry_db_port.get().strip()
        dbname = self.entry_db_name.get().strip()
        user = self.entry_db_user.get().strip()
        pw = self.entry_db_pass.get().strip()

        admin_pw = self.entry_admin_pass.get().strip()
        admin_id = self.entry_admin_id.get().strip()
        admin_name = self.entry_admin_name.get().strip()
        srv_port = self.entry_srv_port.get().strip()

        # Update environment variables
        os.environ["MYSQL_HOST"] = host
        os.environ["MYSQL_PORT"] = port
        os.environ["MYSQL_DATABASE"] = dbname
        os.environ["MYSQL_USER"] = user
        os.environ["MYSQL_PASSWORD"] = pw
        os.environ["ADMIN_PASSWORD"] = admin_pw
        os.environ["ADMIN_ID"] = admin_id
        os.environ["ADMIN_NAME"] = admin_name

        # Construct .env content
        lines = [
            "# Smart Attendance System - Environment Configuration",
            "DB_BACKEND=mysql",
            f"MYSQL_HOST={host}",
            f"MYSQL_PORT={port}",
            f"MYSQL_DATABASE={dbname}",
            f"MYSQL_USER={user}",
            f"MYSQL_PASSWORD={pw}",
            f"API_HOST={os.getenv('API_HOST', '127.0.0.1')}",
            f"API_PORT={os.getenv('API_PORT', '8000')}",
            "",
            "# Application Authentication",
            f"ADMIN_ID={admin_id}",
            f"ADMIN_PASSWORD={admin_pw}",
            f"ADMIN_NAME={admin_name}",
            f"PORT={srv_port}",
            f"ALLOW_OPEN_REGISTRATION={os.getenv('ALLOW_OPEN_REGISTRATION', '1')}",
            f"FACE_DEMO_MODE={os.getenv('FACE_DEMO_MODE', '0')}",
            ""
        ]

        try:
            with open(ENV_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            messagebox.showinfo(
                "Configuration Saved",
                f"Successfully updated environment configuration in:\n{ENV_FILE}\n\n"
                f"Admin password is set to: {'(updated)' if admin_pw else 'the_fool_12'}"
            )
        except Exception as e:
            messagebox.showerror("Error Saving .env", f"Could not write to {ENV_FILE}:\n{e}")

    def _reload_env(self):
        if ENV_FILE.exists():
            load_dotenv(ENV_FILE, override=True)
        self.entry_db_host.delete(0, "end")
        self.entry_db_host.insert(0, os.getenv("MYSQL_HOST", "127.0.0.1"))
        self.entry_db_port.delete(0, "end")
        self.entry_db_port.insert(0, os.getenv("MYSQL_PORT", "3306"))
        self.entry_db_name.delete(0, "end")
        self.entry_db_name.insert(0, os.getenv("MYSQL_DATABASE", "smart_attendance"))
        self.entry_db_user.delete(0, "end")
        self.entry_db_user.insert(0, os.getenv("MYSQL_USER", "root"))
        self.entry_db_pass.delete(0, "end")
        self.entry_db_pass.insert(0, os.getenv("MYSQL_PASSWORD", ""))
        self.entry_admin_pass.delete(0, "end")
        self.entry_admin_pass.insert(0, get_admin_password())
        self.entry_admin_id.delete(0, "end")
        self.entry_admin_id.insert(0, os.getenv("ADMIN_ID", "ADMIN"))
        self.entry_admin_name.delete(0, "end")
        self.entry_admin_name.insert(0, os.getenv("ADMIN_NAME", "Administrator"))
        messagebox.showinfo("Reloaded", "Values reloaded from current environment.")


# ==============================================================================
# 5. STARTUP DATABASE CONNECTION DIALOG & SETUP HELPER
# ==============================================================================
def save_env_config(
    host: str,
    port: str,
    database: str,
    user: str,
    password: str,
    admin_id: Optional[str] = None,
    admin_password: Optional[str] = None,
    admin_name: Optional[str] = None,
    srv_port: Optional[str] = None,
) -> None:
    """Writes updated configuration values into .env and os.environ."""
    os.environ["MYSQL_HOST"] = host
    os.environ["MYSQL_PORT"] = str(port)
    os.environ["MYSQL_DATABASE"] = database
    os.environ["MYSQL_USER"] = user
    os.environ["MYSQL_PASSWORD"] = password
    if admin_id is not None:
        os.environ["ADMIN_ID"] = admin_id
    if admin_password is not None:
        os.environ["ADMIN_PASSWORD"] = admin_password
    if admin_name is not None:
        os.environ["ADMIN_NAME"] = admin_name
    if srv_port is not None:
        os.environ["PORT"] = str(srv_port)

    lines = [
        "# Smart Attendance System - Environment Configuration",
        "DB_BACKEND=mysql",
        f"MYSQL_HOST={host}",
        f"MYSQL_PORT={port}",
        f"MYSQL_DATABASE={database}",
        f"MYSQL_USER={user}",
        f"MYSQL_PASSWORD={password}",
        f"API_HOST={os.getenv('API_HOST', '127.0.0.1')}",
        f"API_PORT={os.getenv('API_PORT', '8000')}",
        "",
        "# Application Authentication",
        f"ADMIN_ID={os.getenv('ADMIN_ID', 'ADMIN')}",
        f"ADMIN_PASSWORD={os.getenv('ADMIN_PASSWORD', 'the_fool_12')}",
        f"ADMIN_NAME={os.getenv('ADMIN_NAME', 'Administrator')}",
        f"PORT={os.getenv('PORT', '5050')}",
        f"ALLOW_OPEN_REGISTRATION={os.getenv('ALLOW_OPEN_REGISTRATION', '1')}",
        f"FACE_DEMO_MODE={os.getenv('FACE_DEMO_MODE', '0')}",
        ""
    ]
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


class DatabaseSetupDialog(ctk.CTk):
    """
    Startup dialog displayed when the MySQL database connection fails on initial launch.
    Allows entering MySQL credentials, testing the connection,
    saving to .env, and seamlessly continuing application launch.
    """
    def __init__(self, initial_error: str, on_connected: Callable[[Any], None], db_path: Any = None):
        super().__init__()
        self.initial_error = initial_error
        self.on_connected = on_connected
        self.db_path = db_path
        self.password_visible = False

        self.title("⚙️ MySQL Database Setup")
        self.geometry("540x550")
        self.resizable(False, False)
        self.configure(fg_color="#0f172a")

        # Center on screen
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w, h = 540, 550
        self.geometry(f"{w}x{h}+{(sw - w)//2}+{(sh - h)//2}")

        self._build_ui()

    def _build_ui(self):
        # Header banner
        header = ctk.CTkFrame(self, fg_color="#1e293b", corner_radius=0)
        header.pack(fill="x", padx=0, pady=0)

        ctk.CTkLabel(
            header,
            text="⚙️ MySQL Connection Required",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="#38bdf8"
        ).pack(anchor="w", padx=24, pady=(16, 2))

        ctk.CTkLabel(
            header,
            text="The application requires a MySQL connection to start. Please verify your credentials:",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8"
        ).pack(anchor="w", padx=24, pady=(0, 16))

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=16)

        # Error notification card
        err_card = ctk.CTkFrame(content, fg_color="#450a0a", border_width=1, border_color="#ef4444", corner_radius=8)
        err_card.pack(fill="x", pady=(0, 14))

        err_text = self.initial_error.strip()
        if len(err_text) > 130:
            err_text = err_text[:127] + "..."
        ctk.CTkLabel(
            err_card,
            text=f"⚠️ {err_text}",
            font=ctk.CTkFont(size=11),
            text_color="#fca5a5",
            wraplength=480,
            justify="left"
        ).pack(anchor="w", padx=12, pady=8)

        # Host & Port grid
        hp_frame = ctk.CTkFrame(content, fg_color="transparent")
        hp_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(hp_frame, text="Host:", font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1").grid(row=0, column=0, sticky="w")
        self.entry_host = ctk.CTkEntry(hp_frame, width=320, fg_color="#1e293b", border_color="#334155")
        self.entry_host.insert(0, os.getenv("MYSQL_HOST", "127.0.0.1"))
        self.entry_host.grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(3, 0))

        ctk.CTkLabel(hp_frame, text="Port:", font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1").grid(row=0, column=1, sticky="w")
        self.entry_port = ctk.CTkEntry(hp_frame, width=150, fg_color="#1e293b", border_color="#334155")
        self.entry_port.insert(0, os.getenv("MYSQL_PORT", "3306"))
        self.entry_port.grid(row=1, column=1, sticky="w", pady=(3, 0))

        # Database Name
        ctk.CTkLabel(content, text="Database Name:", font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1").pack(anchor="w", pady=(0, 3))
        self.entry_db = ctk.CTkEntry(content, fg_color="#1e293b", border_color="#334155")
        self.entry_db.insert(0, os.getenv("MYSQL_DATABASE", "smart_attendance"))
        self.entry_db.pack(fill="x", pady=(0, 10))

        # Username
        ctk.CTkLabel(content, text="Username:", font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1").pack(anchor="w", pady=(0, 3))
        self.entry_user = ctk.CTkEntry(content, fg_color="#1e293b", border_color="#334155")
        self.entry_user.insert(0, os.getenv("MYSQL_USER", "root"))
        self.entry_user.pack(fill="x", pady=(0, 10))

        # Password with eye toggle
        ctk.CTkLabel(content, text="MySQL Password:", font=ctk.CTkFont(size=12, weight="bold"), text_color="#cbd5e1").pack(anchor="w", pady=(0, 3))
        pw_box = ctk.CTkFrame(content, fg_color="transparent")
        pw_box.pack(fill="x", pady=(0, 14))

        self.entry_pass = ctk.CTkEntry(pw_box, show="*", fg_color="#1e293b", border_color="#334155")
        self.entry_pass.insert(0, os.getenv("MYSQL_PASSWORD", ""))
        self.entry_pass.pack(side="left", fill="x", expand=True)

        self.btn_eye = ctk.CTkButton(
            pw_box,
            text="👁",
            width=38,
            fg_color="#334155",
            hover_color="#475569",
            command=self._toggle_password
        )
        self.btn_eye.pack(side="right", padx=(8, 0))

        # Status / Feedback label
        self.lbl_status = ctk.CTkLabel(content, text="", font=ctk.CTkFont(size=12), text_color="#38bdf8")
        self.lbl_status.pack(anchor="w", pady=(0, 10))

        # Action buttons
        btn_bar = ctk.CTkFrame(content, fg_color="transparent")
        btn_bar.pack(fill="x", side="bottom")

        self.btn_cancel = ctk.CTkButton(
            btn_bar,
            text="Exit",
            fg_color="#334155",
            hover_color="#475569",
            width=100,
            command=self._on_cancel
        )
        self.btn_cancel.pack(side="left")

        self.btn_connect = ctk.CTkButton(
            btn_bar,
            text="Connect & Save to .env 🚀",
            fg_color="#0284c7",
            hover_color="#0369a1",
            font=ctk.CTkFont(weight="bold"),
            command=self._on_connect_clicked
        )
        self.btn_connect.pack(side="right")

    def _toggle_password(self):
        self.password_visible = not self.password_visible
        self.entry_pass.configure(show="" if self.password_visible else "*")
        self.btn_eye.configure(text="🔒" if self.password_visible else "👁")

    def _on_cancel(self):
        self.destroy()

    def _on_connect_clicked(self):
        import mysql.connector
        from backend.core.db import DatabaseManager

        host = self.entry_host.get().strip() or "127.0.0.1"
        port = self.entry_port.get().strip() or "3306"
        dbname = self.entry_db.get().strip() or "smart_attendance"
        user = self.entry_user.get().strip() or "root"
        pw = self.entry_pass.get()

        self.lbl_status.configure(text="⏳ Connecting to MySQL server...", text_color="#38bdf8")
        self.update_idletasks()

        try:
            # 1. Test basic server connection
            conn = mysql.connector.connect(host=host, port=int(port), user=user, password=pw)
            cur = conn.cursor()
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{dbname}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            cur.close()
            conn.close()

            # 2. Save settings to .env and os.environ
            save_env_config(host=host, port=port, database=dbname, user=user, password=pw)

            # 3. Initialize DatabaseManager
            db = DatabaseManager(self.db_path)

            self.lbl_status.configure(text="✓ Connection successful! Launching app...", text_color="#4ade80")
            self.update_idletasks()
            self.on_connected(db)
            self.destroy()

        except Exception as exc:
            self.lbl_status.configure(text=f"❌ Connection error: {exc}", text_color="#ef4444")


def ensure_database_connection(db_path=None) -> Any:
    """
    Connects to DatabaseManager. If connection fails due to invalid credentials
    or configuration, opens DatabaseSetupDialog to collect credentials and save to .env.
    """
    from backend.core.db import DatabaseManager
    try:
        return DatabaseManager(db_path)
    except Exception as exc:
        print("\n" + "=" * 65)
        print("⚠️  MySQL Database Connection Error:")
        print(f"   {exc}")
        print("\n👉 Opening MySQL Configuration Setup window...")
        print("   (You can also edit smart_attendance_system/.env directly)")
        print("=" * 65 + "\n")

        resolved_db = [None]

        def on_connected(db):
            resolved_db[0] = db

        try:
            setup_dialog = DatabaseSetupDialog(str(exc), on_connected, db_path=db_path)
            setup_dialog.mainloop()
        except Exception as dlg_err:
            print(f"GUI Dialog could not be displayed: {dlg_err}")

        if resolved_db[0] is None:
            raise SystemExit("Application startup aborted: Database connection was not established.")
        return resolved_db[0]
