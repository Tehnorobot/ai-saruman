"""Route local operator input to the agent or the existing tool console."""


def submit_operator_input(text, ctx_handler, command_queue, *, directive=False):
    text = text.strip()
    if not text:
        return "Введите задание или команду."
    if ctx_handler is None:
        return "Персонаж ещё не инициализирован."
    if not directive and text[0] in "!>/@$":
        command_queue.put({"command": text, "env": "system"})
        return "Команда отправлена."
    ctx_handler.add_message(text, duplicate_check=False)
    print("[OPERATOR] Directive added to agent context")
    return "Задание передано персонажу. Если он остановлен, нажмите Start."
