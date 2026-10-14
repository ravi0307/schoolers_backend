"""Admin leave review records a remark together with the decision.

Runs against an in-memory SQLite database following the same convention as
the transport pick/drop tests.
"""
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.models import Base, LeaveRequest, School

ROOT = Path(__file__).resolve().parent.parent
_ENGINES = []

LEAVE_TABLES = [t for t in Base.metadata.sorted_tables if t.name in ("schools", "leave_requests")]


def seed(db):
    db.add(
        School(
            school_id=1,
            name="School One",
            address="1 Main Rd",
            pincode="560001",
            city="Bengaluru",
            state="KA",
            country="India",
            primary_contact="+9199",
            primary_email="one@example.com",
        )
    )
    db.commit()


def make_request(db, **overrides):
    row = LeaveRequest(
        school_id=1,
        requester_type="Teacher",
        requester_name="Ms. Rao",
        from_date=date(2026, 10, 20),
        to_date=date(2026, 10, 22),
        reason="Family function",
        **overrides,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class LeaveReviewRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        _ENGINES.append(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)

    def setUp(self):
        Base.metadata.drop_all(self.engine, tables=LEAVE_TABLES)
        Base.metadata.create_all(self.engine, tables=LEAVE_TABLES)
        self.session = self.Session()
        seed(self.session)

    def tearDown(self):
        self.session.close()

    def _repo(self):
        from services.leave_service import repository as repo

        return repo

    def test_approve_saves_remark_with_decision(self):
        repo = self._repo()
        row = make_request(self.session)
        out = repo.set_status(self.session, row, "Approved", remark="Cover arranged")
        self.assertEqual(out.status, "Approved")
        self.assertEqual(out.review_remark, "Cover arranged")

    def test_reject_saves_remark_with_decision(self):
        repo = self._repo()
        row = make_request(self.session)
        out = repo.set_status(self.session, row, "Rejected", remark="Exams week")
        self.assertEqual(out.status, "Rejected")
        self.assertEqual(out.review_remark, "Exams week")

    def test_review_without_remark_leaves_existing_remark_alone(self):
        repo = self._repo()
        row = make_request(self.session)
        repo.set_status(self.session, row, "Approved", remark="First note")
        out = repo.set_status(self.session, row, "Rejected")
        self.assertEqual(out.status, "Rejected")
        self.assertEqual(out.review_remark, "First note")

    def test_history_lists_expose_saved_remarks(self):
        repo = self._repo()
        row = make_request(self.session)
        repo.set_status(self.session, row, "Approved", remark="Cover arranged")
        rows = {r.leave_id: r for r in repo.list_for_school(self.session, 1)}
        self.assertEqual(rows[row.leave_id].review_remark, "Cover arranged")


class LeaveReviewContractTests(unittest.TestCase):
    def test_review_body_and_remark_field_line_up(self):
        schema = (ROOT / "services" / "leave_service" / "schemas.py").read_text(encoding="utf-8")
        router = (ROOT / "services" / "leave_service" / "router.py").read_text(encoding="utf-8")
        model = (ROOT / "common" / "models.py").read_text(encoding="utf-8")
        migrations = (ROOT / "db-migrations.sql").read_text(encoding="utf-8")
        self.assertIn("class LeaveRequestReview", schema)
        self.assertIn("review_remark", schema)
        self.assertIn("payload: LeaveRequestReview | None = None", router)
        self.assertIn("review_remark = Column(String(500))", model)
        self.assertIn("ADD COLUMN IF NOT EXISTS review_remark VARCHAR(500)", migrations)


def tearDownModule():
    for engine in _ENGINES:
        engine.dispose()
