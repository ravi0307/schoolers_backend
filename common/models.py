"""
SQLAlchemy ORM models — one class per table in schoolers_schema.sql.
Grouped by domain with comments; kept in one file deliberately so the
mapping to the schema is easy to audit in one place. Individual API
modules import only what they need from here.
"""
from datetime import date, datetime

from sqlalchemy import (
    Column, Integer, String, Boolean, Text, Date, DateTime, Time, ForeignKey,
    Numeric, UniqueConstraint, CheckConstraint, func, text, JSON
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from common.database import Base

# ============================================================================
# AUDIT TRACKING
# ============================================================================


class AuditColumnsMixin:
    """Track who last modified a row and when.

    Every domain table carries `modified_by` (FK to users.user_id — NULL for
    seed/system writes) and `modified_at` (server time on insert, bumped on
    every update). The user id is filled automatically by common.audit's
    before-flush listener from a request-scoped context; call sites that issue
    bulk queries use common.audit.bulk_modified_columns() explicitly.
    """

    modified_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))
    modified_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ============================================================================
# 1. PLATFORM / MASTER ADMIN
# ============================================================================

class School(AuditColumnsMixin, Base):
    __tablename__ = "schools"

    school_id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    address = Column(String(200), nullable=False)
    pincode = Column(String(12), nullable=False)
    city = Column(String(80), nullable=False)
    state = Column(String(80), nullable=False)
    country = Column(String(80), nullable=False, default="India")
    primary_contact = Column(String(30), nullable=False)
    alternative_contact = Column(String(30))
    primary_email = Column(String(120), nullable=False)
    alternative_email = Column(String(120))
    first_name = Column(String(100))
    last_name = Column(String(100))
    logo_url = Column(String(255))
    status = Column(String(10), nullable=False, default="Active")
    route_enabled = Column(Boolean, nullable=False, default=False)
    website_enabled = Column(Boolean, nullable=False, default=False)
    library_enabled = Column(Boolean, nullable=False, default=False)
    fees_enabled = Column(Boolean, nullable=False, default=False)
    salary_enabled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class SchoolNotification(AuditColumnsMixin, Base):
    __tablename__ = "school_notifications"

    notification_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    type = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    sent_at = Column(DateTime, server_default=func.now())
    status = Column(String(10), nullable=False, default="Sent")


# ============================================================================
# 2. ACADEMIC STRUCTURE
# ============================================================================

class Subject(AuditColumnsMixin, Base):
    __tablename__ = "subjects"

    subject_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(40), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (UniqueConstraint("school_id", "name"),)


class SchoolClass(AuditColumnsMixin, Base):
    __tablename__ = "classes"

    class_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(40), nullable=False)
    class_teacher_staff_id = Column(Integer, ForeignKey("staff.staff_id", ondelete="SET NULL"))
    student_count = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (UniqueConstraint("school_id", "name"),)

    @property
    def class_teacher_id(self) -> int | None:
        """Read-only alias of class_teacher_staff_id for existing clients."""
        return self.class_teacher_staff_id


class Period(AuditColumnsMixin, Base):
    __tablename__ = "periods"

    period_id = Column(Integer, primary_key=True)
    period_no = Column(Integer, nullable=False, unique=True)
    period_time = Column(String(31), nullable=False)


class Holiday(AuditColumnsMixin, Base):
    """A single named holiday on a single calendar date.

    This is deliberately *not* a recurring weekday flag: "we are closed every
    Saturday" and "Diwali on 8 November" are different facts, and a table can
    only answer one of them at a time. Timetable entries stay a recurring
    weekday template, so a dated holiday is resolved against the real dates of
    whichever week is being displayed.
    """

    __tablename__ = "holidays"

    holiday_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    occasion = Column(String(120), nullable=False)
    holiday_date = Column(Date, nullable=False)

    # A school cannot have two holidays on the same date; the timetable would
    # otherwise have to pick one arbitrarily when highlighting that column.
    __table_args__ = (UniqueConstraint("school_id", "holiday_date"),)


# ============================================================================
# 3. PEOPLE
# ============================================================================

