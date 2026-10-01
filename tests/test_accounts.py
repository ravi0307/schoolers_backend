"""Accounts: six-month salary and fee sheets.

Covers the parts that are easy to get wrong and expensive in a school:
the month window maths, that unpaid people still appear, that a second
payment for the same month overwrites rather than double-counts, and that
one school can never record money against another school's people.
"""
import importlib
import unittest
from datetime import date

from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.exceptions import AppError, NotFoundError
from common.models import Base, School, SchoolClass, Staff, Student, StudentFee, User
import services.accounts_service.repository as accounts_repo

TABLES = [
    "schools", "users", "classes", "staff", "students",
    "staff_salaries", "student_fees",
]


class MonthWindowTests(unittest.TestCase):
    def test_window_is_six_months_oldest_first_ending_this_month(self):
        self.assertEqual(
            accounts_repo.recent_months(6, date(2026, 9, 15)),
            ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"],
        )

    def test_window_rolls_back_over_a_year_boundary(self):
        self.assertEqual(
            accounts_repo.recent_months(6, date(2026, 2, 3)),
            ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"],
        )

    def test_window_rolls_forward_over_a_year_boundary(self):
        self.assertEqual(
            accounts_repo.recent_months(6, date(2027, 11, 30)),
            ["2027-06", "2027-07", "2027-08", "2027-09", "2027-10", "2027-11"],
        )

    def test_single_month_window_is_the_current_month(self):
        self.assertEqual(accounts_repo.recent_months(1, date(2026, 9, 15)), ["2026-09"])

    def test_default_window_is_six_months(self):
        self.assertEqual(accounts_repo.DEFAULT_MONTHS, 6)
        self.assertEqual(len(accounts_repo.recent_months()), 6)


class AccountsSheetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine, tables=[Base.metadata.tables[name] for name in TABLES]
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.add_all(
            [
                School(school_id=1, name="Greenfield", address="1 Main St", pincode="111",
                       city="Town", state="State", primary_contact="p", primary_email="a@g.test"),
                School(school_id=2, name="Riverside", address="2 Side St", pincode="222",
                       city="Town", state="State", primary_contact="p", primary_email="a@r.test"),
                User(user_id=1, role="admin", school_id=1, email="a@greenfield.test",
                     username="admin1", password_hash="x"),
                User(user_id=2, role="admin", school_id=2, email="a@riverside.test",
                     username="admin2", password_hash="x"),
                SchoolClass(class_id=1, school_id=1, name="Grade 5"),
                SchoolClass(class_id=2, school_id=2, name="Grade 9"),
                Staff(staff_id=10, school_id=1, name="Meera", role="teacher", role_title="Senior Teacher"),
                Staff(staff_id=11, school_id=1, name="Arun", role="driver", role_title="Driver"),
                Staff(staff_id=12, school_id=1, name="Retired", role="teacher", is_active=False),
                Staff(staff_id=20, school_id=2, name="Other School Teacher", role="teacher"),
                Student(student_id=100, school_id=1, class_id=1, admission_no="A100", name="Riya"),
                Student(student_id=101, school_id=1, class_id=1, admission_no="A101", name="Kabir"),
                Student(student_id=200, school_id=2, class_id=2, admission_no="B200", name="Other School Kid"),
            ]
        )
        self.db.commit()
        self.months = accounts_repo.recent_months(6, date(2026, 9, 15))

    def tearDown(self):
        self.db.close()

    # ---- salaries ----

    def test_salary_sheet_lists_every_active_staff_even_with_no_payments(self):
        sheet = accounts_repo.salary_sheet(self.db, 1, self.months)
        names = [r["staff_name"] for r in sheet["rows"]]
        self.assertEqual(names, ["Arun", "Meera"])  # ordered by name, inactive excluded
        self.assertEqual(sheet["total_paid"], 0)

    def test_salary_sheet_reports_paid_and_unpaid_months(self):
        accounts_repo.record_salary(self.db, 1, {"staff_id": 10, "month": "2026-08", "amount": 45000})
        accounts_repo.record_salary(self.db, 1, {"staff_id": 10, "month": "2026-09", "amount": 45000})
        sheet = accounts_repo.salary_sheet(self.db, 1, self.months)
        meera = next(r for r in sheet["rows"] if r["staff_id"] == 10)
        self.assertEqual(meera["amounts"], {"2026-08": 45000.0, "2026-09": 45000.0})
        self.assertEqual(meera["designation"], "Senior Teacher")
        self.assertEqual(sheet["total_paid"], 90000.0)
        # Meera missed 4 of 6 months, Arun missed all 6.
        self.assertEqual(sheet["total_outstanding_months"], 10)

    def test_second_payment_for_same_month_overwrites_and_does_not_double_count(self):
        accounts_repo.record_salary(self.db, 1, {"staff_id": 10, "month": "2026-08", "amount": 45000})
        accounts_repo.record_salary(self.db, 1, {"staff_id": 10, "month": "2026-08", "amount": 47000})
        sheet = accounts_repo.salary_sheet(self.db, 1, self.months)
        meera = next(r for r in sheet["rows"] if r["staff_id"] == 10)
        self.assertEqual(meera["amounts"], {"2026-08": 47000.0})
        self.assertEqual(sheet["total_paid"], 47000.0)

    def test_payments_outside_the_window_are_not_shown(self):
        accounts_repo.record_salary(self.db, 1, {"staff_id": 10, "month": "2026-01", "amount": 40000})
        sheet = accounts_repo.salary_sheet(self.db, 1, self.months)
        meera = next(r for r in sheet["rows"] if r["staff_id"] == 10)
        self.assertEqual(meera["amounts"], {})
        self.assertEqual(sheet["total_paid"], 0)

    def test_amount_is_stored_exactly_and_returned_as_a_number(self):
        accounts_repo.record_salary(self.db, 1, {"staff_id": 10, "month": "2026-08", "amount": 45000.55})
        sheet = accounts_repo.salary_sheet(self.db, 1, self.months)
        meera = next(r for r in sheet["rows"] if r["staff_id"] == 10)
        self.assertEqual(meera["amounts"]["2026-08"], 45000.55)

    def test_total_paid_avoids_float_accumulation_error(self):
        """Summing amounts that are not exactly representable in binary float
        (e.g. 0.1) can drift: 0.1×6 = 0.6000000000000001 in float.  The fix
        sums Decimal paise as integers, then converts once."""
        # 6 payments of 0.10 rupees each across different months.
        for month in ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"):
            accounts_repo.record_salary(
                self.db, 1,
                {"staff_id": 10, "month": month, "amount": Decimal("0.10")},
            )
        sheet = accounts_repo.salary_sheet(self.db, 1, self.months)
        # Old float sum would have produced 0.6000000000000001
        self.assertEqual(sheet["total_paid"], 0.6)

    def test_fee_total_avoids_float_accumulation_error(self):
        """Same test for student fees: 6 payments of 0.10 = 0.60 exactly."""
        for month in ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"):
            accounts_repo.record_fee(
                self.db, 1,
                {"student_id": 100, "month": month, "amount": Decimal("0.10")},
            )
        sheet = accounts_repo.fee_sheet(self.db, 1, self.months)
        riya = next(r for r in sheet["rows"] if r["student_id"] == 100)
        self.assertEqual(sheet["total_collected"], 0.6)

    def test_salary_sheet_never_shows_another_schools_staff(self):
        accounts_repo.record_salary(self.db, 1, {"staff_id": 10, "month": "2026-09", "amount": 45000})
        school2 = accounts_repo.salary_sheet(self.db, 2, self.months)
        self.assertEqual([r["staff_name"] for r in school2["rows"]], ["Other School Teacher"])
        self.assertEqual(school2["total_paid"], 0)

    def test_recording_a_salary_for_another_schools_staff_is_rejected(self):
        with self.assertRaises(NotFoundError):
            accounts_repo.record_salary(self.db, 1, {"staff_id": 20, "month": "2026-09", "amount": 1000})
        self.assertEqual(accounts_repo.salary_sheet(self.db, 2, self.months)["total_paid"], 0)

    def test_recording_a_salary_for_inactive_staff_is_rejected(self):
        with self.assertRaises(NotFoundError):
            accounts_repo.record_salary(self.db, 1, {"staff_id": 12, "month": "2026-09", "amount": 1000})

    def test_deleting_a_salary_clears_only_that_month(self):
        accounts_repo.record_salary(self.db, 1, {"staff_id": 10, "month": "2026-08", "amount": 45000})
        accounts_repo.record_salary(self.db, 1, {"staff_id": 10, "month": "2026-09", "amount": 45000})
        accounts_repo.delete_salary(self.db, 1, 10, "2026-08")
        sheet = accounts_repo.salary_sheet(self.db, 1, self.months)
        meera = next(r for r in sheet["rows"] if r["staff_id"] == 10)
        self.assertEqual(meera["amounts"], {"2026-09": 45000.0})

    def test_deleting_another_schools_salary_leaves_it_intact(self):
        accounts_repo.record_salary(self.db, 2, {"staff_id": 20, "month": "2026-09", "amount": 1000})
        accounts_repo.delete_salary(self.db, 1, 20, "2026-09")
        self.assertEqual(accounts_repo.salary_sheet(self.db, 2, self.months)["total_paid"], 1000.0)

    # ---- fees ----

    def test_fee_sheet_lists_students_with_class_and_admission_number(self):
        sheet = accounts_repo.fee_sheet(self.db, 1, self.months)
        self.assertEqual([r["student_name"] for r in sheet["rows"]], ["Kabir", "Riya"])
        riya = next(r for r in sheet["rows"] if r["student_id"] == 100)
        self.assertEqual(riya["admission_no"], "A100")
        self.assertEqual(riya["class_name"], "Grade 5")
        self.assertEqual(sheet["outstanding_count"], 2)

    def test_fee_sheet_totals_and_overwrite(self):
        accounts_repo.record_fee(self.db, 1, {"student_id": 100, "month": "2026-09", "amount": 5000})
        accounts_repo.record_fee(self.db, 1, {"student_id": 100, "month": "2026-09", "amount": 5500})
        accounts_repo.record_fee(self.db, 1, {"student_id": 101, "month": "2026-09", "amount": 5000})
        sheet = accounts_repo.fee_sheet(self.db, 1, self.months)
        self.assertEqual(sheet["total_collected"], 10500.0)
        # Both paid only 1 of 6 months.
        self.assertEqual(sheet["outstanding_count"], 2)

    def test_recording_a_fee_for_another_schools_student_is_rejected(self):
        with self.assertRaises(NotFoundError):
            accounts_repo.record_fee(self.db, 1, {"student_id": 200, "month": "2026-09", "amount": 5000})
        self.assertEqual(accounts_repo.fee_sheet(self.db, 2, self.months)["total_collected"], 0)

    def test_deleting_a_fee_clears_only_that_month(self):
        accounts_repo.record_fee(self.db, 1, {"student_id": 100, "month": "2026-08", "amount": 5000})
        accounts_repo.record_fee(self.db, 1, {"student_id": 100, "month": "2026-09", "amount": 5000})
        accounts_repo.delete_fee(self.db, 1, 100, "2026-08")
        sheet = accounts_repo.fee_sheet(self.db, 1, self.months)
        riya = next(r for r in sheet["rows"] if r["student_id"] == 100)
        self.assertEqual(riya["amounts"], {"2026-09": 5000.0})

    def test_paid_on_and_note_round_trip(self):
        accounts_repo.record_salary(
            self.db, 1,
            {"staff_id": 10, "month": "2026-09", "amount": 45000,
             "paid_on": date(2026, 9, 28), "note": "September payroll"},
        )
        row = accounts_repo.salary_sheet(self.db, 1, self.months)
        meera = next(r for r in row["rows"] if r["staff_id"] == 10)
        self.assertEqual(meera["amounts"], {"2026-09": 45000.0})
        self.assertEqual(meera["paid_on"], {"2026-09": "2026-09-28"})
        self.assertEqual(meera["notes"], {"2026-09": "September payroll"})

    def test_a_month_with_no_payment_has_no_note_to_show(self):
        # The grid renders a remark beside every month, but a remark belongs to
        # a payment. An unpaid month must not surface a stray note, and a staff
        # member with no payments at all must carry an empty map, not a missing
        # one -- the client treats absent as "nothing recorded" and the
        # difference is invisible either way, so keep the map present.
        accounts_repo.record_salary(
            self.db, 1, {"staff_id": 10, "month": "2026-09", "amount": 45000},
        )
        sheet = accounts_repo.salary_sheet(self.db, 1, self.months)
        meera = next(r for r in sheet["rows"] if r["staff_id"] == 10)
        self.assertEqual(meera["notes"], {"2026-09": None})
        arun = next(r for r in sheet["rows"] if r["staff_id"] == 11)
        self.assertEqual(arun["notes"], {})

    def test_a_remark_can_be_added_or_edited_without_touching_the_figure(self):
        # The admin corrects a remark weeks later, long after the payroll ran.
        # Re-recording the salary is how the note is stored, so the amount and
        # the paid date must both survive the edit untouched -- a remark is not
        # a second payment.
        accounts_repo.record_salary(
            self.db, 1,
            {"staff_id": 10, "month": "2026-09", "amount": 45000,
             "paid_on": date(2026, 9, 28)},
        )
        accounts_repo.record_salary(
            self.db, 1,
            {"staff_id": 10, "month": "2026-09", "amount": 45000,
             "note": "includes arrears"},
        )
        meera = next(r for r in
                     accounts_repo.salary_sheet(self.db, 1, self.months)["rows"]
                     if r["staff_id"] == 10)
        self.assertEqual(meera["notes"], {"2026-09": "includes arrears"})
        self.assertEqual(meera["amounts"], {"2026-09": 45000.0})
        self.assertEqual(meera["paid_on"], {"2026-09": "2026-09-28"})

    def test_clearing_a_remark_leaves_the_payment_in_place(self):
        # An empty remark is an edit, not a deletion: the money is still owed
        # and still shown, it just has nothing written beside it. Clearing is
        # explicit -- note: null -- because "no remark in this payload" and
        # "remove the remark" are different answers, and only one of them is a
        # request to erase something.
        accounts_repo.record_salary(
            self.db, 1,
            {"staff_id": 10, "month": "2026-09", "amount": 45000, "note": "draft"},
        )
        accounts_repo.record_salary(
            self.db, 1,
            {"staff_id": 10, "month": "2026-09", "amount": 45000, "note": None},
        )
        meera = next(r for r in
                     accounts_repo.salary_sheet(self.db, 1, self.months)["rows"]
                     if r["staff_id"] == 10)
        self.assertEqual(meera["notes"], {"2026-09": None})
        self.assertEqual(meera["amounts"], {"2026-09": 45000.0})

    def test_correcting_a_figure_says_nothing_about_the_remark(self):
        # The bug this pins: an amount edit went out without the note field, the
        # server read that as "no remark", and correcting a payment silently
        # deleted the sentence explaining it. An edit that does not mention
        # remarks must leave the remark exactly where it was.
        accounts_repo.record_salary(
            self.db, 1,
            {"staff_id": 10, "month": "2026-09", "amount": 45000,
             "note": "includes arrears", "paid_on": date(2026, 9, 28)},
        )
        accounts_repo.record_salary(
            self.db, 1, {"staff_id": 10, "month": "2026-09", "amount": 46000},
        )
        meera = next(r for r in
                     accounts_repo.salary_sheet(self.db, 1, self.months)["rows"]
                     if r["staff_id"] == 10)
        self.assertEqual(meera["notes"], {"2026-09": "includes arrears"},
                         "an amount correction must not delete the remark")
        self.assertEqual(meera["amounts"], {"2026-09": 46000.0})
        self.assertEqual(meera["paid_on"], {"2026-09": "2026-09-28"})

    def test_a_remark_can_still_be_cleared_after_such_a_correction(self):
        # Preserving must not become un-clearable: once the amount is right, an
        # explicit empty remark still wipes the old sentence.
        accounts_repo.record_salary(
            self.db, 1,
            {"staff_id": 10, "month": "2026-09", "amount": 45000, "note": "draft"},
        )
        accounts_repo.record_salary(
            self.db, 1, {"staff_id": 10, "month": "2026-09", "amount": 46000},
        )
        accounts_repo.record_salary(
            self.db, 1,
            {"staff_id": 10, "month": "2026-09", "amount": 46000, "note": None},
        )
        meera = next(r for r in
                     accounts_repo.salary_sheet(self.db, 1, self.months)["rows"]
                     if r["staff_id"] == 10)
        self.assertEqual(meera["notes"], {"2026-09": None})
        self.assertEqual(meera["amounts"], {"2026-09": 46000.0})

    def test_a_field_the_caller_never_sent_is_absent_from_the_payload(self):
        # The other half of the fix, at the boundary: `exclude_unset` is what
        # lets the repository tell "not mentioned" from "explicitly empty".
        # Without it every field is present and the preservation above cannot
        # work, so the router is pinned here rather than assumed.
        schema = importlib.import_module("services.accounts_service.schemas")
        dumped = schema.SalaryCreate(staff_id=1, month="2026-09", amount=100) \
            .model_dump(exclude_unset=True)
        self.assertNotIn("note", dumped)
        self.assertNotIn("paid_on", dumped)
        self.assertIn("amount", dumped)
        with_note = schema.SalaryCreate(
            staff_id=1, month="2026-09", amount=100, note=None,
        ).model_dump(exclude_unset=True)
        self.assertIn("note", with_note)
        self.assertIsNone(with_note["note"])

    def test_the_paid_date_travels_with_its_own_month(self):
        # paid_on is a parallel sparse map to amounts: only months that carry a
        # payment carry a date, and the two never disagree about which months
        # they cover.
        accounts_repo.record_salary(
            self.db, 1, {"staff_id": 10, "month": "2026-08", "amount": 45000,
                         "paid_on": date(2026, 8, 30)},
        )
        sheet = accounts_repo.salary_sheet(self.db, 1, self.months)
        meera = next(r for r in sheet["rows"] if r["staff_id"] == 10)
        self.assertEqual(meera["amounts"], {"2026-08": 45000.0})
        self.assertEqual(meera["paid_on"], {"2026-08": "2026-08-30"})

    def test_recording_a_payment_without_a_date_stamps_today(self):
        # The admin records "10,000 for September" and the grid shows the date
        # it was paid -- which is the day they recorded it, unless they say
        # otherwise. Leaving the date blank because none was typed would make
        # the grids permanently date-less.
        accounts_repo.record_salary(
            self.db, 1, {"staff_id": 10, "month": "2026-09", "amount": 45000}
        )
        sheet = accounts_repo.salary_sheet(self.db, 1, self.months)
        meera = next(r for r in sheet["rows"] if r["staff_id"] == 10)
        self.assertEqual(meera["paid_on"], {"2026-09": date.today().isoformat()})

    def test_editing_an_amount_keeps_the_original_paid_date(self):
        # A correction to a figure is not a second payment. Moving the paid
        # date to "today" every time someone fixes a typo would rewrite
        # history one keystroke at a time.
        accounts_repo.record_salary(
            self.db, 1, {"staff_id": 10, "month": "2026-09", "amount": 45000,
                         "paid_on": date(2026, 9, 28)},
        )
        accounts_repo.record_salary(
            self.db, 1, {"staff_id": 10, "month": "2026-09", "amount": 47000}
        )
        meera = next(r for r in
                     accounts_repo.salary_sheet(self.db, 1, self.months)["rows"]
                     if r["staff_id"] == 10)
        self.assertEqual(meera["amounts"], {"2026-09": 47000.0})
        self.assertEqual(meera["paid_on"], {"2026-09": "2026-09-28"})

    def test_explicit_paid_on_overrides_on_edit(self):
        accounts_repo.record_salary(
            self.db, 1, {"staff_id": 10, "month": "2026-09", "amount": 45000,
                         "paid_on": date(2026, 9, 28)},
        )
        accounts_repo.record_salary(
            self.db, 1, {"staff_id": 10, "month": "2026-09", "amount": 45000,
                         "paid_on": date(2026, 9, 30)},
        )
        meera = next(r for r in
                     accounts_repo.salary_sheet(self.db, 1, self.months)["rows"]
                     if r["staff_id"] == 10)
        self.assertEqual(meera["paid_on"], {"2026-09": "2026-09-30"})

    def test_fee_sheet_carries_the_paid_date_too(self):
        accounts_repo.record_fee(
            self.db, 1, {"student_id": 100, "month": "2026-09", "amount": 5000,
                         "paid_on": date(2026, 9, 5)},
        )
        sheet = accounts_repo.fee_sheet(self.db, 1, self.months)
        riya = next(r for r in sheet["rows"] if r["student_id"] == 100)
        self.assertEqual(riya["amounts"], {"2026-09": 5000.0})
        self.assertEqual(riya["paid_on"], {"2026-09": "2026-09-05"})

    def test_a_row_with_no_payments_has_no_paid_dates(self):
        sheet = accounts_repo.salary_sheet(self.db, 1, self.months)
        meera = next(r for r in sheet["rows"] if r["staff_id"] == 10)
        self.assertEqual(meera["paid_on"], {})

    def test_empty_school_returns_empty_sheets_rather_than_failing(self):
        self.db.execute(Base.metadata.tables["students"].delete())
        self.db.execute(Base.metadata.tables["staff"].delete())
        self.db.commit()
        self.assertEqual(accounts_repo.salary_sheet(self.db, 1, self.months)["rows"], [])
        self.assertEqual(accounts_repo.fee_sheet(self.db, 1, self.months)["rows"], [])


