"""
Модуль конфигурации

Предоставляет единую точку доступа к настройкам приложения через Pydantic Settings.
Поддерживает загрузку из .env файлов и переменных окружения.

Пример использования:
    from src.Config import Settings
    
    settings = Settings.get()
    print(settings.character.main_character_subdir)
"""

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CharacterSettings(BaseSettings):
    """Пример блока переменных. Хранит единственную настройку - имя активного персонажа"""
    model_config = SettingsConfigDict(
        env_prefix="NETTYAN_CHARACTER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    name: str = Field(
        default="NetTyan",
        description="Путь до папки активного персонажа в папке Characters"
    )


class Settings:
    """
    Единая точка доступа к конфигурации приложения.
    
    Поддерживает:
    - Загрузку из .env файла
    - Переопределение через переменные окружения
    - Singleton паттерн для гарантии одного экземпляра
    
    Пример использования:
        settings = Settings.get()
        print(settings.character.main_character_subdir)
        
        # Для тестов можно сбросить singleton
        Settings.reset()
    """
    _instance: Optional["Settings"] = None
    _initialized: bool = False
    
    def __new__(cls) -> "Settings":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self) -> None:
        if self._initialized:
            return
        
        self.main_character = CharacterSettings()
        self._initialized = True
    
    @classmethod
    def get(cls) -> "Settings":
        """Получить singleton экземпляр Settings."""
        return cls()
    
    @classmethod
    def reset(cls) -> None:
        """Сбросить singleton (полезно для тестов)."""
        cls._instance = None
