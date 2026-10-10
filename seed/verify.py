"""Phase 3 verification: assert the reset+seed produced the expected state.

Checks the database shape, the normalised periods, and that a login for every
role can actually authenticate and read its own data through the gateway.
"""
from __future__ import annotations

from sqlalchemy import text

from seed import db
from seed.client import Api
from seed.fixtures import (
    ADMIN_PW, MASTER_PW, PARENT_PW, PILOT_PW, STAFF_PW, TEACHER_PW,
)

SCHEMA = "schoolers"

EXPECTED_COUNTS = {
    "schools": 2, "subjects": 16, "classes": 12, "staff": 30, "parents": 24,
    "students": 36, "parent_student": 36, "teacher_class_subjects": 96,
    "periods": 9, "timetable_entries": 540, "attendance": 36,
    "staff_attendance": 60, "marks": 144, "staff_salaries": 72,
    "student_fees": 108, "holidays": 10, "leave_requests": 12,
    "broadcasts": 8, "media": 12, "vehicles": 6, "pilots": 6, "routes": 6,
    "route_stops": 24, "route_students": 24, "activities": 8,
    "barter_listings": 6, "website_builder_sites": 0, "support_tickets": 4,
    "support_ticket_messages": 6, "school_notifications": 6,
}

EXPECTED_ROLES = {
    "master": 2, "admin": 2, "teacher": 16, "staff": 8, "parent": 24, "pilot": 6,
}

ROLE_LOGINS = [
    ("meera.nair", MASTER_PW, "master"),
    ("blue.horizon", ADMIN_PW, "admin"),
    ("bh.teacher1", TEACHER_PW, "teacher"),
    ("bh.staff1", STAFF_PW, "staff"),
    ("bh.parent1", PARENT_PW, "parent"),
    ("bh.pilot1", PILOT_PW, "pilot"),
]


def run() -> None:
    engine = db.make_engine()
    failures: list[str] = []
    with engine.connect() as c:
        # The app is live, so users may add rows while we verify: assert the
        # seeded minimum, not an exact total.
        for table, expected in EXPECTED_COUNTS.items():
            got = c.execute(text(f"SELECT count(*) FROM {SCHEMA}.{table}")).scalar_one()
            mark = "ok" if got >= expected else "FAIL"
            if mark == "FAIL":
                failures.append(f"{table}: expected >= {expected}, got {got}")
            print(f"  [{mark:^4}] {table:<26} {got} (>={expected})")
        print("-- roles --")
        for role, expected in EXPECTED_ROLES.items():
            got = c.execute(text(
                f"SELECT count(*) FROM {SCHEMA}.users WHERE role = :r"), {"r": role}).scalar_one()
            mark = "ok" if got >= expected else "FAIL"
            if mark == "FAIL":
                failures.append(f"role {role}: expected >= {expected}, got {got}")
            print(f"  [{mark:^4}] {role:<10} {got} (>={expected})")
        seq_ok = c.execute(text(
            f"SELECT bool_and(period_no BETWEEN 1 AND 9) FROM {SCHEMA}.periods"
        )).scalar_one()
        if not seq_ok:
            failures.append("period_no is not 1..9")
        print(f"  [{'ok' if seq_ok else 'FAIL':^4}] periods normalised to 1..9")

    print("-- live logins / API --")
    api = Api()
    try:
        for username, password, role in ROLE_LOGINS:
            try:
                token = api.login_token(username, password)
                if api_login_role(api, token) != role:
                    failures.append(f"{username}: role mismatch")
                    print(f"  [FAIL] {username}: role mismatch")
                else:
                    print(f"  [ ok ] {username:<14} role={role}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{username}: {exc}")
                print(f"  [FAIL] {username}: {exc}")
        api.set_token(api.login_token("blue.horizon", ADMIN_PW))
        for path, key in [("/classes", "classes"), ("/subjects", "subjects"),
                          ("/students", "students"), ("/staff", "staff")]:
            n = len(api.get(path))
            if n == 0:
                failures.append(f"admin GET {path} empty")
            print(f"  [{'ok' if n else 'FAIL':^4}] admin GET {path} -> {n}")
        api.set_token(api.login_token("meera.nair", MASTER_PW))
        tickets = len(api.get("/support/tickets"))
        print(f"  [{'ok' if tickets else 'FAIL':^4}] master GET /support/tickets -> {tickets}")
        if not tickets:
            failures.append("support tickets empty")
    finally:
        api.close()

    print()
    if failures:
        print("VERIFY FAILED:")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)
    print("VERIFY OK")


def api_login_role(api: Api, token: str) -> str:
    api.set_token(token)
    return api.get("/auth/me")["role"]
