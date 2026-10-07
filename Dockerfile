FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CLOUD_MODE=1 \
    ALLOW_OPEN_REGISTRATION=0 \
    DISABLE_REGISTRATION=1 \
    FORWARDED_ALLOW_IPS=* \
    LOG_FORMAT=json

# build tools: insightface compiles a small Cython extension; libgl/glib/gomp: OpenCV + ONNX Runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential g++ libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# The desktop UI (customtkinter) and the optional Tesseract wrapper are not needed on a server.
RUN grep -viE '^(customtkinter|pytesseract)' requirements.txt > /tmp/req.txt && pip install -r /tmp/req.txt

COPY . .

# Bake the face model into the image so cold starts don't download it (non-fatal if the build is offline).
RUN python -c "from insightface.app import FaceAnalysis; a=FaceAnalysis(name='buffalo_sc', root='models', providers=['CPUExecutionProvider']); a.prepare(ctx_id=0, det_size=(640,640))" \
    || echo "[warn] face model not pre-downloaded"

EXPOSE 8000
# SERVICE=register -> face-registration portal; anything else -> admin web UI + REST API + scheduler.
# Both bind to the platform-provided $PORT. Run ONE replica: sessions and the scheduler live in memory.
CMD ["sh", "-c", "if [ \"$SERVICE\" = \"register\" ]; then exec python register.py; else API_HOST=0.0.0.0 API_PORT=${PORT:-8000} exec python server.py; fi"]
