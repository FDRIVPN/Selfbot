import sqlite3
import json
import time
from config import DB_PATH

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                phone TEXT PRIMARY KEY,
                session_string TEXT NOT NULL,
                selected_groups TEXT DEFAULT '[]',
                meow_enabled INTEGER DEFAULT 1,
                fish_enabled INTEGER DEFAULT 1,
                is_active INTEGER DEFAULT 0,
                cached_groups TEXT,
                cached_groups_time REAL
            )
        """)
        conn.commit()

def save_user(phone, session_string, selected_groups=None, meow_enabled=True,
              fish_enabled=True, is_active=False, cached_groups=None):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO users
            (phone, session_string, selected_groups, meow_enabled, fish_enabled,
             is_active, cached_groups, cached_groups_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            phone,
            session_string,
            json.dumps(selected_groups or []),
            1 if meow_enabled else 0,
            1 if fish_enabled else 0,
            1 if is_active else 0,
            json.dumps(cached_groups) if cached_groups else None,
            time.time() if cached_groups else None
        ))
        conn.commit()

def get_user(phone):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        if not row:
            return None
        return {
            "phone": row["phone"],
            "session_string": row["session_string"],
            "selected_groups": json.loads(row["selected_groups"] or "[]"),
            "meow_enabled": bool(row["meow_enabled"]),
            "fish_enabled": bool(row["fish_enabled"]),
            "is_active": bool(row["is_active"]),
            "cached_groups": json.loads(row["cached_groups"]) if row["cached_groups"] else None,
            "cached_groups_time": row["cached_groups_time"]
        }

def get_all_users():
    with get_conn() as conn:
        rows = conn.execute("SELECT phone, selected_groups, meow_enabled, fish_enabled, is_active FROM users").fetchall()
        return [{
            "phone": r["phone"],
            "groups_count": len(json.loads(r["selected_groups"] or "[]")),
            "meow_enabled": bool(r["meow_enabled"]),
            "fish_enabled": bool(r["fish_enabled"]),
            "is_active": bool(r["is_active"])
        } for r in rows]

def delete_user(phone):
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE phone = ?", (phone,))
        conn.commit()

def update_user_field(phone, **kwargs):
    user = get_user(phone)
    if not user:
        return
    for k, v in kwargs.items():
        if k in user:
            user[k] = v
    save_user(
        phone,
        user["session_string"],
        user["selected_groups"],
        user["meow_enabled"],
        user["fish_enabled"],
        user["is_active"],
        user.get("cached_groups")
    )
