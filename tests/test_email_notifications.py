"""
Tests for the account-lifecycle email notifications:

- new school / admin account created or removed,
- staff added or removed,
- student enrolled or withdrawn (notified via their linked parent).

Email dispatch is best-effort: an SMTP failure must never break the primary
operation, so these tests also cover the swallow-and-log path.
"""
import unittest
from datetime import date
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.database import Base
from common.models import Parent, Pilot, School, Staff, Student, Teacher, User
import common.email as email_helpers
import services.schools_service.repository as schools_repo
import services.people_service.repository as people_repo


def make_session():
    engine = create_engine("sqlite://")
    tables = [t for t in Base.metadata.sorted_tables if t.name != "website_pages"]
    Base.metadata.create_all(engine, tables=tables)
    return engine, sessionmaker(bind=engine, autoflush=False, future=True)


@mock.patch("common.email.send_email")
class NotificationHelperTests(unittest.TestCase):
    def test_school_registered_email(self, mock_send):
        sent = email_helpers.send_school_registered_email("Sunrise High", ["a@x.org", "b@x.org"])
        self.assertTrue(sent)
        args = mock_send.call_args[0]
        self.assertEqual(args[0], ["a@x.org", "b@x.org"])
        self.assertIn("Sunrise High", args[1])

    def test_school_registered_email_includes_credentials_and_reset_steps(self, mock_send):
        sent = email_helpers.send_school_registered_email(
            "Sunrise High", ["a@x.org"], username="sunrise.admin", password="Temp!1234"
        )
        self.assertTrue(sent)
        body = mock_send.call_args[0][2]
        self.assertIn("Username: sunrise.admin", body)
        self.assertIn("Temporary password: Temp!1234", body)
        self.assertIn("Forgot password?", body)
        self.assertIn("6-digit OTP", body)
        self.assertIn("set a new password", body)

    def test_deduplicates_and_drops_empty_recipients(self, mock_send):
        sent = email_helpers.send_staff_added_email("S", "Ann", ["  ann@x.org ", "ann@x.org", ""])
        self.assertTrue(sent)
        args = mock_send.call_args[0]
        self.assertEqual(args[0], ["ann@x.org"])

    def test_no_recipients_skips_without_error(self, mock_send):
        sent = email_helpers.send_student_removed_email("S", "Kid", [])
        self.assertFalse(sent)
        mock_send.assert_not_called()

    def test_send_failure_is_swallowed(self, mock_send):
        mock_send.side_effect = RuntimeError("SMTP down")
        sent = email_helpers.send_school_removed_email("S", ["a@x.org"])
        self.assertFalse(sent)
        mock_send.assert_called_once()


class SchoolsNotificationTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_session()
        self.db: Session = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @mock.patch("common.email.send_email")
    def test_create_school_emails_school_addresses(self, mock_send):
        school = schools_repo.create_school(self.db, {
            "name": "Sunrise High",
            "address": "1 Main Rd",
            "pincode": "110001",
            "city": "New Delhi",
            "state": "Delhi",
            "primary_contact": "9999999999",
            "primary_email": "office@sunrise.edu",
            "alternative_email": "alt@sunrise.edu",
        })
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(sorted(args[0]), ["alt@sunrise.edu", "office@sunrise.edu"])
        self.assertIn("Sunrise High", args[1])
        self.assertNotIn("Temporary password", args[2], "no credentials for a school without an admin account")
        self.assertTrue(self.db.query(School).filter(School.school_id == school.school_id).count() == 1)

    @mock.patch("common.email.send_email")
    def test_create_school_with_admin_names_emails_credentials(self, mock_send):
        school = schools_repo.create_school(self.db, {
            "name": "Northstar High",
            "address": "3 Main Rd",
            "pincode": "400001",
            "city": "Mumbai",
            "state": "Maharashtra",
            "primary_contact": "7777777777",
            "primary_email": "office@northstar.edu",
            "first_name": "Ravi",
            "last_name": "Kumar",
        })
        mock_send.assert_called_once()
        _, subject, body = mock_send.call_args[0]
        self.assertIn("Welcome, Northstar High!", subject)
        self.assertIn("Sign-in details", body)
        self.assertIn("Username: ravi.kumar", body)
        self.assertIn("Temporary password: ", body)
        self.assertIn("Forgot password?", body)
        self.assertIn("6-digit OTP", body)
        self.assertIn("set a new password", body)
        self.assertTrue(self.db.query(User).filter(User.school_id == school.school_id, User.role == "admin").count() == 1)

    @mock.patch("common.email.send_email")
    def test_delete_school_emails_school_addresses(self, mock_send):
        school = schools_repo.create_school(self.db, {
            "name": "Sunset High",
            "address": "2 Main Rd",
            "pincode": "560001",
            "city": "Bengaluru",
            "state": "Karnataka",
            "primary_contact": "8888888888",
            "primary_email": "office@sunset.edu",
        })
        mock_send.reset_mock()
        schools_repo.delete_school(self.db, school)
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], ["office@sunset.edu"])
        self.assertIn("removed", args[2].lower())
        self.assertIn("Sunset High", args[2])

    def _school(self):
        return {
            "name": "Northstar High",
            "address": "3 Main Rd",
            "pincode": "400001",
            "city": "Mumbai",
            "state": "Maharashtra",
            "primary_contact": "7777777777",
            "primary_email": "office@northstar.edu",
        }

    @mock.patch("common.email.send_email")
    def test_delete_school_deactivates_staff_and_students(self, mock_send):
        school = schools_repo.create_school(self.db, self._school())
        staff = people_repo.create_staff(self.db, school.school_id, {
            "name": "Staff One", "role": "Admin", "phone": "111",
        })
        student = people_repo.create_student(self.db, school.school_id, {
            "name": "Student One", "class_id": 1, "admission_no": "A100",
        })
        mock_send.reset_mock()

        schools_repo.delete_school(self.db, school)

        self.assertFalse(self.db.query(Staff).filter(Staff.staff_id == staff.staff_id).one().is_active)
        self.assertFalse(self.db.query(Student).filter(Student.student_id == student.student_id).one().is_active)
        self.assertFalse(self.db.query(School).filter(School.school_id == school.school_id).one().is_active)

    @mock.patch("common.email.send_email")
    def test_delete_school_blocks_staff_login_users(self, mock_send):
        school = schools_repo.create_school(self.db, self._school())
        pilot = people_repo.create_staff(self.db, school.school_id, {
            "name": "Driver One", "role": "Pilot", "phone": "222",
        })
        user = self.db.query(User).filter(User.role == "pilot").one()
        self.assertTrue(user.is_active)
        mock_send.reset_mock()

        schools_repo.delete_school(self.db, school)

        self.assertFalse(user.is_active)
        self.assertFalse(self.db.query(Pilot).filter(Pilot.staff_id == pilot.staff_id).one().is_active)

    @mock.patch("common.email.send_email")
    def test_delete_school_cascades_to_every_school_role(self, mock_send):
        school = schools_repo.create_school(self.db, self._school())

        # Admin login account of the school.
        self.db.add(User(
            school_id=school.school_id, role="admin",
            username="admin@northstar", password_hash="x",
        ))

        # Plain staff member plus its (role "staff") login account.
        admin_staff = people_repo.create_staff(self.db, school.school_id, {
            "name": "Staff Admin", "role": "Admin", "phone": "11",
        })
        self.db.add(User(
            school_id=school.school_id, role="staff", username="staff@northstar",
            password_hash="x", linked_person_id=admin_staff.staff_id,
        ))

        # Pilot staff: create_staff auto-creates the pilot + "pilot" user rows.
        pilot_staff = people_repo.create_staff(self.db, school.school_id, {
            "name": "Driver", "role": "Pilot", "phone": "22",
        })

        # Teacher + its login account.
        teacher = Teacher(school_id=school.school_id, name="Teacher T", role_title="Math", phone="33")
        self.db.add(teacher)
        self.db.flush()
        self.db.add(User(
            school_id=school.school_id, role="teacher", username="teacher@northstar",
            password_hash="x", linked_person_id=teacher.teacher_id,
        ))

        # Parent + its login account.
        parent = Parent(school_id=school.school_id, name="Parent P", phone="44")
        self.db.add(parent)
        self.db.flush()
        self.db.add(User(
            school_id=school.school_id, role="parent", username="parent@northstar",
            password_hash="x", linked_person_id=parent.parent_id,
        ))

        # Students (no login accounts of their own).
        students = [
            people_repo.create_student(self.db, school.school_id, {
                "name": "Kid A", "class_id": 1, "admission_no": "A201",
            }),
            people_repo.create_student(self.db, school.school_id, {
                "name": "Kid B", "class_id": 1, "admission_no": "A202",
            }),
        ]
        self.db.commit()
        self.assertEqual(
            self.db.query(User).filter(User.school_id == school.school_id).count(), 5,
        )
        mock_send.reset_mock()

        schools_repo.delete_school(self.db, school)

        self.assertFalse(self.db.query(School).filter(School.school_id == school.school_id).one().is_active)

        for staff_id in (admin_staff.staff_id, pilot_staff.staff_id):
            self.assertFalse(self.db.query(Staff).filter(Staff.staff_id == staff_id).one().is_active,
                             f"staff {staff_id} should be inactive")
        for student_id in (students[0].student_id, students[1].student_id):
            self.assertFalse(self.db.query(Student).filter(Student.student_id == student_id).one().is_active,
                             f"student {student_id} should be inactive")
        self.assertFalse(self.db.query(Teacher).filter(Teacher.teacher_id == teacher.teacher_id).one().is_active)
        self.assertFalse(self.db.query(Parent).filter(Parent.parent_id == parent.parent_id).one().is_active)
        self.assertFalse(self.db.query(Pilot).filter(Pilot.staff_id == pilot_staff.staff_id).one().is_active)

        # Nobody from the removed school may authenticate anymore.
        self.assertEqual(
            self.db.query(User).filter(User.school_id == school.school_id, User.is_active.is_(True)).count(), 0,
            "all login accounts of a removed school must be inactive",
        )


class PeopleNotificationsTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.Session = make_session()
        self.db: Session = self.Session()
        self.db.add(School(
            name="Sunrise High",
            address="1 Main Rd",
            pincode="110001",
            city="New Delhi",
            state="Delhi",
            primary_contact="9999999999",
            primary_email="office@sunrise.edu",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @mock.patch("common.email.send_email")
    def test_create_staff_emails_the_staff_member(self, mock_send):
        staff = people_repo.create_staff(self.db, 1, {
            "name": "Priya Sharma", "role": "Admin", "phone": "111", "email": "priya@sunrise.edu",
        })
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], ["priya@sunrise.edu"])
        self.assertIn("Sunrise High", args[2])
        self.assertIn("Priya Sharma", args[1])

    @mock.patch("common.email.send_email")
    def test_create_staff_without_email_sends_nothing(self, mock_send):
        people_repo.create_staff(self.db, 1, {"name": "No Mail", "role": "Admin", "phone": "222"})
        mock_send.assert_not_called()

    @mock.patch("common.email.send_email")
    def test_delete_staff_emails_the_staff_member(self, mock_send):
        staff = people_repo.create_staff(self.db, 1, {
            "name": "Ravi Kumar", "role": "Admin", "phone": "333", "email": "ravi@sunrise.edu",
        })
        mock_send.reset_mock()
        people_repo.delete_staff(self.db, staff)
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], ["ravi@sunrise.edu"])
        self.assertIn("Ravi Kumar", args[1])
        self.assertFalse(self.db.query(Staff).filter(Staff.staff_id == staff.staff_id).one().is_active)

    @mock.patch("common.email.send_email")
    def test_delete_student_emails_the_parent(self, mock_send):
        parent = Parent(parent_id=10, school_id=1, name="Parent One", phone="444", email="parent@sunrise.edu")
        self.db.add(parent)
        self.db.flush()
        student = people_repo.create_student(self.db, 1, {
            "name": "Kid One", "class_id": 1, "admission_no": "A10",
        })
        people_repo.link_parent_student(self.db, parent.parent_id, student.student_id)
        mock_send.reset_mock()

        people_repo.delete_student(self.db, student)
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], ["parent@sunrise.edu"])
        self.assertIn("Kid One", args[2])
        self.assertFalse(self.db.query(Student).filter(Student.student_id == student.student_id).one().is_active)

    @mock.patch("common.email.send_email")
    def test_delete_student_without_parent_sends_nothing(self, mock_send):
        student = people_repo.create_student(self.db, 1, {
            "name": "Solo Kid", "class_id": 1, "admission_no": "A11",
        })
        mock_send.reset_mock()
        people_repo.delete_student(self.db, student)
        mock_send.assert_not_called()

    @mock.patch("common.email.send_email")
    def test_student_added_helper_used_by_router(self, mock_send):
        email_helpers.send_student_added_email("Sunrise High", "Kid Two", ["parent@sunrise.edu"])
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertIn("Kid Two", args[1])
        self.assertIn("Sunrise High", args[2])


