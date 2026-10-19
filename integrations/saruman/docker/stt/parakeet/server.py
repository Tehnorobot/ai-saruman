"""Parakeet TDT v3 0.6B ONNX — STT server with VAD streaming.

Endpoints:
  POST /v1/audio/transcriptions — OpenAI-compatible, file upload, returns {"text": "..."}
  WS   /v1/audio/stream         — stream raw 16kHz s16le mono PCM, get STT events back

Model auto-downloads on first run to MODEL_DIR (should be a mapped volume).
"""

import asyncio
import io
import json
import os
import shutil
import tarfile
import tempfile
import urllib.request
from contextlib import asynccontextmanager

import numpy as np
import sherpa_onnx
import soundfile as sf
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

MODEL_DIR = os.getenv("MODEL_DIR", "/data/model")
MODEL_URL = os.getenv(
    "MODEL_URL",
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2",
)
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# VAD config
VAD_SAMPLE_RATE = 16000
VAD_SILENCE_DURATION = float(os.getenv("VAD_SILENCE_DURATION", "2.0"))
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.1"))
VAD_MIN_SPEECH_DURATION = float(os.getenv("VAD_MIN_SPEECH_DURATION", "0.1"))
VAD_WINDOW_SIZE = 512  # Silero VAD expects 512 samples at 16kHz

recognizer = None
vad_model_path = None


def _find_file(directory: str, pattern: str) -> str:
    for f in sorted(os.listdir(directory)):
        if pattern in f:
            return os.path.join(directory, f)
    return ""


def _download_model():
    """Download and extract model if not already present."""
    os.makedirs(MODEL_DIR, exist_ok=True)
    if _find_file(MODEL_DIR, "encoder") and _find_file(MODEL_DIR, "tokens.txt"):
        print(f"[Parakeet] Model already exists in {MODEL_DIR}")
        return

    print(f"[Parakeet] Downloading model from {MODEL_URL}")
    tmp_path, _ = urllib.request.urlretrieve(MODEL_URL)
    print("[Parakeet] Extracting...")
    with tarfile.open(tmp_path, "r:bz2") as tar:
        tar.extractall("/tmp/model_dl")
    os.unlink(tmp_path)

    for root, _, files in os.walk("/tmp/model_dl"):
        for f in files:
            if f.endswith(".onnx") or f == "tokens.txt":
                src = os.path.join(root, f)
                dst = os.path.join(MODEL_DIR, f)
                shutil.copy2(src, dst)
                print(f"  {f}")
    shutil.rmtree("/tmp/model_dl", ignore_errors=True)
    print("[Parakeet] Model ready")


def _init_recognizer():
    global recognizer, vad_model_path
    _download_model()

    encoder = _find_file(MODEL_DIR, "encoder")
    decoder = _find_file(MODEL_DIR, "decoder")
    joiner = _find_file(MODEL_DIR, "joiner")
    tokens = _find_file(MODEL_DIR, "tokens.txt")

    if not all([encoder, decoder, joiner, tokens]):
        raise FileNotFoundError(
            f"Model files not found in {MODEL_DIR}. "
            f"encoder={encoder}, decoder={decoder}, "
            f"joiner={joiner}, tokens={tokens}"
        )

    print(f"[Parakeet] Loading: encoder={encoder}, decoder={decoder}, joiner={joiner}")
    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=encoder,
        decoder=decoder,
        joiner=joiner,
        tokens=tokens,
        model_type="nemo_transducer",
        num_threads=int(os.getenv("NUM_THREADS", "4")),
        provider="cpu",
    )
    print("[Parakeet] STT model loaded OK")

    # Silero VAD — bundled with sherpa-onnx, just need the .onnx path
    vad_model_path = _find_file(MODEL_DIR, "silero_vad")
    if not vad_model_path:
        # Download silero VAD model
        vad_url = (
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
            "silero_vad.onnx"
        )
        vad_model_path = os.path.join(MODEL_DIR, "silero_vad.onnx")
        print(f"[VAD] Downloading Silero VAD to {vad_model_path}")
        urllib.request.urlretrieve(vad_url, vad_model_path)
    print(f"[VAD] Silero VAD model: {vad_model_path}")


def _create_vad(sample_rate: int = 16000):
    """Create a new VAD instance (one per WebSocket session)."""
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = vad_model_path
    config.silero_vad.threshold = VAD_THRESHOLD
    config.silero_vad.min_silence_duration = VAD_SILENCE_DURATION
    config.silero_vad.min_speech_duration = VAD_MIN_SPEECH_DURATION
    config.sample_rate = sample_rate
    return sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)


def _transcribe_samples(samples: np.ndarray, sr: int = 16000) -> str:
    """Run STT on a float32 audio array."""
    stream = recognizer.create_stream()
    stream.accept_waveform(sr, samples)
    recognizer.decode_stream(stream)
    return stream.result.text.strip()


@asynccontextmanager
async def lifespan(_app):
    _init_recognizer()
    yield


app = FastAPI(title="Parakeet STT", lifespan=lifespan)


