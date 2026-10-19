"""
Teaching-assignment tenancy tests.

people_service's add_teaching_assignment must refuse links whose subject,
teacher, or class does not belong to the caller's school, otherwise a
teacher could end up "teaching" another school's subject in another
school's class.
"""
import sys
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.models import Base, SchoolClass, Staff, Subject
from common.exceptions import ConflictError, ForbiddenError, NotFoundError
import services.people_service.repository as people_repo

TABLES = [
    "staff",
    "subjects",
    "classes",
    "teacher_class_subjects",
]


def _load_people_router():
    """Import the people router with its flat `import repository` resolution,
    mirroring the _load helper in test_rollout_flag.py."""
    sys.path.insert(0, "services/people_service")
    try:
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)
        from services.people_service import router as people_router
        return people_router
    finally:
        sys.path.pop(0)
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)


def seed(db: Session):
    db.add_all(
        [
            Subject(subject_id=1, school_id=1, name="Maths"),
            Subject(subject_id=2, school_id=2, name="Robotics"),
            Staff(staff_id=1, school_id=1, name="T. One", role="Teacher",
                  person_type="teacher", role_title="Maths", phone="000"),
            Staff(staff_id=2, school_id=2, name="T. Two", role="Teacher",
                  person_type="teacher", role_title="Robotics", phone="000"),
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
            self._assignment(staff_id=2)

    def test_foreign_class_rejected(self):
        with self.assertRaises(ConflictError):
            self._assignment(class_id=2)

    def test_teaching_load_lists_assignment(self):
        self._assignment()
        load = people_repo.teaching_load(self.db, 1)
        self.assertEqual(len(load), 1)
        self.assertEqual(load[0].subject_id, 1)

    def test_teacher_load_endpoint_is_self_only(self):
        router = _load_people_router()
        me = CurrentUser(user_id=1, role="teacher", school_id=1, linked_person_id=1)
        # Own id: allowed (empty assignment list is still a valid answer).
        self.assertEqual(router.teaching_load(1, self.db, me), [])
        # Another teacher's id — even inside the same school — is refused.
        with self.assertRaises(ForbiddenError):
            router.teaching_load(2, self.db, me)

    def test_admin_load_endpoint_is_school_scoped(self):
        router = _load_people_router()
        admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)
        admin2 = CurrentUser(user_id=12, role="admin", school_id=2, linked_person_id=None)
        schoolless = CurrentUser(user_id=13, role="admin", school_id=None, linked_person_id=None)
        self.assertEqual(router.teaching_load(1, self.db, admin1), [])
        # staff 2 belongs to school 2 -> uniform 404 for school 1's admin
        with self.assertRaises(NotFoundError):
            router.teaching_load(2, self.db, admin1)
        self.assertEqual(router.teaching_load(2, self.db, admin2), [])
        # A school-less token cannot read any load.
        with self.assertRaises(NotFoundError):
            router.teaching_load(1, self.db, schoolless)


if __name__ == "__main__":
    unittest.main()
