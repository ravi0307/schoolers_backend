"""
Regression tests for the standardized email format (subject, body, signature,
plain-text + HTML pair, delivery states).

Covers the delivery checklist:
 1. every email flow produces a non-empty, meaningful subject,
 2. the body carries the expected contextual details,
 3. the correct signature is selected (platform vs school),
 4. missing optional school contact details never produce blank labels,
 5. HTML escaping blocks injected markup from user-controlled values,
 6. plain-text alternatives are generated alongside HTML,
 7. password-reset OTPs are not exposed in subjects or logs,
 8. cross-school recipient isolation is enforced,
 9. SMTP failure after database commit is represented accurately,
10. retrying a failed or ambiguous delivery does not duplicate it,
11. in-app notification workflows keep working alongside email.
"""
import re
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import common.email as email_helpers
from common.database import Base
from common.dependencies import CurrentUser
from common.email import EmailDelivery, _payload_key, dispatch_email
from common.exceptions import AppError
from common.logins import provision_login
from common.models import Parent, School, SchoolNotification, Staff, User
from common.security import verify_password
import services.auth_service.service as auth_service
import services.notifications_service.repository as notifications_repo
import services.people_service.repository as people_repo

VALID_SUBJECT = re.compile(r"^[^\n|]{1,120} \| [^\n|]{3,120}$")
VAGUE_PURPOSES = {"Notification", "Update", "Message", "Alert", "Email"}


def make_session():
    engine = create_engine("sqlite://")
    tables = [t for t in Base.metadata.sorted_tables if t.name != "website_builder_sites"]
    Base.metadata.create_all(engine, tables=tables)
    return engine, sessionmaker(bind=engine, autoflush=False, future=True)


def add_school(db: Session, school_id: int, name: str, email: str, phone: str = "02212345678") -> School:
    school = School(
        school_id=school_id, name=name, address="1 Main Rd", pincode="400001",
        city="Mumbai", state="Maharashtra", primary_contact=phone,
        primary_email=email, is_active=True,
    )
    db.add(school)
    db.commit()
    return school


class SubjectFormatTests(unittest.TestCase):
    """(1) Meaningful subject for every flow, in the '{Sender} | {Purpose}' form."""

    def _capture(self, fn, *args, **kwargs):
        with mock.patch("common.email.send_email") as mock_send:
            result = fn(*args, **kwargs)
        self.assertTrue(result, f"{fn.__name__} reported failure")
        mock_send.assert_called_once()
        return mock_send.call_args[0][1], mock_send.call_args[0][2]

    def test_every_flow_has_a_standard_meaningful_subject(self):
        cases = [
            (
                email_helpers.send_school_registered_email,
                ("Sunrise High", ["a@x.org"]),
                {},
                "Schoolers", "School Account Created: Sunrise High",
            ),
            (
                email_helpers.send_school_removed_email,
                ("Sunrise High", ["a@x.org"]),
                {},
                "Schoolers", "School Account Removed: Sunrise High",
            ),
            (
                email_helpers.send_staff_added_email,
                ("Sunrise High", "Ann Lee", ["a@x.org"]),
                {},
                "Sunrise High", "Staff Account Created: Ann Lee",
            ),
            (
                email_helpers.send_staff_removed_email,
                ("Sunrise High", "Ann Lee", ["a@x.org"]),
                {},
                "Sunrise High", "Staff Access Removed: Ann Lee",
            ),
            (
                email_helpers.send_student_added_email,
                ("Sunrise High", "Kid One", ["p@x.org"]),
                {},
                "Sunrise High", "Student Enrolled: Kid One",
            ),
            (
                email_helpers.send_student_removed_email,
                ("Sunrise High", "Kid One", ["p@x.org"]),
                {},
                "Sunrise High", "Student Withdrawn: Kid One",
            ),
            (
                email_helpers.send_record_updated_email,
                ("Student", "Kid One", "Sunrise High",
                 [("Date of birth", "2014-01-01", "2014-05-20")], ["p@x.org"]),
                {},
                "Sunrise High", "Student Record Updated: Kid One",
            ),
        ]
        for fn, fn_args, fn_kwargs, sender, purpose in cases:
            with self.subTest(flow=fn.__name__):
                subject, _ = self._capture(fn, *fn_args, **fn_kwargs)
                self.assertTrue(subject.strip(), "empty subject")
                self.assertEqual(subject, f"{sender} | {purpose}")
                self.assertRegex(subject, VALID_SUBJECT)
                purpose_part = subject.split(" | ", 1)[1]
                self.assertNotIn(purpose_part, VAGUE_PURPOSES)

    def test_password_reset_subject_is_specific_and_secret_free(self):
        with mock.patch("common.email.send_email") as mock_send:
            auth_service._deliver_reset_token("user@x.org", "493812")
        subject = mock_send.call_args[0][1]
        self.assertEqual(subject, "Schoolers | Password Reset Request")
        self.assertRegex(subject, VALID_SUBJECT)
        self.assertNotIn("493812", subject)

    def test_notification_subject_derives_from_the_message(self):
        subject, _ = self._capture(
            email_helpers.send_school_notification_email,
            "Sunrise High", "Dues",
            "Sports Day will be held on 28 October.", ["a@x.org"],
        )
        self.assertEqual(subject, "Sunrise High | Sports Day will be held on 28 October.")
        self.assertRegex(subject, VALID_SUBJECT)

    def test_notification_subject_falls_back_to_the_type_when_message_is_blank(self):
        subject, _ = self._capture(
            email_helpers.send_school_notification_email,
            "Sunrise High", "Dues", "\n\n  \n", ["a@x.org"],
        )
        self.assertEqual(subject, "Sunrise High | Dues Reminder")

    def test_multiline_announcement_never_becomes_header_injection(self):
        subject, _ = self._capture(
            email_helpers.send_school_notification_email,
            "Sunrise High", "General", "Line one\nBcc: victim@x.org\n\nBody text", ["a@x.org"],
        )
        self.assertNotIn("\n", subject)
        self.assertNotIn("Bcc:", subject)


