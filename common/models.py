"""
SQLAlchemy ORM models — one class per table in schoolers_schema.sql.
Grouped by domain with comments; kept in one file deliberately so the
mapping to the schema is easy to audit in one place. Individual API
modules import only what they need from here.
"""
from datetime import date, datetime

from sqlalchemy import (
    Column, Integer, String, Boolean, Text, Date, DateTime, ForeignKey,
    UniqueConstraint, CheckConstraint, func
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from common.database import Base

# ============================================================================
# 1. PLATFORM / MASTER ADMIN
# ============================================================================

class School(Base):
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
    logo_url = Column(String(255))
    status = Column(String(10), nullable=False, default="Active")
    route_enabled = Column(Boolean, nullable=False, default=False)
    website_enabled = Column(Boolean, nullable=False, default=False)
    library_enabled = Column(Boolean, nullable=False, default=False)
    fees_enabled = Column(Boolean, nullable=False, default=False)
    salary_enabled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class SchoolNotification(Base):
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

class Subject(Base):
    __tablename__ = "subjects"

    subject_id = Column(Integer, primary_key=True)
    name = Column(String(40), unique=True, nullable=False)


class SchoolClass(Base):
    __tablename__ = "classes"

    class_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(40), nullable=False)
    class_teacher_id = Column(Integer, ForeignKey("teachers.teacher_id", ondelete="SET NULL"))
    student_count = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (UniqueConstraint("school_id", "name"),)


class Period(Base):
    __tablename__ = "periods"

    period_id = Column(Integer, primary_key=True)
    period_no = Column(Integer, nullable=False, unique=True)
    period_time = Column(String(15), nullable=False)


class Holiday(Base):
    __tablename__ = "holidays"

    holiday_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(String(3), nullable=False)
    is_holiday = Column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("school_id", "day_of_week"),)


# ============================================================================
# 3. PEOPLE
# ============================================================================

class Teacher(Base):
    __tablename__ = "teachers"

    teacher_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    role_title = Column(String(100), nullable=False)
    phone = Column(String(30), nullable=False)
    email = Column(String(120))
    present_address = Column(String(255))
    permanent_address = Column(String(255))
    date_of_birth = Column(Date)
    emergency_number = Column(String(30))
    gender = Column(String(20))
    attendance_status = Column(String(15), nullable=False, default="On time")
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class TeacherClassSubject(Base):
    __tablename__ = "teacher_class_subjects"

    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey("teachers.teacher_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="CASCADE"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.subject_id", ondelete="CASCADE"), nullable=False)
    is_class_teacher = Column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("teacher_id", "class_id", "subject_id"),)


class Staff(Base):
    __tablename__ = "staff"

    staff_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    role = Column(String(60), nullable=False)
    phone = Column(String(30))
    email = Column(String(120))
    present_address = Column(String(255))
    permanent_address = Column(String(255))
    aadhaar_card = Column(String(30))
    emergency_number = Column(String(30))
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class Parent(Base):
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


