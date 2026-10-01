"""GET /reports/staff/me — a staff member's own salary and attendance.

The admin report (/reports/staff/{staff_id}) is deliberately admin-only, so
before this endpoint a teacher had no way to see their own pay or their own
register at all. This is the self-service read behind the profile page.

Two things are asserted most heavily here:

* Scoping. There is no staff id anywhere in the route, so the row can only ever
  come from current_user.linked_person_id. The tests that matter are the ones
  proving a caller cannot reach a colleague or another school by passing an id,
  and that an account with no staff link is refused instead of answered with a
  blank panel that would read as "you have no attendance".
* Honesty of the two histories. The attendance day list is uncapped and
  month-filterable, and the salary window pages off an explicit anchor. Both
  must keep saying "nothing on record" as None rather than a confident zero.
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from common.database import Base
from common.dependencies import CurrentUser
from common.exceptions import ForbiddenError
from common.models import School, Staff

ROOT = Path(__file__).resolve().parents[1]

# services/reports_service/router.py uses bare `import repository` and
# `from schemas import ...`, so importing it means putting the service
# directory on sys.path. That makes the bare names collide with any other
# service's identically named modules in the same pytest process, so pop them
# first and restore them afterwards. Same approach as test_school_branding.py.
_BARE = ("repository", "router", "schemas")

TABLES = ["schools", "staff", "staff_salaries", "staff_attendance"]

SCHOOL_A = 1
SCHOOL_B = 2
STAFF_ME = 10
STAFF_COLLEAGUE = 11
STAFF_OTHER_SCHOOL = 20


class StaffSelfSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved = {name: sys.modules.pop(name, None) for name in _BARE}
        sys.path.insert(0, str(ROOT / "services" / "reports_service"))
        import router as reports_router  # noqa: PLC0415  (deliberately late)

        # staticmethod: a bare function assigned to a class attribute becomes a
        # bound method and self would be passed as the db argument.
        cls.summary = staticmethod(reports_router.my_staff_summary)
        cls.engine = create_engine("sqlite://")
        Base.metadata.create_all(
            cls.engine, tables=[Base.metadata.tables[name] for name in TABLES]
        )
        cls.Session = sessionmaker(bind=cls.engine, autoflush=False, future=True)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        sys.path.remove(str(ROOT / "services" / "reports_service"))
        for name in _BARE:
            sys.modules.pop(name, None)
        for name, mod in cls._saved.items():
            if mod is not None:
                sys.modules[name] = mod

    def setUp(self):
        self.db = self.Session()
        for name in TABLES:
            self.db.execute(Base.metadata.tables[name].delete())
        self.db.add_all([
            School(school_id=SCHOOL_A, name="Greenfield", address="1", pincode="1",
                   city="t", state="s", primary_contact="p", primary_email="a@g.test"),
            School(school_id=SCHOOL_B, name="Riverside", address="2", pincode="2",
                   city="t", state="s", primary_contact="p", primary_email="a@r.test"),
            Staff(staff_id=STAFF_ME, school_id=SCHOOL_A, name="Meera",
                  role="teacher", role_title="Maths teacher", person_type="teacher"),
            Staff(staff_id=STAFF_COLLEAGUE, school_id=SCHOOL_A, name="Arun",
                  role="driver", person_type="pilot"),
            Staff(staff_id=STAFF_OTHER_SCHOOL, school_id=SCHOOL_B, name="Ravi",
                  role="teacher", person_type="teacher"),
        ])
        self.db.commit()
        self.today = date(2026, 9, 15)

    def tearDown(self):
        self.db.close()

    def _caller(self, linked_person_id=STAFF_ME, school_id=SCHOOL_A, role="teacher"):
        return CurrentUser(
            user_id=1, role=role, school_id=school_id,
            linked_person_id=linked_person_id,
        )

    def summary_for(self, **kwargs):
        """Call the route the way FastAPI would, with the anchors defaulted."""
        params = {
            "months": 6, "salary_end": None, "attendance_month": None,
            "db": self.db, "current_user": self._caller(),
        }
        params.update(kwargs)
        return self.summary(**params)

    def add_salary(self, staff_id, month, amount, paid_on=None, note=None, school_id=SCHOOL_A):
        from common.models import StaffSalary  # noqa: PLC0415
        self.db.add(StaffSalary(
            school_id=school_id, staff_id=staff_id, month=month,
            amount=amount, paid_on=paid_on, note=note,
        ))
        self.db.commit()

    def add_attendance(self, staff_id, day, status, school_id=SCHOOL_A):
        from common.models import StaffAttendance  # noqa: PLC0415
        self.db.add(StaffAttendance(
            school_id=school_id, staff_id=staff_id, date=day, status=status,
        ))
        self.db.commit()

    # ---- it returns the caller's own record ----

    def test_returns_the_callers_own_salary_and_attendance(self):
        self.add_salary(STAFF_ME, "2026-09", 30000, note="Includes a bonus")
        self.add_attendance(STAFF_ME, date(2026, 9, 1), "Present")
        out = self.summary_for()
        self.assertEqual(out["staff"]["name"], "Meera")
        self.assertEqual(out["staff"]["designation"], "Maths teacher")
        self.assertEqual(out["salary"]["records"][0]["amount"], 30000.0)
        self.assertEqual(out["attendance"]["present"], 1)

    def test_carries_the_admin_remark_against_a_payment(self):
        # The remark an admin typed on the payment is part of the pay record
        # the staff member is asking to see, so it must survive the trip.
        self.add_salary(STAFF_ME, "2026-09", 30000, note="Includes Diwali bonus")
        record = self.summary_for()["salary"]["records"][0]
        self.assertEqual(record["note"], "Includes Diwali bonus")

    def test_a_month_with_no_remark_carries_none_not_an_empty_string(self):
        self.add_salary(STAFF_ME, "2026-09", 30000)
        record = self.summary_for()["salary"]["records"][0]
        self.assertIsNone(record["note"])

    def test_each_caller_sees_only_their_own_row(self):
        # Two staff, same school: the endpoint must follow the session, so
        # asking as Arun never surfaces Meera's salary.
        self.add_salary(STAFF_ME, "2026-09", 30000)
        self.add_salary(STAFF_COLLEAGUE, "2026-09", 20000)
        as_me = self.summary_for()["salary"]["records"][0]["amount"]
        as_arun = self.summary_for(
            current_user=self._caller(STAFF_COLLEAGUE, role="pilot")
        )["salary"]["records"][0]["amount"]
        self.assertEqual(as_me, 30000.0)
        self.assertEqual(as_arun, 20000.0)

    # ---- scoping: no id anywhere, so nothing to tamper with ----

    def test_the_route_takes_no_staff_id_at_all(self):
        """There is no parameter to point at another person.

        This is the guarantee the whole endpoint rests on: an admin-only route
        cannot be reused with a trusted id, so a teacher cannot ask for a
        colleague by editing a query string.
        """
        import inspect

        names = set(inspect.signature(self.summary).parameters)
        self.assertNotIn("staff_id", names)
        self.assertEqual(
            names - {"db", "current_user"},
            {"months", "salary_end", "attendance_month"},
        )

    def test_a_session_with_no_staff_link_is_refused_not_answered_blankly(self):
        # A blank panel would read as "you have no attendance", which is a
        # different and wrong statement.
        with self.assertRaises(ForbiddenError):
            self.summary_for(current_user=self._caller(linked_person_id=None))

    def test_a_session_with_no_school_is_refused(self):
        with self.assertRaises(ForbiddenError):
            self.summary_for(current_user=self._caller(school_id=None))

    def test_a_staff_row_in_another_school_is_refused(self):
        # The linked id points at a real person, but not in the caller's school.
        # Forbidden rather than 404, so it cannot be used to probe for ids.
        with self.assertRaises(ForbiddenError):
            self.summary_for(current_user=self._caller(
                linked_person_id=STAFF_OTHER_SCHOOL, school_id=SCHOOL_A
            ))

    def test_a_stray_salary_row_from_another_school_never_leaks_in(self):
        # The salary table is keyed on (staff_id, month), so a row cannot be
        # duplicated for the same month -- but a row written against another
        # school for a different month must still be excluded, because the
        # query filters on school_id as well as staff_id.
        self.add_salary(STAFF_ME, "2026-09", 30000, school_id=SCHOOL_A)
        self.add_salary(STAFF_ME, "2026-08", 999999, school_id=SCHOOL_B)
        records = self.summary_for(salary_end="2026-09")["salary"]["records"]
        self.assertEqual([r["amount"] for r in records], [30000.0])

    def test_stray_attendance_from_another_school_is_excluded(self):
        self.add_attendance(STAFF_ME, date(2026, 9, 1), "Present", school_id=SCHOOL_A)
        self.add_attendance(STAFF_ME, date(2026, 9, 2), "Absent", school_id=SCHOOL_B)
        out = self.summary_for()["attendance"]
        self.assertEqual([d["date"] for d in out["days"]], ["2026-09-01"])
        self.assertEqual(out["marked_days"], 1)

    # ---- attendance: full, filterable history ----

    def test_the_day_list_is_not_capped_at_thirty(self):
        # The admin report stops at 30; someone reading their own register
        # wants all of it, and a silent cap would look like a full history.
        # Same fixture, same rows: the admin report's `recent` tops out at 30
        # while this list carries all 45, which is the difference that matters.
        start = date(2025, 1, 1)
        for i in range(45):
            self.add_attendance(STAFF_ME, start + timedelta(days=i), "Present")
        days = self.summary_for()["attendance"]["days"]
        self.assertEqual(len(days), 45)

        import services.reports_service.repository as reports_repo  # noqa: PLC0415

        admin = reports_repo.staff_report(self.db, SCHOOL_A, STAFF_ME, today=self.today)
        self.assertEqual(len(admin["attendance"]["recent"]), 30)

    def test_days_are_newest_first(self):
        self.add_attendance(STAFF_ME, date(2026, 9, 1), "Present")
        self.add_attendance(STAFF_ME, date(2026, 9, 10), "Absent")
        days = self.summary_for()["attendance"]["days"]
        self.assertEqual([d["date"] for d in days], ["2026-09-10", "2026-09-01"])

    def test_a_month_filter_narrows_the_day_list(self):
        self.add_attendance(STAFF_ME, date(2026, 8, 31), "Present")
        self.add_attendance(STAFF_ME, date(2026, 9, 1), "Absent")
        self.add_attendance(STAFF_ME, date(2026, 9, 30), "Present")
        self.add_attendance(STAFF_ME, date(2026, 10, 1), "Absent")
        out = self.summary_for(attendance_month="2026-09")
        self.assertEqual(
            [d["date"] for d in out["attendance"]["days"]],
            ["2026-09-30", "2026-09-01"],
        )

    def test_a_month_filter_narrows_the_counters_too(self):
        """The percentage must describe the rows on screen.

        If the counters stayed on the whole register while the list showed one
        month, the number above the table would describe days the user cannot
        see -- the exact inconsistency the admin report has today.
        """
        self.add_attendance(STAFF_ME, date(2026, 8, 3), "Present")
        self.add_attendance(STAFF_ME, date(2026, 8, 4), "Absent")
        self.add_attendance(STAFF_ME, date(2026, 9, 1), "Present")
        self.add_attendance(STAFF_ME, date(2026, 9, 2), "Present")
        september = self.summary_for(attendance_month="2026-09")["attendance"]
        self.assertEqual(september["present"], 2)
        self.assertEqual(september["absent"], 0)
        self.assertEqual(september["percentage"], 100.0)
        # The whole-register view still sees everything.
        whole = self.summary_for()["attendance"]
        self.assertEqual(whole["present"], 3)
        self.assertEqual(whole["percentage"], 75.0)

    def test_echoes_back_the_month_that_was_filtered(self):
        out = self.summary_for(attendance_month="2026-08")
        self.assertEqual(out["attendance"]["month"], "2026-08")
        self.assertIsNone(self.summary_for()["attendance"]["month"])

    def test_a_month_with_nothing_marked_is_zero_marked_days_not_none(self):
        out = self.summary_for(attendance_month="2026-07")
        self.assertEqual(out["attendance"]["days"], [])
        self.assertEqual(out["attendance"]["marked_days"], 0)
        self.assertIsNone(out["attendance"]["percentage"])

    def test_never_marked_at_all_reports_no_percentage(self):
        # Not 0% -- that would claim they missed every day on record.
        self.assertIsNone(self.summary_for()["attendance"]["percentage"])

    def test_half_day_is_counted_separately_from_absent(self):
        self.add_attendance(STAFF_ME, date(2026, 9, 1), "Half day")
        self.add_attendance(STAFF_ME, date(2026, 9, 2), "Absent")
        self.add_attendance(STAFF_ME, date(2026, 9, 3), "Present")
        out = self.summary_for()["attendance"]
        self.assertEqual(out["half_day"], 1)
        self.assertEqual(out["absent"], 1)
        self.assertEqual(out["marked_days"], 3)
        self.assertEqual(out["percentage"], 33.3)

    def test_the_register_span_is_still_reported_when_filtered(self):
        # from_date/to_date describe the whole register, so they must not shift
        # when a single month is on screen.
        self.add_attendance(STAFF_ME, date(2026, 1, 5), "Present")
        self.add_attendance(STAFF_ME, date(2026, 9, 5), "Present")
        out = self.summary_for(attendance_month="2026-09")["attendance"]
        self.assertEqual(out["from_date"], "2026-01-05")
        self.assertEqual(out["to_date"], "2026-09-05")

    # ---- salary: six months, pageable ----

    def test_salary_defaults_to_a_six_month_window_ending_this_month(self):
        # No anchor means "now", which is what the admin grid shows on first
        # load, so this is stated relative to today rather than hardcoded.
        self.add_salary(STAFF_ME, "2026-09", 30000)
        window = self.summary_for()["salary"]["window"]
        now = date.today()
        self.assertEqual(len(window), 6)
        self.assertEqual(window[-1], f"{now.year:04d}-{now.month:02d}")

    def test_salary_is_a_six_month_window_when_anchored(self):
        self.add_salary(STAFF_ME, "2026-09", 30000)
        window = self.summary_for(salary_end="2026-09")["salary"]["window"]
        self.assertEqual(window, ["2026-04", "2026-05", "2026-06",
                                  "2026-07", "2026-08", "2026-09"])

    def test_an_anchor_pages_the_salary_window_backwards(self):
        self.add_salary(STAFF_ME, "2026-01", 28000)
        out = self.summary_for(salary_end="2026-02")
        self.assertEqual(out["salary"]["window"][-1], "2026-02")
        self.assertEqual(out["salary"]["window"][0], "2025-09")
        # The January payment now falls inside the paged window.
        self.assertEqual(
            [r["amount"] for r in out["salary"]["records"]], [28000.0]
        )

    def test_a_payment_outside_the_window_is_history_the_window_excludes(self):
        self.add_salary(STAFF_ME, "2026-01", 28000)
        self.add_salary(STAFF_ME, "2026-09", 30000)
        records = self.summary_for()["salary"]["records"]
        self.assertEqual([r["amount"] for r in records], [30000.0])

    def test_an_unpaid_month_in_the_window_is_an_outstanding_month(self):
        self.add_salary(STAFF_ME, "2026-09", 30000)
        salary = self.summary_for()["salary"]
        self.assertEqual(salary["months_paid"], 1)
        self.assertEqual(salary["outstanding_months"], 5)
        self.assertEqual(salary["average_monthly"], 30000.0)

    def test_salary_with_nothing_on_record_is_null_not_zero(self):
        salary = self.summary_for()["salary"]
        self.assertEqual(salary["records"], [])
        self.assertIsNone(salary["average_monthly"])
        self.assertIsNone(salary["from_month"])

    def test_a_paid_on_date_survives(self):
        self.add_salary(STAFF_ME, "2026-09", 30000, paid_on=date(2026, 9, 28))
        self.assertEqual(
            self.summary_for()["salary"]["records"][0]["paid_on"], "2026-09-28"
        )

    # ---- the response model is the contract the frontend codes against ----

    def test_the_response_model_accepts_the_payload_the_repository_builds(self):
        # The unit tests above call the route function directly, which skips
        # FastAPI's response_model validation. A key the repository builds but
        # the schema does not declare (or a required one it never builds) would
        # only surface at runtime as a 500 on the profile page.
        import schemas

        self.add_salary(STAFF_ME, "2026-09", 30000, paid_on=date(2026, 9, 28),
                        note="Includes a bonus")
        self.add_attendance(STAFF_ME, date(2026, 9, 1), "Present")
        model = schemas.StaffSelfSummary.model_validate(
            self.summary_for(attendance_month="2026-09")
        )
        self.assertEqual(model.staff.name, "Meera")
        self.assertEqual(model.attendance.month, "2026-09")
        self.assertEqual([d.date for d in model.attendance.days], ["2026-09-01"])
        self.assertEqual(model.salary.records[0].note, "Includes a bonus")

    def test_the_response_model_rejects_a_payload_missing_a_required_field(self):
        # Proves the previous test would have noticed a gap rather than passing
        # on a model that accepts anything.
        import schemas
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            schemas.StaffSelfSummary.model_validate({"salary": {}, "attendance": {}})

    # ---- the admin route stays closed ----

    def test_the_admin_route_is_still_admin_only(self):
        # Adding a self endpoint must not have widened the by-id route, which
        # is how one staff member reads another.
        import inspect

        import router as reports_router  # noqa: PLC0415

        admin_route = next(
            r for r in reports_router.router.routes
            if r.path == "/reports/staff/{staff_id}"
        )
        checker = next(
            dep.call for dep in admin_route.dependant.dependencies
            if dep.call.__name__ == "_checker"
        )
        # require_role closes over the allowed roles rather than exposing them,
        # so read them off the closure cells.
        allowed = next(
            cell.cell_contents
            for cell in checker.__closure__ or ()
            if isinstance(cell.cell_contents, tuple)
        )
        self.assertEqual(allowed, ("admin",))

        # And /staff/me must be declared before /staff/{staff_id}, or FastAPI
        # matches the int-typed path first and 422s on the literal "me".
        paths = [r.path for r in reports_router.router.routes]
        self.assertLess(
            paths.index("/reports/staff/me"), paths.index("/reports/staff/{staff_id}")
        )
        self.assertTrue(inspect.signature(self.summary).parameters)


if __name__ == "__main__":
    unittest.main()
