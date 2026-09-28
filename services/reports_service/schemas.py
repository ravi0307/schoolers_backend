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
