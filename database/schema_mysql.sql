-- Smart Class Attendance - MySQL 8 schema
CREATE DATABASE IF NOT EXISTS smart_attendance CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE smart_attendance;

CREATE TABLE IF NOT EXISTS users (
  roll_no VARCHAR(100) PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  role ENUM('student','teacher','admin') NOT NULL DEFAULT 'student',
  embedding LONGBLOB NULL,
  multi_embeddings LONGBLOB NULL,
  mesh_path VARCHAR(500) NULL,
  registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS authorized_credentials (
  id_number VARCHAR(100) PRIMARY KEY,
  password VARCHAR(500) NOT NULL,
  role VARCHAR(20) NOT NULL,
  allocated_name VARCHAR(255) NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rooms (
  room_id VARCHAR(100) PRIMARY KEY,
  room_name VARCHAR(255) NOT NULL,
  camera_source VARCHAR(1000) NOT NULL
);

CREATE TABLE IF NOT EXISTS room_camera_config (
  room_id VARCHAR(100) PRIMARY KEY,
  teacher_zone_x1 DOUBLE NOT NULL DEFAULT 0.0,
  teacher_zone_y1 DOUBLE NOT NULL DEFAULT 0.0,
  teacher_zone_x2 DOUBLE NOT NULL DEFAULT 1.0,
  teacher_zone_y2 DOUBLE NOT NULL DEFAULT 1.0,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_room_camera_config_room FOREIGN KEY(room_id) REFERENCES rooms(room_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS timetable (
  id INT AUTO_INCREMENT PRIMARY KEY,
  day_of_week VARCHAR(20) NOT NULL,
  start_time VARCHAR(20) NOT NULL,
  end_time VARCHAR(20) NOT NULL,
  subject VARCHAR(255) NOT NULL,
  teacher_id VARCHAR(255) NOT NULL,
  room_id VARCHAR(100) NOT NULL,
  INDEX idx_timetable_day_time(day_of_week,start_time),
  INDEX idx_timetable_teacher(teacher_id),
  INDEX idx_timetable_room(room_id),
  CONSTRAINT fk_timetable_room FOREIGN KEY(room_id) REFERENCES rooms(room_id) ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id VARCHAR(150) PRIMARY KEY,
  timetable_id INT NULL,
  date VARCHAR(30) NULL,
  status VARCHAR(30) DEFAULT 'active',
  subject VARCHAR(255) NULL,
  started_at VARCHAR(50) NULL,
  ended_at VARCHAR(50) NULL,
  total_scans INT DEFAULT 0,
  scan_interval_mins INT NULL,
  duration_mins INT NULL,
  teacher_suspended TINYINT DEFAULT 0,
  INDEX idx_sessions_date(date),
  CONSTRAINT fk_sessions_timetable FOREIGN KEY(timetable_id) REFERENCES timetable(id) ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS attendance_log (
  log_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(150) NULL,
  roll_no VARCHAR(100) NULL,
  role VARCHAR(30) NULL,
  timestamp VARCHAR(50) NULL,
  status VARCHAR(30) NULL,
  confidence DOUBLE NULL,
  is_override TINYINT DEFAULT 0,
  override_reason TEXT NULL,
  original_auto_status VARCHAR(30) NULL,
  INDEX idx_attendance_session_student(session_id,roll_no),
  INDEX idx_attendance_student(roll_no),
  CONSTRAINT fk_attendance_session FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE SET NULL,
  CONSTRAINT fk_attendance_user FOREIGN KEY(roll_no) REFERENCES users(roll_no) ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS attendance_overrides (
  override_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(150) NOT NULL,
  timetable_id INT NULL,
  date VARCHAR(30) NOT NULL,
  start_time VARCHAR(20) NULL,
  end_time VARCHAR(20) NULL,
  subject VARCHAR(255) NOT NULL,
  roll_no VARCHAR(100) NOT NULL,
  student_name VARCHAR(255) NOT NULL,
  original_status VARCHAR(30) NOT NULL,
  new_status VARCHAR(30) NOT NULL,
  teacher_id VARCHAR(100) NOT NULL,
  teacher_name VARCHAR(255) NOT NULL,
  reason TEXT NOT NULL,
  overridden_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_override_date(date),
  CONSTRAINT fk_override_session FOREIGN KEY(session_id) REFERENCES sessions(session_id),
  CONSTRAINT fk_override_user FOREIGN KEY(roll_no) REFERENCES users(roll_no) ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS grace_records (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  roll_no VARCHAR(100) NOT NULL,
  date VARCHAR(30) NOT NULL,
  from_class_id INT NULL,
  target_next_timetable_id INT NULL,
  room_id VARCHAR(100) NULL,
  grace_minutes DOUBLE DEFAULT 5.0,
  is_used TINYINT DEFAULT 0,
  used_in_session_id VARCHAR(150) NULL,
  granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_grace_student_date(roll_no,date)
);

CREATE TABLE IF NOT EXISTS grace_time (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  roll_no VARCHAR(100) NULL,
  session_id VARCHAR(150) NULL,
  granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scan_logs (
  log_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(150) NULL,
  scan_index INT NULL,
  scanned_at VARCHAR(50) NULL,
  roll_no VARCHAR(100) NULL,
  confidence DOUBLE NULL,
  INDEX idx_scan_session(session_id)
);

CREATE TABLE IF NOT EXISTS attendance_report (
  report_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(150) NULL,
  roll_no VARCHAR(100) NULL,
  student_name VARCHAR(255) NULL,
  scans_attended INT NULL,
  total_scans INT NULL,
  percentage DOUBLE NULL,
  status VARCHAR(30) NULL
);


CREATE TABLE IF NOT EXISTS teacher_attendance (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(150) NOT NULL,
  teacher_id VARCHAR(100) NOT NULL,
  date VARCHAR(30) NOT NULL,
  counted_start VARCHAR(50) NULL,
  counted_end VARCHAR(50) NULL,
  first_seen VARCHAR(50) NULL,
  last_seen VARCHAR(50) NULL,
  present_minutes DOUBLE NOT NULL DEFAULT 0,
  absent_minutes DOUBLE NOT NULL DEFAULT 0,
  longest_absence_minutes DOUBLE NOT NULL DEFAULT 0,
  status VARCHAR(40) NOT NULL DEFAULT 'absent',
  absence_over_20m TINYINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_teacher_attendance_session(session_id, teacher_id),
  INDEX idx_teacher_attendance_date(date),
  CONSTRAINT fk_teacher_attendance_session FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE ON UPDATE CASCADE
);


-- ===== Schema v3: configuration, cameras, sections, event/audit tables =====
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
  setting_key VARCHAR(100) PRIMARY KEY,
  setting_value VARCHAR(500) NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sections (
  section_id VARCHAR(100) PRIMARY KEY,
  section_name VARCHAR(255) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS student_sections (
  section_id VARCHAR(100) NOT NULL,
  roll_no VARCHAR(100) NOT NULL,
  PRIMARY KEY (section_id, roll_no),
  INDEX idx_student_sections_roll(roll_no),
  CONSTRAINT fk_ss_section FOREIGN KEY(section_id) REFERENCES sections(section_id) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_ss_user FOREIGN KEY(roll_no) REFERENCES users(roll_no) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS cameras (
  camera_id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  camera_type VARCHAR(20) NOT NULL DEFAULT 'usb',
  source VARCHAR(1000) NOT NULL DEFAULT '',
  room_id VARCHAR(100) NULL,
  enabled TINYINT NOT NULL DEFAULT 1,
  status VARCHAR(20) NOT NULL DEFAULT 'UNKNOWN',
  last_ok_at DATETIME NULL,
  last_error VARCHAR(500) NULL,
  reconnect_count INT NOT NULL DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_camera_name(name),
  INDEX idx_camera_room(room_id),
  CONSTRAINT fk_camera_room FOREIGN KEY(room_id) REFERENCES rooms(room_id) ON DELETE SET NULL ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS session_events (
  event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(150) NOT NULL,
  event_type VARCHAR(40) NOT NULL,
  from_state VARCHAR(30) NULL,
  to_state VARCHAR(30) NULL,
  detail VARCHAR(500) NULL,
  occurred_at DATETIME(3) NOT NULL,
  INDEX idx_session_events(session_id, occurred_at),
  CONSTRAINT fk_session_events_session FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS recognition_events (
  event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(150) NOT NULL,
  roll_no VARCHAR(100) NOT NULL,
  role VARCHAR(30) NOT NULL,
  camera_id INT NULL,
  detected_at DATETIME(3) NOT NULL,
  confidence DOUBLE NULL,
  UNIQUE KEY uq_recognition(session_id, roll_no, detected_at),
  INDEX idx_recognition_session(session_id, role),
  CONSTRAINT fk_recognition_session FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS session_unmeasurable (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(150) NOT NULL,
  started_at DATETIME(3) NOT NULL,
  ended_at DATETIME(3) NULL,
  reason VARCHAR(255) NULL,
  UNIQUE KEY uq_unmeasurable(session_id, started_at),
  CONSTRAINT fk_unmeasurable_session FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS teacher_flags (
  flag_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(150) NOT NULL,
  teacher_id VARCHAR(100) NOT NULL,
  flag_type VARCHAR(40) NOT NULL,
  started_at DATETIME(3) NOT NULL,
  ended_at DATETIME(3) NULL,
  minutes DOUBLE NOT NULL DEFAULT 0,
  UNIQUE KEY uq_teacher_flag(session_id, flag_type, started_at),
  CONSTRAINT fk_teacher_flags_session FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS session_student_summary (
  session_id VARCHAR(150) NOT NULL,
  roll_no VARCHAR(100) NOT NULL,
  present_minutes DOUBLE NOT NULL DEFAULT 0,
  measurable_minutes DOUBLE NOT NULL DEFAULT 0,
  percentage DOUBLE NULL,
  status VARCHAR(30) NOT NULL,
  computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (session_id, roll_no),
  CONSTRAINT fk_summary_session FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS timetable_drafts (
  draft_id CHAR(32) PRIMARY KEY,
  filename VARCHAR(255) NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  rows_json LONGTEXT NOT NULL,
  issues_json LONGTEXT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'draft'
);

CREATE TABLE IF NOT EXISTS timetable_archive (
  archive_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  reason VARCHAR(255) NULL,
  data_json LONGTEXT NOT NULL
);
