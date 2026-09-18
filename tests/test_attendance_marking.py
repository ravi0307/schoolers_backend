"""
Functional tests for the attendance marking (bulk upsert) flow.

Runs against an in-memory SQLite database. The /attendance/mark endpoint
upserts with ON CONFLICT (student_id, date), which only works when the
attendance table carries a matching UNIQUE constraint; these tests exercise
both the insert path and the re-mark (conflict) path, plus reads.
"""
import unittest
from datetime import date
import sys

from pydantic import ValidationError
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import ForbiddenError
from common.models import Base, Attendance, SchoolClass, Student, TeacherClassSubject
import services.attendance_service.repository as repo
from services.attendance_service.schemas import AttendanceMarkOne

TABLES = ["attendance", "classes", "students", "teacher_class_subjects"]


def seed(db: Session):
    db.add_all(
        [
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Alice"),
            Student(student_id=2, school_id=1, class_id=1, admission_no="A2", name="Bob"),
            Student(student_id=3, school_id=1, class_id=2, admission_no="A3", name="Carol"),
            SchoolClass(class_id=1, school_id=1, name="Class 1"),
            SchoolClass(class_id=2, school_id=2, name="Class 2"),
            SchoolClass(class_id=3, school_id=1, name="Class 3", class_teacher_id=99),
            Student(student_id=5, school_id=1, class_id=3, admission_no="A5", name="Dana"),
            Student(student_id=6, school_id=2, class_id=2, admission_no="A6", name="Eve"),
            TeacherClassSubject(teacher_id=7, class_id=1, subject_id=3, is_class_teacher=True),
        ]
    )
    db.commit()


