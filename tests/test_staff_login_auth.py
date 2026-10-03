"""Staff sign-in must resolve identities and enforce the active tenant link."""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.exceptions import UnauthorizedError
from common.models import Base, Parent, School, Staff, User
from common.security import hash_password
import services.auth_service.service as auth_service

TABLES = ["schools", "parents", "staff", "users"]


class StaffLoginAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(cls.engine, tables=[Base.metadata.tables[name] for name in TABLES])
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db = self.Session()
        for table in (User, Staff, Parent, School):
            self.db.query(table).delete()
        self.db.add_all([
            School(school_id=1, name="North", address="1 Road", pincode="1", city="City",
                   state="State", primary_contact="1", primary_email="north@example.test"),
            School(school_id=2, name="South", address="2 Road", pincode="2", city="City",
                   state="State", primary_contact="2", primary_email="south@example.test"),
        ])
        self.db.add(Parent(parent_id=90, school_id=1, name="Parent", phone="90", email="parent@example.test"))
        self.db.add_all([
            Staff(staff_id=10, school_id=1, name="Teacher One", role="Teacher", phone="10", email="teacher@example.test"),
            Staff(staff_id=20, school_id=2, name="Staff Two", role="Clerk", phone="20"),
            Staff(staff_id=30, school_id=1, name="Inactive", role="Clerk", phone="30", is_active=False),
        ])
        self.db.add_all([
            User(user_id=100, school_id=1, role="teacher", username="teacher10",
                 email="teacher@example.test", linked_person_id=10,
                 password_hash=hash_password("correct-password")),
            User(user_id=200, school_id=1, role="staff", username="cross-school",
                 linked_person_id=20, password_hash=hash_password("correct-password")),
            User(user_id=300, school_id=1, role="staff", username="inactive",
                 linked_person_id=30, password_hash=hash_password("correct-password")),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_staff_login_accepts_trimmed_case_insensitive_username_and_email(self):
        user = auth_service.authenticate(self.db, "  TEACHER10  ", "correct-password")
        self.assertEqual(user.user_id, 100)
        user = auth_service.authenticate(self.db, " TEACHER@EXAMPLE.TEST ", "correct-password")
        self.assertEqual(user.user_id, 100)

    def test_staff_login_rejects_wrong_password(self):
        with self.assertRaises(UnauthorizedError):
            auth_service.authenticate(self.db, "teacher10", "incorrect")

    def test_staff_login_rejects_inactive_staff_record(self):
        with self.assertRaises(UnauthorizedError):
            auth_service.authenticate(self.db, "inactive", "correct-password")

    def test_staff_login_rejects_a_linked_record_from_another_school(self):
        with self.assertRaises(UnauthorizedError):
            auth_service.authenticate(self.db, "cross-school", "correct-password")


if __name__ == "__main__":
    unittest.main()
