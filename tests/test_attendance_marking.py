"""
Functional tests for the attendance marking (bulk upsert) flow.

Runs against an in-memory SQLite database. The /attendance/mark endpoint
upserts with ON CONFLICT (student_id, date), which only works when the
attendance table carries a matching UNIQUE constraint; these tests exercise
both the insert path and the re-mark (conflict) path, plus reads.
"""
import unittest
from datetime import date

from pydantic import ValidationError
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from common.models import Base, Attendance, Student, TeacherClassSubject
import services.attendance_service.repository as repo
from services.attendance_service.schemas import AttendanceMarkOne

TABLES = ["attendance", "students", "teacher_class_subjects"]


def seed(db: Session):
    db.add_all(
        [
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Alice"),
            Student(student_id=2, school_id=1, class_id=1, admission_no="A2", name="Bob"),
            Student(student_id=3, school_id=1, class_id=2, admission_no="A3", name="Carol"),
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


if __name__ == "__main__":
    unittest.main()