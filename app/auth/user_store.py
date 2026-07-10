"""
User authentication store backed by SQLite.
Uses bcrypt for password hashing — no plaintext passwords stored.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import bcrypt

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "sqlite" / "users.db"


def _get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT UNIQUE NOT NULL COLLATE NOCASE,
                email     TEXT UNIQUE NOT NULL COLLATE NOCASE,
                full_name TEXT NOT NULL,
                pw_hash   TEXT NOT NULL,
                role      TEXT NOT NULL DEFAULT 'viewer',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


@dataclass
class User:
    id: int
    username: str
    email: str
    full_name: str
    role: str


def create_user(username: str, email: str, full_name: str, password: str, role: str = "viewer") -> tuple[bool, str]:
    """Return (success, message)."""
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    if len(username) < 3:
        return False, "Username must be at least 3 characters."

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        with _get_connection() as conn:
            conn.execute(
                "INSERT INTO users (username, email, full_name, pw_hash, role) VALUES (?,?,?,?,?)",
                (username.strip(), email.strip().lower(), full_name.strip(), pw_hash, role),
            )
            conn.commit()
        return True, "Account created successfully."
    except sqlite3.IntegrityError as exc:
        msg = str(exc)
        if "username" in msg:
            return False, "Username already taken."
        if "email" in msg:
            return False, "Email already registered."
        return False, "Registration failed."


def authenticate(username_or_email: str, password: str) -> User | None:
    """Return User on success, None on failure."""
    val = username_or_email.strip().lower()
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(username)=? OR LOWER(email)=?",
            (val, val),
        ).fetchone()
    if not row:
        return None
    if not bcrypt.checkpw(password.encode(), row["pw_hash"].encode()):
        return None
    return User(
        id=row["id"],
        username=row["username"],
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
    )


def list_users() -> list[User]:
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT id, username, email, full_name, role FROM users ORDER BY created_at"
        ).fetchall()
    return [User(**dict(r)) for r in rows]


# Initialise DB table on import
init_db()
