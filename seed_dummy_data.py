"""Seed 2 schools with comprehensive dummy data for all flows (idempotent)."""
import os
os.environ["DATABASE_URL"] = "postgresql://ravi@localhost:5432/schoolersdb?options=-csearch_path%3DSchoolers"

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError

from common.models import (
    Base, School, SchoolClass, Subject, Staff, Pilot, Student, Parent, ParentStudent,
    Staff, Mark, TimetableEntry, TeacherClassSubject, Period,
    Media, LeaveRequest, Route, Broadcast,
)

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True, future=True)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
db = Session()

CONTACT = "8105096987"
EMAIL = "ravigupta0307@gmail.com"

def safe_add(obj):
    try:
        db.merge(obj)
        db.flush()
    except IntegrityError:
        db.rollback()

# ---- Schools ----
for sid, name, city, addr, pin in [
    (7, "Ravi Public School", "Delhi", "123 Main Street, Sector 15", "110015"),
    (8, "Green Valley Academy", "Mumbai", "456 Park Avenue, Block B", "400001"),
]:
    school = School(
        school_id=sid, name=name, address=addr, pincode=pin,
        city=city, state=city, country="India",
        primary_contact=CONTACT, alternative_contact=CONTACT,
        primary_email=EMAIL, alternative_email=EMAIL,
        first_name="Ravi", last_name=name.split()[0],
        status="Active",
    )
    safe_add(school)
db.commit()
print("Created 2 schools")

# ---- Periods ----
existing_period_nos = {p.period_no for p in db.query(Period).all()}
for pno, ptime in [(2, "08:00-09:00"), (3, "09:00-10:00")]:
    if pno not in existing_period_nos:
        safe_add(Period(period_no=pno, period_time=ptime))
db.commit()
period_ids = [p.period_id for p in db.query(Period).all()]
print("Created periods")

# ---- Classes ----
for school_id, prefix in [(7, "RPS"), (8, "GVA")]:
    existing = {
        (c.school_id, c.name) for c in db.query(SchoolClass).filter(SchoolClass.school_id == school_id).all()
    }
    for grade in ["I", "II", "III", "IV", "V", "VI"]:
        if (school_id, f"{grade}-{prefix}") not in existing:
            safe_add(SchoolClass(school_id=school_id, name=f"{grade}-{prefix}", student_count=30))
db.commit()
print("Created classes")

# ---- Subjects ----
subj_map = {}
for school_id in [7, 8]:
    existing = {s.name for s in db.query(Subject).filter(Subject.school_id == school_id).all()}
    for name in ["Maths", "Hindi", "Science", "English"]:
        if name not in existing:
            safe_add(Subject(school_id=school_id, name=name, is_active=True))
        if (school_id, name) not in subj_map:
            subj_map[(school_id, name)] = db.query(Subject).filter(Subject.school_id == school_id, Subject.name == name).one().subject_id
db.commit()
print("Created subjects")

# ---- Teachers ----
teacher_map = {}
for school_id, prefix in [(7, "RPS"), (8, "GVA")]:
    existing_names = {
        t.name for t in db.query(Staff).filter(
            Staff.school_id == school_id, Staff.person_type == "teacher"
        ).all()
    }
    for i in range(1, 4):
        tname = f"Teacher{i} {prefix}"
        if tname not in existing_names:
            safe_add(Staff(
                school_id=school_id, name=tname, role="Teacher",
                person_type="teacher", role_title="Subject Teacher",
                phone=CONTACT, email=EMAIL, gender="Male",
                date_of_birth=datetime(1985, 1, 1).date(),
            ))
        t = db.query(Staff).filter(
            Staff.school_id == school_id, Staff.name == tname
        ).first()
        if t:
            teacher_map[(school_id, i)] = t.staff_id
db.commit()
print("Created teachers")

