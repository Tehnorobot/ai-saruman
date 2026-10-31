"""Bounded runtime recording and replay of the operator-approved voice samples."""

import json
from pathlib import Path

from scipy.io import wavfile

from data_schema.structure_templates import REPO_DATA_PATH
from tts.playback import float_pcm

DATA = Path(REPO_DATA_PATH)
LATEST = DATA / "tts" / "last-generated.wav"
DETAILS = LATEST.with_suffix(".json")
PREVIEWS = {
    emotion: DATA / "voice-previews" / "v2" / f"saruman-{emotion}-ru.wav"
    for emotion in ("neutral", "angry")
}


def preview_audio(emotion):
    path = PREVIEWS[emotion]
    rate, samples = wavfile.read(path)
    return float_pcm(samples), rate


def save_generated(samples, rate, entry, device):
    """Overwrite only the latest utterance; never alter approved v2 files."""
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    temporary = LATEST.with_suffix(".tmp.wav")
    wavfile.write(temporary, rate, float_pcm(samples))
    temporary.replace(LATEST)
    metadata = {
        "source": "approved_v2" if entry.get("voice_preview") else "fish",
        "text": entry["text"],
        "emotion": entry.get("emotion", "neutral"),
        "sample_rate": rate,
        "seconds": len(samples) / rate,
        "output_device": device,
        "status": "generated",
    }
    save_details(metadata)
    return metadata


def save_details(metadata):
    temporary = DETAILS.with_suffix(".tmp.json")
    temporary.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(DETAILS)


def latest_recording():
    try:
        return str(LATEST) if LATEST.is_file() else None, json.loads(
            DETAILS.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None, {"status": "Речь ещё не записана"}


def queue_preview(ctx, emotion):
    if emotion not in PREVIEWS or not PREVIEWS[emotion].is_file():
        return "Одобренная проба v2 не найдена"
    if ctx is None:
        return "TTS ещё не запущен"
    ctx["tts_queue"].append(
        {
            "text": f"Проверка сохранённого голоса: {emotion}",
            "emotion": emotion,
            "voice_preview": emotion,
        }
    )
    return "Проба v2 поставлена в очередь на обычное устройство речи"


def build_voice_diagnostics(get_context):
    import gradio as gr

    gr.Markdown(
        "Проверка сохранённой пробы через игровой вывод звука, без нового синтеза."
    )
    emotion = gr.Dropdown(choices=list(PREVIEWS), value="neutral", label="Проба v2")
    play = gr.Button("Проиграть пробу v2 через TTS")
    status = gr.Textbox(label="Проверка голоса", interactive=False)
    play.click(
        lambda value: queue_preview(get_context(), value),
        inputs=emotion,
        outputs=status,
    )
    recording = gr.Audio(
        label="Последняя озвучка до вывода на устройство", type="filepath"
    )
    details = gr.JSON(label="Состояние воспроизведения")
    refresh = gr.Button("Обновить последнюю озвучку")
    refresh.click(latest_recording, outputs=[recording, details])
