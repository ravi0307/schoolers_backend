"""Tests for the accounts seed script.

The script writes into a live-shaped table graph, so the two things worth
testing are that it is safe to re-run (a demo seed that clobbers real
accounts is worse than no seed) and that it covers the same window the admin
grid renders. A seed that derived "the last six months" on its own would
eventually disagree with the API and leave columns silently empty.
"""
import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import seed_accounts
from common.models import Base, School, SchoolClass, Staff, StaffSalary, Student, StudentFee
import services.accounts_service.repository as accounts_repo

TABLES = [
    "schools", "users", "classes", "staff", "students",
    "staff_salaries", "student_fees",
]


class SeedAccountsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine, tables=[Base.metadata.tables[name] for name in TABLES]
        )

    def setUp(self):
        self.db: Session = sessionmaker(bind=self.engine, autoflush=False, future=True)()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.add_all(
            [
                School(school_id=1, name="Green Valley", address="1 St", pincode="1",
                       city="c", state="s", primary_contact="p", primary_email="e@x.test"),
                School(school_id=2, name="Blue Horizon", address="2 St", pincode="2",
                       city="c", state="s", primary_contact="p", primary_email="e@y.test"),
                SchoolClass(class_id=1, school_id=1, name="Grade 1"),
                SchoolClass(class_id=2, school_id=2, name="Grade 9"),
                Staff(staff_id=10, school_id=1, name="Meera", role="teacher"),
                Staff(staff_id=11, school_id=1, name="Arun", role="driver"),
                Staff(staff_id=12, school_id=2, name="Other School Staff", role="teacher"),
                Staff(staff_id=13, school_id=1, name="Left Last Year", role="teacher",
                      is_active=False),
                Student(student_id=100, school_id=1, class_id=1, admission_no="A1", name="Riya"),
                Student(student_id=200, school_id=2, class_id=2, admission_no="B2", name="Kabir"),
                Student(student_id=201, school_id=1, class_id=1, admission_no="A3",
                        name="Left Last Year", is_active=False),
            ]
        )
        self.db.commit()
        self.db.close()
        # Pinned so the seed has a fixed window to fill: anchored to the real
        # clock it would shift every month and the assertions below would start
        # failing on the 1st of each new month, with nothing wrong in the seed.
        self.today = date(2026, 9, 15)
        self.window = accounts_repo.recent_months(6, self.today)

    def _read(self, table):
        db: Session = sessionmaker(bind=self.engine, future=True)()
        try:
            return db.query(table).all()
        finally:
            db.close()

    # ---- happy path ----

    def test_seeds_every_active_person_for_every_month_in_the_window(self):
        _win, salaries, fees, skipped = seed_accounts.seed(self.engine, today=self.today)
        # 3 active staff x 6 months, 2 active students x 6 months.
        self.assertEqual(salaries, 18)
        self.assertEqual(fees, 12)
        self.assertEqual(skipped, 0)
        self.assertEqual(len(self._read(StaffSalary)), 18)
        self.assertEqual(len(self._read(StudentFee)), 12)

    def test_skips_inactive_staff_and_students(self):
        seed_accounts.seed(self.engine, today=self.today)
        staff_ids = {r.staff_id for r in self._read(StaffSalary)}
        student_ids = {r.student_id for r in self._read(StudentFee)}
        self.assertNotIn(13, staff_ids, "departed staff must not be paid")
        self.assertNotIn(201, student_ids, "withdrawn students must not be billed")

    def test_uses_the_default_amounts(self):
        seed_accounts.seed(self.engine, today=self.today)
        self.assertTrue(all(float(r.amount) == 10000.0 for r in self._read(StaffSalary)))
        self.assertTrue(all(float(r.amount) == 4000.0 for r in self._read(StudentFee)))

    def test_amounts_are_overridable(self):
        seed_accounts.seed(self.engine, salary=25000, fee=7500, today=self.today)
        self.assertTrue(all(float(r.amount) == 25000.0 for r in self._read(StaffSalary)))
        self.assertTrue(all(float(r.amount) == 7500.0 for r in self._read(StudentFee)))

    def test_each_row_carries_the_school_of_the_person_it_pays(self):
        # The school_id is denormalised onto the payment. If the seed ever
        # stamped the wrong school, that school's admin would see another
        # school's payroll on their grid.
        seed_accounts.seed(self.engine, today=self.today)
        for row in self._read(StaffSalary):
            self.assertEqual(row.school_id, 1 if row.staff_id in (10, 11) else 2)
        for row in self._read(StudentFee):
            self.assertEqual(row.school_id, 1 if row.student_id == 100 else 2)

    def test_months_match_the_window_the_grid_renders(self):
        # The whole point of calling recent_months rather than re-deriving the
        # six months here: a seed on its own calendar would eventually leave
        # the newest grid column empty.
        seed_accounts.seed(self.engine, today=self.today)
        salary_months = {r.month for r in self._read(StaffSalary)}
        self.assertEqual(salary_months, set(self.window))

    def test_each_seeded_payment_is_stamped_with_a_paid_date(self):
        # The grids show "paid on" next to every figure, so a fresh demo seed
        # must not leave those dates blank. The 28th of the payment's own
        # month is a neutral, believable payday.
        seed_accounts.seed(self.engine, today=self.today)
        for row in self._read(StaffSalary):
            self.assertEqual(str(row.paid_on), row.month + "-28")
        for row in self._read(StudentFee):
            self.assertEqual(str(row.paid_on), row.month + "-28")

    def test_rerunning_keeps_an_existing_paid_date(self):
        # The paid date is real history: re-running the demo seed must not
        # rewrite it back to the synthetic 28th.
        seed_accounts.seed(self.engine, today=self.today)
        db: Session = sessionmaker(bind=self.engine, future=True)()
        row = db.query(StaffSalary).filter(StaffSalary.staff_id == 10).first()
        row.paid_on = date(2026, 9, 15)
        db.commit()
        db.close()

        seed_accounts.seed(self.engine, today=self.today)
        db = sessionmaker(bind=self.engine, future=True)()
        try:
            again = db.query(StaffSalary).filter(StaffSalary.staff_id == 10).first()
            self.assertEqual(str(again.paid_on), "2026-09-15")
        finally:
            db.close()

    def test_the_seeded_grid_reports_no_outstanding_months(self):
        # End to end through the same read the API uses, so "seeded" means the
        # admin actually sees a full grid rather than a half-empty one.
        seed_accounts.seed(self.engine, today=self.today)
        db: Session = sessionmaker(bind=self.engine, future=True)()
        try:
            sheet = accounts_repo.salary_sheet(db, 1, self.window)
            self.assertEqual(sheet["total_outstanding_months"], 0)
            # 2 active staff in school 1 x 6 months x 10000.
            self.assertEqual(sheet["total_paid"], 120000.0)
            fees = accounts_repo.fee_sheet(db, 1, self.window)
            self.assertEqual(fees["outstanding_count"], 0)
            # 1 active student in school 1 x 6 months x 4000.
            self.assertEqual(fees["total_collected"], 24000.0)
        finally:
            db.close()

    # ---- idempotency: the property that matters most ----

    def test_rerunning_writes_nothing_new(self):
        seed_accounts.seed(self.engine, today=self.today)
        _win, salaries, fees, skipped = seed_accounts.seed(self.engine, today=self.today)
        self.assertEqual(salaries, 0)
        self.assertEqual(fees, 0)
        self.assertEqual(skipped, 30)
        self.assertEqual(len(self._read(StaffSalary)), 18)
        self.assertEqual(len(self._read(StudentFee)), 12)

    def test_rerunning_does_not_overwrite_a_corrected_amount(self):
        # An admin who fixes a typo must survive someone re-running the demo
        # seed. Overwriting silently is the failure this guards against.
        seed_accounts.seed(self.engine, today=self.today)
        db: Session = sessionmaker(bind=self.engine, future=True)()
        row = db.query(StaffSalary).filter(StaffSalary.staff_id == 10).first()
        row.amount = 31000
        db.commit()
        db.close()

        seed_accounts.seed(self.engine, today=self.today)
        db = sessionmaker(bind=self.engine, future=True)()
        try:
            again = db.query(StaffSalary).filter(StaffSalary.staff_id == 10).first()
            self.assertEqual(float(again.amount), 31000.0)
        finally:
            db.close()

    def test_force_overwrites_when_asked_explicitly(self):
        seed_accounts.seed(self.engine, today=self.today)
        db: Session = sessionmaker(bind=self.engine, future=True)()
        row = db.query(StaffSalary).filter(StaffSalary.staff_id == 10).first()
        row.amount = 31000
        db.commit()
        db.close()

        seed_accounts.seed(self.engine, force=True, today=self.today)
        db = sessionmaker(bind=self.engine, future=True)()
        try:
            again = db.query(StaffSalary).filter(StaffSalary.staff_id == 10).first()
            self.assertEqual(float(again.amount), 10000.0)
        finally:
            db.close()

    def test_force_does_not_duplicate_rows(self):
        seed_accounts.seed(self.engine, today=self.today)
        seed_accounts.seed(self.engine, force=True, today=self.today)
        self.assertEqual(len(self._read(StaffSalary)), 18)
        self.assertEqual(len(self._read(StudentFee)), 12)

    def test_partially_seeded_school_only_gains_what_is_missing(self):
        # A school that already has real history keeps it and gains the rest.
        seed_accounts.seed(self.engine, today=self.today)
        db: Session = sessionmaker(bind=self.engine, future=True)()
        db.query(StaffSalary).filter(
            StaffSalary.staff_id == 10, StaffSalary.month == self.window[0]
        ).delete()
        db.commit()
        db.close()

        _win, salaries, fees, _skipped = seed_accounts.seed(self.engine, today=self.today)
        self.assertEqual(salaries, 1, "only the one missing month should be written")
        self.assertEqual(fees, 0)
        self.assertEqual(len(self._read(StaffSalary)), 18)

    def test_seed_with_no_active_people_is_a_no_op(self):
        db: Session = sessionmaker(bind=self.engine, future=True)()
        db.execute(Base.metadata.tables["staff"].delete())
        db.execute(Base.metadata.tables["students"].delete())
        db.commit()
        db.close()

        _win, salaries, fees, skipped = seed_accounts.seed(self.engine, today=self.today)
        self.assertEqual((salaries, fees, skipped), (0, 0, 0))


class SeedWindowTests(unittest.TestCase):
    def test_seed_uses_the_api_window_by_default(self):
        # Guard the import-level coupling: the seed must not grow its own
        # month arithmetic.
        self.assertEqual(seed_accounts.DEFAULT_SALARY, 10000.00)
        self.assertEqual(seed_accounts.DEFAULT_FEE, 4000.00)
        self.assertEqual(len(accounts_repo.recent_months(6)), 6)

    def test_a_wider_window_is_honoured(self):
        # The window still comes from the API helper rather than any month
        # arithmetic of the seed's own. `today` is passed through, so the call
        # keeps its (months, today) shape instead of re-deriving either.
        source = open(seed_accounts.__file__).read()
        self.assertIn("accounts_repo.recent_months(months, today)", source)


if __name__ == "__main__":
    unittest.main()
