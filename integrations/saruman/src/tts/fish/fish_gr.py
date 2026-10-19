import os
import sys
import traceback
from typing import TypedDict

import numpy as np
from gradio_client import Client, handle_file
from scipy.io import wavfile

from character.character_manager import CharacterManager

if __name__ == "__main__":
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    )

from utils.audio_utils import change_pitch_speed

client = "not_started"
error = None

character = CharacterManager.get_main_character()

class FishTTSGradioKWArgs(TypedDict):
    """
    Accepts 12 parameters:
    text str Required

    The input value that is provided in the "Realtime Transform Text" Textbox component.

    normalize bool Default: False

    The input value that is provided in the "Text Normalization" Checkbox component.

    reference_id str Required

    The input value that is provided in the "Reference ID" Textbox component.

    reference_audio filepath Required

    The input value that is provided in the "Reference Audio" Audio component. The FileData class is a subclass of the GradioModel class that represents a file object within a Gradio interface. It is used to store file data and metadata when a file is uploaded. Attributes: path: The server file path where the file is stored. url: The normalized server URL pointing to the file. size: The size of the file in bytes. orig_name: The original filename before upload. mime_type: The MIME type of the file. is_stream: Indicates whether the file is a stream. meta: Additional metadata used internally (should not be changed).

    reference_text str Default: ""

    The input value that is provided in the "Reference Text" Textbox component.

    max_new_tokens float Default: 0

    The input value that is provided in the "Maximum tokens per batch, 0 means no limit" Slider component.

    chunk_length float Default: 200

    The input value that is provided in the "Iterative Prompt Length, 0 means off" Slider component.

    top_p float Default: 0.7

    The input value that is provided in the "Top-P" Slider component.

    repetition_penalty float Default: 1.2

    The input value that is provided in the "Repetition Penalty" Slider component.

    temperature float Default: 0.7

    The input value that is provided in the "Temperature" Slider component.

    seed float Default: 0

    The input value that is provided in the "Seed" Number component.

    use_memory_cache Literal['on', 'off'] Default: "on"

    The input value that is provided in the "Use Memory Cache" Radio component.

    Returns tuple of 2 elements
    [0] filepath

    The output value that appears in the "Generated Audio" Audio component.

    [1] str

    The output value that appears in the "Error Message" Html component.
    """

    text: str  # = "Hello!!"
    normalize: bool  # = False
    reference_id: str  # = "Hello!!"
    reference_audio: str  # = handle_file('https://github.com/gradio-app/gradio/raw/main/test/test_files/audio_sample.wav')
    reference_text: str  # = ""
    max_new_tokens: int  # = 0
    chunk_length: int  # = 200
    top_p: float  # = 0.7
    repetition_penalty: float  # = 1.2
    temperature: float  # = 0.7
    seed: int  # = 0
    use_memory_cache: str  # = "on"
    api_name: str  # ="/partial"


def fish_tts(text: str, ref: str, ref_text: str = ""):
    global client
    endpoint_url = os.environ.get("TTS_ENDPOINT_URL", "http://127.0.0.1:22231/")
    if client == "not_started":
        try:
            client = Client(
                endpoint_url,
                verbose=False,
                httpx_kwargs={"timeout": 120.0},
            )
        except Exception as e:
            print(
                f"[TEXT-TO-SPEECH] Cannot connect to TTS client on url {endpoint_url}: {e}\n Disabling TTS features. =("
            )
            traceback.print_exc()
            client = None
            return fish_tts(text, ref, ref_text)

    if client is not None:
        if text and len(text) > 300:
            text = text[:250] + ". Устала говорить, слишком длинный текст."
            print("[FISH GR] TTS TEXT TRUNCATED!!!!! to " + text)
        audiofile = client.predict(
            **FishTTSGradioKWArgs(
                text=text,
                # normalize=True,
                reference_id="",  # seems like its internal id in fish
                reference_audio=handle_file(ref),
                reference_text=ref_text,
                max_new_tokens=500,  # 0 means no limit, 500 tokens ~= 30 secs
                chunk_length=200,  # TODO new untested
                top_p=0.7,
                repetition_penalty=1.2,
                temperature=0.4,  # TODO UNTESTED was 0.7
                # seed=0,
                # use_memory_cache="off",  # TODO untested, was disabled
                api_name="/partial",
            )
        )[0]
        sr, data = wavfile.read(audiofile)
        data = data.astype(np.float32) / np.iinfo(np.int16).max
        data = change_pitch_speed(data, sr, 1.05)  # let's some roll =)
        return data, sr
    else:

        empty_voice_data = np.zeros(48000)
        return empty_voice_data, 48000


def fish_tts_emo(inp: dict):
    text = inp["text"]
    emotion = inp["emotion"]
    ref_text, wav_path = character.resolve_voice_ref(emotion)
    if not os.path.exists(wav_path):
        print("[FISH TTS] WARNING! NONE SAMPLE FOUND!")
    return fish_tts(text=text, ref=wav_path, ref_text=ref_text)


if __name__ == "__main__":
    import sounddevice as sd

    from utils.debug import timing_context

    # audio, sample_rate = fish_tts("Hello world", 'https://github.com/gradio-app/gradio/raw/main/test/test_files/audio_sample.wav')
    emotion: str
    for emotion in [
        "neutral",
        "happy",
        "angry",
        "sad",
        "disgusted",
        "sarcastic",
        "whispering",
    ]:
        print("Now will start generation...")
        # "disgusted", "sarcastic" bad
        with timing_context("FISH TTS test emo=" + emotion):
            audio, sample_rate = fish_tts_emo(
                {
                    "text": "Приветствую вас, дорогие друзья! Как ваши дела?",
                    "emotion": emotion,
                }
            )
        print("Got audio, sr", sample_rate, "emotion", emotion)
        with timing_context("FISH TTS sound duration"):
            sd.play(audio, 48000, blocking=True)
    print("Perform too long audio")
    with timing_context("FISH TTS test emo=" + emotion):
        audio, sample_rate = fish_tts_emo(
            {
                "text": "Приветствую вас, дорогие друзья! АХахахахах" * 10,
                "emotion": emotion,
            }
        )
    with timing_context("FISH TTS sound duration"):
        sd.play(audio, 48000, blocking=True)
