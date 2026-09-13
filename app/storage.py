from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from app.settings import settings

_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    path = settings.data_dir / "teradrop.sqlite3"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init() -> None:
    with _db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                seen_at REAL,
                downloads INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS auth (user_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL,
                level TEXT,
                message TEXT
            );
            CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
            """
        )


def touch_user(user_id: int, username: str | None, first_name: str | None) -> None:
    with _lock, _db() as conn:
        conn.execute(
            """
            INSERT INTO users(user_id, username, first_name, seen_at, downloads)
            VALUES(?,?,?,?,0)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                seen_at=excluded.seen_at
            """,
            (user_id, username, first_name, time.time()),
        )


def bump_download(user_id: int) -> None:
    with _lock, _db() as conn:
        conn.execute(
            "UPDATE users SET downloads = downloads + 1 WHERE user_id = ?",
            (user_id,),
        )


def is_banned(user_id: int) -> bool:
    with _db() as conn:
        row = conn.execute("SELECT 1 FROM bans WHERE user_id=?", (user_id,)).fetchone()
        return bool(row)


def ban(user_id: int) -> None:
    with _lock, _db() as conn:
        conn.execute("INSERT OR IGNORE INTO bans(user_id) VALUES(?)", (user_id,))


def unban(user_id: int) -> None:
    with _lock, _db() as conn:
        conn.execute("DELETE FROM bans WHERE user_id=?", (user_id,))


def is_authorized(user_id: int) -> bool:
    if user_id in settings.authorized_ids:
        return True
    with _db() as conn:
        row = conn.execute("SELECT 1 FROM auth WHERE user_id=?", (user_id,)).fetchone()
        return bool(row)


def authorize(user_id: int) -> None:
    with _lock, _db() as conn:
        conn.execute("INSERT OR IGNORE INTO auth(user_id) VALUES(?)", (user_id,))


def unauthorize(user_id: int) -> None:
    with _lock, _db() as conn:
        conn.execute("DELETE FROM auth WHERE user_id=?", (user_id,))


def log(level: str, message: str) -> None:
    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO logs(created_at, level, message) VALUES(?,?,?)",
            (time.time(), level, message[:2000]),
        )
        conn.execute("DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 80)")


def recent_logs(limit: int = 12) -> list[str]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT level, message FROM logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [f"{r['level']}: {r['message']}" for r in rows]


def stats() -> dict:
    with _db() as conn:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        downloads = conn.execute("SELECT COALESCE(SUM(downloads),0) c FROM users").fetchone()["c"]
        bans = conn.execute("SELECT COUNT(*) c FROM bans").fetchone()["c"]
    return {"users": users, "downloads": downloads, "bans": bans}


def user_ids() -> list[int]:
    with _db() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
    return [int(r["user_id"]) for r in rows]


def recent_users(limit: int = 15) -> list[str]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT user_id, username, first_name, downloads FROM users ORDER BY seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        name = r["username"] or r["first_name"] or "user"
        out.append(f"{r['user_id']} · {name} · {r['downloads']} files")
    return out


def kv_get(key: str, default: str = "") -> str:
    with _db() as conn:
        row = conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def kv_set(key: str, value: str) -> None:
    with _lock, _db() as conn:
        conn.execute(
            "INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )


def dump_json(path: Path) -> None:
    path.write_text(json.dumps(stats(), indent=2))