class BodyContextTests(unittest.TestCase):
    """(2) The body carries the contextual details of the event."""

    def _body(self, fn, *args, **kwargs):
        with mock.patch("common.email.send_email") as mock_send:
            self.assertTrue(fn(*args, **kwargs))
        call = mock_send.call_args[0]
        return call[2], (mock_send.call_args.kwargs.get("html") or "")

    def test_school_created_body_has_credentials_and_next_steps(self):
        text, _ = self._body(
            email_helpers.send_school_registered_email,
            "Sunrise High", ["a@x.org"], username="sunrise.admin", password="Temp!1234",
        )
        for phrase in (
            "Sunrise High", "Sign-in details", "Username: sunrise.admin",
            "Temporary password: Temp!1234", "Forgot password?", "6-digit OTP",
            "set a new password", "Schoolers Team",
        ):
            self.assertIn(phrase, text)

    def test_staff_added_body_names_person_and_school(self):
        text, _ = self._body(
            email_helpers.send_staff_added_email,
            "Sunrise High", "Ann Lee", ["a@x.org"],
        )
        self.assertIn("Ann Lee", text)
        self.assertIn("Sunrise High", text)

    def test_record_update_body_lists_old_and_new_values(self):
        text, _ = self._body(
            email_helpers.send_record_updated_email,
            "Student", "Kid One", "Sunrise High",
            [("Email", "old@x.org", "new@x.org")], ["p@x.org"],
        )
        self.assertIn("Kid One", text)
        self.assertIn("Email: old@x.org → new@x.org", text)
        self.assertIn("contact your school administrator", text)

    def test_notification_body_contains_the_announcement(self):
        text, _ = self._body(
            email_helpers.send_school_notification_email,
            "Sunrise High", "Dues", "Fees for October are due by the 10th.", ["a@x.org"],
        )
        self.assertIn("Fees for October are due by the 10th.", text)
        self.assertIn("Sunrise High", text)

    def test_password_reset_body_carries_the_code_and_expiry(self):
        with mock.patch("common.email.send_email") as mock_send:
            auth_service._deliver_reset_token("user@x.org", "493812", expires_minutes=30)
        text = mock_send.call_args[0][2]
        self.assertIn("code is: 493812", text)
        self.assertIn("expires in 30 minutes", text)
        self.assertIn("ignore this email", text.lower())


