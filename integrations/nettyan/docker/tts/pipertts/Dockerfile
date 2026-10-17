FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
    espeak-ng wget \
    && rm -rf /var/lib/apt/lists/*

RUN uv pip install --system --no-cache gradio piper-tts scipy numpy

RUN mkdir -p /models && \
    wget -q "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx" \
         -O /models/ru_RU-ruslan-medium.onnx && \
    wget -q "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx.json" \
         -O /models/ru_RU-ruslan-medium.onnx.json

WORKDIR /app
COPY pipertts_server.py .

EXPOSE 22231

CMD ["python", "-c", "from pipertts_server import build_and_launch; build_and_launch(server_name='0.0.0.0')"]