class AnchoredWindowTests(unittest.TestCase):
    """The window is anchored on its LAST month so the selector can page back.

    An unanchored window could only ever answer "the last six months", which
    would leave the admin with no previous page to look at.
    """

    def test_window_ends_on_the_anchor(self):
        self.assertEqual(
            accounts_repo.month_window(6, "2026-09"),
            ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"],
        )

    def test_paging_back_one_month_shifts_the_whole_window(self):
        # Six months ending 2026-08, not 2026-03 alone: paging must move the
        # window, not leave a gap in the middle of the grid.
        self.assertEqual(
            accounts_repo.month_window(6, "2026-08"),
            ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"],
        )

    def test_paging_back_past_a_year_boundary(self):
        self.assertEqual(
            accounts_repo.month_window(6, "2026-02"),
            ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02"],
        )

    def test_anchored_and_unanchored_agree_for_the_current_month(self):
        # recent_months(today=...) must equal month_window at that same month.
        # The seed script uses the first and the API the second; if they ever
        # disagreed the seed would quietly miss the grid's newest column.
        for anchor in ("2026-01", "2026-06", "2026-09", "2026-12"):
            year, month = int(anchor[:4]), int(anchor[5:7])
            self.assertEqual(
                accounts_repo.recent_months(6, date(year, month, 15)),
                accounts_repo.month_window(6, anchor),
                f"window disagreement at {anchor}",
            )

    def test_window_length_is_honoured(self):
        for count in (1, 3, 6, 12):
            window = accounts_repo.month_window(count, "2026-09")
            self.assertEqual(len(window), count)
            self.assertEqual(window[-1], "2026-09")
            self.assertEqual(window, sorted(window))

    def _seed_db(self):
        """A two-school fixture mirroring the sheet tests."""
        engine = create_engine("sqlite://")
        Base.metadata.create_all(
            engine, tables=[Base.metadata.tables[name] for name in TABLES]
        )
        db = sessionmaker(bind=engine, future=True)()
        db.add_all(
            [
                School(school_id=1, name="Greenfield", address="1 St", pincode="1",
                       city="T", state="S", primary_contact="p", primary_email="a@g.test"),
                School(school_id=2, name="Riverside", address="2 St", pincode="2",
                       city="T", state="S", primary_contact="p", primary_email="a@r.test"),
                SchoolClass(class_id=1, school_id=1, name="Grade 5"),
                Staff(staff_id=10, school_id=1, name="Meera", role="teacher"),
                Student(student_id=100, school_id=1, class_id=1, admission_no="A1", name="Riya"),
            ]
        )
        db.commit()
        return db

    def test_anchored_window_reads_payments_from_that_range(self):
        # The anchor has to reach the query, not just the column headers.
        db = self._seed_db()
        try:
            accounts_repo.record_salary(db, 1, {"staff_id": 10, "month": "2026-08", "amount": 100})
            accounts_repo.record_salary(db, 1, {"staff_id": 10, "month": "2026-09", "amount": 200})
            accounts_repo.record_salary(db, 1, {"staff_id": 10, "month": "2026-02", "amount": 999})

            # A window ending in March does not include August or September.
            march = accounts_repo.salary_sheet(
                db, 1, accounts_repo.month_window(6, "2026-03")
            )
            meera = next(r for r in march["rows"] if r["staff_id"] == 10)
            self.assertEqual(meera["amounts"], {"2026-02": 999.0})

            # A window ending in September includes both, and not February.
            sept = accounts_repo.salary_sheet(
                db, 1, accounts_repo.month_window(6, "2026-09")
            )
            meera = next(r for r in sept["rows"] if r["staff_id"] == 10)
            self.assertEqual(meera["amounts"], {"2026-08": 100.0, "2026-09": 200.0})
        finally:
            db.close()

    def test_paging_does_not_lose_data(self):
        # Every recorded month is reachable by some window, so an admin can
        # always find a payment they made.
        db = self._seed_db()
        try:
            for month in ("2025-11", "2026-01", "2026-04", "2026-09"):
                accounts_repo.record_salary(
                    db, 1, {"staff_id": 10, "month": month, "amount": 100}
                )
            seen = set()
            for anchor in ("2025-11", "2026-01", "2026-04", "2026-09"):
                window = accounts_repo.month_window(6, anchor)
                sheet = accounts_repo.salary_sheet(db, 1, window)
                meera = next(r for r in sheet["rows"] if r["staff_id"] == 10)
                seen.update(meera["amounts"])
            self.assertEqual(seen, {"2025-11", "2026-01", "2026-04", "2026-09"})
        finally:
            db.close()


