"""Просмотр содержимого базы модерации.

База в репозиторий не входит: она наполняется реальной перепиской пользователей,
такому в открытом репозитории не место. Укажите путь к своей базе:

    python database_viewer.py путь/к/базе.db
"""

import os
import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "FILTER_DB_PATH", "filter.db"
)

if not os.path.exists(db_path):
    print(f"База не найдена: {db_path}")
    print("Передайте путь аргументом или задайте переменную FILTER_DB_PATH.")
    sys.exit(1)

# Подключение к базе данных
conn = sqlite3.connect(db_path)
cursor = conn.cursor()


# 2. Получение списка таблиц
def list_tables():
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Таблицы в базе данных:")
    for table in tables:
        print(table[0])  # выводим имя таблицы


# 3. Получение данных из таблицы
def fetch_data_from_table(table_name):
    cursor.execute(f"SELECT * FROM {table_name};")
    rows = cursor.fetchall()
    print(f"\nДанные из таблицы '{table_name}':")
    for row in rows:
        print(row)


# Основная логика
if __name__ == "__main__":
    list_tables()  # Выводим список таблиц

    # Запрашиваем у пользователя имя таблицы для вывода данных
    table_name = input("\nВведите имя таблицы, чтобы увидеть данные: ")
    fetch_data_from_table(table_name)

# Закрытие соединения с базой данных
conn.close()
