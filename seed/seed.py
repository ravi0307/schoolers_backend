"""Create two complete schools through the real gateway API.

Run only after `seed/run.py reset`. Reads nothing from the DB except to attach
teacher/staff/parent logins (which the API has no endpoint for) and to
normalise the global periods table, which the timetable API cannot address
directly.
"""
from __future__ import annotations

import datetime as dt
import io
import sys
import time
from pathlib import Path

from sqlalchemy import text

from seed import db
from seed.client import Api
from seed.fixtures import (
    ADMIN_PW, BREAK_INDEX, DAYS, MASTER_PW, PARENT_PW, PERIOD_TIMES,
    PILOT_PW, SCHOOLS, STAFF_PW, SUBJECTS, TEACHER_PW,
)

SCHEMA = "schoolers"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f0300050001a5f6453f0000000049454e44ae426082"
)


def log(msg: str) -> None:
    print(f"[seed] {msg}", flush=True)


def month_str(offset: int) -> str:
    today = dt.date.today()
    y, m = today.year, today.month - offset
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def date_str(days: int) -> str:
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


class SchoolSeed:
    def __init__(self, cfg, engine):
        self.cfg = cfg
        self.engine = engine
        self.api = Api()
        self.school_id: int = 0
        self.admin_username: str = ""
        self.master_token: str = ""
        self.admin_token: str = ""
        self.classes: list[dict] = []
        self.subjects: list[dict] = []
        self.teachers: list[dict] = []
        self.staff: list[dict] = []
        self.parents: list[dict] = []
        self.students: list[dict] = []
        self.pilots: list[dict] = []
        self.routes: list[dict] = []
        self.assign: dict[tuple[int, int], int] = {}

    # -- lifecycle -------------------------------------------------------
    def run(self) -> None:
        cfg = self.cfg
        log(f"{cfg.name}: master login as {cfg.master_username}")
        self.master_token = self.api.login_token(cfg.master_username, MASTER_PW)
        self.api.set_token(self.master_token)
        self.create_school()
        self.admin_token = self.api.login_token(self.admin_username, self._admin_pw)
        self.api.set_token(self.admin_token)
        self.create_subjects_classes()
        self.create_people()
        self.create_logins()
        self.create_assignments()
        self.create_timetable()
        self.create_attendance_marks()
        self.create_transport()
        self.create_staff_attendance()
        self.create_accounts()
        self.create_communications_and_misc()
        self.api.close()

    def create_school(self) -> None:
        cfg = self.cfg
        school = self.api.post("/schools", json={
            "name": cfg.name, "address": cfg.address, "pincode": cfg.pincode,
            "city": cfg.city, "state": cfg.state, "country": "India",
            "primary_contact": cfg.primary_contact, "primary_email": cfg.primary_email,
            "alternative_contact": cfg.primary_contact,
            "alternative_email": cfg.primary_email,
            "first_name": cfg.first_name, "last_name": cfg.last_name,
        })
        self.school_id = school["school_id"]
        self.admin_username = school["admin_username"]
        self._admin_pw = school["admin_password"]
        log(f"{cfg.name}: created school_id={self.school_id} admin={self.admin_username}")
        temp_token = self.api.login_token(self.admin_username, self._admin_pw)
        self.api.change_password(temp_token, self._admin_pw, ADMIN_PW)
        self._admin_pw = ADMIN_PW

    # -- academics / people ---------------------------------------------
    def create_subjects_classes(self) -> None:
        for name in SUBJECTS:
            self.subjects.append(self.api.post("/subjects", json={"name": name}))
        for grade in ("I", "II", "III", "IV", "V", "VI"):
            self.classes.append(self.api.post("/classes", json={"name": f"Grade {grade}"}))
        log(f"{self.cfg.name}: {len(self.subjects)} subjects, {len(self.classes)} classes")

    def create_people(self) -> None:
        cfg = self.cfg
        for i, name in enumerate(cfg.teacher_names, start=1):
            t = self.api.post("/teachers", json={
                "name": name, "role_title": "Subject Teacher",
                "phone": f"98450{self.school_id}{i:03d}"[:12],
                "email": cfg.primary_email, "gender": "Female" if i % 2 else "Male",
            })
            self.teachers.append(t)
        for i, name in enumerate(cfg.staff_names, start=1):
            s = self.api.post("/staff", json={
                "name": name, "role": "Support Staff", "person_type": "staff",
                "phone": f"98451{self.school_id}{i:03d}"[:12], "email": cfg.primary_email,
                "date_of_birth": "1988-06-15", "marital_status": "Married",
                "gender": "Male" if i % 2 else "Female",
                "present_address": f"{self.school_id} Staff Colony, {cfg.city}",
                "permanent_address": f"{self.school_id} Staff Colony, {cfg.city}",
                "aadhaar_card": f"9000{self.school_id}{i:04d}",
                "emergency_number": cfg.primary_contact,
            })
            self.staff.append(s)
        for i, name in enumerate(cfg.parent_names, start=1):
            p = self.api.post("/parents", json={
                "name": name, "phone": f"98452{self.school_id}{i:03d}"[:12],
                "email": cfg.primary_email,
            })
            self.parents.append(p)
        for i, name in enumerate(cfg.student_names, start=1):
            cls = self.classes[(i - 1) % len(self.classes)]
            parent = self.parents[(i - 1) % len(self.parents)]
            s = self.api.post("/students", json={
                "class_id": cls["class_id"], "name": name,
                "admission_no": f"{cfg.code.upper()}-{i:04d}",
                "gender": "Female" if i % 2 else "Male",
                "date_of_birth": f"2015-{((i - 1) % 12) + 1:02d}-10",
                "parent_id": parent["parent_id"],
            })
            self.students.append(s)
        log(f"{self.cfg.name}: {len(self.teachers)} teachers, {len(self.staff)} staff, "
            f"{len(self.parents)} parents, {len(self.students)} students")

    def create_logins(self) -> None:
        """The API creates no login for teachers/staff/parents — insert them here."""
        cfg = self.cfg
        with self.engine.begin() as c:
            for i, t in enumerate(self.teachers, start=1):
                db.ensure_login(c, school_id=self.school_id, role="teacher",
                                username=f"{cfg.code}.teacher{i}", password=TEACHER_PW,
                                email=cfg.primary_email, linked_person_id=t["staff_id"])
            for i, s in enumerate(self.staff, start=1):
                db.ensure_login(c, school_id=self.school_id, role="staff",
                                username=f"{cfg.code}.staff{i}", password=STAFF_PW,
                                email=cfg.primary_email, linked_person_id=s["staff_id"])
            for i, p in enumerate(self.parents, start=1):
                db.ensure_login(c, school_id=self.school_id, role="parent",
                                username=f"{cfg.code}.parent{i}", password=PARENT_PW,
                                email=cfg.primary_email, linked_person_id=p["parent_id"])
        log(f"{self.cfg.name}: inserted {len(self.teachers)+len(self.staff)+len(self.parents)} logins")

    def create_assignments(self) -> None:
        for ci, cls in enumerate(self.classes):
            for si, subj in enumerate(self.subjects):
                teacher = self.teachers[(ci + si) % len(self.teachers)]
                self.api.post("/teachers/assignments", json={
                    "staff_id": teacher["staff_id"], "class_id": cls["class_id"],
                    "subject_id": subj["subject_id"],
                    "is_class_teacher": si == 0,
                })
                self.assign[(cls["class_id"], subj["subject_id"])] = teacher["staff_id"]
        log(f"{self.cfg.name}: {len(self.classes)*len(self.subjects)} assignments")

    def create_timetable(self) -> None:
        calls = 0
        for cls in self.classes:
            for di, day in enumerate(DAYS):
                for pi, ptime in enumerate(PERIOD_TIMES):
                    payload = {"period_time": ptime, "day_of_week": day}
                    if pi != BREAK_INDEX:
                        subj = self.subjects[(pi + di) % len(self.subjects)]
                        payload["subject_id"] = subj["subject_id"]
                        payload["teacher_id"] = self.assign[(cls["class_id"], subj["subject_id"])]
                    self.api.post(f"/timetable/class/{cls['class_id']}/period", json=payload)
                    calls += 1
        log(f"{self.cfg.name}: {calls} timetable entries")

    def create_attendance_marks(self) -> None:
        d = date_str(-1)
        for cls in self.classes:
            roster = [s for s in self.students if s["class_id"] == cls["class_id"]]
            entries = [{"student_id": s["student_id"],
                        "status": "Absent" if i % 7 == 0 else "Present"}
                       for i, s in enumerate(roster)]
            if entries:
                self.api.post("/attendance/mark", json={
                    "class_id": cls["class_id"], "date": d, "entries": entries})
        for s in self.students:
            for k, subj in enumerate(self.subjects[:4]):
                self.api.put(f"/marks/{s['student_id']}/{subj['subject_id']}",
                             json={"score": 55 + (k * 6) + (s["student_id"] % 20),
                                   "term": f"Term {(k % 2) + 1}"})
        log(f"{self.cfg.name}: attendance + {len(self.students)*4} marks")

    def create_transport(self) -> None:
        cfg = self.cfg
        vehicles = []
        for i, vn in enumerate(cfg.vehicle_numbers):
            vehicles.append(self.api.post("/routes/vehicles", json={
                "vehicle_number": vn, "registration_number": vn.replace("-", ""),
                "vehicle_type": "Bus",
            }))
        for i, name in enumerate(cfg.pilot_names, start=1):
            self.pilots.append(self.api.post("/routes/pilots", json={
                "username": f"{cfg.code}.pilot{i}", "password": PILOT_PW,
                "full_name": name, "phone": f"98453{self.school_id}{i:03d}"[:12],
                "email": cfg.primary_email,
            }))
        for i, rname in enumerate(cfg.route_names):
            route = self.api.post("/routes", json={
                "name": rname, "vehicle": vehicles[i]["vehicle_number"],
                "driver_pilot_id": self.pilots[i]["pilot_id"], "status": "Scheduled",
            })
            self.routes.append(route)
            for stop_no, (stop, ptime, dtime) in enumerate([
                (f"{rname.split()[0]} Stop A", "07:15 AM", "04:00 PM"),
                (f"{rname.split()[0]} Stop B", "07:35 AM", "04:20 PM"),
            ], start=1):
                self.api.post(f"/routes/{route['route_id']}/stops", json={
                    "stop_name": stop, "pickup_time": ptime, "drop_time": dtime,
                    "pickup_order": stop_no, "drop_order": stop_no,
                })
            for s in self.students[i::len(cfg.route_names)][:4]:
                self.api.post(f"/routes/{route['route_id']}/students/{s['student_id']}")
        log(f"{self.cfg.name}: {len(vehicles)} vehicles, {len(self.pilots)} pilots, "
            f"{len(self.routes)} routes")

    def create_staff_attendance(self) -> None:
        staff_ids = ([t["staff_id"] for t in self.teachers]
                     + [s["staff_id"] for s in self.staff]
                     + [p["staff_id"] for p in self.pilots])
        for day_offset in (-1, -2):
            entries = [{"staff_id": sid,
                        "status": "Absent" if i % 9 == 0 else "Present"}
                       for i, sid in enumerate(staff_ids)]
            self.api.post("/attendance/staff/mark", json={
                "date": date_str(day_offset), "entries": entries})
        log(f"{self.cfg.name}: staff attendance for {len(staff_ids)} staff x 2 days")

    def create_accounts(self) -> None:
        all_staff = self.teachers + self.staff
        for m in range(3):
            month = month_str(m)
            for i, s in enumerate(all_staff):
                self.api.post("/accounts/salaries", json={
                    "staff_id": s["staff_id"], "month": month,
                    "amount": 30000 + (i * 1500), "paid_on": date_str(-m * 30),
                    "note": f"Salary {month}",
                })
            for i, stu in enumerate(self.students):
                self.api.post("/accounts/fees", json={
                    "student_id": stu["student_id"], "month": month,
                    "amount": 4500, "paid_on": date_str(-m * 30),
                    "note": f"Tuition {month}",
                })
        log(f"{self.cfg.name}: salaries + fees for 3 months")

    def create_communications_and_misc(self) -> None:
        cfg = self.cfg
        # holidays
        for i, (occ, off) in enumerate([
            ("Gandhi Jayanti", 10), ("Diwali Break", 30), ("Christmas", 60),
            ("Republic Day", 80), ("Holi", 120),
        ]):
            self.api.post("/holidays", json={"occasion": occ, "holiday_date": date_str(off)})
        # leave
        leave_ids = []
        for i, (rtype, name) in enumerate([
            ("Teacher", self.teachers[0]["name"]), ("Teacher", self.teachers[1]["name"]),
            ("Staff", self.staff[0]["name"]), ("Pilot", cfg.pilot_names[0]),
            ("Student", self.students[0]["name"]), ("Student", self.students[1]["name"]),
        ]):
            lv = self.api.post("/leave", json={
                "requester_type": rtype, "requester_name": name,
                "from_date": date_str(-i - 1), "to_date": date_str(-i),
                "reason": "Personal",
            })
            leave_ids.append(lv["leave_id"])
        self.api.patch(f"/leave/{leave_ids[0]}/approve")
        self.api.patch(f"/leave/{leave_ids[1]}/reject")
        self.api.patch(f"/leave/{leave_ids[2]}/approve")
        # broadcasts
        self.api.post("/broadcasts", json={"scope": "school",
                                           "message": f"Welcome to {cfg.name}!"})
        self.api.post("/broadcasts", json={"scope": "class",
                                           "class_id": self.classes[0]["class_id"],
                                           "message": "Science exhibition on Friday."})
        self.api.post("/broadcasts", json={"scope": "route",
                                           "route_id": self.routes[0]["route_id"],
                                           "message": "Bus delayed by 10 minutes."})
        self.api.post("/broadcasts", json={"scope": "pilot",
                                           "message": "Safety briefing at 6:30 AM."})
        # gallery
        for i in range(1, 7):
            self.api.post("/media", data={"title": f"{cfg.code.upper()} Gallery {i}"},
                          files={"file": (f"img{i}.png", io.BytesIO(PNG), "image/png")})
        # website
        self.api.put("/website/settings", json={
            "school_name": cfg.name, "tagline": "Learn. Grow. Lead.",
            "font_family": "Inter", "accent_color": "#1d4ed8",
            "footer_address": cfg.address, "footer_phone": cfg.primary_contact,
            "footer_email": cfg.primary_email,
            "footer_copyright": f"© {dt.date.today().year} {cfg.name}",
        })
        for slug, heading in [
            ("home", "Welcome"), ("about", "About Us"), ("academics", "Academics"),
            ("admissions", "Admissions"), ("contact", "Contact Us"),
        ]:
            self.api.put(f"/website/pages/{slug}", json={
                "heading": f"{heading} — {cfg.name}",
                "subheading": "A place to learn and grow",
                "body": f"Discover {cfg.name} in {cfg.city}.",
            })
        for name, role, quote in [
            (self.parents[0]["name"], "Parent", "Great teachers and caring staff."),
            (self.parents[1]["name"], "Parent", "My child loves going to school."),
            (self.students[0]["name"], "Student", "The labs are amazing!"),
            (self.teachers[0]["name"], "Teacher", "A wonderful place to work."),
        ]:
            self.api.post("/website/testimonials", json={
                "name": name, "role": role, "quote": quote})
        self.api.post("/website/go-live")
        # activities
        for tag, title in zip(["Sports", "Arts", "Academics", "Community"], cfg.clubs):
            self.api.post("/activities", json={
                "tag": tag, "title": title,
                "description": f"{title} at {cfg.name}."})
        # support (admin raises)
        tickets = []
        for subject, body in [
            ("Unable to export fee report", "The fee export button shows an error."),
            ("Add a new subject", "Please add 'Robotics' to the subject list."),
        ]:
            tickets.append(self.api.post("/support/tickets", json={
                "subject": subject, "body": body, "attachments": []}))
        self.master_api().post(
            f"/support/tickets/{tickets[0]['ticket_id']}/messages",
            json={"body": "Thanks — our team is looking into this.", "attachments": []})
        self.master_api().patch(
            f"/support/tickets/{tickets[0]['ticket_id']}/status",
            json={"status": "Completed"})
        self.master_api().patch(
            f"/support/tickets/{tickets[1]['ticket_id']}/status",
            json={"status": "In progress"})
        # parent-only barter
        parent_api = Api()
        parent_api.set_token(parent_api.login_token(f"{cfg.code}.parent1", PARENT_PW))
        for i, (title, price) in enumerate([
            ("Grade III Maths textbook", "₹200"),
            ("School blazer (size 32)", "₹700"),
            ("Cyclone bicycle", "₹3,500"),
        ]):
            parent_api.post("/barter", json={
                "title": title, "price": price, "listed_by": self.parents[0]["name"]})
        parent_api.close()
        log(f"{cfg.name}: holidays, leave, broadcasts, media, website, activities, support, barter")

    def master_api(self) -> Api:
        api = Api()
        api.set_token(self.master_token)
        return api


