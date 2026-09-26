"""
Timetable subject tenancy tests.

timetable_service now refuses to attach a subject that belongs to another
school when creating a week period or updating an entry, so timetable_rows
never leak a foreign school's subject id.
"""
import unittest
from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from common.models import Base, Subject, SchoolClass
from common.exceptions import NotFoundError
import services.timetable_service.repository as repo

TABLES = [
    "subjects",
    "timetable_entries",
    "periods",
    "classes",
    "holidays",
]


def _sqlite_helpers(dbapi_connection, connection_record):
    # timetable_entries.created_on uses text("timezone('Asia/Kolkata', now())"),
    # a Postgres expression; mimic it so inserts work on the in-memory DB.
    dbapi_connection.create_function("now", 0, lambda: datetime.now().isoformat())
    dbapi_connection.create_function("timezone", 2, lambda zone, ts: ts)


class TimetableSubjectScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        event.listen(cls.engine, "connect", _sqlite_helpers)
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[name] for name in TABLES],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()
        self.db.add_all(
            [
                Subject(subject_id=1, school_id=1, name="Maths"),
                Subject(subject_id=2, school_id=2, name="Robotics"),
                Subject(subject_id=3, school_id=1, name="English"),
                SchoolClass(class_id=1, school_id=1, name="Class 1"),
                SchoolClass(class_id=2, school_id=2, name="Class 2"),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def test_subject_in_school(self):
        self.assertTrue(repo.subject_in_school(self.db, 1, 1))
        self.assertTrue(repo.subject_in_school(self.db, 1, 3))
        self.assertFalse(repo.subject_in_school(self.db, 1, 2))
        self.assertFalse(repo.subject_in_school(self.db, 2, 1))

    def test_create_week_period_rejects_foreign_subject(self):
        with self.assertRaises(NotFoundError):
            repo.create_week_period(
                self.db, class_id=1, period_time="9:00 AM - 9:45 AM",
                school_id=1, subject_id=2,
            )

    def test_create_week_period_accepts_own_subject(self):
        entries = repo.create_week_period(
            self.db, class_id=1, period_time="9:00 AM - 9:45 AM",
            school_id=1, subject_id=1, day_of_week="Mon",
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].school_id, 1)
        self.assertEqual(entries[0].subject_id, 1)

    def test_create_week_period_allows_missing_subject(self):
        entries = repo.create_week_period(
            self.db, class_id=1, period_time="9:00 AM - 9:45 AM",
            school_id=1, subject_id=None,
        )
        self.assertEqual(len(entries), 7)
        self.assertTrue(all(e.subject_id is None for e in entries))

    def test_update_entry_rejects_foreign_subject(self):
        entries = repo.create_week_period(
            self.db, class_id=1, period_time="9:00 AM - 9:45 AM",
            school_id=1, subject_id=1, day_of_week="Mon",
        )
        entry = entries[0]
        with self.assertRaises(NotFoundError):
            repo.update_entry(
                self.db, entry, subject_id=2, teacher_id=None,
                period_start_time=None, period_end_time=None, school_id=1,
            )

    def test_update_entry_accepts_own_subject(self):
        entries = repo.create_week_period(
            self.db, class_id=1, period_time="9:00 AM - 9:45 AM",
            school_id=1, subject_id=1, day_of_week="Mon",
        )
        entry = entries[0]
        updated = repo.update_entry(
            self.db, entry, subject_id=3, teacher_id=None,
            period_start_time=None, period_end_time=None, school_id=1,
        )
        self.assertEqual(updated.subject_id, 3)


if __name__ == "__main__":
    unittest.main()