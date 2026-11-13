import asyncio
import time
import traceback
from threading import Thread
from saruman.activity import emit

from config_schema.general import get_name
from data_schema.tool_structures import SPELL_TO_EMOTION
from live2d.vtube_settings import Live2DConfig
from live2d.vtube_studio import VTubeStudioIntegration
from tts.audio_device import AudioProcessor
from tts.fish.fish_gr import fish_tts_emo
from tts.voice_diagnostics import preview_audio, save_details, save_generated

MAX_AUDIO_ALLOWED_TIME = 20

SPELL_TO_EMOTIONS_RU = {
    "сарказм": "sarcastic",
    "подмигивает": "sarcastic",
    "злая": "angry",
    "страх": "scared",
    "боится": "scared",
    "яндере": "yandere",
    "агрессивная": "angry",
    "флирт": "sarcastic",
    "идевается": "sarcastic",
    "флиртует": "sarcastic",
    "любопытная": "happy",
    "бесится": "angry",
}

SPELL_TO_EMOTION.update(SPELL_TO_EMOTIONS_RU)

EMOTION_TO_COMMAND_OLD = {
    "neutral": "thinking-face",
    "happy": "smiling-face-with-hearts",
    "sad": "crying-cat",
    "angry": "smiling-face-with-horns",
    "scared": "face-screaming-in-fear",
    "whispering": "shushing-face",
    "disgusted": "face-vomiting",
    "sarcastic": "smirking-face",
}

EMOTION_TO_COMMAND_ESCAPED = {
    "neutral": "\u00a7eНейтральная",
    "happy": "\u00a76Весёлая",
    "sad": "\u00a7bГрустная",
    "angry": "\u00a74ЗЛАЯ",
    "scared": "\u00a79Боится",
    "whispering": "\u00a7dУгарает",
    "disgusted": "\u00a7aХочет блевать",
    "sarcastic": "\u00a7Саркастичная",
    "yandere": "\u00a74☠☠☠",
}

EMOTION_TO_COMMAND = {
    "neutral": 'Нейтральная","color":"gold',
    "happy": 'Весёлая","color":"green',
    "sad": 'Грустная","color":"gold',
    "angry": 'ЗЛАЯ","color":"red',
    "scared": 'Боится","color":"blue',
    "whispering": 'Угарает","color":"gold',
    "disgusted": 'Хочет блевать","color":"green',
    "sarcastic": 'Саркастичная","color":"gold',
    "yandere": '☠☠☠","color":"red',
}


# bossbar set 100 name "Saruman сейчас: Злая"
SELF_NAME = get_name()
for k, v in EMOTION_TO_COMMAND.items():
    # EMOTION_TO_COMMAND[k] = "st " + v + ""
    # /minestamp advance :abacus: 1 1 2
    # EMOTION_TO_COMMAND[k] = "minestamp advance :" + v + ": 5 1 1"
    # MEGABATTLE
    EMOTION_TO_COMMAND[k] = (
        'bossbar set 100 name [{"text":"'
        + SELF_NAME
        + 'сейчас: ","color":"white"},{"text":"'
        + v
        + '"}]'
    )


