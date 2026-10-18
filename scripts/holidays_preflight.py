#!/usr/bin/env python3
"""Read-only preflight for db-migration-holidays-dates.sql.

Reports what the migration will touch BEFORE anyone runs it:
  * which shape the holidays table is in (weekly legacy vs dated),
  * per-school counts of recurring weekly rows that the migration DELETES,
  * the columns/constraints expected before and after migration,
  * backup + post-migration verification steps, and an operator checklist.

Optionally exports the current holiday rows to a local CSV (--export-csv)
so the recurring data is preserved on disk before the destructive step.

This tool never writes to the database. On PostgreSQL every session is
opened READ ONLY (SET TRANSACTION READ ONLY), and the script contains no
INSERT/UPDATE/DELETE/DDL statements.

Usage:
    python scripts/holidays_preflight.py --database-url postgresql://... \
        [--export-csv /tmp/holidays_before.csv] [--schema schoolers]
"""
from __future__ import annotations

import argparse
import csv
import sys
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

DEFAULT_SCHEMA = "schoolers"

# What db-migration-holidays-dates.sql expects BEFORE it runs (weekly shape).
EXPECTED_WEEKLY_COLUMNS = ["school_id", "day_of_week", "is_holiday"]
EXPECTED_WEEKLY_CONSTRAINTS = [
    "holidays_school_id_day_of_week_key",  # UNIQUE (school_id, day_of_week)
    "holidays_day_of_week_check",
]
# What exists AFTER the migration (dated shape, matches common.models.Holiday).
EXPECTED_DATED_COLUMNS = ["school_id", "occasion", "holiday_date"]
POST_MIGRATION_CONSTRAINT = "holidays_school_id_holiday_date_key"  # UNIQUE (school_id, holiday_date)

WARNINGS = [
    "The migration DISCARDS every recurring weekly row: all rows without "
    "an occasion or holiday_date are removed as a cleanup step.",
    "Weekly rows cannot be translated to dates automatically — each school "
    "must RE-ENTER recurring closures (e.g. 'closed every Saturday') as "
    "dated rows after the migration.",
    "Do not run the migration until an authorized operator approves this "
    "plan and a fresh backup exists.",
]


def _prefix(schema: str | None) -> str:
    return f"{schema}." if schema else ""


def inspect_holidays_schema(engine, schema: str | None = DEFAULT_SCHEMA) -> dict:
    """Classify the holidays table: 'weekly' (pre-migration), 'dated'
    (post-migration), or 'missing'."""
    schema = schema or None  # "" (e.g. --schema "" on sqlite) means main schema
    inspector = inspect(engine)
    if not inspector.has_table("holidays", schema=schema):
        return {"shape": "missing", "columns": []}
    columns = [c["name"] for c in inspector.get_columns("holidays", schema=schema)]
    if "holiday_date" in columns:
        shape = "dated"
    elif "day_of_week" in columns:
        shape = "weekly"
    else:
        shape = "unknown"
    return {"shape": shape, "columns": columns}


def _select_connect(engine):
    """Connection guaranteed READ ONLY on dialects that support it
    (PostgreSQL). Used by every helper that touches rows."""
    conn = engine.connect()
    if engine.dialect.name == "postgresql":
        conn.execute(text("SET TRANSACTION READ ONLY"))
    return conn


def weekly_counts(engine, schema: str | None = DEFAULT_SCHEMA) -> list[dict]:
    """Per-school count of the weekly rows the migration would discard."""
    schema = schema or None
    prefix = _prefix(schema)
    with _select_connect(engine) as conn:
        rows = conn.execute(text(
            f"SELECT h.school_id AS school_id, sc.name AS school_name, "
            f"COUNT(*) AS weekly_rows "
            f"FROM {prefix}holidays h "
            f"LEFT JOIN {prefix}schools sc ON sc.school_id = h.school_id "
            f"GROUP BY h.school_id, sc.name "
            f"ORDER BY h.school_id"
        )).mappings().all()
    return [dict(r) for r in rows]


def export_rows(engine, path: str | Path, schema: str | None = DEFAULT_SCHEMA) -> int:
    """Export ALL holiday rows to a local CSV (SELECT only). Returns rows."""
    schema = schema or None
    prefix = _prefix(schema)
    with _select_connect(engine) as conn:
        columns = [c["name"] for c in inspect(engine).get_columns("holidays", schema=schema)]
        rows = conn.execute(text(f"SELECT * FROM {prefix}holidays")).mappings().all()
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return len(rows)


@contextmanager
def _read_only_connect(engine):
    """Yields a connection pinned READ ONLY where the dialect supports it.
    Row-touching helpers open their own equally pinned connections."""
    conn = engine.connect()
    try:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET TRANSACTION READ ONLY"))
        yield conn
    finally:
        conn.close()