class TeacherClassSubject(AuditColumnsMixin, Base):
    """A staff member's teaching assignment.

    staff_id points at the unified staff table; teacher_id is a read-only alias
    kept so existing clients keep working after the teachers/pilots merge.
    """

    __tablename__ = "teacher_class_subjects"

    id = Column(Integer, primary_key=True)
    staff_id = Column(Integer, ForeignKey("staff.staff_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="CASCADE"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.subject_id", ondelete="CASCADE"), nullable=False)
    is_class_teacher = Column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("staff_id", "class_id", "subject_id"),)

    @property
    def teacher_id(self) -> int:
        return self.staff_id


class Staff(AuditColumnsMixin, Base):
    __tablename__ = "staff"

    staff_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    # role is the free-text display label; person_type is the reliable
    # discriminator (teacher/pilot/admin/staff) that teaching assignments and
    # reporting filter on. role_title replaces the old teachers.role_title.
    role = Column(String(60), nullable=False)
    role_title = Column(String(100))
    person_type = Column(
        String(20), nullable=False, default="staff", server_default="staff"
    )
    phone = Column(String(30))
    email = Column(String(120))
    date_of_birth = Column(Date)
    marital_status = Column(String(20))
    gender = Column(String(20))
    present_address = Column(String(255))
    permanent_address = Column(String(255))
    aadhaar_card = Column(String(30))
    emergency_number = Column(String(30))
    driving_license = Column(String(40))
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        CheckConstraint(
            "person_type IN ('teacher', 'pilot', 'admin', 'staff')",
            name="ck_staff_person_type",
        ),
    )