class Student(Base):
    __tablename__ = "students"

    student_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="RESTRICT"), nullable=False)
    admission_no = Column(String(20), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    date_of_birth = Column(Date)
    gender = Column(String(10))
    present_today = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class ParentStudent(Base):
    __tablename__ = "parent_student"

    id = Column(Integer, primary_key=True)
    parent_id = Column(Integer, ForeignKey("parents.parent_id", ondelete="CASCADE"), nullable=False)
    student_id = Column(Integer, ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False)
    relationship_ = Column("relationship", String(20), nullable=False, default="Parent")

    __table_args__ = (UniqueConstraint("parent_id", "student_id"),)


# ============================================================================
# 4. TRANSPORT / ROUTES
# ============================================================================

class Route(Base):
    __tablename__ = "routes"

    route_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    vehicle = Column(String(60), nullable=False)
    driver_name = Column(String(100), nullable=False)
    status = Column(String(15), nullable=False, default="Scheduled")
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class Vehicle(Base):
    __tablename__ = "vehicles"

    vehicle_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    vehicle_number = Column(String(30), nullable=False)
    vehicle_type = Column(String(40))
    registration_number = Column(String(40), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (UniqueConstraint("school_id", "vehicle_number"),)


class RouteStop(Base):
    __tablename__ = "route_stops"

    stop_id = Column(Integer, primary_key=True)
    route_id = Column(Integer, ForeignKey("routes.route_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(120), nullable=False)
    stop_time = Column(String(15), nullable=False)
    stop_type = Column(String(10), nullable=False)
    stop_order = Column(Integer, nullable=False, default=1)


class RouteStudent(Base):
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

class TimetableEntry(Base):
    __tablename__ = "timetable_entries"

    entry_id = Column(Integer, primary_key=True)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(String(3), nullable=False)
    period_id = Column(Integer, ForeignKey("periods.period_id", ondelete="CASCADE"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.subject_id", ondelete="SET NULL"))
    teacher_id = Column(Integer, ForeignKey("teachers.teacher_id", ondelete="SET NULL"))
    is_holiday_override = Column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("class_id", "day_of_week", "period_id"),)


# ============================================================================
# 6. ATTENDANCE & MARKS
# ============================================================================

class Attendance(Base):
    __tablename__ = "attendance"

    attendance_id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    status = Column(String(10), nullable=False)
    marked_by = Column(Integer, ForeignKey("teachers.teacher_id"))

    __table_args__ = (UniqueConstraint("student_id", "date"),)


class Mark(Base):
    __tablename__ = "marks"

    mark_id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("students.student_id", ondelete="CASCADE"), nullable=False)
    subject_id = Column(Integer, ForeignKey("subjects.subject_id", ondelete="CASCADE"), nullable=False)
    term = Column(String(20), nullable=False, default="Term 1")
    score = Column(Integer, nullable=False)
    updated_by = Column(Integer, ForeignKey("teachers.teacher_id"))
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("student_id", "subject_id", "term"),
        CheckConstraint("score BETWEEN 0 AND 100"),
    )


# ============================================================================
# 7. LEAVE REQUESTS
# ============================================================================

class LeaveRequest(Base):
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

class Broadcast(Base):
    __tablename__ = "broadcasts"

    broadcast_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="CASCADE"))
    scope = Column(String(10), nullable=False)
    from_name = Column(String(100), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class Media(Base):
    __tablename__ = "media"

    media_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    class_id = Column(Integer, ForeignKey("classes.class_id", ondelete="SET NULL"))
    title = Column(String(150), nullable=False)
    posted_by = Column(String(100), nullable=False)
    icon = Column(String(10))
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class BarterListing(Base):
    __tablename__ = "barter_listings"

    listing_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    title = Column(String(150), nullable=False)
    price = Column(String(20), nullable=False)
    icon = Column(String(10))
    listed_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class Activity(Base):
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

class WebsiteSettings(Base):
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
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class WebsitePage(Base):
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


class WebsiteTestimonial(Base):
    __tablename__ = "website_testimonials"

    testimonial_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)
    role = Column(String(100), nullable=False)
    quote = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


# ============================================================================
# 10. PLATFORM USERS / AUTH
# ============================================================================

class User(Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"))
    role = Column(String(10), nullable=False)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    linked_person_id = Column(Integer)
    last_login = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")


class Pilot(Base):
    __tablename__ = "pilots"

    pilot_id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, unique=True)
    school_id = Column(Integer, ForeignKey("schools.school_id", ondelete="CASCADE"), nullable=False)
    full_name = Column(String(100), nullable=False)
    email = Column(String(120))
    phone = Column(String(30), nullable=False)
    present_address = Column(String(255))
    permanent_address = Column(String(255))
    aadhaar_number = Column(String(30))
    dl_number = Column(String(40))
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime, server_default=func.now())
