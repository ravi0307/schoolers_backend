"""Master-to-School feature-access regression tests.

Root cause: the Master Admin toggle persisted flags but nothing downstream
consumed them reliably — no School Admin read path, no key validation, and
one exam-framework endpoint set (class offerings) skipped the flag gate.

Covers: master read/write of feature config with read-back persistence,
unsupported-key rejection, School Admin own-school reads, cross-school and
non-configured-role denial, School Admin write denial, disabled-feature
denial for the newly gated endpoints, and the consistent {"detail": ...}
403 body shape for unauthorized vs disabled access.
"""
import sys
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import Base, get_db
from common.dependencies import CurrentUser, get_current_user
from common.exception_handlers import register_exception_handlers
from common.exceptions import ForbiddenError
from common.models import School, SchoolClass, Subject

ROOT = Path(__file__).resolve().parents[1]

TABLES = ["schools", "classes", "subjects", "class_subjects"]


def _load(service_dir, package):
    sys.path.insert(0, service_dir)
    try:
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)
        router = __import__(f"{package}.router", fromlist=["x"])
        schemas = __import__(f"{package}.schemas", fromlist=["x"])
        return router, schemas
    finally:
        sys.path.pop(0)
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)


def _school(db, school_id, name, enabled):
    db.add(School(school_id=school_id, name=name, address="A", pincode="1",
                  city="C", state="S", primary_contact="P", primary_email="e@x.io",
                  examinations_enabled=enabled))
    db.commit()


class FeatureAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # TestClient runs sync endpoints in a worker thread, so SQLite must
        # allow cross-thread use of the single in-memory connection.
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(cls.engine,
                                 tables=[Base.metadata.tables[name] for name in TABLES])
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

        cls.schools_router, cls.schools_schemas = _load(
            "services/schools_service", "services.schools_service")
        cls.acad_router, cls.acad_schemas = _load(
            "services/academics_service", "services.academics_service")

        cls.app = FastAPI()
        cls.app.include_router(cls.schools_router.router)
        register_exception_handlers(cls.app)
        cls.current = {"user": None}

        def _db():
            return cls._session

        cls.app.dependency_overrides[get_db] = _db
        cls.app.dependency_overrides[get_current_user] = lambda: cls.current["user"]

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.engine.dispose()

    def setUp(self):
        self.db: Session = self.Session()
        type(self)._session = self.db
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()
        self.master = CurrentUser(user_id=1, role="master", school_id=None)
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1)
        self.admin2 = CurrentUser(user_id=12, role="admin", school_id=2)
        self.teacher = CurrentUser(user_id=31, role="teacher", school_id=1)
        _school(self.db, 1, "School A", False)
        _school(self.db, 2, "School B", True)
        self.db.add_all([
            SchoolClass(class_id=1, school_id=1, name="8-A"),
            SchoolClass(class_id=2, school_id=2, name="8-B"),
            Subject(subject_id=1, school_id=1, name="Maths"),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _as(self, user):
        self.current["user"] = user
        return TestClient(self.app)

    # ---- Master Admin: load, toggle, persist, read back ----

    def test_master_loads_feature_configuration(self):
        resp = self._as(self.master).get("/schools/2/features")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["school_id"], 2)
        self.assertTrue(body["examinations_enabled"])
        self.assertFalse(body["route_enabled"])

    def test_master_enable_persists_and_survives_reload(self):
        client = self._as(self.master)
        resp = client.patch("/schools/1/features", json={"examinations_enabled": True})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["examinations_enabled"])
        # Read-back in a fresh request = what the UI sees after refresh.
        reload = client.get("/schools/1/features")
        self.assertTrue(reload.json()["examinations_enabled"])
        # And the disable direction persists too.
        off = client.patch("/schools/1/features", json={"examinations_enabled": False})
        self.assertFalse(off.json()["examinations_enabled"])
        self.assertFalse(client.get("/schools/1/features").json()["examinations_enabled"])

    def test_unsupported_feature_key_is_rejected(self):
        resp = self._as(self.master).patch(
            "/schools/1/features", json={"bogus_enabled": True})
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(self.schools_schemas.FeatureFlags.model_config["extra"], "forbid")
        with self.assertRaises(ValidationError):
            self.schools_schemas.FeatureFlags(**{"bogus_enabled": True})

    # ---- School Admin: own-school read, cross-school denied ----

    def test_school_admin_reads_own_school_features(self):
        resp = self._as(self.admin1).get("/schools/1/features")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["school_id"], 1)
        self.assertFalse(body["examinations_enabled"])

    def test_cross_school_feature_read_is_denied(self):
        resp = self._as(self.admin1).get("/schools/2/features")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"detail": "Not allowed to access this school"})

    # ---- Writes stay Master-only ----

    def test_school_admin_cannot_modify_feature_settings(self):
        resp = self._as(self.admin1).patch(
            "/schools/1/features", json={"examinations_enabled": True})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            resp.json(),
            {"detail": "Role 'admin' cannot access this endpoint"},
        )
        self.assertFalse(self.db.get(School, 1).examinations_enabled)

    def test_non_configured_roles_are_denied(self):
        client = self._as(self.teacher)
        responses = [
            client.get("/schools/1/features"),
            client.patch("/schools/1/features", json={"examinations_enabled": True}),
        ]
        for resp in responses:
            with self.subTest(status=resp.status_code, body=resp.text):
                self.assertEqual(resp.status_code, 403)
                self.assertIn("detail", resp.json())

    # ---- Disabled-feature enforcement (class offerings gate) ----

    def test_disabled_school_denies_class_offering_endpoints(self):
        args_list = (
            lambda: self.acad_router.list_class_offerings(1, self.db, 1, self.admin1),
            lambda: self.acad_router.set_class_offering(
                1, self.acad_schemas.ClassSubjectSet(subject_id=1), self.db, 1, self.admin1),
            lambda: self.acad_router.delete_class_offering(1, 1, self.db, 1, self.admin1),
        )
        for i, call in enumerate(args_list):
            with self.subTest(endpoint=i):
                with self.assertRaises(ForbiddenError) as ctx:
                    call()
                self.assertEqual(
                    str(ctx.exception.message),
                    "Examinations are not enabled for this school",
                )

    def test_enabled_school_allows_class_offering_reads(self):
        rows = self.acad_router.list_class_offerings(2, self.db, 2, self.admin2)
        self.assertIsInstance(rows, list)

    def test_403_body_shape_is_consistent(self):
        """Unauthorized role, cross-school access, and a disabled feature all
        answer 403 with the same {"detail": ...} envelope."""
        client = self._as(self.admin1)
        shapes = [
            client.get("/schools/2/features").json(),           # cross-school
            client.patch("/schools/1/features",
                         json={"route_enabled": True}).json(),  # role
        ]
        for body in shapes:
            with self.subTest(body=body):
                self.assertEqual(set(body), {"detail"})
                self.assertIsInstance(body["detail"], str)


if __name__ == "__main__":
    unittest.main()