class Parent(AuditColumnsMixin, Base):
    __tablename__ = "parents"

    parent_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    phone = Column(String(30), nullable=False)
    email = Column(String(120))
    address = Column(String(255))
    emergency_number = Column(String(30))
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class Student(AuditColumnsMixin, Base):
    __tablename__ = "students"

    student_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="RESTRICT"), nullable=False)
    admission_no = Column(String(20), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    date_of_birth = Column(Date)
    gender = Column(String(10))
    photo_url = Column(String(255))
    aadhaar_number = Column(String(20))
    birth_certificate_number = Column(String(40))
    documents = Column(JSON)
    present_today = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class ParentStudent(AuditColumnsMixin, Base):
    __tablename__ = "parent_student"

    id = Column(Integer, primary_key=True)
    parent_id = Column(Integer, ForeignKey("parents.parent_id", ondelete="CASCADE"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False)
    relationship_ = Column("relationship", String(20), nullable=False, default="Parent")

    __table_args__ = (UniqueConstraint("parent_id", "student_id"),)


# ============================================================================
# 4. TRANSPORT / ROUTES
# ============================================================================

class Route(AuditColumnsMixin, Base):
    __tablename__ = "routes"

    route_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    vehicle = Column(String(60), nullable=False)
    # The driver is not stored here: a route's driver is the staff member whose
    # pilots row points at this route. See Pilot.route_id.
    status = Column(String(15), nullable=False, default="Scheduled")
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    pilot = relationship("Pilot", uselist=False, viewonly=True, back_populates="route")

    @property
    def driver_name(self) -> str | None:
        """Derived driver name. Kept in the API so the parent pick/drop
        payload and existing clients are unaffected by the drivers table
        becoming a reference to pilots."""
        if self.pilot is not None and self.pilot.staff is not None:
            return self.pilot.staff.name
        return None


class Vehicle(AuditColumnsMixin, Base):
    __tablename__ = "vehicles"

    vehicle_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    vehicle_number = Column(String(30), nullable=False)
    vehicle_type = Column(String(40))
    registration_number = Column(String(40), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (UniqueConstraint("school_id", "vehicle_number"),)


class RouteStop(AuditColumnsMixin, Base):
    __tablename__ = "route_stops"

    stop_id = Column(Integer, primary_key=True)
    route_id = Column(Integer, ForeignKey("routes.route_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(120), nullable=False)
    stop_time = Column(String(15), nullable=False)
    stop_type = Column(String(10), nullable=False)
    stop_order = Column(Integer, nullable=False, default=1)


class RouteStudent(AuditColumnsMixin, Base):
    __tablename__ = "route_students"

    id = Column(Integer, primary_key=True)
    route_id = Column(Integer, ForeignKey("routes.route_id", ondelete="CASCADE"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False)
    status = Column(String(10), nullable=False, default="pending")  # pending/picked/dropped (app-level, not in original DDL)

    __table_args__ = (
        UniqueConstraint("route_id", "student_id"),
        UniqueConstraint("student_id"),
    )


# ============================================================================
# 5. TIMETABLE
# ============================================================================

class TimetableEntry(AuditColumnsMixin, Base):
    __tablename__ = "timetable_entries"

    entry_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(String(3), nullable=False)
    period_id = Column(Integer, ForeignKey("periods.period_id", ondelete="CASCADE"), nullable=False)
    period_start_time = Column(Time)
    period_end_time = Column(Time)
    subject_id = Column(Integer, ForeignKey("subjects.subject_id", ondelete="SET NULL"))
    staff_id = Column(Integer, ForeignKey("staff.staff_id", ondelete="SET NULL"))
    created_on = Column(DateTime, server_default=text("timezone('Asia/Kolkata', now())"))
    created_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))
    is_holiday_override = Column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("class_id", "day_of_week", "period_id"),)

    @property
    def teacher_id(self) -> int | None:
        return self.staff_id


# ============================================================================
# 6. ATTENDANCE & MARKS
# ============================================================================

class Attendance(AuditColumnsMixin, Base):
    __tablename__ = "attendance"

    attendance_id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    status = Column(String(10), nullable=False)
    marked_by = Column(Integer, ForeignKey("staff.staff_id", ondelete="SET NULL"))

    __table_args__ = (UniqueConstraint("student_id", "date"),)


class Mark(AuditColumnsMixin, Base):
    __tablename__ = "marks"

    mark_id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.subject_id", ondelete="CASCADE"), nullable=False)
    term = Column(String(20), nullable=False, default="Term 1")
    score = Column(Integer, nullable=False)
    updated_by = Column(Integer, ForeignKey("staff.staff_id", ondelete="SET NULL"))
    updated_by_user = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("student_id", "subject_id", "term"),
        CheckConstraint("score BETWEEN 0 AND 100"),
    )


# ============================================================================
# 7. LEAVE REQUESTS
# ============================================================================

class LeaveRequest(AuditColumnsMixin, Base):
    __tablename__ = "leave_requests"

    leave_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    requester_type = Column(String(10), nullable=False)
    requester_name = Column(String(100), nullable=False)
    from_date = Column(Date, nullable=False)
    to_date = Column(Date, nullable=False)
    reason = Column(String(255))
    status = Column(String(10), nullable=False, default="Pending")
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


# ============================================================================
# 8. COMMUNICATION
# ============================================================================

class Broadcast(AuditColumnsMixin, Base):
    __tablename__ = "broadcasts"

    broadcast_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="CASCADE"))
    route_id = Column(Integer, ForeignKey("routes.route_id", ondelete="CASCADE"))
    scope = Column(String(10), nullable=False)
    role_name = Column(String(100), nullable=False)
    sender_name = Column(String(100), nullable=False, server_default="")
    # Who authored this, as a user id. sender_name is only a label and cannot
    # answer "is this mine?": two admins both post as "Admin", and one person's
    # name can change after the fact. NULL for rows written before this column
    # existed, and for seed data with no author.
    sender_user_id = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))
    message = Column(Text, nullable=False)
    created_at = Column(
        DateTime,
        server_default=text("timezone('Asia/Kolkata', now())"),
    )
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class Media(AuditColumnsMixin, Base):
    __tablename__ = "media"

    media_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="SET NULL"))
    title = Column(String(150), nullable=False)
    posted_by = Column(String(100), nullable=False)
    icon = Column(String(10))
    # Who uploaded this, as a user id. posted_by is only a display label and
    # cannot answer "is this mine?": two teachers can share a name, and a name
    # can change after the upload. NULL for rows written before this column
    # existed -- those stay admin-only rather than being guessed at.
    uploader_user_id = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))
    # Gallery media file. file_url is the API path to the stored file; kind is
    # either "image" or "video" (NULL for legacy text-only album rows).
    file_url = Column(String(255))
    media_kind = Column(String(10))
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class BarterListing(AuditColumnsMixin, Base):
    __tablename__ = "barter_listings"

    listing_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    title = Column(String(150), nullable=False)
    price = Column(String(20), nullable=False)
    icon = Column(String(10))
    listed_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class Activity(AuditColumnsMixin, Base):
    __tablename__ = "activities"

    activity_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    tag = Column(String(30), nullable=False)
    title = Column(String(150), nullable=False)
    description = Column(Text)
    published_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


