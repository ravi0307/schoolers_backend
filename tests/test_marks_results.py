"""
Functional tests for results aggregation (Phase 6).

Covers mixed-maximum totals, absent/missing/withheld handling, grade
distributions without invented numbers, legacy-term grouping with no
double-counting, grade-band mapping from pinned scales, and the role
scoping on both results endpoints.
"""
import sys
import unittest
from datetime import date, time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.dependencies import CurrentUser
from common.exceptions import ForbiddenError, NotFoundError
from common.models import (
    Base, AcademicYear, Examination, ExaminationSession, ExaminationType,
    GradeLevel, GradeScale, Mark, Parent, ParentStudent, Student, Subject,
    TeacherClassSubject,
)
import services.marks_service.repository as repo

TABLES = [
    "academic_years", "examination_types", "examinations", "examination_sessions",
    "grade_scales", "grade_levels", "students", "subjects", "parents",
    "parent_student", "teacher_class_subjects", "marks",
    "class_subjects", "student_subjects",
]


def _load_marks_router():
    sys.path.insert(0, "services/marks_service")
    try:
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)
        from services.marks_service import router as marks_router
        return marks_router
    finally:
        sys.path.pop(0)
        for mod in ("repository", "schemas", "bulk"):
            sys.modules.pop(mod, None)


def _session(db, session_id, exam_id, day, max_marks=100, status="scheduled"):
    db.add(ExaminationSession(
        session_id=session_id, exam_id=exam_id, class_id=1, subject_id=3,
        date=day, start_time=time(9, 0), end_time=time(12, 0),
        max_marks=max_marks, status=status))
    db.commit()
    return db.query(ExaminationSession).filter_by(session_id=session_id).one()