def build_report(engine, schema: str | None = DEFAULT_SCHEMA) -> str:
    """Human-readable preflight report + operator checklist (read-only)."""
    schema = schema or None
    info = inspect_holidays_schema(engine, schema)
    lines = ["=== Holidays date-migration preflight (READ ONLY) ===", ""]

    if info["shape"] == "missing":
        lines.append(f"RESULT: no holidays table found (schema={schema or '<main>'}). Nothing to migrate.")
        return "\n".join(lines)
    if info["shape"] == "dated":
        lines.append("RESULT: holidays table is already in the DATED shape (occasion, holiday_date).")
        lines.append("The migration has already been applied to this database — no action needed.")
        return "\n".join(lines)
    if info["shape"] != "weekly":
        lines.append(f"RESULT: unrecognized holidays shape; columns={info['columns']}")
        lines.append("STOP — do not run the migration against an unrecognized schema.")
        return "\n".join(lines)

    with _read_only_connect(engine):
        lines.append("RESULT: holidays table is in the WEEKLY legacy shape — migration not yet applied.")
        lines.append("")
        lines.append("Columns found: " + ", ".join(info["columns"]))
        missing = [c for c in EXPECTED_WEEKLY_COLUMNS if c not in info["columns"]]
        if missing:
            lines.append(f"STOP — expected pre-migration columns missing: {', '.join(missing)}")
        else:
            lines.append(f"Pre-migration columns OK ({', '.join(EXPECTED_WEEKLY_COLUMNS)}).")
        lines.append("Expected pre-migration constraints: " + ", ".join(EXPECTED_WEEKLY_CONSTRAINTS))
        lines.append(f"Post-migration constraint created by the migration: {POST_MIGRATION_CONSTRAINT}")
        lines.append("")
        lines.append("Rows the migration WILL DISCARD (per school):")
        counts = weekly_counts(engine, schema)
        total = 0
        for row in counts:
            total += int(row["weekly_rows"])
            name = row["school_name"] or "(unknown school)"
            lines.append(f"  school {row['school_id']} ({name}): {row['weekly_rows']} weekly row(s)")
        lines.append(f"  TOTAL: {total} recurring weekly row(s) to be re-entered as dated rows.")
        lines.append("")
        lines.append("WARNINGS:")
        for w in WARNINGS:
            lines.append(f"  ! {w}")
        lines.append("")
        lines.append("BACKUP (before migration):")
        lines.append("  pg_dump -Fc -d <database> -t " + (schema or "public") + ".holidays -f holidays_before.dump")
        lines.append("  Verify the dump restores: pg_restore --list holidays_before.dump | head")
        lines.append("")
        lines.append("POST-MIGRATION VERIFICATION:")
        lines.append(f"  1. \\d {(schema or 'public')}.holidays  → occasion/holiday_date present, day_of_week/is_holiday gone.")
        lines.append(f"  2. SELECT count(*) FROM {(schema or 'public')}.holidays WHERE occasion IS NULL OR holiday_date IS NULL;  → 0")
        lines.append(f"  3. Recurring closures re-entered: SELECT school_id, count(*) FROM {(schema or 'public')}.holidays GROUP BY school_id;")
        lines.append("  4. Session scheduling works: create/check a session that spans a dated holiday (check_session_conflicts).")
        lines.append("")
        lines.append("OPERATOR CHECKLIST (all required before running the migration):")
        checklist = [
            "Fresh pg_dump backup taken and restore verified",
            "This preflight report attached to the change ticket",
            "Per-school weekly row counts confirmed with each school",
            "Plan in place to re-enter recurring closures as dated rows",
            "Application version deployed supports dated holidays (post PR #19)",
            "Authorized operator approval recorded (name + timestamp)",
            "Migration window scheduled with rollback (restore dump) ready",
        ]
        for i, item in enumerate(checklist, 1):
            lines.append(f"  [ ] {i}. {item}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", required=True,
                        help="SQLAlchemy database URL (read-only session is enforced on PostgreSQL)")
    parser.add_argument("--schema", default=DEFAULT_SCHEMA,
                        help=f"Schema containing holidays (default: {DEFAULT_SCHEMA})")
    parser.add_argument("--export-csv", default=None,
                        help="Optional local CSV path to preserve current holiday rows")
    args = parser.parse_args(argv)

    engine = create_engine(args.database_url)
    try:
        print(build_report(engine, args.schema))
        info = inspect_holidays_schema(engine, args.schema)
        if args.export_csv and info["shape"] != "missing":
            n = export_rows(engine, args.export_csv, args.schema)
            print(f"\nExported {n} holiday row(s) to {args.export_csv} (local file, database untouched).")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