# ============================================================================
# 9. SCHOOL WEBSITE
# ============================================================================

class WebsiteSettings(AuditColumnsMixin, Base):
    __tablename__ = "website_settings"

    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), primary_key=True)
    school_name = Column(String(150), nullable=False)
    tagline = Column(String(200))
    nav_links = Column(String(255), nullable=False, default="Home,About,Academics,Admissions,Contact")
    font_family = Column(String(60), nullable=False, default="Inter, sans-serif")
    font_size = Column(String(10), nullable=False, default="Medium")
    accent_color = Column(String(10), nullable=False, default="#023859")
    icon_url = Column(String(255))
    footer_address = Column(String(200))
    footer_phone = Column(String(30))
    footer_email = Column(String(120))
    footer_copyright = Column(String(150))
    is_active = Column(Boolean, nullable=False, default=False, server_default="false")


class WebsitePage(AuditColumnsMixin, Base):
    __tablename__ = "website_pages"

    page_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    slug = Column(String(20), nullable=False)
    banner_url = Column(String(255))
    heading = Column(String(200), nullable=False)
    subheading = Column(String(255))
    body = Column(Text)
    extra_json = Column(JSONB)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (UniqueConstraint("school_id", "slug"),)


class WebsiteTestimonial(AuditColumnsMixin, Base):
    __tablename__ = "website_testimonials"

    testimonial_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    role = Column(String(100), nullable=False)
    quote = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


# ============================================================================
# 12. ACCOUNTS
# ============================================================================
# One row per person per calendar month. `month` is a 'YYYY-MM' string rather
# than a date because the admin view is a six-column month grid, and a month
# bucket has no meaningful day. The unique constraints make a second payment
# for the same month an overwrite rather than a duplicate row, which is what
# the grid expects: one figure per cell.
#
# Both tables keep their own school_id even though staff and students are
# already school-scoped. It is denormalised on purpose: the accounts read is a
# single grouped query filtered by school, and it makes the ownership check on
# write explicit rather than something a join has to be trusted for.

class StaffSalary(AuditColumnsMixin, Base):
    __tablename__ = "staff_salaries"

    salary_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    staff_id = Column(Integer, ForeignKey("staff.staff_id", ondelete="CASCADE"), nullable=False)
    month = Column(String(7), nullable=False)
    # Numeric, not Float: money does not belong in binary floating point.
    amount = Column(Numeric(12, 2), nullable=False)
    paid_on = Column(Date)
    note = Column(String(200))

    __table_args__ = (
        UniqueConstraint("staff_id", "month"),
    )


