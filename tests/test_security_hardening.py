"""
Regression tests for the security fixes raised on the PR review:

- bcrypt's 72-byte limit is enforced for multibyte passwords everywhere a
  password can be set (so a Unicode password can't reach `hash_password`).
- Password reset requires a one-time token, not just a known identifier, and
  unknown identifiers are indistinguishable from known ones at the service.
- Broadcast senders are server-derived (no impersonation) and each role can
  only address its allowed audiences.
- A parent without a linked person record only sees school-wide broadcasts.
- Class-scoped reads/writes are bound to the caller's school.
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import ForbiddenError, UnauthorizedError
from common.models import Base, Broadcast, Parent, School, SchoolClass, Staff, Teacher, User, Pilot
from common.security import hash_password, validate_password_byte_length, verify_password
from services.auth_service.schemas import ForgotPasswordResetRequest
from services.transport_service.schemas import PilotCreate, PilotUpdate
import services.auth_service.service as auth_service
import services.communication_service.repository as comm_repo
import services.marks_service.repository as marks_repo
import services.attendance_service.repository as attendance_repo

EMOJI = "\U0001F600"  # 4 UTF-8 bytes each


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_ENGINES = []


def make_session(tables):
    engine = create_engine("sqlite://")
    _ENGINES.append(engine)
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in tables])
    return sessionmaker(bind=engine, autoflush=False, future=True)


def tearDownModule():
    for engine in _ENGINES:
        engine.dispose()


class PasswordByteLengthTests(unittest.TestCase):
    def test_ascii_password_at_limit_is_accepted(self):
        validate_password_byte_length("a" * 72)

    def test_multibyte_password_over_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_password_byte_length(EMOJI * 19)  # 76 bytes
        with self.assertRaises(ValueError):
            validate_password_byte_length("é" * 37)  # 74 bytes

    def test_hash_password_handles_multibyte_within_limit(self):
        plain = EMOJI * 18  # exactly 72 bytes
        hashed = hash_password(plain)
        self.assertTrue(verify_password(plain, hashed))

    def test_hash_password_rejects_multibyte_over_limit(self):
        with self.assertRaises(ValueError):
            hash_password(EMOJI * 40)

    def test_reset_schema_rejects_over_limit_multibyte_password(self):
        with self.assertRaises(ValidationError):
            ForgotPasswordResetRequest(identifier="a", reset_token="tok-12345", new_password=EMOJI * 19)

    def test_reset_schema_accepts_valid_password(self):
        payload = ForgotPasswordResetRequest(
            identifier="a", reset_token="tok-12345", new_password="supersecret"
        )
        self.assertEqual(payload.new_password, "supersecret")

    def test_reset_schema_requires_reset_token(self):
        with self.assertRaises(ValidationError):
            ForgotPasswordResetRequest(identifier="a", new_password="supersecret")

    def test_pilot_schemas_reject_over_limit_multibyte_password(self):
        with self.assertRaises(ValidationError):
            PilotCreate(username="p", password=EMOJI * 19, full_name="P", phone="1")
        with self.assertRaises(ValidationError):
            PilotUpdate(password=EMOJI * 19)

    def test_pilot_update_allows_omitted_password(self):
        self.assertIsNone(PilotUpdate(password=None).password)


class PasswordResetTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Session = make_session(["users", "schools", "teachers", "staff", "parents", "pilots"])

    def setUp(self):
        self.db: Session = self.Session()
        self.db.query(User).delete()
        self.db.commit()
        self.user = User(
            user_id=1, role="admin", username="admin1",
            password_hash=hash_password("oldpassword"),
        )
        self.db.add(self.user)
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_unknown_identifier_returns_no_token(self):
        self.assertEqual(auth_service.forgot_password_request(self.db, "nobody"), (None, None))

    def test_known_identifier_issues_token(self):
        raw, expires_at = auth_service.forgot_password_request(self.db, "admin1")
        self.assertTrue(raw)
        self.assertGreater(expires_at, utcnow_naive())

    def test_reset_without_valid_token_is_rejected(self):
        auth_service.forgot_password_request(self.db, "admin1")
        with self.assertRaises(UnauthorizedError):
            auth_service.forgot_password_reset(self.db, "admin1", "wrong-token", "newpassword")

    def test_reset_with_unknown_identifier_is_rejected(self):
        with self.assertRaises(UnauthorizedError):
            auth_service.forgot_password_reset(self.db, "nobody", "whatever-token", "newpassword")

    def test_reset_with_valid_token_changes_password_and_consumes_token(self):
        raw, _ = auth_service.forgot_password_request(self.db, "admin1")
        auth_service.forgot_password_reset(self.db, "admin1", raw, "newpassword")
        self.user = self.db.query(User).filter(User.user_id == 1).one()
        self.assertTrue(verify_password("newpassword", self.user.password_hash))
        self.assertIsNone(self.user.password_reset_token)
        # A consumed token cannot be replayed.
        with self.assertRaises(UnauthorizedError):
            auth_service.forgot_password_reset(self.db, "admin1", raw, "anotherpassword")

    def test_expired_token_is_rejected(self):
        raw, _ = auth_service.forgot_password_request(self.db, "admin1")
        self.user = self.db.query(User).filter(User.user_id == 1).one()
        self.user.password_reset_token_expires_at = utcnow_naive() - timedelta(minutes=1)
        self.db.commit()
        with self.assertRaises(UnauthorizedError):
            auth_service.forgot_password_reset(self.db, "admin1", raw, "newpassword")

    def test_admin_linked_staff_email_resolves_reset(self):
        # An admin account links straight to a staff record (role "admin"),
        # not a `staff`-role user, so the email lookup must match too.
        self.db.add(Staff(staff_id=11, school_id=1, name="Admin Two", role="Admin", email="boss@school.org"))
        self.db.add(User(
            user_id=2, role="admin", username="admin2",
            password_hash=hash_password("x"), linked_person_id=11,
        ))
        self.db.commit()
        raw, expires_at = auth_service.forgot_password_request(self.db, "boss@school.org")
        self.assertTrue(raw)
        self.assertGreater(expires_at, utcnow_naive())

    def test_admin_email_reset_does_not_cross_person_tables(self):
        # Person ids only mean something within their own table: an admin whose
        # linked_person_id collides with a *parent* row (not a staff row) must
        # not be reset via that parent's email — the admin's staff lookup
        # simply won't match it.
        self.db.add(Parent(parent_id=7, school_id=1, name="Par Seven", phone="1", email="seven@school.org"))
        self.db.add(User(
            user_id=4, role="admin", username="admin4",
            password_hash=hash_password("x"), linked_person_id=7,
        ))
        self.db.commit()
        self.assertEqual(auth_service.forgot_password_request(self.db, "seven@school.org"), (None, None))

    def test_token_issued_for_known_account_is_emailed_out_of_band(self):
        self.db.add(School(
            school_id=9, name="Sunrise High", address="1 Main Rd", pincode="110001",
            city="New Delhi", state="Delhi", primary_contact="9999999999",
            primary_email="office@sunrise.edu",
        ))
        self.db.add(Staff(staff_id=21, school_id=9, name="Admin Five", role="Admin", email="boss@sunrise.edu"))
        self.db.add(User(
            user_id=5, school_id=9, role="admin", username="admin5",
            password_hash=hash_password("x"), linked_person_id=21,
        ))
        self.db.commit()
        with mock.patch("common.email.send_email") as mock_send:
            raw, expires_at = auth_service.forgot_password_request(self.db, "boss@sunrise.edu")
            self.assertTrue(raw)
            self.assertGreater(expires_at, utcnow_naive())
            mock_send.assert_called_once()
            to_addrs, subject, body = mock_send.call_args[0]
            self.assertEqual(to_addrs, ["office@sunrise.edu"])
            self.assertIn("reset", subject.lower())
            self.assertIn(raw, body)


class BroadcastAuthorizationTests(unittest.TestCase):
    POLICY = comm_repo.ALLOWED_BROADCAST_SCOPES

    def test_scope_policy_restricts_roles(self):
        self.assertEqual(self.POLICY["teacher"], {"school", "class"})
        self.assertEqual(self.POLICY["pilot"], {"school", "route"})
        self.assertEqual(self.POLICY["admin"], {"school", "class", "route", "pilot"})
        self.assertNotIn("pilot", self.POLICY["teacher"])
        self.assertNotIn("class", self.POLICY["pilot"])
        self.assertNotIn("route", self.POLICY["teacher"])

    def test_unlinked_parent_sees_only_school_broadcasts(self):
        Session = make_session(["broadcasts"])
        db: Session = Session()
        now = datetime(2026, 9, 1, 9, 0, 0)
        db.add_all([
            Broadcast(school_id=1, scope="school", role_name="Admin", sender_name="A", message="school", created_at=now),
            Broadcast(school_id=1, class_id=1, scope="class", role_name="Teacher", sender_name="T", message="class", created_at=now),
            Broadcast(school_id=1, route_id=1, scope="route", role_name="Pilot", sender_name="P", message="route", created_at=now),
            Broadcast(school_id=1, scope="pilot", role_name="Admin", sender_name="A", message="pilot", created_at=now),
        ])
        db.commit()
        parent = CurrentUser(user_id=3, role="parent", school_id=1, linked_person_id=None)
        seen = {b.message for b in comm_repo.list_broadcasts(db, 1, parent)}
        self.assertEqual(seen, {"school"})
        db.close()


class BroadcastSenderIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Session = make_session(["teachers", "staff", "pilots"])

    def setUp(self):
        self.db: Session = self.Session()
        for name in ("teachers", "staff", "pilots"):
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()
        self.db.add_all([
            Staff(staff_id=5, school_id=1, name="V. Joshi", role="Admin"),
            Teacher(teacher_id=3, school_id=1, name="T. Eacher", role_title="Teacher", phone="1"),
            Pilot(pilot_id=1, user_id=7, school_id=1, full_name="P. One", phone="1"),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_teacher_identity_is_derived(self):
        user = CurrentUser(user_id=2, role="teacher", school_id=1, linked_person_id=3)
        self.assertEqual(comm_repo.resolve_sender_identity(self.db, user), ("Teacher", "T. Eacher"))

    def test_unlinked_teacher_is_rejected(self):
        user = CurrentUser(user_id=2, role="teacher", school_id=1, linked_person_id=None)
        with self.assertRaises(ForbiddenError):
            comm_repo.resolve_sender_identity(self.db, user)

    def test_pilot_identity_is_derived(self):
        user = CurrentUser(user_id=7, role="pilot", school_id=1)
        self.assertEqual(comm_repo.resolve_sender_identity(self.db, user), ("Pilot", "P. One"))

    def test_admin_identity_uses_staff_record_or_falls_back(self):
        linked = CurrentUser(user_id=9, role="admin", school_id=1, linked_person_id=5)
        self.assertEqual(comm_repo.resolve_sender_identity(self.db, linked), ("Admin", "V. Joshi"))
        unlinked = CurrentUser(user_id=9, role="admin", school_id=1, linked_person_id=None)
        self.assertEqual(comm_repo.resolve_sender_identity(self.db, unlinked), ("Admin", "Admin"))


class ClassSchoolScopingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Session = make_session(["classes"])

    def setUp(self):
        self.db: Session = self.Session()
        self.db.execute(Base.metadata.tables["classes"].delete())
        self.db.commit()
        self.db.add(SchoolClass(class_id=1, school_id=1, name="Class 1"))
        self.db.add(SchoolClass(class_id=2, school_id=2, name="Class 2"))
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_marks_class_scope_helper(self):
        self.assertTrue(marks_repo.class_in_school(self.db, 1, 1))
        self.assertFalse(marks_repo.class_in_school(self.db, 1, 2))
        self.assertFalse(marks_repo.class_in_school(self.db, 999, 1))

    def test_attendance_class_scope_helper(self):
        self.assertTrue(attendance_repo.class_in_school(self.db, 1, 1))
        self.assertFalse(attendance_repo.class_in_school(self.db, 1, 2))
        self.assertFalse(attendance_repo.class_in_school(self.db, 999, 1))


class ResetThrottlingTests(unittest.TestCase):
    """The anonymous reset endpoints are rate-limited and tokens are never
    re-issued back-to-back for the same identifier, so a caller can't flood
    delivery or keep invalidating a user's legitimate token."""

    @classmethod
    def setUpClass(cls):
        # The router imports its sibling via a bare `import service` /
        # `from schemas import ...`, which collides with the top-level names
        # left behind by other services' routers in this same test run. Save
        # and evict those names so a fresh, auth-only import happens here.
        sys.path.insert(0, "services/auth_service")
        cls._saved = {name: sys.modules.pop(name, None) for name in ("schemas", "service", "router")}
        try:
            import router as auth_router
            cls.router = auth_router
        except Exception:
            for name, mod in cls._saved.items():
                if mod is not None:
                    sys.modules[name] = mod
            raise

    @classmethod
    def tearDownClass(cls):
        for name, mod in cls._saved.items():
            if mod is not None:
                sys.modules[name] = mod
        sys.path.pop(0)

    def setUp(self):
        self.router._attempts.clear()
        self.router._last_issue.clear()

    def test_rate_limiter_allows_limit_then_blocks(self):
        router = self.router
        key = "victim@example.com"
        for _ in range(router._RESET_ATTEMPT_LIMIT):
            self.assertFalse(router._rate_limited(key))
        self.assertTrue(router._rate_limited(key))
        # A different identifier is unaffected.
        self.assertFalse(router._rate_limited("other@example.com"))

    def test_cooldown_blocks_immediate_reissue(self):
        router = self.router
        router._record_issue("cool@example.com")
        self.assertTrue(router._cooldown_active("cool@example.com"))
        self.assertFalse(router._cooldown_active("fresh@example.com"))


