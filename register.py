"""
Secure KYC Registration Portal for Smart Class Attendance System
Supports separate portals for Students (/student) and Faculty/Teachers (/teacher)
Features:
 - Bank-Grade KYC 5-direction head-turning face scan (FRONT -> UP -> DOWN -> LEFT -> RIGHT)
 - WiFi-style authorized ID & Passcode verification (separate for student & faculty)
 - Resilient mobile camera permission handling with HTTPS / Secure Context checks
 - User-gesture camera activation button & native camera capture fallback
 - Database integration with the MySQL-backed DatabaseManager saving multi-embeddings to `users`

NOTE: pose maths + the scan state machine (KYCSession) now live in kyc_core.py
      so they can be unit-tested with pytest.
"""
from __future__ import annotations

import os
import sys
import secrets
import cv2
import time
import math
import threading
import pickle
import numpy as np
import socket
from datetime import datetime
from pathlib import Path
from ipaddress import ip_address
from typing import Optional, List, Dict, Any

import uvicorn
from fastapi import FastAPI, Form, File, UploadFile, Request, Depends, Cookie, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

try:
    from insightface.app import FaceAnalysis
    INSIGHTFACE_AVAILABLE = True
except ImportError:
    INSIGHTFACE_AVAILABLE = False

from backend.core.db import DatabaseManager
from kyc_core import STEPS, FACE_MODEL, head_pose, check_duplicate_face, KYCSession


# ==============================================================================
# FACE ENGINE
# ==============================================================================
class RegistrationFaceEngine:
    def __init__(self):
        self.app = None
        self.ready = False
        if INSIGHTFACE_AVAILABLE:
            try:
                base_dir = Path(__file__).parent
                models_dir = str(base_dir / "models")
                self.app = FaceAnalysis(name=FACE_MODEL, root=models_dir, providers=["CPUExecutionProvider"])
                self.app.prepare(ctx_id=0, det_size=(640, 640))
                self.ready = True
                print(f"[INFO] InsightFace {FACE_MODEL} initialized for KYC Registration.")
            except Exception as e:
                print(f"[WARN] InsightFace init fallback: {e}")
                try:
                    self.app = FaceAnalysis(name=FACE_MODEL, providers=["CPUExecutionProvider"])
                    self.app.prepare(ctx_id=0, det_size=(640, 640))
                    self.ready = True
                except Exception as e2:
                    print(f"[ERROR] Could not load InsightFace: {e2}")

    def analyze(self, img_bgr):
        if not self.ready or self.app is None:
            return []
        out = []
        for f in self.app.get(img_bgr):
            if f.kps is None:
                continue
            yaw, pitch, roll = head_pose(f.kps)
            norm = np.linalg.norm(f.embedding)
            emb = f.embedding / (norm + 1e-9)
            out.append({
                "bbox": f.bbox.astype(int),
                "det_score": float(f.det_score),
                "norm": float(norm),
                "embedding": emb.astype(np.float32),
                "yaw": yaw, "pitch": pitch, "roll": roll,
            })
        return out


class KYCManager:
    MAX_SESSIONS = 250
    SESSION_TTL = 600

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.engine = RegistrationFaceEngine()
        self.engine_lock = threading.Lock()
        self.sessions = {}
        self.sessions_lock = threading.Lock()

    def get(self, sid: str) -> Optional[KYCSession]:
        sid = (sid or "")[:64]
        with self.sessions_lock:
            now = time.time()
            for k in [k for k, v in self.sessions.items() if now - v.last_seen > self.SESSION_TTL]:
                del self.sessions[k]
            if sid not in self.sessions:
                if len(self.sessions) >= self.MAX_SESSIONS:
                    return None
                self.sessions[sid] = KYCSession(self.db)
            return self.sessions[sid]

    def analyze(self, img):
        with self.engine_lock:
            return self.engine.analyze(img)


# Global instances (will be bound in factory or module load)
_BASE_DIR = Path(__file__).parent
_DEFAULT_DB = DatabaseManager(_BASE_DIR)
manager = KYCManager(_DEFAULT_DB)

app = FastAPI(title="Smart Attendance KYC Registration")


