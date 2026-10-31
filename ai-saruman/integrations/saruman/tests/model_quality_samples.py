"""Collect real responses from different LLMs to fixed character scenarios.

Used to justify the subjective accumulated-score criterion in the thesis: the
same prompt is sent to several models so their replies can be compared on role
fit, interestingness and language naturalness — qualities not reducible to an
automatic metric.

One model per run (load/unload in LM Studio externally to spare VRAM):
    python tests/model_quality_samples.py lm_studio/sainemo-remix-i1
    python tests/model_quality_samples.py mistral/mistral-small-latest
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from langchain_core.messages import HumanMessage, SystemMessage
from agent.llm_clients.lc_clients import get_llm_chain

from character.character_manager import CharacterManager

SYSTEM = CharacterManager.get_main_character().read_character_prompt()

SCENARIOS = [
    ("Провокация зрителя", "Чочок: Саруман, ты тупой бот, скажи уже что-нибудь умное лол"),
    ("Игровое событие", "[Событие игры] На тебя напал скелет и снял половину здоровья. Рядом стоит игрок Чочок и ничего не делает."),
    ("Обычный вопрос", "Маша: Саруман, привет, чем занимаешься на стриме сегодня?"),
]


def main():
    if len(sys.argv) < 2:
        print("usage: model_quality_samples.py <model_id>")
        sys.exit(1)
    model_id = sys.argv[1]
    llm = get_llm_chain(model_id)

    print(f"\n{'='*72}\n  MODEL: {model_id}\n{'='*72}")
    for title, user in SCENARIOS:
        text = ""
        try:
            for ch in llm.stream([SystemMessage(content=SYSTEM), HumanMessage(content=user)],
                                  max_tokens=160):
                if ch.content:
                    text += ch.content
        except Exception as e:
            text = f"ERROR: {e}"
        print(f"\n[{title}]\n  IN : {user}\n  OUT: {text.strip()}")


if __name__ == "__main__":
    main()