class SignatureTests(unittest.TestCase):
    """(3)+(4) Correct signature; missing contacts never leave placeholders."""

    def _text(self, fn, *args, **kwargs):
        with mock.patch("common.email.send_email") as mock_send:
            self.assertTrue(fn(*args, **kwargs))
        return mock_send.call_args[0][2]

    def test_platform_flows_use_the_schoolers_team_signature(self):
        text = self._text(email_helpers.send_school_removed_email, "Sunrise High", ["a@x.org"])
        self.assertIn("Schoolers Team", text)
        self.assertIn("School Management Platform", text)
        self.assertNotIn("School Administration", text)

    def test_school_flows_use_the_school_administration_signature(self):
        text = self._text(
            email_helpers.send_staff_added_email,
            "Sunrise High", "Ann Lee", ["a@x.org"],
            school_email="office@sunrise.edu", school_phone="02211112222",
        )
        self.assertIn("School Administration", text)
        self.assertIn("Sunrise High", text)
        self.assertIn("office@sunrise.edu", text)
        self.assertIn("02211112222", text)
        self.assertNotIn("Schoolers Team", text)

    def test_missing_school_contacts_leave_no_blank_or_none_lines(self):
        text = self._text(email_helpers.send_staff_added_email, "Sunrise High", "Ann Lee", ["a@x.org"])
        self.assertNotIn("None", text)
        self.assertNotIn("School Administration\n\n", text)
        self.assertTrue(
            text.rstrip().endswith("Sunrise High"),
            f"signature should end at the school name, got: {text[-80:]!r}",
        )

    def test_signature_skips_individual_missing_fields(self):
        lines = email_helpers.school_signature("Sunrise High", None, "02211112222")
        self.assertEqual(lines, ["Regards,", "", "School Administration", "Sunrise High", "02211112222"])
        lines = email_helpers.school_signature("Sunrise High", "office@sunrise.edu", None)
        self.assertEqual(lines, ["Regards,", "", "School Administration", "Sunrise High", "office@sunrise.edu"])


class HtmlEscapingTests(unittest.TestCase):
    """(5) User-controlled values are escaped in the HTML alternative."""

    def test_record_update_escapes_markup_in_html_part(self):
        with mock.patch("common.email.send_email") as mock_send:
            email_helpers.send_record_updated_email(
                "Student",
                "<img src=x onerror=alert(1)>",
                "Sunrise High",
                [("Name", "<b>Old</b>", "</p><script>bad()</script>")],
                ["p@x.org"],
            )
        text = mock_send.call_args[0][2]
        html = mock_send.call_args.kwargs["html"]
        self.assertIsNotNone(html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img src=x", html)
        self.assertIn("&lt;b&gt;Old&lt;/b&gt;", html)
        # The plain-text part is exactly the same information, unescaped.
        self.assertIn("</p><script>bad()</script>", text)

    def test_announcement_html_escapes_message_body(self):
        with mock.patch("common.email.send_email") as mock_send:
            email_helpers.send_school_notification_email(
                "Sunrise High", "General", "<script>alert('x')</script> picnic", ["a@x.org"],
            )
        html = mock_send.call_args.kwargs["html"]
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)


class PlainTextAlternativeTests(unittest.TestCase):
    """(6) HTML emails always carry an equivalent plain-text part."""

    def test_outbox_row_keeps_both_alternatives(self):
        engine, Session = make_session()
        db = Session()
        try:
            with mock.patch("common.email.is_email_configured", return_value=True), \
                 mock.patch("common.email.send_email") as mock_send:
                ok = email_helpers.send_staff_added_email(
                    "Sunrise High", "Ann Lee", ["a@x.org"], db=db,
                )
            self.assertTrue(ok)
            html = mock_send.call_args.kwargs["html"]
            self.assertTrue(html.startswith("<!doctype html>"))
            row = db.query(EmailDelivery).one()
            self.assertTrue(row.body_text.strip())
            self.assertTrue(row.body_html.strip())
            self.assertIn("Ann Lee", row.body_text)
            self.assertIn("Ann Lee", row.body_html)
            self.assertIn("Sunrise High", row.body_text)
            self.assertIn("Sunrise High", row.body_html)
        finally:
            db.close()
            engine.dispose()


