"""
Tests for scripts/holidays_preflight.py — the read-only preparation tool
for db-migration-holidays-dates.sql.

Covers: weekly/dated/missing shape detection, per-school discard counts,
local CSV export that leaves the database untouched, report content
(warnings, backup, verification, checklist), and a statement-level
read-only guard (every executed statement must be a SELECT).
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, event, text

sys.path.insert(0, "scripts")
import holidays_preflight as preflight
sys.path.pop(0)


WEEKLY_DDL = """
CREATE TABLE schools (school_id INTEGER PRIMARY KEY, name VARCHAR(100));
CREATE TABLE holidays (
    holiday_id INTEGER PRIMARY KEY,
    school_id INTEGER,
    day_of_week VARCHAR(3),
    is_holiday BOOLEAN
);
"""

DATED_DDL = """
CREATE TABLE schools (school_id INTEGER PRIMARY KEY, name VARCHAR(100));
CREATE TABLE holidays (
    holiday_id INTEGER PRIMARY KEY,
    school_id INTEGER,
    occasion VARCHAR(100),
    holiday_date DATE
);
"""


class HolidaysPreflightTests(unittest.TestCase):
    def _engine(self, ddl=WEEKLY_DDL, seed: str | None = None):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            for stmt in ddl.split(";"):
                if stmt.strip():
                    conn.execute(text(stmt))
            if seed:
                for stmt in seed.split(";"):
                    if stmt.strip():
                        conn.execute(text(stmt))
        return engine

    def _weekly_engine(self):
        seed = """
            INSERT INTO schools VALUES (1, 'Green Valley'), (2, 'Blue Horizon');
            INSERT INTO holidays VALUES (1, 1, 'Sat', 1), (2, 1, 'Sun', 1), (3, 1, 'Fri', 1);
            INSERT INTO holidays VALUES (4, 2, 'Sat', 1), (5, 2, 'Wed', 1)
        """
        return self._engine(WEEKLY_DDL, seed)

    # ---- shape detection ----

    def test_detects_weekly_legacy_shape(self):
        info = preflight.inspect_holidays_schema(self._weekly_engine(), schema=None)
        self.assertEqual(info["shape"], "weekly")
        self.assertIn("day_of_week", info["columns"])

    def test_detects_dated_shape_as_migrated(self):
        engine = self._engine(DATED_DDL, "INSERT INTO holidays VALUES (1, 1, 'Founders Day', '2026-12-25')")
        info = preflight.inspect_holidays_schema(engine, schema=None)
        self.assertEqual(info["shape"], "dated")

    def test_missing_table_reported(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        info = preflight.inspect_holidays_schema(engine, schema=None)
        self.assertEqual(info["shape"], "missing")

    def test_empty_schema_string_treated_as_main_schema(self):
        # --schema "" (common on sqlite dev DBs) must mean the main schema,
        # not a literal "" namespace that would misreport 'missing'.
        engine = self._weekly_engine()
        info = preflight.inspect_holidays_schema(engine, schema="")
        self.assertEqual(info["shape"], "weekly")
        self.assertEqual(len(preflight.weekly_counts(engine, schema="")), 2)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "holidays.csv"
            self.assertEqual(preflight.export_rows(engine, path, schema=""), 5)
        self.assertIn("weekly row(s)", preflight.build_report(engine, schema=""))

    # ---- per-school discard counts ----

    def test_weekly_counts_per_school(self):
        counts = preflight.weekly_counts(self._weekly_engine(), schema=None)
        self.assertEqual(
            [(c["school_id"], c["school_name"], c["weekly_rows"]) for c in counts],
            [(1, "Green Valley", 3), (2, "Blue Horizon", 2)],
        )

    # ---- CSV export is local-only ----

    def test_export_csv_preserves_rows_and_database_untouched(self):
        engine = self._weekly_engine()
        before = engine.connect().execute(
            text("SELECT COUNT(*) FROM holidays")).scalar_one()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "holidays.csv"
            n = preflight.export_rows(engine, path, schema=None)
            self.assertEqual(n, 5)
            lines = path.read_text().strip().splitlines()
            self.assertEqual(len(lines), 6)  # header + 5 rows
            self.assertTrue(lines[0].startswith("holiday_id,school_id,day_of_week,is_holiday"))
        after = engine.connect().execute(
            text("SELECT COUNT(*) FROM holidays")).scalar_one()
        self.assertEqual(after, before)

    # ---- report content ----

    def test_report_contains_warnings_backup_verification_checklist(self):
        report = preflight.build_report(self._weekly_engine(), schema=None)
        self.assertIn("WEEKLY legacy shape", report)
        self.assertIn("DISCARD", report)
        self.assertIn("RE-ENTER", report.upper())
        self.assertIn("Green Valley", report)
        self.assertIn("TOTAL: 5 recurring weekly row(s)", report)
        self.assertIn("pg_dump", report)
        self.assertIn("POST-MIGRATION VERIFICATION", report)
        self.assertIn("OPERATOR CHECKLIST", report)
        self.assertIn("rollback", report.lower())

    def test_report_says_dated_shape_needs_no_action(self):
        engine = self._engine(DATED_DDL, "INSERT INTO holidays VALUES (1, 1, 'Day', '2026-01-01')")
        report = preflight.build_report(engine, schema=None)
        self.assertIn("already in the DATED shape", report)
        self.assertNotIn("OPERATOR CHECKLIST", report)

    def test_report_missing_table(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        report = preflight.build_report(engine, schema=None)
        self.assertIn("no holidays table", report)

    # ---- read-only guard ----

    def test_only_select_statements_are_executed(self):
        engine = self._weekly_engine()
        executed: list[str] = []

        @event.listens_for(engine, "before_cursor_execute")
        def _record(conn, cursor, statement, parameters, context, executemany):
            executed.append(statement.strip().split()[0].upper())

        with TemporaryDirectory() as tmp:
            preflight.build_report(engine, schema=None)
            preflight.weekly_counts(engine, schema=None)
            preflight.export_rows(engine, Path(tmp) / "out.csv", schema=None)
        self.assertTrue(executed)
        self.assertLessEqual(set(executed), {"SELECT", "PRAGMA", "SET"})

    def test_module_contains_no_dml_statements(self):
        source = Path(preflight.__file__).read_text()
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP TABLE", "ALTER TABLE"):
            self.assertNotIn(forbidden, source, f"{forbidden.strip()} found in preflight source")


if __name__ == "__main__":
    unittest.main()
