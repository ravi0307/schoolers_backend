"""Accounts: six-month salary and fee sheets.

Covers the parts that are easy to get wrong and expensive in a school:
the month window maths, that unpaid people still appear, that a second
payment for the same month overwrites rather than double-counts, and that
one school can never record money against another school's people.
"""
import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from common.exceptions import NotFoundError
from common.models import Base, School, SchoolClass, Staff, Student, User
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

    def test_empty_school_returns_empty_sheets_rather_than_failing(self):
        self.db.execute(Base.metadata.tables["students"].delete())
        self.db.execute(Base.metadata.tables["staff"].delete())
        self.db.commit()
        self.assertEqual(accounts_repo.salary_sheet(self.db, 1, self.months)["rows"], [])
        self.assertEqual(accounts_repo.fee_sheet(self.db, 1, self.months)["rows"], [])


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
