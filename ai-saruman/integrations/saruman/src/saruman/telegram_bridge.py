"""Optional Telegram adapter. No network/model calls until runtime and UI opt in."""
import importlib.util
import sys
from pathlib import Path

folder = Path(__file__).resolve().parents[3] / "telegram-gateway"
if "_saruman_telegram" not in sys.modules:
    spec = importlib.util.spec_from_file_location("_saruman_telegram", folder / "__init__.py",
                                                submodule_search_locations=[str(folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from _saruman_telegram.automation import Director, POST_PROMPT, POST_SCHEMA, record_goal  # noqa: E402,F401


def make_director(role_store, session, character_prompt, telegram_store=None):
    from saruman.role_client import RoleClient

    client = RoleClient(role_store, "telegram", session + ":telegram")

    def draft(payload):
        client.resume()
        return client.complete(character_prompt + POST_PROMPT, payload, POST_SCHEMA)

    director = Director(draft, telegram_store)
    director.profile = lambda: role_store.profiles()["telegram"]
    return director