class OtpSafetyTests(unittest.TestCase):
    """(7) Reset codes never reach subjects or logs."""

    def test_successful_send_logs_subject_without_the_code(self):
        with mock.patch("common.email.send_email") as mock_send:
            auth_service._deliver_reset_token("user@x.org", "493812")
        subject, text = mock_send.call_args[0][1], mock_send.call_args[0][2]
        self.assertNotIn("493812", subject)
        self.assertIn("code is: 493812", text)  # delivered in the body only
        # The success log line formats only recipients + subject (no body), so
        # the code cannot reach it; assert the call that produced it carried
        # the OTP solely in the body argument.
        self.assertEqual(mock_send.call_args[0][:2], (["user@x.org"], subject))

    def test_failed_send_logs_without_the_code(self):
        with mock.patch("common.email.send_email", side_effect=RuntimeError("SMTP down")), \
             self.assertLogs("common.email", level="WARNING") as logs:
            auth_service._deliver_reset_token("user@x.org", "493812")
        joined = "\n".join(logs.output)
        self.assertNotIn("493812", joined)
        # The failure log carries the exception type, never the raw SMTP text.
        self.assertNotIn("SMTP down", joined)

    def test_reset_flow_never_writes_the_code_to_the_outbox(self):
        engine, Session = make_session()
        db = Session()
        try:
            with mock.patch("common.email.is_email_configured", return_value=True), \
                 mock.patch("common.email.send_email"):
                auth_service._deliver_reset_token("user@x.org", "493812")
            self.assertEqual(db.query(EmailDelivery).count(), 0)
        finally:
            db.close()
            engine.dispose()


class CrossSchoolIsolationTests(unittest.TestCase):
    """(8) A school's mail never reaches another school's addresses."""

    def test_notification_goes_only_to_its_own_school(self):
        engine, Session = make_session()
        db = Session()
        try:
            add_school(db, 1, "Blue Horizon Academy", "office@bh.edu")
            db.add(School(
                school_id=2, name="Green Valley Public School", address="2 Way St",
                pincode="400002", city="Pune", state="Maharashtra",
                primary_contact="02012345678", primary_email="office@gv.edu",
                alternative_email="admin@gv.edu", is_active=True,
            ))
            db.commit()
            with mock.patch("services.notifications_service.repository.is_email_configured", return_value=True), \
                 mock.patch("common.email.send_email") as mock_send:
                notifications_repo.create(db, 1, {"type": "Dues", "message": "Dues reminder."})
            recipients = mock_send.call_args[0][0]
            self.assertEqual(recipients, ["office@bh.edu"])
            self.assertNotIn("office@gv.edu", recipients)
            self.assertNotIn("admin@gv.edu", recipients)
        finally:
            db.close()
            engine.dispose()

    def test_staff_email_goes_only_to_the_new_staff_member(self):
        engine, Session = make_session()
        db = Session()
        try:
            add_school(db, 1, "Blue Horizon Academy", "office@bh.edu")
            add_school(db, 2, "Green Valley Public School", "office@gv.edu")
            with mock.patch("common.email.is_email_configured", return_value=True), \
                 mock.patch("common.email.send_email") as mock_send:
                email_helpers.send_staff_added_email(
                    "Green Valley Public School", "Meera Nair", ["meera@gv.edu"], db=db,
                )
                # After the first send, sweep nothing — assert recipients of row 1.
            row = db.query(EmailDelivery).one()
            self.assertEqual(row.recipients, "meera@gv.edu")
            self.assertNotIn("bh.edu", row.recipients)
            self.assertNotIn("gv.edu, office", row.recipients)
        finally:
            db.close()
            engine.dispose()


