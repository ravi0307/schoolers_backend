"""Per-staff report: details, salary and attendance in one read.

The mirror of the student report, about the people who get paid. The system
has no designation field, no hire date and no payroll abstract, so most of
these tests assert the report is honest about that: a designation is just
role_title (or role), "recorded on" is created_at, and salary is whatever
StaffSalary rows exist, each with the date it was actually paid. The other
half is scoping: a staff member of another school must 404 the same way a
missing id does, and no salary or attendance row may leak across people.
"""
import unittest
from datetime import date, time, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.models import (
    Base, School, Staff, StaffSalary, StaffAttendance,
)
import services.reports_service.repository as reports_repo

TABLES = [
    "schools", "staff", "staff_salaries", "staff_attendance",
]


def iso(day: date) -> str:
    return day.isoformat()


class StaffReportTests(unittest.TestCase):
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
            Staff(staff_id=10, school_id=1, name="Meera", role="teacher",
                  role_title="Maths teacher", person_type="teacher",
                  phone="111", email="meera@g.test", date_of_birth=date(1990, 3, 2),
                  gender="Female", marital_status="Married",
                  present_address="9 Lane", permanent_address="10 Lane",
                  aadhaar_card="1234", emergency_number="999", driving_license="DL1"),
            Staff(staff_id=11, school_id=1, name="Arun", role="driver",
                  person_type="staff"),
            Staff(staff_id=20, school_id=2, name="Other School Staff", role="teacher"),
        ])
        self.db.commit()
        # The salary window is anchored so tests do not depend on today.
        self.today = date(2026, 9, 15)

    def tearDown(self):
        self.db.close()

    def report(self, staff_id=10, school_id=1):
        return reports_repo.staff_report(
            self.db, school_id, staff_id, today=self.today
        )

    def add_salary(self, staff_id, month, amount, paid_on=None, note=None, school_id=1):
        self.db.add(StaffSalary(
            school_id=school_id, staff_id=staff_id, month=month,
            amount=amount, paid_on=paid_on, note=note,
        ))
        self.db.commit()

    def add_attendance(self, staff_id, day, status, check_in=None, check_out=None):
        self.db.add(StaffAttendance(
            school_id=1, staff_id=staff_id, date=day, status=status,
            check_in=check_in, check_out=check_out,
        ))
        self.db.commit()

    # ---- details ----

    def test_report_carries_the_staff_details(self):
        staff = self.report()["staff"]
        self.assertEqual(staff["name"], "Meera")
        # designation resolves the refined title over the plain role.
        self.assertEqual(staff["designation"], "Maths teacher")
        self.assertEqual(staff["person_type"], "teacher")
        self.assertEqual(staff["phone"], "111")
        self.assertEqual(staff["email"], "meera@g.test")
        self.assertEqual(staff["date_of_birth"], "1990-03-02")
        self.assertEqual(staff["marital_status"], "Married")
        self.assertEqual(staff["present_address"], "9 Lane")
        self.assertEqual(staff["permanent_address"], "10 Lane")
        self.assertEqual(staff["aadhaar_card"], "1234")
        self.assertEqual(staff["emergency_number"], "999")
        self.assertEqual(staff["driving_license"], "DL1")

    def test_designation_falls_back_to_the_plain_role(self):
        staff = self.report(staff_id=11)["staff"]
        self.assertEqual(staff["role_title"], None)
        self.assertEqual(staff["designation"], "driver")

    def test_a_new_person_has_an_empty_salary_and_no_attendance(self):
        report = self.report(staff_id=11)
        self.assertEqual(report["salary"]["records"], [])
        self.assertEqual(report["salary"]["months_paid"], 0)
        self.assertIsNone(report["salary"]["average_monthly"])
        self.assertEqual(report["salary"]["outstanding_months"], 6)
        self.assertIsNone(report["attendance"]["percentage"])
        self.assertEqual(report["attendance"]["marked_days"], 0)

    # ---- salary ----

    def test_salary_covers_the_same_six_month_window_as_the_accounts_grid(self):
        today = date(2026, 9, 15)
        for offset in range(6):
            month = reports_repo._recent_months(6, today)[offset]
            self.add_salary(10, month, 10000.0, paid_on=date(int(month[:4]), int(month[5:7]), 28))

        salary = self.report()["salary"]
        self.assertEqual(salary["window"], reports_repo._recent_months(6, today))
        self.assertEqual(salary["window_size"], 6)
        self.assertEqual(salary["months_paid"], 6)
        self.assertEqual(salary["outstanding_months"], 0)
        # 6 x 10000, summed server-side so the client never adds floats.
        self.assertEqual(salary["total_paid"], 60000.0)
        self.assertEqual(salary["average_monthly"], 10000.0)
        self.assertEqual(salary["from_month"], "2026-04")
        self.assertEqual(salary["to_month"], "2026-09")

    def test_the_paid_date_travels_with_each_amount(self):
        self.add_salary(10, "2026-09", 45000.0, paid_on=date(2026, 9, 28), note="September")
        record = self.report()["salary"]["records"][0]
        self.assertEqual(record["amount"], 45000.0)
        self.assertEqual(record["paid_on"], "2026-09-28")
        self.assertEqual(record["note"], "September")

    def test_an_amount_without_a_paid_date_is_null_never_fabricated(self):
        self.add_salary(10, "2026-09", 45000.0)
        self.assertIsNone(self.report()["salary"]["records"][0]["paid_on"])

    def test_unpaid_window_months_count_as_outstanding(self):
        self.add_salary(10, "2026-04", 10000.0)
        self.add_salary(10, "2026-09", 10000.0)
        salary = self.report()["salary"]
        self.assertEqual(salary["months_paid"], 2)
        self.assertEqual(salary["outstanding_months"], 4)
        self.assertEqual(salary["total_paid"], 20000.0)
        self.assertEqual(salary["average_monthly"], 10000.0)

    def test_salary_outside_the_window_is_not_shown(self):
        # An amount paid in March is real history, but not part of "the last
        # six months" the grid renders. Leaving it out is the honest reading.
        self.add_salary(10, "2025-12", 10000.0)
        self.add_salary(10, "2026-09", 10000.0)
        salary = self.report()["salary"]
        self.assertEqual([r["month"] for r in salary["records"]], ["2026-09"])
        self.assertEqual(salary["total_paid"], 10000.0)
        self.assertEqual(salary["from_month"], "2026-09")

    def test_an_empty_salary_window_has_no_total_and_no_average(self):
        salary = self.report()["salary"]
        self.assertEqual(salary["total_paid"], 0.0)
        self.assertIsNone(salary["average_monthly"])
        self.assertIsNone(salary["from_month"])
        self.assertIsNone(salary["to_month"])

    # ---- attendance ----

    def test_attendance_counts_every_status_separately(self):
        for day in range(1, 4):
            self.add_attendance(10, date(2026, 9, day), "Present")
        self.add_attendance(10, date(2026, 9, 4), "Absent")
        self.add_attendance(10, date(2026, 9, 5), "On leave")
        self.add_attendance(10, date(2026, 9, 6), "Half day")
        att = self.report()["attendance"]
        # A half day must not hide inside "absent".
        self.assertEqual(att["present"], 3)
        self.assertEqual(att["absent"], 1)
        self.assertEqual(att["on_leave"], 1)
        self.assertEqual(att["half_day"], 1)
        self.assertEqual(att["marked_days"], 6)
        self.assertEqual(att["percentage"], 50.0)

    def test_attendance_reports_the_range_it_actually_covers(self):
        self.add_attendance(10, date(2026, 3, 2), "Present")
        self.add_attendance(10, date(2026, 9, 20), "On leave")
        att = self.report()["attendance"]
        self.assertEqual(att["from_date"], "2026-03-02")
        self.assertEqual(att["to_date"], "2026-09-20")

    def test_recent_days_are_newest_first_capped_and_carry_check_times(self):
        first = date(2026, 2, 1)
        for offset in range(39):
            self.add_attendance(10, first + timedelta(days=offset), "Present",
                                check_in=time(8, 30), check_out=time(16, 45))
        recent = self.report()["attendance"]["recent"]
        self.assertEqual(len(recent), reports_repo.RECENT_ATTENDANCE_DAYS)
        self.assertEqual(recent[0]["date"], "2026-03-11")
        self.assertEqual(recent[0]["check_in"], "08:30:00")
        self.assertEqual(recent[0]["check_out"], "16:45:00")
        # Newest first, and the totals still cover every marked day.
        self.assertGreater(recent[0]["date"], recent[-1]["date"])
        self.assertEqual(self.report()["attendance"]["marked_days"], 39)

    def test_days_with_no_check_time_report_none_not_empty_string(self):
        self.add_attendance(10, date(2026, 9, 1), "Present")
        recent = self.report()["attendance"]["recent"]
        self.assertEqual(recent[0]["check_in"], None)
        self.assertEqual(recent[0]["check_out"], None)

    def test_no_attendance_reports_none_not_a_perfect_score(self):
        att = self.report()["attendance"]
        self.assertIsNone(att["percentage"])
        self.assertIsNone(att["from_date"])
        self.assertEqual(att["marked_days"], 0)
        self.assertEqual(att["recent"], [])

    # ---- scoping ----

    def test_another_schools_staff_is_not_found_rather_than_returned(self):
        self.assertIsNone(reports_repo.staff_report(self.db, 1, 20))

    def test_a_made_up_staff_id_is_also_not_found(self):
        self.assertIsNone(reports_repo.staff_report(self.db, 1, 999999))

    def test_salary_never_leaks_across_people_or_schools(self):
        self.add_salary(10, "2026-09", 45000.0)
        self.add_salary(11, "2026-09", 31000.0)
        self.add_salary(20, "2026-09", 90000.0, school_id=2)
        self.assertEqual(self.report(10)["salary"]["total_paid"], 45000.0)
        # Staff 11's own report only sees staff 11's rows.
        self.assertEqual(self.report(11)["salary"]["total_paid"], 31000.0)

    def test_attendance_never_leaks_across_staff(self):
        self.add_attendance(10, date(2026, 9, 1), "Absent")
        self.add_attendance(11, date(2026, 9, 1), "Present")
        self.assertEqual(self.report(10)["attendance"]["absent"], 1)
        self.assertEqual(self.report(11)["attendance"]["present"], 1)


if __name__ == "__main__":
    unittest.main()