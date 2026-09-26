"""
Subject catalog CRUD tests.

Subjects are a global catalog (no school_id) managed by admins. These cover
the repository layer: create, case-insensitive uniqueness, rename, and the
guard that blocks removal of a subject still referenced by timetable entries,
marks, or teacher-class assignments (otherwise the FK ON DELETE CASCADE rules
would silently wipe marks/assignments).
"""
import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.models import (
    Base, Subject, Mark, TimetableEntry, TeacherClassSubject,
)
from common.exceptions import ConflictError
import services.academics_service.repository as academics_repo

TABLES = [
    "subjects",
    "timetable_entries",
    "marks",
    "teacher_class_subjects",
]


class SubjectCrudTests(unittest.TestCase):
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

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_create_subject(self):
        subject = academics_repo.create_subject(self.db, "Mathematics")
        self.assertEqual(subject.name, "Mathematics")
        self.assertEqual([s.subject_id for s in academics_repo.list_subjects(self.db)], [subject.subject_id])

    def test_create_duplicate_name_rejected_case_insensitively(self):
        academics_repo.create_subject(self.db, "Science")
        with self.assertRaises(ConflictError):
            academics_repo.create_subject(self.db, "science")

    def test_list_subjects_sorted_by_name(self):
        for name in ("Biology", "Algebra", "Chemistry"):
            academics_repo.create_subject(self.db, name)
        self.assertEqual(
            [s.name for s in academics_repo.list_subjects(self.db)],
            ["Algebra", "Biology", "Chemistry"],
        )

    def test_get_subject(self):
        subject = academics_repo.create_subject(self.db, "Maths")
        self.assertIsNotNone(academics_repo.get_subject(self.db, subject.subject_id))
        self.assertIsNone(academics_repo.get_subject(self.db, 4242))

    def test_update_subject_rename(self):
        subject = academics_repo.create_subject(self.db, "Maths")
        updated = academics_repo.update_subject(self.db, subject, {"name": "Mathematics"})
        self.assertEqual(updated.name, "Mathematics")

    def test_update_to_existing_name_rejected(self):
        academics_repo.create_subject(self.db, "English")
        other = academics_repo.create_subject(self.db, "Literature")
        with self.assertRaises(ConflictError):
            academics_repo.update_subject(self.db, other, {"name": "english"})

    def test_rename_to_own_name_is_allowed(self):
        subject = academics_repo.create_subject(self.db, "Art")
        updated = academics_repo.update_subject(self.db, subject, {"name": "Art"})
        self.assertEqual(updated.name, "Art")

    def test_delete_unused_subject(self):
        subject = academics_repo.create_subject(self.db, "History")
        academics_repo.delete_subject(self.db, subject)
        self.assertIsNone(academics_repo.get_subject(self.db, subject.subject_id))

    def test_delete_subject_referenced_by_timetable_blocked(self):
        subject = academics_repo.create_subject(self.db, "Physics")
        self.db.add(TimetableEntry(
            school_id=1, class_id=1, day_of_week="MON", period_id=1,
            subject_id=subject.subject_id, created_on=datetime.now(),
        ))
        self.db.commit()
        with self.assertRaises(ConflictError):
            academics_repo.delete_subject(self.db, subject)

    def test_delete_subject_referenced_by_marks_blocked(self):
        subject = academics_repo.create_subject(self.db, "Chemistry")
        self.db.add(Mark(student_id=1, subject_id=subject.subject_id, term="Term 1", score=80))
        self.db.commit()
        with self.assertRaises(ConflictError):
            academics_repo.delete_subject(self.db, subject)

    def test_delete_subject_referenced_by_teacher_assignment_blocked(self):
        subject = academics_repo.create_subject(self.db, "Geography")
        self.db.add(TeacherClassSubject(teacher_id=1, class_id=1, subject_id=subject.subject_id))
        self.db.commit()
        with self.assertRaises(ConflictError):
            academics_repo.delete_subject(self.db, subject)


if __name__ == "__main__":
    unittest.main()