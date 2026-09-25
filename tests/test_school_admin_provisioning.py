"""
Tests for the master-portal school admin provisioning.

Covers auto-creating a school's admin login from the admin's first/last name,
the firstname.lastname username rule (spaces removed, numeric suffix on
collision), and renaming/provisioning through school updates. Runs against an
in-memory SQLite database exactly like the media-gallery tests.
"""
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.models import Base, User
from common.security import verify_password
import services.schools_service.repository as repo

_ENGINES = []

# SQLite cannot render the Postgres-only JSONB columns; create just the tables
# the provisioning flow exercises (everything except JSONB tables).
SQLITE_TABLES = [
    t
    for t in Base.metadata.sorted_tables
    if not any(col.type.__class__.__name__ == "JSONB" for col in t.columns)
]


def school_data(name):
    return {
        "name": name,
        "address": "1 Test Street",
        "pincode": "560001",
        "city": "Bengaluru",
        "state": "Karnataka",
        "country": "India",
        "primary_contact": "9876543210",
        "primary_email": f"{name.lower().replace(' ', '')}@school.example",
        "first_name": "Ravi",
        "last_name": "Kumar",
    }


@mock.patch("services.schools_service.repository.send_school_registered_email")
class MasterProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        _ENGINES.append(self.engine)
        Base.metadata.create_all(self.engine, tables=SQLITE_TABLES)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.Session().close()
        self.engine.dispose()

    def _admin_user(self, school_id: int) -> User | None:
        session = self.Session()
        try:
            return (
                session.query(User)
                .filter(User.school_id == school_id, User.role == "admin")
                .first()
            )
        finally:
            session.close()

    def test_create_school_provisions_admin_login(self, _mail):
        session = self.Session()
        school = repo.create_school(session, school_data("Sunrise Public School"))
        admin = self._admin_user(school.school_id)
        self.assertIsNotNone(admin)
        self.assertEqual(admin.username, "ravi.kumar")
        self.assertEqual(admin.email, "sunrisepublicschool@school.example")
        self.assertEqual(school.admin_username, "ravi.kumar")
        self.assertTrue(school.admin_password)
        self.assertTrue(verify_password(school.admin_password, admin.password_hash))
        self.assertEqual(school.first_name, "Ravi")
        self.assertEqual(school.last_name, "Kumar")

    def test_create_school_email_includes_admin_credentials(self, _mail):
        session = self.Session()
        school = repo.create_school(session, school_data("Sunrise Public School"))
        _mail.assert_called_once_with(
            "Sunrise Public School",
            ["sunrisepublicschool@school.example"],
            username="ravi.kumar",
            password=school.admin_password,
        )

    def test_create_school_without_names_emails_without_credentials(self, _mail):
        session = self.Session()
        data = school_data("Legacy High")
        data["first_name"] = None
        data["last_name"] = None
        repo.create_school(session, data)
        self.assertIsNone(self._admin_user(1))
        _mail.assert_called_once_with(
            "Legacy High",
            ["legacyhigh@school.example"],
            username=None,
            password=None,
        )

    def test_create_school_removes_spaces_from_username(self, _mail):
        session = self.Session()
        data = school_data("Green Valley")
        data["first_name"] = "Ravi Kumar"
        data["last_name"] = "Verma"
        school = repo.create_school(session, data)
        self.assertEqual(self._admin_user(school.school_id).username, "ravikumar.verma")
        data2 = school_data("Blue Hills")
        data2["first_name"] = "Ravi "
        data2["last_name"] = " Kumar "
        school2 = repo.create_school(session, data2)
        self.assertEqual(self._admin_user(school2.school_id).username, "ravi.kumar")

    def test_duplicate_username_gets_sequential_suffix(self, _mail):
        session = self.Session()
        repo.create_school(session, school_data("Sunrise Public School"))
        school2 = repo.create_school(session, school_data("Lakeside Academy"))
        self.assertEqual(self._admin_user(school2.school_id).username, "ravi.kumar2")
        school3 = repo.create_school(session, school_data("Riverside Public"))
        self.assertEqual(self._admin_user(school3.school_id).username, "ravi.kumar3")
        admins = session.query(User).filter(User.role == "admin").count()
        self.assertEqual(admins, 3)

    def test_update_school_renames_admin_username(self, _mail):
        session = self.Session()
        repo.create_school(session, school_data("Sunrise Public School"))
        school = repo.get_school(session, 1)  # fresh object, like the HTTP flow
        school = repo.update_school(session, school, {"first_name": "Anjali", "last_name": "Rao"})
        admin = self._admin_user(school.school_id)
        self.assertEqual(admin.username, "anjali.rao")
        self.assertIsNone(getattr(school, "admin_password", None), "no fresh credentials on a plain rename")

    def test_update_school_provisions_admin_when_names_added(self, _mail):
        session = self.Session()
        data = school_data("Heritage High")
        data["first_name"] = None
        data["last_name"] = None
        school = repo.create_school(session, data)
        self.assertIsNone(self._admin_user(school.school_id))
        school = repo.update_school(session, school, {"first_name": "Meera", "last_name": "Nair"})
        admin = self._admin_user(school.school_id)
        self.assertIsNotNone(admin)
        self.assertEqual(admin.username, "meera.nair")
        self.assertEqual(admin.email, "heritagehigh@school.example")
        self.assertEqual(school.admin_username, "meera.nair")
        self.assertTrue(school.admin_password)

    def test_update_school_syncs_admin_email_when_primary_changes(self, _mail):
        session = self.Session()
        school = repo.create_school(session, school_data("Sunrise Public School"))
        repo.update_school(session, school, {"primary_email": "new.office@example.com"})
        admin = self._admin_user(school.school_id)
        self.assertEqual(admin.email, "new.office@example.com")

    def test_rename_keeps_username_when_names_unchanged(self, _mail):
        session = self.Session()
        school = repo.create_school(session, school_data("Sunrise Public School"))
        admin = self._admin_user(school.school_id)
        repo.update_school(session, school, {"city": "Chennai"})
        self.assertEqual(self._admin_user(school.school_id).user_id, admin.user_id)
        self.assertEqual(admin.username, "ravi.kumar")

    def test_is_active_users_count_unchanged_by_rename(self, _mail):
        session = self.Session()
        school = repo.create_school(session, school_data("Sunrise Public School"))
        repo.update_school(session, school, {"first_name": "Anjali", "last_name": "Rao"})
        admins = session.query(User).filter(User.role == "admin").count()
        self.assertEqual(admins, 1)


if __name__ == "__main__":
    unittest.main()