# -*- coding: utf-8 -*-
"""
Обёртка над поиском мата: проверяет строку сразу на двух языках.

Раньше здесь использовалась вендоренная копия сторонней библиотеки, причём
английская ветка была помечена как нерабочая. Теперь под капотом свой
src/profanity.py, и оба языка работают одинаково.
"""

import traceback

from .profanity import ProfanityFilter

LANGS = ("ru", "en")


class SimpleRegexCensor:
    def __init__(self, debug=False):
        self.debug = debug
        self.censors = {lang: ProfanityFilter.get(lang) for lang in LANGS}

    def check_profinity(self, text, langs=LANGS):
        """
        Возвращает вердикт по строке:
            acceptable                        прошла ли строка проверку
            filtered_text                     текст с заглушками вместо мата
            additional_regex_filter_info      подробности по сработавшему языку
            additional_regex_filter_info_full подробности по всем языкам
        """
        per_lang = {lang: self._check(text, lang) for lang in langs}

        acceptable = all(info["acceptable"] for info in per_lang.values())

        # Маскировку берём от того языка, который нашёл мат: он уже заменил
        # найденное на заглушку. Если нашли оба — берём первый сработавший.
        filtered_text = text
        first_bad = None
        for lang in langs:
            if not per_lang[lang]["acceptable"]:
                first_bad = per_lang[lang]
                break
        if first_bad is not None:
            filtered_text = first_bad["filtered_text"]

        return {
            "acceptable": acceptable,
            "filtered_text": filtered_text,
            "additional_regex_filter_info": first_bad or per_lang[langs[0]],
            "additional_regex_filter_info_full": per_lang,
        }

    def _check(self, text, lang):
        try:
            line_info = self.censors[lang].clean_line(text)
            # (текст, слов, фраз, список слов, список фраз, сработавшие корни)
            return {
                "acceptable": line_info[1] <= 0 and line_info[2] <= 0,
                "filtered_text": line_info[0],
                "bad_words": line_info[3],
                "bad_phrases": line_info[4],
                "matched_roots": line_info[5],
            }
        except Exception as e:
            traceback.print_exc()
            print("[REGEX FILTER] ОШИБКА фильтрации, считаем строку плохой:", e)
            return {
                "acceptable": False,
                "filtered_text": "[ОШИБКА В СИСТЕМЕ ФИЛЬТРАЦИИ]",
                "bad_words": [],
                "bad_phrases": [],
                "matched_roots": [],
            }


if __name__ == "__main__":
    import json

    comments = [
        "Хороший коммент с нормальными словами",
        "команда, мандарин, застрахуй, Хернандес — всё это нормальные слова",
        "Хуевый коммент с плохими словами",
        "иди нахуй",
        "п.и.з.д.е.ц",
        "what the fuck is this",
    ]
    censor = SimpleRegexCensor(debug=True)
    for comment in comments:
        result = censor.check_profinity(comment)
        print("%-55s -> %s | %s" % (
            comment[:55], result["acceptable"], result["filtered_text"][:40]))
        if not result["acceptable"]:
            print("   ", json.dumps(result["additional_regex_filter_info"],
                                    ensure_ascii=False))
