"""Direct-database helpers for the small number of things the HTTP API cannot do.

The app has no endpoint to create a login for a teacher / non-teaching staff
member / parent, and forgot-password deliberately refuses to email master
accounts (they have no address on file). Those rows are written here with the
app's own `hash_password`, so a seeded login behaves exactly like an
app-provisioned one. Everything else is created over HTTP via `seed.client`.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, text

from common.security import hash_password

SCHEMA = "schoolers"


def make_engine(database_url: str | None = None):
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        from common.config import settings
        url = settings.DATABASE_URL
    return create_engine(url, pool_pre_ping=True, future=True)


def set_user_password(conn, username: str, password: str) -> None:
    conn.execute(
        text(f"UPDATE {SCHEMA}.users SET password_hash = :h WHERE username = :u"),
        {"h": hash_password(password), "u": username},
    )


def ensure_login(
    conn,
    *,
    school_id: int,
    role: str,
    username: str,
    password: str,
    email: str | None,
    linked_person_id: int | None,
) -> int:
    """Insert a login if the username is free; otherwise reset its password."""
    existing = conn.execute(
        text(f"SELECT user_id FROM {SCHEMA}.users WHERE username = :u"),
        {"u": username},
    ).scalar_one_or_none()
    if existing:
        set_user_password(conn, username, password)
        return existing
    row = conn.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.users
                (school_id, role, username, email, password_hash, linked_person_id, is_active)
            VALUES (:school_id, :role, :username, :email, :hash, :linked, true)
            RETURNING user_id
            """
        ),
        {
            "school_id": school_id,
            "role": role,
            "username": username,
            "email": email,
            "hash": hash_password(password),
            "linked": linked_person_id,
        },
    ).scalar_one()
    return row


def user_table_has_master(conn) -> bool:
    return bool(conn.execute(
        text(f"SELECT 1 FROM {SCHEMA}.users WHERE role = 'master' LIMIT 1")
    ).first())
