"""Save comparable Russian voice samples through the same Fish client as runtime."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    from saruman.launcher import load_environment

    load_environment(ROOT / ".env.saruman")
    from scipy.io import wavfile

    from tts.fish.fish_gr import character, fish_tts_emo, voice_synthesis_options

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--text",
        default="Я осмотрю окрестности, посажу дуб у воды и вернусь, когда дерево вырастет.",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "data/voice-previews/v2")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for emotion in ("neutral", "angry"):
        reference_text, reference_path = character.resolve_voice_ref(emotion)
        options = voice_synthesis_options(emotion)
        samples, rate = fish_tts_emo({"text": args.text, "emotion": emotion})
        target = args.output / f"saruman-{emotion}-ru.wav"
        wavfile.write(target, rate, samples)
        records.append(
            {
                "emotion": emotion,
                "text": args.text,
                "file": target.name,
                "reference": Path(reference_path).name,
                "reference_sha256": hashlib.sha256(
                    Path(reference_path).read_bytes()
                ).hexdigest(),
                "reference_text": reference_text,
                **options,
                "sample_rate": rate,
                "seconds": len(samples) / rate,
            }
        )
        print(f"Saved {target} ({len(samples) / rate:.2f} s)")
    (args.output / "manifest.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
