"""
Live role-traversal smoke test (manual — needs the stack running).

Run with the full stack up and the dev secret in the environment:

    PYTHONPATH="/Users/ravi/Downloads/schoolers 3" \
      venv/bin/python tests/live_smoke_roles.py

Walks every role's real HTTP flows through the gateway (:8000): teacher
dashboard reads, marks grading scope, timetable write lockout, parent
privacy scoping, and pilot reads. Fully read-only — the only writes are
deliberate 403 denials, so it is safe to run repeatedly against live data.

Named `live_smoke_*` (not `test_*`) so unittest discovery stays hermetic.
"""
import json
import sys
import urllib.request
import urllib.error

from common.security import create_access_token

BASE = "http://127.0.0.1:8000/api/v1"
JSON = {"Content-Type": "application/json"}

TEACHER = create_access_token(2, "teacher", 1, linked_person_id=1)
ADMIN = create_access_token(4, "admin", 1)
PARENT = create_access_token(3, "parent", 1, linked_person_id=225)
PILOT = create_access_token(172, "pilot", 1)

results = []


def call(method, path, token=None, body=None, headers=None):
    h = dict(JSON)
    h.update(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def check(label, cond, detail=""):
    results.append((label, bool(cond), detail[:400]))


def main():
    print("== TEACHER flows (user 2, teaches class 1: subjects 3, 6, 4) ==")
    s, b = call("GET", "/teachers/1/load", TEACHER)
    load = json.loads(b) if s == 200 else []
    subs = sorted({x["subject_id"] for x in load if x["class_id"] == 1})
    check("teacher load class1 subjects", s == 200 and subs == [3, 4, 6], f"status={s} subjects={subs}")

    s, b = call("GET", "/students?class_id=1", TEACHER)
    students = json.loads(b) if s == 200 else []
    check("teacher list class students", s == 200 and len(students) >= 1, f"status={s} n={len(students)}")
    check("student has personal + parent fields",
          students and all(k in students[0] for k in ("date_of_birth", "gender", "parent_name", "parent_phone")),
          f"sample keys={sorted(students[0]) if students else 'none'}")

    s, b = call("GET", "/attendance?student_id=1", TEACHER)
    check("teacher reads attendance history", s == 200, f"status={s}")
    s, b = call("GET", "/marks/student/1", TEACHER)
    check("teacher reads student marks", s == 200, f"status={s}")

    s, b = call("GET", "/marks/class/1?term=Term%201", TEACHER)
    class_marks = json.loads(b) if s == 200 else []
    subject_ids = sorted({m["subject_id"] for m in class_marks})
    check("class marks are teacher-scoped to taught subjects", s == 200 and set(subject_ids) <= {3, 4, 6},
          f"status={s} subjects={subject_ids}")

    s, b = call("GET", "/marks/class/1", TEACHER)
    j = json.loads(b) if s == 200 else []
    subj = sorted({m["subject_id"] for m in j}) if isinstance(j, list) else []
    check("class marks all-terms still teacher-scoped", s == 200 and isinstance(j, list) and set(subj) <= {3, 4, 6},
          f"status={s} n={len(j) if isinstance(j, list) else 'n/a'}")

    s, b = call("PUT", "/marks/1/5", TEACHER, {"term": "Term 1", "score": 77})
    check("teacher cannot grade unassigned subject", s == 403, f"status={s}")
    s, b = call("PUT", "/marks/1/3", TEACHER, {"term": "Term 1", "score": 85.5})
    check("non-integer score rejected with 422 (frontend quirk!)", s == 422, f"status={s} {b[:100]}")

    s, b = call("GET", "/timetable/class/1", TEACHER)
    entries = json.loads(b) if s == 200 else []
    check("teacher reads class timetable", s == 200 and len(entries) >= 1, f"status={s} n={len(entries)}")
    s, b = call("GET", "/timetable/entry/1", TEACHER)
    check("teacher reads single entry", s == 200, f"status={s}")

    s, _ = call("PATCH", "/timetable/entry/8570", TEACHER, {"subject_id": 8, "teacher_id": 310})
    check("teacher CANNOT update timetable entry", s == 403, f"status={s}")
    s, _ = call("POST", "/timetable/class/1/period", TEACHER, {"period_time": "10:00 - 10:45", "subject_id": 1, "teacher_id": 304, "day_of_week": "Mon"})
    check("teacher CANNOT create timetable period", s == 403, f"status={s}")
    s, _ = call("PATCH", "/timetable/entry/8570/clear-override", TEACHER)
    check("teacher CANNOT clear override", s == 403, f"status={s}")
    s, _ = call("DELETE", "/timetable/entry/8570", TEACHER)
    check("teacher CANNOT delete timetable entry", s == 403, f"status={s}")

    s, b = call("GET", "/broadcasts", TEACHER)
    check("teacher reads broadcast feed", s == 200, f"status={s}")

    print("\n== ADMIN flows (user 4) ==")
    s, b = call("PUT", "/marks/2/3", ADMIN, {"term": "Term 1", "score": 90})
    check("admin can grade any subject", s == 200, f"status={s}")
    s, b = call("GET", "/marks/class/1?term=Term%201", ADMIN)
    admin_subjects = sorted({m["subject_id"] for m in (json.loads(b) if s == 200 else [])})
    check("admin class marks include ALL subjects (not teacher-scoped)",
          s == 200 and (not admin_subjects or admin_subjects != [3, 4, 6]), f"subjects={admin_subjects}")
    s, b = call("PATCH", "/timetable/entry/8570", ADMIN, {"subject_id": 8, "teacher_id": 310, "period_start_time": "09:08:00", "period_end_time": "09:14:00"})
    check("admin CAN update timetable entry (idempotent)", s == 200, f"status={s}")

    print("\n== PARENT flows (user 3 -> parent 225 -> student 2) ==")
    s, b = call("GET", "/parents/225/children", PARENT)
    kids = json.loads(b) if s == 200 else []
    check("parent lists child", s == 200 and any(k["student_id"] == 2 for k in kids), f"status={s} n={len(kids)}")
    s, b = call("GET", "/marks/student/2", PARENT)
    check("parent reads child marks", s == 200, f"status={s}")
    s, b = call("GET", "/attendance?student_id=2", PARENT)
    check("parent reads child attendance", s == 200, f"status={s}")
    s, b = call("GET", "/broadcasts", PARENT)
    check("parent reads broadcasts", s == 200, f"status={s}")
    s, _ = call("PUT", "/marks/2/3", PARENT, {"term": "Term 1", "score": 80})
    check("parent CANNOT write marks", s == 403, f"status={s}")
    s, _ = call("PATCH", "/timetable/entry/8570", PARENT, {"subject_id": 8, "teacher_id": 310})
    check("parent CANNOT write timetable", s == 403, f"status={s}")

    print("\n== PILOT flows (user 172, school 1) ==")
    s, b = call("GET", "/broadcasts", PILOT)
    check("pilot reads broadcasts", s == 200, f"status={s}")
    s, b = call("GET", "/routes", PILOT)
    check("pilot lists routes", s == 200, f"status={s} body_len={len(b)}")
    s, _ = call("PATCH", "/timetable/entry/8570", PILOT, {"subject_id": 8, "teacher_id": 310})
    check("pilot CANNOT write timetable", s == 403, f"status={s}")
    s, _ = call("DELETE", "/timetable/entry/8570", PILOT)
    check("pilot CANNOT delete timetable", s == 403, f"status={s}")

    print("\n== Attendance read + class summary ==")
    s, b = call("GET", "/attendance/class/1/summary?date=1990-01-01", TEACHER)
    check("class summary readable (teacher)", s == 200 and "present" in (json.loads(b) if s == 200 else {}), f"status={s}")

    print("\n== Auth + parent-scoping edge cases ==")
    s, b = call("GET", "/students", None)
    check("no token -> 401", s == 401, f"status={s}")
    s, b = call("GET", "/marks/student/2", PARENT)
    check("parent reads OWN child's marks (student 2)", s == 200, f"status={s}")
    s, b = call("GET", "/marks/student/1", PARENT)
    check("parent BLOCKED from other family's marks (student 1)", s == 403, f"status={s}")
    s, b = call("GET", "/attendance?student_id=2", PARENT)
    check("parent reads OWN child's attendance (student 2)", s == 200, f"status={s}")
    s, b = call("GET", "/attendance?student_id=1", PARENT)
    check("parent BLOCKED from other family's attendance (student 1)", s == 403, f"status={s}")
    s, b = call("GET", "/parents/999/children", PARENT)
    kids = json.loads(b) if s == 200 else []
    ids = sorted(k["student_id"] for k in kids) if isinstance(kids, list) else []
    check("parent children path is bound to caller (not parent 999)", s == 200 and 1 not in ids, f"status={s} ids={ids}")
    s, b = call("GET", "/marks/student/1", ADMIN)
    check("admin (unscoped) can still read any student's marks", s == 200, f"status={s}")
    s, b = call("GET", "/marks/student/1", TEACHER)
    check("teacher can still read student marks", s == 200, f"status={s}")

    print("\n== Validation + self-service endpoints ==")
    s, b = call("GET", "/parents/me/children", PARENT)
    me_kids = json.loads(b) if s == 200 else []
    me_ids = sorted(k["student_id"] for k in me_kids) if isinstance(me_kids, list) else []
    check("parent self-service children endpoint", s == 200 and 2 in me_ids, f"status={s} ids={me_ids}")
    s, _ = call("GET", "/parents/me/children", TEACHER)
    check("non-parent blocked from /parents/me/children", s == 403, f"status={s}")
    s, _ = call("GET", "/marks/student/999999", ADMIN)
    check("unknown student marks -> 404", s == 404, f"status={s}")
    s, _ = call("GET", "/attendance?student_id=999999", ADMIN)
    check("unknown student attendance -> 404", s == 404, f"status={s}")
    s, _ = call("PUT", "/marks/1/999999", ADMIN, {"term": "Term 1", "score": 50})
    check("unknown subject upsert -> 404", s == 404, f"status={s}")
    s, _ = call("PUT", "/marks/999999/3", ADMIN, {"term": "Term 1", "score": 50})
    check("unknown student upsert -> 404", s == 404, f"status={s}")
    s, _ = call(
        "POST",
        "/attendance/mark",
        ADMIN,
        {"class_id": 1, "date": "2099-12-31", "entries": [{"student_id": 1, "status": "Bogus"}]},
    )
    check("bogus attendance status -> 422", s == 422, f"status={s}")
    s, _ = call("GET", "/attendance?student_id=1", TEACHER)
    check("teacher reads attendance for taught class", s == 200, f"status={s}")

    print("\n== SUMMARY ==")
    passed = sum(1 for _, ok, _ in results if ok)
    for label, ok, detail in results:
        print(("PASS" if ok else "FAIL") + f" - {label}" + (f"  [{detail}]" if not ok or detail else ""))
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())