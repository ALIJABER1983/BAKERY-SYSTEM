from __future__ import annotations

import json
import os
import sqlite3
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover
    psycopg = None
    dict_row = None

BASE_DIR = Path(__file__).resolve().parent
SQLITE_DB_PATH = Path(os.environ.get('DB_PATH', str(BASE_DIR / 'database' / 'bakery.sqlite3')))
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
DEFAULT_CONFIG_PATH = BASE_DIR / 'default_master_config.json'
SEED_SQLITE_PATH = Path(os.environ.get('SEED_SQLITE_PATH', str(BASE_DIR / 'database' / 'bakery.sqlite3')))
IS_POSTGRES = bool(DATABASE_URL)
APP_STATE_DEFAULTS = {
    'bakery_sales_v1': {'customers': {}},
    'bakery_expenses_v1': {'entries': {}},
    'bakery_worker_wages_v1': {'entries': {}},
}


def load_default_master_config() -> dict[str, Any]:
    return json.loads(DEFAULT_CONFIG_PATH.read_text(encoding='utf-8'))


def get_db():
    if IS_POSTGRES:
        if psycopg is None:
            raise RuntimeError('psycopg is not installed')
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        conn.autocommit = False
        return conn

    SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def normalize_row(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    if hasattr(row, 'keys'):
        return dict(row)
    return row


def fetchone(query: str, params: tuple = ()):
    conn = get_db()
    try:
        row = conn.execute(query, params).fetchone()
        conn.commit()
        return normalize_row(row)
    finally:
        conn.close()


def fetchall(query: str, params: tuple = ()):
    conn = get_db()
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return [normalize_row(r) for r in rows]
    finally:
        conn.close()


def upsert_user(conn, user: dict[str, Any]) -> None:
    if IS_POSTGRES:
        conn.execute(
            """
            INSERT INTO users(username, password, role, active, label)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT(username) DO UPDATE SET
              password=excluded.password,
              role=excluded.role,
              active=excluded.active,
              label=excluded.label
            """,
            (
                user['username'],
                user['password'],
                user.get('role', 'entry'),
                bool(user.get('active', True)),
                user.get('label', user['username']),
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO users(username, password, role, active, label)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
              password=excluded.password,
              role=excluded.role,
              active=excluded.active,
              label=excluded.label
            """,
            (
                user['username'],
                user['password'],
                user.get('role', 'entry'),
                1 if user.get('active', True) else 0,
                user.get('label', user['username']),
            ),
        )


def sync_users_from_config(conn, config: dict[str, Any]) -> None:
    usernames = []
    for user in config.get('users', []):
        usernames.append(user['username'])
        upsert_user(conn, user)
    if usernames:
        placeholders = ','.join(['%s' if IS_POSTGRES else '?'] * len(usernames))
        conn.execute(f'DELETE FROM users WHERE username NOT IN ({placeholders})', usernames)
    conn.commit()


def import_seed_from_sqlite_if_needed(conn) -> None:
    if not IS_POSTGRES:
        return
    if os.environ.get('MIGRATE_SQLITE_ON_FIRST_RUN', 'true').lower() not in {'1', 'true', 'yes'}:
        return
    if not SEED_SQLITE_PATH.exists():
        return
    row = conn.execute('SELECT COUNT(*) AS c FROM app_state').fetchone()
    if row and int(row['c']) > 0:
        return

    seed = sqlite3.connect(SEED_SQLITE_PATH)
    seed.row_factory = sqlite3.Row
    try:
        tables = {r['name'] for r in seed.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if 'users' in tables:
            for r in seed.execute('SELECT username, password, role, active, label FROM users').fetchall():
                upsert_user(conn, dict(r))
        if 'app_state' in tables:
            for r in seed.execute('SELECT key, value FROM app_state').fetchall():
                conn.execute(
                    """
                    INSERT INTO app_state(key, value) VALUES (%s, %s)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (r['key'], r['value']),
                )
        conn.commit()
    finally:
        seed.close()


def init_db() -> None:
    conn = get_db()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        if IS_POSTGRES:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                  username TEXT PRIMARY KEY,
                  password TEXT NOT NULL,
                  role TEXT NOT NULL,
                  active BOOLEAN NOT NULL DEFAULT TRUE,
                  label TEXT NOT NULL
                )
                """
            )
            import_seed_from_sqlite_if_needed(conn)
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                  username TEXT PRIMARY KEY,
                  password TEXT NOT NULL,
                  role TEXT NOT NULL,
                  active INTEGER NOT NULL DEFAULT 1,
                  label TEXT NOT NULL
                )
                """
            )

        row = conn.execute(
            "SELECT value FROM app_state WHERE key=%s" if IS_POSTGRES else "SELECT value FROM app_state WHERE key=?",
            ('bakery_master_config_v1',),
        ).fetchone()
        if row is None:
            default_config = load_default_master_config()
            conn.execute(
                "INSERT INTO app_state(key, value) VALUES (%s, %s) ON CONFLICT(key) DO NOTHING" if IS_POSTGRES else "INSERT OR IGNORE INTO app_state(key, value) VALUES (?, ?)",
                ('bakery_master_config_v1', json.dumps(default_config, ensure_ascii=False)),
            )
            sync_users_from_config(conn, default_config)
        else:
            existing_config = json.loads(row['value'])
            sync_users_from_config(conn, existing_config)
            sync_users_from_config(conn, load_default_master_config())

        for key, value in APP_STATE_DEFAULTS.items():
            conn.execute(
                "INSERT INTO app_state(key, value) VALUES (%s, %s) ON CONFLICT(key) DO NOTHING" if IS_POSTGRES else "INSERT OR IGNORE INTO app_state(key, value) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
        conn.commit()
    finally:
        conn.close()


app = Flask(__name__, static_folder='public', static_url_path='/static')
@app.route("/")
def home():
    return send_from_directory(".", "index.html")

@app.route("/index.html")
def home_index():
    return send_from_directory(".", "index.html")
app.secret_key = os.environ.get('SECRET_KEY', 'bakery-change-me-in-production')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)


