-- Smart Class Attendance - MySQL 8 schema
CREATE DATABASE IF NOT EXISTS smart_attendance CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE smart_attendance;

CREATE TABLE IF NOT EXISTS users (
  roll_no VARCHAR(100) PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  role ENUM('student','teacher','admin') NOT NULL DEFAULT 'student',
  embedding LONGBLOB NOT NULL,
  multi_embeddings LONGBLOB NULL,
  mesh_path VARCHAR(500) NULL,
  registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS authorized_credentials (
  id_number VARCHAR(100) PRIMARY KEY,
  password VARCHAR(500) NOT NULL,
  role ENUM('student','teacher','admin') NOT NULL,
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
  CONSTRAINT fk_attendance_user FOREIGN KEY(roll_no) REFERENCES users(roll_no) ON DELETE SET NULL ON UPDATE CASCADE ON UPDATE CASCADE
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
