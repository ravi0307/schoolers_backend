"""Audit-trail coverage: every domain table carries modified_by/modified_at
and the before-flush listener (plus the bulk-update helper) stamps them."""

import asyncio
import sys
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from common import audit
from common.audit import bulk_modified_columns, current_actor
from common.database import Base
from common.models import (
    School,
    Teacher,
    Student,
    SchoolClass,
    User,
)  # noqa: F401  (registering the models/listener is the point)

ROOT = Path(__file__).resolve().parents[1]

_ENGINES = []


def make_session(tables):
    engine = create_engine("sqlite://")
    _ENGINES.append(engine)
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in tables])
    return sessionmaker(bind=engine, autoflush=False, future=True)


def tearDownModule():
    for engine in _ENGINES:
        engine.dispose()


def build_school(school_id=1):
    return School(
        school_id=school_id,
        name="Sunrise High",
        address="1 Main Rd",
        pincode="110001",
        city="New Delhi",
        state="Delhi",
        primary_contact="9999999999",
        primary_email="principal@sunrise.edu",
    )


def build_teacher(teacher_id=1, school_id=1):
    return Teacher(
        teacher_id=teacher_id,
        school_id=school_id,
        name="Ravi Kumar",
        role_title="Mathematics",
        phone="9876543210",
    )


class AuditColumnsEverywhereTests(unittest.TestCase):
    def test_every_domain_table_has_modified_by_and_modified_at(self):
        mapped_tables = list(Base.metadata.tables.values())
        self.assertGreater(len(mapped_tables), 25)
        for table in mapped_tables:
            with self.subTest(table=table.name):
                self.assertIn("modified_by", table.c)
                self.assertIn("modified_at", table.c)

    def test_modified_by_references_users_on_every_table(self):
        for table in Base.metadata.tables.values():
            fk = table.c.modified_by.foreign_keys
            with self.subTest(table=table.name):
                self.assertEqual(len(fk), 1)
                self.assertEqual(list(fk)[0].column.table.name, "users")


class AuditStampingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Session = make_session(
            ["users", "schools", "teachers", "classes", "students"]
        )

    def setUp(self):
        self.db: Session = self.Session()
        self.db.query(Student).delete()
        self.db.query(SchoolClass).delete()
        self.db.query(Teacher).delete()
        self.db.query(User).delete()
        self.db.query(School).delete()
        self.db.add(build_school())
        self.db.add(build_teacher())
        self.db.commit()

    def tearDown(self):
        audit.set_current_actor(None)
        self.db.rollback()
        self.db.close()

    def load_teacher(self) -> Teacher:
        return self.db.query(Teacher).first()

    def test_update_stamps_actor_and_time_only_when_actor_set(self):
        audit.set_current_actor(42)
        teacher = self.load_teacher()
        teacher.role_title = "Physics"
        self.db.commit()

        self.assertEqual(teacher.modified_by, 42)
        self.assertIsNotNone(teacher.modified_at)

    def test_update_without_actor_keeps_modified_by_null(self):
        teacher = self.load_teacher()
        teacher.role_title = "Chemistry"
        self.db.commit()

        self.assertIsNone(teacher.modified_by)
        self.assertIsNotNone(teacher.modified_at)

    def test_insert_stamps_creator_from_context(self):
        audit.set_current_actor(7)
        new_teacher = build_teacher(teacher_id=2)
        self.db.add(new_teacher)
        self.db.commit()

        self.assertEqual(new_teacher.modified_by, 7)
        self.assertIsNotNone(new_teacher.modified_at)

    def test_school_and_user_rows_stamped_too(self):
        audit.set_current_actor(3)
        user = User(
            user_id=9,
            school_id=1,
            role="admin",
            username="auditor",
            password_hash="x",
        )
        self.db.add(user)
        self.db.commit()
        school = self.db.query(School).first()
        school.primary_contact = "9888888888"
        self.db.commit()

        self.assertEqual(user.modified_by, 3)
        self.assertEqual(school.modified_by, 3)
        self.assertIsNotNone(school.modified_at)

    def test_new_actor_overwrites_previous_stamp(self):
        audit.set_current_actor(5)
        teacher = self.load_teacher()
        teacher.role_title = "Physics"
        self.db.commit()

        audit.set_current_actor(9)
        teacher = self.load_teacher()
        teacher.role_title = "Chemistry"
        self.db.commit()

        self.assertEqual(teacher.modified_by, 9)

    def test_system_update_without_actor_preserves_previous_stamp(self):
        audit.set_current_actor(5)
        teacher = self.load_teacher()
        teacher.role_title = "Physics"
        self.db.commit()
        first_stamp = teacher.modified_at
        self.assertEqual(teacher.modified_by, 5)

        audit.set_current_actor(None)
        teacher = self.load_teacher()
        teacher.role_title = "Biology"
        self.db.commit()

        self.assertEqual(teacher.modified_by, 5)
        self.assertIsNotNone(teacher.modified_at)
        self.assertGreaterEqual(teacher.modified_at, first_stamp)

    def test_modified_at_bumped_by_onupdate_on_later_change(self):
        teacher = self.load_teacher()
        sentinel = datetime(2000, 1, 1)
        teacher.modified_at = sentinel
        teacher.role_title = "Physics"
        self.db.commit()
        self.assertEqual(teacher.modified_at, sentinel)

        teacher = self.load_teacher()
        teacher.role_title = "Chemistry"
        self.db.commit()

        self.assertIsNotNone(teacher.modified_at)
        self.assertGreater(teacher.modified_at, sentinel)


class AuditBulkUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Session = make_session(
            ["users", "schools", "teachers", "classes", "students"]
        )
        from services.people_service import repository as people_repo  # noqa: E402

        cls.repo = people_repo

    def setUp(self):
        self.db: Session = self.Session()
        self.db.query(Student).delete()
        self.db.query(SchoolClass).delete()
        self.db.query(Teacher).delete()
        self.db.query(User).delete()
        self.db.query(School).delete()
        self.db.add(build_school())
        self.db.add(SchoolClass(school_id=1, name="Class 9"))
        self.db.add(
            Student(
                student_id=1,
                school_id=1,
                class_id=1,
                admission_no="ADM-001",
                name="Anya",
            )
        )
        self.db.commit()

    def tearDown(self):
        audit.set_current_actor(None)
        self.db.rollback()
        self.db.close()

    def test_bulk_cascade_deactivation_is_stamped(self):
        audit.set_current_actor(11)
        affected = self.repo.deactivate_school_students(self.db, school_id=1)
        self.assertEqual(affected, 1)
        student = self.db.query(Student).first()
        self.assertFalse(student.is_active)
        self.assertEqual(student.modified_by, 11)
        self.assertIsNotNone(student.modified_at)

    def test_bulk_helper_without_actor_stamps_time_only(self):
        affected = self.repo.deactivate_school_students(self.db, school_id=1)
        self.assertEqual(affected, 1)
        student = self.db.query(Student).first()
        self.assertIsNone(student.modified_by)
        self.assertIsNotNone(student.modified_at)


class ActorContextTests(unittest.TestCase):
    def tearDown(self):
        audit.set_current_actor(None)

    def test_get_current_user_sets_actor_from_token(self):
        from common.dependencies import get_current_user
        from common.security import create_access_token

        token = create_access_token(user_id=42, role="admin", school_id=1)

        async def resolve_and_read() -> int | None:
            # await + read inside the same context: asyncio.run would copy the
            # context for its own task and discard the set on return.
            await get_current_user(authorization=f"Bearer {token}", db=None)
            return current_actor()

        self.assertEqual(asyncio.run(resolve_and_read()), 42)

    def test_invalid_token_leaves_actor_unset(self):
        from common.dependencies import get_current_user

        async def resolve() -> None:
            await get_current_user(authorization="Bearer not.a.token", db=None)

        with self.assertRaises(Exception):
            asyncio.run(resolve())
        self.assertIsNone(current_actor())


