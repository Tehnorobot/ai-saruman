from typing import Any
from tests.framework.testable import Testable


class TestHelper:
    """Утилити-методы для тестирования"""

    @staticmethod
    def announce_testing(module_name: str):
        print("="*50)
        print("Тесты для ", module_name)
        print("="*50)
    
    @staticmethod
    def test(value: Any) -> Testable:
        """Создает тестируемый объект из любого значения"""
        return Testable(value)