class RecordUpdateNotificationTests(unittest.TestCase):
    """Editing a student / staff / teacher / school-admin record must email the
    affected person(s) a diff of what changed (field, old value, new value)."""

    def setUp(self):
        self.engine, self.Session = make_session()
        self.db: Session = self.Session()
        self.db.add(School(
            name="Sunrise High",
            address="1 Main Rd",
            pincode="110001",
            city="New Delhi",
            state="Delhi",
            primary_contact="9999999999",
            primary_email="office@sunrise.edu",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _student_with_parent(self):
        parent = Parent(parent_id=10, school_id=1, name="Parent One", phone="444",
                        email="parent@sunrise.edu")
        self.db.add(parent)
        self.db.flush()
        student = people_repo.create_student(self.db, 1, {
            "name": "Kid One", "class_id": 1, "admission_no": "A10",
        })
        people_repo.link_parent_student(self.db, parent.parent_id, student.student_id)
        return student

    @mock.patch("common.email.send_email")
    def test_student_email_update_notifies_old_and_new_parent(self, mock_send):
        student = self._student_with_parent()
        mock_send.reset_mock()
        people_repo.update_student_with_changes(
            self.db, 1, student, {}, None, {"email": "new.parent@sunrise.edu"},
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(sorted(args[0]), ["new.parent@sunrise.edu", "parent@sunrise.edu"])
        self.assertIn("Kid One", args[1])
        self.assertIn("Email", args[2])
        self.assertIn("parent@sunrise.edu", args[2])
        self.assertIn("new.parent@sunrise.edu", args[2])
        self.assertIn("→", args[2])

    @mock.patch("common.email.send_email")
    def test_student_field_update_emails_linked_parent(self, mock_send):
        student = self._student_with_parent()
        mock_send.reset_mock()
        people_repo.update_student_with_changes(
            self.db, 1, student, {"date_of_birth": date(2014, 5, 20)}, None, {},
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], ["parent@sunrise.edu"])
        self.assertIn("Date of birth", args[2])
        self.assertIn("2014-05-20", args[2])

    @mock.patch("common.email.send_email")
    def test_student_update_without_changes_sends_nothing(self, mock_send):
        student = self._student_with_parent()
        mock_send.reset_mock()
        people_repo.update_student_with_changes(
            self.db, 1, student, {"name": "Kid One"}, None, {"email": "parent@sunrise.edu"},
        )
        mock_send.assert_not_called()

    @mock.patch("common.email.send_email")
    def test_staff_update_emails_staff_member_with_diff(self, mock_send):
        staff = people_repo.create_staff(self.db, 1, {
            "name": "Priya Sharma", "role": "Admin", "phone": "111", "email": "priya@sunrise.edu",
        })
        mock_send.reset_mock()
        people_repo.update_staff(self.db, staff, {"phone": "9991112222"})
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], ["priya@sunrise.edu"])
        self.assertIn("Priya Sharma", args[1])
        self.assertIn("Phone", args[2])
        self.assertIn("9991112222", args[2])

    @mock.patch("common.email.send_email")
    def test_staff_email_update_notifies_old_and_new(self, mock_send):
        staff = people_repo.create_staff(self.db, 1, {
            "name": "Priya Sharma", "role": "Admin", "phone": "111", "email": "priya@sunrise.edu",
        })
        mock_send.reset_mock()
        people_repo.update_staff(self.db, staff, {"email": "priya.new@sunrise.edu"})
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(sorted(args[0]), ["priya.new@sunrise.edu", "priya@sunrise.edu"])
        self.assertIn("priya@sunrise.edu → priya.new@sunrise.edu", args[2])

    @mock.patch("common.email.send_email")
    def test_teacher_update_emails_teacher(self, mock_send):
        teacher = Teacher(school_id=1, name="Meera Nair", role_title="Math",
                          phone="123", email="meera@sunrise.edu")
        self.db.add(teacher)
        self.db.flush()
        mock_send.reset_mock()
        people_repo.update_teacher(self.db, teacher, {"phone": "5550001111"})
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], ["meera@sunrise.edu"])
        self.assertIn("Meera Nair", args[1])
        self.assertIn("Phone", args[2])
        self.assertIn("5550001111", args[2])

    @mock.patch("common.email.send_email")
    def test_school_admin_email_update_notifies_old_and_new(self, mock_send):
        school = self.db.query(School).one()
        mock_send.reset_mock()
        schools_repo.update_school(self.db, school, {"primary_email": "office.new@sunrise.edu"})
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(sorted(args[0]), ["office.new@sunrise.edu", "office@sunrise.edu"])
        self.assertIn("Primary email", args[2])
        self.assertIn("office@sunrise.edu → office.new@sunrise.edu", args[2])

    @mock.patch("common.email.send_email")
    def test_school_update_without_email_change_sends_nothing(self, mock_send):
        school = self.db.query(School).one()
        mock_send.reset_mock()
        schools_repo.update_school(self.db, school, {"address": "99 Green Lane"})
        mock_send.assert_not_called()

    @mock.patch("common.email.send_email")
    def test_send_failure_is_swallowed_for_update_email(self, mock_send):
        mock_send.side_effect = RuntimeError("SMTP down")
        sent = email_helpers.send_record_updated_email(
            "Student", "Kid One", "Sunrise High",
            [("Email", "a@x.org", "b@x.org")], ["a@x.org", "b@x.org"],
        )
        self.assertFalse(sent)
        mock_send.assert_called_once()

    @mock.patch("common.email.send_email")
    def test_update_email_dedupes_recipients(self, mock_send):
        email_helpers.send_record_updated_email(
            "Student", "Kid One", "Sunrise High",
            [("Email", "a@x.org", "b@x.org")], [" b@x.org ", "b@x.org"],
        )
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], ["b@x.org"])


if __name__ == "__main__":
    unittest.main()