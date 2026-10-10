"""Reset the Schoolers database to a clean, master-only state.

Tenant/transactional rows are removed in dependency order (children first) so
the schema's foreign keys never block the delete; the two master user rows and
the schema/DDL are left untouched. Sequences of emptied tables are restarted so
a fresh seed produces deterministic ids.

Per the user's Phase-0 decision, `periods` is treated as seed-managed timetable
data (shared by both schools) and is normalised by the seeder to 8 periods +
1 break, so it is emptied here too.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import create_engine, text

SCHEMA = "schoolers"

# Children before parents. Every school-scoped / user-generated table.
TENANT_TABLES: list[str] = [
    "support_ticket_messages",
    "support_tickets",
    "marks",
    "attendance",
    "route_students",
    "timetable_entries",
    "periods",
    "staff_attendance",
    "staff_salaries",
    "student_fees",
    "leave_requests",
    "broadcasts",
    "media",
    "route_stops",
    "pilots",
    "vehicles",
    "routes",
    "students",            # students.class_id is ON DELETE RESTRICT -> before classes
    "parent_student",
    "parents",
    "teacher_class_subjects",
    "classes",
    "subjects",
    "holidays",
    "activities",
    "barter_listings",
    "website_builder_sites",
    "school_notifications",
    "users",               # only non-master rows
    "schools",
]

# Tables whose rows are NOT all removed (master rows survive).
KEEP_MASTER_USERS = "role = 'master'"


def make_engine(database_url: str | None = None):
    url = database_url or os.environ.get("DATABASE_URL") or _default_url()
    return create_engine(url, pool_pre_ping=True, future=True)


def _default_url() -> str:
    from common.config import settings
    return settings.DATABASE_URL


@dataclass
class TableCount:
    table: str
    rows: int


def _full(table: str) -> str:
    return f"{SCHEMA}.{table}"


def count_rows(conn, table: str) -> int:
    if table == "users":
        return conn.execute(text(
            f"SELECT count(*) FROM {_full(table)} WHERE NOT ({KEEP_MASTER_USERS})"
        )).scalar_one()
    return conn.execute(text(f"SELECT count(*) FROM {_full(table)}")).scalar_one()


def counts(conn) -> list[TableCount]:
    out = []
    for table in TENANT_TABLES:
        out.append(TableCount(table, count_rows(conn, table)))
    out.append(TableCount("users(master, kept)", conn.execute(text(
        f"SELECT count(*) FROM {_full('users')} WHERE {KEEP_MASTER_USERS}"
    )).scalar_one()))
    return out


def sequences(conn) -> list[tuple[str, str, str]]:
    """(sequence, table, column) for every owned sequence in the schema."""
    rows = conn.execute(text(
        """
        SELECT c.relname AS seq, t.relname AS tbl, a.attname AS col
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_depend d ON d.objid = c.oid AND d.deptype IN ('a', 'i')
        JOIN pg_class t ON t.oid = d.refobjid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
        WHERE c.relkind = 'S' AND n.nspname = :schema
        ORDER BY t.relname, a.attname
        """
    ), {"schema": SCHEMA})
    return [(r.seq, r.tbl, r.col) for r in rows]


def delete_tenant(conn) -> dict[str, int]:
    deleted: dict[str, int] = {}
    for table in TENANT_TABLES:
        if table == "users":
            res = conn.execute(text(
                f"DELETE FROM {_full(table)} WHERE NOT ({KEEP_MASTER_USERS})"
            ))
        else:
            res = conn.execute(text(f"DELETE FROM {_full(table)}"))
        deleted[table] = res.rowcount or 0
    return deleted


def reset_sequences(conn) -> dict[str, int]:
    """Restart each sequence just after the max surviving value in its column."""
    reset: dict[str, int] = {}
    for seq, tbl, col in sequences(conn):
        nxt = conn.execute(text(
            f"SELECT COALESCE(max({col}), 0) + 1 FROM {_full(tbl)}"
        )).scalar_one()
        conn.execute(text("SELECT setval(:seq, :nxt, false)"), {"seq": seq, "nxt": nxt})
        reset[seq] = int(nxt)
    return reset


def reset(engine, dry_run: bool = False) -> dict:
    with engine.begin() as conn:
        before = counts(conn)
        if dry_run:
            return {"dry_run": True, "before": before}
        deleted = delete_tenant(conn)
        seqs = reset_sequences(conn)
        after = counts(conn)
        return {"dry_run": False, "before": before, "deleted": deleted,
                "sequences": seqs, "after": after}
