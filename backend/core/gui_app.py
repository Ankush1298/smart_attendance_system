import sys
import customtkinter as ctk
import pandas as pd
import numpy as np
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk
import io
import threading
import time
import os
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Any, Union, Callable

from backend.core.db import DatabaseManager
from backend.core.session_logic import SessionLogic
from backend.core.web_portal import get_local_ip, generate_qr
from backend.core.face_core import FaceEngine

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def normalize_camera_source(cam_source):
    """
    Normalizes camera sources:
    - 0, 1 -> int (local webcams)
    - DroidCam URLs (port 4747) -> appends /video if missing
    - IP Webcam URLs (port 8080) -> appends /video if missing
    - Raw IP strings -> prepends http:// if needed
    """
    if cam_source is None:
        return 0
    src_str = str(cam_source).strip()
    if not src_str:
        return 0
    if src_str.isdigit():
        return int(src_str)
    
    # Strip trailing slashes for clean matching
    if src_str.endswith("/"):
        src_str = src_str[:-1]

    # Handle DroidCam (standard port 4747)
    if ":4747" in src_str and not src_str.endswith(("/video", "/mjpegfeed")):
        src_str += "/video"

    # Handle IP Webcam (standard port 8080)
    if ":8080" in src_str and not src_str.endswith(("/video", "/video.mjpg", "/mjpegfeed")):
        src_str += "/video"

    # Prepend http:// if raw IP:Port was entered
    if not src_str.startswith(("http://", "https://", "rtsp://", "rtmp://", "/")):
        if ":" in src_str or "." in src_str:
            src_str = "http://" + src_str

    return src_str

