"""GET /auth/me must carry the caller's own school name and logo.

Every role needs its own school's branding in the sidebar, but the school
endpoints cannot serve it: GET /schools is master-only and
GET /schools/{id} is master/admin-only, so a teacher, pilot or parent gets a
403. /auth/me is the one endpoint every authenticated role can reach, so the
branding travels with the session.

These tests pin that, and pin the two cases that could leak or crash:
a master account with no school, and a session whose school_id points at a
school that no longer exists.
"""
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.database import Base
from common.dependencies import CurrentUser
from common.models import School

ROOT = Path(__file__).resolve().parents[1]

# services/auth_service/router.py uses bare `import service` and
# `from schemas import ...`, so importing it means putting the service
# directory on sys.path. That makes the bare names `service`, `router` and
# `schemas` resolve to the auth modules and collide with any other service's
# identically named modules in the same pytest process, so pop them first and
# restore them afterwards. Same approach as test_audit_trail.py.
_BARE = ("service", "router", "schemas")

SCHOOL_A = 1
SCHOOL_B = 2


class AuthMeSchoolBrandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = {name: sys.modules.pop(name, None) for name in _BARE}
        sys.path.insert(0, str(ROOT / "services" / "auth_service"))
        import router as auth_router  # noqa: PLC0415  (deliberately late)

        # staticmethod: a bare function assigned to a class attribute becomes
        # a bound method, and self would be passed as the db argument.
        cls.me = staticmethod(auth_router.me)
        cls.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        Base.metadata.create_all(
            cls.engine, tables=[Base.metadata.tables["schools"]]
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)
        db = cls.Session()
        db.add_all(
            [
                School(school_id=SCHOOL_A, name="Green Valley Public School", address="a",
                       pincode="1", city="c", state="s", primary_contact="p", primary_email="e",
                       logo_url="/api/v1/schools/uploads/green-valley.png"),
                School(school_id=SCHOOL_B, name="Blue Horizon Academy", address="a",
                       pincode="1", city="c", state="s", primary_contact="p", primary_email="e"),
            ]
        )
        db.commit()
        db.close()

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

    def tearDown(self):
        self.db.close()

    def _me(self, role, school_id, user_id=1):
        return self.me(db=self.db, current_user=CurrentUser(
            user_id=user_id, role=role, school_id=school_id, linked_person_id=None
        ))

    # -- the branding itself -------------------------------------------------

    def test_returns_school_name_and_logo(self):
        out = self._me("admin", SCHOOL_A)
        self.assertEqual(out["school_name"], "Green Valley Public School")
        self.assertEqual(out["school_logo_url"], "/api/v1/schools/uploads/green-valley.png")

    def test_school_without_a_logo_returns_a_name_and_null_logo(self):
        # Both live schools have logo_url = NULL today, so this is the
        # normal case, not an edge case: the client must still get the name.
        out = self._me("admin", SCHOOL_B)
        self.assertEqual(out["school_name"], "Blue Horizon Academy")
        self.assertIsNone(out["school_logo_url"])

    def test_each_school_sees_only_its_own_branding(self):
        self.assertEqual(self._me("parent", SCHOOL_A)["school_name"], "Green Valley Public School")
        self.assertEqual(self._me("parent", SCHOOL_B)["school_name"], "Blue Horizon Academy")

    def test_keeps_the_existing_identity_fields(self):
        # Branding is additive; the fields the login flow already relies on
        # must not move or disappear.
        out = self._me("parent", SCHOOL_A, user_id=329)
        self.assertEqual(out["user_id"], 329)
        self.assertEqual(out["role"], "parent")
        self.assertEqual(out["school_id"], SCHOOL_A)
        self.assertIn("linked_person_id", out)

    # -- every role can read it ----------------------------------------------

    def test_every_school_scoped_role_gets_the_branding(self):
        # This is the whole point: GET /schools is master-only and
        # GET /schools/{id} is master/admin-only, so if /auth/me did not carry
        # the name there would be no endpoint a parent or pilot could use.
        for role in ("admin", "teacher", "pilot", "parent", "staff"):
            with self.subTest(role=role):
                out = self._me(role, SCHOOL_A)
                self.assertEqual(out["school_name"], "Green Valley Public School")

    # -- cases that must not crash or leak ----------------------------------

    def test_master_with_no_school_gets_nulls(self):
        out = self._me("master", None)
        self.assertIsNone(out["school_name"])
        self.assertIsNone(out["school_logo_url"])
        self.assertIsNone(out["school_id"])

    def test_dangling_school_id_does_not_raise(self):
        # A session can outlive its school row. Returning nulls keeps the
        # sidebar on the product brand rather than 500-ing the whole portal.
        out = self._me("parent", 9999)
        self.assertIsNone(out["school_name"])
        self.assertIsNone(out["school_logo_url"])

    def test_school_lookup_is_never_by_request_supplied_id(self):
        """The branding comes from the session, not from a query parameter.

        A caller must not be able to ask for another school's name; there is
        no parameter to pass, and the response is derived from
        current_user.school_id alone.
        """
        import inspect

        self.assertEqual(list(inspect.signature(self.me).parameters), ["db", "current_user"])
        out = self._me("parent", SCHOOL_A)
        self.assertNotEqual(out["school_name"], "Blue Horizon Academy")


if __name__ == "__main__":
    unittest.main()
