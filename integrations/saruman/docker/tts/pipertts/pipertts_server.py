import os
import tempfile
import threading
import time
import traceback
import urllib.request
from typing import TypedDict

import gradio as gr
import numpy as np
from scipy.io import wavfile
from scipy.signal import resample

MODELS_DIR = os.environ.get("PIPER_MODELS_DIR", "/models")
MODEL_NAME = os.environ.get("PIPER_MODEL_NAME", "ru_RU-ruslan-medium")
MODEL_PATH = os.path.join(MODELS_DIR, f"{MODEL_NAME}.onnx")
CONFIG_PATH = os.path.join(MODELS_DIR, f"{MODEL_NAME}.onnx.json")

_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium"
MODEL_URL = f"{_HF_BASE}/{MODEL_NAME}.onnx"
CONFIG_URL = f"{_HF_BASE}/{MODEL_NAME}.onnx.json"

_voice = None
_model_init_error = None


def _download_if_missing():
    os.makedirs(MODELS_DIR, exist_ok=True)
    for path, url in [(MODEL_PATH, MODEL_URL), (CONFIG_PATH, CONFIG_URL)]:
        if not os.path.exists(path):
            print(f"[DOWNLOAD] {url} → {path}")
            urllib.request.urlretrieve(url, path)
            print(f"[DOWNLOAD] Done: {os.path.basename(path)}")


def _init_model():
    global _voice, _model_init_error
    try:
        _download_if_missing()
        from piper import PiperVoice

        print(f"[STARTUP] Loading Piper model '{MODEL_PATH}'...")
        start = time.time()
        _voice = PiperVoice.load(MODEL_PATH)
        print(f"[STARTUP] Model loaded in {time.time() - start:.2f}s")
    except Exception as e:
        print(f"[STARTUP] Failed: {e}")
        traceback.print_exc()
        _model_init_error = str(e)


threading.Thread(target=_init_model, daemon=True).start()


class FishTTSGradioKWArgs(TypedDict):
    """Same interface as fish_gr / simple_tts_server — only 'text' is used."""

    text: str
    normalize: bool
    reference_id: str
    reference_audio: str
    reference_text: str
    max_new_tokens: int
    chunk_length: int
    top_p: float
    repetition_penalty: float
    temperature: float
    seed: int
    use_memory_cache: str
    api_name: str


def _resample(input_path: str, output_path: str, target_rate: int = 48000) -> bool:
    try:
        sr, data = wavfile.read(input_path)
        if sr == target_rate:
            return False
        n_samples = int(len(data) / sr * target_rate)
        if data.ndim == 1:
            resampled = resample(data, n_samples)
        else:
            resampled = np.zeros((n_samples, data.shape[1]), dtype=data.dtype)
            for ch in range(data.shape[1]):
                resampled[:, ch] = resample(data[:, ch], n_samples)
        if resampled.dtype != np.int16:
            resampled = resampled.astype(np.int16)
        wavfile.write(output_path, target_rate, resampled)
        print(f"[RESAMPLE] {sr} Hz → {target_rate} Hz")
        return True
    except Exception as e:
        print(f"[RESAMPLE] Failed: {e}")
        return False


def predict(
    text: str,
    normalize: bool,
    reference_id: str,
    reference_audio: str | None,
    reference_text: str,
    max_new_tokens: int,
    chunk_length: int,
    top_p: float,
    repetition_penalty: float,
    temperature: float,
    seed: int,
    use_memory_cache: str,
) -> tuple[str | None, str]:
    if _model_init_error:
        return None, f"<div style='color:red'>Model init failed: {_model_init_error}</div>"
    if _voice is None:
        return None, "<div style='color:orange'>Model is still loading, please retry in a moment</div>"
    if not text or not text.strip():
        return None, "<div style='color:red'>Empty text</div>"

    try:
        print(f"[GENERATE] '{text[:60]}'")
        start = time.time()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp_path = f.name
        with tempfile.NamedTemporaryFile(suffix="_resampled.wav", delete=False) as f:
            tmp_resampled_path = f.name

        try:
            chunks = list(_voice.synthesize(text))
            audio_arr = np.concatenate([c.audio_int16_array for c in chunks])
            sample_rate = chunks[0].sample_rate
            wavfile.write(tmp_path, sample_rate, audio_arr)

            print(f"[GENERATE] Done in {time.time() - start:.2f}s")

            if _resample(tmp_path, tmp_resampled_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return tmp_resampled_path, ""
            else:
                try:
                    os.unlink(tmp_resampled_path)
                except Exception:
                    pass
                return tmp_path, ""

        except Exception:
            for p in (tmp_path, tmp_resampled_path):
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except Exception:
                        pass
            raise

    except Exception as e:
        traceback.print_exc()
        return None, f"<div style='color:red'>Synthesis failed: {e}</div>"


def build_and_launch(server_name: str = "127.0.0.1", server_port: int = 22231):
    with gr.Blocks(title="PiperTTS Gradio Server") as demo:
        with gr.Row():
            text_in = gr.Textbox(label="Realtime Transform Text", lines=4, value="Привет!")
        with gr.Row():
            normalize = gr.Checkbox(label="Text Normalization", value=False)
            reference_id = gr.Textbox(label="Reference ID", value="")
        with gr.Row():
            reference_audio = gr.Audio(label="Reference Audio", type="filepath")
            reference_text = gr.Textbox(label="Reference Text", value="")
        with gr.Row():
            max_new_tokens = gr.Slider(0, 2048, value=0, step=1, label="Max New Tokens")
            chunk_length = gr.Slider(0, 2000, value=200, step=1, label="Chunk Length")
        with gr.Row():
            top_p = gr.Slider(0.0, 1.0, value=0.7, step=0.01, label="Top P")
            repetition_penalty = gr.Slider(0.1, 5.0, value=1.2, step=0.1, label="Repetition Penalty")
        with gr.Row():
            temperature = gr.Slider(0.0, 2.0, value=0.7, step=0.01, label="Temperature")
            seed = gr.Number(value=0, label="Seed")
        use_memory_cache = gr.Radio(choices=["on", "off"], value="on", label="Use Memory Cache")
        audio_out = gr.Audio(label="Generated Audio")
        error_html = gr.HTML()
        generate_btn = gr.Button("Generate")
        generate_btn.click(
            predict,
            inputs=[
                text_in, normalize, reference_id, reference_audio, reference_text,
                max_new_tokens, chunk_length, top_p, repetition_penalty,
                temperature, seed, use_memory_cache,
            ],
            outputs=[audio_out, error_html],
            api_name="partial",
        )

    demo.launch(server_name=server_name, server_port=server_port, share=False)


if __name__ == "__main__":
    build_and_launch()