class StudentFee(AuditColumnsMixin, Base):
    __tablename__ = "student_fees"

    fee_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False)
    month = Column(String(7), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    paid_on = Column(Date)
    note = Column(String(200))

    __table_args__ = (
        UniqueConstraint("student_id", "month"),
    )


# ============================================================================
# 13. PLATFORM USERS / AUTH
# ============================================================================

class User(AuditColumnsMixin, Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"))
    role = Column(String(10), nullable=False)
    username = Column(String(100), unique=True, nullable=False)
    email = Column(String(255))
    password_hash = Column(String(255), nullable=False)
    linked_person_id = Column(Integer)
    password_reset_token = Column(String(255))
    password_reset_token_expires_at = Column(DateTime)
    last_login = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class Pilot(AuditColumnsMixin, Base):
    """Driver-specific extension of a staff member.

    A pilot row exists only for staff who drive, and holds just the fields a
    generic staff row should not carry. Every personal detail (name, phone,
    addresses, aadhaar) lives on the linked staff row. Because each row holds
    at most one route_id, the driver<->route relationship is 1:1.
    """

    __tablename__ = "pilots"

    pilot_id = Column(Integer, primary_key=True)
    staff_id = Column(Integer, ForeignKey("staff.staff_id", ondelete="CASCADE"), nullable=False, unique=True)
    license_expiry = Column(Date)
    route_id = Column(
        Integer, ForeignKey("routes.route_id", ondelete="SET NULL"), unique=True
    )
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    staff = relationship("Staff", uselist=False, viewonly=True)
    route = relationship(
        "Route", uselist=False, viewonly=True, back_populates="pilot"
    )


class StaffAttendance(AuditColumnsMixin, Base):
    """Dated attendance for any staff member (teacher, pilot, admin, staff).

    Replaces the denormalised teachers.attendance_status column, which could
    only ever hold one current value per teacher.
    """

    __tablename__ = "staff_attendance"

    attendance_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    staff_id = Column(Integer, ForeignKey("staff.staff_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    status = Column(String(10), nullable=False)
    check_in = Column(Time)
    check_out = Column(Time)
    remarks = Column(String(255))
    # Whoever marked the row. An admin accounts for staff attendance, so this
    # points at the login rather than at a teacher record.
    marked_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))
    modified_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))

    __table_args__ = (
        UniqueConstraint("staff_id", "date"),
        CheckConstraint(
            "status IN ('Present','Absent','On leave','Half day')",
            name="staff_attendance_status_check",
        ),
    )


# ============================================================================
# SUPPORT / HELP DESK
# ============================================================================

class SupportTicket(AuditColumnsMixin, Base):
    """An issue a school admin raises for the master admin to handle.

    A ticket belongs to exactly one school and is opened by an admin of that
    school. The master admin sees every school's tickets; an admin only ever
    sees their own school's. The conversation itself lives on
    SupportTicketMessage, so the ticket row stays a small, listable summary.

    `created_by_name` is denormalised at open time (matching leave_requests):
    the display name is resolved from the linked staff row server-side, so a
    ticket keeps reading sensibly even if the account is later removed.
    """

    __tablename__ = "support_tickets"

    ticket_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))
    created_by_name = Column(String(100), nullable=False)
    subject = Column(String(200), nullable=False)
    status = Column(String(20), nullable=False, default="Open", server_default="Open")
    created_at = Column(DateTime, server_default=func.now())

    messages = relationship(
        "SupportTicketMessage",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="SupportTicketMessage.created_at",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('Open','In progress','Assigned','Completed','Cancelled')",
            name="support_tickets_status_check",
        ),
    )


class SupportTicketMessage(AuditColumnsMixin, Base):
    """One turn in a ticket's conversation, including the opening message.

    `author_role` is the account role that wrote it ('admin' or 'master'), so
    the thread reads as two parties rather than as a pile of bodies. The name
    is denormalised for the same reason as the ticket's `created_by_name`.

    `attachments` is a JSON list of URL strings minted by the existing upload
    endpoints; support stores references only and never owns the files.
    """

    __tablename__ = "support_ticket_messages"

    message_id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, ForeignKey("support_tickets.ticket_id", ondelete="CASCADE"), nullable=False)
    author_user_id = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))
    author_role = Column(String(20), nullable=False)
    author_name = Column(String(100), nullable=False)
    body = Column(Text, nullable=False)
    attachments = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, server_default=func.now())

    ticket = relationship("SupportTicket", back_populates="messages")

    __table_args__ = (
        CheckConstraint(
            "author_role IN ('admin','master')",
            name="support_ticket_messages_role_check",
        ),
    )


# Register the audit stamper so every process importing the models configures
# the modified_by/modified_at listener before the first flush.
import common.audit  # noqa: E402,F401