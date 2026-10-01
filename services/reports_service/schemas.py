from pydantic import BaseModel


class SchoolOverview(BaseModel):
    school_id: int
    teachers: int
    staff: int
    students: int
    parents: int
    classes: int
    pending_leave_requests: int
    active_routes: int


class ClassAttendanceTrendPoint(BaseModel):
    date: str
    present: int
    absent: int


# ---- Per-student report ----
#
# There are no exam, grade or result tables anywhere in the system. `marks`
# stores one raw 0-100 score per subject per term, and `term` is free text
# rather than a foreign key to a term table. So this report reports what is
# actually stored -- scores, and an average computed from them -- and
# deliberately offers no grade letter, pass/fail or rank. Those would be
# invented thresholds, and a report card that disagrees with the school's own
# grading is worse than one that admits there are no grades in the system.


class Guardian(BaseModel):
    parent_id: int
    name: str
    relationship: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    emergency_number: str | None = None


class ReportStudent(BaseModel):
    student_id: int
    name: str
    admission_no: str
    class_id: int
    class_name: str | None = None
    date_of_birth: str | None = None
    gender: str | None = None
    photo_url: str | None = None
    # `created_at` is the only admission-ish date in the schema; there is no
    # admission_date column. Exposed as recorded_on rather than pretending.
    recorded_on: str | None = None
    guardians: list[Guardian] = []


class SubjectMark(BaseModel):
    subject_id: int
    subject_name: str | None = None
    score: int


class TermMarks(BaseModel):
    term: str
    subjects: list[SubjectMark] = []
    # Mean of the recorded scores. Null when the term has no marks at all,
    # which is different from an average of zero.
    average: float | None = None
    graded_subjects: int = 0
    # How many active subjects the school teaches, so the report can say
    # "3 of 6 subjects graded" instead of implying six straight As.
    total_subjects: int = 0


class AttendanceDay(BaseModel):
    date: str
    status: str


class ReportAttendance(BaseModel):
    present: int = 0
    absent: int = 0
    marked_days: int = 0
    # Null when nothing was ever marked, so the UI can say "no records" rather
    # than dividing by zero and reporting a perfect 100%.
    percentage: float | None = None
    from_date: str | None = None
    to_date: str | None = None
    recent: list[AttendanceDay] = []


class StudentReport(BaseModel):
    student: ReportStudent
    terms: list[str] = []
    marks_by_term: list[TermMarks] = []
    attendance: ReportAttendance


# ---- Per-staff report ----
#
# The mirror of the student report for the people who get paid. There is no
# designation field, no hire date and no payroll abstract anywhere; `role_title`
# (or `role`) is the closest thing to a designation, `created_at` is the only
# join-ish date, and salary is whatever StaffSalary rows exist. So the report
# reports that: a designation, a recorded-on date, and the actual amounts with
# their paid dates -- and it never invents a title, an enrolment date or a
# "salary band".


class ReportStaff(BaseModel):
    staff_id: int
    name: str
    role: str
    role_title: str | None = None
    person_type: str
    designation: str | None = None
    phone: str | None = None
    email: str | None = None
    date_of_birth: str | None = None
    gender: str | None = None
    marital_status: str | None = None
    present_address: str | None = None
    permanent_address: str | None = None
    aadhaar_card: str | None = None
    emergency_number: str | None = None
    driving_license: str | None = None
    is_active: bool = True
    # `created_at` is the closest thing to an admission date the staff table
    # has; exposed as recorded_on rather than pretending it is a hire date.
    recorded_on: str | None = None


class SalaryRecord(BaseModel):
    month: str
    amount: float
    # The date the figure was actually paid, which the accounts grid shows too.
    paid_on: str | None = None
    note: str | None = None


class ReportSalary(BaseModel):
    # The rolling window the grid calls "the last six months"; every slot
    # without a payment is an outstanding month.
    window: list[str] = []
    window_size: int = 0
    records: list[SalaryRecord] = []
    months_paid: int = 0
    outstanding_months: int = 0
    total_paid: float = 0.0
    # Null when no payment is on record, which is different from 0.
    average_monthly: float | None = None
    from_month: str | None = None
    to_month: str | None = None


class StaffAttendanceDay(BaseModel):
    date: str
    status: str
    check_in: str | None = None
    check_out: str | None = None


class StaffReportAttendance(BaseModel):
    present: int = 0
    absent: int = 0
    on_leave: int = 0
    half_day: int = 0
    marked_days: int = 0
    # Null when nothing was ever marked, never a fake 100%.
    percentage: float | None = None
    from_date: str | None = None
    to_date: str | None = None
    recent: list[StaffAttendanceDay] = []


class StaffSelfAttendance(StaffReportAttendance):
    """The same counters, plus an uncapped day list.

    `days` is every marked day rather than the admin report's 30-day `recent`,
    narrowed to `month` when one is given. Because the counters are scoped the
    same way as the list, the percentage always describes the rows on screen --
    with no filter that is the whole register, and with one it is that month.
    """
    month: str | None = None
    days: list[StaffAttendanceDay] = []


class StaffSelfStaff(BaseModel):
    """Just enough identity to label the section; the profile already shows
    the caller's own name, role and email from /auth/me."""
    staff_id: int
    name: str
    role: str
    role_title: str | None = None
    person_type: str
    designation: str | None = None


class StaffSelfSummary(BaseModel):
    staff: StaffSelfStaff
    salary: ReportSalary
    attendance: StaffSelfAttendance


class StaffReport(BaseModel):
    staff: ReportStaff
    salary: ReportSalary
    attendance: StaffReportAttendance
