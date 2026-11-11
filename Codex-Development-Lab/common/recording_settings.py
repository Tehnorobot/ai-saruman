"""Durable operator settings for optional test recordings.

The worker reads the value when preparing each new runner request. A change
does not alter an active verification session or any product test timeout.
"""
from common import db

DEFAULT_MAX_SECONDS = 300
_KEY = 'recording.max_seconds'


def validate_max_seconds(value):
    if type(value) is not int or value < 1:
        raise ValueError('Максимальная длина записи должна быть положительным целым числом секунд')
    return value


def _table(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS lab_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )''')


def _view(value):
    return {'max_seconds': value, 'scope': 'global', 'applies_to': 'next_test_run'}


def get_settings():
    with db.connect() as connection:
        _table(connection)
        row = connection.execute('SELECT value FROM lab_settings WHERE key=?', (_KEY,)).fetchone()
    # Text storage preserves an operator's integer without SQLite's 64-bit cap.
    value = DEFAULT_MAX_SECONDS if row is None else validate_max_seconds(int(row['value']))
    return _view(value)


def max_seconds():
    return get_settings()['max_seconds']


def set_max_seconds(value):
    value = validate_max_seconds(value)
    with db.connect() as connection:
        _table(connection)
        connection.execute('''INSERT INTO lab_settings(key,value,updated_at) VALUES(?,?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at''',
            (_KEY, str(value), db.utc()))
    return _view(value)
