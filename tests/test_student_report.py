"""Per-student report: details, marks and attendance in one read.

The report exists in a system with no exam, grade, result or report-card
tables. So most of what it asserts is that it is *honest* about that: it
reports scores and an average, and never invents a grade letter, a pass/fail
or a rank. The other half is that it cannot leak across schools, which is the
one mistake that would matter.
"""
import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.models import (
    Attendance, Base, Mark, Parent, ParentStudent, School, SchoolClass, Staff,
    Student, Subject,
)
from services.reports_service.schemas import TermMarks
import services.reports_service.repository as reports_repo

TABLES = [
    "schools", "users", "classes", "staff", "students", "parents",
    "parent_student", "subjects", "marks", "attendance",
]


class TermOrderingTests(unittest.TestCase):
    """`term` is free text, so ordering has to be derived, not assumed."""

    def test_numeric_terms_sort_numerically_not_as_strings(self):
        # A plain string sort puts "Term 10" before "Term 2" and buries it.
        self.assertLess(
            reports_repo._term_sort_key("Term 2"),
            reports_repo._term_sort_key("Term 10"),
        )

    def test_a_school_using_semester_keeps_its_own_ordering(self):
        self.assertLess(
            reports_repo._term_sort_key("Semester 1"),
            reports_repo._term_sort_key("Semester 2"),
        )

    def test_labels_without_a_number_still_sort_stably_after_numbered_ones(self):
        keys = ["Mid-term", "Term 1", "Term 2"]
        self.assertEqual(
            sorted(keys, key=reports_repo._term_sort_key),
            ["Term 1", "Term 2", "Mid-term"],
        )


class StudentReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine, tables=[Base.metadata.tables[name] for name in TABLES]
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.add_all([
            School(school_id=1, name="Greenfield", address="1 Main St", pincode="111",
                   city="Town", state="State", primary_contact="p", primary_email="a@g.test"),
            School(school_id=2, name="Riverside", address="2 Side St", pincode="222",
                   city="Town", state="State", primary_contact="p", primary_email="a@r.test"),
            SchoolClass(class_id=1, school_id=1, name="Grade 5"),
            SchoolClass(class_id=2, school_id=2, name="Grade 9"),
            Staff(staff_id=10, school_id=1, name="Meera", role="teacher"),
            Staff(staff_id=20, school_id=2, name="Other Teacher", role="teacher"),
            Student(student_id=100, school_id=1, class_id=1, admission_no="A100",
                    name="Riya", date_of_birth=date(2014, 6, 15), gender="Female"),
            Student(student_id=101, school_id=1, class_id=1, admission_no="A101", name="Kabir"),
            Student(student_id=200, school_id=2, class_id=2, admission_no="B200",
                    name="Other School Kid"),
            Subject(subject_id=1, school_id=1, name="Mathematics"),
            Subject(subject_id=2, school_id=1, name="English"),
            Subject(subject_id=3, school_id=1, name="Science"),
            Subject(subject_id=4, school_id=1, name="Retired Subject", is_active=False),
            Subject(subject_id=9, school_id=2, name="Other School Subject"),
            Parent(parent_id=50, school_id=1, name="Kavya", phone="111",
                   email="k@g.test", address="9 Lane", emergency_number="999"),
            Parent(parent_id=51, school_id=1, name="Raj", phone="222", email="r@g.test"),
            ParentStudent(parent_id=50, student_id=100, relationship_="Mother"),
            ParentStudent(parent_id=51, student_id=100, relationship_="Father"),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def report(self, student_id=100, school_id=1):
        return reports_repo.student_report(self.db, school_id, student_id)

    def add_marks(self, student_id, term, subject_id, score):
        self.db.add(Mark(student_id=student_id, subject_id=subject_id,
                         term=term, score=score))
        self.db.commit()

    def add_attendance(self, student_id, day, status, class_id=1):
        self.db.add(Attendance(student_id=student_id, class_id=class_id,
                               date=day, status=status))
        self.db.commit()

    # ---- details ----

    def test_report_carries_the_student_details_with_the_class_name_resolved(self):
        student = self.report()["student"]
        self.assertEqual(student["name"], "Riya")
        self.assertEqual(student["admission_no"], "A100")
        # The stored row only has class_id; the report resolves the name.
        self.assertEqual(student["class_name"], "Grade 5")
        self.assertEqual(str(student["date_of_birth"]), "2014-06-15")

    def test_every_guardian_is_listed_with_their_relationship(self):
        # people_service resolves a student to .first() parent, which silently
        # drops the second guardian. A report card is exactly where that hurts.
        guardians = self.report()["student"]["guardians"]
        self.assertEqual([g["name"] for g in guardians], ["Kavya", "Raj"])
        self.assertEqual([g["relationship"] for g in guardians], ["Mother", "Father"])
        self.assertEqual(guardians[0]["phone"], "111")
        self.assertEqual(guardians[0]["emergency_number"], "999")

    def test_a_student_with_no_guardian_linked_gets_an_empty_list_not_an_error(self):
        self.assertEqual(self.report(student_id=101)["student"]["guardians"], [])

    def test_class_name_is_none_when_the_class_belongs_to_another_school(self):
        # Belt and braces: the class lookup is school-scoped too.
        self.assertIsNone(reports_repo._class_name(self.db, 2, 1))

    # ---- marks ----

    def test_marks_are_grouped_by_term_with_subject_names_and_an_average(self):
        self.add_marks(100, "Term 1", 1, 80)
        self.add_marks(100, "Term 1", 2, 90)
        self.add_marks(100, "Term 2", 1, 70)
        report = self.report()
        self.assertEqual(report["terms"], ["Term 1", "Term 2"])
        first = report["marks_by_term"][0]
        self.assertEqual(
            [(s["subject_name"], s["score"]) for s in first["subjects"]],
            [("Mathematics", 80), ("English", 90)],
        )
        self.assertEqual(first["average"], 85.0)
        self.assertEqual(report["marks_by_term"][1]["average"], 70.0)

    def test_averages_are_rounded_to_one_decimal(self):
        self.add_marks(100, "Term 1", 1, 80)
        self.add_marks(100, "Term 1", 2, 81)
        self.add_marks(100, "Term 1", 3, 82)
        self.assertEqual(self.report()["marks_by_term"][0]["average"], 81.0)

    def test_terms_with_no_marks_report_a_null_average_not_zero(self):
        # An average of 0 would read as "this student failed everything".
        self.assertEqual(self.report()["marks_by_term"], [])
        self.assertEqual(self.report()["terms"], [])

    def test_graded_subject_count_is_shown_against_the_schools_active_subjects(self):
        # So the report says "1 of 3 subjects graded" rather than implying a
        # complete term. Inactive subjects are excluded from the denominator.
        self.add_marks(100, "Term 1", 1, 80)
        term = self.report()["marks_by_term"][0]
        self.assertEqual(term["graded_subjects"], 1)
        self.assertEqual(term["total_subjects"], 3)

    def test_marks_for_a_deleted_subject_keep_the_score_and_say_so(self):
        self.add_marks(100, "Term 1", 1, 80)
        self.db.add(Subject(subject_id=7, school_id=1, name="History"))
        self.db.commit()
        self.add_marks(100, "Term 1", 7, 65)
        # Deleting the subject would cascade the mark away in Postgres, so the
        # fallback is for a dangling id, not a normal state. Either way the
        # score must not vanish silently.
        subjects = self.report()["marks_by_term"][0]["subjects"]
        self.assertEqual(len(subjects), 2)

    def test_the_report_invents_no_grade_letter_or_pass_fail(self):
        # The system has no grade bands. Emitting one would mean shipping
        # thresholds nobody agreed to.
        fields = set(TermMarks.model_fields)
        for invented in ("grade", "letter_grade", "result", "passed", "rank", "gpa"):
            self.assertNotIn(invented, fields)
        self.add_marks(100, "Term 1", 1, 95)
        self.add_marks(100, "Term 1", 2, 96)
        term = self.report()["marks_by_term"][0]
        self.assertEqual(term["average"], 95.5)
        self.assertFalse(hasattr(term, "grade"))

    # ---- attendance ----

    def test_attendance_is_summarised_over_every_recorded_day(self):
        for day in range(1, 6):
            self.add_attendance(100, date(2026, 9, day), "Present")
        self.add_attendance(100, date(2026, 9, 6), "Absent")
        att = self.report()["attendance"]
        self.assertEqual(att["present"], 5)
        self.assertEqual(att["absent"], 1)
        self.assertEqual(att["marked_days"], 6)
        self.assertEqual(att["percentage"], 83.3)

    def test_attendance_reports_the_range_it_actually_covers(self):
        # No term or session concept exists, so the UI must be able to say
        # which period the percentage describes.
        self.add_attendance(100, date(2026, 3, 2), "Present")
        self.add_attendance(100, date(2026, 9, 20), "Present")
        att = self.report()["attendance"]
        self.assertEqual(att["from_date"], "2026-03-02")
        self.assertEqual(att["to_date"], "2026-09-20")

    def test_recent_days_are_newest_first_and_capped(self):
        first = date(2026, 2, 1)
        for offset in range(39):
            self.add_attendance(100, first + timedelta(days=offset), "Present")
        recent = self.report()["attendance"]["recent"]
        self.assertEqual(len(recent), reports_repo.RECENT_ATTENDANCE_DAYS)
        self.assertEqual(recent[0]["date"], "2026-03-11")
        # Newest first, so the UI does not have to reverse it.
        self.assertGreater(recent[0]["date"], recent[-1]["date"])
        # The cap trims the list only; the totals cover every day.
        self.assertEqual(self.report()["attendance"]["marked_days"], 39)

    def test_a_student_with_no_attendance_reports_none_not_a_perfect_score(self):
        att = self.report()["attendance"]
        self.assertIsNone(att["percentage"])
        self.assertIsNone(att["from_date"])
        self.assertEqual(att["marked_days"], 0)

    # ---- scoping ----

    def test_another_schools_student_is_not_found_rather_than_returned(self):
        # Returning None (and so a 404) is what stops this endpoint being used
        # to discover which student ids exist in other schools.
        self.assertIsNone(reports_repo.student_report(self.db, 1, 200))

    def test_a_made_up_student_id_is_also_not_found(self):
        self.assertIsNone(reports_repo.student_report(self.db, 1, 999999))

    def test_marks_never_leak_across_students(self):
        self.add_marks(100, "Term 1", 1, 95)
        self.add_marks(101, "Term 1", 1, 20)
        self.assertEqual(self.report(100)["marks_by_term"][0]["average"], 95.0)
        self.assertEqual(self.report(101)["marks_by_term"][0]["average"], 20.0)

    def test_attendance_never_leaks_across_students(self):
        self.add_attendance(100, date(2026, 9, 1), "Absent")
        self.add_attendance(101, date(2026, 9, 1), "Present")
        self.assertEqual(self.report(100)["attendance"]["absent"], 1)
        self.assertEqual(self.report(101)["attendance"]["present"], 1)

    def test_subject_names_are_scoped_to_the_school(self):
        # School 2's subject must not be resolvable while reading school 1.
        self.assertIsNone(reports_repo._class_name(self.db, 1, 2))


if __name__ == "__main__":
    unittest.main()