# === HTTP endpoint (existing, unchanged) ===

@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form(default=None),
    response_format: str = Form(default="json"),
):
    raw = await file.read()

    try:
        samples, sr = sf.read(io.BytesIO(raw), dtype="float32")
    except Exception:
        suffix = os.path.splitext(file.filename or "audio.wav")[1]
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        try:
            samples, sr = sf.read(tmp_path, dtype="float32")
        finally:
            os.unlink(tmp_path)

    if samples.ndim > 1:
        samples = samples.mean(axis=1)

    if sr != 16000:
        import soxr
        samples = soxr.resample(samples, sr, 16000)

    text = _transcribe_samples(samples)
    return JSONResponse({"text": text})


# === WebSocket streaming endpoint (new) ===

@app.websocket("/v1/audio/stream")
async def audio_stream(ws: WebSocket):
    """Stream 16kHz float32 mono PCM, get STT events back.

    Client sends: raw PCM bytes (int16 little-endian, 16kHz, mono)
    Server sends: JSON events:
      {"event": "vad", "speech": true/false}
      {"event": "transcript", "text": "...", "start": 1.2, "end": 3.5}

    Client MUST resample to 16kHz before sending.
    """
    await ws.accept()

    FLUSH_TIMEOUT = float(os.getenv("VAD_FLUSH_TIMEOUT", "1.0"))

    print("[WS] Connected (expecting 16kHz s16le mono)", flush=True)

    vad = _create_vad()
    sample_count = 0
    chunk_count = 0
    was_speech = False
    vad_buffer = np.array([], dtype=np.float32)
    # 1.5s silence to flush VAD when data stops
    SILENCE_PADDING = np.zeros(int(VAD_SAMPLE_RATE * 1.5), dtype=np.float32)

    async def _feed_vad_and_transcribe(audio):
        nonlocal sample_count, was_speech, vad_buffer

        vad_buffer = np.concatenate([vad_buffer, audio])
        while len(vad_buffer) >= VAD_WINDOW_SIZE:
            vad.accept_waveform(vad_buffer[:VAD_WINDOW_SIZE])
            vad_buffer = vad_buffer[VAD_WINDOW_SIZE:]

        sample_count += len(audio)

        is_speech = not vad.empty()
        if is_speech != was_speech:
            await ws.send_text(json.dumps({"event": "vad", "speech": is_speech}))
            was_speech = is_speech

        while not vad.empty():
            samples = vad.front.samples
            duration = len(samples) / VAD_SAMPLE_RATE
            if os.getenv("STT_DEBUG_VAD_SEGMENT", "false").lower() == "true":
                try:
                    sf.write("/data/debug_vad_segment.wav", list(samples), VAD_SAMPLE_RATE)
                except Exception:
                    pass
            print(f"[WS] VAD segment: {len(samples)} samples, {duration:.2f}s", flush=True)
            text = _transcribe_samples(samples, sr=VAD_SAMPLE_RATE)
            vad.pop()
            if text:
                end_sec = sample_count / VAD_SAMPLE_RATE
                start_sec = end_sec - duration
                evt = {"event": "transcript", "text": text,
                       "start": round(start_sec, 2),
                       "end": round(end_sec, 2)}
                print(f"[WS] {evt}", flush=True)
                await ws.send_text(json.dumps(evt))

    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_bytes(), timeout=FLUSH_TIMEOUT)
            except asyncio.TimeoutError:
                await _feed_vad_and_transcribe(SILENCE_PADDING)
                continue

            if not data:
                break

            int16 = np.frombuffer(data, dtype=np.int16)
            float32 = int16.astype(np.float32) / 32768.0

            chunk_count += 1
            if chunk_count <= 3 or chunk_count % 50 == 0:
                lvl = np.max(np.abs(float32)) if len(float32) > 0 else 0
                print(f"[WS] #{chunk_count} samples={len(float32)} lvl={lvl:.3f} t={sample_count/VAD_SAMPLE_RATE:.1f}s", flush=True)

            await _feed_vad_and_transcribe(float32)

    except WebSocketDisconnect as e:
        print(f"[WS] Client disconnected: {e}", flush=True)
    except Exception as e:
        import traceback
        print(f"[WS] Error: {e}", flush=True)
        traceback.print_exc()
    finally:
        print("[WS] Session ended", flush=True)


@app.get("/debug/vad_segment")
async def debug_vad_segment():
    """Download last VAD segment as WAV — this is what STT actually receives."""
    path = "/data/debug_vad_segment.wav"
    if not os.path.exists(path):
        return JSONResponse({"error": "no segment yet"}, status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="audio/wav", filename="vad_segment.wav")


@app.get("/debug/last_audio")
async def debug_last_audio():
    """Download last received audio stream as WAV for debugging."""
    debug_path = "/data/debug_last_stream.wav"
    if not os.path.exists(debug_path):
        return JSONResponse({"error": "no audio saved yet"}, status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(debug_path, media_type="audio/wav", filename="debug_last_stream.wav")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model_loaded": recognizer is not None,
        "vad_loaded": vad_model_path is not None,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