class SmartAttendanceApp(ctk.CTk):
    def __init__(self, db: DatabaseManager, session_logic: SessionLogic, port: int = 5000):
        super().__init__()
        self.db = db
        self.session_logic = session_logic
        self.port = port

        self.title("Smart Class Attendance System (Pro)")
        self.geometry("1200x800")
        self.minsize(1050, 680)

        # Root Window Layout: Navigation (fixed width) + Main View (expands full screen)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)

        # Navigation Frame
        self.nav_frame = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.nav_frame.grid(row=0, column=0, sticky="nsew")
        self.nav_frame.grid_rowconfigure(9, weight=1)

        self.logo_label = ctk.CTkLabel(self.nav_frame, text="Smart Class Pro", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.btn_dashboard = ctk.CTkButton(self.nav_frame, corner_radius=0, height=40, border_spacing=10, text="Dashboard",
                                           fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                           anchor="w", command=self.show_dashboard)
        self.btn_dashboard.grid(row=1, column=0, sticky="ew")

        self.btn_live = ctk.CTkButton(self.nav_frame, corner_radius=0, height=40, border_spacing=10, text="📡 Live Attendance",
                                      fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                      anchor="w", command=self.show_live_attendance)
        self.btn_live.grid(row=2, column=0, sticky="ew")

        self.btn_timetable = ctk.CTkButton(self.nav_frame, corner_radius=0, height=40, border_spacing=10, text="Timetable",
                                           fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                           anchor="w", command=self.show_timetable)
        self.btn_timetable.grid(row=3, column=0, sticky="ew")

        self.btn_cameras = ctk.CTkButton(self.nav_frame, corner_radius=0, height=40, border_spacing=10, text="Cameras & Rooms",
                                         fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                         anchor="w", command=self.show_cameras)
        self.btn_cameras.grid(row=4, column=0, sticky="ew")

        self.btn_local_registration = ctk.CTkButton(self.nav_frame, corner_radius=0, height=40, border_spacing=10, text="Local Advanced Registration",
                                              fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                              anchor="w", command=self.show_local_registration)
        self.btn_local_registration.grid(row=5, column=0, sticky="ew")

        self.btn_registration = ctk.CTkButton(self.nav_frame, corner_radius=0, height=40, border_spacing=10, text="Web Portal Link",
                                              fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                              anchor="w", command=self.show_registration)
        self.btn_registration.grid(row=6, column=0, sticky="ew")

        self.btn_users = ctk.CTkButton(self.nav_frame, corner_radius=0, height=40, border_spacing=10, text="👥 Students & Faculty",
                                       fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                       anchor="w", command=self.show_users)
        self.btn_users.grid(row=7, column=0, sticky="ew")

        self.btn_override = ctk.CTkButton(self.nav_frame, corner_radius=0, height=40, border_spacing=10, text="🛡️ Override & Audit",
                                          fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                          anchor="w", command=self.show_override_audit)
        self.btn_override.grid(row=8, column=0, sticky="ew")

        self.btn_export = ctk.CTkButton(self.nav_frame, corner_radius=0, height=40, border_spacing=10, text="Export & Reports",
                                        fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                        anchor="w", command=self.show_export)
        self.btn_export.grid(row=9, column=0, sticky="ew")

        # Main Content Frame - MUST expand 100% full width and full height
        self.main_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)
        
        self.current_frame = None

        self.show_dashboard()

        # Gracefully stop any live camera/preview loop before Tk tears the
        # window down. Destroying the interpreter while an .after() timer
        # (update_preview) is still pending on a live camera tab is a
        # known way to crash Tcl/Tk (fatal abort in _tkinter's callback
        # dispatch), rather than a clean Python exception.
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        try:
            if self.current_frame is not None and hasattr(self.current_frame, "stop_camera"):
                self.current_frame.stop_camera()
        except Exception:
            pass
        try:
            self.session_logic.stop()
        except Exception:
            pass
        self.destroy()


    def select_frame_by_name(self, name):
        # Update button colors
        self.btn_dashboard.configure(fg_color=("gray75", "gray25") if name == "dashboard" else "transparent")
        self.btn_live.configure(fg_color=("gray75", "gray25") if name == "live_attendance" else "transparent")
        self.btn_timetable.configure(fg_color=("gray75", "gray25") if name == "timetable" else "transparent")
        self.btn_cameras.configure(fg_color=("gray75", "gray25") if name == "cameras" else "transparent")
        self.btn_local_registration.configure(fg_color=("gray75", "gray25") if name == "local_registration" else "transparent")
        self.btn_registration.configure(fg_color=("gray75", "gray25") if name == "registration" else "transparent")
        self.btn_users.configure(fg_color=("gray75", "gray25") if name == "users" else "transparent")
        self.btn_override.configure(fg_color=("gray75", "gray25") if name == "override_audit" else "transparent")
        self.btn_export.configure(fg_color=("gray75", "gray25") if name == "export" else "transparent")

    def show_override_audit(self):
        self.select_frame_by_name("override_audit")
        if self.current_frame:
            if hasattr(self.current_frame, 'stop_camera'):
                self.current_frame.stop_camera()
            self.current_frame.destroy()
            
        self.current_frame = OverrideAuditTab(self.main_frame, self.db)
        self.current_frame.grid(row=0, column=0, sticky="nsew")

    def show_live_attendance(self, selected_room_id=None):
        self.select_frame_by_name("live_attendance")
        if self.current_frame:
            if hasattr(self.current_frame, 'stop_camera'):
                self.current_frame.stop_camera()
            self.current_frame.destroy()
            
        self.current_frame = LiveAttendanceTab(
            self.main_frame, self.db, self.session_logic.engine,
            preselected_room_id=selected_room_id
        )
        self.current_frame.grid(row=0, column=0, sticky="nsew")

    def show_dashboard(self):
        self.select_frame_by_name("dashboard")
        if self.current_frame: self.current_frame.destroy()
        
        self.current_frame = ctk.CTkScrollableFrame(self.main_frame, fg_color="transparent")
        self.current_frame.grid(row=0, column=0, sticky="nsew")

        # Top Header
        header_row = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(header_row, text="📊 System Dashboard", font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")
        now_str = datetime.now().strftime("%A, %d %B %Y")
        ctk.CTkLabel(header_row, text=now_str, font=ctk.CTkFont(size=14), text_color="#94a3b8").pack(side="right", padx=10)

        # Stats Cards Grid (instant sub-millisecond query)
        counts = self.db.get_dashboard_counts()
        total_students = counts["students"]
        total_teachers = counts["teachers"]
        total_rooms = counts["rooms"]
        total_timetable = counts["timetable"]

        stats_container = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        stats_container.pack(fill="x", pady=(0, 20))
        stats_container.grid_columnconfigure((0, 1, 2, 3), weight=1)

        kpi_metrics = [
            ("Enrolled Students", str(total_students), "#38bdf8", "🎓 Verified Biometric Profiles"),
            ("Faculty & Teachers", str(total_teachers), "#c084fc", "👨‍🏫 Authorized Instructors"),
            ("Configured Rooms", str(total_rooms), "#34d399", "📹 Multi-Camera Feeds"),
            ("Timetable Periods", str(total_timetable), "#fbbf24", "📅 Course Timetable Entries"),
        ]

        for idx, (title, val, color, note) in enumerate(kpi_metrics):
            card = ctk.CTkFrame(stats_container, corner_radius=14)
            card.grid(row=0, column=idx, padx=6, pady=6, sticky="nsew")
            ctk.CTkLabel(card, text=val, font=ctk.CTkFont(size=28, weight="bold"), text_color=color).pack(pady=(16, 2))
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(0, 2))
            ctk.CTkLabel(card, text=note, font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(0, 14))

        # Active Session & Class Tracker Card
        session_card = ctk.CTkFrame(self.current_frame, corner_radius=14)
        session_card.pack(fill="x", pady=(0, 20))
        
        s_title_row = ctk.CTkFrame(session_card, fg_color="transparent")
        s_title_row.pack(fill="x", padx=20, pady=(16, 8))
        ctk.CTkLabel(s_title_row, text="🕒 Automated Class Attendance Engine", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkLabel(s_title_row, text="● ENGINE ACTIVE", font=ctk.CTkFont(size=12, weight="bold"), text_color="#22c55e").pack(side="right")

        engine_desc = (
            "• Automated Start: Waits 5 minutes after scheduled class start before the first check.\n"
            "• Teacher Verification: Checks for teacher first. If teacher is absent for 20 minutes from scheduled start -> class is automatically SUSPENDED.\n"
            "• Autonomous Scanning: Once teacher is detected, automatically scans student attendance every 5 minutes throughout the class.\n"
            "• Post-Class Grace Check: If both teacher & students are present in the last 2 checks before class ends, a +5 min post-class check grants a 5–10 min grace period for transitioning to the next class.\n"
            "• Attendance Rules: ≥80% Present | 60–79.9% Warning (Absent on 3rd warning) | <60% Absent."
        )
        ctk.CTkLabel(session_card, text=engine_desc, font=ctk.CTkFont(size=12), text_color="#cbd5e1", justify="left").pack(anchor="w", padx=20, pady=(0, 16))

        # Quick Actions Row
        action_row = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        action_row.pack(fill="x", pady=(0, 20))
        action_row.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        actions = [
            ("👥 Students & Faculty", "View & edit users", self.show_users, "#0284c7"),
            ("🛡️ Override & Audit", "Teacher manual exemption", self.show_override_audit, "#9333ea"),
            ("📱 Web QR Portals", "Student/Faculty registration", self.show_registration, "#7c3aed"),
            ("📅 Timetable Schedule", "Manage lecture slots", self.show_timetable, "#059669"),
            ("📊 Export Reports", "Download CSV / Excel logs", self.show_export, "#d97706"),
        ]

        for i, (act_title, act_sub, act_cmd, act_col) in enumerate(actions):
            ac_card = ctk.CTkFrame(action_row, corner_radius=12)
            ac_card.grid(row=0, column=i, padx=4, pady=4, sticky="nsew")
            ctk.CTkLabel(ac_card, text=act_title, font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(12, 2), padx=8)
            ctk.CTkLabel(ac_card, text=act_sub, font=ctk.CTkFont(size=10), text_color="gray").pack(pady=(0, 8), padx=8)
            ctk.CTkButton(ac_card, text="Open", fg_color=act_col, height=28, command=act_cmd).pack(pady=(0, 12), padx=10, fill="x")

        # Recent Registered Users Preview Table
        preview_sec = ctk.CTkFrame(self.current_frame, corner_radius=14)
        preview_sec.pack(fill="x", pady=(0, 20))

        p_header = ctk.CTkFrame(preview_sec, fg_color="transparent")
        p_header.pack(fill="x", padx=20, pady=(16, 10))
        ctk.CTkLabel(p_header, text="👥 Recently Enrolled Members", font=ctk.CTkFont(size=17, weight="bold")).pack(side="left")
        ctk.CTkButton(p_header, text="View All →", width=100, height=28, fg_color="transparent", border_width=1, command=self.show_users).pack(side="right")

        recent_users = self.db.get_recent_users_meta(5)
        if not recent_users:
            ctk.CTkLabel(preview_sec, text="No members enrolled yet. Use the Web Registration QR portals to enroll.", font=ctk.CTkFont(size=13), text_color="gray").pack(padx=20, pady=(0, 20))
        else:
            for u in recent_users:
                u_row = ctk.CTkFrame(preview_sec, fg_color="transparent")
                u_row.pack(fill="x", padx=20, pady=4)
                icon = "👨‍🏫" if u.get("role") == "teacher" else "🎓"
                color = "#c084fc" if u.get("role") == "teacher" else "#38bdf8"
                ctk.CTkLabel(u_row, text=f"{icon} {u.get('name')}", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
                ctk.CTkLabel(u_row, text=f"ID: {u.get('roll_no')} | {u.get('role', 'student').upper()}", font=ctk.CTkFont(size=12), text_color=color).pack(side="left", padx=14)
                ctk.CTkLabel(u_row, text=f"Registered: {u.get('registered_at', '-')}", font=ctk.CTkFont(size=11), text_color="gray").pack(side="right")
            ctk.CTkLabel(preview_sec, text="").pack(pady=4)

    def _import_timetable(self, filepath: str):
        """Parse a timetable file offline and load it into the database.

        Runs on a worker thread with a progress window: a 192-page scanned
        PDF takes minutes to OCR, and blocking the Tk event loop for that
        long would freeze the whole app.
        """
        import queue
        import threading

        import backend.core.timetable_parser as tparser

        progress_win = ctk.CTkToplevel(self)
        progress_win.title("Parsing Timetable")
        progress_win.geometry("470x180")
        progress_win.attributes("-topmost", True)
        progress_win.resizable(False, False)

        ctk.CTkLabel(progress_win, text="Reading timetable locally (no AI service, no API key)",
                     font=ctk.CTkFont(weight="bold")).pack(pady=(18, 4))
        status = ctk.CTkLabel(progress_win, text="Preparing...", text_color="gray", wraplength=430)
        status.pack(pady=2)
        bar = ctk.CTkProgressBar(progress_win, width=410)
        bar.set(0)
        bar.pack(pady=14)

        events = queue.Queue()

        def on_progress(done, total, label):
            events.put(("progress", done, total, label))

        def worker():
            try:
                events.put(("done", tparser.extract_timetable_data(filepath, progress=on_progress)))
            except Exception as exc:
                events.put(("error", exc))

        def poll():
            try:
                while True:
                    event = events.get_nowait()
                    if event[0] == "progress":
                        _, done, total, label = event
                        bar.set(done / total if total else 0)
                        status.configure(text=f"[{done}/{total}] {label}")
                    elif event[0] == "done":
                        progress_win.destroy()
                        self._finish_timetable_import(event[1])
                        return
                    else:
                        progress_win.destroy()
                        messagebox.showerror("Error", f"Failed to parse timetable:\n{event[1]}")
                        return
            except queue.Empty:
                pass
            if progress_win.winfo_exists():
                self.after(120, poll)

        progress_win.protocol("WM_DELETE_WINDOW", lambda: None)
        threading.Thread(target=worker, daemon=True).start()
        self.after(120, poll)

    def _finish_timetable_import(self, df):
        """Create any missing rooms, save the parsed slots, report the result."""
        import backend.core.timetable_parser as tparser

        report = df.attrs.get("report", {}) if hasattr(df, "attrs") else {}
        if df.empty:
            messagebox.showwarning(
                "Nothing Imported",
                "No class slots could be read from that file." + self._timetable_report_text(report),
            )
            return

        try:
            created = tparser.ensure_rooms(self.db, df)
            self.db.load_timetable_from_df(df)
        except Exception as exc:
            import traceback

            traceback.print_exc()
            messagebox.showerror("Error", f"Failed to save timetable:\n{exc}")
            return

        summary = f"Imported {len(df)} class slots."
        if created:
            listed = ", ".join(created[:8]) + (f" (+{len(created) - 8} more)" if len(created) > 8 else "")
            summary += f"\n\n{len(created)} new room(s) added: {listed}"
            summary += "\nAssign real cameras to them from the Cameras tab."
        summary += self._timetable_report_text(report)
        messagebox.showinfo("Timetable Imported", summary)

        # Invalidate the orchestrator's in-memory + file cache so it picks
        # up the new timetable on its next 10-second tick.
        if hasattr(self, "session_logic") and self.session_logic is not None:
            try:
                self.session_logic.invalidate_timetable_cache()
            except Exception:
                pass

        self.show_timetable()

    @staticmethod
    def _timetable_report_text(report) -> str:
        """Human-readable notes about what the parse did and did not read."""
        lines = []
        classes = report.get("classes") or []
        if classes:
            named = [c for c in classes if c.get("title")]
            lines.append(f"\n\nClasses found: {len(classes)}")
            for entry in named[:6]:
                lines.append(f"   p{entry['page']}: {entry['title']}")
            if len(named) > 6:
                lines.append(f"   ... and {len(named) - 6} more")

        inferred = report.get("inferred") or []
        if inferred:
            lines.append(
                f"\n\n{len(inferred)} slot(s) had no room printed on the sheet. "
                "They were placed in the room the same subject and teacher use "
                "elsewhere on that page, so check those if a class lands somewhere odd."
            )

        skipped = report.get("skipped") or []
        if skipped:
            lines.append(
                f"\n\nSkipped {len(skipped)} cell(s) with no teacher or no room "
                "(library / free periods, or a subject with no teacher assigned)."
            )

        warnings = report.get("warnings") or []
        if warnings:
            lines.append(f"\n\n{len(warnings)} page(s) could not be read:")
            for entry in warnings[:5]:
                lines.append(f"   p{entry['page']}: {entry['warning']}")
        return "\n".join(lines)

    def show_timetable(self):
        self.select_frame_by_name("timetable")
        if self.current_frame: self.current_frame.destroy()

        self.current_frame = ctk.CTkScrollableFrame(self.main_frame, fg_color="transparent")
        self.current_frame.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(self.current_frame, text="Timetable Management",
                     font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", pady=(0, 10))

        # ── Upload button ───────────────────────────────────────────────────
        def upload_file():
            filepath = filedialog.askopenfilename(filetypes=[
                ("All Supported Files", "*.csv;*.xlsx;*.xls;*.pdf;*.png;*.jpg;*.jpeg;*.doc;*.docx"),
                ("PDF Timetable", "*.pdf"),
                ("Spreadsheets", "*.csv;*.xlsx;*.xls"),
                ("Images", "*.png;*.jpg;*.jpeg"),
                ("All Files", "*.*")
            ])
            if filepath:
                self._import_timetable(filepath)

        top_bar = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        top_bar.pack(fill="x", pady=(0, 14))
        ctk.CTkButton(top_bar, text="📤 Upload New Timetable (PDF / CSV / Excel)",
                      command=upload_file, fg_color="#059669", hover_color="#047857").pack(side="left")

        tt = self.db.get_timetable()
        if not tt:
            ctk.CTkLabel(self.current_frame,
                         text="No timetable uploaded.\nUse the button above to upload a PDF, CSV, or Excel file.",
                         font=ctk.CTkFont(size=14), text_color="gray").pack(anchor="w", pady=30)
            return

        # ── Stats strip ─────────────────────────────────────────────────────
        from collections import Counter as _Counter
        day_counts = _Counter(r["day_of_week"] for r in tt)
        rooms_set  = {r["room_id"] for r in tt}
        teachers_set = {r["teacher_id"] for r in tt}

        stats = ctk.CTkFrame(self.current_frame, corner_radius=12)
        stats.pack(fill="x", pady=(0, 14))
        stats.grid_columnconfigure((0, 1, 2), weight=1)
        for col, (val, label) in enumerate([
            (str(len(tt)),           "Total Slots"),
            (str(len(rooms_set)),    "Unique Rooms"),
            (str(len(teachers_set)), "Unique Teachers"),
        ]):
            ctk.CTkLabel(stats, text=val, font=ctk.CTkFont(size=22, weight="bold"),
                         text_color="#38bdf8").grid(row=0, column=col, padx=20, pady=(12, 2))
            ctk.CTkLabel(stats, text=label, font=ctk.CTkFont(size=12),
                         text_color="gray").grid(row=1, column=col, padx=20, pady=(0, 12))

        # ── Filters ─────────────────────────────────────────────────────────
        filter_bar = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        filter_bar.pack(fill="x", pady=(0, 8))

        DAY_OPTIONS = ["All Days", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
        day_var = ctk.StringVar(value="All Days")
        search_var = ctk.StringVar()

        ctk.CTkLabel(filter_bar, text="Day:").pack(side="left", padx=(0, 4))
        ctk.CTkOptionMenu(filter_bar, values=DAY_OPTIONS, variable=day_var,
                          width=130, command=lambda _: _render(1)).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(filter_bar, text="Search:").pack(side="left", padx=(0, 4))
        search_entry = ctk.CTkEntry(filter_bar, textvariable=search_var, width=220,
                                    placeholder_text="subject / teacher / room…")
        search_entry.pack(side="left")
        ctk.CTkButton(filter_bar, text="🔍", width=36,
                      command=lambda: _render(1)).pack(side="left", padx=4)

        # ── Table area ───────────────────────────────────────────────────────
        PAGE_SIZE = 30
        table_frame = ctk.CTkFrame(self.current_frame, corner_radius=12)
        table_frame.pack(fill="x", pady=(0, 4))

        page_bar = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        page_bar.pack(fill="x", pady=(4, 14))

        HDR = ["Day", "Start", "End", "Subject", "Teacher", "Room"]
        HDR_W = [100, 68, 68, 320, 200, 90]
        COLS = ["day_of_week", "start_time", "end_time", "subject", "teacher_id", "room_id"]

        def _filtered_rows():
            day = day_var.get()
            q = search_var.get().lower().strip()
            rows = tt
            if day != "All Days":
                rows = [r for r in rows if r["day_of_week"] == day]
            if q:
                rows = [r for r in rows
                        if q in r["subject"].lower()
                        or q in r["teacher_id"].lower()
                        or q in r["room_id"].lower()]
            return rows

        def _render(page: int):
            for w in table_frame.winfo_children():
                w.destroy()
            for w in page_bar.winfo_children():
                w.destroy()

            rows = _filtered_rows()
            total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
            page = max(1, min(page, total_pages))
            slice_ = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]

            # Header
            hdr_row = ctk.CTkFrame(table_frame, fg_color="#1e293b", corner_radius=8)
            hdr_row.pack(fill="x", padx=2, pady=(2, 0))
            for i, (h, w) in enumerate(zip(HDR, HDR_W)):
                ctk.CTkLabel(hdr_row, text=h, font=ctk.CTkFont(size=12, weight="bold"),
                             width=w, anchor="w").grid(row=0, column=i, padx=(8 if i == 0 else 4, 4), pady=6)

            # Rows
            day_colors = {
                "monday": "#0f172a", "tuesday": "#0f172a", "wednesday": "#0f172a",
                "thursday": "#0f172a", "friday": "#0f172a", "saturday": "#1a1f2e",
            }
            for ri, row in enumerate(slice_):
                bg = "#1e293b" if ri % 2 == 0 else "#263245"
                r_frame = ctk.CTkFrame(table_frame, fg_color=bg, corner_radius=0)
                r_frame.pack(fill="x", padx=2)
                vals = [row[c] for c in COLS]
                for ci, (val, w) in enumerate(zip(vals, HDR_W)):
                    text = val if len(val) <= (w // 7 + 2) else val[:w // 7] + "…"
                    ctk.CTkLabel(r_frame, text=text, font=ctk.CTkFont(size=12),
                                 width=w, anchor="w").grid(
                        row=0, column=ci, padx=(8 if ci == 0 else 4, 4), pady=4)

            # Pagination
            ctk.CTkLabel(page_bar,
                         text=f"Showing {len(slice_)} of {len(rows)} rows  (page {page}/{total_pages})",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(side="left")
            if page > 1:
                ctk.CTkButton(page_bar, text="◀ Prev", width=80,
                              command=lambda: _render(page - 1)).pack(side="right", padx=4)
            if page < total_pages:
                ctk.CTkButton(page_bar, text="Next ▶", width=80,
                              command=lambda: _render(page + 1)).pack(side="right", padx=4)

        search_var.trace_add("write", lambda *_: _render(1))
        _render(1)

    def show_cameras(self):
        self.select_frame_by_name("cameras")
        if self.current_frame: self.current_frame.destroy()
        
        self.current_frame = ctk.CTkScrollableFrame(self.main_frame, fg_color="transparent")
        self.current_frame.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(self.current_frame, text="Cameras & Rooms Management", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", pady=(0, 10))
        ctk.CTkLabel(self.current_frame, text="Configure classroom cameras (webcams, RTSP streams, or USB cameras).", font=ctk.CTkFont(size=13), text_color="gray").pack(anchor="w", pady=(0, 20))

        # Top Control & Add Room Frame
        add_frame = ctk.CTkFrame(self.current_frame, corner_radius=10)
        add_frame.pack(fill="x", pady=(0, 14))

        room_id_entry = ctk.CTkEntry(add_frame, placeholder_text="Room ID (e.g. 101, MB-109)", width=160)
        room_id_entry.pack(side="left", padx=10, pady=10)
        
        name_entry = ctk.CTkEntry(add_frame, placeholder_text="Room Name", width=180)
        name_entry.pack(side="left", padx=10, pady=10)

        cam_entry = ctk.CTkEntry(add_frame, placeholder_text="Camera (0 for Webcam, or IP URL)", width=230)
        cam_entry.pack(side="left", padx=10, pady=10)

        def add_room():
            rid = room_id_entry.get().strip()
            rname = name_entry.get().strip() or rid
            raw_cam = cam_entry.get().strip()
            if rid:
                rcam = str(normalize_camera_source(raw_cam) if raw_cam else "")
                self.db.upsert_room(rid, rname, rcam)
                if hasattr(self, "session_logic") and self.session_logic:
                    self.session_logic.invalidate_timetable_cache()
                messagebox.showinfo("Success", f"Room '{rname}' ({rid}) saved with camera: {rcam or '[None - Inactive]'}")
                self.show_cameras()
            else:
                messagebox.showwarning("Warning", "Please provide at least a Room ID.")

        ctk.CTkButton(add_frame, text="➕ Add / Update Room", command=add_room, fg_color="#0284c7", hover_color="#0369a1").pack(side="left", padx=10)

        # Search / Filter Bar
        search_bar = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        search_bar.pack(fill="x", pady=(0, 10))
        
        ctk.CTkLabel(search_bar, text="🔍 Find Room:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(4, 8))
        search_entry = ctk.CTkEntry(search_bar, placeholder_text="Type room name or ID (e.g. 101, MB, WS)...", width=280)
        search_entry.pack(side="left", padx=(0, 10))

        # Pagination & info bar
        info_bar = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        info_bar.pack(fill="x", pady=(0, 6))

        count_lbl = ctk.CTkLabel(info_bar, text="", font=ctk.CTkFont(size=12), text_color="gray")
        count_lbl.pack(side="left")

        nav_box = ctk.CTkFrame(info_bar, fg_color="transparent")
        nav_box.pack(side="right")

        rooms_list_frame = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        rooms_list_frame.pack(fill="x")

        all_rooms = self.db.get_rooms()
        PAGE_SIZE = 25
        cur_page = [1]

        def open_edit_dialog(rid: str, rname: str, cur_cam: str, cur_zone=None):
            dialog = ctk.CTkToplevel(self)
            dialog.title(f"Edit Room Camera — {rid}")
            dialog.geometry("480x320")
            dialog.resizable(False, False)
            dialog.attributes("-topmost", True)

            ctk.CTkLabel(dialog, text=f"📹 Configure Camera for Room {rid}", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(16, 4))
            ctk.CTkLabel(dialog, text="Assign your laptop webcam or phone/IP camera stream to this room.", font=ctk.CTkFont(size=12), text_color="gray").pack(pady=(0, 12))

            form = ctk.CTkFrame(dialog, fg_color="transparent")
            form.pack(fill="x", padx=24, pady=6)

            ctk.CTkLabel(form, text="Room Name:", width=110, anchor="w").grid(row=0, column=0, pady=6, sticky="w")
            e_name = ctk.CTkEntry(form, width=280)
            e_name.insert(0, rname)
            e_name.grid(row=0, column=1, pady=6)

            ctk.CTkLabel(form, text="Camera Source:", width=110, anchor="w").grid(row=1, column=0, pady=6, sticky="w")
            e_cam = ctk.CTkEntry(form, width=280, placeholder_text="0, 1, or http://IP:8080/video")
            e_cam.insert(0, cur_cam)
            e_cam.grid(row=1, column=1, pady=6)

            zone = cur_zone or {}
            ctk.CTkLabel(form, text="Teacher Zone (x1,y1,x2,y2):", width=150, anchor="w").grid(row=2, column=0, pady=6, sticky="w")
            e_zone = ctk.CTkEntry(form, width=280, placeholder_text="0.0,0.0,1.0,1.0")
            e_zone.insert(0, f"{zone.get('teacher_zone_x1',0.0)},{zone.get('teacher_zone_y1',0.0)},{zone.get('teacher_zone_x2',1.0)},{zone.get('teacher_zone_y2',1.0)}")
            e_zone.grid(row=2, column=1, pady=6)
            ctk.CTkLabel(dialog, text="Zone values are normalized 0–1. Set the rectangle around the whiteboard/front teaching area. Full frame is the safe default.", font=ctk.CTkFont(size=10), text_color="gray").pack(pady=(0, 4))

            presets_frame = ctk.CTkFrame(dialog, fg_color="transparent")
            presets_frame.pack(fill="x", padx=24, pady=(4, 12))
            ctk.CTkLabel(presets_frame, text="Quick Presets:", font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=(0, 6))

            def set_preset(val):
                e_cam.delete(0, 'end')
                e_cam.insert(0, val)

            ctk.CTkButton(presets_frame, text="Webcam (0)", width=80, height=24, fg_color="#0284c7", hover_color="#0369a1", command=lambda: set_preset("0")).pack(side="left", padx=3)
            ctk.CTkButton(presets_frame, text="DroidCam", width=75, height=24, fg_color="gray40", hover_color="gray30", command=lambda: set_preset("http://192.168.1.X:4747/video")).pack(side="left", padx=3)
            ctk.CTkButton(presets_frame, text="IP Webcam", width=75, height=24, fg_color="gray40", hover_color="gray30", command=lambda: set_preset("http://192.168.1.X:8080/video")).pack(side="left", padx=3)
            ctk.CTkButton(presets_frame, text="Blank", width=55, height=24, fg_color="#ef4444", hover_color="#dc2626", command=lambda: set_preset("")).pack(side="left", padx=3)

            btn_box = ctk.CTkFrame(dialog, fg_color="transparent")
            btn_box.pack(pady=(10, 16))

            def save_changes():
                new_name = e_name.get().strip() or rid
                raw_c = e_cam.get().strip()
                normalized_c = str(normalize_camera_source(raw_c) if raw_c else "")
                try:
                    vals = [float(v.strip()) for v in e_zone.get().split(',')]
                    if len(vals) != 4:
                        raise ValueError
                    zone_cfg = {"x1": vals[0], "y1": vals[1], "x2": vals[2], "y2": vals[3]}
                except Exception:
                    messagebox.showerror("Invalid Teacher Zone", "Use four normalized numbers: x1,y1,x2,y2 (example: 0.15,0.05,0.85,0.70).")
                    return
                self.db.upsert_room(rid, new_name, normalized_c, zone_cfg)
                if hasattr(self, "session_logic") and self.session_logic:
                    self.session_logic.invalidate_timetable_cache()
                dialog.destroy()
                messagebox.showinfo("Saved", f"Room '{new_name}' ({rid}) camera updated to: {normalized_c or '[Blank / Inactive]'}")
                self.show_cameras()

            ctk.CTkButton(btn_box, text="💾 Save Changes", font=ctk.CTkFont(weight="bold"), fg_color="#10b981", hover_color="#059669", width=140, command=save_changes).pack(side="left", padx=8)
            ctk.CTkButton(btn_box, text="Cancel", width=90, fg_color="gray40", hover_color="gray30", command=dialog.destroy).pack(side="left", padx=8)

        def render_rooms(page=1):
            cur_page[0] = page
            for w in rooms_list_frame.winfo_children():
                w.destroy()
            for w in nav_box.winfo_children():
                w.destroy()

            query = search_entry.get().strip().lower()
            # Sort active cameras first, then by room id
            sorted_rooms = sorted(all_rooms, key=lambda r: (0 if str(r.get("camera_source", "")).strip() else 1, str(r.get("room_id", ""))))
            filtered = [
                r for r in sorted_rooms 
                if query in str(r.get("room_id", "")).lower() or query in str(r.get("room_name", "")).lower()
            ]

            active_count = sum(1 for r in all_rooms if str(r.get("camera_source", "")).strip())
            total_filtered = len(filtered)
            total_pages = max(1, (total_filtered + PAGE_SIZE - 1) // PAGE_SIZE)
            page = max(1, min(page, total_pages))

            count_lbl.configure(text=f"Showing {total_filtered} rooms ({active_count} active cameras configured, {len(all_rooms) - active_count} blank/inactive) • Page {page}/{total_pages}")

            if total_pages > 1:
                if page > 1:
                    ctk.CTkButton(nav_box, text="◀ Prev", width=70, height=24, command=lambda: render_rooms(page - 1)).pack(side="left", padx=3)
                if page < total_pages:
                    ctk.CTkButton(nav_box, text="Next ▶", width=70, height=24, command=lambda: render_rooms(page + 1)).pack(side="left", padx=3)

            if not filtered:
                ctk.CTkLabel(rooms_list_frame, text="No matching rooms found.", text_color="gray").pack(anchor="w", pady=10)
                return

            slice_ = filtered[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]

            for r in slice_:
                rid = r['room_id']
                rname = r.get('room_name', rid)
                c_src = str(r.get('camera_source', '')).strip()
                has_cam = bool(c_src)

                rcard = ctk.CTkFrame(rooms_list_frame, corner_radius=10, fg_color=("gray92", "gray17") if has_cam else ("gray96", "gray14"))
                rcard.pack(fill="x", pady=4)

                # Left details
                left_sec = ctk.CTkFrame(rcard, fg_color="transparent")
                left_sec.pack(side="left", padx=14, pady=8)
                icon = "📹" if has_cam else "⚪"
                ctk.CTkLabel(left_sec, text=f"{icon} {rname} (ID: {rid})", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")

                if has_cam:
                    ctk.CTkLabel(left_sec, text=f"Camera: {c_src}", font=ctk.CTkFont(size=11, weight="bold"), text_color="#22c55e").pack(anchor="w")
                else:
                    ctk.CTkLabel(left_sec, text="No Camera Assigned (Blank / Idle)", font=ctk.CTkFont(size=11), text_color="gray").pack(anchor="w")

                # Action buttons
                def set_quick_webcam(cur_id=rid, cur_name=rname):
                    self.db.upsert_room(cur_id, cur_name, "0")
                    if hasattr(self, "session_logic") and self.session_logic:
                        self.session_logic.invalidate_timetable_cache()
                    messagebox.showinfo("Camera Assigned", f"Room '{cur_name}' ({cur_id}) is now set to primary Webcam (0)!")
                    self.show_cameras()

                def make_del_room(cur_id=rid):
                    return lambda: (self.db.delete_room(cur_id), self.show_cameras())

                def make_start_room(cur_id=rid):
                    return lambda: self.show_live_attendance(selected_room_id=cur_id)

                ctk.CTkButton(rcard, text="🗑️", width=36, fg_color="#ef4444", hover_color="#dc2626", command=make_del_room()).pack(side="right", padx=(4, 12), pady=8)
                ctk.CTkButton(rcard, text="✏️ Edit Camera", width=105, height=28, fg_color="#7c3aed", hover_color="#6d28d9", command=lambda cid=rid, cname=rname, ccam=c_src, cz=r: open_edit_dialog(cid, cname, ccam, cz)).pack(side="right", padx=4, pady=8)

                if has_cam:
                    ctk.CTkButton(rcard, text="▶ Live View", width=100, height=28, fg_color="#10b981", hover_color="#059669", font=ctk.CTkFont(weight="bold"), command=make_start_room()).pack(side="right", padx=4, pady=8)
                else:
                    ctk.CTkButton(rcard, text="📹 Use Webcam (0)", width=140, height=28, fg_color="#0284c7", hover_color="#0369a1", command=set_quick_webcam).pack(side="right", padx=4, pady=8)

        search_entry.bind("<KeyRelease>", lambda e: render_rooms(1))
        render_rooms(1)

    def show_local_registration(self):
        self.select_frame_by_name("local_registration")
        if self.current_frame: 
            if hasattr(self.current_frame, 'stop_camera'):
                self.current_frame.stop_camera()
            self.current_frame.destroy()
            
        self.current_frame = LocalRegistrationTab(self.main_frame, self.db, self.session_logic.engine)
        self.current_frame.grid(row=0, column=0, sticky="nsew")

    def show_registration(self):
        self.select_frame_by_name("registration")
        if self.current_frame: 
            if hasattr(self.current_frame, 'stop_camera'):
                self.current_frame.stop_camera()
            self.current_frame.destroy()
        
        self.current_frame = ctk.CTkScrollableFrame(self.main_frame, fg_color="transparent")
        self.current_frame.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(self.current_frame, text="QR Registration Portals", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", pady=(0, 6))
        ctk.CTkLabel(self.current_frame, text="Students and Teachers can scan their respective QR code to register from their mobile devices with WiFi-style password protection.", font=ctk.CTkFont(size=13), text_color="gray").pack(anchor="w", pady=(0, 20))

        ip = get_local_ip()
        # HTTPS is required by mobile browsers for camera permissions
        student_url = f"https://{ip}:{self.port}/student"
        teacher_url = f"https://{ip}:{self.port}/teacher"

        # Cards container for Student & Teacher QR
        qr_container = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        qr_container.pack(fill="x", pady=(0, 20))
        qr_container.grid_columnconfigure((0, 1), weight=1)

        # Student Card
        student_card = ctk.CTkFrame(qr_container, corner_radius=14)
        student_card.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

        ctk.CTkLabel(student_card, text="🎓 Student QR Portal", font=ctk.CTkFont(size=18, weight="bold"), text_color="#38bdf8").pack(pady=(16, 4))
        ctk.CTkLabel(student_card, text="Scan with Phone to open Secure KYC Face Scan", font=ctk.CTkFont(size=12), text_color="gray").pack(pady=(0, 6))

        student_qr_img = generate_qr(student_url)
        student_qr_ctk = ctk.CTkImage(light_image=student_qr_img, dark_image=student_qr_img, size=(190, 190))
        ctk.CTkLabel(student_card, image=student_qr_ctk, text="").pack(pady=6)

        student_entry = ctk.CTkEntry(student_card, width=280)
        student_entry.insert(0, student_url)
        student_entry.configure(state="readonly")
        student_entry.pack(pady=(4, 6))

        import webbrowser
        btn_row_s = ctk.CTkFrame(student_card, fg_color="transparent")
        btn_row_s.pack(pady=(0, 14))
        ctk.CTkButton(btn_row_s, text="Open in Browser", width=130, height=28, command=lambda: webbrowser.open(student_url)).pack(side="left", padx=4)

        ctk.CTkLabel(student_card, text="🔒 Note: Tap 'Show Details' -> 'Visit Website' once on phone\nto grant camera permissions for local network SSL.", font=ctk.CTkFont(size=10), text_color="#94a3b8").pack(pady=(0, 14))

        # Teacher / Faculty Card
        teacher_card = ctk.CTkFrame(qr_container, corner_radius=14)
        teacher_card.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")

        ctk.CTkLabel(teacher_card, text="👨‍🏫 Faculty / Teacher QR Portal", font=ctk.CTkFont(size=18, weight="bold"), text_color="#c084fc").pack(pady=(16, 4))
        ctk.CTkLabel(teacher_card, text="Protected: Requires Teacher ID & Secret Password", font=ctk.CTkFont(size=12), text_color="gray").pack(pady=(0, 6))

        teacher_qr_img = generate_qr(teacher_url)
        teacher_qr_ctk = ctk.CTkImage(light_image=teacher_qr_img, dark_image=teacher_qr_img, size=(190, 190))
        ctk.CTkLabel(teacher_card, image=teacher_qr_ctk, text="").pack(pady=6)

        teacher_entry = ctk.CTkEntry(teacher_card, width=280)
        teacher_entry.insert(0, teacher_url)
        teacher_entry.configure(state="readonly")
        teacher_entry.pack(pady=(4, 6))

        btn_row_t = ctk.CTkFrame(teacher_card, fg_color="transparent")
        btn_row_t.pack(pady=(0, 14))
        ctk.CTkButton(btn_row_t, text="Open in Browser", width=130, height=28, command=lambda: webbrowser.open(teacher_url)).pack(side="left", padx=4)

        ctk.CTkLabel(teacher_card, text="🔒 Note: Tap 'Show Details' -> 'Visit Website' once on phone\nto grant camera permissions for local network SSL.", font=ctk.CTkFont(size=10), text_color="#94a3b8").pack(pady=(0, 14))

        # Authorized Access Credentials Manager (like WiFi access password)
        cred_sec = ctk.CTkFrame(self.current_frame, corner_radius=14)
        cred_sec.pack(fill="x", pady=10)

        ctk.CTkLabel(cred_sec, text="🔑 Authorized IDs & Passwords (WiFi-Style Access Control)", font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w", padx=20, pady=(16, 4))
        ctk.CTkLabel(cred_sec, text="Issue official IDs & Passwords so unauthorized individuals cannot register as Teachers or Students.", font=ctk.CTkFont(size=12), text_color="gray").pack(anchor="w", padx=20, pady=(0, 14))

        input_row = ctk.CTkFrame(cred_sec, fg_color="transparent")
        input_row.pack(fill="x", padx=20, pady=(0, 12))

        c_role = ctk.CTkComboBox(input_row, values=["teacher", "student"], width=120)
        c_role.set("teacher")
        c_role.pack(side="left", padx=(0, 8))

        c_id = ctk.CTkEntry(input_row, placeholder_text="ID / Roll / Staff Code", width=160)
        c_id.pack(side="left", padx=(0, 8))

        c_pass = ctk.CTkEntry(input_row, placeholder_text="Secret Password", width=150)
        c_pass.pack(side="left", padx=(0, 8))

        c_name = ctk.CTkEntry(input_row, placeholder_text="Official Name", width=160)
        c_name.pack(side="left", padx=(0, 8))

        def add_cred():
            rid = c_id.get().strip()
            rpw = c_pass.get().strip()
            rrole = c_role.get().strip()
            rname = c_name.get().strip()
            if not rid or not rpw:
                messagebox.showerror("Error", "ID and Password are required.")
                return
            self.db.set_authorized_credential(rid, rpw, rrole, rname)
            messagebox.showinfo("Success", f"Credential created for {rrole.capitalize()} {rid}!")
            c_id.delete(0, 'end')
            c_pass.delete(0, 'end')
            c_name.delete(0, 'end')
            self.show_registration()

        ctk.CTkButton(input_row, text="Add / Authorize", command=add_cred, width=120).pack(side="left")

        # Credentials List
        creds = self.db.get_authorized_credentials()
        if creds:
            cred_list_frame = ctk.CTkFrame(cred_sec, fg_color="transparent")
            cred_list_frame.pack(fill="x", padx=20, pady=(0, 16))
            ctk.CTkLabel(cred_list_frame, text="Active Authorized Credentials:", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", pady=(0, 6))

            for cr in creds:
                crow = ctk.CTkFrame(cred_list_frame, fg_color=("gray85", "gray20"), corner_radius=8)
                crow.pack(fill="x", pady=2)
                icon = "👨‍🏫" if cr["role"] == "teacher" else "🎓"
                ctk.CTkLabel(crow, text=f"{icon} {cr['role'].upper()}: {cr['id_number']}  |  Name: {cr.get('allocated_name') or 'N/A'}", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=12, pady=6)
                
                def make_del(i=cr['id_number']):
                    return lambda: (self.db.delete_authorized_credential(i), self.show_registration())
                
                ctk.CTkButton(crow, text="Revoke", width=70, height=24, fg_color="#ef4444", hover_color="#dc2626", command=make_del()).pack(side="right", padx=10, pady=4)
        else:
            ctk.CTkLabel(cred_sec, text="💡 Tip: Add pre-authorized Teacher or Student credentials above to activate password protection.", font=ctk.CTkFont(size=12), text_color="orange").pack(anchor="w", padx=20, pady=(0, 16))


    def show_export(self):
        self.select_frame_by_name("export")
        if self.current_frame: self.current_frame.destroy()
        
        self.current_frame = ctk.CTkScrollableFrame(self.main_frame, fg_color="transparent")
        self.current_frame.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(self.current_frame, text="📊 Attendance Reports & Export", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", pady=(0, 6))
        ctk.CTkLabel(self.current_frame, text="Download verified attendance records in CSV or Excel format for institutional grading and records.", font=ctk.CTkFont(size=13), text_color="gray").pack(anchor="w", pady=(0, 16))

        df = self.db.get_attendance_report()

        btn_row = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        btn_row.pack(anchor="w", pady=(0, 20))

        def export_excel():
            if df.empty:
                messagebox.showinfo("Export", "No attendance data to export.")
                return
            filepath = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
            if filepath:
                df.to_excel(filepath, index=False)
                messagebox.showinfo("Success", f"Exported {len(df)} records to Excel!")

        def export_csv():
            if df.empty:
                messagebox.showinfo("Export", "No attendance data to export.")
                return
            filepath = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
            if filepath:
                df.to_csv(filepath, index=False)
                messagebox.showinfo("Success", f"Exported {len(df)} records to CSV!")

        ctk.CTkButton(btn_row, text="📥 Download as Excel (.xlsx)", fg_color="#16a34a", hover_color="#15803d", command=export_excel).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btn_row, text="📄 Download as CSV (.csv)", fg_color="#0284c7", hover_color="#0369a1", command=export_csv).pack(side="left", padx=10)

        # HOD / management teacher attendance section
        teacher_df = self.db.get_teacher_attendance_report()
        teacher_sec = ctk.CTkFrame(self.current_frame, corner_radius=12)
        teacher_sec.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(teacher_sec, text="👨‍🏫 Teacher Attendance / HOD Report", font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w", padx=20, pady=(16, 4))
        ctk.CTkLabel(teacher_sec, text="Only the middle 40 minutes of a 50-minute lecture count. A teacher absence gap over 20 minutes is explicitly flagged.", font=ctk.CTkFont(size=12), text_color="#cbd5e1").pack(anchor="w", padx=20, pady=(0, 10))
        t_btn = ctk.CTkFrame(teacher_sec, fg_color="transparent")
        t_btn.pack(anchor="w", padx=20, pady=(0, 12))

        def export_teacher_report():
            if teacher_df.empty:
                messagebox.showinfo("Teacher Report", "No teacher attendance records yet.")
                return
            filepath = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
            if filepath:
                teacher_df.to_excel(filepath, index=False)
                messagebox.showinfo("Success", f"Exported {len(teacher_df)} teacher attendance records.")

        ctk.CTkButton(t_btn, text="📥 Export Teacher/HOD Report", fg_color="#7c3aed", hover_color="#6d28d9", command=export_teacher_report).pack(side="left")
        if teacher_df.empty:
            ctk.CTkLabel(teacher_sec, text="No teacher attendance sessions finalized yet.", text_color="gray").pack(anchor="w", padx=20, pady=(0, 16))
        else:
            for _, row in teacher_df.head(10).iterrows():
                flagged = bool(row.get("AbsenceOver20Minutes", 0))
                tag = " ⚠️ ABSENCE > 20 MIN" if flagged else ""
                ctk.CTkLabel(teacher_sec, text=f"{row.get('TeacherName','Teacher')} | {row.get('Subject','-')} | Present {float(row.get('PresentMinutes',0)):.1f}m | Absent {float(row.get('AbsentMinutes',0)):.1f}m | Longest absence {float(row.get('LongestAbsenceMinutes',0)):.1f}m | {str(row.get('Status','')).upper()}{tag}", text_color="#f59e0b" if flagged else "#e2e8f0").pack(anchor="w", padx=20, pady=3)
            ctk.CTkLabel(teacher_sec, text="").pack(pady=2)

        # HOD Override Audit Export Section
        audit_sec = ctk.CTkFrame(self.current_frame, corner_radius=12)
        audit_sec.pack(fill="x", pady=(0, 16))
        
        a_hdr = ctk.CTkFrame(audit_sec, fg_color="transparent")
        a_hdr.pack(fill="x", padx=20, pady=(16, 8))
        ctk.CTkLabel(a_hdr, text="🛡️ HOD / Management Manual Override Audit Report", font=ctk.CTkFont(size=17, weight="bold")).pack(side="left")
        ctk.CTkLabel(a_hdr, text="🔒 IMMUTABLE AUDIT TRAIL", font=ctk.CTkFont(size=11, weight="bold"), text_color="#c084fc").pack(side="right")

        ctk.CTkLabel(audit_sec, text="Full institutional audit log tracking every manual attendance change made by faculty, including original auto statuses, timestamps, and provided reasons.", font=ctk.CTkFont(size=12), text_color="#cbd5e1").pack(anchor="w", padx=20, pady=(0, 12))

        audit_df = self.db.get_override_audit_report()

        a_btn_row = ctk.CTkFrame(audit_sec, fg_color="transparent")
        a_btn_row.pack(anchor="w", padx=20, pady=(0, 16))

        def export_audit_excel():
            if audit_df.empty:
                messagebox.showinfo("Audit Export", "No manual override audit records to export.")
                return
            filepath = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
            if filepath:
                audit_df.to_excel(filepath, index=False)
                messagebox.showinfo("Success", f"Exported {len(audit_df)} audit records to Excel!")

        def export_audit_csv():
            if audit_df.empty:
                messagebox.showinfo("Audit Export", "No manual override audit records to export.")
                return
            filepath = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
            if filepath:
                audit_df.to_csv(filepath, index=False)
                messagebox.showinfo("Success", f"Exported {len(audit_df)} audit records to CSV!")

        ctk.CTkButton(a_btn_row, text="📥 Export Override Audit Log (Excel)", fg_color="#7c3aed", hover_color="#6d28d9", command=export_audit_excel).pack(side="left", padx=(0, 10))
        ctk.CTkButton(a_btn_row, text="📄 Export Override Audit Log (CSV)", fg_color="#0284c7", hover_color="#0369a1", command=export_audit_csv).pack(side="left", padx=10)
        ctk.CTkButton(a_btn_row, text="🔍 Open Audit Viewer", fg_color="transparent", border_width=1, command=self.show_override_audit).pack(side="left", padx=10)

        # Preview table
        prev_card = ctk.CTkFrame(self.current_frame, corner_radius=12)
        prev_card.pack(fill="x", pady=10)
        ctk.CTkLabel(prev_card, text=f"📋 Attendance Log Preview ({len(df)} records logged)", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=20, pady=(16, 10))

        if df.empty:
            ctk.CTkLabel(prev_card, text="No attendance sessions recorded yet.", text_color="gray").pack(padx=20, pady=(0, 20))
        else:
            for _, row in df.tail(15).iterrows():
                r_frame = ctk.CTkFrame(prev_card, fg_color="transparent")
                r_frame.pack(fill="x", padx=20, pady=4)
                is_ovr = row.get("IsManualOverride", 0) == 1
                ovr_tag = " [OVERRIDE ✍️]" if is_ovr else ""
                ctk.CTkLabel(r_frame, text=f"👤 {row.get('StudentName', 'Student')} ({row.get('RollNo', '-')}){ovr_tag}", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
                status_col = "#22c55e" if row.get('Status') == "present" else ("#f59e0b" if row.get('Status') == "warning" else "#ef4444")
                ctk.CTkLabel(r_frame, text=f"Status: {str(row.get('Status', 'Present')).upper()} | Subject: {row.get('Subject', '-')}", font=ctk.CTkFont(size=12), text_color=status_col).pack(side="left", padx=14)
                ctk.CTkLabel(r_frame, text=f"Time: {row.get('Timestamp', '-')}", font=ctk.CTkFont(size=11), text_color="gray").pack(side="right")
            ctk.CTkLabel(prev_card, text="").pack(pady=4)

    def show_users(self):
        self.select_frame_by_name("users")
        if self.current_frame: self.current_frame.destroy()
        
        self.current_frame = ctk.CTkScrollableFrame(self.main_frame, fg_color="transparent")
        self.current_frame.grid(row=0, column=0, sticky="nsew")

        # Title and header
        ctk.CTkLabel(self.current_frame, text="👥 Manage Students & Faculty Directory", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(self.current_frame, text="Search, modify names/roles/IDs, or delete enrolled students and faculty members.", font=ctk.CTkFont(size=13), text_color="gray").pack(anchor="w", pady=(0, 16))

        # Stats Cards
        all_users = self.db.get_all_users() or []
        all_users = all_users or []
        students_count = sum(
            1 for u in all_users
            if isinstance(u, dict) and u.get("role") == "student"
            )
        
        teachers_count = sum(1 for u in all_users if u.get("role") == "teacher")

        stats_row = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        stats_row.pack(fill="x", pady=(0, 14))

        for title, count, color in [("Total Enrolled", len(all_users), "#38bdf8"), ("Students", students_count, "#34d399"), ("Faculty & Teachers", teachers_count, "#c084fc")]:
            c = ctk.CTkFrame(stats_row, corner_radius=10)
            c.pack(side="left", fill="x", expand=True, padx=5)
            ctk.CTkLabel(c, text=str(count), font=ctk.CTkFont(size=22, weight="bold"), text_color=color).pack(pady=(10, 0))
            ctk.CTkLabel(c, text=title, font=ctk.CTkFont(size=12), text_color="gray").pack(pady=(0, 10))

        # Search & Filter bar
        filter_frame = ctk.CTkFrame(self.current_frame)
        filter_frame.pack(fill="x", pady=(0, 14))

        search_entry = ctk.CTkEntry(filter_frame, placeholder_text="🔍 Search by ID or Name...", width=320)
        search_entry.pack(side="left", padx=12, pady=10)

        role_filter = ctk.CTkComboBox(filter_frame, values=["All Roles", "Students Only", "Faculty Only"], width=150)
        role_filter.set("All Roles")
        role_filter.pack(side="left", padx=8, pady=10)

        users_list_frame = ctk.CTkFrame(self.current_frame, fg_color="transparent")
        users_list_frame.pack(fill="x", expand=True)

        def render_list():
            for widget in users_list_frame.winfo_children():
                widget.destroy()

            q = search_entry.get().strip().lower()
            rf = role_filter.get()

            filtered = self.db.get_all_users()
            if rf == "Students Only":
                filtered = [u for u in filtered if u.get("role") == "student"]
            elif rf == "Faculty Only":
                filtered = [u for u in filtered if u.get("role") == "teacher"]

            if q:
                filtered = [u for u in filtered if q in u.get("roll_no", "").lower() or q in u.get("name", "").lower()]

            if not filtered:
                ctk.CTkLabel(users_list_frame, text="No registered users found matching the query.", font=ctk.CTkFont(size=14), text_color="gray").pack(pady=30)
                return

            for u in filtered:
                roll = u.get("roll_no", "")
                name = u.get("name", "")
                role = u.get("role", "student")
                reg_date = u.get("registered_at", "-")
                poses = len(u.get("multi_embeddings") or [1])

                card = ctk.CTkFrame(users_list_frame, corner_radius=12)
                card.pack(fill="x", pady=5, padx=2)

                # Info section
                info_left = ctk.CTkFrame(card, fg_color="transparent")
                info_left.pack(side="left", padx=14, pady=10)

                icon = "👨‍🏫" if role == "teacher" else "🎓"
                role_color = "#c084fc" if role == "teacher" else "#38bdf8"
                ctk.CTkLabel(info_left, text=f"{icon} {name}", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w")
                subtext = f"ID: {roll}   |   Role: {role.upper()}   |   {poses} Face Poses   |   Registered: {reg_date}"
                ctk.CTkLabel(info_left, text=subtext, font=ctk.CTkFont(size=12), text_color="gray").pack(anchor="w")

                # Action buttons
                act_right = ctk.CTkFrame(card, fg_color="transparent")
                act_right.pack(side="right", padx=14, pady=10)

                edit_btn = ctk.CTkButton(act_right, text="✏️ Modify", width=85, fg_color="#0284c7", hover_color="#0369a1", command=lambda u_data=u: open_edit_dialog(u_data))
                edit_btn.pack(side="left", padx=5)

                del_btn = ctk.CTkButton(act_right, text="🗑️ Delete", width=85, fg_color="#dc2626", hover_color="#b91c1c", command=lambda u_data=u: delete_user_action(u_data))
                del_btn.pack(side="left", padx=5)

        def delete_user_action(user_data):
            roll = str(user_data.get("roll_no", ""))
            name = str(user_data.get("name", ""))
            if messagebox.askyesno("Confirm Delete", f"Delete {name} (ID: {roll})?\n\nThis will permanently remove their face profile and allow them to re-register."):
                success = self.db.delete_user(roll)
                if success:
                    messagebox.showinfo("Success", f"{name} ({roll}) was deleted successfully.")
                    render_list()
                else:
                    messagebox.showerror("Error", f"Failed to delete {roll}.")

        def open_edit_dialog(user_data):
            old_roll = str(user_data.get("roll_no", ""))
            old_name = str(user_data.get("name", ""))
            old_role = str(user_data.get("role", "student"))

            diag = ctk.CTkToplevel(self)
            diag.title(f"Modify User - {old_roll}")
            diag.geometry("420x360")
            diag.transient(self)
            diag.grab_set()

            ctk.CTkLabel(diag, text=f"✏️ Modify Details: {old_name}", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(18, 12), padx=20, anchor="w")

            ctk.CTkLabel(diag, text="Full Name:").pack(anchor="w", padx=20, pady=(4, 2))
            name_entry = ctk.CTkEntry(diag, width=380)
            name_entry.insert(0, old_name)
            name_entry.pack(padx=20, pady=(0, 8))

            ctk.CTkLabel(diag, text="ID / Roll Number:").pack(anchor="w", padx=20, pady=(4, 2))
            roll_entry = ctk.CTkEntry(diag, width=380)
            roll_entry.insert(0, old_roll)
            roll_entry.pack(padx=20, pady=(0, 8))

            ctk.CTkLabel(diag, text="Role:").pack(anchor="w", padx=20, pady=(4, 2))
            role_combo = ctk.CTkComboBox(diag, values=["student", "teacher"], width=380)
            role_combo.set(old_role)
            role_combo.pack(padx=20, pady=(0, 16))

            def save_modifications():
                new_n = name_entry.get().strip()
                new_r = roll_entry.get().strip()
                new_role = role_combo.get().strip().lower()

                if not new_n or not new_r:
                    messagebox.showerror("Validation Error", "Name and ID cannot be empty.")
                    return

                res = self.db.update_user_details(old_roll_no=old_roll, new_roll_no=new_r, new_name=new_n, new_role=new_role)
                if res:
                    messagebox.showinfo("Updated", f"Successfully updated {new_n} ({new_r}).")
                    diag.destroy()
                    render_list()
                else:
                    messagebox.showerror("Error", "Could not update user.")

            btn_box = ctk.CTkFrame(diag, fg_color="transparent")
            btn_box.pack(fill="x", padx=20, pady=8)
            ctk.CTkButton(btn_box, text="Save Changes", fg_color="#16a34a", hover_color="#15803d", command=save_modifications).pack(side="right", padx=4)
            ctk.CTkButton(btn_box, text="Cancel", fg_color="gray", command=diag.destroy).pack(side="right", padx=4)

        search_entry.bind("<KeyRelease>", lambda e: render_list())
        role_filter.configure(command=lambda e: render_list())
        ctk.CTkButton(filter_frame, text="🔄 Refresh", width=90, command=render_list).pack(side="left", padx=8, pady=10)

        render_list()

import cv2
from PIL import Image, ImageTk


class LocalRegistrationTab(ctk.CTkFrame):
    """
    Local Advanced Registration Tab.
    
    Captures face from webcam at ORIGINAL quality — no downscaling.
    Saves face embeddings for ALL angles including:
      - FRONT (looking straight)
      - LEFT / RIGHT (head turned)
      - UPLIFTED (looking up)
      - TILT_DOWN (looking slightly down)
      - WRITING (head deeply down, like writing an exam)
    
    InsightFace is run on the full native frame so that the saved
    embeddings reflect maximum biometric quality.
    """
    # All pose buckets we want to capture — includes WRITING for exam scenarios
    POSE_BUCKETS = ["FRONT", "LEFT", "RIGHT", "UPLIFTED", "TILT_DOWN", "WRITING"]
    # Minimum number of distinct poses before registration is allowed
    MIN_POSES_FOR_REGISTRATION = 1  # Allow registration with just FRONT
    # Minimum det_score to accept — kept strict to prevent false face captures
    MIN_DET_SCORE = 0.45

    def __init__(self, master, db: DatabaseManager, engine: FaceEngine):
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.engine = engine
        self.cap = None
        self._preview_running = False
        self._preview_after_id = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ── Left panel: controls ─────────────────────────────────────────────
        form_frame = ctk.CTkScrollableFrame(self, width=320, fg_color="transparent")
        form_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        ctk.CTkLabel(form_frame, text="🎓 Local Registration",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(10, 4))
        ctk.CTkLabel(form_frame,
                     text="Camera captures your face at all angles\n"
                          "automatically — even when writing!",
                     font=ctk.CTkFont(size=11), text_color="gray",
                     justify="center").pack(pady=(0, 10))

        self.uid_entry = ctk.CTkEntry(form_frame, placeholder_text="ID / Roll No")
        self.uid_entry.pack(pady=6, padx=16, fill="x")

        self.name_entry = ctk.CTkEntry(form_frame, placeholder_text="Full Name")
        self.name_entry.pack(pady=6, padx=16, fill="x")

        self.role_entry = ctk.CTkComboBox(form_frame, values=["student", "teacher"])
        self.role_entry.set("student")
        self.role_entry.pack(pady=6, padx=16, fill="x")

        self.password_entry = ctk.CTkEntry(form_frame,
                                           placeholder_text="Access Password / PIN",
                                           show="*")
        self.password_entry.pack(pady=6, padx=16, fill="x")

        self.start_btn = ctk.CTkButton(form_frame, text="▶ Start Camera",
                                       fg_color="#10b981", hover_color="#059669",
                                       command=self.start_camera)
        self.start_btn.pack(pady=10, padx=16, fill="x")

        self.status_lbl = ctk.CTkLabel(form_frame, text="Status: Ready",
                                       text_color="gray",
                                       font=ctk.CTkFont(size=12))
        self.status_lbl.pack(pady=4)

        # Pose progress indicators
        ctk.CTkLabel(form_frame,
                     text="Captured Angle Poses:",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(10, 2))

        self._pose_pill_labels: dict[str, ctk.CTkLabel] = {}
        pill_icons = {
            "FRONT": "😐", "LEFT": "👈", "RIGHT": "👉",
            "UPLIFTED": "😮", "TILT_DOWN": "😑", "WRITING": "✍️"
        }
        pills_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        pills_frame.pack(fill="x", padx=10, pady=4)
        for pose_key in self.POSE_BUCKETS:
            pill_bg = ctk.CTkFrame(pills_frame, fg_color=("gray85", "gray20"),
                                   corner_radius=8)
            pill_bg.pack(side="left", padx=2, pady=2)
            lbl = ctk.CTkLabel(
                pill_bg,
                text=f"{pill_icons.get(pose_key, '⚪')}\n{pose_key[:7]}\n⬜",
                font=ctk.CTkFont(size=9, weight="bold"),
                text_color="gray",
                justify="center",
            )
            lbl.pack(padx=5, pady=3)
            self._pose_pill_labels[pose_key] = lbl

        self.pose_detail_lbl = ctk.CTkLabel(
            form_frame,
            text="Turn head in different directions\nand look down to capture all angles",
            font=ctk.CTkFont(size=10), text_color="gray", justify="center"
        )
        self.pose_detail_lbl.pack(pady=4)

        # Blink status
        self.blink_lbl = ctk.CTkLabel(form_frame, text="👁 Blink: Not detected",
                                      font=ctk.CTkFont(size=11), text_color="gray")
        self.blink_lbl.pack(pady=2)

        # Register button — always visible, enabled only when face is present
        self.register_btn = ctk.CTkButton(
            form_frame, text="📸 Register Now",
            fg_color="#4f8ef7", hover_color="#3b6fd4",
            font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled",
            command=self._do_register
        )
        self.register_btn.pack(pady=10, padx=16, fill="x")

        self.reset_btn = ctk.CTkButton(
            form_frame, text="🔄 Reset Captured Angles",
            fg_color="transparent", border_width=1,
            text_color="gray",
            command=self._reset_poses
        )
        self.reset_btn.pack(pady=2, padx=16, fill="x")

        # ── Right panel: live video ──────────────────────────────────────────
        video_container = ctk.CTkFrame(self, corner_radius=10)
        video_container.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        video_container.grid_rowconfigure(0, weight=1)
        video_container.grid_columnconfigure(0, weight=1)

        self.video_lbl = ctk.CTkLabel(video_container,
                                      text="📷 Camera Preview\n\nClick 'Start Camera' to begin",
                                      font=ctk.CTkFont(size=14), text_color="gray")
        self.video_lbl.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        self.current_pose_lbl = ctk.CTkLabel(video_container,
                                              text="Head Pose: —",
                                              font=ctk.CTkFont(size=12, weight="bold"),
                                              text_color="#38bdf8")
        self.current_pose_lbl.grid(row=1, column=0, pady=(0, 8))

        # State
        self._pose_embeddings: dict[str, np.ndarray] = {}  # bucket -> embedding
        self._current_best_face = None  # most recently detected face object
        self._baseline_ear = 0.25
        self._eye_state = "open"
        self._blink_detected = False
        self._frame_count = 0

    def _reset_poses(self):
        self._pose_embeddings.clear()
        self._current_best_face = None
        pill_icons = {
            "FRONT": "😐", "LEFT": "👈", "RIGHT": "👉",
            "UPLIFTED": "😮", "TILT_DOWN": "😑", "WRITING": "✍️"
        }
        for key, lbl in self._pose_pill_labels.items():
            lbl.configure(
                text=f"{pill_icons.get(key, '⚪')}\n{key[:7]}\n⬜",
                text_color="gray"
            )
        self.pose_detail_lbl.configure(text="Turn head in different directions\nand look down to capture all angles")
        self.register_btn.configure(state="disabled")

    def _pose_to_bucket(self, pose: str) -> str:
        """Normalize pose string to a canonical bucket."""
        pose_up = pose.upper()
        if "WRITING" in pose_up:
            return "WRITING"
        if "UPLIFTED" in pose_up or "TILT-UP" in pose_up or "TILT_UP" in pose_up:
            return "UPLIFTED"
        if "DOWN" in pose_up or "TILT_DOWN" in pose_up:
            return "TILT_DOWN"
        if "LEFT" in pose_up:
            return "LEFT"
        if "RIGHT" in pose_up:
            return "RIGHT"
        return "FRONT"

    def _buffer_pose(self, bucket: str, emb: np.ndarray):
        """Buffer a new embedding for a pose bucket and update the UI pill."""
        if bucket not in self._pose_embeddings:
            self._pose_embeddings[bucket] = emb.copy()
            pill_icons = {
                "FRONT": "😐", "LEFT": "👈", "RIGHT": "👉",
                "UPLIFTED": "😮", "TILT_DOWN": "😑", "WRITING": "✍️"
            }
            lbl = self._pose_pill_labels.get(bucket)
            if lbl:
                lbl.configure(
                    text=f"{pill_icons.get(bucket, '⚪')}\n{bucket[:7]}\n✅",
                    text_color="#22c55e"
                )
            n = len(self._pose_embeddings)
            self.pose_detail_lbl.configure(
                text=f"✅ {n} angle(s) captured. Capture more or Register Now.",
                text_color="#22c55e"
            )
            # Enable register button once we have at least one pose
            if n >= self.MIN_POSES_FOR_REGISTRATION:
                self.register_btn.configure(state="normal")

    def start_camera(self):
        uid = self.uid_entry.get().strip()
        pw = self.password_entry.get().strip()
        name = self.name_entry.get().strip()
        role = self.role_entry.get().strip()

        if not uid or not name:
            messagebox.showerror("Error", "Fill ID and Name first.")
            return
        if not pw:
            messagebox.showerror("Error", "Access Password / PIN is required.")
            return

        # Verify credentials
        cred = self.db.verify_credential(uid, pw, role)
        if not cred.get("valid"):
            messagebox.showerror("Unauthorized",
                                  cred.get("error", "Invalid ID or Password."))
            return

        # Pre-fill name if credential has one and user left name blank
        if cred.get("name") and not name:
            self.name_entry.delete(0, 'end')
            self.name_entry.insert(0, cred["name"])

        if self.cap is None:
            # Open camera at FULL resolution — no downscaling
            self.cap = cv2.VideoCapture(0)
            if not self.cap.isOpened():
                self.cap = cv2.VideoCapture(1)
            try:
                # Request the highest quality the webcam supports
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                self.cap.set(cv2.CAP_PROP_FPS, 30)
                self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
            except Exception:
                pass

            self._preview_running = True
            self._pose_embeddings = {}
            self._current_best_face = None
            self._blink_detected = False
            self._baseline_ear = 0.25
            self._eye_state = "open"
            self._frame_count = 0
            self._reset_poses()

            self.status_lbl.configure(text="📡 Scanning all angles…", text_color="#f59e0b")
            self.start_btn.configure(text="⏹ Stop Camera",
                                      fg_color="#ef4444", hover_color="#dc2626",
                                      command=self.stop_camera)
            self.update_preview()

    def stop_camera(self):
        self._preview_running = False
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None
        if self.cap:
            self.cap.release()
            self.cap = None
        if self.winfo_exists():
            self.start_btn.configure(text="▶ Start Camera",
                                      fg_color="#10b981", hover_color="#059669",
                                      command=self.start_camera)
            self.video_lbl.configure(image="", text="📷 Camera Preview\n\nClick 'Start Camera' to begin")
            self.current_pose_lbl.configure(text="Head Pose: —")

    def _do_register(self):
        """Register the student with all captured pose embeddings."""
        uid = self.uid_entry.get().strip()
        name = self.name_entry.get().strip()
        role = self.role_entry.get().strip()

        if not uid or not name:
            messagebox.showerror("Error", "ID and Name are required.")
            return

        if not self._pose_embeddings:
            messagebox.showerror("No Face Data",
                                  "No face captured yet. Start the camera and face it clearly.")
            return

        # Use the first (primary) embedding as the canonical one
        all_embs = list(self._pose_embeddings.values())
        primary_emb = all_embs[0]

        self.db.upsert_user(
            roll_no=uid,
            name=name,
            role=role,
            embedding=primary_emb,
            multi_embeddings=all_embs
        )

        poses_str = ", ".join(self._pose_embeddings.keys())
        self.status_lbl.configure(
            text=f"✅ Registered! {len(all_embs)} angle(s): {poses_str}",
            text_color="#22c55e"
        )
        messagebox.showinfo(
            "Registration Successful! ✅",
            f"🎉 {name} ({uid}) registered as {role.upper()}!\n\n"
            f"📐 Total pose embeddings saved: {len(all_embs)}\n"
            f"📍 Angles captured: {poses_str}\n\n"
            f"💡 Tip: Run camera again and look in more directions to\n"
            f"   improve recognition accuracy from all angles."
        )
        self.stop_camera()
        self._reset_poses()
        self.uid_entry.delete(0, 'end')
        self.name_entry.delete(0, 'end')

    def update_preview(self):
        if not self._preview_running or not self.winfo_exists():
            return

        try:
            ret, frame = self.cap.read() if self.cap else (False, None)
        except Exception:
            ret, frame = False, None

        if ret and frame is not None:
            # Keep the ORIGINAL frame for face detection — full resolution
            # This is critical: embedding quality = detection frame quality
            self._frame_count += 1

            # Run face detection on the full-resolution frame
            # Use try_get_all_faces to avoid blocking Tk main loop
            faces = self.engine.try_get_all_faces(frame)
            if faces is None:
                faces = []  # inference lock held by background, skip this frame

            # Convert to RGB for display after detection
            frame_display = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            if faces:
                # Sort by face area — largest = closest/most prominent
                faces.sort(
                    key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]),
                    reverse=True
                )
                face = faces[0]
                bbox, kps = face.bbox, face.kps
                det_score = float(getattr(face, 'det_score', 1.0))

                # Accept even angled/partial faces (det_score threshold lowered)
                if det_score >= self.MIN_DET_SCORE and face.embedding is not None:
                    # Normalize embedding
                    emb = face.embedding / (np.linalg.norm(face.embedding) + 1e-9)
                    self._current_best_face = face

                    # Get pose
                    if hasattr(self.engine, 'mesh_helper') and kps is not None:
                        pose = self.engine.mesh_helper.estimate_pose(kps)
                    else:
                        pose = "FRONT"

                    bucket = self._pose_to_bucket(pose)
                    self._buffer_pose(bucket, emb)

                    # Eye blink / liveness
                    if hasattr(self.engine, 'mesh_helper') and kps is not None:
                        ear = self.engine.mesh_helper.compute_eye_aspect(frame, kps)
                        if ear > 0.05:
                            if self._baseline_ear == 0.25:
                                self._baseline_ear = ear
                            elif ear >= self._baseline_ear * 0.70:
                                self._baseline_ear = 0.92 * self._baseline_ear + 0.08 * ear
                        drop = (self._baseline_ear - ear) / (self._baseline_ear + 1e-6)
                        if drop > 0.15:
                            self._eye_state = "closed"
                        elif self._eye_state == "closed" and drop < 0.08:
                            self._eye_state = "open"
                            self._blink_detected = True

                    # Draw mesh overlay
                    if hasattr(self.engine, 'mesh_helper'):
                        frame_display = self.engine.mesh_helper.draw_mesh_overlay(
                            frame_display, bbox, kps, pose
                        )

                    # Update pose label
                    pose_color = {
                        "WRITING": "#f59e0b",
                        "TILT_DOWN": "#fbbf24",
                        "UPLIFTED (TILT-UP)": "#c084fc",
                        "LEFT": "#38bdf8",
                        "RIGHT": "#38bdf8",
                        "FRONT": "#22c55e"
                    }.get(pose, "#22c55e")

                    self.current_pose_lbl.configure(
                        text=f"Head Pose: {pose}  |  Score: {det_score:.2f}",
                        text_color=pose_color
                    )

                    # Update blink label
                    if self._blink_detected:
                        self.blink_lbl.configure(text="👁 Blink: Detected ✅",
                                                   text_color="#22c55e")
                    else:
                        self.blink_lbl.configure(
                            text="👁 Blink: Not yet (optional)", text_color="gray"
                        )
            else:
                self.current_pose_lbl.configure(text="Head Pose: No face detected",
                                                  text_color="gray")

            # Scale for display — compute proportional size to fit the panel
            h, w = frame_display.shape[:2]
            # Target display width ~720px, maintaining aspect ratio
            display_w = min(720, w)
            display_h = int(h * display_w / max(w, 1))

            frame_resized = cv2.resize(frame_display, (display_w, display_h))
            img = Image.fromarray(frame_resized)
            imgtk = ctk.CTkImage(img, size=(display_w, display_h))
            try:
                self.video_lbl.configure(image=imgtk, text="")
                self.video_lbl.image = imgtk
            except Exception:
                return

        if self._preview_running and self.winfo_exists():
            self._preview_after_id = self.after(30, self.update_preview)




class LiveAttendanceTab(ctk.CTkFrame):
    """
    Live Classroom Attendance Monitor & Multi-Face Recognition View
    Streams from any configured camera source (Webcam / USB / RTSP IP stream)
    and tracks student and faculty attendance in real-time.
    """
    def __init__(self, master, db: DatabaseManager, engine: FaceEngine, preselected_room_id: Optional[str] = None):
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.engine = engine
        self.cap = None
        self._running = False
        self.session_id = None
        self.preselected_room_id = preselected_room_id
        
        # State tracking
        self.present_students = set()
        self.teacher_present = False
        self.attendance_records = {} # roll_no -> dict
        self.registered_users = []
        self._start_timestamp = None
        self._total_students_count = 0
        self.matched_timetable_id = None
        self.matched_duration_mins = 60.0
        self.student_minutes_present = {}
        self.student_best_conf = {}
        self._preview_after_id = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_ui()
        self._refresh_users_cache()

    def _refresh_users_cache(self):
        try:
            self.registered_users = self.db.get_all_users()
            self._total_students_count = len([u for u in self.registered_users if u.get("role") == "student"])
            self._update_stat_cards()
        except Exception as e:
            print("Error refreshing users cache:", e)

    def _build_ui(self):
        # Header Row
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
        
        ctk.CTkLabel(header_frame, text="📡 Live Classroom Attendance Monitor", font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")
        self.status_badge = ctk.CTkLabel(header_frame, text="● IDLE", font=ctk.CTkFont(size=12, weight="bold"), text_color="gray", fg_color=("gray85", "gray20"), corner_radius=6, padx=10, pady=4)
        self.status_badge.pack(side="left", padx=14)

        # Control & Configuration Bar
        ctrl_card = ctk.CTkFrame(self, corner_radius=10)
        ctrl_card.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

        # Room selector
        rooms = self.db.get_rooms()
        self.room_map = {f"{r['room_name']} ({r['room_id']})": r for r in rooms}
        room_options = list(self.room_map.keys()) if self.room_map else ["No Rooms Configured"]

        default_room_choice = room_options[0]
        if self.preselected_room_id:
            for opt_text, r_data in self.room_map.items():
                if r_data["room_id"] == self.preselected_room_id:
                    default_room_choice = opt_text
                    break

        ctk.CTkLabel(ctrl_card, text="Room:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(14, 4), pady=12)
        self.room_combo = ctk.CTkOptionMenu(ctrl_card, values=room_options, width=190)
        self.room_combo.set(default_room_choice)
        self.room_combo.pack(side="left", padx=4, pady=12)

        # Subject Entry
        ctk.CTkLabel(ctrl_card, text="Subject:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(10, 4), pady=12)
        self.subj_entry = ctk.CTkEntry(ctrl_card, placeholder_text="e.g. CS101 / General", width=140)
        self.subj_entry.insert(0, "General Session")
        self.subj_entry.pack(side="left", padx=4, pady=12)

        # Auto Detect Button
        ctk.CTkButton(ctrl_card, text="⚡ Schedule Match", width=120, fg_color="transparent", border_width=1, command=self._auto_detect_schedule).pack(side="left", padx=6, pady=12)

        # Action Buttons
        self.start_btn = ctk.CTkButton(ctrl_card, text="▶ Start Attendance", font=ctk.CTkFont(weight="bold"), fg_color="#10b981", hover_color="#059669", width=150, command=self.toggle_session)
        self.start_btn.pack(side="right", padx=(6, 14), pady=12)

        self.scan_now_btn = ctk.CTkButton(ctrl_card, text="📷 Instant Scan", width=110, fg_color="#38bdf8", text_color="#000", hover_color="#0ea5e9", state="disabled", command=self.instant_scan)
        self.scan_now_btn.pack(side="right", padx=6, pady=12)

        # Main Workspace: 2 Column Layout (Left: Video + Stats, Right: Real-time Attendance Roster)
        content_pane = ctk.CTkFrame(self, fg_color="transparent")
        content_pane.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        content_pane.grid_columnconfigure(0, weight=3)
        content_pane.grid_columnconfigure(1, weight=2)
        content_pane.grid_rowconfigure(1, weight=1)

        # Top Stats Cards in Left Pane
        stats_frame = ctk.CTkFrame(content_pane, fg_color="transparent")
        stats_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        stats_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # 1. Students Present
        self.card_students = ctk.CTkFrame(stats_frame, corner_radius=8)
        self.card_students.grid(row=0, column=0, padx=4, sticky="ew")
        ctk.CTkLabel(self.card_students, text="Students Present", font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(6, 0))
        self.lbl_students_count = ctk.CTkLabel(self.card_students, text="0 / 0", font=ctk.CTkFont(size=18, weight="bold"), text_color="#38bdf8")
        self.lbl_students_count.pack(pady=(0, 6))

        # 2. Teacher Status
        self.card_teacher = ctk.CTkFrame(stats_frame, corner_radius=8)
        self.card_teacher.grid(row=0, column=1, padx=4, sticky="ew")
        ctk.CTkLabel(self.card_teacher, text="Faculty Status", font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(6, 0))
        self.lbl_teacher_status = ctk.CTkLabel(self.card_teacher, text="⏳ Awaiting", font=ctk.CTkFont(size=15, weight="bold"), text_color="#f59e0b")
        self.lbl_teacher_status.pack(pady=(0, 6))

        # 3. Faces in Frame
        self.card_faces = ctk.CTkFrame(stats_frame, corner_radius=8)
        self.card_faces.grid(row=0, column=2, padx=4, sticky="ew")
        ctk.CTkLabel(self.card_faces, text="Current Frame Faces", font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(6, 0))
        self.lbl_faces_count = ctk.CTkLabel(self.card_faces, text="0 Faces", font=ctk.CTkFont(size=18, weight="bold"), text_color="#c084fc")
        self.lbl_faces_count.pack(pady=(0, 6))

        # 4. Session Elapsed Time
        self.card_time = ctk.CTkFrame(stats_frame, corner_radius=8)
        self.card_time.grid(row=0, column=3, padx=4, sticky="ew")
        ctk.CTkLabel(self.card_time, text="Session Duration", font=ctk.CTkFont(size=11), text_color="gray").pack(pady=(6, 0))
        self.lbl_session_time = ctk.CTkLabel(self.card_time, text="00:00", font=ctk.CTkFont(size=18, weight="bold"), text_color="#34d399")
        self.lbl_session_time.pack(pady=(0, 6))

        # Video Viewport (Left)
        video_container = ctk.CTkFrame(content_pane, corner_radius=10)
        video_container.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        video_container.grid_rowconfigure(0, weight=1)
        video_container.grid_columnconfigure(0, weight=1)

        self.video_lbl = ctk.CTkLabel(video_container, text="Camera Offline\nClick 'Start Attendance' to begin live stream", font=ctk.CTkFont(size=14), text_color="gray")
        self.video_lbl.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        # Right Pane: Real-Time Attendance Roster
        roster_container = ctk.CTkFrame(content_pane, corner_radius=10)
        roster_container.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        roster_container.grid_rowconfigure(1, weight=1)
        roster_container.grid_columnconfigure(0, weight=1)

        roster_hdr = ctk.CTkFrame(roster_container, fg_color="transparent")
        roster_hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=10)
        ctk.CTkLabel(roster_hdr, text="📋 Live Attendance Log", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        ctk.CTkButton(roster_hdr, text="Clear List", width=70, height=24, fg_color="transparent", border_width=1, text_color="gray", command=self._clear_roster).pack(side="right")

        self.roster_scroll = ctk.CTkScrollableFrame(roster_container, fg_color="transparent")
        self.roster_scroll.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

        self.empty_roster_lbl = ctk.CTkLabel(self.roster_scroll, text="No students detected yet.\nFaces identified in camera feed will appear here instantly.", text_color="gray", font=ctk.CTkFont(size=12))
        self.empty_roster_lbl.pack(pady=30)

    def _auto_detect_schedule(self):
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_day = now.strftime("%A")
        
        timetable = self.db.get_timetable()
        found = False
        for t in timetable:
            if t["day_of_week"].lower() == current_day.lower():
                if t["start_time"] <= current_time <= t["end_time"]:
                    # Match found!
                    self.subj_entry.delete(0, 'end')
                    self.subj_entry.insert(0, t["subject"])
                    for opt_text, r_data in self.room_map.items():
                        if str(r_data["room_id"]).lower() == str(t["room_id"]).lower():
                            self.room_combo.set(opt_text)
                            break
                    self.matched_timetable_id = t.get("class_id") or t.get("id")
                    fmt = "%H:%M"
                    try:
                        t1 = datetime.strptime(t["start_time"], fmt)
                        t2 = datetime.strptime(t["end_time"], fmt)
                        self.matched_duration_mins = max(1.0, (t2 - t1).total_seconds() / 60.0)
                    except Exception:
                        self.matched_duration_mins = 60.0
                    messagebox.showinfo("Timetable Matched", f"Found scheduled class:\nSubject: {t['subject']}\nRoom: {t['room_id']}\nTime: {t['start_time']} - {t['end_time']}\nDuration: {int(self.matched_duration_mins)} mins")
                    found = True
                    break
        if not found:
            messagebox.showinfo("No Active Schedule", f"No scheduled class found in timetable for {current_day} at {current_time}.\nYou can start a manual session.")

    def _update_stat_cards(self):
        self.lbl_students_count.configure(text=f"{len(self.present_students)} / {self._total_students_count}")
        if self.teacher_present:
            self.lbl_teacher_status.configure(text="✅ Present", text_color="#22c55e")
        else:
            self.lbl_teacher_status.configure(text="⏳ Awaiting", text_color="#f59e0b")

    def toggle_session(self):
        if not self._running:
            self.start_session()
        else:
            self.stop_session()

    def start_session(self):
        selected_room_text = self.room_combo.get()
        if not selected_room_text or selected_room_text not in self.room_map:
            messagebox.showwarning("Warning", "Please add and select a valid classroom room with a camera source first.")
            return

        room_data = self.room_map[selected_room_text]
        raw_source = room_data.get("camera_source", "0")
        source = normalize_camera_source(raw_source)

        try:
            self.cap = cv2.VideoCapture(source)
            if isinstance(source, int):
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        except Exception as e:
            self.cap = None

        if not self.cap or not self.cap.isOpened():
            err_msg = (
                f"Could not connect to camera stream:\n'{source}'\n\n"
                "💡 Quick Fixes for Phone / IP Cameras:\n"
                "1. If using DroidCam: ensure the app is open on your phone and use: http://<PhoneIP>:4747/video\n"
                "2. If using IP Webcam: ensure the server is started and use: http://<PhoneIP>:8080/video\n"
                "3. Ensure both your Mac and phone are connected to the EXACT same Wi-Fi network."
            )
            messagebox.showerror("Camera Connection Error", err_msg)
            if self.cap:
                self.cap.release()
            self.cap = None
            return

        self._refresh_users_cache()

        now = datetime.now()
        subject = self.subj_entry.get().strip() or "General Class"
        self.session_id = f"SES_{room_data['room_id']}_{now.strftime('%Y%m%d_%H%M%S')}"
        
        self.student_minutes_present.clear()
        self.student_best_conf.clear()
        
        # Register in database
        try:
            self.db.create_session(self.session_id, self.matched_timetable_id, now.strftime('%Y-%m-%d'), subject)
        except Exception as e:
            print("Session create warning:", e)

        self._running = True
        self._start_timestamp = time.time()
        self.status_badge.configure(text=f"● LIVE ({subject})", text_color="#22c55e", fg_color=("#dcfce7", "#14532d"))
        self.start_btn.configure(text="⏹ Stop Attendance", fg_color="#ef4444", hover_color="#dc2626")
        self.scan_now_btn.configure(state="normal")
        self.room_combo.configure(state="disabled")

        self.update_preview()

    def instant_scan(self):
        if not self._running or not self.cap:
            return
        ret, frame = self.cap.read()
        if ret:
            self._process_frame_faces(frame, force_instant=True)
            messagebox.showinfo("Instant Scan Complete", f"Processed current frame.\nTotal Students Present: {len(self.present_students)}")

    def stop_session(self):
        self._running = False
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
            self._preview_after_id = None
        if self.cap:
            self.cap.release()
            self.cap = None

        if self.session_id:
            try:
                self.db.update_session_status(self.session_id, 'completed')
                # Process student attendance percentages with precise transition grace adjustment
                today_str = datetime.now().strftime("%Y-%m-%d")
                for uid, minutes_set in self.student_minutes_present.items():
                    user_data = next((u for u in self.registered_users if u.get("roll_no") == uid), None)
                    if not user_data or user_data.get("role") == "teacher":
                        continue

                    mins_present = float(len(minutes_set))
                    grace_rec = self.db.get_valid_transition_grace(uid, today_str, self.matched_timetable_id) if self.matched_timetable_id else None
                    grace_mins = float(grace_rec.get("grace_minutes", 5.0)) if grace_rec else 0.0

                    calc_res = self.db.calculate_attendance_with_grace(
                        roll_no=uid,
                        detected_minutes=mins_present,
                        scheduled_duration=self.matched_duration_mins,
                        grace_minutes_available=grace_mins
                    )
                    if grace_rec:
                        self.db.consume_transition_grace(grace_rec["id"], self.session_id)

                    status = calc_res["status"]
                    conf = self.student_best_conf.get(uid, 0.0)
                    self.db.log_attendance(self.session_id, uid, "student", status, float(conf))
                    
                    # Update local set for summary if they became absent
                    if status == "absent" and uid in self.present_students:
                        self.present_students.remove(uid)

            except Exception as e:
                print("Error saving session summary:", e)

        self.status_badge.configure(text="● IDLE", text_color="gray", fg_color=("gray85", "gray20"))
        self.start_btn.configure(text="▶ Start Attendance", fg_color="#10b981", hover_color="#059669")
        self.scan_now_btn.configure(state="disabled")
        self.room_combo.configure(state="normal")
        self.video_lbl.configure(image="", text="Session Ended\nClick 'Start Attendance' to resume")
        self.lbl_faces_count.configure(text="0 Faces")

        total_present = len(self.present_students)
        messagebox.showinfo(
            "Attendance Session Summary",
            f"Session Completed!\n\n"
            f"🎓 Students Marked Present: {total_present} / {self._total_students_count}\n"
            f"👨‍🏫 Faculty: {'Present' if self.teacher_present else 'Absent'}\n"
            f"Records safely logged to MySQL database."
        )

    def stop_camera(self):
        """Called automatically when switching views in the app."""
        if self._running:
            self.stop_session()

    def _clear_roster(self):
        for widget in self.roster_scroll.winfo_children():
            widget.destroy()
        self.present_students.clear()
        self.attendance_records.clear()
        self.teacher_present = False
        self._update_stat_cards()
        self.empty_roster_lbl = ctk.CTkLabel(self.roster_scroll, text="Attendance list cleared.", text_color="gray", font=ctk.CTkFont(size=12))
        self.empty_roster_lbl.pack(pady=30)

    def _add_to_roster_ui(self, roll_no: str, name: str, role: str, conf: float, timestamp_str: str):
        if self.empty_roster_lbl and self.empty_roster_lbl.winfo_exists():
            self.empty_roster_lbl.destroy()

        card = ctk.CTkFrame(self.roster_scroll, corner_radius=6, fg_color=("gray90", "gray18"))
        card.pack(fill="x", pady=3, padx=2)

        icon = "👨‍🏫" if role == "teacher" else "🎓"
        role_color = "#c084fc" if role == "teacher" else "#38bdf8"
        
        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill="x", padx=8, pady=(6, 2))
        ctk.CTkLabel(top_row, text=f"{icon} {name}", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkLabel(top_row, text="PRESENT ✅", font=ctk.CTkFont(size=11, weight="bold"), text_color="#22c55e").pack(side="right")

        sub_row = ctk.CTkFrame(card, fg_color="transparent")
        sub_row.pack(fill="x", padx=8, pady=(0, 6))
        ctk.CTkLabel(sub_row, text=f"ID: {roll_no} | {role.upper()}", font=ctk.CTkFont(size=11), text_color=role_color).pack(side="left")
        ctk.CTkLabel(sub_row, text=f"Match: {int(conf * 100)}% • {timestamp_str}", font=ctk.CTkFont(size=11), text_color="gray").pack(side="right")

    def _process_frame_faces(self, frame_bgr: np.ndarray, force_instant: bool = False):
        if not self.engine or not self.registered_users:
            return []

        try:
            faces = self.engine.try_get_all_faces(frame_bgr)
            if faces is None:
                # Background orchestrator holds the inference lock right
                # now — skip this frame instead of blocking the Tk
                # main-loop thread.
                faces = []
        except Exception:
            return []

        now_str = datetime.now().strftime("%H:%M:%S")
        processed_faces = []

        for face in faces:
            bbox = face.bbox.astype(int)
            emb = face.embedding
            if emb is None:
                continue

            # Match against registered users
            match_res = self.engine.match(emb, self.registered_users)
            if match_res:
                uid, name, role, conf = match_res
                processed_faces.append({
                    "bbox": bbox,
                    "name": name,
                    "uid": uid,
                    "role": role,
                    "conf": conf,
                    "recognized": True
                })

                if role == "teacher":
                    self.teacher_present = True
                    if uid not in self.attendance_records:
                        self.attendance_records[uid] = True
                        if self.session_id:
                            self.db.log_attendance(self.session_id, uid, role, 'present', float(conf))
                        self._add_to_roster_ui(uid, name, role, conf, now_str)
                else: # student
                    current_min = int(time.time()) // 60
                    if uid not in self.student_minutes_present:
                        self.student_minutes_present[uid] = set()
                    self.student_minutes_present[uid].add(current_min)
                    
                    if uid not in self.student_best_conf:
                        self.student_best_conf[uid] = conf
                    else:
                        self.student_best_conf[uid] = max(conf, self.student_best_conf[uid])
                        
                    if uid not in self.present_students:
                        self.present_students.add(uid)
                        self.attendance_records[uid] = True
                        if self.session_id:
                            # Log preliminary present to DB. Will be finalized at stop_session
                            self.db.log_attendance(self.session_id, uid, role, 'present', float(conf))
                        self._add_to_roster_ui(uid, name, role, conf, now_str)
            else:
                processed_faces.append({
                    "bbox": bbox,
                    "name": "Unknown",
                    "uid": "-",
                    "role": "unregistered",
                    "conf": 0.0,
                    "recognized": False
                })

        self._update_stat_cards()
        return processed_faces

    def update_preview(self):
        # Guard against a stray, already-queued .after() callback firing
        # after this frame (or its widgets) has been torn down.
        if not self._running or not self.cap or not self.winfo_exists():
            return

        try:
            ret, frame = self.cap.read()
        except Exception:
            ret, frame = False, None

        if ret:
            # Update elapsed timer
            if self._start_timestamp:
                elapsed_sec = int(time.time() - self._start_timestamp)
                mins = elapsed_sec // 60
                secs = elapsed_sec % 60
                self.lbl_session_time.configure(text=f"{mins:02d}:{secs:02d}")

            # Process faces
            recognized_faces = self._process_frame_faces(frame)
            self.lbl_faces_count.configure(text=f"{len(recognized_faces)} Faces")

            # Draw stylish anti-aliased overlays on frame
            for f in recognized_faces:
                x1, y1, x2, y2 = f["bbox"]
                rec = f["recognized"]
                role = f["role"]
                name = f["name"]
                conf_pct = int(f["conf"] * 100)

                # Colors: Green for student, Purple for teacher, Red/Amber for unknown
                if rec and role == "teacher":
                    box_color = (250, 130, 190) # BGR purple
                    label_text = f"FACULTY: {name} ({conf_pct}%)"
                elif rec:
                    box_color = (90, 210, 35) # BGR green
                    label_text = f"STUDENT: {name} ({conf_pct}%)"
                else:
                    box_color = (70, 70, 240) # BGR red
                    label_text = "UNKNOWN"

                # Draw smooth anti-aliased bounding box
                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2, lineType=cv2.LINE_AA)
                
                # Label backdrop with anti-aliasing
                label_size, _ = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
                lbl_y = max(y1 - 10, label_size[1] + 10)
                cv2.rectangle(frame, (x1, lbl_y - label_size[1] - 4), (x1 + label_size[0] + 8, lbl_y + 4), box_color, -1, lineType=cv2.LINE_AA)
                cv2.putText(frame, label_text, (x1 + 4, lbl_y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2, lineType=cv2.LINE_AA)

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)
            imgtk = ctk.CTkImage(light_image=img, dark_image=img, size=(640, 480))
            try:
                self.video_lbl.configure(image=imgtk, text="")
                self.video_lbl.image = imgtk
            except Exception:
                # Widget was destroyed between the winfo_exists() check
                # above and here (e.g. tab switched mid-frame).
                return

        if self._running and self.winfo_exists():
            self._preview_after_id = self.after(25, self.update_preview)


# ==============================================================================
# SECURE TEACHER MANUAL OVERRIDE & HOD AUDIT TAB
# ==============================================================================

class ManualOverrideDialog(ctk.CTkToplevel):
    """
    Secure modal dialog for faculty manual attendance override.
    Requires selecting an official institutional reason and provides full audit logging.
    """
    def __init__(self, parent, db: DatabaseManager, student_data: Dict[str, Any], session_data: Dict[str, Any], teacher_id: str, on_success_callback=None):
        super().__init__(parent)
        self.title("Secure Manual Attendance Override")
        self.geometry("540x650")
        self.resizable(False, False)
        self.db = db
        self.student = student_data
        self.session = session_data
        self.teacher_id = teacher_id
        self.on_success = on_success_callback
        
        self.transient(parent)
        self.grab_set()
        
        self._build_ui()

    def _build_ui(self):
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=24, pady=20)

        # Header Badge & Title
        ctk.CTkLabel(container, text="✍️ TEACHER MANUAL OVERRIDE", font=ctk.CTkFont(size=11, weight="bold"), text_color="#c084fc").pack(anchor="w")
        ctk.CTkLabel(container, text="Authorize Attendance Exemption", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", pady=(2, 10))

        # Warning / Policy Alert Box
        alert_box = ctk.CTkFrame(container, fg_color=("gray90", "#1e293b"), corner_radius=10)
        alert_box.pack(fill="x", pady=(0, 14))
        alert_text = (
            "⚠️ Policy Notice: Automatic face detection remains the primary attendance source.\n"
            "This manual change will be permanently logged in the HOD/Management Audit Trail "
            "with your instructor ID, exact timestamp, and the reason provided."
        )
        ctk.CTkLabel(alert_box, text=alert_text, font=ctk.CTkFont(size=11), text_color="#94a3b8", justify="left").pack(padx=12, pady=10)

        # Student & Session Summary Box
        info_card = ctk.CTkFrame(container, corner_radius=10)
        info_card.pack(fill="x", pady=(0, 14))
        
        s_name = self.student.get("name", "Student")
        s_roll = self.student.get("roll_no", "-")
        orig_stat = str(self.student.get("original_auto_status", "absent")).upper()
        subj = self.session.get("subject", "General Class")
        dt_str = self.session.get("date", "-")
        t_str = f"{self.session.get('start_time', '-')} - {self.session.get('end_time', '-')}"

        ctk.CTkLabel(info_card, text=f"🎓 Student: {s_name} ({s_roll})", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(info_card, text=f"📅 Class: {subj} | Date: {dt_str} ({t_str})", font=ctk.CTkFont(size=12), text_color="#38bdf8").pack(anchor="w", padx=14, pady=2)
        ctk.CTkLabel(info_card, text=f"🔍 Original System Decision: {orig_stat} 🔴  ➡️  New Status: PRESENT 🟢", font=ctk.CTkFont(size=12, weight="bold"), text_color="#22c55e").pack(anchor="w", padx=14, pady=(2, 10))

        # Reason Selector
        ctk.CTkLabel(container, text="1. Select Official Reason / Exemption Category: *", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(6, 4))
        
        reason_categories = [
            "Medical Leave (Verified Doctor Certificate / Prescription)",
            "On-Duty (OD) / Official Inter-College Event Participation",
            "Approved Departmental / Laboratory Research Activity",
            "Verified Technical Issue / Camera Hardware Glitch",
            "Special Academic Exemption Approved by HOD",
            "Other Genuine Academic Ground (Specify Details Below)"
        ]
        
        self.reason_combo = ctk.CTkOptionMenu(container, values=reason_categories, width=480)
        self.reason_combo.pack(fill="x", pady=(0, 10))

        # Additional Reason Notes / Explanations
        ctk.CTkLabel(container, text="2. Specific Notes / Documentation Details: *", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(4, 4))
        self.notes_entry = ctk.CTkTextbox(container, height=90, corner_radius=8)
        self.notes_entry.pack(fill="x", pady=(0, 14))
        self.notes_entry.insert("1.0", "Medical certificate submitted to instructor; student was present in campus.")

        # Teacher Confirmation PIN / Password (optional re-auth check)
        ctk.CTkLabel(container, text="3. Confirm Instructor Passcode / PIN (Security Check):", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w", pady=(2, 4))
        self.pin_entry = ctk.CTkEntry(container, placeholder_text="Enter your password to sign the override", show="*")
        self.pin_entry.pack(fill="x", pady=(0, 18))

        # Buttons Row
        btn_row = ctk.CTkFrame(container, fg_color="transparent")
        btn_row.pack(fill="x", pady=(10, 0))

        ctk.CTkButton(btn_row, text="Cancel", fg_color="transparent", border_width=1, width=120, command=self.destroy).pack(side="left")
        self.submit_btn = ctk.CTkButton(btn_row, text="🔒 Sign & Submit Override", fg_color="#16a34a", hover_color="#15803d", font=ctk.CTkFont(weight="bold"), command=self._submit_override)
        self.submit_btn.pack(side="right", fill="x", expand=True, padx=(12, 0))

    def _submit_override(self):
        category = self.reason_combo.get().strip()
        notes = self.notes_entry.get("1.0", "end").strip()
        pin = self.pin_entry.get().strip()

        if not category:
            messagebox.showerror("Validation Error", "Please select an exemption category reason.")
            return

        if not notes:
            messagebox.showerror("Validation Error", "Please enter specific notes/details explaining the manual override reason.")
            return

        full_reason = f"{category}: {notes}"
        session_id = self.session.get("session_id")
        roll_no = self.student.get("roll_no")

        res = self.db.apply_manual_override(
            session_id=session_id,
            roll_no=roll_no,
            new_status="present",
            teacher_id=self.teacher_id,
            reason=full_reason,
            password=pin if pin else None,
            is_admin=(self.teacher_id.strip().casefold() == os.environ.get("ADMIN_ID", "ADMIN").strip().casefold())
        )

        if not res.get("success"):
            messagebox.showerror("Override Failed", res.get("error", "Failed to apply manual override."))
            return

        messagebox.showinfo("Override Authorized", res.get("message", "Attendance override successfully recorded in permanent audit trail."))
        if self.on_success:
            self.on_success()
        self.destroy()


class OverrideAuditTab(ctk.CTkScrollableFrame):
    """
    Dedicated Teacher Manual Attendance Override Panel & HOD Audit Trail System.
    """
    def __init__(self, master, db: DatabaseManager):
        super().__init__(master, fg_color="transparent")
        self.db = db
        self.current_teacher_id = None
        self.current_teacher_name = None
        self.authenticated = False
        self.active_session_data = None
        self.teacher_sessions = []
        
        self.grid_columnconfigure(0, weight=1)
        self._build_ui()
        self._load_teachers()

    def _build_ui(self):
        # Header Row
        header_row = ctk.CTkFrame(self, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, 16))

        ctk.CTkLabel(header_row, text="🛡️ Faculty Override & HOD Audit Portal", font=ctk.CTkFont(size=24, weight="bold")).pack(side="left")
        self.audit_count_badge = ctk.CTkLabel(header_row, text="● 0 Overrides Logged", font=ctk.CTkFont(size=12, weight="bold"), text_color="#c084fc", fg_color=("gray85", "gray20"), corner_radius=6, padx=10, pady=4)
        self.audit_count_badge.pack(side="right", padx=10)

        # Mode Selector (Tabs)
        self.mode_selector = ctk.CTkSegmentedButton(
            self, 
            values=["👨‍🏫 Teacher Manual Override Panel", "🛡️ HOD & Management Audit Trail"],
            command=self._switch_mode,
            height=36,
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.mode_selector.set("👨‍🏫 Teacher Manual Override Panel")
        self.mode_selector.pack(fill="x", pady=(0, 16))

        # Dynamic Content Container
        self.content_container = ctk.CTkFrame(self, fg_color="transparent")
        self.content_container.pack(fill="both", expand=True)

        self._render_teacher_override_view()

    def _switch_mode(self, mode: str):
        for widget in self.content_container.winfo_children():
            widget.destroy()

        if "Teacher Manual Override" in mode:
            self._render_teacher_override_view()
        else:
            self._render_hod_audit_view()

    def _load_teachers(self):
        try:
            users = self.db.get_all_users()
            self.teachers_list = [u for u in users if u.get("role") == "teacher"]
            # Also add Admin / HOD accounts if configured
            creds = self.db.get_authorized_credentials()
            for c in creds:
                if c.get("role") == "teacher" and not any(t["roll_no"] == c["id_number"] for t in self.teachers_list):
                    self.teachers_list.append({"roll_no": c["id_number"], "name": c.get("allocated_name") or f"Teacher {c['id_number']}"})
        except Exception:
            self.teachers_list = []

    # ==========================================================================
    # VIEW 1: TEACHER OVERRIDE WORKSPACE
    # ==========================================================================
    def _render_teacher_override_view(self):
        # 1. Authentication Bar Card
        auth_card = ctk.CTkFrame(self.content_container, corner_radius=12)
        auth_card.pack(fill="x", pady=(0, 16))

        a_hdr = ctk.CTkFrame(auth_card, fg_color="transparent")
        a_hdr.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(a_hdr, text="🔑 Instructor Authentication & Class Access", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        
        self.auth_badge = ctk.CTkLabel(
            a_hdr, 
            text="🔒 LOCKED — Please Authenticate" if not self.authenticated else f"🔓 AUTHENTICATED: {self.current_teacher_name} ({self.current_teacher_id})", 
            font=ctk.CTkFont(size=12, weight="bold"), 
            text_color="#f59e0b" if not self.authenticated else "#22c55e"
        )
        self.auth_badge.pack(side="right")

        auth_inputs = ctk.CTkFrame(auth_card, fg_color="transparent")
        auth_inputs.pack(fill="x", padx=16, pady=(0, 14))

        teacher_opts = [f"{t['name']} ({t['roll_no']})" for t in self.teachers_list] if self.teachers_list else ["No Teachers Registered"]
        teacher_opts.append("HOD / Administrator (All Classes)")

        ctk.CTkLabel(auth_inputs, text="Teacher ID:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(0, 6))
        self.teacher_combo = ctk.CTkOptionMenu(auth_inputs, values=teacher_opts, width=220)
        self.teacher_combo.pack(side="left", padx=4)

        ctk.CTkLabel(auth_inputs, text="Password / PIN:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(12, 6))
        self.auth_pass_entry = ctk.CTkEntry(auth_inputs, placeholder_text="Instructor Passcode", show="*", width=150)
        self.auth_pass_entry.pack(side="left", padx=4)

        self.btn_auth = ctk.CTkButton(auth_inputs, text="🔓 Unlock Classes", width=130, fg_color="#0284c7", hover_color="#0369a1", command=self._authenticate_teacher)
        self.btn_auth.pack(side="left", padx=10)

        if self.authenticated:
            ctk.CTkButton(auth_inputs, text="Sign Out", width=80, fg_color="#ef4444", hover_color="#dc2626", command=self._sign_out).pack(side="left", padx=4)

        # 2. Class / Session Selection Card
        self.session_select_card = ctk.CTkFrame(self.content_container, corner_radius=12)
        self.session_select_card.pack(fill="x", pady=(0, 16))

        s_hdr = ctk.CTkFrame(self.session_select_card, fg_color="transparent")
        s_hdr.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(s_hdr, text="📅 Select Assigned Class Session", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")

        s_ctrl = ctk.CTkFrame(self.session_select_card, fg_color="transparent")
        s_ctrl.pack(fill="x", padx=16, pady=(0, 14))

        if not self.authenticated:
            ctk.CTkLabel(s_ctrl, text="🔒 Sign in with your Instructor ID above to load the classes assigned strictly to you.", text_color="gray", font=ctk.CTkFont(size=12)).pack(anchor="w")
        else:
            self.teacher_sessions = self.db.get_teacher_sessions(self.current_teacher_id)
            if not self.teacher_sessions:
                ctk.CTkLabel(s_ctrl, text=f"No class attendance sessions found for instructor '{self.current_teacher_id}'.", text_color="orange", font=ctk.CTkFont(size=12)).pack(anchor="w")
            else:
                session_labels = []
                self.session_map = {}
                for s in self.teacher_sessions:
                    lbl = f"{s.get('date', '-')} | {s.get('subject', 'Class')} ({s.get('start_time', '-')}-{s.get('end_time', '-')}) | Room: {s.get('room_id', '-')}"
                    session_labels.append(lbl)
                    self.session_map[lbl] = s

                ctk.CTkLabel(s_ctrl, text="Session:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=(0, 6))
                self.session_combo = ctk.CTkOptionMenu(s_ctrl, values=session_labels, width=380, command=self._on_session_picked)
                self.session_combo.pack(side="left", padx=4)

                ctk.CTkButton(s_ctrl, text="🔄 Refresh Roster", width=120, fg_color="transparent", border_width=1, command=self._refresh_roster).pack(side="left", padx=10)

        # 3. Student Roster & Attendance Overrides Container
        self.roster_card = ctk.CTkFrame(self.content_container, corner_radius=12)
        self.roster_card.pack(fill="both", expand=True, pady=(0, 10))

        self._render_session_roster()

    def _authenticate_teacher(self):
        sel = self.teacher_combo.get()
        pw = self.auth_pass_entry.get().strip()

        if "HOD / Administrator" in sel:
            t_id = os.environ.get("ADMIN_ID", "ADMIN").strip()
            t_name = os.environ.get("ADMIN_NAME", "HOD / Administrator").strip()
            cred = self.db.verify_credential(t_id, pw, "admin")
            if not cred.get("valid"):
                messagebox.showerror("Authentication Failed", cred.get("error", "Administrator authentication failed."))
                return
        else:
            # Extract ID from "Name (ID)"
            if "(" in sel and ")" in sel:
                t_id = sel.split("(")[-1].replace(")", "").strip()
                t_name = sel.split("(")[0].strip()
            else:
                t_id = sel.strip()
                t_name = sel.strip()

        # Check credentials if credentials table has password for this teacher
        if t_id != "ADMIN":
            cred = self.db.verify_credential(t_id, pw, "teacher")
            if not cred.get("valid"):
                messagebox.showerror("Authentication Failed", cred.get("error", "Incorrect Instructor Password."))
                return
            if cred.get("name"):
                t_name = cred["name"]

        self.current_teacher_id = t_id
        self.current_teacher_name = t_name
        self.authenticated = True
        messagebox.showinfo("Authentication Successful", f"Welcome, {self.current_teacher_name}!\nAssigned classes have been unlocked.")
        self._switch_mode("👨‍🏫 Teacher Manual Override Panel")

    def _sign_out(self):
        self.authenticated = False
        self.current_teacher_id = None
        self.current_teacher_name = None
        self.active_session_data = None
        self._switch_mode("👨‍🏫 Teacher Manual Override Panel")

    def _on_session_picked(self, choice):
        if hasattr(self, "session_map") and choice in self.session_map:
            self.active_session_data = self.session_map[choice]
            self._render_session_roster()

    def _refresh_roster(self):
        self._render_session_roster()

    def _render_session_roster(self):
        for widget in self.roster_card.winfo_children():
            widget.destroy()

        if not self.authenticated or not self.teacher_sessions:
            ctk.CTkLabel(self.roster_card, text="Authenticate and select a class session to view student attendance and issue overrides.", text_color="gray", font=ctk.CTkFont(size=13)).pack(padx=20, pady=30)
            return

        if not self.active_session_data:
            self.active_session_data = self.teacher_sessions[0]

        session_id = self.active_session_data.get("session_id")
        subj = self.active_session_data.get("subject", "Class")
        dt_str = self.active_session_data.get("date", "-")
        t_str = f"{self.active_session_data.get('start_time', '-')} - {self.active_session_data.get('end_time', '-')}"

        r_hdr = ctk.CTkFrame(self.roster_card, fg_color="transparent")
        r_hdr.pack(fill="x", padx=16, pady=(14, 10))
        ctk.CTkLabel(r_hdr, text=f"📋 Class Attendance Roster: {subj} ({dt_str} {t_str})", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")

        students = self.db.get_session_students_for_override(session_id)

        if not students:
            ctk.CTkLabel(self.roster_card, text="No student records found for this session yet.", text_color="gray", font=ctk.CTkFont(size=12)).pack(padx=20, pady=20)
            return

        # Summary KPIs in roster
        present_cnt = sum(1 for s in students if s.get("current_status") == "present")
        warning_cnt = sum(1 for s in students if s.get("current_status") == "warning")
        absent_cnt = sum(1 for s in students if s.get("current_status") == "absent")
        override_cnt = sum(1 for s in students if s.get("is_override") == 1)

        kpi_row = ctk.CTkFrame(self.roster_card, fg_color="transparent")
        kpi_row.pack(fill="x", padx=16, pady=(0, 10))
        ctk.CTkLabel(kpi_row, text=f"Present: {present_cnt} 🟢  |  Warnings: {warning_cnt} 🟡  |  Absent: {absent_cnt} 🔴  |  Manual Overrides: {override_cnt} ✍️", font=ctk.CTkFont(size=12, weight="bold"), text_color="#38bdf8").pack(anchor="w")

        # Roster list
        for s in students:
            s_card = ctk.CTkFrame(self.roster_card, corner_radius=8, fg_color=("gray90", "gray18"))
            s_card.pack(fill="x", padx=16, pady=4)

            s_left = ctk.CTkFrame(s_card, fg_color="transparent")
            s_left.pack(side="left", padx=12, pady=8)

            stat = str(s.get("current_status", "absent")).lower()
            stat_color = "#22c55e" if stat == "present" else ("#f59e0b" if stat == "warning" else "#ef4444")
            
            is_ovr = s.get("is_override", 0) == 1
            ovr_badge = " [OVERRIDE AUTHORIZED ✍️]" if is_ovr else ""

            ctk.CTkLabel(s_left, text=f"🎓 {s.get('name', 'Student')} (ID: {s.get('roll_no', '-')}){ovr_badge}", font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
            
            orig_stat = s.get("original_auto_status", stat)
            sub_text = f"Status: {stat.upper()}  |  Auto-Scan Decision: {orig_stat.upper()}"
            if is_ovr and s.get("override_reason"):
                sub_text += f"  |  Reason: {s.get('override_reason')}"
            ctk.CTkLabel(s_left, text=sub_text, font=ctk.CTkFont(size=11), text_color=stat_color).pack(anchor="w")

            s_right = ctk.CTkFrame(s_card, fg_color="transparent")
            s_right.pack(side="right", padx=12, pady=8)

            # Override button for Absent or Warning students
            def make_override_cmd(student_dict=s):
                return lambda: self._open_override_dialog(student_dict)

            if stat != "present" or is_ovr:
                btn_text = "✏️ Override to Present" if stat != "present" else "✏️ Update Override"
                btn_col = "#16a34a" if stat != "present" else "#7c3aed"
                ctk.CTkButton(s_right, text=btn_text, width=150, height=28, fg_color=btn_col, command=make_override_cmd()).pack(side="right")
            else:
                ctk.CTkLabel(s_right, text="✅ Verified Present", font=ctk.CTkFont(size=12, weight="bold"), text_color="#22c55e").pack(side="right", padx=10)

    def _open_override_dialog(self, student_dict: Dict[str, Any]):
        ManualOverrideDialog(
            self,
            self.db,
            student_dict,
            self.active_session_data,
            self.current_teacher_id,
            on_success_callback=self._render_session_roster
        )

    # ==========================================================================
    # VIEW 2: HOD & MANAGEMENT AUDIT TRAIL
    # ==========================================================================
    def _render_hod_audit_view(self):
        # Header & Export Controls
        audit_hdr = ctk.CTkFrame(self.content_container, corner_radius=12)
        audit_hdr.pack(fill="x", pady=(0, 16))

        h_top = ctk.CTkFrame(audit_hdr, fg_color="transparent")
        h_top.pack(fill="x", padx=16, pady=(12, 6))
        ctk.CTkLabel(h_top, text="🛡️ HOD & Management Permanent Audit Trail", font=ctk.CTkFont(size=17, weight="bold")).pack(side="left")
        ctk.CTkLabel(h_top, text="🔒 IMMUTABLE INSTITUTIONAL AUDIT", font=ctk.CTkFont(size=11, weight="bold"), text_color="#c084fc").pack(side="right")

        ctk.CTkLabel(audit_hdr, text="Transparent, chronological history of all manual attendance overrides. Inspect which instructor changed which student's attendance, exact timestamps, original system decisions, and reasons.", font=ctk.CTkFont(size=12), text_color="#cbd5e1").pack(anchor="w", padx=16, pady=(0, 10))

        # Search / Filter Bar
        f_bar = ctk.CTkFrame(audit_hdr, fg_color="transparent")
        f_bar.pack(fill="x", padx=16, pady=(0, 14))

        self.audit_search_entry = ctk.CTkEntry(f_bar, placeholder_text="🔍 Filter by Student ID, Teacher, or Subject...", width=320)
        self.audit_search_entry.pack(side="left", padx=(0, 10))

        def export_audit_excel():
            df = self.db.get_override_audit_report()
            if df.empty:
                messagebox.showinfo("Export", "No override audit records found to export.")
                return
            filepath = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
            if filepath:
                df.to_excel(filepath, index=False)
                messagebox.showinfo("Success", f"Exported {len(df)} audit logs to Excel!")

        def export_audit_csv():
            df = self.db.get_override_audit_report()
            if df.empty:
                messagebox.showinfo("Export", "No override audit records found to export.")
                return
            filepath = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
            if filepath:
                df.to_csv(filepath, index=False)
                messagebox.showinfo("Success", f"Exported {len(df)} audit logs to CSV!")

        ctk.CTkButton(f_bar, text="Filter", width=80, command=self._render_audit_logs_list).pack(side="left", padx=4)
        ctk.CTkButton(f_bar, text="🔄 Refresh", width=90, fg_color="transparent", border_width=1, command=self._render_audit_logs_list).pack(side="left", padx=4)
        ctk.CTkButton(f_bar, text="📥 Export Excel", fg_color="#16a34a", hover_color="#15803d", width=110, command=export_audit_excel).pack(side="right", padx=(6, 0))
        ctk.CTkButton(f_bar, text="📄 Export CSV", fg_color="#0284c7", hover_color="#0369a1", width=100, command=export_audit_csv).pack(side="right", padx=6)

        # Audit Logs Scroll List
        self.audit_list_frame = ctk.CTkFrame(self.content_container, corner_radius=12)
        self.audit_list_frame.pack(fill="both", expand=True, pady=(0, 10))

        self._render_audit_logs_list()

    def _render_audit_logs_list(self):
        for widget in self.audit_list_frame.winfo_children():
            widget.destroy()

        q = self.audit_search_entry.get().strip().lower() if hasattr(self, "audit_search_entry") else ""
        logs = self.db.get_override_audit_logs()

        if q:
            logs = [
                l for l in logs
                if q in str(l.get("student_name", "")).lower() or
                   q in str(l.get("roll_no", "")).lower() or
                   q in str(l.get("teacher_name", "")).lower() or
                   q in str(l.get("teacher_id", "")).lower() or
                   q in str(l.get("subject", "")).lower() or
                   q in str(l.get("reason", "")).lower()
            ]

        self.audit_count_badge.configure(text=f"● {len(logs)} Overrides Logged")

        if not logs:
            ctk.CTkLabel(self.audit_list_frame, text="No manual attendance overrides recorded in institutional audit log.", font=ctk.CTkFont(size=13), text_color="gray").pack(padx=20, pady=40)
            return

        for l in logs:
            card = ctk.CTkFrame(self.audit_list_frame, corner_radius=10, fg_color=("gray90", "gray18"))
            card.pack(fill="x", padx=16, pady=6)

            # Top Header Row in Card
            c_top = ctk.CTkFrame(card, fg_color="transparent")
            c_top.pack(fill="x", padx=14, pady=(10, 2))
            
            s_name = l.get("student_name", "Student")
            s_id = l.get("roll_no", "-")
            subj = l.get("subject", "-")
            c_dt = l.get("date", "-")
            c_time = f"{l.get('start_time', '-')}-{l.get('end_time', '-')}"
            t_name = l.get("teacher_name", "Teacher")
            t_id = l.get("teacher_id", "-")
            orig = str(l.get("original_status", "absent")).upper()
            new_s = str(l.get("new_status", "present")).upper()
            ovr_time = l.get("overridden_at", "-")
            reason = l.get("reason", "N/A")

            ctk.CTkLabel(c_top, text=f"Audit ID: #{l.get('override_id')}  |  Class: {subj} ({c_dt} {c_time})", font=ctk.CTkFont(size=12, weight="bold"), text_color="#38bdf8").pack(side="left")
            ctk.CTkLabel(c_top, text=f"🕒 Changed At: {ovr_time}", font=ctk.CTkFont(size=11), text_color="gray").pack(side="right")

            # Middle Row: Student & Transition
            c_mid = ctk.CTkFrame(card, fg_color="transparent")
            c_mid.pack(fill="x", padx=14, pady=2)
            ctk.CTkLabel(c_mid, text=f"🎓 Student: {s_name} ({s_id})", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
            ctk.CTkLabel(c_mid, text=f"Original Auto: {orig} 🔴  ➡️  Overridden: {new_s} 🟢", font=ctk.CTkFont(size=12, weight="bold"), text_color="#22c55e").pack(side="left", padx=16)

            # Bottom Row: Teacher & Reason
            c_bot = ctk.CTkFrame(card, fg_color="transparent")
            c_bot.pack(fill="x", padx=14, pady=(2, 10))
            ctk.CTkLabel(c_bot, text=f"👨‍🏫 Authorized By: {t_name} (ID: {t_id})", font=ctk.CTkFont(size=12, weight="bold"), text_color="#c084fc").pack(anchor="w")
            ctk.CTkLabel(c_bot, text=f"📝 Reason: \"{reason}\"", font=ctk.CTkFont(size=12), text_color="#f1f5f9", justify="left").pack(anchor="w", pady=(2, 0))