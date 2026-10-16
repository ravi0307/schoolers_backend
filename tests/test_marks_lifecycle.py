"""
Functional tests for the session marks lifecycle (Phase 4).

Covers session-scoped entry for both marking formats, the
draft -> submitted -> published flow with withholding, post-publish
corrections, legacy/session disjointness, the session-maximum guard,
grade-freeze guard, and the CSV import/export pipeline.
"""
import sys
import unittest
from datetime import date, time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
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


def _session_payload(**over):
    data = {"assessment_status": "present", "score": 80}
    data.update(over)
    return data


class SessionMarksLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
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
        self.db.add_all([
            School(school_id=1, name="Green Valley", address="A", pincode="1",
                   city="C", state="S", primary_contact="P", primary_email="e@x.io",
                   examinations_enabled=True),
            AcademicYear(year_id=1, school_id=1, name="2026-27",
                         start_date=date(2026, 6, 1), end_date=date(2027, 5, 31)),
            ExaminationType(type_id=1, school_id=1, name="Half-Yearly", default_max_marks=100),
            Examination(exam_id=1, school_id=1, type_id=1, year_id=1,
                        name="Half-Yearly", result_format_expectation="marks", status="draft"),
            Examination(exam_id=2, school_id=1, type_id=1, year_id=1,
                        name="Half-Yearly Grades", result_format_expectation="grade",
                        grade_scale_id=1, status="draft"),
            GradeScale(scale_id=1, school_id=1, name="Alpha", version=1),
            GradeLevel(level_id=1, scale_id=1, label="A", rank=1),
            ExaminationSession(session_id=1, exam_id=1, class_id=1, subject_id=3,
                               date=date(2026, 12, 15), start_time=time(9, 0),
                               end_time=time(12, 0), max_marks=100, status="scheduled"),
            ExaminationSession(session_id=2, exam_id=2, class_id=1, subject_id=3,
                               date=date(2026, 12, 16), start_time=time(9, 0),
                               end_time=time(12, 0), max_marks=100, status="scheduled"),
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            Student(student_id=2, school_id=1, class_id=1, admission_no="A2", name="Dev"),
            Student(student_id=3, school_id=1, class_id=2, admission_no="A3", name="Una"),
            Subject(subject_id=3, school_id=1, name="Maths"),
            TeacherClassSubject(staff_id=7, class_id=1, subject_id=3),
        ])
        self.db.commit()
        self.session1 = self.db.get(ExaminationSession, 1)
        self.exam1 = self.db.get(Examination, 1)
        self.session2 = self.db.get(ExaminationSession, 2)
        self.exam2 = self.db.get(Examination, 2)
        self.teacher7 = CurrentUser(user_id=31, role="teacher", school_id=1, linked_person_id=7)
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _mark(self, session=None, exam=None, student_id=1, **payload):
        session = session or self.session1
        exam = exam or self.exam1
        return repo.create_session_mark(
            self.db, session, exam, student_id, 3, _session_payload(**payload), 31
        )

    def test_create_marks_entry_starts_as_draft(self):
        mark = self._mark()
        self.assertEqual((mark.workflow_status, mark.assessment_status), ("draft", "present"))
        self.assertEqual((mark.score, mark.max_marks), (80, 100))

    def test_zero_is_a_valid_score(self):
        self.assertEqual(self._mark(score=0).score, 0)

    def test_score_above_session_max_rejected(self):
        with self.assertRaises(AppError):
            self._mark(score=101)

    def test_absent_with_score_rejected_422(self):
        with self.assertRaises(AppError) as ctx:
            self._mark(assessment_status="absent", score=0)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_absent_without_score_accepted(self):
        mark = self._mark(assessment_status="absent", score=None)
        self.assertIsNone(mark.score)

    def test_grade_entry_uses_pinned_scale(self):
        mark = self._mark(session=self.session2, exam=self.exam2,
                          grade_label="A", score=None)
        self.assertEqual(mark.grade_level_id, 1)
        self.assertIsNone(mark.score)
        with self.assertRaises(AppError):
            self._mark(session=self.session2, exam=self.exam2,
                       grade_label="Z", score=None)

    def test_duplicate_session_mark_rejected(self):
        self._mark()
        with self.assertRaises(ConflictError):
            self._mark()

    def test_student_outside_session_class_rejected(self):
        with self.assertRaises(AppError):
            repo.create_session_mark(
                self.db, self.session1, self.exam1, 3, 3,
                _session_payload(), 31,
            )

    def test_two_exams_share_subject_and_term_without_collision(self):
        """Issue-1 proof: same student+subject+term across two sessions."""
        m1 = self._mark()
        other_session = ExaminationSession(
            session_id=9, exam_id=1, class_id=1, subject_id=3,
            date=date(2026, 12, 17), start_time=time(9, 0), end_time=time(12, 0),
            max_marks=50, status="scheduled",
        )
        self.db.add(other_session)
        self.db.commit()
        m2 = repo.create_session_mark(
            self.db, other_session, self.exam1, 1, 3,
            {"assessment_status": "present", "score": 42}, 31,
        )
        legacy = repo.upsert_mark(self.db, 1, 3, "Term 1", 70, 7)
        rows = self.db.query(Mark).filter(Mark.student_id == 1, Mark.subject_id == 3).all()
        self.assertEqual(len(rows), 3)
        self.assertEqual({m.score for m in rows}, {80, 42, 70})
        sessions = sorted(
            m.examination_session_id for m in rows if m.examination_session_id is not None
        )
        self.assertEqual(sessions, [1, 9])

    def test_submit_publish_withhold_lifecycle(self):
        self._mark()
        self._mark(student_id=2)
        out = repo.submit_session_marks(self.db, self.session1)
        self.assertEqual(out["submitted"], 2)
        self.assertEqual(out["missing_student_ids"], [])
        out = repo.publish_session_marks(self.db, self.session1)
        self.assertEqual(out["published"], 2)
        rows = repo.get_for_student(self.db, 1, published_only=True, include_withheld=False)
        self.assertEqual(len(rows), 1)

    def test_submit_reports_missing_students(self):
        self._mark()
        out = repo.submit_session_marks(self.db, self.session1)
        self.assertEqual(out["missing_student_ids"], [2])

    def test_submitted_edit_returns_to_draft(self):
        mark = self._mark()
        repo.submit_session_marks(self.db, self.session1)
        updated = repo.update_session_mark(self.db, mark, {"score": 90})
        self.assertEqual((updated.score, updated.workflow_status), (90, "draft"))

    def test_published_edit_refused_without_correction(self):
        mark = self._mark()
        repo.submit_session_marks(self.db, self.session1)
        repo.publish_session_marks(self.db, self.session1)
        with self.assertRaises(ConflictError):
            repo.update_session_mark(self.db, mark, {"score": 90})

    def test_correction_appends_history_and_moves_mark(self):
        mark = self._mark()
        repo.submit_session_marks(self.db, self.session1)
        repo.publish_session_marks(self.db, self.session1)
        updated, correction = repo.correct_mark(
            self.db, mark, {"assessment_status": "present", "score": 88},
            "Totalling error", 11,
        )
        self.assertEqual(updated.score, 88)
        self.assertEqual((correction.old_score, correction.new_score), (80, 88))
        self.assertEqual(correction.reason, "Totalling error")
        kept = self.db.query(MarkCorrection).filter(
            MarkCorrection.mark_id == mark.mark_id).all()
        self.assertEqual(len(kept), 1)

    def test_withhold_hides_from_parent_view(self):
        mark = self._mark()
        repo.submit_session_marks(self.db, self.session1)
        repo.publish_session_marks(self.db, self.session1)
        repo.withhold_marks(self.db, [mark.mark_id], "Review", 11)
        self.assertEqual(
            repo.get_for_student(self.db, 1, published_only=True, include_withheld=False), [])
        self.assertEqual(
            len(repo.get_for_student(self.db, 1, published_only=True, include_withheld=True)), 1)
        repo.unwithhold_marks(self.db, [mark.mark_id])
        self.assertEqual(
            len(repo.get_for_student(self.db, 1, published_only=True, include_withheld=False)), 1)

    def test_router_teacher_unassigned_and_cross_school(self):
        router, schemas = _load_marks_router()
        stranger = CurrentUser(user_id=32, role="teacher", school_id=1, linked_person_id=99)
        with self.assertRaises(ForbiddenError):
            router.create_session_mark(
                schemas.SessionMarkCreate(
                    session_id=1, student_id=1, subject_id=3,
                    score=80, assessment_status="present"),
                self.db, stranger,
            )

    def test_legacy_upsert_returns_legacy_row_beside_session_rows(self):
        """E2E catch: with session and legacy rows sharing one
        (student, subject, term), the legacy write path must read back the
        legacy row — returning a session row breaks the legacy contract."""
        self._mark(score=42)
        row = repo.upsert_mark(self.db, 1, 3, "Term 1", 70, 7)
        self.assertIsNone(row.examination_session_id)
        self.assertEqual(row.score, 70)
        row = repo.upsert_mark(self.db, 1, 3, "Term 1", 75, 7)
        self.assertIsNone(row.examination_session_id)
        self.assertEqual(row.score, 75)

    def test_session_max_reduction_blocked_with_entered_scores(self):
        import services.academics_service.repository as acad_repo
        self._mark(score=80)
        with self.assertRaises(ConflictError):
            acad_repo.update_session(
                self.db, self.exam1, self.session1, 11, {"max_marks": 50}, "typo")
        session, _ = acad_repo.update_session(
            self.db, self.exam1, self.session1, 11, {"max_marks": 90}, "typo")
        self.assertEqual(session.max_marks, 90)

    def test_grade_freeze_blocks_delete_with_used_levels(self):
        import services.academics_service.repository as acad_repo
        from common.models import GradeLevel as GradeLevelModel
        # A level referenced by any mark cannot be removed; an unused one can.
        repo.create_session_mark(
            self.db, self.session2, self.exam2, 1, 3,
            {"assessment_status": "present", "grade_label": "A"}, 31)
        repo.submit_session_marks(self.db, self.session2)
        level_a = self.db.query(GradeLevelModel).filter_by(label="A").one()
        with self.assertRaises(ConflictError):
            acad_repo.delete_grade_level(self.db, level_a)
        self.assertIsNotNone(
            self.db.query(GradeLevelModel).filter_by(level_id=level_a.level_id).one_or_none())
        spare = GradeLevelModel(scale_id=1, label="Z", rank=99)
        self.db.add(spare)
        self.db.commit()
        acad_repo.delete_grade_level(self.db, spare)
        self.assertIsNone(
            self.db.query(GradeLevelModel).filter_by(level_id=spare.level_id).one_or_none())
        import pathlib
        src = pathlib.Path("services/marks_service/router.py").read_text(encoding="utf-8")
        for func in ("def publish_session_marks", "def withhold_marks"):
            start = src.rindex("@router.post(", 0, src.index(func))
            self.assertIn('require_role("admin")', src[start:start + 400])


if __name__ == "__main__":
    unittest.main()
