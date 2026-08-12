import sqlite3
import json
import time
from config import DB_PATH

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


DEFAULT_FISH_RULES = {
    "افسانه": "fridge",
    "حماسی": "fridge",
    "کمیاب": "cat",
    "غیرمعمول": "cat",
    "معمولی": "sell",
    "اسطوره": "fridge",
}

DEFAULT_COOKED_RULES = {
    "افسانه": "keep",
    "حماسی": "keep",
    "کمیاب": "cat",
    "غیرمعمول": "cat",
    "معمولی": "sell",
    "اسطوره": "keep",
}


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                phone TEXT PRIMARY KEY,
                session_string TEXT NOT NULL,
                selected_groups TEXT DEFAULT '[]',
                meow_enabled INTEGER DEFAULT 1,
                fish_enabled INTEGER DEFAULT 1,
                rescue_enabled INTEGER DEFAULT 1,
                catch_enabled INTEGER DEFAULT 1,
                is_active INTEGER DEFAULT 0,
                cached_groups TEXT,
                cached_groups_time REAL,
                harvest_button TEXT DEFAULT 'برداشت میو پوینت ها',
                rescue_button TEXT DEFAULT 'نجات پیشی خیابونی',
                meow_interval INTEGER DEFAULT 300,
                fish_interval INTEGER DEFAULT 600,
                catch_interval INTEGER DEFAULT 120,
                fish_rules TEXT DEFAULT '{}',
                cooked_rules TEXT DEFAULT '{}'
            )
        """)
        columns = [
            ("rescue_enabled", "INTEGER DEFAULT 1"),
            ("catch_enabled", "INTEGER DEFAULT 1"),
            ("harvest_button", "TEXT DEFAULT 'برداشت میو پوینت ها'"),
            ("rescue_button", "TEXT DEFAULT 'نجات پیشی خیابونی'"),
            ("meow_interval", "INTEGER DEFAULT 300"),
            ("fish_interval", "INTEGER DEFAULT 600"),
            ("catch_interval", "INTEGER DEFAULT 120"),
            ("fish_rules", "TEXT DEFAULT '{}'"),
            ("cooked_rules", "TEXT DEFAULT '{}'"),
        ]
        for col, typ in columns:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass
        conn.commit()


def save_user(phone, session_string, selected_groups=None,
              meow_enabled=True, fish_enabled=True, rescue_enabled=True,
              catch_enabled=True, is_active=False, cached_groups=None,
              harvest_button="برداشت میو پوینت ها",
              rescue_button="نجات پیشی خیابونی",
              meow_interval=300, fish_interval=600, catch_interval=120,
              fish_rules=None, cooked_rules=None):
    if fish_rules is None:
        fish_rules = DEFAULT_FISH_RULES
    if cooked_rules is None:
        cooked_rules = DEFAULT_COOKED_RULES

    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO users
            (phone, session_string, selected_groups, meow_enabled, fish_enabled,
             rescue_enabled, catch_enabled, is_active, cached_groups, cached_groups_time,
             harvest_button, rescue_button, meow_interval, fish_interval, catch_interval,
             fish_rules, cooked_rules)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            phone,
            session_string,
            json.dumps(selected_groups or []),
            1 if meow_enabled else 0,
            1 if fish_enabled else 0,
            1 if rescue_enabled else 0,
            1 if catch_enabled else 0,
            1 if is_active else 0,
            json.dumps(cached_groups) if cached_groups else None,
            time.time() if cached_groups else None,
            harvest_button or "برداشت میو پوینت ها",
            rescue_button or "نجات پیشی خیابونی",
            int(meow_interval) if meow_interval else 300,
            int(fish_interval) if fish_interval else 600,
            int(catch_interval) if catch_interval else 120,
            json.dumps(fish_rules) if isinstance(fish_rules, dict) else (fish_rules or "{}"),
            json.dumps(cooked_rules) if isinstance(cooked_rules, dict) else (cooked_rules or "{}"),
        ))
        conn.commit()


def get_user(phone):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        if not row:
            return None
        keys = row.keys()

        rules_raw = row["fish_rules"] if "fish_rules" in keys and row["fish_rules"] else "{}"
        try:
            rules = json.loads(rules_raw) if rules_raw else {}
        except Exception:
            rules = {}
        if not rules:
            rules = DEFAULT_FISH_RULES.copy()

        cooked_raw = row["cooked_rules"] if "cooked_rules" in keys and row["cooked_rules"] else "{}"
        try:
            cooked = json.loads(cooked_raw) if cooked_raw else {}
        except Exception:
            cooked = {}
        if not cooked:
            cooked = DEFAULT_COOKED_RULES.copy()

        return {
            "phone": row["phone"],
            "session_string": row["session_string"],
            "selected_groups": json.loads(row["selected_groups"] or "[]"),
            "meow_enabled": bool(row["meow_enabled"]),
            "fish_enabled": bool(row["fish_enabled"]),
            "rescue_enabled": bool(row["rescue_enabled"]) if "rescue_enabled" in keys else True,
            "catch_enabled": bool(row["catch_enabled"]) if "catch_enabled" in keys else True,
            "is_active": bool(row["is_active"]),
            "cached_groups": json.loads(row["cached_groups"]) if row["cached_groups"] else None,
            "cached_groups_time": row["cached_groups_time"],
            "harvest_button": row["harvest_button"] if "harvest_button" in keys and row["harvest_button"] else "برداشت میو پوینت ها",
            "rescue_button": row["rescue_button"] if "rescue_button" in keys and row["rescue_button"] else "نجات پیشی خیابونی",
            "meow_interval": int(row["meow_interval"]) if "meow_interval" in keys and row["meow_interval"] else 300,
            "fish_interval": int(row["fish_interval"]) if "fish_interval" in keys and row["fish_interval"] else 600,
            "catch_interval": int(row["catch_interval"]) if "catch_interval" in keys and row["catch_interval"] else 120,
            "fish_rules": rules,
            "cooked_rules": cooked,
        }


def get_all_users():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT phone, selected_groups, meow_enabled, fish_enabled, is_active FROM users"
        ).fetchall()
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
