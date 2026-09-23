"""Audit-trail coverage: every domain table carries modified_by/modified_at
and the before-flush listener (plus the bulk-update helper) stamps them."""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common import audit
from common.database import Base
from common.models import (
    School,
    Teacher,
    Student,
    SchoolClass,
    User,
)  # noqa: F401  (registering the models/listener is the point)

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


if __name__ == "__main__":
    unittest.main()