"""
Tests for the dedicated staff_attendance table (attendance_service).

This table replaced the denormalised teachers.attendance_status column, which
could only ever hold one current value per teacher. The behaviours that matter
here are: the (staff_id, date) unique target the upsert depends on, one dated
row per person (teacher, pilot, admin or staff alike), the tenancy guard that
keeps a school from marking another school's roster, and the role split where
only an admin may mark while everyone may read their own sheet.

Runs against an in-memory SQLite database; the pg_insert(...).on_conflict_do_update()
upsert emits ON CONFLICT, which SQLite also supports.
"""
import ast
import sys
import unittest
from datetime import date
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import create_engine, func, inspect
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
from common.models import Base, Staff, StaffAttendance
import services.attendance_service.repository as repo
from services.attendance_service.schemas import StaffAttendanceMarkOne

TABLES = ["staff", "staff_attendance"]

ROOT = Path(__file__).resolve().parent.parent
ROUTER_SOURCE = ROOT / "services" / "attendance_service" / "router.py"


def seed(db: Session):
    db.add_all(
        [
            # School 1 roster, covering every person_type so the sheet is not
            # teacher-only.
            Staff(staff_id=1, school_id=1, name="Tara Teacher", role="Teacher",
                  person_type="teacher", phone="000"),
            Staff(staff_id=2, school_id=1, name="Pia Pilot", role="Pilot",
                  person_type="pilot", phone="000"),
            Staff(staff_id=3, school_id=1, name="Adam Admin", role="Admin",
                  person_type="admin", phone="000"),
            Staff(staff_id=4, school_id=1, name="Sam Support", role="Staff",
                  person_type="staff", phone="000"),
            # School 2 must never be markable by school 1.
            Staff(staff_id=9, school_id=2, name="Other Teacher", role="Teacher",
                  person_type="teacher", phone="000"),
        ]
    )
    db.commit()


class StaffAttendanceMarkingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[name] for name in TABLES],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()
        seed(self.db)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _mark(self, the_date, entries, marked_by=1, school_id=1):
        return repo.mark_staff_bulk(self.db, school_id, the_date, entries, marked_by)

    def test_schema_declares_unique_constraint_for_upsert_target(self):
        """The upsert target (staff_id, date) must have a unique constraint."""
        uniques = [tuple(u["column_names"]) for u in inspect(self.engine).get_unique_constraints("staff_attendance")]
        self.assertIn(("staff_id", "date"), uniques)

    def test_first_mark_inserts_one_row_per_staff(self):
        rows = self._mark(
            date(2026, 9, 16),
            [
                {"staff_id": 1, "status": "Present", "check_in": "08:30", "check_out": "16:00"},
                {"staff_id": 2, "status": "Absent"},
                {"staff_id": 3, "status": "On leave"},
                {"staff_id": 4, "status": "Half day"},
            ],
            marked_by=7,
        )
        by_staff = {r["staff_id"]: r for r in rows}
        self.assertEqual(len(by_staff), 4)
        self.assertEqual(by_staff[1]["status"], "Present")
        self.assertEqual(by_staff[1]["staff_name"], "Tara Teacher")
        self.assertEqual(by_staff[1]["person_type"], "teacher")
        self.assertEqual(by_staff[1]["school_id"], 1)
        self.assertEqual(by_staff[1]["date"], date(2026, 9, 16))
        self.assertEqual(by_staff[1]["check_in"].hour, 8)
        self.assertEqual(by_staff[1]["check_in"].minute, 30)
        self.assertEqual(by_staff[2]["status"], "Absent")
        self.assertEqual(by_staff[3]["status"], "On leave")
        self.assertEqual(by_staff[4]["status"], "Half day")

    def test_attendance_covers_every_person_type_not_just_teachers(self):
        """The whole point of the unified staff table: one sheet for all staff."""
        self._mark(
            date(2026, 9, 16),
            [{"staff_id": i, "status": "Present"} for i in (1, 2, 3, 4)],
        )
        rows = repo.staff_attendance_rows(self.db, 1, None, None)
        self.assertEqual(
            {r["person_type"] for r in rows},
            {"teacher", "pilot", "admin", "staff"},
        )

    def test_remark_overwrites_status_without_duplicates(self):
        self._mark(date(2026, 9, 16), [{"staff_id": 1, "status": "Present"}], marked_by=1)
        rows = self._mark(
            date(2026, 9, 16),
            [{"staff_id": 1, "status": "Absent", "remarks": "Called in sick"}],
            marked_by=2,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "Absent")
        self.assertEqual(rows[0]["remarks"], "Called in sick")

        count = self.db.query(func.count()).select_from(StaffAttendance).filter(
            StaffAttendance.staff_id == 1,
            StaffAttendance.date == date(2026, 9, 16),
        ).scalar()
        self.assertEqual(count, 1)

    def test_marking_a_new_date_keeps_previous_day(self):
        """Dated rows, not a single current value like the old column."""
        self._mark(date(2026, 9, 16), [{"staff_id": 1, "status": "Present"}])
        self._mark(date(2026, 9, 17), [{"staff_id": 1, "status": "Absent"}])
        total = self.db.query(func.count()).select_from(StaffAttendance).filter(
            StaffAttendance.staff_id == 1
        ).scalar()
        self.assertEqual(total, 2)

    def test_status_is_restricted_to_the_four_supported_values(self):
        for status in ("Present", "Absent", "On leave", "Half day"):
            with self.subTest(status=status):
                self.assertEqual(StaffAttendanceMarkOne(staff_id=1, status=status).status, status)
        for bogus in ("present", "Late", "", "P", "Leave"):
            with self.subTest(status=bogus):
                with self.assertRaises(ValidationError):
                    StaffAttendanceMarkOne(staff_id=1, status=bogus)

    def test_staff_in_school_scopes_by_school(self):
        """Tenancy guard used by the router before it trusts the request body."""
        self.assertTrue(repo.staff_in_school(self.db, 1, 1))
        self.assertTrue(repo.staff_in_school(self.db, 9, 2))
        self.assertFalse(repo.staff_in_school(self.db, 9, 1))
        self.assertFalse(repo.staff_in_school(self.db, 1, 2))
        self.assertFalse(repo.staff_in_school(self.db, 4242, 1))

    def test_rows_are_scoped_to_the_callers_school(self):
        self._mark(date(2026, 9, 16), [{"staff_id": 1, "status": "Present"}])
        self._mark(date(2026, 9, 16), [{"staff_id": 9, "status": "Present"}], school_id=2)
        one = repo.staff_attendance_rows(self.db, 1, None, None)
        two = repo.staff_attendance_rows(self.db, 2, None, None)
        self.assertEqual([r["staff_id"] for r in one], [1])
        self.assertEqual([r["staff_id"] for r in two], [9])

    def test_date_range_filters_rows(self):
        self._mark(date(2026, 9, 15), [{"staff_id": 1, "status": "Present"}])
        self._mark(date(2026, 9, 16), [{"staff_id": 1, "status": "Absent"}])
        self._mark(date(2026, 9, 17), [{"staff_id": 1, "status": "Present"}])

        rows = repo.staff_attendance_rows(self.db, 1, date(2026, 9, 16), date(2026, 9, 17))
        self.assertEqual([r["date"] for r in rows], [date(2026, 9, 17), date(2026, 9, 16)])

    def test_rows_can_be_narrowed_to_specific_staff(self):
        self._mark(
            date(2026, 9, 16),
            [{"staff_id": 1, "status": "Present"}, {"staff_id": 2, "status": "Absent"}],
        )
        rows = repo.staff_attendance_rows(self.db, 1, None, None, [2])
        self.assertEqual([r["staff_id"] for r in rows], [2])
        self.assertEqual(repo.staff_attendance_rows(self.db, 1, None, None, []), [])

    def test_latest_returns_the_most_recent_row_per_staff(self):
        self._mark(date(2026, 9, 15), [{"staff_id": 1, "status": "Absent"}])
        self._mark(date(2026, 9, 17), [{"staff_id": 1, "status": "Present"}])
        self._mark(date(2026, 9, 16), [{"staff_id": 2, "status": "Present"}])

        latest = {r["staff_id"]: r for r in repo.latest_staff_attendance(self.db, 1)}
        self.assertEqual(len(latest), 2)
        self.assertEqual(latest[1]["date"], date(2026, 9, 17))
        self.assertEqual(latest[1]["status"], "Present")
        self.assertEqual(latest[2]["date"], date(2026, 9, 16))

    def test_latest_is_scoped_to_the_callers_school(self):
        self._mark(date(2026, 9, 16), [{"staff_id": 1, "status": "Present"}])
        self._mark(date(2026, 9, 16), [{"staff_id": 9, "status": "Present"}], school_id=2)
        self.assertEqual([r["staff_id"] for r in repo.latest_staff_attendance(self.db, 1)], [1])
        self.assertEqual([r["staff_id"] for r in repo.latest_staff_attendance(self.db, 2)], [9])

    def test_attendance_is_cascaded_when_the_staff_row_goes(self):
        """Deleting a person must not leave orphaned dated rows behind.

        Asserted from the metadata rather than by deleting: SQLite does not
        enforce ON DELETE CASCADE unless foreign_keys is turned on per
        connection, and turning it on here would demand the schools/users
        tables that this focused schema deliberately omits.
        """
        fk = next(
            f for f in StaffAttendance.__table__.foreign_keys
            if f.target_fullname.endswith("staff.staff_id")
        )
        self.assertEqual(fk.ondelete, "CASCADE")

        school_fk = next(
            f for f in StaffAttendance.__table__.foreign_keys
            if f.target_fullname.endswith("schools.school_id")
        )
        self.assertEqual(school_fk.ondelete, "CASCADE")


