"""
Invariants of the unified staff model (people/transport/academics/reports).

The teachers and pilots tables were folded into one staff table, with
person_type as the discriminator. These tests pin the structural guarantees the
merge depends on, so a future change that quietly reintroduces a parallel
identity store, drops a compatibility alias, or double counts staff in a report
fails here rather than in production.
"""
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from common import models
from common.models import (
    Base,
    Pilot,
    Route,
    SchoolClass,
    Staff,
    StaffAttendance,
    Subject,
    TeacherClassSubject,
)
import services.people_service.repository as people_repo
import services.reports_service.repository as reports_repo
import services.transport_service.repository as transport_repo

TABLES = [
    "staff",
    "pilots",
    "routes",
    "classes",
    "teacher_class_subjects",
    "subjects",
    "students",
    "parents",
    "staff_attendance",
    "leave_requests",
    "users",
]

ROOT = Path(__file__).resolve().parent.parent


def seed(db: Session):
    db.add_all(
        [
            Staff(staff_id=1, school_id=1, name="Tara Teacher", role="Teacher",
                  person_type="teacher", phone="000"),
            Staff(staff_id=2, school_id=1, name="Pia Pilot", role="Pilot",
                  person_type="pilot", phone="000"),
            Staff(staff_id=3, school_id=1, name="Adam Admin", role="Admin",
                  person_type="admin", phone="000"),
            Staff(staff_id=4, school_id=1, name="Sam Support", role="Staff",
                  person_type="staff", phone="000"),
            Staff(staff_id=5, school_id=1, name="Ida Inactive", role="Teacher",
                  person_type="teacher", phone="000", is_active=False),
            Staff(staff_id=9, school_id=2, name="Other Teacher", role="Teacher",
                  person_type="teacher", phone="000"),
            Route(route_id=1, school_id=1, name="Route 1", vehicle="Van 1", status="On route"),
            Pilot(pilot_id=1, staff_id=2, route_id=1),
        ]
    )
    db.commit()