@app.after_request
def add_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


def current_user() -> dict[str, Any] | None:
    username = session.get('username')
    if not username:
        return None
    row = fetchone(
        'SELECT username, role, active, label FROM users WHERE username = %s' if IS_POSTGRES else 'SELECT username, role, active, label FROM users WHERE username = ?',
        (username,),
    )
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
    row = fetchone(
        'SELECT value FROM app_state WHERE key=%s' if IS_POSTGRES else 'SELECT value FROM app_state WHERE key=?',
        (key,),
    )
    if row is None:
        return None
    return json.loads(row['value'])


def write_state(key: str, value: Any) -> None:
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO app_state(key, value) VALUES (%s, %s)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """ if IS_POSTGRES else """
            INSERT INTO app_state(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, json.dumps(value, ensure_ascii=False)),
        )
        if key == 'bakery_master_config_v1':
            sync_users_from_config(conn, value)
        else:
            conn.commit()
    finally:
        conn.close()


@app.get('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.get('/health')
def health():
    return jsonify({'ok': True, 'backend': 'postgres' if IS_POSTGRES else 'sqlite'})
@app.get('/force-sync-users')
def force_sync_users():
    cfg = load_default_master_config()
    write_state('bakery_master_config_v1', cfg)
    return jsonify({
        'ok': True,
        'users': [u['username'] for u in cfg.get('users', [])]
    })

@app.post('/api/login')
def login():
    payload = request.get_json(silent=True) or {}
    username = (payload.get('username') or '').strip()
    password = payload.get('password') or ''
    if not username or not password:
        return jsonify({'ok': False, 'error': 'MISSING_CREDENTIALS'}), 400

    row = fetchone(
        'SELECT username, password, role, active, label FROM users WHERE username = %s' if IS_POSTGRES else 'SELECT username, password, role, active, label FROM users WHERE username = ?',
        (username,),
    )
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
    payload = {
        'backend': 'postgres' if IS_POSTGRES else 'sqlite',
        'users': fetchall('SELECT username, role, active, label FROM users ORDER BY username'),
        'app_state': fetchall('SELECT key, value FROM app_state ORDER BY key'),
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    return Response(
        raw,
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=bakery-export.json'},
    )


init_db()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    app.run(host='0.0.0.0', port=port, debug=False)