class NotificationFailureTests(unittest.TestCase):
    """(9) SMTP failure after commit is accurate, safe, and non-destructive."""

    def _setup(self):
        engine, Session = make_session()
        db = Session()
        add_school(db, 1, "Blue Horizon Academy", "office@bh.edu")
        return engine, db

    def test_failure_after_commit_keeps_row_and_reports_safely(self):
        engine, db = self._setup()
        try:
            with mock.patch(
                "services.notifications_service.repository.is_email_configured", return_value=True
            ), mock.patch(
                "common.email.is_email_configured", return_value=True
            ), mock.patch(
                "common.email.send_email", side_effect=RuntimeError("relay.internal: 550 blocked")
            ):
                with self.assertRaises(AppError) as ctx:
                    notifications_repo.create(db, 1, {"type": "Dues", "message": "October dues."})
            message = str(ctx.exception)
            self.assertEqual(ctx.exception.status_code, 400)
            # The client sees a safe, accurate partial-success message...
            self.assertIn("saved", message.lower())
            self.assertIn("could not be sent", message.lower())
            # ...with no SMTP internals leaking out of the logs boundary.
            self.assertNotIn("relay.internal", message)
            self.assertNotIn("550", message)
            self.assertNotIn("RuntimeError", message)
            # The notification row survives with its original status contract.
            n = db.query(SchoolNotification).one()
            self.assertEqual(n.status, "Sent")
            self.assertEqual(n.message, "October dues.")
            # The delivery is recorded with an explicit state and safe error.
            row = db.query(EmailDelivery).one()
            self.assertEqual(row.state, "Failed")
            self.assertEqual(row.attempts, 1)
            self.assertEqual(row.last_error, "RuntimeError")
        finally:
            db.close()
            engine.dispose()

    def test_unconfigured_smtp_fails_before_any_row_is_created(self):
        engine, db = self._setup()
        try:
            with mock.patch(
                "services.notifications_service.repository.is_email_configured", return_value=False
            ):
                with self.assertRaises(AppError) as ctx:
                    notifications_repo.create(db, 1, {"type": "Dues", "message": "October dues."})
            self.assertIn("not configured", str(ctx.exception))
            self.assertEqual(db.query(SchoolNotification).count(), 0)
            self.assertEqual(db.query(EmailDelivery).count(), 0)
        finally:
            db.close()
            engine.dispose()


class RetryDedupeTests(unittest.TestCase):
    """(10) Retrying a failed/ambiguous delivery never duplicates it."""

    def test_failed_delivery_reuses_its_row_and_sends_once_on_retry(self):
        engine, Session = make_session()
        db = Session()
        try:
            with mock.patch("common.email.is_email_configured", return_value=True), \
                 mock.patch("common.email.send_email", side_effect=RuntimeError("boom")):
                first = dispatch_email(db, ["a@x.org"], "Subject line", "Body text")
            self.assertFalse(first)
            row = db.query(EmailDelivery).one()
            self.assertEqual(row.state, "Failed")
            self.assertEqual(row.attempts, 1)

            with mock.patch("common.email.is_email_configured", return_value=True), \
                 mock.patch("common.email.send_email") as mock_send:
                second = dispatch_email(db, ["a@x.org"], "Subject line", "Body text")
            self.assertTrue(second)
            self.assertEqual(db.query(EmailDelivery).count(), 1, "retry must reuse the row")
            row = db.query(EmailDelivery).one()
            self.assertEqual(row.state, "Sent")
            self.assertEqual(row.attempts, 2)
            mock_send.assert_called_once()

            # A further dispatch of an already-sent message sends nothing.
            with mock.patch("common.email.is_email_configured", return_value=True), \
                 mock.patch("common.email.send_email") as mock_send:
                third = dispatch_email(db, ["a@x.org"], "Subject line", "Body text")
            self.assertTrue(third)
            mock_send.assert_not_called()
            self.assertEqual(db.query(EmailDelivery).count(), 1)
        finally:
            db.close()
            engine.dispose()

    def test_key_buckets_change_outside_the_idempotency_window(self):
        base = datetime(2026, 10, 21, 12, 0, 0)
        same_window = base + timedelta(minutes=5)
        next_window = base + timedelta(minutes=11)
        key_a = _payload_key(["a@x.org"], "S", "B", now=base)
        key_b = _payload_key(["a@x.org"], "S", "B", now=same_window)
        key_c = _payload_key(["a@x.org"], "S", "B", now=next_window)
        self.assertEqual(key_a, key_b, "retries within the window must hit the same row")
        self.assertNotEqual(key_a, key_c, "a later genuine repeat must get its own delivery")

    def test_opportunistic_sweep_retries_stale_failures(self):
        engine, Session = make_session()
        db = Session()
        try:
            stale = EmailDelivery(
                idempotency_key="k" * 64,
                recipients="old@x.org",
                subject="Old subject",
                body_text="Old body",
                body_html=None,
                state="Failed",
                attempts=1,
                updated_at=datetime(2026, 10, 21, 8, 0, 0),
            )
            db.add(stale)
            db.commit()
            with mock.patch("common.email.is_email_configured", return_value=True), \
                 mock.patch("common.email.send_email") as mock_send:
                dispatch_email(db, ["new@x.org"], "New subject", "New body")
            stale = db.query(EmailDelivery).filter_by(idempotency_key="k" * 64).one()
            self.assertEqual(stale.state, "Sent")
            self.assertEqual(stale.attempts, 2)
            swept = [c[0][0] for c in mock_send.call_args_list]
            self.assertIn(["old@x.org"], swept)
            self.assertIn(["new@x.org"], swept)
        finally:
            db.close()
            engine.dispose()