# ---- Students & Parents ----
student_map = {}
for school_id, prefix in [(7, "RPS"), (8, "GVA")]:
    cls_list = [c for c in db.query(SchoolClass).filter(SchoolClass.school_id == school_id).limit(5).all()]
    for idx, cls in enumerate(cls_list):
        for stud_num in range(1, 4):
            adm = f"ADM{school_id}{idx}{stud_num:03d}"
            existing = db.query(Student).filter(Student.school_id == school_id, Student.admission_no == adm).first()
            if existing:
                student_map[(school_id, idx, stud_num)] = existing.student_id
                continue
            stu = Student(
                school_id=school_id, class_id=cls.class_id,
                admission_no=adm,
                name=f"Student{idx}{stud_num} {prefix}",
                date_of_birth=datetime(2012, 1, 1).date(),
                gender="Male",
            )
            safe_add(stu)
            stu_id = db.query(Student).filter(Student.school_id == school_id, Student.admission_no == adm).one().student_id
            student_map[(school_id, idx, stud_num)] = stu_id
            parent = Parent(
                school_id=school_id,
                name=f"Parent{idx}{stud_num} {prefix}",
                phone=CONTACT, email=EMAIL,
                address=f"Address {idx}", emergency_number=CONTACT,
            )
            safe_add(parent)
            parent_rec = db.query(Parent).filter(Parent.school_id == school_id, Parent.name == f"Parent{idx}{stud_num} {prefix}").one()
            db.merge(ParentStudent(parent_id=parent_rec.parent_id, student_id=stu_id, relationship_="Parent"))
db.commit()
print("Created students and parents")

# ---- Staff ----
for school_id, prefix in [(7, "RPS"), (8, "GVA")]:
    existing = {s.name for s in db.query(Staff).filter(Staff.school_id == school_id).all()}
    for i in range(1, 3):
        sname = f"Staff{i} {prefix}"
        if sname not in existing:
            safe_add(Staff(school_id=school_id, name=sname, role="Helper", phone=CONTACT, email=EMAIL, gender="Male", date_of_birth=datetime(1980, 1, 1).date()))
db.commit()
print("Created staff")

# ---- TeacherClassSubject ----
for school_id in [7, 8]:
    cls_list = [c for c in db.query(SchoolClass).filter(SchoolClass.school_id == school_id).limit(3).all()]
    subj_names = ["Maths", "Hindi", "Science", "English"]
    for idx, cls in enumerate(cls_list):
        for j, name in enumerate(subj_names):
            subj_id = subj_map[(school_id, name)]
            t_id = teacher_map[(school_id, (j % 3) + 1)]
            existing = db.query(TeacherClassSubject).filter(TeacherClassSubject.staff_id == t_id, TeacherClassSubject.class_id == cls.class_id, TeacherClassSubject.subject_id == subj_id).first()
            if not existing:
                safe_add(TeacherClassSubject(staff_id=t_id, class_id=cls.class_id, subject_id=subj_id))
db.commit()
print("Linked teachers to subjects/classes")

# ---- Media ----
for school_id, prefix in [(7, "RPS"), (8, "GVA")]:
    existing_titles = {m.title for m in db.query(Media).filter(Media.school_id == school_id).all()}
    for i in range(1, 5):
        title = f"{prefix} Gallery {i}"
        if title not in existing_titles:
            safe_add(Media(school_id=school_id, class_id=None, title=title, posted_by=EMAIL, file_url=f"/api/v1/media/files/{prefix.lower()}_gallery_{i}.jpg", media_kind="image", created_at=datetime.now() - timedelta(days=i)))
    for i in range(5, 7):
        title = f"{prefix} Album"
        if title not in existing_titles:
            safe_add(Media(school_id=school_id, class_id=None, title=title, posted_by=EMAIL, file_url=f"/api/v1/media/files/{prefix.lower()}_album_{i}.jpg", media_kind="image", created_at=datetime.now() - timedelta(days=i)))
db.commit()
print("Created media/gallery")

