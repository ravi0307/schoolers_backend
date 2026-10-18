"""
Regression tests for the correction-audit defect (rollout report D3):
`correct_mark` recorded `corrected_by`/`corrected_at` but never populated
`approved_at`, so every post-publication correction carried a NULL
approval timestamp.

Required areas:
1. a successful correction records corrected_by, approved_by,
   corrected_at, and approved_at;
2. approval timestamps are non-null and follow the application's
   server-clock timestamp convention;
3. old/new values, grade, reason, and actor fields stay correctly
   recorded;
4. unauthorized users cannot correct marks;
5. cross-school attempts cannot reach another school's marks;
6. corrections are blocked when the feature flag is off;
7. draft marks are refused by the post-publication endpoint;
8. a failed correction leaves marks and audit history unchanged
   (validation failure and mid-commit failure);
9. legacy term-mark updates behave exactly as before.

Plus: historical rows with NULL approved_at stay valid, and the
correction reason remains mandatory at the HTTP boundary.

All data is disposable SQLite created per test; nothing here touches
production or shared records.
"""
import sys
import unittest
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from common.database import get_db
from common.dependencies import CurrentUser, get_current_user
from common.exception_handlers import register_exception_handlers
from common.exceptions import AppError
from common.models import (
    Base, AcademicYear, Examination, ExaminationSession, ExaminationType,
    GradeLevel, GradeScale, Mark, MarkCorrection, School, Student, Subject,
    TeacherClassSubject,
)
import services.marks_service.repository as repo

TABLES = [
    "schools", "academic_years", "academic_periods", "examination_types", "examinations",
    "examination_sessions", "grade_scales", "grade_levels",
    "students", "subjects", "teacher_class_subjects", "marks",
    "holidays", "academic_events", "class_subjects", "student_subjects",
    "mark_corrections", "mark_import_batches", "mark_import_rows",
]


def _load_marks_router():
    sys.path.insert(0, "services/marks_service")
    try:
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)
        from services.marks_service import router as marks_router
        from services.marks_service import schemas as marks_schemas
        return marks_router, marks_schemas
    finally:
        sys.path.pop(0)
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)


class CorrectionAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # StaticPool + check_same_thread=False: the HTTP tests run the
        # sync endpoint in Starlette's threadpool while sharing this
        # single in-memory connection with the test thread.
        cls.engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(
            cls.engine,
            tables=[Base.metadata.tables[name] for name in TABLES],
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)
        cls.router, cls.schemas = _load_marks_router()

    def setUp(self):
        self.db: Session = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.commit()
        # Fixture-only flag values on the disposable database; production
        # school flags are never touched by these tests.
        self.db.add_all([
            School(school_id=1, name="Green Valley", address="A", pincode="1",
                   city="C", state="S", primary_contact="P", primary_email="e@x.io",
                   examinations_enabled=True),
            School(school_id=2, name="Blue Horizon", address="A", pincode="1",
                   city="C", state="S", primary_contact="P", primary_email="e@x.io",
                   examinations_enabled=True),
            AcademicYear(year_id=1, school_id=1, name="2026-27",
                         start_date=datetime(2026, 6, 1).date(),
                         end_date=datetime(2027, 5, 31).date()),
            ExaminationType(type_id=1, school_id=1, name="Half-Yearly", default_max_marks=100),
            Examination(exam_id=1, school_id=1, type_id=1, year_id=1,
                        name="Half-Yearly", result_format_expectation="marks", status="draft"),
            Examination(exam_id=2, school_id=1, type_id=1, year_id=1,
                        name="Half-Yearly Grades", result_format_expectation="grade",
                        grade_scale_id=1, status="draft"),
            GradeScale(scale_id=1, school_id=1, name="Alpha", version=1),
            GradeLevel(level_id=1, scale_id=1, label="A", rank=1),
            GradeLevel(level_id=2, scale_id=1, label="B", rank=2),
            ExaminationSession(session_id=1, exam_id=1, class_id=1, subject_id=3,
                               date=datetime(2026, 12, 15).date(),
                               start_time=datetime(2026, 12, 15, 9, 0).time(),
                               end_time=datetime(2026, 12, 15, 12, 0).time(),
                               max_marks=100, status="scheduled"),
            ExaminationSession(session_id=2, exam_id=2, class_id=1, subject_id=3,
                               date=datetime(2026, 12, 16).date(),
                               start_time=datetime(2026, 12, 16, 9, 0).time(),
                               end_time=datetime(2026, 12, 16, 12, 0).time(),
                               max_marks=100, status="scheduled"),
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            Student(student_id=2, school_id=1, class_id=1, admission_no="A2", name="Dev"),
            Subject(subject_id=3, school_id=1, name="Maths"),
            TeacherClassSubject(staff_id=7, class_id=1, subject_id=3),
        ])
        self.db.commit()
        self.session1 = self.db.get(ExaminationSession, 1)
        self.exam1 = self.db.get(Examination, 1)
        self.session2 = self.db.get(ExaminationSession, 2)
        self.exam2 = self.db.get(Examination, 2)
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)
        self.admin2 = CurrentUser(user_id=12, role="admin", school_id=2, linked_person_id=None)
        self.teacher7 = CurrentUser(user_id=31, role="teacher", school_id=1, linked_person_id=7)
        self.parent225 = CurrentUser(user_id=21, role="parent", school_id=1, linked_person_id=225)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    # ---- fixtures ----

    def _published_mark(self, student_id=1, **payload):
        mark = repo.create_session_mark(
            self.db, self.session1, self.exam1, student_id, 3,
            {"assessment_status": "present", "score": 80, **payload}, 31,
        )
        repo.submit_session_marks(self.db, self.session1)
        repo.publish_session_marks(self.db, self.session1)
        self.assertEqual(mark.workflow_status, "published")
        return mark

    def _published_grade_mark(self, student_id=1, grade_label="A"):
        mark = repo.create_session_mark(
            self.db, self.session2, self.exam2, student_id, 3,
            {"assessment_status": "present", "grade_label": grade_label}, 31,
        )
        repo.submit_session_marks(self.db, self.session2)
        repo.publish_session_marks(self.db, self.session2)
        self.assertEqual(mark.workflow_status, "published")
        return mark

    def _client(self, current_user):
        app = FastAPI()
        app.include_router(self.router.router)
        register_exception_handlers(app)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: current_user
        return TestClient(app)

    def _correction_count(self) -> int:
        return self.db.query(MarkCorrection).count()

    def _assert_mark_unchanged(self, mark_id: int, score=80):
        self.db.rollback()
        row = self.db.get(Mark, mark_id)
        self.assertEqual(row.score, score)
        self.assertEqual(row.workflow_status, "published")
        self.assertEqual(row.assessment_status, "present")

    # ---- 1 + 2: audit capture and timestamp conventions ----

    def test_successful_correction_records_all_audit_fields(self):
        mark = self._published_mark()
        updated, corr = repo.correct_mark(
            self.db, mark,
            {"assessment_status": "present", "score": 88},
            "Totalling error", 11,
        )
        self.assertEqual(updated.score, 88)
        self.assertEqual(corr.corrected_by, 11)
        self.assertEqual(corr.approved_by, 11)
        self.assertIsNotNone(corr.corrected_at)
        self.assertIsNotNone(corr.approved_at)

    def test_approval_timestamps_use_database_clock(self):
        mark = self._published_mark()
        _, corr = repo.correct_mark(
            self.db, mark,
            {"assessment_status": "present", "score": 88},
            "Totalling error", 11,
        )
        self.assertIsNotNone(corr.corrected_at)
        self.assertIsNotNone(corr.approved_at)
        # Same server-side clock (func.now()), same transaction: the two
        # stamps cannot disagree meaningfully (second precision).
        self.assertGreaterEqual(corr.approved_at, corr.corrected_at)
        self.assertLessEqual(abs((corr.approved_at - corr.corrected_at).total_seconds()), 2)
        # Convention is naive UTC (SQLite CURRENT_TIMESTAMP / func.now()).
        self.assertIsNone(corr.approved_at.tzinfo)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.assertLessEqual(abs((corr.approved_at - now).total_seconds()), 300)

    # ---- 3: history fields ----

    def test_history_fields_recorded_for_marks_and_grade_corrections(self):
        mark = self._published_mark()
        _, corr = repo.correct_mark(
            self.db, mark,
            {"assessment_status": "present", "score": 88},
            "Totalling error", 11,
        )
        self.assertEqual((corr.old_score, corr.new_score), (80, 88))
        self.assertIsNone(corr.old_grade_level_id)
        self.assertIsNone(corr.new_grade_level_id)
        self.assertEqual(corr.reason, "Totalling error")
        self.assertEqual((corr.corrected_by, corr.approved_by), (11, 11))

        gmark = self._published_grade_mark()
        gupdated, gcorr = repo.correct_mark(
            self.db, gmark,
            {"assessment_status": "present", "grade_label": "B"},
            "Scale misread", 11,
        )
        self.assertEqual(gupdated.grade_level_id, 2)
        self.assertEqual((gcorr.old_grade_level_id, gcorr.new_grade_level_id), (1, 2))
        self.assertIsNone(gcorr.old_score)
        self.assertIsNone(gcorr.new_score)
        self.assertEqual(gcorr.reason, "Scale misread")
        self.assertEqual((gcorr.corrected_by, gcorr.approved_by), (11, 11))
        self.assertIsNotNone(gcorr.approved_at)
        self.assertEqual(self._correction_count(), 2)

    # ---- 4: authorization ----

    def test_unauthorized_roles_cannot_correct(self):
        mark = self._published_mark()
        body = {"score": 88, "assessment_status": "present", "reason": "Nope"}
        for user in (self.teacher7, self.parent225):
            resp = self._client(user).post(
                f"/marks/{mark.mark_id}/correct", json=body
            )
            self.assertEqual(resp.status_code, 403)
        self._assert_mark_unchanged(mark.mark_id)
        self.assertEqual(self._correction_count(), 0)

    # ---- 5: cross-school isolation ----

    def test_cross_school_correction_is_uniform_404_without_writes(self):
        mark = self._published_mark()
        client = self._client(self.admin2)
        known = client.post(
            f"/marks/{mark.mark_id}/correct",
            json={"score": 88, "assessment_status": "present", "reason": "Intrusion"},
        )
        unknown = client.post(
            f"/marks/{mark.mark_id + 999}/correct",
            json={"score": 88, "assessment_status": "present", "reason": "Intrusion"},
        )
        self.assertEqual(known.status_code, 404)
        self.assertEqual(unknown.status_code, 404)
        # Uniform response: no oracle distinguishing ids from other schools.
        self.assertEqual(known.json(), unknown.json())
        self._assert_mark_unchanged(mark.mark_id)
        self.assertEqual(self._correction_count(), 0)

    # ---- 6: feature-flag gate ----

    def test_flag_off_blocks_correction_without_writes(self):
        mark = self._published_mark()
        school = self.db.get(School, 1)
        school.examinations_enabled = False
        self.db.commit()
        resp = self._client(self.admin1).post(
            f"/marks/{mark.mark_id}/correct",
            json={"score": 88, "assessment_status": "present", "reason": "Nope"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["detail"], "Examinations are not enabled for this school")
        self._assert_mark_unchanged(mark.mark_id)
        self.assertEqual(self._correction_count(), 0)

    # ---- 7: draft refusal ----

    def test_draft_mark_cannot_be_corrected_through_endpoint(self):
        mark = repo.create_session_mark(
            self.db, self.session1, self.exam1, 1, 3,
            {"assessment_status": "present", "score": 80}, 31,
        )
        self.assertEqual(mark.workflow_status, "draft")
        resp = self._client(self.admin1).post(
            f"/marks/{mark.mark_id}/correct",
            json={"score": 88, "assessment_status": "present", "reason": "Premature"},
        )
        self.assertEqual(resp.status_code, 409)
        self.db.rollback()
        row = self.db.get(Mark, mark.mark_id)
        self.assertEqual(row.workflow_status, "draft")
        self.assertEqual(row.score, 80)
        self.assertEqual(self._correction_count(), 0)

    # ---- 8: failed corrections change nothing ----

    def test_failed_correction_leaves_marks_and_audit_unchanged(self):
        mark = self._published_mark()
        bad_payloads = [
            ({"assessment_status": "present", "score": 200}, 400),
            ({"assessment_status": "absent", "score": 10}, 422),
        ]
        for payload, status in bad_payloads:
            with self.assertRaises(AppError) as ctx:
                repo.correct_mark(self.db, mark, payload, "Bad input", 11)
            self.assertEqual(ctx.exception.status_code, status)
        self._assert_mark_unchanged(mark.mark_id)
        self.assertEqual(self._correction_count(), 0)
        # The flow is still healthy afterwards: exactly one clean record.
        repo.correct_mark(
            self.db, self.db.get(Mark, mark.mark_id),
            {"assessment_status": "present", "score": 88},
            "Totalling error", 11,
        )
        self.assertEqual(self._correction_count(), 1)

    def test_commit_failure_rolls_back_mark_and_audit_together(self):
        mark = self._published_mark()
        original_commit = self.db.commit

        def boom():
            raise RuntimeError("simulated commit failure")

        self.db.commit = boom
        try:
            with self.assertRaises(RuntimeError):
                repo.correct_mark(
                    self.db, mark,
                    {"assessment_status": "present", "score": 88},
                    "Totalling error", 11,
                )
        finally:
            self.db.commit = original_commit
        self.db.rollback()
        row = self.db.get(Mark, mark.mark_id)
        self.assertEqual(row.score, 80)
        self.assertEqual(row.workflow_status, "published")
        self.assertEqual(self._correction_count(), 0)

    # ---- 9: legacy term-mark behavior ----

    def test_legacy_term_mark_update_behavior_unchanged(self):
        # Deployment default: flag off. Legacy PUT must still work and
        # must not start writing correction records.
        school = self.db.get(School, 1)
        school.examinations_enabled = False
        legacy = Mark(
            student_id=2, subject_id=3, term="Term 1", score=70, max_marks=100,
            result_format="marks", workflow_status="published",
            assessment_status="present", is_withheld=False,
        )
        self.db.add(legacy)
        self.db.commit()
        payload = self.schemas.MarkUpsert(term="Term 1", score=75)
        row = self.router.upsert_mark(2, 3, payload, self.db, self.admin1)
        self.db.refresh(legacy)
        self.assertEqual(row.score, 75)
        self.assertEqual(legacy.workflow_status, "published")
        self.assertEqual(legacy.assessment_status, "present")
        self.assertEqual(legacy.examination_session_id, None)
        self.assertEqual(self._correction_count(), 0)

    # ---- historical rows and mandatory reason ----

    def test_historical_null_approval_timestamps_remain_valid(self):
        mark = self._published_mark()
        self.db.add(MarkCorrection(
            mark_id=mark.mark_id, old_score=70, new_score=75,
            reason="Pre-fix correction", corrected_by=11, approved_by=11,
        ))
        self.db.commit()
        rows = (
            self.db.query(MarkCorrection)
            .filter(MarkCorrection.approved_at.is_(None))
            .all()
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].approved_by, 11)
        self.assertEqual(rows[0].reason, "Pre-fix correction")

    def test_correction_reason_is_mandatory_at_http_boundary(self):
        mark = self._published_mark()
        resp = self._client(self.admin1).post(
            f"/marks/{mark.mark_id}/correct",
            json={"score": 88, "assessment_status": "present"},
        )
        self.assertEqual(resp.status_code, 422)
        self._assert_mark_unchanged(mark.mark_id)
        self.assertEqual(self._correction_count(), 0)

    def test_successful_correction_via_endpoint(self):
        mark = self._published_mark()
        resp = self._client(self.admin1).post(
            f"/marks/{mark.mark_id}/correct",
            json={"score": 88, "assessment_status": "present", "reason": "Totalling error"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["score"], 88)
        self.db.rollback()
        corr = self.db.query(MarkCorrection).one()
        self.assertEqual(corr.corrected_by, 11)
        self.assertEqual(corr.approved_by, 11)
        self.assertIsNotNone(corr.corrected_at)
        self.assertIsNotNone(corr.approved_at)
        self.assertEqual(corr.reason, "Totalling error")


if __name__ == "__main__":
    unittest.main()