class UnifiedStaffModelTests(unittest.TestCase):
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

    # ---- the old identity store is gone ---------------------------------

    def test_teachers_table_no_longer_exists(self):
        self.assertNotIn("teachers", Base.metadata.tables)
        self.assertFalse(hasattr(models, "Teacher"))

    def test_pilots_table_holds_no_personal_details(self):
        """Pilots are a thin driver extension; identity lives on the staff row."""
        cols = {c.name for c in Pilot.__table__.columns}
        self.assertEqual(
            cols,
            {
                "pilot_id", "staff_id", "license_expiry", "route_id", "is_active",
                "modified_by", "modified_at",
            },        )

    def test_pilot_is_one_to_one_with_staff(self):
        self.assertTrue(Pilot.__table__.columns["staff_id"].unique)
        self.db.add(Pilot(pilot_id=2, staff_id=2))
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_pilot_row_reads_its_name_off_the_staff_row(self):
        pilots = transport_repo.list_pilots(self.db, school_id=1)
        self.assertEqual(len(pilots), 1)
        self.assertEqual(pilots[0]["full_name"], "Pia Pilot")
        self.assertEqual(pilots[0]["staff_id"], 2)
        self.assertEqual(pilots[0]["route_id"], 1)

    def test_pilots_of_another_school_are_not_listed(self):
        self.db.add(Staff(staff_id=7, school_id=2, name="Other Pilot", role="Pilot",
                          person_type="pilot", phone="000"))
        self.db.add(Pilot(pilot_id=2, staff_id=7))
        self.db.commit()
        self.assertEqual([p["staff_id"] for p in transport_repo.list_pilots(self.db, school_id=1)], [2])
        self.assertEqual([p["staff_id"] for p in transport_repo.list_pilots(self.db, school_id=2)], [7])

    def test_pilot_absence_from_the_staff_roster_means_no_pilot_row(self):
        """Admins and teachers are staff rows but never pilots."""
        self.assertEqual(
            [p["staff_id"] for p in transport_repo.list_pilots(self.db, school_id=1)],
            [2],
        )
        self.assertNotIn(1, [p["staff_id"] for p in transport_repo.list_pilots(self.db, school_id=1)])

    # ---- person_type is the discriminator -------------------------------

    def test_person_type_check_constraint_is_declared(self):
        names = {c.name for c in Staff.__table__.constraints}
        self.assertIn("ck_staff_person_type", names)

    def test_person_type_rejects_a_value_outside_the_allowed_set(self):
        constraint = next(
            c for c in Staff.__table__.constraints if c.name == "ck_staff_person_type"
        )
        sql = str(constraint.sqltext)
        for allowed in ("teacher", "pilot", "admin", "staff"):
            self.assertIn(allowed, sql)

    def test_reports_count_teachers_and_non_teachers_disjointly(self):
        """The two headline figures must not double count anyone."""
        overview = reports_repo.school_overview(self.db, 1)
        # staff 5 is inactive, so school 1 has 1 teacher + pilot/admin/support.
        self.assertEqual(overview["teachers"], 1)
        self.assertEqual(overview["staff"], 3)
        self.assertEqual(overview["teachers"] + overview["staff"], 4)

    def test_teacher_listing_only_returns_teacher_typed_active_staff(self):
        listed = people_repo.list_teachers(self.db, school_id=1)
        self.assertEqual([t["staff_id"] for t in listed], [1])

    def test_teacher_listing_is_scoped_to_the_school(self):
        self.assertEqual(people_repo.list_teachers(self.db, school_id=2)[0]["staff_id"], 9)
        self.assertEqual(people_repo.list_teachers(self.db, school_id=1)[0]["staff_id"], 1)

    # ---- teacher_id stays a read alias of staff_id ----------------------

    def test_teacher_id_alias_matches_staff_id_on_assignments(self):
        tcs = TeacherClassSubject(staff_id=1, class_id=1, subject_id=1, is_class_teacher=True)
        self.assertEqual(tcs.teacher_id, tcs.staff_id)

    def test_class_teacher_id_alias_matches_staff_id_on_classes(self):
        self.assertIsNone(SchoolClass(class_id=1, school_id=1, name="A").class_teacher_id)
        klass = SchoolClass(class_id=1, school_id=1, name="A", class_teacher_staff_id=1)
        self.assertEqual(klass.class_teacher_id, klass.class_teacher_staff_id)

    def test_teaching_assignment_accepts_teacher_id_alias_in_the_body(self):
        self.db.add(SchoolClass(class_id=1, school_id=1, name="A"))
        self.db.add(Subject(subject_id=1, school_id=1, name="Maths"))
        self.db.commit()

        # Old clients still post teacher_id; the repository maps it to staff_id.
        tcs = people_repo.add_teaching_assignment(
            self.db, school_id=1,
            data={"teacher_id": 1, "class_id": 1, "subject_id": 1, "is_class_teacher": False},
        )
        self.assertEqual(tcs.staff_id, 1)
        self.assertEqual(tcs.teacher_id, 1)

    def test_teacher_payload_reports_both_staff_id_and_teacher_id(self):
        staff = people_repo.get_teacher(self.db, school_id=1, teacher_id=1)
        payload = people_repo.teacher_response(staff)
        self.assertEqual(payload["staff_id"], 1)
        self.assertEqual(payload["teacher_id"], 1)
        self.assertEqual(payload["person_type"], "teacher")

    def test_creating_a_teacher_marks_the_row_teacher_typed(self):
        created = people_repo.create_teacher(
            self.db, school_id=1, data={"name": "New Teacher", "phone": "000", "role_title": "Maths"}
        )
        self.assertEqual(created["person_type"], "teacher")
        self.assertEqual(created["teacher_id"], created["staff_id"])
        self.assertEqual(created["role_title"], "Maths")

    def test_get_teacher_cannot_reach_a_pilot(self):
        """A pilot is not a teacher, even though both are staff rows."""
        self.assertIsNone(people_repo.get_teacher(self.db, school_id=1, teacher_id=2))
        self.assertIsNone(people_repo.get_teacher(self.db, school_id=1, teacher_id=9))

    def test_inactive_teacher_is_not_returned_by_get_teacher(self):
        self.assertIsNone(people_repo.get_teacher(self.db, school_id=1, teacher_id=5))

    # ---- the new dated table --------------------------------------------

    def test_staff_attendance_carries_date_and_status(self):
        cols = {c.name for c in StaffAttendance.__table__.columns}
        self.assertIn("date", cols)
        self.assertIn("status", cols)

    def test_old_single_value_attendance_column_is_gone(self):
        self.assertNotIn("attendance_status", {c.name for c in Staff.__table__.columns})

    def test_staff_attendance_is_unique_per_person_per_day(self):
        uniques = {
            tuple(c.columns.keys())
            for c in StaffAttendance.__table__.constraints
            if type(c).__name__ == "UniqueConstraint"
        }
        self.assertIn(("staff_id", "date"), uniques)

    def test_staff_attendance_status_check_is_declared(self):
        names = {c.name for c in StaffAttendance.__table__.constraints}
        self.assertIn("staff_attendance_status_check", names)


class MigrationRemapCoverageTests(unittest.TestCase):
    """Pin the teacher -> staff id remap in db-migrations.sql.

    Renaming teacher_id to staff_id keeps the old numbers, so every table that
    stored a teacher id needs its values rewritten to the matching staff id
    first. Without that, teacher 2 becomes staff 2 -- a different person.
    """

    @classmethod
    def setUpClass(cls):
        cls.migrations = (ROOT / "db-migrations.sql").read_text(encoding="utf-8")

    def _b0_block(self) -> str:
        """The remap section, from its marker up to the column renames."""
        start = self.migrations.index("-- B0.")
        end = self.migrations.index("RENAME COLUMN", start)
        return self.migrations[start:end]

    def test_b0_remaps_every_table_that_stored_a_teacher_id(self):
        b0 = self._b0_block()
        for table in ("teacher_class_subjects", "timetable_entries", "classes"):
            with self.subTest(table=table):
                self.assertIn(f"UPDATE schoolers.{table}", b0)

    def test_b0_nulls_out_teacher_ids_with_no_staff_match(self):
        """An unmapped id must become NULL, not point at an unrelated row."""
        b0 = self._b0_block()
        self.assertIn("SET teacher_id = NULL", b0)
        self.assertIn("NOT EXISTS", b0)
        self.assertIn("SET class_teacher_id = NULL", b0)

    def test_b0_joins_through_the_teachers_staff_id_mapping(self):
        b0 = self._b0_block()
        self.assertIn("FROM schoolers.teachers t", b0)
        self.assertIn("SET teacher_id = t.staff_id", b0)
        self.assertIn("SET class_teacher_id = t.staff_id", b0)
        # The join must be guarded, or NULL staff ids would remap to NULL.
        self.assertIn("t.staff_id IS NOT NULL", b0)


if __name__ == "__main__":
    unittest.main()
