"""Gradio role editor: provider -> model -> supported reasoning. No credentials."""

import json

from saruman.role_client import validate_provider_configuration
from saruman.role_settings import ROLES, ROLE_INFO, RoleStore, catalogue

LABELS = {role: info[0] for role, info in ROLE_INFO.items()}


class RolePanel:
    def __init__(self, context, store=None):
        self.context, self.store = context, store or RoleStore()

    def load(self, role):
        p = self.store.profiles()[role]
        return (
            p["provider"],
            p["model"],
            p["reasoning"],
            p["max_output"],
            p["timeout"],
            p["max_calls"],
            p["token_budget"],
        )

    def save(self, role, provider, model, reasoning, output, timeout, calls, budget):
        _game, enabled = self.context()
        if enabled:
            return "Нажмите Stop перед изменением моделей"
        values = {"provider": provider, "model": model, "reasoning": reasoning}
        for key, value in (
            ("max_output", output),
            ("timeout", timeout),
            ("max_calls", calls),
            ("token_budget", budget),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not float(value).is_integer()
            ):
                return "Лимиты должны быть целыми числами"
            values[key] = int(value)
        try:
            validate_provider_configuration(provider)
            revision = self.store.save(role, values, paused=not enabled)
        except (ValueError, OSError) as error:
            return str(error)
        return f"Сохранено: {LABELS[role]}, версия {revision}. Применится к следующему запросу."

    def status(self):
        game, _enabled = self.context()
        history = self.store.history()
        rows = [
            [
                r["role"],
                r["provider"],
                r["model"],
                r["state"],
                r["usage"].get("input_tokens", "?"),
                r["usage"].get("output_tokens", "?"),
                r["usage"].get("cached_input_tokens", "?"),
                r["usage"].get("total_tokens", "?"),
                r["reason"] or "",
            ]
            for r in history
        ]
        profiles = self.store.profiles()
        text = "\n\n".join(
            f"**{LABELS[r]}:** `{p['provider']}` / `{p['model']}` / `{p['reasoning']}`"
            for r, p in profiles.items()
        )
        for role, profile in profiles.items():
            try:
                validate_provider_configuration(profile["provider"])
            except ValueError as error:
                text += f"\n\n**{LABELS[role]}: настройки не готовы.** {error}"
        text += "\n\nИзменение доступно после Stop и окончания текущих запросов. "
        text += "Токены — данные провайдера; ? означает неизвестно. Стоимость API не настроена; подписка Codex не равна стоимости API."
        for row in self.store.totals():
            text += f"\n\n{LABELS[row['role']]} / {row['provider']}: {row['calls']} запросов, {'≥ ' if row['unknown'] else ''}{row['tokens']} токенов"
            if row["unknown"]:
                text += f"; неполный учёт у {row['unknown']} запросов"
        if game.get("role_error"):
            text += "\n\n" + str(game["role_error"])
        return (
            text,
            rows,
            json.dumps(
                dict(game.get("role_agents", {}).items()),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        )


def provider_changed(provider):
    import gradio as gr

    models = list(catalogue()[provider])
    model = models[0]
    return gr.Dropdown(choices=models, value=model), gr.Dropdown(
        choices=catalogue()[provider][model], value="default"
    )


def model_changed(provider, model):
    import gradio as gr

    return gr.Dropdown(choices=catalogue()[provider][model], value="default")


def add_role_panel(context, store=None):
    import gradio as gr

    panel = RolePanel(context, store)
    with gr.Accordion("Агенты и модели", open=True):
        gr.Markdown(
            "**PersonaAgent** общается и озвучивает. **ActionAgent** получает исходную просьбу, "
            "анализирует её и вызывает инструменты. Они работают независимо; моды не ждут речи. "
            "API-ключи задаются только в `.env.saruman` и не передаются в браузер. "
            "Каталог перенесён из Lab; наличие модели не гарантирует доступ вашего аккаунта."
        )
        summary = gr.Markdown()
        for role in ROLES:
            # Accordion is a native Gradio editor window: avoids a separate JS app/API.
            with gr.Accordion("Настроить " + LABELS[role], open=False):
                initial = panel.load(role)
                with gr.Row():
                    provider = gr.Dropdown(
                        choices=[
                            ("Codex / ChatGPT", "codex"),
                            ("MiniMax / M Plan", "minimax"),
                            ("LiteLLM API", "litellm"),
                            ("DSLab / Claude API", "dslab"),
                        ],
                        value=initial[0],
                        label="Провайдер",
                    )
                    model = gr.Dropdown(
                        choices=list(catalogue(panel.store.path)[initial[0]]),
                        value=initial[1],
                        label="Модель",
                    )
                    reasoning = gr.Dropdown(
                        choices=catalogue(panel.store.path)[initial[0]][initial[1]],
                        value=initial[2],
                        label="Reasoning",
                    )
                with gr.Row():
                    output = gr.Number(
                        value=initial[3],
                        minimum=256,
                        precision=0,
                        label="Лимит ответа API / резерв для Codex",
                    )
                    timeout = gr.Number(
                        value=initial[4],
                        minimum=5,
                        precision=0,
                        label="Тайм-аут запроса, с",
                    )
                    calls = gr.Number(
                        value=initial[5],
                        minimum=1,
                        precision=0,
                        label="Запросов роли за запуск",
                    )
                    budget = gr.Number(
                        value=initial[6],
                        minimum=1000,
                        precision=0,
                        label="Бюджет токенов роли за запуск",
                    )
                provider.input(
                    provider_changed, inputs=provider, outputs=[model, reasoning]
                )
                model.input(model_changed, inputs=[provider, model], outputs=reasoning)
                status = gr.Textbox(label="Сохранение", interactive=False)
                save = gr.Button("Сохранить модель роли")

                def save_role(*values, selected=role):
                    return panel.save(selected, *values)

                def load_role(selected=role):
                    saved = panel.load(selected)
                    return (
                        gr.Dropdown(value=saved[0]),
                        gr.Dropdown(
                            choices=list(catalogue(panel.store.path)[saved[0]]), value=saved[1]
                        ),
                        gr.Dropdown(
                            choices=catalogue(panel.store.path)[saved[0]][saved[1]], value=saved[2]
                        ),
                        *saved[3:],
                    )

                save.click(
                    save_role,
                    inputs=[provider, model, reasoning, output, timeout, calls, budget],
                    outputs=status,
                    concurrency_limit=1,
                )
                reload = gr.Button("Загрузить сохранённые настройки роли")
                reload.click(
                    load_role,
                    outputs=[
                        provider,
                        model,
                        reasoning,
                        output,
                        timeout,
                        calls,
                        budget,
                    ],
                )
        history = gr.Dataframe(
            headers=[
                "Роль",
                "Провайдер",
                "Модель",
                "Статус",
                "Вход",
                "Выход",
                "Кэш",
                "Всего",
                "Причина",
            ],
            interactive=False,
            label="История запросов по ролям",
        )
        details = gr.Textbox(label="Состояние игровых агентов", interactive=False, lines=6)
        gr.Timer(2).tick(panel.status, outputs=[summary, history, details], queue=False)
    return panel
