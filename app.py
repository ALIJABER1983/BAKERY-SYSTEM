from __future__ import annotations

import json
import os
import sqlite3
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory, session

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('DB_PATH', str(BASE_DIR / 'database' / 'bakery.sqlite3')))
DEFAULT_CONFIG_PATH = BASE_DIR / 'default_master_config.json'
APP_STATE_DEFAULTS = {
    'bakery_sales_v1': {'customers': {}},
    'bakery_expenses_v1': {'entries': {}},
    'bakery_worker_wages_v1': {'entries': {}},
}


def load_default_master_config() -> dict[str, Any]:
    return json.loads(DEFAULT_CONFIG_PATH.read_text(encoding='utf-8'))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def upsert_user(conn: sqlite3.Connection, user: dict[str, Any]) -> None:
    conn.execute(
        '''
        INSERT INTO users(username, password, role, active, label)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
          password=excluded.password,
          role=excluded.role,
          active=excluded.active,
          label=excluded.label
        ''',
        (
            user['username'],
            user['password'],
            user.get('role', 'entry'),
            1 if user.get('active', True) else 0,
            user.get('label', user['username']),
        ),
    )


def sync_users_from_config(conn: sqlite3.Connection, config: dict[str, Any]) -> None:
    usernames = []
    for user in config.get('users', []):
        usernames.append(user['username'])
        upsert_user(conn, user)
    if usernames:
        placeholders = ','.join('?' for _ in usernames)
        conn.execute(f'DELETE FROM users WHERE username NOT IN ({placeholders})', usernames)
    conn.commit()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    conn.execute(
        'CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)'
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS users (
          username TEXT PRIMARY KEY,
          password TEXT NOT NULL,
          role TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          label TEXT NOT NULL
        )
        '''
    )
    row = conn.execute(
        "SELECT value FROM app_state WHERE key='bakery_master_config_v1'"
    ).fetchone()
    if row is None:
        default_config = load_default_master_config()
        conn.execute(
            'INSERT INTO app_state(key, value) VALUES (?, ?)',
            ('bakery_master_config_v1', json.dumps(default_config, ensure_ascii=False)),
        )
        sync_users_from_config(conn, default_config)
    else:
        existing_config = json.loads(row['value'])
        sync_users_from_config(conn, existing_config)

    for key, value in APP_STATE_DEFAULTS.items():
        conn.execute(
            'INSERT OR IGNORE INTO app_state(key, value) VALUES (?, ?)',
            (key, json.dumps(value, ensure_ascii=False)),
        )
    conn.commit()
    conn.close()


app = Flask(__name__, static_folder='public', static_url_path='/static')
app.secret_key = os.environ.get('SECRET_KEY', 'bakery-change-me-in-production')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)
app.wsgi_app = app.wsgi_app


@app.after_request
def add_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


def current_user() -> dict[str, Any] | None:
    username = session.get('username')
    if not username:
        return None
    conn = get_db()
    row = conn.execute(
        'SELECT username, role, active, label FROM users WHERE username = ?', (username,)
    ).fetchone()
    conn.close()
    if not row or not row['active']:
        session.clear()
        return None
    return dict(row)


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({'ok': False, 'error': 'AUTH_REQUIRED'}), 401
        request.user = user
        return fn(*args, **kwargs)

    return wrapper


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({'ok': False, 'error': 'AUTH_REQUIRED'}), 401
        if user['role'] != 'admin':
            return jsonify({'ok': False, 'error': 'ADMIN_REQUIRED'}), 403
        request.user = user
        return fn(*args, **kwargs)

    return wrapper


def read_state(key: str) -> Any:
    conn = get_db()
    row = conn.execute('SELECT value FROM app_state WHERE key=?', (key,)).fetchone()
    conn.close()
    if row is None:
        return None
    return json.loads(row['value'])


def write_state(key: str, value: Any) -> None:
    conn = get_db()
    conn.execute(
        '''
        INSERT INTO app_state(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
        ''',
        (key, json.dumps(value, ensure_ascii=False)),
    )
    if key == 'bakery_master_config_v1':
        sync_users_from_config(conn, value)
    else:
        conn.commit()
    conn.close()


@app.get('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.get('/health')
def health():
    return jsonify({'ok': True})


@app.post('/api/login')
def login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    password = payload.get('password') or ''
    if not username or not password:
        return jsonify({'ok': False, 'error': 'MISSING_CREDENTIALS'}), 400

    conn = get_db()
    row = conn.execute(
        'SELECT username, password, role, active, label FROM users WHERE username = ?',
        (username,),
    ).fetchone()
    conn.close()
    if not row or row['password'] != password or not row['active']:
        return jsonify({'ok': False, 'error': 'INVALID_LOGIN'}), 401

    session['username'] = row['username']
    return jsonify(
        {
            'ok': True,
            'user': {
                'username': row['username'],
                'role': row['role'],
                'label': row['label'],
            },
        }
    )


@app.post('/api/logout')
@require_login
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.get('/api/session')
def session_info():
    user = current_user()
    if not user:
        return jsonify({'ok': False, 'user': None})
    return jsonify({'ok': True, 'user': user})


@app.get('/api/bootstrap')
@require_login
def bootstrap():
    data = {
        'bakery_master_config_v1': read_state('bakery_master_config_v1'),
        'bakery_sales_v1': read_state('bakery_sales_v1'),
        'bakery_expenses_v1': read_state('bakery_expenses_v1'),
        'bakery_worker_wages_v1': read_state('bakery_worker_wages_v1'),
        'bakery_session_v1': {
            'username': request.user['username'],
            'role': request.user['role'],
            'label': request.user['label'],
        },
    }
    return jsonify({'ok': True, 'store': data, 'user': request.user})


@app.post('/api/state/batch')
@require_login
def save_batch():
    payload = request.get_json(silent=True) or {}
    updates = payload.get('updates') or {}
    if not isinstance(updates, dict):
        return jsonify({'ok': False, 'error': 'BAD_PAYLOAD'}), 400

    allowed_keys = {
        'bakery_sales_v1',
        'bakery_expenses_v1',
        'bakery_worker_wages_v1',
        'bakery_master_config_v1',
    }
    for key, value in updates.items():
        if key not in allowed_keys:
            continue
        if key == 'bakery_master_config_v1' and request.user['role'] != 'admin':
            return jsonify({'ok': False, 'error': 'ADMIN_REQUIRED'}), 403
        write_state(key, value)
    return jsonify({'ok': True})


@app.get('/api/export/database')
@require_admin
def export_database():
    return send_from_directory(DB_PATH.parent, DB_PATH.name, as_attachment=True)


if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', '8000'))
    app.run(host='0.0.0.0', port=port, debug=False)
