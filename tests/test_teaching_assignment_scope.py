"""
Teaching-assignment tenancy tests.

people_service's add_teaching_assignment must refuse links whose subject,
teacher, or class does not belong to the caller's school, otherwise a
teacher could end up "teaching" another school's subject in another
school's class.
"""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.models import Base, Subject, Teacher, SchoolClass
from common.exceptions import ConflictError
import services.people_service.repository as people_repo

TABLES = [
    "subjects",
    "teachers",
    "classes",
    "teacher_class_subjects",
]


def seed(db: Session):
    db.add_all(
        [
            Subject(subject_id=1, school_id=1, name="Maths"),
            Subject(subject_id=2, school_id=2, name="Robotics"),
            Teacher(teacher_id=1, school_id=1, name="T. One", role_title="Maths", phone="000"),
            Teacher(teacher_id=2, school_id=2, name="T. Two", role_title="Robotics", phone="000"),
            SchoolClass(class_id=1, school_id=1, name="Class 1"),
            SchoolClass(class_id=2, school_id=2, name="Class 2"),
        ]
    )
    db.commit()


class TeachingAssignmentScopeTests(unittest.TestCase):
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

    def _assignment(self, **overrides):
        data = {
            "teacher_id": 1,
            "class_id": 1,
            "subject_id": 1,
            "is_class_teacher": False,
        }
        data.update(overrides)
        return people_repo.add_teaching_assignment(self.db, school_id=1, data=data)

    def test_valid_assignment_within_school_created(self):
        tcs = self._assignment()
        self.assertEqual(tcs.subject_id, 1)
        self.assertEqual(tcs.teacher_id, 1)
        self.assertEqual(tcs.class_id, 1)

    def test_foreign_subject_rejected(self):
        with self.assertRaises(ConflictError):
            self._assignment(subject_id=2)

    def test_foreign_teacher_rejected(self):
        with self.assertRaises(ConflictError):
            self._assignment(teacher_id=2)

    def test_foreign_class_rejected(self):
        with self.assertRaises(ConflictError):
            self._assignment(class_id=2)

    def test_teaching_load_lists_assignment(self):
        self._assignment()
        load = people_repo.teaching_load(self.db, 1)
        self.assertEqual(len(load), 1)
        self.assertEqual(load[0].subject_id, 1)


if __name__ == "__main__":
    unittest.main()