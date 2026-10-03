import base64
import io
import threading
import cv2
import numpy as np
from flask import Flask, request, jsonify, render_template_string
from PIL import Image
from typing import Optional
import qrcode
import socket

from backend.core.db import DatabaseManager
from backend.core.face_core import FaceEngine

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Smart Class — {{ role_title }} Registration</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: #090d16;
            color: #f1f5f9;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 16px;
        }
        .container {
            width: 100%;
            max-width: 440px;
            background: rgba(26, 32, 53, 0.95);
            border: 1px solid rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(16px);
            border-radius: 20px;
            padding: 28px 24px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
            text-align: center;
        }
        .badge {
            display: inline-block;
            padding: 6px 14px;
            border-radius: 30px;
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.8px;
            text-transform: uppercase;
            margin-bottom: 12px;
            background: {{ badge_bg }};
            color: {{ badge_color }};
            border: 1px solid {{ badge_border }};
        }
        h2 { font-size: 22px; font-weight: 800; margin-bottom: 6px; }
        p.subtitle { font-size: 13px; color: #94a3b8; margin-bottom: 20px; }
        .input-group { text-align: left; margin-bottom: 14px; }
        .input-group label { display: block; font-size: 12px; font-weight: 600; color: #cbd5e1; margin-bottom: 6px; }
        input {
            width: 100%;
            padding: 12px 14px;
            border-radius: 10px;
            border: 1px solid #334155;
            background: #0f172a;
            color: #f8fafc;
            font-size: 14px;
            font-family: inherit;
            outline: none;
            transition: border-color 0.2s;
        }
        input:focus { border-color: #38bdf8; }
        .video-box {
            position: relative;
            width: 100%;
            height: 250px;
            background: #020617;
            border-radius: 14px;
            overflow: hidden;
            margin: 16px 0;
            border: 2px dashed #334155;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        video {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transform: scaleX(-1);
            display: block;
        }
        #previewImg {
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: none;
        }
        .cam-placeholder {
            position: absolute;
            inset: 0;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 16px;
            background: #0a0f1d;
            color: #94a3b8;
            font-size: 13px;
            text-align: center;
            cursor: pointer;
        }
        .cam-placeholder span { font-size: 36px; margin-bottom: 8px; }
        .mesh-scanline {
            position: absolute;
            top: 0; left: 0; right: 0; height: 3px;
            background: linear-gradient(90deg, transparent, #38bdf8, transparent);
            animation: scan 2s linear infinite;
            pointer-events: none;
        }
        @keyframes scan { 0% { top: 0; } 50% { top: 100%; } 100% { top: 0; } }
        button.action-btn {
            width: 100%;
            padding: 14px;
            border-radius: 12px;
            border: none;
            background: {{ btn_bg }};
            color: #ffffff;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s;
            box-shadow: 0 8px 20px {{ btn_shadow }};
        }
        button.action-btn:hover { filter: brightness(1.1); transform: translateY(-1px); }
        button.action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .fallback-btn {
            display: block;
            width: 100%;
            padding: 11px;
            border-radius: 10px;
            border: 1px solid #475569;
            background: #1e293b;
            color: #e2e8f0;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 10px;
        }
        .fallback-btn:hover { background: #334155; }
        #status {
            margin-top: 14px;
            font-size: 13px;
            font-weight: 600;
            min-height: 20px;
            line-height: 1.4;
        }
        .status-success { color: #4ade80; }
        .status-error { color: #f87171; }
        .status-info { color: #38bdf8; }
        .switch-link {
            display: block;
            margin-top: 18px;
            font-size: 12px;
            color: #64748b;
            text-decoration: none;
        }
        .switch-link:hover { color: #94a3b8; }
    </style>
</head>
<body>
    <div class="container">
        <span class="badge">{{ role_badge }}</span>
        <h2>{{ role_title }} Registration</h2>
        <p class="subtitle">Secure authentication & biometric registration</p>

        <div class="input-group">
            <label>{{ id_label }}</label>
            <input type="text" id="uid" placeholder="e.g. {{ id_placeholder }}" autocomplete="off" />
        </div>

        <div class="input-group">
            <label>Security Password / PIN</label>
            <input type="password" id="password" placeholder="Assigned access password" />
        </div>

        <div class="input-group">
            <label>Full Official Name</label>
            <input type="text" id="name" placeholder="Enter full name" />
        </div>

        <!-- Camera / Photo Viewport -->
        <div class="video-box" id="viewport">
            <video id="webcam" autoplay playsinline muted></video>
            <img id="previewImg" alt="Face Preview" />
            <div id="camPlaceholder" class="cam-placeholder" onclick="requestCam()">
                <span>📷</span>
                <strong>Tap to enable live camera</strong>
                <small style="margin-top:4px; opacity:0.8;">Or use the direct photo button below</small>
            </div>
            <div class="mesh-scanline"></div>
        </div>

        <input type="file" id="fileInput" accept="image/*" capture="user" style="display:none;" />
        
        <button id="captureBtn" class="action-btn">Capture & Register Face</button>
        <button id="nativeCamBtn" type="button" class="fallback-btn" onclick="document.getElementById('fileInput').click()">📸 Take Selfie with Native Camera</button>
        
        <div id="status"></div>

        <a class="switch-link" href="{{ alt_url }}">Switch to {{ alt_title }} Portal →</a>
    </div>

    <script>
        const video = document.getElementById('webcam');
        const previewImg = document.getElementById('previewImg');
        const placeholder = document.getElementById('camPlaceholder');
        const canvas = document.createElement('canvas');
        const role = "{{ role }}";
        const statusEl = document.getElementById('status');
        const btn = document.getElementById('captureBtn');
        const fileInput = document.getElementById('fileInput');

        let activeStream = null;
        let capturedB64 = null;

        function setStatus(msg, type) {
            statusEl.innerText = msg;
            statusEl.className = 'status-' + type;
        }

        async function requestCam() {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                setStatus("Live video stream is restricted by your browser on insecure HTTP. Please tap 'Take Selfie with Native Camera' below.", "info");
                return;
            }

            setStatus("Requesting camera access...", "info");
                const stream = await navigator.mediaDevices.getUserMedia({
                    video: { facingMode: "user", width: { ideal: 1920, min: 1280 }, height: { ideal: 1080, min: 720 } },
                    audio: false
                });
                activeStream = stream;
                video.srcObject = stream;
                video.style.display = "block";
                previewImg.style.display = "none";
                placeholder.style.display = "none";
                setStatus("Camera active. Align your face and tap Capture.", "info");
            } catch (err) {
                console.warn("Camera getUserMedia error:", err);
                placeholder.style.display = "flex";
                if (window.location.protocol !== 'https:' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
                    setStatus("Note: Modern mobile browsers block live webcam over unencrypted HTTP. Use 'Take Selfie with Native Camera' below to capture immediately!", "info");
                } else {
                    setStatus("Camera permission denied. Tap 'Take Selfie with Native Camera' below.", "error");
                }
            }
        }

        // Handle native mobile camera capture / file upload fallback
        fileInput.onchange = function(e) {
            const file = e.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = function(evt) {
                capturedB64 = evt.target.result;
                previewImg.src = capturedB64;
                previewImg.style.display = "block";
                video.style.display = "none";
                placeholder.style.display = "none";
                setStatus("Photo ready! Tap 'Capture & Register Face' to finalize.", "info");
            };
            reader.readAsDataURL(file);
        };

        // Try requesting camera on page load
        window.addEventListener('DOMContentLoaded', () => {
            requestCam();
        });

        btn.onclick = async () => {
            const uid = document.getElementById('uid').value.trim();
            const password = document.getElementById('password').value.trim();
            const name = document.getElementById('name').value.trim();

            if (!uid) { setStatus("Please enter your ID.", "error"); return; }
            if (!password) { setStatus("Please enter your access password.", "error"); return; }
            if (!name) { setStatus("Please enter your full name.", "error"); return; }

            let frameB64 = capturedB64;

            if (!frameB64) {
                if (!video.videoWidth) {
                    setStatus("Please tap 'Take Selfie with Native Camera' below to take your photo.", "error");
                    return;
                }
                canvas.width = video.videoWidth;
                canvas.height = video.videoHeight;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(video, 0, 0);
                frameB64 = canvas.toDataURL('image/jpeg', 0.85);
            }

            btn.disabled = true;
            setStatus("Scanning facial features and verifying biometrics...", "info");

            try {
                const res = await fetch('/api/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        user_id: uid,
                        password: password,
                        name: name,
                        role: role,
                        frames: [frameB64]
                    })
                });

                const data = await res.json();
                if (res.ok && data.success) {
                    setStatus("✅ " + data.message, "success");
                    document.getElementById('password').value = "";
                } else {
                    setStatus("❌ " + (data.error || "Registration failed"), "error");
                }
            } catch (err) {
                setStatus("Network error connecting to attendance server.", "error");
            } finally {
                btn.disabled = false;
            }
        };
    </script>
</body>
</html>
"""

def decode_base64_frame(b64_str: str) -> Optional[np.ndarray]:
    try:
        if "," in b64_str:
            b64_str = b64_str.split(",", 1)[1]
        img_bytes = base64.b64decode(b64_str)
        img = Image.open(io.BytesIO(img_bytes))
        frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        return frame
    except Exception:
        return None

def create_flask_app(db: DatabaseManager, engine: FaceEngine) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_student()

    @app.route("/student")
    def render_student():
        return render_template_string(
            HTML_TEMPLATE,
            role="student",
            role_title="Student",
            role_badge="🎓 Student Access",
            id_label="Student Roll No / University Reg No",
            id_placeholder="CS-2026-042",
            badge_bg="rgba(56, 189, 248, 0.15)",
            badge_color="#38bdf8",
            badge_border="rgba(56, 189, 248, 0.3)",
            btn_bg="linear-gradient(135deg, #0284c7, #2563eb)",
            btn_shadow="rgba(37, 99, 235, 0.35)",
            alt_url="/teacher",
            alt_title="Faculty / Teacher"
        )

    @app.route("/teacher")
    @app.route("/faculty")
    def render_teacher():
        return render_template_string(
            HTML_TEMPLATE,
            role="teacher",
            role_title="Faculty & Teacher",
            role_badge="👨‍🏫 Faculty Official Portal",
            id_label="Faculty Employee ID / Staff Code",
            id_placeholder="EMP-FAC-101",
            badge_bg="rgba(168, 85, 247, 0.15)",
            badge_color="#c084fc",
            badge_border="rgba(168, 85, 247, 0.3)",
            btn_bg="linear-gradient(135deg, #9333ea, #7c3aed)",
            btn_shadow="rgba(124, 58, 237, 0.35)",
            alt_url="/student",
            alt_title="Student"
        )

    @app.route("/api/register", methods=["POST"])
    def api_register():
        data = request.get_json(force=True)
        user_id = data.get("user_id", "").strip()
        password = data.get("password", "").strip()
        name = data.get("name", "").strip()
        role = data.get("role", "student").strip()
        frames_b64 = data.get("frames", [])

        if not user_id or not name:
            return jsonify({"error": "ID and Full Name are required."}), 400
        if not password:
            return jsonify({"error": "Access Password is required."}), 400
        if not frames_b64:
            return jsonify({"error": "No camera frame received."}), 400
        if not engine.ready:
            return jsonify({"error": "Biometric engine is still loading. Please wait a moment."}), 503

        # Validate Pre-authorized ID & Password credentials (e.g. WiFi-style password protection)
        cred_check = db.verify_credential(user_id, password, role)
        if not cred_check.get("valid"):
            return jsonify({"error": cred_check.get("error", "Unauthorized registration attempt.")}), 401
        
        # If pre-allocated name was configured by admin, ensure name matches or default to allocated name
        allocated_name = cred_check.get("name", "")
        if allocated_name and allocated_name.lower() not in name.lower() and name.lower() not in allocated_name.lower():
            # suggest or auto-correct to allocated official name
            name = allocated_name

        embeddings = []
        for b64 in frames_b64:
            frame = decode_base64_frame(b64)
            if frame is None: continue
            emb = engine.extract_embedding(frame)
            if emb is not None: embeddings.append(emb)

        if not embeddings:
            return jsonify({"error": "No face detected in webcam. Look directly at the camera in good lighting."}), 400

        users = db.get_all_users()
        match = engine.match(embeddings[0], users)
        if match:
            matched_id, matched_name, matched_role, conf = match
            if matched_id != user_id and conf >= 0.50:
                return jsonify({"error": f"Duplicate biometric match! This face is already registered as '{matched_name}' ({matched_id})."}), 409

        db.upsert_user(user_id, name, role, embeddings[0], embeddings)
        return jsonify({"success": True, "message": f"Welcome {name}! {role.capitalize()} face profile registered successfully."})

    return app

def run_flask_in_thread(db: DatabaseManager, engine: FaceEngine, port: int = 5000):
    app = create_flask_app(db, engine)
    threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": port, "debug": False, "use_reloader": False}, daemon=True).start()

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def generate_qr(url: str) -> Image.Image:
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").get_image()