class FeePlanTests(unittest.TestCase):
    """A deposit is one amount handed over for a period, not a monthly rate.

    The decisions that are expensive to get wrong: which months the period
    covers, that the split adds back up to the money that arrived, and that a
    deposit never quietly erases something already recorded.
    """

    def test_the_four_periods_are_one_three_six_and_twelve_months(self):
        self.assertEqual(
            accounts_repo.FEE_PLANS,
            {"monthly": 1, "quarterly": 3, "half_yearly": 6, "yearly": 12},
        )
        # Every plan has a label, so the dialog is never showing a raw key.
        for key in accounts_repo.FEE_PLANS:
            self.assertTrue(accounts_repo.FEE_PLAN_LABELS[key].strip())

    def test_a_period_covers_the_months_from_its_start_forwards(self):
        # A quarterly deposit taken in September pays for September, October
        # and November -- the term ahead -- not the three months already gone.
        self.assertEqual(
            accounts_repo.plan_months("2026-09", "quarterly"),
            ["2026-09", "2026-10", "2026-11"],
        )
        self.assertEqual(accounts_repo.plan_months("2026-09", "monthly"), ["2026-09"])
        self.assertEqual(accounts_repo.plan_months("2026-09", "half_yearly"),
                         ["2026-09", "2026-10", "2026-11", "2026-12", "2027-01", "2027-02"])
        self.assertEqual(len(accounts_repo.plan_months("2026-09", "yearly")), 12)

    def test_a_period_rolls_across_the_year_boundary(self):
        self.assertEqual(
            accounts_repo.plan_months("2026-11", "quarterly"),
            ["2026-11", "2026-12", "2027-01"],
        )
        self.assertEqual(
            accounts_repo.plan_months("2026-02", "yearly"),
            ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07",
             "2026-08", "2026-09", "2026-10", "2026-11", "2026-12", "2027-01"],
        )
        # And a period that starts in January still runs forwards, not back.
        self.assertEqual(
            accounts_repo.plan_months("2027-01", "quarterly"),
            ["2027-01", "2027-02", "2027-03"],
        )

    def test_an_unknown_period_is_rejected_by_name(self):
        # The error has to name the alternatives: "unknown plan 'fortnightly'"
        # with the list is actionable, a bare 500 is not.
        with self.assertRaises(AppError) as caught:
            accounts_repo.plan_months("2026-09", "fortnightly")
        message = str(caught.exception)
        for valid in accounts_repo.FEE_PLANS:
            self.assertIn(valid, message)
        self.assertIn("fortnightly", message)

    def test_an_even_total_splits_into_equal_months(self):
        parts = accounts_repo.split_total(36000, 12)
        self.assertEqual(parts, [3000.0] * 12)
        self.assertEqual(accounts_repo.split_total(12000, 3), [4000.0, 4000.0, 4000.0])

    def test_the_parts_add_back_up_to_the_total_exactly(self):
        # The whole point of splitting in paise: what the parent handed over is
        # what the register ends up holding. A rupee over 3 months is not a
        # clean division, and rounding it away would quietly lose or gain money.
        for total, months in ((1, 3), (10000, 12), (0.01, 12), (99.99, 6), (45000.55, 7)):
            parts = accounts_repo.split_total(total, months)
            self.assertEqual(len(parts), months)
            self.assertAlmostEqual(
                sum(parts), total, places=2,
                msg=f"{total} over {months} months lost or gained money",
            )

    def test_the_odd_paise_lands_on_the_first_month_only(self):
        # One rupee over three months: the first month carries the extra paise
        # and the rest stay clean, which is what a fee register reads like.
        parts = accounts_repo.split_total(1, 3)
        self.assertEqual(parts, [0.34, 0.33, 0.33])
        self.assertAlmostEqual(sum(parts), 1.0, places=2)

    def test_a_division_that_does_not_come_out_even_is_still_a_whole_rupee_amount(self):
        # 10,000 over 12 months is 833.33 recurring, and the leftover 4 paise
        # rides on the first month rather than being rounded away.
        parts = accounts_repo.split_total(10000, 12)
        self.assertEqual(parts[0], 833.37)
        self.assertEqual(parts[1:], [833.33] * 11)
        self.assertAlmostEqual(sum(parts), 10000.0, places=2)

    def test_a_zero_deposit_splits_to_zeros_rather_than_failing(self):
        self.assertEqual(accounts_repo.split_total(0, 6), [0.0] * 6)

    def test_a_single_month_deposit_is_the_whole_amount(self):
        self.assertEqual(accounts_repo.split_total(5400, 1), [5400.0])


class FeeDepositTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine, tables=[Base.metadata.tables[name] for name in TABLES]
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    def setUp(self):
        self.db: Session = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.add_all(
            [
                School(school_id=1, name="Greenfield", address="1 St", pincode="1",
                       city="T", state="S", primary_contact="p", primary_email="a@g.test"),
                School(school_id=2, name="Riverside", address="2 St", pincode="2",
                       city="T", state="S", primary_contact="p", primary_email="a@r.test"),
                SchoolClass(class_id=1, school_id=1, name="Grade 5"),
                SchoolClass(class_id=2, school_id=2, name="Grade 9"),
                Staff(staff_id=10, school_id=1, name="Meera", role="teacher"),
                Student(student_id=100, school_id=1, class_id=1, admission_no="A100", name="Riya"),
                Student(student_id=101, school_id=1, class_id=1, admission_no="A101", name="Kabir"),
                Student(student_id=200, school_id=2, class_id=2, admission_no="B200", name="Other Kid"),
            ]
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def deposit(self, **over):
        payload = {"student_id": 100, "start_month": "2026-09", "plan": "quarterly",
                   "amount": 12000}
        payload.update(over)
        return accounts_repo.deposit_fee(self.db, 1, payload)

    def _fees(self, student_id=100):
        return {
            r.month: r
            for r in self.db.query(StudentFee).filter(StudentFee.student_id == student_id).all()
        }

    def test_a_quarterly_deposit_fills_three_months_from_the_grid_point_of_view(self):
        result = self.deposit()
        self.assertEqual(result["created"], 3)
        self.assertEqual(result["replaced"], 0)
        self.assertEqual(result["total"], 12000.0)
        # Each covered month is an ordinary fee row, so the grid, the
        # outstanding count and any report keep reading it with no new path.
        fees = self._fees()
        self.assertEqual(sorted(fees), ["2026-09", "2026-10", "2026-11"])
        self.assertEqual([float(fees[m].amount) for m in sorted(fees)],
                         [4000.0, 4000.0, 4000.0])
        # And the sheet sees one paid month per covered month.
        sheet = accounts_repo.fee_sheet(self.db, 1, ["2026-09", "2026-10", "2026-11"])
        riya = next(r for r in sheet["rows"] if r["student_id"] == 100)
        self.assertEqual(riya["amounts"],
                         {"2026-09": 4000.0, "2026-10": 4000.0, "2026-11": 4000.0})
        self.assertEqual(sheet["total_collected"], 12000.0)
        # Kabir is the only student left short of the window, so a completed
        # quarter drops the outstanding count to exactly him.
        self.assertEqual(sheet["outstanding_count"], 1)

    def test_every_covered_month_carries_the_one_date_the_money_arrived(self):
        # The payment happened once, so all three months show the same day.
        # Stamping each month with today independently would be a different,
        # and untrue, story.
        self.deposit(paid_on=date(2026, 9, 5))
        for row in self._fees().values():
            self.assertEqual(str(row.paid_on), "2026-09-05")

    def test_a_deposit_with_no_date_is_stamped_today(self):
        self.deposit()
        for row in self._fees().values():
            self.assertEqual(str(row.paid_on), date.today().isoformat())

    def test_a_yearly_deposit_covers_twelve_months(self):
        result = self.deposit(plan="yearly", amount=60000)
        self.assertEqual(result["created"], 12)
        self.assertEqual(len(self._fees()), 12)
        self.assertEqual(sorted(self._fees())[0], "2026-09")
        self.assertEqual(sorted(self._fees())[-1], "2027-08")
        self.assertTrue(all(float(r.amount) == 5000.0 for r in self._fees().values()))

    def test_a_deposit_reports_the_months_it_will_replace_instead_of_hiding_them(self):
        accounts_repo.record_fee(self.db, 1, {"student_id": 100, "month": "2026-10",
                                              "amount": 3000})
        result = self.deposit()
        self.assertEqual(result["replaced"], 1)
        self.assertEqual(result["created"], 2)
        replaced = [m for m in result["months"] if m["replaced"]]
        self.assertEqual([m["month"] for m in replaced], ["2026-10"])
        # It really is replaced: the deposit's share wins, as it would for a
        # correction made by hand.
        self.assertEqual(float(self._fees()["2026-10"].amount), 4000.0)
        self.assertEqual(len(self._fees()), 3, "a replacement is not a second row")

    def test_a_deposit_keeps_a_months_own_remark_unless_it_carries_one(self):
        # A yearly payment is a statement about the period. It is not a licence
        # to erase the note someone wrote about one particular month.
        accounts_repo.record_fee(self.db, 1, {"student_id": 100, "month": "2026-10",
                                              "amount": 3000, "note": "paid in cash"})
        self.deposit()
        self.assertEqual(self._fees()["2026-10"].note, "paid in cash")
        self.assertIsNone(self._fees()["2026-09"].note)

    def test_a_deposits_remark_is_written_to_every_month_it_covers(self):
        self.deposit(note="cheque 1042")
        for row in self._fees().values():
            self.assertEqual(row.note, "cheque 1042")
        sheet = accounts_repo.fee_sheet(self.db, 1, ["2026-09", "2026-10", "2026-11"])
        riya = next(r for r in sheet["rows"] if r["student_id"] == 100)
        self.assertEqual(riya["notes"],
                         {"2026-09": "cheque 1042", "2026-10": "cheque 1042",
                          "2026-11": "cheque 1042"})

    def test_a_deposit_only_touches_the_student_it_names(self):
        accounts_repo.record_fee(self.db, 1, {"student_id": 101, "month": "2026-09",
                                              "amount": 4000})
        self.deposit()
        self.assertEqual(float(self._fees(101)["2026-09"].amount), 4000.0,
                         "another student's month must be left alone")
        self.assertEqual(len(self._fees(101)), 1)

    def test_a_deposit_for_another_schools_student_is_rejected(self):
        with self.assertRaises(NotFoundError):
            accounts_repo.deposit_fee(self.db, 1, {"student_id": 200, "start_month": "2026-09",
                                                  "plan": "quarterly", "amount": 1000})
        self.assertEqual(accounts_repo.fee_sheet(self.db, 2, ["2026-09"])["total_collected"], 0)

    def test_a_deposit_does_not_appear_in_another_schools_sheet(self):
        self.deposit()
        other = accounts_repo.fee_sheet(self.db, 2, ["2026-09", "2026-10", "2026-11"])
        self.assertEqual(other["total_collected"], 0.0)

    def test_previewing_a_deposit_writes_nothing(self):
        plan = accounts_repo.plan_fee_deposit(self.db, 1, {
            "student_id": 100, "start_month": "2026-09", "plan": "yearly", "amount": 60000,
        })
        self.assertEqual(plan["created"], 12)
        self.assertEqual([m["amount"] for m in plan["months"]], [5000.0] * 12)
        # The whole reason it exists: the admin sees the twelve months and the
        # split before committing to any of them.
        self.assertEqual(self._fees(), {})
        self.assertEqual(accounts_repo.fee_sheet(self.db, 1, ["2026-09"])["total_collected"], 0)

    def test_the_preview_names_the_months_a_deposit_would_replace(self):
        accounts_repo.record_fee(self.db, 1, {"student_id": 100, "month": "2026-11",
                                              "amount": 4000})
        plan = accounts_repo.plan_fee_deposit(self.db, 1, {
            "student_id": 100, "start_month": "2026-09", "plan": "quarterly", "amount": 12000,
        })
        self.assertEqual([m["replaced"] for m in plan["months"]], [False, False, True])
        self.assertEqual(plan["replaced"], 1)

    def test_a_deposit_that_would_overwrite_says_so_before_anything_is_written(self):
        accounts_repo.record_fee(self.db, 1, {"student_id": 100, "month": "2026-09",
                                              "amount": 9999})
        plan = accounts_repo.plan_fee_deposit(self.db, 1, {
            "student_id": 100, "start_month": "2026-09", "plan": "quarterly", "amount": 12000,
        })
        self.assertEqual(plan["replaced"], 1)
        # The existing entry is still standing when the admin is deciding.
        self.assertEqual(float(self._fees()["2026-09"].amount), 9999.0)

    def test_a_second_deposit_replaces_rather_than_duplicating(self):
        self.deposit()
        again = self.deposit(amount=15000)
        self.assertEqual(again["replaced"], 3)
        self.assertEqual(again["created"], 0)
        self.assertEqual(len(self._fees()), 3, "one row per month, whatever the number of deposits")
        self.assertEqual(float(self._fees()["2026-09"].amount), 5000.0)

    def test_a_rejected_deposit_writes_no_month_at_all(self):
        # The plan is resolved before anything is written, so a typo cannot
        # half-record a term: either the whole period lands or none of it does.
        with self.assertRaises(AppError):
            self.deposit(plan="fortnightly")
        self.assertEqual(self._fees(), {}, "a rejected deposit must write nothing")
        with self.assertRaises(NotFoundError):
            accounts_repo.deposit_fee(self.db, 1, {
                "student_id": 999, "start_month": "2026-09", "plan": "yearly",
                "amount": 60000,
            })
        self.assertEqual(self._fees(), {})

    def test_a_deposit_whose_students_school_changes_nothing_of_its_own(self):
        # The write re-asserts school_id on every row it touches, so a deposit
        # can never leave a month pointing at a different school than the
        # student it belongs to.
        self.deposit()
        for row in self._fees().values():
            self.assertEqual(row.school_id, 1)

    def test_a_deposit_that_overlaps_partly_only_touches_the_overlap(self):
        self.deposit(plan="quarterly", amount=12000)
        later = self.deposit(plan="monthly", start_month="2026-11", amount=4500)
        self.assertEqual([m["month"] for m in later["months"]], ["2026-11"])
        self.assertEqual(later["replaced"], 1)
        self.assertEqual(len(self._fees()), 3)
        self.assertEqual(float(self._fees()["2026-11"].amount), 4500.0)

    def test_a_fee_grid_remark_round_trips(self):
        # The fee grid's remark column, mirroring the salary one.
        accounts_repo.record_fee(
            self.db, 1,
            {"student_id": 100, "month": "2026-09", "amount": 5000, "note": "by cheque"},
        )
        sheet = accounts_repo.fee_sheet(self.db, 1, ["2026-09", "2026-10"])
        riya = next(r for r in sheet["rows"] if r["student_id"] == 100)
        self.assertEqual(riya["notes"], {"2026-09": "by cheque"})

    def test_a_student_with_no_fees_carries_no_notes(self):
        sheet = accounts_repo.fee_sheet(self.db, 1, ["2026-09"])
        riya = next(r for r in sheet["rows"] if r["student_id"] == 100)
        self.assertEqual(riya["notes"], {})

    def test_editing_a_fee_remark_keeps_the_amount_and_the_paid_date(self):
        accounts_repo.record_fee(
            self.db, 1,
            {"student_id": 100, "month": "2026-09", "amount": 5000,
             "paid_on": date(2026, 9, 5)},
        )
        accounts_repo.record_fee(
            self.db, 1, {"student_id": 100, "month": "2026-09", "amount": 5000,
                        "note": "part paid in cash"},
        )
        riya = next(r for r in
                    accounts_repo.fee_sheet(self.db, 1, ["2026-09"])["rows"]
                    if r["student_id"] == 100)
        self.assertEqual(riya["notes"], {"2026-09": "part paid in cash"})
        self.assertEqual(riya["amounts"], {"2026-09": 5000.0})
        self.assertEqual(riya["paid_on"], {"2026-09": "2026-09-05"})

    def test_correcting_a_fee_figure_says_nothing_about_the_remark(self):
        # Same trap as the salary grid, and the same fix: an amount edit that
        # does not mention remarks must not delete the one already recorded.
        accounts_repo.record_fee(
            self.db, 1,
            {"student_id": 100, "month": "2026-09", "amount": 5000,
             "note": "by cheque", "paid_on": date(2026, 9, 5)},
        )
        accounts_repo.record_fee(
            self.db, 1, {"student_id": 100, "month": "2026-09", "amount": 5200},
        )
        riya = next(r for r in
                    accounts_repo.fee_sheet(self.db, 1, ["2026-09"])["rows"]
                    if r["student_id"] == 100)
        self.assertEqual(riya["notes"], {"2026-09": "by cheque"},
                         "a fee correction must not delete the remark")
        self.assertEqual(riya["amounts"], {"2026-09": 5200.0})
        self.assertEqual(riya["paid_on"], {"2026-09": "2026-09-05"})

    def test_a_fee_remark_can_be_cleared_explicitly(self):
        accounts_repo.record_fee(
            self.db, 1,
            {"student_id": 100, "month": "2026-09", "amount": 5000, "note": "by cheque"},
        )
        accounts_repo.record_fee(
            self.db, 1,
            {"student_id": 100, "month": "2026-09", "amount": 5000, "note": None},
        )
        riya = next(r for r in
                    accounts_repo.fee_sheet(self.db, 1, ["2026-09"])["rows"]
                    if r["student_id"] == 100)
        self.assertEqual(riya["notes"], {"2026-09": None})
        self.assertEqual(riya["amounts"], {"2026-09": 5000.0},
                         "clearing a remark is not a deletion of the payment")


class FeeDepositSchemaTests(unittest.TestCase):
    """The deposit request, checked at the boundary.

    `plan` is deliberately not a Literal: the plan table lives with the
    arithmetic in the repository, so these pin only what the schema can know --
    that the payload is shaped like a deposit, and that a malformed start month
    or a negative total never reaches the repository.
    """

    def _schema(self):
        return importlib.import_module("services.accounts_service.schemas")

    def _deposit(self, **over):
        payload = {"student_id": 100, "start_month": "2026-09", "plan": "quarterly",
                   "amount": 12000}
        payload.update(over)
        return self._schema().FeeDeposit(**payload)

    def test_a_well_formed_deposit_is_accepted(self):
        deposit = self._deposit()
        self.assertEqual(deposit.plan, "quarterly")
        self.assertEqual(deposit.start_month, "2026-09")
        # The total is the money handed over, so it defaults to nothing rather
        # than to a monthly rate the server would then have to divide.
        self.assertIsNone(deposit.paid_on)
        self.assertIsNone(deposit.note)

    def test_a_malformed_start_month_is_rejected_before_the_repository(self):
        for bad in ("2026-13", "26-09", "2026/09", "2026-9", "september", ""):
            with self.assertRaises(Exception):
                self._deposit(start_month=bad)

    def test_a_negative_total_is_rejected(self):
        with self.assertRaises(Exception):
            self._deposit(amount=-1)

    def test_an_omitted_total_is_rejected_rather_than_defaulted_to_zero(self):
        # A deposit with no amount would otherwise be a request to record twelve
        # months as paid nothing.
        payload = {"student_id": 100, "start_month": "2026-09", "plan": "yearly"}
        with self.assertRaises(Exception):
            self._schema().FeeDeposit(**payload)

    def test_the_remark_is_bounded_like_the_grid_cells(self):
        self.assertEqual(self._deposit(note="x" * 200).note, "x" * 200)
        with self.assertRaises(Exception):
            self._deposit(note="x" * 201)

    def test_the_response_schemas_carry_the_month_by_month_breakdown(self):
        # The preview and the write answer with the same shape, so what the
        # admin read before confirming is what actually happened.
        entry = self._schema().FeeDepositEntry(
            student_id=100, start_month="2026-09", plan="quarterly", total=12000,
            months=[{"month": "2026-09", "amount": 4000, "replaced": False}],
            created=1, replaced=0,
        )
        self.assertEqual(entry.months[0].month, "2026-09")
        self.assertFalse(entry.months[0].replaced)

    def test_the_plan_response_carries_a_label_for_every_period(self):
        plans = self._schema().FeePlans(plans=[
            {"plan": "quarterly", "months": 3, "label": "Quarterly"},
        ])
        self.assertEqual(plans.plans[0].months, 3)
        self.assertEqual(plans.plans[0].label, "Quarterly")


class MonthValidationTests(unittest.TestCase):
    def _schema(self, name):
        import importlib
        mod = importlib.import_module("services.accounts_service.schemas")
        return getattr(mod, name)

    def test_valid_month_accepted(self):
        for cls_name, field in (("SalaryCreate", "staff_id"), ("FeeCreate", "student_id")):
            schema = self._schema(cls_name)(
                **{field: 1, "month": "2026-09", "amount": 100}
            )
            self.assertEqual(schema.month, "2026-09")

    def test_malformed_month_rejected(self):
        for bad in ("2026-13", "26-09", "2026/09", "2026-9", "202609", "september", ""):
            with self.assertRaises(Exception):
                self._schema("SalaryCreate")(staff_id=1, month=bad, amount=100)
            with self.assertRaises(Exception):
                self._schema("FeeCreate")(student_id=1, month=bad, amount=100)

    def test_negative_amount_rejected(self):
        with self.assertRaises(Exception):
            self._schema("SalaryCreate")(staff_id=1, month="2026-09", amount=-1)
        with self.assertRaises(Exception):
            self._schema("FeeCreate")(student_id=1, month="2026-09", amount=-0.01)

    def test_zero_amount_allowed(self):
        schema = self._schema("SalaryCreate")(staff_id=1, month="2026-09", amount=0)
        self.assertEqual(schema.amount, 0.0)


if __name__ == "__main__":
    unittest.main()