def normalize_periods(engine) -> None:
    with engine.begin() as c:
        canonical = []
        for i, ptime in enumerate(PERIOD_TIMES):
            pid = c.execute(
                text(f"INSERT INTO {SCHEMA}.periods (period_no, period_time) "
                     "VALUES (:n, :t) RETURNING period_id"),
                {"n": 1001 + i, "t": ptime},
            ).scalar_one()
            canonical.append((i + 1, ptime, pid))
        for no, ptime, pid in canonical:
            c.execute(text(
                f"UPDATE {SCHEMA}.timetable_entries SET period_id = :pid "
                f"WHERE period_id IN (SELECT period_id FROM {SCHEMA}.periods "
                "WHERE period_time = :t)"), {"pid": pid, "t": ptime})
        keep = ", ".join(str(pid) for _, _, pid in canonical)
        c.execute(text(f"DELETE FROM {SCHEMA}.periods WHERE period_id NOT IN ({keep})"))
        for no, _, pid in canonical:
            c.execute(text(f"UPDATE {SCHEMA}.periods SET period_no = :n WHERE period_id = :pid"),
                      {"n": no, "pid": pid})
    log(f"normalized periods to {len(PERIOD_TIMES)} (8 periods + 1 break)")


def backdate(engine) -> None:
    """Spread audit timestamps; the user approved post-create UPDATE."""
    offsets = {
        "leave_requests": 5, "broadcasts": 4, "media": 3,
        "barter_listings": 6, "support_tickets": 7, "support_ticket_messages": 6,
    }
    for table, days in offsets.items():
        try:
            with engine.begin() as c:
                c.execute(text(
                    f"UPDATE {SCHEMA}.{table} SET created_at = created_at - "
                    "make_interval(days => :days) WHERE created_at IS NOT NULL"),
                    {"days": days})
        except Exception as exc:  # noqa: BLE001
            log(f"backdate {table} skipped: {exc}")


