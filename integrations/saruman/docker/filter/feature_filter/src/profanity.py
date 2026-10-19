# -*- coding: utf-8 -*-
"""
Поиск и маскировка мата — своя реализация вместо сторонней библиотеки.

Зачем своя: раньше здесь лежала вендоренная копия Py-Censure без лицензии,
из-за чего у проекта был лицензионный хвост. Функционально оттуда
использовалась ровно одна возможность — найти в строке мат и заменить его
заглушкой, причём английская ветка была помечена автором как нерабочая.

Как это работает:

1. Слово приводится к канону под нужный язык. Мат в чате почти никогда не
   пишут прямо: разбавляют буквами другого алфавита, цифрами, точками
   и повторами. Для русского латиница-двойники переводятся в кириллицу,
   для английского — наоборот. Цифры превращаются в похожие буквы,
   повторы от трёх подряд схлопываются.
2. Корень ищется НЕ где попало в слове, а с начала — допускается лишь
   цепочка обычных приставок. Иначе "манда" ловит "команду" и "мандарин",
   а "хуй" — "застрахуй"; именно на этом ломаются наивные фильтры.
3. Слова из белого списка не проверяются вообще — туда идут законные слова,
   которые всё же совпали.

Данные лежат рядом, в data/:
    profanity_ru.txt     корни русского мата
    profanity_en.txt     корни английского
    profanity_allow.txt  белый список

Совместимость: clean_line возвращает тот же кортеж, что и прежняя библиотека,
поэтому вызывающий код менять не пришлось.
"""

from __future__ import unicode_literals

import os
import re

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data")

BEEP = "[beep]"

# Чем маскируют кириллицу: латинские двойники и цифры
TO_CYRILLIC = {
    "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
    "o": "о", "p": "р", "t": "т", "x": "х", "y": "у", "u": "и",
    "@": "а", "$": "с", "0": "о", "1": "и", "3": "з", "4": "ч", "6": "б",
    "9": "д", "7": "т", "ё": "е", "й": "и",
}

# Чем маскируют латиницу: кириллические двойники и цифры
TO_LATIN = {
    "а": "a", "в": "b", "с": "c", "е": "e", "н": "h", "к": "k", "м": "m",
    "о": "o", "р": "p", "т": "t", "х": "x", "у": "y", "и": "u",
    "@": "a", "$": "s", "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
    "7": "t", "8": "b",
}

# Приставки, после которых корень всё ещё остаётся матом: "на-хуй", "за-ебал"
RU_PREFIXES = (
    "не", "на", "по", "за", "до", "пере", "под", "от", "ото", "у", "вы",
    "при", "про", "раз", "рас", "разо", "изо", "из", "во", "в", "об", "о",
    "с", "со", "ни", "и", "а",
)
EN_PREFIXES = ("mother", "bull", "dumb", "jack", "horse", "un", "re")

SEPARATORS = re.compile(r"[\s\.\-_,:;!\?\*'\"`~^|/\\()\[\]{}+=]+")
KEEP_CYRILLIC = re.compile(r"[^а-я]+")
KEEP_LATIN = re.compile(r"[^a-z]+")


def _load_list(filename):
    path = os.path.join(DATA_DIR, filename)
    items = []
    if not os.path.exists(path):
        return items
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#")[0].strip().lower()
            if line:
                items.append(line)
    return items


def canon(word, lang="ru"):
    """Приводит слово к канону выбранного языка."""
    word = word.lower()
    if lang == "ru":
        word = "".join(TO_CYRILLIC.get(ch, ch) for ch in word)
        word = KEEP_CYRILLIC.sub("", word)
    else:
        word = "".join(TO_LATIN.get(ch, ch) for ch in word)
        word = KEEP_LATIN.sub("", word)
    return re.sub(r"(.)\1{2,}", r"\1", word)


class ProfanityFilter(object):
    """Ищет мат в строке и заменяет найденное заглушкой."""

    _cache = {}

    def __init__(self, lang="ru"):
        self.lang = lang
        self.roots = _load_list("profanity_%s.txt" % lang)
        self.allow = set(canon(w, lang) for w in _load_list("profanity_allow.txt"))
        prefixes = RU_PREFIXES if lang == "ru" else EN_PREFIXES
        # корень должен стоять в начале слова или сразу после цепочки приставок
        pref = "|".join(sorted(prefixes, key=len, reverse=True))
        self._patterns = [
            (root, re.compile(r"^(?:%s){0,2}%s" % (pref, re.escape(root))))
            for root in self.roots
        ]

    @classmethod
    def get(cls, lang="ru"):
        if lang not in cls._cache:
            cls._cache[lang] = cls(lang)
        return cls._cache[lang]

    def find_root(self, word_canon):
        """Возвращает сработавший корень или None."""
        if not word_canon or word_canon in self.allow:
            return None
        for root, pattern in self._patterns:
            if pattern.match(word_canon):
                return root
        return None

    def clean_line(self, line):
        """
        Возвращает кортеж, совместимый с прежней библиотекой:
        (очищенная строка, число матерных слов, число матерных фраз,
         список матерных слов, список матерных фраз, список сработавших корней)
        """
        if not line:
            return ("", 0, 0, [], [], [])

        bad_words, matched_roots, out = [], [], []

        for token in re.split(r"(\s+)", line):
            if not token.strip():
                out.append(token)
                continue
            root = self.find_root(canon(token, self.lang))
            if root:
                bad_words.append(token)
                matched_roots.append(root)
                out.append(BEEP)
            else:
                out.append(token)

        cleaned = "".join(out)

        # Мат, растащенный разделителями между словами: "х у й", "пи зда".
        # Проверяем строку целиком, склеенную в одно слово.
        bad_phrases = []
        if not bad_words:
            glued = canon(SEPARATORS.sub("", line), self.lang)
            if len(glued) <= 40:
                root = self.find_root(glued)
                if root:
                    bad_phrases.append(line.strip())
                    matched_roots.append(root)
                    cleaned = BEEP

        return (cleaned, len(bad_words), len(bad_phrases),
                bad_words, bad_phrases, matched_roots)

    def is_clean(self, line):
        info = self.clean_line(line)
        return info[1] == 0 and info[2] == 0


if __name__ == "__main__":
    for lang, clean, dirty in (
        ("ru",
         ["команда commands мандарин богатый странный камера",
          "Оскорблять, застрахуй, Хернандес приехал",
          "Пойдём копать железо к порталу, нужен варден"],
         ["Хуевый коммент", "иди нахуй", "п.и.з.д.е.ц", "xyйня", "ЕБАТЬ"]),
        ("en",
         ["nice work bro", "software warden reward camera command"],
         ["what the fuck is this", "you are a bitch", "bullshit"]),
    ):
        f = ProfanityFilter.get(lang)
        print("\n=== %s: корней %d ===" % (lang, len(f.roots)))
        for s in clean:
            r = f.clean_line(s)
            print("  %-6s %-48s %s" % (
                "ОК" if r[1] == 0 and r[2] == 0 else "ЛОЖНО", s[:48], r[3] or ""))
        for s in dirty:
            r = f.clean_line(s)
            print("  %-6s %-28s -> %s" % (
                "ОК" if r[1] or r[2] else "ПРОПУСК", s[:28], r[0][:34]))