class BulkHelperTests(unittest.TestCase):
    def tearDown(self):
        audit.set_current_actor(None)

    def test_bulk_modified_columns_uses_actor_and_clock(self):
        audit.set_current_actor(5)
        cols = bulk_modified_columns()
        self.assertEqual(cols["modified_by"], 5)
        self.assertIsInstance(cols["modified_at"], datetime)

    def test_bulk_modified_columns_without_actor_nulls_user_only(self):
        cols = bulk_modified_columns()
        self.assertIsNone(cols["modified_by"])
        self.assertIsInstance(cols["modified_at"], datetime)


class AuditEndToEndHttpTests(unittest.TestCase):
    """The full path: JWT -> async get_current_user -> contextvar -> flush
    listener -> row, without relying on starlette's TestClient (its httpx2
    dependency is missing in CI). It reproduces exactly what FastAPI does for a
    sync route: the async dependency is awaited in the event loop (setting the
    actor in the request context), then the endpoint runs in a threadpool
    worker that inherits that context. Catches the contextvar/threadpool
    propagation bug that unit tests calling set_current_actor() directly would
    never see."""

    _BARE = ("main", "router", "repository", "schemas")

    @classmethod
    def setUpClass(cls):
        cls._saved = {name: sys.modules.pop(name, None) for name in cls._BARE}
        sys.path.insert(0, str(ROOT / "services" / "people_service"))
        import main as people_main  # noqa: E402  (bare import inside service)
        import router as people_router  # noqa: E402

        cls.people_main = people_main
        cls.people_router = people_router
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,  # one shared in-memory DB across all threads/connections
        )
        _ENGINES.append(cls.engine)
        Base.metadata.create_all(
            cls.engine,
            tables=[
                Base.metadata.tables[name]
                for name in ("users", "schools", "teachers", "staff", "classes", "students", "parents")
            ],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

        seed = cls.Session()
        seed.add(build_school())
        seed.add(build_teacher())
        seed.add(
            User(user_id=99, school_id=1, role="admin", username="root", password_hash="x")
        )
        seed.commit()
        seed.close()

    @classmethod
    def tearDownClass(cls):
        sys.path.pop(0)
        for name in cls._BARE:
            sys.modules.pop(name, None)
        for name, mod in cls._saved.items():
            if mod is not None:
                sys.modules[name] = mod

    def setUp(self):
        from common.security import create_access_token

        self.token = create_access_token(user_id=99, role="admin", school_id=1)
        self.db = self.Session()

    def tearDown(self):
        audit.set_current_actor(None)
        self.db.close()

    def _update_through_threadpool(self, token: str, role_title: str, teacher_id: int = 1) -> None:
        """Serve one request the way FastAPI does, but borrowing this repo's
        real route function and threadpool mechanism. The async dependency sets
        the actor in the caller context; asyncio.to_thread copies that context
        into the thread that performs the flush, matching run_in_threadpool."""

        from common.dependencies import get_current_user

        db = self.db

        async def serve():
            current_user = await get_current_user(
                authorization=f"Bearer {token}", db=db
            )
            await asyncio.to_thread(
                self.people_router.update_teacher,
                teacher_id,
                self.people_router.TeacherUpdate(role_title=role_title),
                db,
                1,
                current_user,
            )
            db.commit()

        asyncio.run(serve())

    def test_authenticated_update_stamps_modified_by_from_token(self):
        self._update_through_threadpool(self.token, "Physics")

        teacher = self.db.query(Teacher).first()
        self.assertEqual(teacher.role_title, "Physics")
        self.assertEqual(teacher.modified_by, 99)
        self.assertIsNotNone(teacher.modified_at)

    def test_second_request_from_other_user_restamps_actor(self):
        from common.security import create_access_token

        self._update_through_threadpool(self.token, "Physics")
        other_token = create_access_token(user_id=77, role="admin", school_id=1)
        self._update_through_threadpool(other_token, "Chemistry")

        teacher = self.db.query(Teacher).first()
        self.assertEqual(teacher.role_title, "Chemistry")
        self.assertEqual(teacher.modified_by, 77)


if __name__ == "__main__":
    unittest.main()