class StaffAttendanceRouterScopeTests(unittest.TestCase):
    """Role split: admin-only marking, everyone may read their own row."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[name] for name in TABLES],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()
        seed(self.db)
        sys.path.insert(0, "services/attendance_service")
        try:
            from services.attendance_service.router import (
                get_staff_attendance as _get,
                mark_staff_attendance as _mark,
            )
        finally:
            sys.path.pop(0)
        self.mark = _mark
        self.get = _get

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _bulk(self, entries, the_date=date(2026, 9, 16)):
        from services.attendance_service.schemas import StaffAttendanceMarkBulk

        return StaffAttendanceMarkBulk(date=the_date, entries=[StaffAttendanceMarkOne(**e) for e in entries])

    def test_admin_can_mark_the_whole_school_roster(self):
        admin = CurrentUser(user_id=7, role="admin", school_id=1, linked_person_id=3)
        rows = self.mark(
            self._bulk([{"staff_id": 1, "status": "Present"}, {"staff_id": 2, "status": "Absent"}]),
            self.db,
            admin,
        )
        self.assertEqual({r["staff_id"] for r in rows}, {1, 2})

    def test_marking_rejects_staff_from_another_school(self):
        """Staff id 9 belongs to school 2; school 1's admin must not mark it."""
        admin = CurrentUser(user_id=7, role="admin", school_id=1, linked_person_id=3)
        with self.assertRaises(NotFoundError):
            self.mark(self._bulk([{"staff_id": 9, "status": "Present"}]), self.db, admin)

    def test_admin_without_a_school_cannot_mark(self):
        master = CurrentUser(user_id=1, role="master", school_id=None, linked_person_id=None)
        with self.assertRaises(ForbiddenError):
            self.mark(self._bulk([{"staff_id": 1, "status": "Present"}]), self.db, master)

    def test_teacher_is_forced_to_their_own_row_when_reading(self):
        self.mark(
            self._bulk([{"staff_id": 1, "status": "Present"}, {"staff_id": 2, "status": "Absent"}]),
            self.db,
            CurrentUser(user_id=7, role="admin", school_id=1, linked_person_id=3),
        )
        # A teacher asking for the whole school is silently narrowed to self.
        teacher = CurrentUser(user_id=1, role="teacher", school_id=1, linked_person_id=1)
        rows = self.get(None, None, None, False, self.db, teacher)
        self.assertEqual([r["staff_id"] for r in rows], [1])

    def test_teacher_cannot_read_another_staff_members_row(self):
        teacher = CurrentUser(user_id=1, role="teacher", school_id=1, linked_person_id=1)
        with self.assertRaises(ForbiddenError):
            self.get(None, None, 2, False, self.db, teacher)

    def test_pilot_reads_own_attendance(self):
        """Pilots are staff rows now, so they get a sheet too."""
        self.mark(
            self._bulk([{"staff_id": 2, "status": "Present"}]),
            self.db,
            CurrentUser(user_id=7, role="admin", school_id=1, linked_person_id=3),
        )
        pilot = CurrentUser(user_id=2, role="pilot", school_id=1, linked_person_id=2)
        rows = self.get(None, None, None, False, self.db, pilot)
        self.assertEqual([r["staff_id"] for r in rows], [2])
        self.assertEqual(rows[0]["person_type"], "pilot")

    def test_account_without_a_staff_link_cannot_read(self):
        orphan = CurrentUser(user_id=9, role="staff", school_id=1, linked_person_id=None)
        with self.assertRaises(ForbiddenError):
            self.get(None, None, None, False, self.db, orphan)

    def test_latest_only_is_scoped_to_the_callers_own_row(self):
        self.mark(
            self._bulk([{"staff_id": 1, "status": "Present"}, {"staff_id": 2, "status": "Absent"}]),
            self.db,
            CurrentUser(user_id=7, role="admin", school_id=1, linked_person_id=3),
        )
        teacher = CurrentUser(user_id=1, role="teacher", school_id=1, linked_person_id=1)
        rows = self.get(None, None, None, True, self.db, teacher)
        self.assertEqual([r["staff_id"] for r in rows], [1])


class StaffAttendanceRoleGateTests(unittest.TestCase):
    """Pin the role guard in the route signature via AST."""

    def test_mark_endpoint_is_bound_to_admin_only(self):
        tree = ast.parse(ROUTER_SOURCE.read_text())
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "mark_staff_attendance"
        )
        rendered = ast.unparse(fn)
        # ast.unparse normalises string quotes to single, so match on that.
        self.assertIn("require_role('admin')", rendered)

    def test_read_endpoint_allows_the_staff_facing_roles(self):
        tree = ast.parse(ROUTER_SOURCE.read_text())
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "get_staff_attendance"
        )
        rendered = ast.unparse(fn)
        for role in ("admin", "teacher", "staff", "pilot"):
            self.assertIn(f"'{role}'", rendered)


if __name__ == "__main__":
    unittest.main()
