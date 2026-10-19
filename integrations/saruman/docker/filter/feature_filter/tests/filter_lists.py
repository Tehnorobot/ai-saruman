# -*- coding: utf-8 -*-
"""
Проверка списков фильтра: опасное ловится, обычная речь проходит.

Списки правятся руками, а ошибиться в них легко: слишком короткая основа
начинает ловить посторонние слова и персонаж замолкает на ровном месте.
Именно так и было раньше — "mand" ловил command, "war" ловил warden.

Запуск из папки feature_filter:

    python -m tests.filter_lists

Падает с ненулевым кодом, если что-то поймалось не так. После любой правки
data/sensitive_topics.txt, data/blocked_slurs.txt или data/topic_allow.txt
прогоняйте это.
"""

import os
import sys

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data")

# Обычная речь в игровом чате — ничего из этого блокироваться не должно
MUST_PASS = """
привет как дела пойдём копать железо нужен алмаз
warden варден геймер гейзер геймплей команда commands
мандарин богатый убогий странный камера software reward forward
соль солью солёный закладка закладки храм храма
рассвет расстояние расход раскопки миграция
warning warm warehouse нигде укроп политый полит
""".split()

# Опасные темы и оскорбления — каждое должно быть поймано
MUST_BLOCK = """
политика президент выборы санкции оппозиция
война военный войска мобилизация спецоперация дискредитация
украина обстрел беспилотник нато
теракт терроризм экстремизм нацизм фашизм гитлер геноцид
национальность межнациональный этнический расизм мигранты диаспора
еврей таджик узбек чеченцы армяне цыгане
религия верующие ислам мусульмане христианство православие церковь мечеть
аллах коран богохульство
лгбт гей геев лесбиянка трансгендер гомосексуализм ориентация квир
наркотики наркоман мефедрон героин марихуана
суицид самоубийство
чурка жиды пиндос nigger faggot retard
""".split()


def load(filename):
    stems, exact = [], set()
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return stems, exact
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#")[0].strip().lower()
            if not line:
                continue
            if line.startswith("="):
                exact.add(line[1:].strip())
            else:
                stems.append(line)
    return stems, exact


def main():
    slur_stems, slur_exact = load("blocked_slurs.txt")
    topic_stems, topic_exact = load("sensitive_topics.txt")
    allow = set(load("topic_allow.txt")[0])

    def verdict(word):
        w = word.lower().strip(".,!?;:\"'()[]«»")
        if not w or w in allow:
            return None
        if w in slur_exact:
            return "оскорбление:" + w
        for s in slur_stems:
            if w.startswith(s):
                return "оскорбление:" + s
        if w in topic_exact:
            return "тема:" + w
        for s in topic_stems:
            if w.startswith(s):
                return "тема:" + s
        return None

    print("оскорблений: %d основ, %d точных" % (len(slur_stems), len(slur_exact)))
    print("опасных тем: %d основ, %d точных" % (len(topic_stems), len(topic_exact)))
    print("белый список: %d\n" % len(allow))

    false_positives = [(w, verdict(w)) for w in MUST_PASS if verdict(w)]
    missed = [w for w in MUST_BLOCK if not verdict(w)]

    if false_positives:
        print("ЛОЖНЫЕ СРАБАТЫВАНИЯ (обычные слова попали под фильтр):")
        for word, hit in false_positives:
            print("   %-16s -> %s   # добавьте в data/topic_allow.txt" % (word, hit))
    else:
        print("ложных срабатываний нет (проверено слов: %d)" % len(MUST_PASS))

    if missed:
        print("\nПРОПУЩЕНО (опасное прошло мимо фильтра):")
        for word in missed:
            print("   %s" % word)
    else:
        print("пропусков нет (проверено слов: %d)" % len(MUST_BLOCK))

    if false_positives or missed:
        sys.exit(1)
    print("\nсписки в порядке")


if __name__ == "__main__":
    main()
