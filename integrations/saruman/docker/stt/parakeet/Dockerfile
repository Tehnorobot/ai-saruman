FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN uv pip install --system --no-cache \
    sherpa-onnx \
    fastapi \
    uvicorn[standard] \
    python-multipart \
    websockets \
    soundfile \
    numpy \
    soxr

COPY server.py .

# Model downloads to /data/model on first run (mapped volume).
# No model baked into image — keeps it small and cacheable on host.

ENV PYTHONUNBUFFERED=1
ENV MODEL_DIR=/data/model
ENV PORT=8000
ENV NUM_THREADS=4

EXPOSE 8000

CMD ["python", "server.py"]
