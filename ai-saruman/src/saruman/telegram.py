"""Load the dependency-free Telegram package without mixing the game environment."""
import importlib.util
import sys
from pathlib import Path

folder = Path(__file__).resolve().parents[2] / "integrations/telegram-gateway"
if "_saruman_telegram" not in sys.modules:
    spec = importlib.util.spec_from_file_location("_saruman_telegram", folder / "__init__.py",
                                                submodule_search_locations=[str(folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from _saruman_telegram.automation import Control, Director, Store, POST_PROMPT, POST_SCHEMA  # noqa: E402
from _saruman_telegram.conversations import Conversations, REPLY_PROMPT, REPLY_SCHEMA