class ResetResponseUniformityTests(unittest.TestCase):
    """Forgot-password responses never carry the reset code — it is delivered
    by email out of band — and known vs unknown identifiers get byte-identical
    bodies so the endpoint cannot be used to enumerate accounts."""

    @classmethod
    def setUpClass(cls):
        cls.Session = make_session(["users", "schools", "staff", "teachers", "parents", "pilots"])
        sys.path.insert(0, "services/auth_service")
        cls._saved = {name: sys.modules.pop(name, None) for name in ("schemas", "service", "router")}
        try:
            import router as auth_router
            cls.router = auth_router
        except Exception:
            for name, mod in cls._saved.items():
                if mod is not None:
                    sys.modules[name] = mod
            raise

    @classmethod
    def tearDownClass(cls):
        for name, mod in cls._saved.items():
            if mod is not None:
                sys.modules[name] = mod
        sys.path.pop(0)

    def setUp(self):
        self.db: Session = self.Session()
        self.db.query(User).delete()
        self.db.query(Staff).delete()
        self.db.query(School).delete()
        self.db.add(School(
            school_id=1, name="Sunrise High", address="1 Main Rd", pincode="110001",
            city="New Delhi", state="Delhi", primary_contact="9999999999",
            primary_email="boss@school.org",
        ))
        self.db.add(User(
            user_id=1, school_id=1, role="admin", username="admin1",
            password_hash=hash_password("x"),
        ))
        self.db.commit()
        self.router._attempts.clear()
        self.router._last_issue.clear()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    @mock.patch("common.email.send_email")
    def test_known_identifier_emails_token_out_of_band(self, mock_send):
        payload = self.router.ForgotPasswordRequest(identifier="admin1")
        response = self.router.forgot_password(payload, db=self.db)
        self.assertIsNone(response["reset_token"])
        self.assertIsNone(response["reset_expires_at"])
        self.assertIsNone(response["email"])
        self.assertIsNone(response["identifier"])
        mock_send.assert_called_once()
        to_addrs, subject, body = mock_send.call_args[0]
        self.assertEqual(to_addrs, ["boss@school.org"])
        self.assertIn("reset", subject.lower())
        self.assertIn("token", body.lower())

    @mock.patch("common.email.send_email")
    def test_known_and_unknown_responses_are_identical(self, mock_send):
        known_response = self.router.forgot_password(
            self.router.ForgotPasswordRequest(identifier="admin1"), db=self.db,
        )
        self.assertEqual(mock_send.call_count, 1)
        mock_send.reset_mock()
        unknown_response = self.router.forgot_password(
            self.router.ForgotPasswordRequest(identifier="nobody@nowhere.org"), db=self.db,
        )
        self.assertEqual(known_response, unknown_response)
        mock_send.assert_not_called()

    @mock.patch("common.email.send_email")
    def test_response_model_keeps_token_fields_null(self, mock_send):
        response = self.router.forgot_password(
            self.router.ForgotPasswordRequest(identifier="admin1"), db=self.db,
        )
        validated = self.router.ForgotPasswordResponse(**response)
        self.assertIsNone(validated.reset_token)
        self.assertIsNone(validated.reset_expires_at)


if __name__ == "__main__":
    unittest.main()