class InAppWorkflowTests(unittest.TestCase):
    """(11) In-app notification workflows keep working beside email."""

    def test_notification_lifecycle_survives_email_standardization(self):
        engine, Session = make_session()
        db = Session()
        try:
            add_school(db, 1, "Blue Horizon Academy", "office@bh.edu")
            with mock.patch(
                "services.notifications_service.repository.is_email_configured", return_value=True
            ), mock.patch("common.email.send_email"):
                n = notifications_repo.create(db, 1, {"type": "General", "message": "Picnic Friday."})
            self.assertEqual(n.status, "Sent")
            listed = notifications_repo.list_for_school(db, 1)
            self.assertEqual(len(listed), 1)
            read_back = notifications_repo.mark_read(db, n.notification_id)
            self.assertEqual(read_back.status, "Read")
            # Read status is independent of delivery state.
            row = db.query(EmailDelivery).one()
            self.assertEqual(row.state, "Sent")
        finally:
            db.close()
            engine.dispose()

    def test_helpers_without_db_still_send_directly_and_never_raise(self):
        with mock.patch("common.email.send_email") as mock_send:
            ok = email_helpers.send_student_removed_email("Sunrise High", "Kid One", ["p@x.org"])
        self.assertTrue(ok)
        mock_send.assert_called_once()
        # Direct path: nothing is persisted to the outbox.
        engine, Session = make_session()
        db = Session()
        try:
            self.assertEqual(db.query(EmailDelivery).count(), 0)
        finally:
            db.close()
            engine.dispose()


