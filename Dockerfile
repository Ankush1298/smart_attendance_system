FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CLOUD_MODE=1 \
    ALLOW_OPEN_REGISTRATION=0 \
    FACE_MODEL=buffalo_sc

# build-essential/g++: insightface builds a small Cython extension; libglib/libgomp: OpenCV + ONNX Runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential g++ libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-cloud.txt .
RUN pip install -r requirements-cloud.txt

COPY . .

# Bake the face model into the image so cold starts don't download it. Non-fatal: it will
# fall back to downloading on first start if the build has no network.
RUN python -c "from insightface.app import FaceAnalysis; a=FaceAnalysis(name='buffalo_sc', root='models', providers=['CPUExecutionProvider']); a.prepare(ctx_id=0, det_size=(640,640))" \
    || echo "[warn] face model not pre-downloaded"

# Hosting platforms inject PORT. Single process: KYC/admin sessions live in memory.
EXPOSE 8000
CMD ["python", "register.py"]