def check_tts_queue(audio_processor, ctx_swarm, check_interrupt=False) -> bool:
    tts_queue = ctx_swarm["tts_queue"]
    if not len(tts_queue):
        return False
    entry = tts_queue[0]
    if entry.get("text") == "interrupt" or entry.get("emotion") == "interrupt":
        tts_queue.pop(0)
        return True
    if check_interrupt:
        return False
    entry = tts_queue.pop(0)
    voice = ctx_swarm["voice"]
    old_state = ctx_swarm["vtube"]["state"]
    playback = None
    metadata = None
    try:
        voice["speak_entry"] = entry
        emit('voice.synthesis',status='running',trace=entry.get('request_id',''),details={'text':entry.get('text'),'emotion':entry.get('emotion')})
        voice["text_chunk"] = entry["text"]
        if entry.get("voice_preview"):
            audio, sr = preview_audio(entry["voice_preview"])
        else:
            audio, sr = fish_tts_emo(entry)
        try:
            metadata = save_generated(
                audio, sr, entry, audio_processor.main_speaker.name
            )
        except OSError as error:
            print(f"[TTS] Cannot save voice diagnostic: {error}")
        # Bound speech before output so the completion event reflects the actual clip.
        if len(audio) > MAX_AUDIO_ALLOWED_TIME * sr:
            print(f"[TTS] Truncating speech to {MAX_AUDIO_ALLOWED_TIME}s")
            audio = audio[: MAX_AUDIO_ALLOWED_TIME * sr]
        voice["is_speaking"] = True
        ctx_swarm["vtube"]["state"] = "idle"
        playback = audio_processor.play_sound(audio, sr, blocking=False)
        deadline = time.monotonic() + MAX_AUDIO_ALLOWED_TIME + 10
        print(f"[TTS] playing {len(audio) / sr:.2f}s; waiting for device completion")
        while not playback.wait(0.05):
            if (
                check_tts_queue(audio_processor, ctx_swarm, check_interrupt=True)
                or not ctx_swarm["env"]["actived"]
            ):
                audio_processor.interrupt_main_device()
            if time.monotonic() > deadline:
                audio_processor.interrupt_main_device()
                raise TimeoutError("Speech output did not finish in time")
        playback.result()
        emit('voice.playback',status='interrupted' if playback.interrupted else 'played',trace=entry.get('request_id',''),
             details={'text':entry.get('text'),'seconds':len(audio)/sr})
        if metadata is not None:
            metadata["status"] = "interrupted" if playback.interrupted else "played"
            metadata["output_seconds"] = len(audio) / sr
    except Exception as error:  # noqa: BLE001 - keep the speech worker alive; report the failure
        emit('voice.playback',status='error',trace=entry.get('request_id',''),details={'error':type(error).__name__})
        if playback is not None:
            audio_processor.interrupt_main_device()
        if metadata is not None:
            metadata.update(status="error", error=str(error))
        print(f"[TTS] Speech failed: {error}")
        traceback.print_exc()
    finally:
        voice["is_speaking"] = False
        voice["text_chunk"] = ""
        ctx_swarm["vtube"]["state"] = old_state
        if metadata is not None:
            try:
                save_details(metadata)
            except OSError as error:
                print(f"[TTS] Cannot save voice diagnostic: {error}")
    return False


def simple_tts_handler(ctx_swarm):
    ctx_env = ctx_swarm["env"]
    audio_processor = AudioProcessor()
    # Starting eye rotater thread
    live2d = VTubeStudioIntegration(Live2DConfig(), ctx_swarm)

    fx_thread = Thread(
        target=fx_sound_handler,
        args=(
            audio_processor,
            ctx_swarm,
        ),
        daemon=True,
    )
    fx_thread.start()
    tts_queue_handler(audio_processor, ctx_env, ctx_swarm)
    asyncio.run(live2d.cleanup())


def tts_queue_handler(audio_processor, ctx_env, ctx_swarm):
    while ctx_env["actived"]:
        check_tts_queue(audio_processor, ctx_swarm)
        time.sleep(0.1)


def fx_sound_handler(audio_processor: AudioProcessor, ctx_swarm):
    fx_queue = ctx_swarm.get("fx_queue", None)
    if fx_queue:
        ctx_env = ctx_swarm["env"]
        while ctx_env["actived"]:
            # yield
            # return None, None, "🟡 Generating, GOT QUEUE ITEM " + str(fx_queue.pop(0))
            try:
                sound_name = fx_queue.get()
                audio_processor.play_fx(sound_name)
            except Exception as e:
                print("ERROR FX PLAY =(", e)
                time.sleep(10)
            time.sleep(0.1)
