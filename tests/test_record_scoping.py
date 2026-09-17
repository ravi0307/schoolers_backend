"""
Privacy/scoping tests for parent-facing reads.

A parent must never be able to read another family's data by guessing ids.
These cover the parent_student link checks used by the marks and attendance
services (`is_parent_of`), which back the router-level guards.
"""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.models import Base, Parent, ParentStudent, Student
import services.marks_service.repository as marks_repo
import services.attendance_service.repository as attendance_repo
import services.people_service.repository as people_repo

TABLES = [
    "schools", "classes", "subjects",
    "parents", "students", "parent_student", "marks", "attendance",
]


def seed(db: Session):
    db.add_all(
        [
            Parent(parent_id=225, school_id=1, name="Mrs Sinha", phone="9000000001"),
            Parent(parent_id=999, school_id=1, name="Mr Choudhry", phone="9000000002"),
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            Student(student_id=2, school_id=1, class_id=1, admission_no="A2", name="Arjun"),
            ParentStudent(parent_id=225, student_id=2),
            ParentStudent(parent_id=999, student_id=1),
        ]
    )
    db.commit()


class ParentScopingTests(unittest.TestCase):
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

    def test_marks_is_parent_of_accepts_own_child(self):
        self.assertTrue(marks_repo.is_parent_of(self.db, parent_id=225, student_id=2))

    def test_marks_is_parent_of_rejects_other_family(self):
        # Parent 225 must not claim Arjun's classmate Riya (student 1).
        self.assertFalse(marks_repo.is_parent_of(self.db, parent_id=225, student_id=1))

    def test_marks_is_parent_of_rejects_unknown_student(self):
        self.assertFalse(marks_repo.is_parent_of(self.db, parent_id=225, student_id=4242))

    def test_attendance_is_parent_of_accepts_own_child(self):
        self.assertTrue(attendance_repo.is_parent_of(self.db, parent_id=225, student_id=2))

    def test_attendance_is_parent_of_rejects_other_family(self):
        self.assertFalse(attendance_repo.is_parent_of(self.db, parent_id=225, student_id=1))

    def test_each_student_is_owned_by_exactly_their_parent(self):
        self.assertTrue(marks_repo.is_parent_of(self.db, parent_id=999, student_id=1))
        self.assertFalse(marks_repo.is_parent_of(self.db, parent_id=999, student_id=2))
        self.assertTrue(attendance_repo.is_parent_of(self.db, parent_id=999, student_id=1))
        self.assertFalse(attendance_repo.is_parent_of(self.db, parent_id=999, student_id=2))

    def test_get_parent_scopes_to_school(self):
        self.assertIsNotNone(people_repo.get_parent(self.db, school_id=1, parent_id=225))
        # Same parent, different school -> treated as not found.
        self.assertIsNone(people_repo.get_parent(self.db, school_id=2, parent_id=225))
        self.assertIsNone(people_repo.get_parent(self.db, school_id=1, parent_id=4242))

    def test_get_parent_ignores_inactive_records(self):
        parent = self.db.query(Parent).filter(Parent.parent_id == 225).one()
        parent.is_active = False
        self.db.commit()
        self.assertIsNone(people_repo.get_parent(self.db, school_id=1, parent_id=225))

    def test_children_of_parent_returns_only_that_familys_students(self):
        self.assertEqual(
            [s.student_id for s in people_repo.children_of_parent(self.db, 225)], [2]
        )
        self.assertEqual(
            [s.student_id for s in people_repo.children_of_parent(self.db, 999)], [1]
        )
        self.assertEqual(people_repo.children_of_parent(self.db, 4242), [])


if __name__ == "__main__":
    unittest.main()