class AttendanceMarkingTests(unittest.TestCase):
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

    def _mark(self, class_id, the_date, entries, marked_by=1):
        return repo.mark_bulk(self.db, class_id, the_date, entries, marked_by)

    def test_schema_declares_unique_constraint_for_upsert_target(self):
        """The upsert target (student_id, date) must have a unique constraint."""
        inspector = inspect(self.engine)
        uniques = inspector.get_unique_constraints("attendance")
        names = {tuple(sorted(u["column_names"])) for u in uniques}
        self.assertIn(("date", "student_id"), names)

    def test_first_mark_inserts_one_row_per_student(self):
        rows = self._mark(
            1,
            date(2026, 9, 16),
            [
                {"student_id": 1, "status": "Present"},
                {"student_id": 2, "status": "Absent"},
                {"student_id": 3, "status": "Present"},
            ],
            marked_by=1,
        )
        by_student = {r.student_id: r for r in rows}
        self.assertEqual(len(by_student), 3)
        self.assertEqual(by_student[1].status, "Present")
        self.assertEqual(by_student[2].status, "Absent")
        self.assertEqual(by_student[1].marked_by, 1)
        self.assertEqual(by_student[1].class_id, 1)
        self.assertEqual(by_student[1].date, date(2026, 9, 16))

    def test_remark_overwrites_status_without_duplicates(self):
        self._mark(
            1, date(2026, 9, 16),
            [{"student_id": 1, "status": "Present"}, {"student_id": 2, "status": "Absent"}],
            marked_by=1,
        )
        self._mark(
            1, date(2026, 9, 16),
            [{"student_id": 1, "status": "Absent"}, {"student_id": 2, "status": "Present"}],
            marked_by=2,
        )
        rows = self._mark(1, date(2026, 9, 16), [{"student_id": 1, "status": "Present"}], marked_by=2)
        by_student = {r.student_id: r for r in rows}
        self.assertEqual(len(by_student), 2)
        self.assertEqual(by_student[1].status, "Present")
        self.assertEqual(by_student[1].marked_by, 2)
        self.assertEqual(by_student[2].status, "Present")
        self.assertEqual(by_student[2].marked_by, 2)

        from sqlalchemy import func
        count = self.db.query(func.count()).select_from(Attendance).filter(
            Attendance.student_id == 1, Attendance.date == date(2026, 9, 16)
        ).scalar()
        self.assertEqual(count, 1)

    def test_marking_a_new_date_keeps_previous_day(self):
        self._mark(1, date(2026, 9, 16), [{"student_id": 1, "status": "Present"}], marked_by=1)
        self._mark(1, date(2026, 9, 17), [{"student_id": 1, "status": "Absent"}], marked_by=1)
        rows = self._mark(1, date(2026, 9, 17), [{"student_id": 1, "status": "Present"}], marked_by=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].date, date(2026, 9, 17))
        self.assertEqual(rows[0].status, "Present")

        from sqlalchemy import func
        total = self.db.query(func.count()).select_from(Attendance).filter(Attendance.student_id == 1).scalar()
        self.assertEqual(total, 2)

    def test_class_summary_counts_present_and_absent(self):
        self._mark(
            1,
            date(2026, 9, 16),
            [
                {"student_id": 1, "status": "Present"},
                {"student_id": 2, "status": "Absent"},
                {"student_id": 3, "status": "Present"},
            ],
            marked_by=1,
        )
        summary = repo.class_summary(self.db, 1, date(2026, 9, 16))
        self.assertEqual(summary["present"], 2)
        self.assertEqual(summary["absent"], 1)
        self.assertEqual(summary["total"], 3)

    def test_get_for_student_returns_newest_first(self):
        self._mark(1, date(2026, 9, 15), [{"student_id": 1, "status": "Absent"}], marked_by=1)
        self._mark(1, date(2026, 9, 16), [{"student_id": 1, "status": "Present"}], marked_by=1)
        rows = repo.get_for_student(self.db, 1, None, None)
        self.assertEqual([r.date for r in rows], [date(2026, 9, 16), date(2026, 9, 15)])
        self.assertEqual(rows[0].status, "Present")

    def test_status_is_restricted_to_present_or_absent(self):
        for status in ("Present", "Absent"):
            with self.subTest(status=status):
                entry = AttendanceMarkOne(student_id=1, status=status)
                self.assertEqual(entry.status, status)
        for bogus in ("present", "Late", "", "P"):
            with self.subTest(status=bogus):
                with self.assertRaises(ValidationError):
                    AttendanceMarkOne(student_id=1, status=bogus)

    def test_student_exists_distinguishes_known_from_unknown(self):
        self.assertTrue(repo.student_exists(self.db, 1))
        self.assertFalse(repo.student_exists(self.db, 4242))

    def test_student_in_school_scopes_by_school(self):
        self.assertTrue(repo.student_in_school(self.db, 1, 1))
        self.assertFalse(repo.student_in_school(self.db, 1, 2))
        self.assertFalse(repo.student_in_school(self.db, 4242, 1))

    def test_teacher_teaches_student_checks_students_class(self):
        self.assertTrue(repo.teacher_teaches_student(self.db, teacher_id=7, student_id=1))
        self.assertFalse(repo.teacher_teaches_student(self.db, teacher_id=7, student_id=3))
        self.assertFalse(repo.teacher_teaches_student(self.db, teacher_id=7, student_id=4242))

    def test_class_teacher_without_subject_assignment_can_read_attendance(self):
        # Teacher 99 is the class teacher of class 3 but has no subject rows.
        self.assertTrue(repo.teacher_teaches_class(self.db, teacher_id=99, class_id=3))
        self.assertTrue(repo.teacher_teaches_student(self.db, teacher_id=99, student_id=5))
        self.assertFalse(repo.teacher_teaches_class(self.db, teacher_id=99, class_id=2))

    def test_class_summary_router_binds_teacher_and_admin_to_their_scope(self):
        sys.path.insert(0, "services/attendance_service")
        try:
            from services.attendance_service.repository import class_summary as _summary
            from services.attendance_service.router import class_summary
        finally:
            sys.path.pop(0)

        # A class teacher may only read summaries for the classes they teach.
        teacher = CurrentUser(user_id=1, role="teacher", school_id=1, linked_person_id=99)
        self.assertEqual(class_summary(3, date(2026, 9, 16), self.db, teacher)["class_id"], 3)
        with self.assertRaises(ForbiddenError):
            class_summary(2, date(2026, 9, 16), self.db, teacher)

        # An admin is bound to their own school.
        admin = CurrentUser(user_id=2, role="admin", school_id=1, linked_person_id=None)
        with self.assertRaises(ForbiddenError):
            class_summary(2, date(2026, 9, 16), self.db, admin)

        # An unlinked teacher account is rejected outright.
        ghost = CurrentUser(user_id=3, role="teacher", school_id=1, linked_person_id=None)
        with self.assertRaises(ForbiddenError):
            class_summary(3, date(2026, 9, 16), self.db, ghost)


if __name__ == "__main__":
    unittest.main()