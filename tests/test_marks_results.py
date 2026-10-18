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
from common.exceptions import AppError, ForbiddenError, NotFoundError
from common.models import (
    Base, AcademicPeriod, AcademicYear, Examination, ExaminationSession, ExaminationType,
    GradeLevel, GradeScale, Mark, Parent, ParentStudent, School, Student, Subject,
    TeacherClassSubject,
)
import services.marks_service.repository as repo

TABLES = [
    "schools", "academic_years", "academic_periods", "examination_types", "examinations",
    "examination_sessions",
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
            School(school_id=1, name="Green Valley", address="A", pincode="1",
                   city="C", state="S", primary_contact="P", primary_email="e@x.io",
                   examinations_enabled=True),
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

    def test_parent_list_carries_subject_name_and_configured_grade(self):
        """The flat student route returns subject names from the catalog and
        the school's own scale label — never an invented threshold."""
        router = _load_marks_router()
        self._publish(1, 1, score=42)          # marks exam, scale_id NULL
        self._publish(3, 1, grade_label="A")   # grade exam, pinned scale
        out = router.get_student_marks(1, None, self.db, self.parent225)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(r["subject_name"] == "Maths" for r in out))
        grade_rows = [r for r in out if r["grade_level_id"] is not None]
        self.assertEqual(len(grade_rows), 1)
        self.assertEqual(grade_rows[0]["grade"], {"label": "A", "description": None})
        mark_rows = [r for r in out if r["grade_level_id"] is None]
        self.assertEqual(len(mark_rows), 1)
        self.assertIsNone(mark_rows[0]["grade"])  # no scale → no invented band
        self.assertEqual(mark_rows[0]["score"], 42)
        self.assertEqual(mark_rows[0]["updated_by_user"], 31)  # session marks carry user, not staff slot

    def test_student_results_filters_by_period(self):
        """period_id narrows results to examinations scheduled in one term."""
        self.db.add_all([
            AcademicPeriod(period_id=1, year_id=1, name="Term 1",
                           start_date=date(2026, 6, 1), end_date=date(2026, 10, 15),
                           is_active=False),
            AcademicPeriod(period_id=2, year_id=1, name="Term 2",
                           start_date=date(2026, 10, 16), end_date=date(2027, 3, 31),
                           is_active=True),
            Examination(exam_id=3, school_id=1, type_id=1, year_id=1, period_id=2,
                        name="Term 2 Weekly", result_format_expectation="marks",
                        status="published"),
        ])
        self.db.commit()
        self.exam1.period_id = 1
        self.db.commit()
        self._publish(1, 1, score=42)
        _session(self.db, 7, 3, date(2027, 3, 10))
        self._publish(7, 1, score=90)

        term1 = repo.student_results(self.db, 1, 1, year_id=1, period_id=1)
        self.assertEqual([e["exam_id"] for e in term1["examinations"]], [1])
        self.assertEqual(term1["aggregate"]["scope"], "term")
        term2 = repo.student_results(self.db, 1, 1, year_id=1, period_id=2)
        self.assertEqual([e["exam_id"] for e in term2["examinations"]], [3])
        everything = repo.student_results(self.db, 1, 1, year_id=1)
        self.assertEqual(
            [e["exam_id"] for e in everything["examinations"]], [1, 3]
        )
        self.assertEqual(everything["aggregate"]["scope"], "year")
        # Router-level: the new kwarg sits after current_user, so the
        # established positional call shape keeps working untouched.
        router = _load_marks_router()
        via_router = router.get_student_results(
            1, None, None, None, self.db, self.parent225, period_id=2
        )
        self.assertEqual(
            [e["exam_id"] for e in via_router["examinations"]], [3]
        )
        positional = router.get_student_results(1, None, None, None, self.db, self.parent225)
        self.assertEqual(
            [e["exam_id"] for e in positional["examinations"]], [1, 3]
        )

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

    # ---- incomplete-flag semantics (B1) ----
    def test_grade_exam_complete_when_all_rows_present(self):
        self._publish(3, 1, grade_label="A")
        out = repo.student_results(self.db, 1, 1, year_id=1)
        entry = out["examinations"][0]
        self.assertFalse(entry["incomplete"])
        self.assertEqual(entry["grade_distribution"], {"A": 1})

    def test_grade_exam_incomplete_when_any_row_not_sat(self):
        self._publish(3, 1, grade_label="A")
        self._publish(3, 2, assessment_status="absent")
        out = repo.student_results(self.db, 1, 2, year_id=1)
        entry = out["examinations"][0]
        self.assertTrue(entry["incomplete"])
        self.assertEqual(entry["subjects_total"], 1)

    def test_empty_rows_are_incomplete(self):
        self.assertTrue(repo._summarize_marks_rows([])["incomplete"])

    # ---- entry view for staff grids (B2) ----
    def test_entry_view_shows_draft_and_submitted_states(self):
        s1 = self.db.get(ExaminationSession, 1)
        repo.create_session_mark(self.db, s1, self.exam1, 1, 3,
                                 {"assessment_status": "present", "score": 42}, 31)
        repo.submit_session_marks(self.db, s1)  # student 1 -> submitted
        repo.create_session_mark(self.db, s1, self.exam1, 2, 3,
                                 {"assessment_status": "present", "score": 50}, 31)  # draft
        entry = repo.examination_results(self.db, 1, self.exam1, view="entry")
        self.assertEqual([r["student_id"] for r in entry["results"]], [1, 2])
        self.assertEqual(entry["missing_student_ids"], [])
        states = {r["student_id"]: r["subjects"][0]["workflow_status"]
                  for r in entry["results"]}
        self.assertEqual(states, {1: "submitted", 2: "draft"})
        pub = repo.examination_results(self.db, 1, self.exam1)
        self.assertEqual(pub["results"], [])
        self.assertEqual(pub["missing_student_ids"], [1, 2])

    def test_entry_view_includes_withheld_published_rows(self):
        mark = self._publish(1, 1, score=42)
        repo.withhold_marks(self.db, [mark.mark_id], "Review", 11)
        pub = repo.examination_results(self.db, 1, self.exam1)
        self.assertEqual(pub["results"], [])
        self.assertEqual(pub["missing_student_ids"], [1, 2])
        entry = repo.examination_results(self.db, 1, self.exam1, view="entry")
        row = entry["results"][0]["subjects"][0]
        self.assertTrue(row["is_withheld"])
        # Student 2 never entered → entry view still reports them missing.
        self.assertEqual(entry["missing_student_ids"], [2])

    def test_unknown_view_rejected(self):
        with self.assertRaises(AppError):
            repo.examination_results(self.db, 1, self.exam1, view="bogus")

    def test_entry_view_reachable_at_router(self):
        router = _load_marks_router()
        self._publish(1, 1, score=42)
        out = router.get_examination_results(1, None, self.db, self.admin1, view="entry")
        self.assertEqual(len(out["results"]), 1)
        with self.assertRaises(AppError):
            router.get_examination_results(1, None, self.db, self.admin1, view="bogus")

    def test_entry_view_for_teacher_scoped_to_own_sessions(self):
        # Exam 1 gains a second slot (class 2 / subject 4) taught by staff 8
        # only; staff 7 teaches the class-1 slots. Drafts exist on both.
        router = _load_marks_router()
        self.db.add_all([
            Subject(subject_id=4, school_id=1, name="Science"),
            Student(student_id=4, school_id=1, class_id=2, name="Two Four", admission_no="A4"),
            Student(student_id=5, school_id=1, class_id=2, name="Five Five", admission_no="A5"),
            ExaminationSession(session_id=4, exam_id=1, class_id=2, subject_id=4,
                               date=date(2026, 12, 18), start_time=time(9, 0),
                               end_time=time(12, 0), max_marks=100, status="scheduled"),
            TeacherClassSubject(staff_id=8, class_id=2, subject_id=4),
        ])
        self.db.commit()
        repo.create_session_mark(
            self.db, self.db.get(ExaminationSession, 1), self.exam1, 1, 3,
            {"assessment_status": "present", "score": 42}, 31)  # staff 7's slot
        repo.create_session_mark(
            self.db, self.db.get(ExaminationSession, 4), self.exam1, 4, 4,
            {"assessment_status": "present", "score": 55}, 31)  # staff 8's slot
        teacher8 = CurrentUser(user_id=32, role="teacher", school_id=1, linked_person_id=8)

        # Staff 8: only their own slot's rows; class-2 classmate still missing.
        entry8 = router.get_examination_results(1, None, self.db, teacher8, view="entry")
        self.assertEqual([r["student_id"] for r in entry8["results"]], [4])
        self.assertEqual(
            {s["examination_session_id"] for r in entry8["results"] for s in r["subjects"]},
            {4},
        )
        self.assertEqual(entry8["missing_student_ids"], [5])
        # Staff 7 never sees class 2's draft either.
        entry7 = router.get_examination_results(1, None, self.db, self.teacher7, view="entry")
        self.assertEqual([r["student_id"] for r in entry7["results"]], [1])
        self.assertEqual(entry7["missing_student_ids"], [2])
        # Admins keep the whole-examination picture.
        admin = router.get_examination_results(1, None, self.db, self.admin1, view="entry")
        self.assertEqual([r["student_id"] for r in admin["results"]], [1, 4])
        # The published view's exam-wide contract for teachers is unchanged.
        pub7 = router.get_examination_results(1, None, self.db, self.teacher7)
        self.assertEqual(pub7["results"], [])  # nothing published yet


if __name__ == "__main__":
    unittest.main()