class MarksResultsTests(unittest.TestCase):
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
            AcademicYear(year_id=1, school_id=1, name="2026-27",
                         start_date=date(2026, 6, 1), end_date=date(2027, 5, 31)),
            ExaminationType(type_id=1, school_id=1, name="Half-Yearly", default_max_marks=100),
            Examination(exam_id=1, school_id=1, type_id=1, year_id=1,
                        name="Half-Yearly", result_format_expectation="marks", status="published"),
            Examination(exam_id=2, school_id=1, type_id=1, year_id=1,
                        name="Half-Yearly Grades", result_format_expectation="grade",
                        grade_scale_id=1, status="published"),
            GradeScale(scale_id=1, school_id=1, name="Alpha", version=1),
            GradeLevel(level_id=1, scale_id=1, label="A", rank=1, min_score=80, max_score=100),
            GradeLevel(level_id=2, scale_id=1, label="B", rank=2, min_score=60, max_score=79),
            ExaminationSession(session_id=1, exam_id=1, class_id=1, subject_id=3,
                               date=date(2026, 12, 15), start_time=time(9, 0),
                               end_time=time(12, 0), max_marks=50, status="scheduled"),
            ExaminationSession(session_id=2, exam_id=1, class_id=1, subject_id=3,
                               date=date(2026, 12, 16), start_time=time(9, 0),
                               end_time=time(12, 0), max_marks=100, status="scheduled"),
            ExaminationSession(session_id=3, exam_id=2, class_id=1, subject_id=3,
                               date=date(2026, 12, 17), start_time=time(9, 0),
                               end_time=time(12, 0), max_marks=100, status="scheduled"),
            Student(student_id=1, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            Student(student_id=2, school_id=1, class_id=1, admission_no="A2", name="Dev"),
            Student(student_id=9, school_id=2, class_id=9, admission_no="B1", name="Zara"),
            Subject(subject_id=3, school_id=1, name="Maths"),
            Parent(parent_id=225, school_id=1, name="Mrs Sinha", phone="9000000001"),
            ParentStudent(parent_id=225, student_id=1),
            TeacherClassSubject(staff_id=7, class_id=1, subject_id=3),
        ])
        self.db.commit()
        self.exam1 = self.db.get(Examination, 1)
        self.exam2 = self.db.get(Examination, 2)
        self.admin1 = CurrentUser(user_id=11, role="admin", school_id=1, linked_person_id=None)
        self.parent225 = CurrentUser(user_id=21, role="parent", school_id=1, linked_person_id=225)
        self.teacher7 = CurrentUser(user_id=31, role="teacher", school_id=1, linked_person_id=7)
        self.master = CurrentUser(user_id=1, role="master", school_id=None, linked_person_id=None)

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def _publish(self, session_id, student_id, **cols):
        session = self.db.get(ExaminationSession, session_id)
        exam = self.db.get(Examination, session.exam_id)
        mark = repo.create_session_mark(
            self.db, session, exam, student_id, 3,
            {"assessment_status": "present", **cols}, 31)
        repo.submit_session_marks(self.db, session)
        repo.publish_session_marks(self.db, session)
        return mark

    def test_mixed_maxima_sum_not_averaged(self):
        # 42/50 + 78/100 aggregates as 120/150 = 80%, never averaged percents.
        self._publish(1, 1, score=42)
        self._publish(2, 1, score=78)
        out = repo.student_results(self.db, 1, 1, year_id=1)
        self.assertEqual(len(out["examinations"]), 1)
        entry = out["examinations"][0]
        self.assertEqual((entry["obtained"], entry["possible"]), (120, 150))
        self.assertEqual(entry["percentage"], 80.0)
        self.assertFalse(entry["incomplete"])
        self.assertEqual(out["aggregate"]["percentage"], 80.0)

    def test_absent_excluded_with_incomplete_flag(self):
        self._publish(1, 1, score=42)
        repo.create_session_mark(
            self.db, self.db.get(ExaminationSession, 2), self.exam1, 1, 3,
            {"assessment_status": "absent"}, 31)
        repo.submit_session_marks(self.db, self.db.get(ExaminationSession, 2))
        repo.publish_session_marks(self.db, self.db.get(ExaminationSession, 2))
        out = repo.student_results(self.db, 1, 1, year_id=1)
        entry = out["examinations"][0]
        self.assertEqual((entry["obtained"], entry["possible"]), (42, 50))
        self.assertTrue(entry["incomplete"])
        self.assertEqual(len(entry["subjects"]), 2)

    def test_withheld_rows_are_invisible(self):
        mark = self._publish(1, 1, score=42)
        repo.withhold_marks(self.db, [mark.mark_id], "Review", 11)
        out = repo.student_results(self.db, 1, 1, year_id=1)
        self.assertEqual(out["examinations"], [])
        self.assertIsNone(out["aggregate"])

    def test_grade_exam_yields_distribution_not_numbers(self):
        self._publish(3, 1, grade_label="A")
        out = repo.student_results(self.db, 1, 1, year_id=1)
        entry = out["examinations"][0]
        self.assertEqual(entry["grade_distribution"], {"A": 1})
        self.assertNotIn("percentage", entry)
        self.assertEqual(out["aggregate"]["scope"], "grade-only")

    def test_legacy_terms_grouped_without_double_count(self):
        repo.upsert_mark(self.db, 1, 3, "Term 1", 70, 7)
        self._publish(1, 1, score=42)
        out = repo.student_results(self.db, 1, 1, year_id=1)
        self.assertEqual(len(out["legacy_terms"]), 1)
        self.assertEqual(out["legacy_terms"][0]["term"], "Term 1")
        self.assertEqual(out["legacy_terms"][0]["obtained"], 70)
        # The year aggregate sees only session rows, never the legacy row.
        self.assertEqual((out["aggregate"]["obtained"], out["aggregate"]["possible"]), (42, 50))

    def test_grade_band_mapping_from_pinned_scale(self):
        self._publish(1, 1, score=42)
        out = repo.student_results(self.db, 1, 1, year_id=1)
        # No scale pinned: no grade is invented.
        self.assertNotIn("grade", out["examinations"][0])
        # Pin a banded scale: 42/50 = 84% falls in the A band.
        self.exam1.grade_scale_id = 1
        self.db.commit()
        out = repo.student_results(self.db, 1, 1, year_id=1)
        self.assertEqual(out["examinations"][0].get("grade"), {"label": "A", "description": None})

    def test_examination_results_with_missing_list(self):
        self._publish(1, 1, score=42)
        out = repo.examination_results(self.db, 1, self.exam1)
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["obtained"], 42)
        self.assertEqual(out["missing_student_ids"], [2])

    def test_results_scoping_matrix(self):
        router = _load_marks_router()
        self._publish(1, 1, score=42)
        # Parent: own child ok, others forbidden (uniform for unknown ids).
        self.assertEqual(len(router.get_student_results(1, None, None, None, self.db, self.parent225)["examinations"]), 1)
        for probe in (2, 9, 4242):
            with self.assertRaises(ForbiddenError):
                router.get_student_results(probe, None, None, None, self.db, self.parent225)
        # Teacher outside the exam: forbidden.
        with self.assertRaises(ForbiddenError):
            router.get_examination_results(1, None, self.db, CurrentUser(
                user_id=99, role="teacher", school_id=1, linked_person_id=99))
        self.assertEqual(
            len(router.get_examination_results(1, None, self.db, self.teacher7)["results"]), 1)
        # Master: explicit school required, cross-school is 404.
        with self.assertRaises(ForbiddenError):
            router.get_student_results(1, None, None, None, self.db, self.master)
        with self.assertRaises(NotFoundError):
            router.get_student_results(9, None, None, 1, self.db, self.master)
        self.assertEqual(
            len(router.get_student_results(1, None, None, 1, self.db, self.master)["examinations"]), 1)


if __name__ == "__main__":
    unittest.main()
