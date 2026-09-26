"""
Subject catalog CRUD tests — per-school scope.

Each school owns its own subject list (subject rows carry school_id). These
cover the repository layer: create within a school, case-insensitive
uniqueness per school (duplicates allowed across schools), rename, the guard
that blocks removal of a subject still referenced by timetable entries, marks,
or teacher-class assignments (otherwise the FK ON DELETE CASCADE rules would
silently wipe marks/assignments), and cross-school isolation.
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


def seed_subject(db: Session, school_id: int = 1, subject_id: int | None = None, name: str = "Maths") -> Subject:
    subject = Subject(name=name, school_id=school_id)
    if subject_id is not None:
        subject.subject_id = subject_id
    db.add(subject)
    db.commit()
    db.refresh(subject)
    return subject


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
        subject = academics_repo.create_subject(self.db, school_id=1, name="Mathematics")
        self.assertEqual(subject.name, "Mathematics")
        self.assertEqual(subject.school_id, 1)
        self.assertEqual([s.subject_id for s in academics_repo.list_subjects(self.db, 1)], [subject.subject_id])

    def test_create_duplicate_name_rejected_case_insensitively_within_school(self):
        academics_repo.create_subject(self.db, school_id=1, name="Science")
        with self.assertRaises(ConflictError):
            academics_repo.create_subject(self.db, school_id=1, name="science")

    def test_same_subject_name_allowed_in_another_school(self):
        academics_repo.create_subject(self.db, school_id=1, name="Maths")
        other = academics_repo.create_subject(self.db, school_id=2, name="Maths")
        self.assertNotEqual(other.school_id, 1)
        self.assertTrue(academics_repo.get_subject(self.db, 2, other.subject_id))

    def test_list_subjects_scoped_to_school(self):
        academics_repo.create_subject(self.db, school_id=1, name="Biology")
        academics_repo.create_subject(self.db, school_id=2, name="Robotics")
        self.assertEqual(
            [s.name for s in academics_repo.list_subjects(self.db, 1)],
            ["Biology"],
        )

    def test_list_subjects_sorted_by_name(self):
        for name in ("Biology", "Algebra", "Chemistry"):
            academics_repo.create_subject(self.db, school_id=1, name=name)
        self.assertEqual(
            [s.name for s in academics_repo.list_subjects(self.db, 1)],
            ["Algebra", "Biology", "Chemistry"],
        )

    def test_get_subject_scoped_to_school(self):
        subject = academics_repo.create_subject(self.db, school_id=1, name="Maths")
        self.assertIsNotNone(academics_repo.get_subject(self.db, 1, subject.subject_id))
        self.assertIsNone(academics_repo.get_subject(self.db, 2, subject.subject_id))
        self.assertIsNone(academics_repo.get_subject(self.db, 1, 4242))

    def test_update_subject_rename(self):
        subject = academics_repo.create_subject(self.db, school_id=1, name="Maths")
        updated = academics_repo.update_subject(self.db, subject, {"name": "Mathematics"})
        self.assertEqual(updated.name, "Mathematics")

    def test_update_to_existing_name_rejected(self):
        academics_repo.create_subject(self.db, school_id=1, name="English")
        other = academics_repo.create_subject(self.db, school_id=1, name="Literature")
        with self.assertRaises(ConflictError):
            academics_repo.update_subject(self.db, other, {"name": "english"})

    def test_rename_into_another_schools_name_is_allowed(self):
        academics_repo.create_subject(self.db, school_id=2, name="English")
        subject = academics_repo.create_subject(self.db, school_id=1, name="Literature")
        updated = academics_repo.update_subject(self.db, subject, {"name": "English"})
        self.assertEqual(updated.name, "English")

    def test_rename_to_own_name_is_allowed(self):
        subject = academics_repo.create_subject(self.db, school_id=1, name="Art")
        updated = academics_repo.update_subject(self.db, subject, {"name": "Art"})
        self.assertEqual(updated.name, "Art")

    def test_delete_unused_subject(self):
        subject = academics_repo.create_subject(self.db, school_id=1, name="History")
        academics_repo.delete_subject(self.db, subject)
        self.assertIsNone(academics_repo.get_subject(self.db, 1, subject.subject_id))

    def test_delete_subject_referenced_by_timetable_blocked(self):
        subject = academics_repo.create_subject(self.db, school_id=1, name="Physics")
        self.db.add(TimetableEntry(
            school_id=1, class_id=1, day_of_week="MON", period_id=1,
            subject_id=subject.subject_id, created_on=datetime.now(),
        ))
        self.db.commit()
        with self.assertRaises(ConflictError):
            academics_repo.delete_subject(self.db, subject)

    def test_delete_subject_referenced_by_marks_blocked(self):
        subject = academics_repo.create_subject(self.db, school_id=1, name="Chemistry")
        self.db.add(Mark(student_id=1, subject_id=subject.subject_id, term="Term 1", score=80))
        self.db.commit()
        with self.assertRaises(ConflictError):
            academics_repo.delete_subject(self.db, subject)

    def test_delete_subject_referenced_by_teacher_assignment_blocked(self):
        subject = academics_repo.create_subject(self.db, school_id=1, name="Geography")
        self.db.add(TeacherClassSubject(teacher_id=1, class_id=1, subject_id=subject.subject_id))
        self.db.commit()
        with self.assertRaises(ConflictError):
            academics_repo.delete_subject(self.db, subject)

    def test_missing_subject_used_to_seed_is_expected_reference(self):
        # Subjects from a school can be referenced while that school owns them.
        subject = academics_repo.create_subject(self.db, school_id=3, name="Art")
        self.assertTrue(academics_repo.get_subject(self.db, 3, subject.subject_id))
        self.assertFalse(academics_repo.get_subject(self.db, 2, subject.subject_id))


if __name__ == "__main__":
    unittest.main()