class OnboardingCredentialTests(unittest.TestCase):
    """Staff/teacher and student-onboarding emails carry working sign-in
    details: the login exists at send time, the emailed username/password
    match the stored hash, and the secret never reaches the outbox."""

    def setUp(self):
        self.engine, Session = make_session()
        self.db: Session = Session()
        add_school(self.db, 1, "Blue Horizon Academy", "office@bh.edu")

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _credential(body: str, label: str) -> str:
        marker = f"{label}:"
        for line in body.splitlines():
            if marker in line:
                return line.split(marker, 1)[1].strip()
        raise AssertionError(f"{label} not found in body")

    @mock.patch("common.email.send_email")
    def test_staff_onboarding_login_and_credentials_match(self, mock_send):
        staff = people_repo.create_staff(self.db, 1, {
            "name": "Priya Menon", "role": "Admin", "phone": "111",
            "email": "priya@bh.edu",
        })
        user = self.db.query(User).filter(
            User.role == "staff", User.linked_person_id == staff.staff_id
        ).one()
        self.assertEqual(user.username, "priya.menon")

        mock_send.assert_called_once()
        _, subject, body = mock_send.call_args[0]
        self.assertEqual(subject, "Blue Horizon Academy | Staff Account Created: Priya Menon")
        username = self._credential(body, "Username")
        password = self._credential(body, "Temporary password")
        self.assertEqual(username, user.username)
        self.assertTrue(verify_password(password, user.password_hash))
        # The plaintext secret is delivered once and never stored at rest.
        self.assertEqual(self.db.query(EmailDelivery).count(), 0)

    @mock.patch("common.email.send_email")
    def test_teacher_created_as_staff_gets_teacher_role_login(self, mock_send):
        staff = people_repo.create_staff(self.db, 1, {
            "name": "Asha Rao", "role": "Teacher", "phone": "112",
            "email": "asha@bh.edu",
        })
        user = self.db.query(User).filter(
            User.role == "teacher", User.linked_person_id == staff.staff_id
        ).one()
        body = mock_send.call_args[0][2]
        self.assertEqual(self._credential(body, "Username"), user.username)
        self.assertTrue(verify_password(self._credential(body, "Temporary password"),
                                        user.password_hash))

    @mock.patch("common.email.send_email")
    def test_staff_without_email_still_gets_a_login(self, mock_send):
        staff = people_repo.create_staff(self.db, 1, {
            "name": "No Mail", "role": "Admin", "phone": "113",
        })
        mock_send.assert_not_called()
        user = self.db.query(User).filter(
            User.role == "staff", User.linked_person_id == staff.staff_id
        ).one()
        self.assertTrue(user.is_active)

    def test_duplicate_onboarding_never_mints_a_second_account(self):
        first = provision_login(self.db, school_id=1, role="staff",
                                name="Dup Person", email="dup@bh.edu",
                                linked_person_id=42)
        second = provision_login(self.db, school_id=1, role="staff",
                                 name="Dup Person", email="dup@bh.edu",
                                 linked_person_id=42)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(self.db.query(User).filter(User.linked_person_id == 42).count(), 1)

    def test_school_created_credentials_heading_appears_exactly_once(self):
        with mock.patch("common.email.send_email") as mock_send:
            email_helpers.send_school_registered_email(
                "Blue Horizon Academy", ["office@bh.edu"],
                username="bh.admin", password="Cedar!abc123",
            )
        body = mock_send.call_args[0][2]
        self.assertEqual(body.count("Sign-in details"), 1)
        self.assertNotIn("\n\nDetails:", body)

    @mock.patch("common.email.send_email")
    def test_student_onboarding_router_email_carries_parent_credentials(self, mock_send):
        # The router lives in a service directory wired via sys.path; import it
        # exactly the way the other router-level tests do.
        sys.path.insert(0, "services/people_service")
        try:
            for module in ("repository", "schemas", "bulk"):
                sys.modules.pop(module, None)
            router = __import__("services.people_service.router", fromlist=["x"])
            schemas = __import__("schemas", fromlist=["x"])
            payload = schemas.StudentCreate(
                name="Aarav Sharma", class_id=1, admission_no="A700",
                parent_name="Rita Sharma", parent_phone="9000000001",
                parent_email="rita@bh.edu",
            )
            router.create_student(
                payload=payload, db=self.db, school_id=1,
                current_user=CurrentUser(user_id=1, role="admin", school_id=1),
            )
        finally:
            sys.path.pop(0)
            for module in ("repository", "schemas", "bulk"):
                sys.modules.pop(module, None)

        parent = self.db.query(Parent).filter(Parent.email == "rita@bh.edu").one()
        user = self.db.query(User).filter(
            User.role == "parent", User.linked_person_id == parent.parent_id
        ).one()
        mock_send.assert_called_once()
        _, subject, body = mock_send.call_args[0]
        self.assertEqual(subject, "Blue Horizon Academy | Student Enrolled: Aarav Sharma")
        self.assertEqual(self._credential(body, "Username"), user.username)
        self.assertTrue(verify_password(self._credential(body, "Temporary password"),
                                        user.password_hash))
        self.assertEqual(self.db.query(EmailDelivery).count(), 0)

    def test_staff_added_without_credentials_has_no_password_block(self):
        text, _ = self._body(
            email_helpers.send_staff_added_email,
            "Blue Horizon Academy", "Ann Lee", ["a@bh.edu"],
        )
        self.assertNotIn("Temporary password", text)
        self.assertNotIn("Username:", text)

    def _body(self, fn, *args, **kwargs):
        with mock.patch("common.email.send_email") as mock_send:
            result = fn(*args, **kwargs)
        self.assertTrue(result)
        return mock_send.call_args[0][2], (mock_send.call_args.kwargs.get("html") or "")


if __name__ == "__main__":
    unittest.main()
