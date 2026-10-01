"""GET /auth/me and POST /auth/change-password back the profile page.

/auth/me previously carried only identity plus school branding, so there was
no way for a user to see their own username, email or name. It now carries all
three, resolved from the session's user_id, plus a change-password endpoint so
the only editable thing on the profile is the caller's own credential.

These tests pin the resolution across roles (the name lives on a different
person table per role, and linked_person_id is only unique within its own
table), the identity fields that must not move, the account cases that must not
crash, and the fact that a change-password call requires the current password.
"""
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.database import Base
from common.dependencies import CurrentUser
from common.exceptions import UnauthorizedError
from common.models import School, Staff, Parent, User
from common.security import hash_password, verify_password

ROOT = Path(__file__).resolve().parents[1]

# services/auth_service/router.py uses bare `import service` and
# `from schemas import ...`, so importing it means putting the service
# directory on sys.path. That makes the bare names `service`, `router` and
# `schemas` resolve to the auth modules and collide with any other service's
# identically named modules in the same pytest process, so pop them first and
# restore them afterwards. Same approach as test_school_branding.py.
_BARE = ("service", "router", "schemas")

SCHOOL_A = 1
CURRENT_PASSWORD = "old-password-1"
NEW_PASSWORD = "new-password-2"


class UserProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = {name: sys.modules.pop(name, None) for name in _BARE}
        sys.path.insert(0, str(ROOT / "services" / "auth_service"))
        import router as auth_router  # noqa: PLC0415  (deliberately late)

        # staticmethod: a bare function assigned to a class attribute becomes
        # a bound method, and self would be passed as the db argument.
        cls.me = staticmethod(auth_router.me)
        cls.change_password_route = staticmethod(auth_router.change_password)
        cls.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        Base.metadata.create_all(
            cls.engine,
            tables=[
                Base.metadata.tables["schools"],
                Base.metadata.tables["staff"],
                Base.metadata.tables["parents"],
                Base.metadata.tables["users"],
            ],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        sys.path.remove(str(ROOT / "services" / "auth_service"))
        for name in _BARE:
            sys.modules.pop(name, None)
        for name, mod in cls._saved.items():
            if mod is not None:
                sys.modules[name] = mod

    def setUp(self):
        self.db = self.Session()
        # Each test needs a clean database: the change-password tests rotate a
        # stored hash, so a hash changed by one test would otherwise decide
        # whether the next test's "wrong current password" assertion holds.
        # Delete in dependency order, then rebuild the same fixed ids.
        self.db.query(User).delete()
        self.db.query(Staff).delete()
        self.db.query(Parent).delete()
        self.db.query(School).delete()
        self.db.commit()
        db = self.db
        db.add_all([
            School(school_id=SCHOOL_A, name="Green Valley Public School", address="a",
                   pincode="1", city="c", state="s", primary_contact="p",
                   primary_email="office@greenvalley.test"),
            Staff(staff_id=1, school_id=SCHOOL_A, name="Ravi Tejaswi",
                  role="Teacher", person_type="teacher", email="ravi@greenvalley.test"),
            Staff(staff_id=2, school_id=SCHOOL_A, name="Asha Menon",
                  role="Driver", person_type="pilot"),
            Staff(staff_id=3, school_id=SCHOOL_A, name="Principal",
                  role="Principal", person_type="admin"),
            Parent(parent_id=1, school_id=SCHOOL_A, name="Priya Sharma",
                   phone="999", email="priya@families.test"),
        ])
        db.commit()
        self._add_user(1, "teacher", 1)
        self._add_user(2, "pilot", 2)
        self._add_user(3, "admin", 3)
        self._add_user(4, "parent", 1)
        self._add_user(5, "master", None)

    def tearDown(self):
        self.db.close()

    def _add_user(self, user_id, role, linked_person_id, email=None):
        self.db.add(User(
            user_id=user_id, school_id=SCHOOL_A if role != "master" else None,
            role=role, username=f"{role}{user_id}",
            email=email, password_hash=hash_password(CURRENT_PASSWORD),
            linked_person_id=linked_person_id,
        ))
        self.db.commit()

    def _me(self, user_id, role, linked_person_id=None, school_id=SCHOOL_A):
        return self.me(db=self.db, current_user=CurrentUser(
            user_id=user_id, role=role, school_id=school_id,
            linked_person_id=linked_person_id,
        ))

    # -- what the profile page shows -----------------------------------------

    def test_returns_username_email_and_display_name(self):
        out = self._me(1, "teacher", 1)
        self.assertEqual(out["username"], "teacher1")
        self.assertEqual(out["email"], "ravi@greenvalley.test")
        self.assertEqual(out["display_name"], "Ravi Tejaswi")

    def test_name_resolves_per_role(self):
        # linked_person_id 1 is a teacher and a parent in the same database,
        # so only the role can decide which row the name comes from.
        cases = [
            (1, "teacher", 1, "Ravi Tejaswi"),
            (2, "pilot", 2, "Asha Menon"),
            (3, "admin", 3, "Principal"),
            (4, "parent", 1, "Priya Sharma"),
        ]
        for user_id, role, linked, expected in cases:
            with self.subTest(role=role):
                self.assertEqual(self._me(user_id, role, linked)["display_name"], expected)

    def test_account_email_wins_over_the_linked_person_email(self):
        # The account's own email is what the account actually uses, so it
        # takes precedence over the staff row it happens to link to.
        self._add_user(6, "teacher", 1, email="ravi.personal@proton.test")
        out = self._me(6, "teacher", 1)
        self.assertEqual(out["email"], "ravi.personal@proton.test")

    def test_admin_falls_back_to_the_school_primary_email(self):
        # An admin with no email on the account still has a deliverable one.
        out = self._me(3, "admin", 3)
        self.assertEqual(out["email"], "office@greenvalley.test")

    def test_pilot_with_no_email_anywhere_returns_null(self):
        out = self._me(2, "pilot", 2)
        self.assertIsNone(out["email"])

    def test_keeps_the_identity_and_branding_fields(self):
        # The branding block and the session fields the sidebar and the login
        # flow already rely on must not move while profile fields are added.
        out = self._me(1, "teacher", 1)
        self.assertEqual(out["user_id"], 1)
        self.assertEqual(out["role"], "teacher")
        self.assertEqual(out["school_id"], SCHOOL_A)
        self.assertEqual(out["linked_person_id"], 1)
        self.assertEqual(out["school_name"], "Green Valley Public School")

    # -- cases that must not crash -------------------------------------------

    def test_master_falls_back_to_the_username_as_the_name(self):
        # Masters have no linked person row, so there is no name to read.
        out = self._me(5, "master", None, school_id=None)
        self.assertEqual(out["display_name"], "master5")
        self.assertIsNone(out["email"])
        self.assertIsNone(out["school_name"])

    def test_dangling_linked_person_falls_back_to_the_username(self):
        # A session can outlive the person row it points at. Showing the
        # username beats rendering an empty profile.
        self._add_user(7, "teacher", 9999)
        out = self._me(7, "teacher", 9999)
        self.assertEqual(out["display_name"], "teacher7")

    def test_missing_user_row_returns_null_details_not_a_crash(self):
        out = self._me(9999, "teacher", 1)
        self.assertIsNone(out["username"])
        self.assertIsNone(out["email"])
        self.assertIsNone(out["display_name"])

    def test_details_come_from_the_session_not_a_parameter(self):
        """The profile must never read another account.

        There is no parameter to pass a user_id, and the response is derived
        from current_user alone, so the only way to see another account is to
        authenticate as it.
        """
        import inspect

        self.assertEqual(list(inspect.signature(self.me).parameters), ["db", "current_user"])
        out = self._me(1, "teacher", 1)
        self.assertEqual(out["username"], "teacher1")
        self.assertNotEqual(out["username"], "parent4")

    # -- changing the password ------------------------------------------------

    def _change(self, user_id, role, current_password, new_password):
        from schemas import ChangePasswordRequest  # noqa: PLC0415
        return self.change_password_route(
            ChangePasswordRequest(
                current_password=current_password, new_password=new_password
            ),
            db=self.db,
            current_user=CurrentUser(
                user_id=user_id, role=role, school_id=SCHOOL_A, linked_person_id=1
            ),
        )

    def test_change_password_swaps_the_stored_hash(self):
        out = self._change(1, "teacher", CURRENT_PASSWORD, NEW_PASSWORD)
        self.assertIn("changed", out["message"].lower())

        user = self.db.query(User).filter(User.user_id == 1).first()
        self.assertTrue(verify_password(NEW_PASSWORD, user.password_hash))
        self.assertFalse(verify_password(CURRENT_PASSWORD, user.password_hash))

    def test_wrong_current_password_is_rejected_and_changes_nothing(self):
        with self.assertRaises(UnauthorizedError):
            self._change(1, "teacher", "not-my-password", NEW_PASSWORD)
        user = self.db.query(User).filter(User.user_id == 1).first()
        self.assertTrue(verify_password(CURRENT_PASSWORD, user.password_hash))

    def test_change_clears_any_outstanding_reset_otp(self):
        # Otherwise a code issued before the change could still be redeemed.
        from datetime import datetime, timedelta, timezone  # noqa: PLC0415
        user = self.db.query(User).filter(User.user_id == 1).first()
        user.password_reset_token = "a-digest"
        # Naive UTC, matching the timezone-naive column the service compares
        # against.
        user.password_reset_token_expires_at = (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=30)
        )
        self.db.commit()

        self._change(1, "teacher", CURRENT_PASSWORD, NEW_PASSWORD)

        user = self.db.query(User).filter(User.user_id == 1).first()
        self.assertIsNone(user.password_reset_token)
        self.assertIsNone(user.password_reset_token_expires_at)

    def test_rejects_a_new_password_identical_to_the_current_one(self):
        with self.assertRaises(ValueError):
            self._change(1, "teacher", CURRENT_PASSWORD, CURRENT_PASSWORD)

    def test_rejects_a_new_password_under_eight_characters(self):
        with self.assertRaises(ValueError):
            self._change(1, "teacher", CURRENT_PASSWORD, "short7c")

    def test_rejects_a_unicode_password_beyond_the_bcrypt_limit(self):
        # bcrypt silently truncates past 72 bytes, so a passphrase at 80
        # multi-byte characters (160 bytes) would be hashed from only its first
        # 72 bytes, letting any 80-byte string collide with its prefix.
        with self.assertRaises(ValueError):
            self._change(1, "teacher", CURRENT_PASSWORD, "\u00e9" * 80)

    def test_missing_user_row_is_rejected(self):
        with self.assertRaises(UnauthorizedError):
            self._change(9999, "teacher", CURRENT_PASSWORD, NEW_PASSWORD)

    def test_route_is_post_under_auth(self):
        # Registered as POST /api/v1/auth/change-password through the gateway.
        import router as auth_router  # noqa: PLC0415

        paths = {
            (route.path, method)
            for route in auth_router.router.routes
            for method in route.methods
        }
        self.assertIn(("/auth/change-password", "POST"), paths)


if __name__ == "__main__":
    unittest.main()
