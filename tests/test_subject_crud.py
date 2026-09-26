"""
Subject catalog CRUD tests — per-school scope, soft deactivation.

Each school owns its own subject list (subject rows carry school_id).
Deactivation is non-destructive: a subject can be marked inactive (hiding it
from new assignments) and reactivated later, while marks/timetable references
stay intact and keep resolving by name. Names stay unique within a school
across both states, so the same name can't be created while an inactive copy
of it exists.
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


def create_subject(db: Session, school_id: int = 1, name: str = "Maths") -> Subject:
    return academics_repo.create_subject(db, school_id=school_id, name=name)


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

    def test_create_subject_active_by_default(self):
        subject = create_subject(self.db, name="Mathematics")
        self.assertEqual(subject.name, "Mathematics")
        self.assertEqual(subject.school_id, 1)
        self.assertTrue(subject.is_active)
        self.assertEqual(academics_repo.list_subjects(self.db, 1), [subject])

    def test_create_duplicate_name_rejected_case_insensitively_within_school(self):
        create_subject(self.db, name="Science")
        with self.assertRaises(ConflictError):
            create_subject(self.db, name="science")

    def test_same_subject_name_allowed_in_another_school(self):
        create_subject(self.db, school_id=1, name="Maths")
        other = create_subject(self.db, school_id=2, name="Maths")
        self.assertFalse(academics_repo.get_subject(self.db, 1, other.subject_id))

    def test_list_subjects_scoped_to_school(self):
        create_subject(self.db, school_id=1, name="Biology")
        create_subject(self.db, school_id=2, name="Robotics")
        self.assertEqual(
            [s.name for s in academics_repo.list_subjects(self.db, 1)],
            ["Biology"],
        )

    def test_list_subjects_sorted_active_first_then_by_name(self):
        for name in ("Biology", "Algebra", "Chemistry"):
            create_subject(self.db, name=name)
        biology = academics_repo.get_subject(self.db, 1, academics_repo.list_subjects(self.db, 1)[0].subject_id)
        # Force ordering explicitly instead of relying on the first listed row.
        active = [s for s in academics_repo.list_subjects(self.db, 1) if s.is_active]
        self.assertEqual([s.name for s in active], ["Algebra", "Biology", "Chemistry"])

    def test_list_subjects_keeps_inactive_rows_and_orders_active_first(self):
        retired = create_subject(self.db, name="Latin")
        academics_repo.deactivate_subject(self.db, retired)
        create_subject(self.db, name="Algebra")
        names = [s.name for s in academics_repo.list_subjects(self.db, 1)]
        self.assertEqual(names, ["Algebra", "Latin"])
        flags = {s.name: s.is_active for s in academics_repo.list_subjects(self.db, 1)}
        self.assertFalse(flags["Latin"])
        self.assertTrue(flags["Algebra"])

    def test_get_subject_scoped_to_school(self):
        subject = create_subject(self.db)
        self.assertIsNotNone(academics_repo.get_subject(self.db, 1, subject.subject_id))
        self.assertIsNone(academics_repo.get_subject(self.db, 2, subject.subject_id))
        self.assertIsNone(academics_repo.get_subject(self.db, 1, 4242))

    def test_get_subject_finds_inactive(self):
        retired = create_subject(self.db, name="Latin")
        academics_repo.deactivate_subject(self.db, retired)
        self.assertIsNotNone(academics_repo.get_subject(self.db, 1, retired.subject_id))

    def test_update_subject_rename(self):
        subject = create_subject(self.db, name="Maths")
        updated = academics_repo.update_subject(self.db, subject, {"name": "Mathematics"})
        self.assertEqual(updated.name, "Mathematics")

    def test_update_to_existing_name_rejected(self):
        create_subject(self.db, name="English")
        other = create_subject(self.db, name="Literature")
        with self.assertRaises(ConflictError):
            academics_repo.update_subject(self.db, other, {"name": "english"})

    def test_rename_into_another_schools_name_is_allowed(self):
        create_subject(self.db, school_id=2, name="English")
        subject = create_subject(self.db, school_id=1, name="Literature")
        updated = academics_repo.update_subject(self.db, subject, {"name": "English"})
        self.assertEqual(updated.name, "English")

    def test_rename_to_own_name_is_allowed(self):
        subject = create_subject(self.db, name="Art")
        updated = academics_repo.update_subject(self.db, subject, {"name": "Art"})
        self.assertEqual(updated.name, "Art")

    def test_deactivate_then_reactivate_round_trip(self):
        subject = create_subject(self.db, name="History")
        inactive = academics_repo.deactivate_subject(self.db, subject)
        self.assertFalse(inactive.is_active)
        reactivated = academics_repo.activate_subject(self.db, inactive)
        self.assertTrue(reactivated.is_active)

    def test_deactivate_is_allowed_even_when_referenced(self):
        subject = create_subject(self.db, name="Physics")
        self.db.add(TimetableEntry(
            school_id=1, class_id=1, day_of_week="MON", period_id=1,
            subject_id=subject.subject_id, created_on=datetime.now(),
        ))
        self.db.add(Mark(student_id=1, subject_id=subject.subject_id, term="Term 1", score=80))
        self.db.add(TeacherClassSubject(teacher_id=1, class_id=1, subject_id=subject.subject_id))
        self.db.commit()
        inactive = academics_repo.deactivate_subject(self.db, subject)
        self.assertFalse(inactive.is_active)
        # References are untouched by the soft delete.
        self.assertEqual(self.db.query(Mark).filter(Mark.subject_id == subject.subject_id).count(), 1)
        self.assertEqual(
            self.db.query(TimetableEntry).filter(TimetableEntry.subject_id == subject.subject_id).count(), 1
        )

    def test_reactivation_restores_same_name_ownership(self):
        retired = create_subject(self.db, name="Latin")
        academics_repo.deactivate_subject(self.db, retired)
        # Name still owned by the inactive row, so creating a new one is blocked.
        with self.assertRaises(ConflictError):
            create_subject(self.db, name="latin")
        reactivated = academics_repo.activate_subject(self.db, retired)
        self.assertTrue(reactivated.is_active)

    def test_rename_inactive_subject_then_reactivate(self):
        retired = create_subject(self.db, name="OldName")
        academics_repo.deactivate_subject(self.db, retired)
        renamed = academics_repo.update_subject(self.db, retired, {"name": "NewName"})
        reactivated = academics_repo.activate_subject(self.db, renamed)
        self.assertTrue(reactivated.is_active)
        self.assertEqual(reactivated.name, "NewName")


if __name__ == "__main__":
    unittest.main()