# ==============================================================================
# HTML TEMPLATE BUILDER FOR STUDENT AND FACULTY
# ==============================================================================
def render_kyc_html(role: str) -> str:
    is_teacher = (role == "teacher")
    theme_color = "#c084fc" if is_teacher else "#38bdf8"
    theme_accent = "#818cf8" if is_teacher else "#0284c7"
    portal_badge = "👨‍🏫 Faculty / Teacher Registration" if is_teacher else "🎓 Student Registration"
    portal_title = "Faculty Biometric KYC Enrollment" if is_teacher else "Student Biometric KYC Enrollment"
    id_label = "Faculty / Employee ID:" if is_teacher else "Student Roll Number / ID:"
    id_placeholder = "e.g. FAC-101" if is_teacher else "e.g. 1 or STU-101"
    pass_label = "Faculty Passcode (Secret):" if is_teacher else "Student Access Passcode:"
    pass_placeholder = "Enter WiFi-style Passcode provided by Admin"
    name_label = "Teacher Full Name:" if is_teacher else "Student Full Name:"
    name_placeholder = "e.g. Dr. Alex Smith" if is_teacher else "e.g. Ankush Sharma"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{portal_title} - Smart Attendance</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
    :root {{
        --theme-primary: {theme_color};
        --theme-accent: {theme_accent};
    }}
    body {{
        background: radial-gradient(circle at top, #1e1b4b 0%, #0f172a 70%);
        color: #f8fafc;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        min-height: 100vh;
        margin: 0;
        padding-bottom: 30px;
    }}
    .hero-card {{
        background: rgba(30, 41, 59, 0.85);
        backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 24px;
        box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.6);
    }}
    .role-badge {{
        display: inline-block;
        padding: 6px 16px;
        background: rgba(56, 189, 248, 0.15);
        color: var(--theme-primary);
        border: 1px solid var(--theme-primary);
        border-radius: 30px;
        font-size: 0.9rem;
        font-weight: 700;
        letter-spacing: 0.5px;
    }}
    .stage {{
        position: relative;
        width: min(340px, 86vw);
        aspect-ratio: 1/1;
        margin: 15px auto;
    }}
    .video-circle {{
        position: absolute;
        inset: 14%;
        border-radius: 50%;
        overflow: hidden;
        background: #000;
        box-shadow: inset 0 0 25px rgba(0,0,0,0.8);
    }}
    .video-circle video {{
        width: 100%;
        height: 100%;
        object-fit: cover;
        transform: scaleX(-1);
        display: block;
    }}
    .camera-placeholder {{
        position: absolute;
        inset: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        background: #111827;
        z-index: 5;
        padding: 12px;
        text-align: center;
        border-radius: 50%;
    }}
    .ring {{
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: 10;
    }}
    .front-track {{ fill: none; stroke: #334155; stroke-width: 6; }}
    .front-prog  {{ fill: none; stroke: var(--theme-primary); stroke-width: 6; stroke-linecap: round; transition: stroke-dashoffset .15s linear; }}
    .front-prog.done {{ stroke: #22c55e; }}
    .tick {{ stroke: #334155; stroke-width: 4; stroke-linecap: round; transition: stroke .2s; }}
    .tick.target {{ stroke: var(--theme-primary); opacity: .7; }}
    .tick.on {{ stroke: #22c55e; opacity: 1; }}
    .mk {{ fill: #64748b; font-size: 18px; text-anchor: middle; dominant-baseline: central; font-weight: bold; }}
    .mk.target {{ fill: var(--theme-primary); animation: pulse 1s infinite; }}
    .mk.done {{ fill: #22c55e; }}
    @keyframes pulse {{ 50% {{ opacity: .2; }} }}
    #pct {{ font-size: 2.8rem; font-weight: 800; line-height: 1; color: #fff; }}
    .badge-step {{ font-size: .8rem; padding: 6px 12px; border-radius: 20px; }}
    .camera-notice {{
        background: rgba(245, 158, 11, 0.15);
        border: 1px solid rgba(245, 158, 11, 0.4);
        border-radius: 12px;
        padding: 12px;
        font-size: 0.85rem;
    }}
</style>
</head>
<body>
<div class="container py-3 py-md-4">
  <div class="row justify-content-center">
    <div class="col-lg-6 col-md-9 text-center">
      <div class="hero-card p-3 p-md-4">
        <div class="mb-3">
          <span class="role-badge">{portal_badge}</span>
        </div>
        <h4 class="fw-bold mb-1">{portal_title}</h4>
        <p class="text-secondary small mb-2">5-direction KYC verification. Fill credentials, grant camera permission, and turn head following directions.</p>

        <!-- Insecure context alert (shown if opened via http:// on phone) -->
        <div id="httpsWarning" class="alert alert-warning text-start d-none mb-3">
          <div class="fw-bold">🔒 Camera Requires Secure (HTTPS) Mode</div>
          <div class="small mt-1">Mobile browsers block camera access on plain HTTP. Tap below to switch to the secure HTTPS connection:</div>
          <div class="mt-2 text-center">
            <a id="httpsSwitchBtn" href="#" class="btn btn-warning btn-sm fw-bold">👉 Switch to Secure HTTPS Now</a>
          </div>
          <div class="text-muted" style="font-size:0.75rem; margin-top:6px;">
            ⚠️ If your phone says <i>"Connection is not private"</i>, tap <b>Show Details / Advanced</b> and tap <b>Visit this website / Proceed</b> once to allow camera access.
          </div>
        </div>

        <!-- Camera stage -->
        <div class="stage">
          <div class="video-circle">
            <video id="cam" autoplay playsinline webkit-playsinline muted></video>
            <div id="camOverlay" class="camera-placeholder">
              <span style="font-size:2.2rem; margin-bottom:6px;">📷</span>
              <button class="btn btn-primary btn-sm fw-bold px-3 py-2" id="requestCamBtn" onclick="userRequestCamera()">
                Tap to Enable Camera
              </button>
              <div class="text-secondary mt-1" style="font-size:0.7rem;">Browser permission required</div>
            </div>
          </div>
          <svg class="ring" viewBox="0 0 460 460">
            <circle class="front-track" cx="230" cy="230" r="172"></circle>
            <circle id="frontRing" class="front-prog" cx="230" cy="230" r="172" transform="rotate(-90 230 230)"></circle>
            <g id="ticks"></g>
            <g id="markers"></g>
          </svg>
        </div>

        <div id="pct">0%</div>
        <div class="text-secondary small mb-2">Face Biometric Progress</div>

        <div class="d-flex justify-content-center flex-wrap gap-2 mb-3" id="chips"></div>
        <div id="statusAlert" class="alert alert-info fw-bold py-2 px-3 mb-3 small">
          Please allow camera permission and fill your details below.
        </div>

        <!-- Credentials Form -->
        <div class="card bg-dark border-secondary p-3 mb-3 text-start">
          <div class="mb-2">
            <label class="form-label fw-bold small text-light">{id_label}</label>
            <input type="text" id="rollInput" class="form-control bg-black text-white border-secondary" placeholder="{id_placeholder}" autocomplete="off">
          </div>
          <div class="mb-2">
            <label class="form-label fw-bold small text-light">{pass_label}</label>
            <input type="password" id="passInput" class="form-control bg-black text-white border-secondary" placeholder="{pass_placeholder}">
            <div class="text-secondary" style="font-size:0.72rem; margin-top:2px;">WiFi-style passcode issued by administration.</div>
          </div>
          <div>
            <label class="form-label fw-bold small text-light">{name_label}</label>
            <input type="text" id="nameInput" class="form-control bg-black text-white border-secondary" placeholder="{name_placeholder}">
          </div>
        </div>

        <input type="hidden" id="roleInput" value="{role}">

        <button class="btn btn-success btn-lg w-100 fw-bold py-3 mb-2" id="startBtn" onclick="startKyc()">
          ⚡ Start KYC Biometric Face Scan
        </button>

        <button class="btn btn-outline-light w-100 mb-2 d-none" id="cancelBtn" onclick="cancelKyc()">
          Cancel Scan
        </button>

        <!-- Native camera photo fallback for restricted devices -->
        <div class="mt-3 pt-2 border-top border-secondary">
          <div class="text-secondary small mb-1">Camera stream blocked by browser?</div>
          <input type="file" id="nativePhotoInput" accept="image/*" capture="user" style="display:none;" onchange="handleNativePhoto(event)">
          <button class="btn btn-outline-info btn-sm" onclick="document.getElementById('nativePhotoInput').click()">
            📸 Or Snap Photo with Native Phone Camera
          </button>
        </div>

      </div>
    </div>
  </div>
</div>

<script>
const ROLE = "{role}";
const STEPS = ['FRONT','UP','DOWN','LEFT','RIGHT'];
const ARC = {{UP:0, RIGHT:1, DOWN:2, LEFT:3}};
const ICON = {{UP:'▲', RIGHT:'▶', DOWN:'▼', LEFT:'◀'}};
const LABEL = {{FRONT:'Front', UP:'Up', DOWN:'Down', LEFT:'Left', RIGHT:'Right'}};
const SPEECH = {{
  FRONT:'Look straight at the camera and hold still.',
  UP:'Slowly raise your head up.',
  DOWN:'Now slowly lower your head down.',
  LEFT:'Now turn your head left.',
  RIGHT:'Now turn your head right.'
}};
const NS = 'http://www.w3.org/2000/svg';
const N = 60, PER = 15, CX = 230, CY = 230;
const ticks = [];
let lastCurrent = null, lastPhase = 'idle';
let cameraActive = false;

function buildRing() {{
  const g = document.getElementById('ticks');
  for (let i = 0; i < N; i++) {{
    const deg = i * 6, a = deg * Math.PI / 180;
    const s = Math.sin(a), c = Math.cos(a);
    const ln = document.createElementNS(NS, 'line');
    ln.setAttribute('x1', CX + 186 * s); ln.setAttribute('y1', CY - 186 * c);
    ln.setAttribute('x2', CX + 204 * s); ln.setAttribute('y2', CY - 204 * c);
    ln.setAttribute('class', 'tick');
    g.appendChild(ln);
    const m = (deg + 45) % 360;
    const arc = Math.floor(m / 90);
    ticks.push({{el: ln, arc: arc, k: Math.floor((m - arc * 90) / 6)}});
  }}
  const pos = {{UP:[230,11], RIGHT:[449,230], DOWN:[230,449], LEFT:[11,230]}};
  const mg = document.getElementById('markers');
  for (const d in pos) {{
    const t = document.createElementNS(NS, 'text');
    t.setAttribute('x', pos[d][0]); t.setAttribute('y', pos[d][1]);
    t.setAttribute('class', 'mk'); t.setAttribute('id', 'mk-' + d);
    t.textContent = ICON[d];
    mg.appendChild(t);
  }}
  const chips = document.getElementById('chips');
  STEPS.forEach((s, i) => {{
    const b = document.createElement('span');
    b.id = 'chip-' + s; b.className = 'badge bg-secondary badge-step';
    b.textContent = (i + 1) + '. ' + LABEL[s];
    chips.appendChild(b);
  }});
}}

function speak(text) {{
  if (!text || !('speechSynthesis' in window)) return;
  try {{
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.0;
    window.speechSynthesis.speak(u);
  }} catch(e) {{}}
}}

function render(s) {{
  document.getElementById('pct').textContent = s.percent + '%';

  const C = 2 * Math.PI * 172, fr = document.getElementById('frontRing');
  fr.style.strokeDasharray = C;
  fr.style.strokeDashoffset = C * (1 - s.steps.FRONT.progress);
  fr.classList.toggle('done', s.steps.FRONT.done);

  const dirName = ['UP','RIGHT','DOWN','LEFT'];
  ticks.forEach(t => {{
    const d = dirName[t.arc], st = s.steps[d];
    const on = st.done || t.k < st.progress * PER;
    t.el.classList.toggle('on', on);
    t.el.classList.toggle('target', !on && s.current === d);
  }});
  dirName.forEach(d => {{
    const mk = document.getElementById('mk-' + d), st = s.steps[d];
    mk.textContent = st.done ? '✔' : ICON[d];
    mk.classList.toggle('done', st.done);
    mk.classList.toggle('target', !st.done && s.current === d);
  }});
  STEPS.forEach(k => {{
    const b = document.getElementById('chip-' + k), st = s.steps[k];
    b.className = 'badge badge-step ' + (st.done ? 'bg-success' : (s.current === k ? 'bg-primary' : 'bg-secondary'));
  }});

  let cls = 'alert-info';
  if (s.phase === 'complete') cls = 'alert-success';
  else if (s.phase === 'rejected') cls = 'alert-danger';
  else if (s.code !== 'ok') cls = 'alert-warning';
  const box = document.getElementById('statusAlert');
  box.className = 'alert fw-bold mb-3 small ' + cls;
  box.textContent = s.message;

  const scanning = s.phase === 'scanning';
  document.getElementById('startBtn').disabled = scanning;
  document.getElementById('cancelBtn').classList.toggle('d-none', !scanning);

  if (scanning && s.current !== lastCurrent) speak(SPEECH[s.current]);
  if (lastPhase === 'scanning' && (s.phase === 'complete' || s.phase === 'rejected')) speak(s.message);
  if (lastPhase === 'scanning' && s.phase === 'complete') {{
    document.getElementById('rollInput').value = '';
    document.getElementById('passInput').value = '';
    document.getElementById('nameInput').value = '';
  }}
  lastCurrent = scanning ? s.current : null;
  lastPhase = s.phase;
}}

const SID = (window.crypto && crypto.randomUUID) ? crypto.randomUUID()
                                                  : (String(Math.random()).slice(2) + Date.now());
const video = document.getElementById('cam');
// CAPTURE_MAX_SIDE: Maximum side length of the square capture frame sent
// to the server. Higher = better biometric quality, but larger uploads.
// 1280px captures full HD quality from most mobile cameras (1080p+ native).
// This is NOT scaled down further on the server — InsightFace receives the
// full-resolution frame, so embeddings reflect original camera quality.
const CAPTURE_MAX_SIDE = 1280;
const grab = document.createElement('canvas');
const gctx = grab.getContext('2d');

function showError(msg) {{
  const box = document.getElementById('statusAlert');
  box.className = 'alert alert-danger fw-bold mb-3 small';
  box.textContent = msg;
}}

function checkSecurityContext() {{
  const isHttps = location.protocol === 'https:';
  const isLocalhost = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  if (!isHttps && !isLocalhost) {{
    const warn = document.getElementById('httpsWarning');
    warn.classList.remove('d-none');
    const switchBtn = document.getElementById('httpsSwitchBtn');
    switchBtn.href = 'https://' + location.hostname + ':' + (location.port || '5050') + location.pathname;
  }}
}}

async function userRequestCamera() {{
  const reqBtn = document.getElementById('requestCamBtn');
  reqBtn.disabled = true;
  reqBtn.textContent = 'Connecting...';
  await startCamera();
}}

async function startCamera() {{
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {{
    showError('Camera access requires HTTPS in mobile browsers. Tap "Switch to Secure HTTPS" above.');
    document.getElementById('httpsWarning').classList.remove('d-none');
    return;
  }}

  const constraintsList = [
    {{ video: {{ facingMode: 'user', width: {{ ideal: 1920 }}, height: {{ ideal: 1440 }} }}, audio: false }},
    {{ video: {{ facingMode: 'user', width: {{ ideal: 1280 }}, height: {{ ideal: 960 }} }}, audio: false }},
    {{ video: {{ facingMode: 'user' }}, audio: false }},
    {{ video: true, audio: false }}
  ];

  let stream = null;
  for (const c of constraintsList) {{
    try {{
      stream = await navigator.mediaDevices.getUserMedia(c);
      if (stream) break;
    }} catch(err) {{
      console.warn('Constraint attempt failed:', c, err);
    }}
  }}

  if (stream) {{
    video.srcObject = stream;
    try {{
      await video.play();
    }} catch(e) {{
      console.log('video.play() caught:', e);
    }}
    cameraActive = true;
    document.getElementById('camOverlay').style.display = 'none';
    frameLoop();
  }} else {{
    document.getElementById('camOverlay').style.display = 'flex';
    document.getElementById('requestCamBtn').disabled = false;
    document.getElementById('requestCamBtn').textContent = 'Retry Camera Permission';
    showError('Camera permission was blocked. Please tap the lock icon in your browser address bar and set Camera to Allow.');
  }}
}}

async function frameLoop() {{
  while (cameraActive) {{
    if (video.videoWidth) {{
      // Use the camera's own native resolution for the square crop,
      // instead of forcing everything down to a fixed 320x320 -- the
      // saved face embeddings come straight from this frame, so a
      // higher-quality capture here means better biometric quality,
      // not just a nicer-looking preview.
      const nativeSide = Math.min(video.videoWidth, video.videoHeight);
      const s = Math.min(nativeSide, CAPTURE_MAX_SIDE);
      if (grab.width !== s) {{ grab.width = s; grab.height = s; }}
      gctx.drawImage(video, (video.videoWidth - nativeSide) / 2, (video.videoHeight - nativeSide) / 2, nativeSide, nativeSide, 0, 0, s, s);
      const blob = await new Promise(r => grab.toBlob(r, 'image/jpeg', 0.96));
      const fd = new FormData();
      fd.append('session_id', SID);
      fd.append('frame', blob, 'f.jpg');
      try {{
        const r = await fetch('/api/frame', {{method: 'POST', body: fd}});
        if (!r.ok) {{ showError('Server error while scanning (HTTP ' + r.status + '). Check the server log.'); }}
        else {{ render(await r.json()); }}
      }} catch (e) {{ console.warn('frame upload failed', e); }}
    }}
    await new Promise(r => setTimeout(r, 90));
  }}
}}

async function startKyc() {{
  const roll = document.getElementById('rollInput').value.trim();
  const pass = document.getElementById('passInput').value.trim();
  const name = document.getElementById('nameInput').value.trim();
  const role = document.getElementById('roleInput').value.trim();

  if (!roll || !pass) {{
    showError('Please enter both ID Number and Access Passcode!');
    return;
  }}

  if (!cameraActive) {{
    await startCamera();
    if (!cameraActive) {{
      showError('Please allow camera access above before starting verification!');
      return;
    }}
  }}

  const fd = new FormData();
  fd.append('session_id', SID);
  fd.append('roll_no', roll);
  fd.append('password', pass);
  fd.append('name', name);
  fd.append('role', role);

  try {{
    const res = await fetch('/api/start_kyc_enrollment', {{method: 'POST', body: fd}});
    const data = await res.json();
    if (data.status === 'error') {{
      showError(data.message);
      speak('Authentication error. ' + data.message);
    }} else {{
      speak('Credentials verified. ' + SPEECH['FRONT']);
    }}
  }} catch(err) {{
    showError('Connection error contacting registration server.');
  }}
}}

async function cancelKyc() {{
  const fd = new FormData(); fd.append('session_id', SID);
  await fetch('/api/cancel_kyc', {{method: 'POST', body: fd}});
}}

async function handleNativePhoto(event) {{
  const file = event.target.files[0];
  if (!file) return;
  const roll = document.getElementById('rollInput').value.trim();
  const pass = document.getElementById('passInput').value.trim();
  const name = document.getElementById('nameInput').value.trim();
  const role = document.getElementById('roleInput').value.trim();

  if (!roll || !pass) {{
    showError('Please fill ID Number and Passcode before photo snapshot.');
    return;
  }}

  const fd = new FormData();
  fd.append('session_id', SID);
  fd.append('roll_no', roll);
  fd.append('password', pass);
  fd.append('name', name);
  fd.append('role', role);
  fd.append('photo', file);

  showError('Analyzing selfie snapshot...');
  try {{
    const r = await fetch('/api/snapshot_enroll', {{method: 'POST', body: fd}});
    const d = await r.json();
    if (d.status === 'success') {{
      const box = document.getElementById('statusAlert');
      box.className = 'alert alert-success fw-bold mb-3 small';
      box.textContent = d.message;
      speak('Face registration successful!');
      document.getElementById('rollInput').value = '';
      document.getElementById('passInput').value = '';
      document.getElementById('nameInput').value = '';
    }} else {{
      showError(d.message);
    }}
  }} catch(e) {{
    showError('Upload failed: ' + e);
  }}
}}

// Initialize
buildRing();
checkSecurityContext();
// Try requesting camera smoothly on initial interaction
setTimeout(() => {{
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {{
    startCamera();
  }}
}}, 300);
</script>
</body>
</html>
"""

# ==============================================================================
# HUB PAGE
# ==============================================================================
HUB_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Smart Class Attendance - KYC Registration Hub</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>
    body { background: radial-gradient(circle at top, #1e1b4b 0%, #0f172a 70%); color: #f8fafc; font-family: system-ui, sans-serif; min-height: 100vh; display:flex; align-items:center; }
    .hub-card { background: rgba(30, 41, 59, 0.85); backdrop-filter: blur(16px); border: 1px solid rgba(255,255,255,0.12); border-radius: 24px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.6); }
    .portal-btn { transition: transform 0.2s, box-shadow 0.2s; border-radius: 16px; text-decoration: none; display: block; padding: 22px; }
    .portal-btn:hover { transform: translateY(-4px); box-shadow: 0 12px 24px -6px rgba(0,0,0,0.4); }
    .btn-student { background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%); color: white; border: 1px solid #38bdf8; }
    .btn-teacher { background: linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%); color: white; border: 1px solid #c084fc; }
</style>
</head>
<body>
<div class="container py-5">
  <div class="row justify-content-center">
    <div class="col-lg-6 col-md-8 text-center">
      <div class="hub-card p-4 p-md-5">
        <h2 class="fw-bold mb-2">🏛️ Smart Class Attendance</h2>
        <h5 class="text-info mb-3">Biometric KYC Registration Portal</h5>
        <p class="text-secondary small mb-4">Select your role to access your dedicated biometric registration portal with WiFi-style passcode verification.</p>

        <div class="d-grid gap-3">
          <a href="/student" class="portal-btn btn-student text-start">
            <div class="d-flex align-items-center">
              <span style="font-size:2.4rem;" class="me-3">🎓</span>
              <div>
                <h5 class="fw-bold mb-1">Student Portal</h5>
                <div class="small opacity-75">Student ID & Access Passcode verification</div>
              </div>
            </div>
          </a>

          <a href="/teacher" class="portal-btn btn-teacher text-start">
            <div class="d-flex align-items-center">
              <span style="font-size:2.4rem;" class="me-3">👨‍🏫</span>
              <div>
                <h5 class="fw-bold mb-1">Faculty & Teacher Portal</h5>
                <div class="small opacity-75">Faculty ID & Administrative secret passcode</div>
              </div>
            </div>
          </a>
        </div>

        <div class="mt-4 pt-3 border-top border-secondary text-secondary small">
          Admin access: <a href="/admin" class="text-info text-decoration-none">Administrative KYC Dashboard</a>
        </div>
      </div>
    </div>
  </div>
</div>
</body>
</html>
"""

# ==============================================================================
# FASTAPI ROUTES
# ==============================================================================
@app.get("/", response_class=HTMLResponse)
def hub_page():
    return HTMLResponse(content=HUB_HTML)


@app.get("/student", response_class=HTMLResponse)
def student_portal():
    return HTMLResponse(content=render_kyc_html("student"))


@app.get("/teacher", response_class=HTMLResponse)
def teacher_portal():
    return HTMLResponse(content=render_kyc_html("teacher"))


@app.get("/faculty", response_class=HTMLResponse)
def faculty_portal():
    return HTMLResponse(content=render_kyc_html("teacher"))


@app.post("/api/frame")
def api_frame(session_id: str = Form(...), frame: UploadFile = File(...)):
    sess = manager.get(session_id)
    if sess is None:
        return JSONResponse(content={
            "phase": "rejected", "percent": 0, "current": None, "code": "busy",
            "message": "Server is busy. Please try again.",
            "steps": {st: {"progress": 0, "done": False} for st in STEPS}
        })
    # Accept up to 8MB per frame (original quality 1280x1280 JPEG can be ~1-3MB)
    raw = frame.file.read(8_000_000)
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is not None:
        h, w = img.shape[:2]
        side = min(h, w)
        y0, x0 = (h - side) // 2, (w - side) // 2
        img = img[y0:y0 + side, x0:x0 + side]
        faces = manager.analyze(img)
        try:
            sess.process(faces, side)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[ERROR] KYC frame processing failed: {e}")
    return JSONResponse(content=sess.status())


@app.post("/api/start_kyc_enrollment")
def start_kyc_enrollment(
    session_id: str = Form(...),
    roll_no: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    role: str = Form("student"),
):
    sess = manager.get(session_id)
    if sess is None:
        return JSONResponse(content={"status": "error", "message": "Server is busy. Try again in a minute."})
    return JSONResponse(content=sess.start_session(roll_no=roll_no, name=name, password=password, role=role))


@app.post("/api/cancel_kyc")
def cancel_kyc(session_id: str = Form(...)):
    sess = manager.get(session_id)
    if sess:
        sess.cancel_session()
    return JSONResponse(content={"status": "ok"})


@app.post("/api/snapshot_enroll")
def api_snapshot_enroll(
    session_id: str = Form(...),
    roll_no: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    role: str = Form("student"),
    photo: UploadFile = File(...),
):
    """Fallback endpoint for mobile cameras using native file capture."""
    roll_no = roll_no.strip()
    password = password.strip()
    name = name.strip()
    role = role.strip().lower()

    cred_check = manager.db.verify_credential(id_number=roll_no, password=password, role=role)
    if not cred_check.get("valid", False):
        return JSONResponse(content={
            "status": "error",
            "message": cred_check.get("error", "Invalid ID or Passcode.")
        })

    raw = photo.file.read(5_000_000)
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return JSONResponse(content={"status": "error", "message": "Could not decode uploaded photo."})

    faces = manager.analyze(img)
    if not faces:
        return JSONResponse(content={"status": "error", "message": "No face found in photo. Make sure your face is clearly visible."})
    if len(faces) > 1:
        return JSONResponse(content={"status": "error", "message": "Multiple faces detected. Only one face allowed."})

    emb = faces[0]["embedding"]
    all_users = manager.db.get_all_users()
    dup = check_duplicate_face([emb], all_users)
    if dup:
        return JSONResponse(content={
            "status": "error",
            "message": f"Face matches already registered person '{dup[1]}' (ID: {dup[0]})."
        })

    final_name = name or cred_check.get("name") or roll_no
    manager.db.upsert_user(
        roll_no=roll_no,
        name=final_name,
        role=role,
        embedding=emb,
        multi_embeddings=[emb],
    )
    return JSONResponse(content={
        "status": "success",
        "message": f"Snapshot registered successfully! {final_name} ({roll_no}) enrolled as {role.capitalize()}."
    })


# ==============================================================================
# ADMIN PANEL
# ==============================================================================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_ID = os.environ.get("ADMIN_ID", "ADMIN")
ADMIN_SESSION_SECONDS = 8 * 3600
admin_basic = HTTPBasic(auto_error=True)

ADMIN_PANEL_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Admin Portal - Biometric Registrations</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
<style>body{background:#0f172a;color:#f8fafc}.card{background:#1e293b;border:1px solid #334155;border-radius:16px}
.table{--bs-table-bg:transparent;--bs-table-color:#f8fafc;--bs-table-border-color:#334155}</style>
</head><body><div class="container py-4">
<div class="card p-4">
  <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
    <h4 class="fw-bold mb-0">🛠️ Registered Users <span id="count" class="badge bg-primary ms-2">0</span></h4>
    <div class="d-flex gap-2">
      <a class="btn btn-outline-info btn-sm" href="/student" target="_blank">Student Portal</a>
      <a class="btn btn-outline-warning btn-sm" href="/teacher" target="_blank">Faculty Portal</a>
      <button class="btn btn-outline-light btn-sm" onclick="load()">Refresh</button>
    </div>
  </div>
  <input id="search" class="form-control bg-dark text-white border-secondary mb-3" placeholder="Search ID or Name..." oninput="render()">
  <div class="table-responsive">
    <table class="table align-middle">
      <thead><tr><th>ID / Roll No</th><th>Name</th><th>Role</th><th>Registered</th><th class="text-end">Actions</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
  <div id="empty" class="text-secondary text-center py-3 d-none">No users registered yet.</div>
</div></div>
<script>
let users = [];
async function load() {
  const r = await fetch('/admin/api/users');
  users = await r.json();
  render();
}
function cell(tr, text) { const td = document.createElement('td'); td.textContent = text; tr.appendChild(td); return td; }
function render() {
  const q = document.getElementById('search').value.toLowerCase();
  const body = document.getElementById('rows'); body.textContent = '';
  const list = users.filter(s => s.roll_no.toLowerCase().includes(q) || s.name.toLowerCase().includes(q));
  document.getElementById('count').textContent = users.length;
  document.getElementById('empty').classList.toggle('d-none', list.length > 0);
  list.forEach(s => {
    const tr = document.createElement('tr');
    cell(tr, s.roll_no); cell(tr, s.name); cell(tr, (s.role || 'student').toUpperCase());
    cell(tr, s.registered_at || '-');
    const act = document.createElement('td'); act.className = 'text-end';

    const editBtn = document.createElement('button');
    editBtn.className = 'btn btn-outline-info btn-sm me-2';
    editBtn.textContent = '✏️ Edit';
    editBtn.onclick = () => editUser(s);

    const del = document.createElement('button');
    del.className = 'btn btn-outline-danger btn-sm';
    del.textContent = '🗑️ Delete';
    del.onclick = async () => {
      if (confirm('Delete ' + s.name + ' (' + s.roll_no + ')? This will allow them to re-register.')) {
        const fd = new FormData(); fd.append('roll_no', s.roll_no);
        await fetch('/admin/api/delete', {method: 'POST', body: fd});
        load();
      }
    };
    act.appendChild(editBtn);
    act.appendChild(del);
    tr.appendChild(act);
    body.appendChild(tr);
  });
}

async function editUser(s) {
  const newName = prompt('Edit Full Name for ' + s.roll_no + ':', s.name);
  if (newName === null) return;
  const newRole = prompt('Edit Role (enter "student" or "teacher"):', s.role || 'student');
  if (newRole === null) return;
  const newRoll = prompt('Edit ID / Roll Number:', s.roll_no);
  if (newRoll === null) return;

  const fd = new FormData();
  fd.append('old_roll_no', s.roll_no);
  fd.append('new_roll_no', newRoll.trim() || s.roll_no);
  fd.append('new_name', newName.trim() || s.name);
  fd.append('new_role', (newRole.trim().toLowerCase() === 'teacher') ? 'teacher' : 'student');

  const r = await fetch('/admin/api/modify', {method: 'POST', body: fd});
  const res = await r.json();
  if (res.status === 'ok') {
    alert('User updated successfully!');
    load();
  } else {
    alert('Failed to update user: ' + (res.message || 'error'));
  }
}
load();
</script></body></html>
"""

def require_admin(credentials: HTTPBasicCredentials = Depends(admin_basic)):
    result = manager.db.verify_credential(credentials.username, credentials.password, "admin")
    if not result.get("valid"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Administrator authentication required.",
            headers={"WWW-Authenticate": "Basic realm=admin"},
        )
    return result


@app.get("/admin", response_class=HTMLResponse)
def admin_page(_: Dict[str, Any] = Depends(require_admin)):
    return HTMLResponse(content=ADMIN_PANEL_HTML)


@app.get("/admin/api/users")
def admin_api_users(_: Dict[str, Any] = Depends(require_admin)):

    users = manager.db.get_all_users()
    out = []
    for u in users:
        out.append({
            "roll_no": u.get("roll_no", ""),
            "name": u.get("name", ""),
            "role": u.get("role", "student"),
            "registered_at": u.get("registered_at", "")
        })
    return JSONResponse(content=out)


@app.post("/admin/api/modify")
def admin_api_modify(
    old_roll_no: str = Form(...),
    new_roll_no: str = Form(...),
    new_name: str = Form(...),
    new_role: str = Form(...),
    _: Dict[str, Any] = Depends(require_admin),
):
    success = manager.db.update_user_details(
        old_roll_no=old_roll_no.strip(),
        new_roll_no=new_roll_no.strip(),
        new_name=new_name.strip(),
        new_role=new_role.strip().lower(),
    )
    return JSONResponse(content={"status": "ok" if success else "error"})


@app.post("/admin/api/delete")
def admin_api_delete(roll_no: str = Form(...), _: Dict[str, Any] = Depends(require_admin)):
    success = manager.db.delete_user(roll_no.strip())
    return JSONResponse(content={"status": "ok" if success else "not_found"})


# ==============================================================================
# SERVER RUNNER (Runs with a local development certificate for secure mobile camera access)
# ==============================================================================
def _ensure_local_certificate() -> tuple[Path, Path]:
    """Create a local self-signed certificate on first run instead of shipping a private key."""
    cert_dir = Path(__file__).parent / ".runtime_certs"
    cert_dir.mkdir(exist_ok=True)
    cert_file = cert_dir / "cert.pem"
    key_file = cert_dir / "cert_key.pem"
    if cert_file.exists() and key_file.exists():
        return cert_file, key_file
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        from datetime import timedelta

        host = socket.gethostname()
        names = [x509.DNSName("localhost"), x509.DNSName(host)]
        try:
            names.append(x509.IPAddress(ip_address(socket.gethostbyname(host))))
        except Exception:
            pass
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(datetime.utcnow() - timedelta(minutes=1))
                .not_valid_after(datetime.utcnow() + timedelta(days=365)).add_extension(x509.SubjectAlternativeName(names), critical=False)
                .sign(key, hashes.SHA256()))
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return cert_file, key_file
    except Exception as exc:
        print(f"[KYC Server] Could not create local TLS certificate: {exc}")
        return cert_file, key_file


def run_registration_server(host: str = "0.0.0.0", port: int = 5050, ssl: bool = True):
    cert_file, key_file = _ensure_local_certificate() if ssl else (Path(""), Path(""))
    use_ssl = ssl and cert_file.exists() and key_file.exists()
    scheme = "https" if use_ssl else "http"
    print(f"\n[KYC Server] Starting Registration Server on {scheme}://{host}:{port}")
    print(f"[KYC Server] 🎓 Student Portal: {scheme}://{host}:{port}/student")
    print(f"[KYC Server] 👨‍🏫 Faculty Portal: {scheme}://{host}:{port}/teacher")

    if use_ssl:
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
            ssl_certfile=str(cert_file),
            ssl_keyfile=str(key_file),
        )
    else:
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="info",
        )

    server = uvicorn.Server(config)
    server.run()


def run_registration_server_in_thread(db_instance=None, port: int = 5050, ssl: bool = True):
    global manager
    if db_instance is not None:
        manager = KYCManager(db_instance)

    t = threading.Thread(target=run_registration_server, kwargs={"port": port, "ssl": ssl}, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    run_registration_server(host="0.0.0.0", port=5050, ssl=True)