def create_notifications(engine) -> None:
    api = Api()
    try:
        api.set_token(api.login_token("meera.nair", MASTER_PW))
        with engine.connect() as c:
            school_ids = [r[0] for r in c.execute(
                text(f"SELECT school_id FROM {SCHEMA}.schools ORDER BY school_id"))]
        for sid in school_ids:
            for ntype, msg in [("General", "Welcome to the Schoolers portal."),
                               ("Dues", "Monthly fee reminders go out on the 1st."),
                               ("Activation", "Your website is now live.")]:
                api.post(f"/notifications/school/{sid}", json={"type": ntype, "message": msg})
        log("created notifications (mock SMTP; no real email)")
    finally:
        api.close()


def run(dry_run: bool = False) -> None:
    engine = db.make_engine()
    if dry_run:
        log("dry run: would create " + ", ".join(s.name for s in SCHOOLS))
        return
    started = time.time()
    with engine.begin() as c:
        if not db.user_table_has_master(c):
            sys.exit("No master user found — reset/seed expect an existing master.")
    with engine.begin() as c:
        for cfg in SCHOOLS:
            db.set_user_password(c, cfg.master_username, MASTER_PW)
    for cfg in SCHOOLS:
        SchoolSeed(cfg, engine).run()
    normalize_periods(engine)
    create_notifications(engine)
    backdate(engine)
    log(f"done in {time.time() - started:.0f}s")


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
