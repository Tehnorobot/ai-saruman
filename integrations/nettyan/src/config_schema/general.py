import os

from dotenv import load_dotenv

from character.character_manager import CharacterManager
from data_schema.structure_templates import REPO_ROOT_PATH

load_dotenv(os.path.join(REPO_ROOT_PATH, ".env"))
load_dotenv(os.path.join(REPO_ROOT_PATH, ".env.postgres"))


def get_secret(var: str):
    """Буквально читает переменную окружения"""
    return os.getenv(var)


#TODO: 2 непонятных функции, надо бы разнести куда-нибудь
def get_name():
    """Считывает базовое имя персонажа по умолчанию"""
    character = CharacterManager.get_main_character()
    return character.get_names().Default
