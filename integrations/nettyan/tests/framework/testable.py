from typing import Any, TypeVar, Generic

T = TypeVar('T')

class Testable(Generic[T]):
    """Обертка для любого значения с методами тестирования"""
    
    def __init__(self, value: T):
        self._value = value
    
    def should_equal(self, expected: Any, message: str = "") -> "Testable[T]":
        """Проверяет равенство"""
        print(f"\n🔍 {message if message else 'Проверка равенства'}")
        try:
            assert self._value == expected, f"Ожидали: {expected}, Получили: {self._value}"
            print(f"   ✅ {self._value} == {expected}")
        except AssertionError as e:
            print(f"   ❌ {e}")
        return self
    
    def should_not_equal(self, expected: Any, message: str = "") -> "Testable[T]":
        """Проверяет неравенство"""
        print(f"\n🔍 {message if message else 'Проверка неравенства'}")
        try:
            assert self._value != expected, f"Значение не должно быть равно {expected}"
            print(f"   ✅ {self._value} != {expected}")
        except AssertionError:
            print(f"   ❌ Значение равно {expected}")
        return self
    
    def should_be(self, expected: Any, message: str = "") -> "Testable[T]":
        """Проверяет идентичность объектов (is)"""
        print(f"\n🔍 {message if message else 'Проверка идентичности'}")
        try:
            assert self._value is expected, f"Объекты должны быть идентичными"
            print(f"   ✅ Объекты идентичны")
        except AssertionError:
            print(f"   ❌ Объекты разные")
        return self
    
    def should_be_true(self, message: str = "") -> "Testable[T]":
        """Проверяет что значение истинно"""
        print(f"\n🔍 {message if message else 'Проверка на истинность'}")
        try:
            assert bool(self._value), f"Значение должно быть истинным"
            print(f"   ✅ {self._value} истинно")
        except AssertionError:
            print(f"   ❌ {self._value} ложно")
        return self
    
    def should_be_false(self, message: str = "") -> "Testable[T]":
        """Проверяет что значение ложно"""
        print(f"\n🔍 {message if message else 'Проверка на ложность'}")
        try:
            assert not bool(self._value), f"Значение должно быть ложным"
            print(f"   ✅ {self._value} ложно")
        except AssertionError:
            print(f"   ❌ {self._value} истинно")
        return self
    
    def should_contain(self, item: Any, message: str = "") -> "Testable[T]":
        """Проверяет содержит ли значение элемент"""
        print(f"\n🔍 {message if message else f'Проверка на содержание {item}'}")
        try:
            assert item in self._value, f"Должно содержать {item}"
            print(f"   ✅ Содержит {item}")
        except (AssertionError, TypeError):
            print(f"   ❌ Не содержит {item} или тип не поддерживает оператор 'in'")
        return self
    
    def transform(self, func, message: str = "") -> "Testable":
        """Применяет функцию к значению и возвращает новый Testable"""
        print(f"\n🔄 {message if message else 'Преобразование'}")
        result = func(self._value)
        print(f"   {self._value} → {result}")
        return Testable(result)
    
    @property
    def value(self) -> T:
        """Возвращает исходное значение"""
        return self._value
    
    def __repr__(self) -> str:
        return f"Testable({self._value})"


# Пример использования
if __name__ == "__main__":
    from tests.framework.test_helper import TestHelper
    
    TestHelper.announce_testing(__file__)
    
    # Цепочка проверок
    TestHelper.test(10 + 5)\
        .should_equal(15, "Сложение")\
        .should_not_equal(0, "Не должно быть нулем")\
        .should_be_true("Должно быть истинным")
    
    # Работа со строками
    TestHelper.test("Hello World")\
        .should_equal("Hello World", "Проверка строки")\
        .should_contain("World", "Должно содержать 'World'")\
        .transform(lambda s: s.upper(), "В верхний регистр")\
        .should_equal("HELLO WORLD", "После преобразования")
    
    # Работа со списками
    TestHelper.test([1, 2, 3])\
        .should_contain(2, "Список должен содержать 2")\
        .transform(lambda lst: sum(lst), "Сумма элементов")\
        .should_equal(6, "Сумма должна быть 6")
    
    # Работа с булевыми значениями
    TestHelper.test(True).should_be_true("True должно быть истинным")
    TestHelper.test(False).should_be_false("False должно быть ложным")
    
    # Словари
    TestHelper.test({"a": 1, "b": 2})\
        .should_contain("a", "Должен содержать ключ 'a'")\
        .transform(lambda d: d.get("a"), "Получение значения по ключу 'a'")\
        .should_equal(1, "Значение должно быть 1")