# ---- Timetable entries ----
for school_id, prefix in [(7, "RPS"), (8, "GVA")]:
    cls_list = [c for c in db.query(SchoolClass).filter(SchoolClass.school_id == school_id).limit(2).all()]
    subj_names = ["Maths", "Hindi", "Science", "English"]
    day_idx = 0
    for cls in cls_list:
        for name in subj_names:
            subj_id = subj_map[(school_id, name)]
            tcs = db.query(TeacherClassSubject).filter(TeacherClassSubject.class_id == cls.class_id, TeacherClassSubject.subject_id == subj_id).first()
            if not tcs or not period_ids:
                continue
            existing = db.query(TimetableEntry).filter(TimetableEntry.school_id == school_id, TimetableEntry.class_id == cls.class_id, TimetableEntry.day_of_week == ["MON", "TUE", "WED", "THU", "FRI"][day_idx % 5], TimetableEntry.period_id == period_ids[day_idx % len(period_ids)], TimetableEntry.subject_id == subj_id).first()
            if not existing:
                safe_add(TimetableEntry(school_id=school_id, class_id=cls.class_id, day_of_week=["MON", "TUE", "WED", "THU", "FRI"][day_idx % 5], period_id=period_ids[day_idx % len(period_ids)], subject_id=subj_id, staff_id=tcs.staff_id, created_on=datetime.now()))
            day_idx += 1
db.commit()
print("Created timetable entries")

# ---- Marks ----
for school_id in [7, 8]:
    cls_list = [c for c in db.query(SchoolClass).filter(SchoolClass.school_id == school_id).limit(3).all()]
    subj_names = ["Maths", "Hindi", "Science", "English"]
    for idx, cls in enumerate(cls_list):
        for stud_num in range(1, 4):
            adm = f"ADM{school_id}{idx}{stud_num:03d}"
            stu = db.query(Student).filter(Student.school_id == school_id, Student.admission_no == adm).first()
            if not stu:
                continue
            for subj_name in subj_names:
                subj_id = subj_map[(school_id, subj_name)]
                existing = db.query(Mark).filter(Mark.student_id == stu.student_id, Mark.subject_id == subj_id).first()
                if not existing:
                    safe_add(Mark(student_id=stu.student_id, subject_id=subj_id, term=f"Term {(subj_names.index(subj_name) % 2) + 1}", score=70 + (subj_names.index(subj_name) * 5) + stud_num))
db.commit()
print("Created marks")

# ---- Leave requests ----
for school_id in [7, 8]:
    stus = db.query(Student).filter(Student.school_id == school_id).limit(4).all()
    for stu in stus:
        existing = db.query(LeaveRequest).filter(LeaveRequest.school_id == school_id, LeaveRequest.requester_name == stu.name).first()
        if not existing:
            safe_add(LeaveRequest(school_id=school_id, requester_type="Student", requester_name=stu.name, from_date=datetime.now().date(), to_date=datetime.now().date() + timedelta(days=1), reason="Fever", status="Approved", created_at=datetime.now()))
db.commit()
print("Created leave requests")

# ---- Routes ----
for school_id, prefix in [(7, "RPS"), (8, "GVA")]:
    existing = db.query(Route).filter(Route.school_id == school_id).first()
    if not existing:
        driver_name = f"Driver {prefix}"
        route = Route(
            school_id=school_id, name=f"{prefix} Main Route",
            vehicle="Bus #101", status="Scheduled",
        )
        safe_add(route)
        db.flush()
        driver = db.query(Staff).filter(
            Staff.school_id == school_id, Staff.name == driver_name
        ).first()
        if driver is None:
            driver = Staff(
                school_id=school_id, name=driver_name, role="Pilot",
                person_type="pilot", phone=CONTACT, email=EMAIL,
            )
            safe_add(driver)
            db.flush()
        # One pilot row per staff member, so reuse it when re-seeding.
        pilot = db.query(Pilot).filter(Pilot.staff_id == driver.staff_id).first()
        if pilot is None:
            pilot = Pilot(staff_id=driver.staff_id, is_active=True)
            safe_add(pilot)
            db.flush()
        # A route has exactly one driver.
        pilot.route_id = route.route_id
db.commit()
print("Created routes")

# ---- Broadcasts ----
for school_id, prefix in [(7, "RPS"), (8, "GVA")]:
    existing = db.query(Broadcast).filter(Broadcast.school_id == school_id).first()
    if not existing:
        safe_add(Broadcast(school_id=school_id, scope="School", role_name="all", sender_name=prefix, message=f"Welcome to {prefix}!"))
db.commit()
print("Created broadcasts")

print("\n=== DONE ===")
print("School 7: Ravi Public School (Delhi)")
print("School 8: Green Valley Academy (Mumbai)")
print("All dummy data seeded: classes, subjects, teachers, students, parents, staff, media, timetable, marks, leave, routes, broadcasts")
