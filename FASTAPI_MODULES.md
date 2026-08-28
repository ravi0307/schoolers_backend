# Schoolers — FastAPI Backend: Module Breakdown

This maps the existing `schoolers` PostgreSQL schema and the 5 front-end apps
(Parent, Teacher, School Admin, Pilot, Master Admin) onto a modular FastAPI
backend. Recommended architecture: a **modular monolith** — one deployable
FastAPI app, internally split into self-contained feature modules. This
keeps things simple to run and test now, while each module is cohesive
enough to be pulled out into its own microservice later if you ever need to
(e.g. `attendance` or `notifications` under heavy load).

---

## 1. Suggested project layout

Each module is a **vertical slice** — its own router, schemas, service
(business logic), and repository (DB access) — rather than one giant
`models.py`/`routers.py` per layer. This scales much better once multiple
people are working on it.

```
app/
├── main.py                     # app factory, router registration, middleware, CORS
├── core/                       # cross-cutting concerns, no business logic
│   ├── config.py               # env vars, settings (pydantic-settings)
│   ├── database.py             # SQLAlchemy engine/session, get_db dependency
│   ├── security.py             # JWT issue/verify, password hashing (passlib)
│   ├── dependencies.py         # current_user, current_school, role guards
│   ├── permissions.py          # RBAC rules per role (parent/teacher/admin/pilot/master)
│   ├── exceptions.py           # domain exceptions -> HTTP error mapping
│   └── pagination.py           # shared limit/offset or cursor helpers
│
├── modules/
│   ├── auth/                   # login, token refresh, password reset
│   ├── schools/                # Master Admin: schools CRUD, feature flags
│   ├── academics/              # classes, subjects, periods, holidays
│   ├── people/                 # teachers, staff, parents, students, links
│   ├── attendance/
│   ├── marks/
│   ├── timetable/
│   ├── transport/              # routes, stops, route_students, live pickup/drop
│   ├── leave/                  # leave_requests (teacher/student/staff/pilot)
│   ├── communication/          # broadcasts, media/insta class
│   ├── barter/                 # barter_listings
│   ├── activities/             # daily activity content feed
│   ├── website/                # school website builder (settings/pages/testimonials)
│   ├── notifications/          # school_notifications + generic dispatch
│   └── reports/                # aggregated stats, dashboards
│
├── shared/
│   ├── models/                 # SQLAlchemy ORM models (mirrors schema.sql)
│   ├── enums.py                # Role, LeaveStatus, NotificationType, etc.
│   └── utils/                  # file/image handling, slugify, date helpers
│
├── alembic/                    # migrations (generate from shared/models)
└── tests/
    └── modules/                # one test folder per module, mirrors app/modules
```

Each `modules/<name>/` folder contains:
```
modules/attendance/
├── router.py        # FastAPI APIRouter, thin — calls service, returns schema
├── schemas.py        # Pydantic request/response models
├── service.py         # business rules (e.g. "can't mark attendance twice")
├── repository.py      # SQLAlchemy queries, no business logic
└── __init__.py
```

---

## 2. Cross-cutting foundation (build this first)

### `core` — not a module, but everything else depends on it
- **Auth dependency**: `get_current_user()` decodes JWT → `(user_id, role, school_id)`
- **Tenancy guard**: every query in every module must be scoped by `school_id`
  from the token — never trust a `school_id` passed in the request body/query
  for anything except Master Admin endpoints
- **Role guard**: `require_role("teacher", "admin")` dependency factory, used
  per-endpoint
- **Response envelope**: consistent `{data, meta}` or plain resource — pick
  one convention early

### `modules/auth`
- `POST /auth/login` — username/password → JWT (role + school_id in claims)
- `POST /auth/refresh`
- `POST /auth/logout` (token blacklist, optional)
- `POST /auth/password-reset/request`, `POST /auth/password-reset/confirm`
- Maps to: `users` table

---

## 3. Feature modules (mapped to your schema + screens)

### `modules/schools` — Master Admin only
- School CRUD: `GET/POST /schools`, `GET/PATCH/DELETE /schools/{id}`
- Feature toggles: `PATCH /schools/{id}/features` (route/website/library/fees/salary)
- Activate/deactivate: `PATCH /schools/{id}/status`
- Reports: `GET /schools/{id}/stats` (teacher/staff/student/parent counts —
  live-computed via joins, not stored counters)
- Tables: `schools`

### `modules/academics` — School Admin
- Classes: CRUD, assign class teacher
- Subjects: mostly a fixed lookup, rarely mutated via API
- Periods: CRUD period time slots
- Holidays: get/set weekly Mon–Sun working/holiday pattern per school
- Tables: `classes`, `subjects`, `periods`, `holidays`

### `modules/people` — School Admin (+ self-service for own profile)
- Teachers: CRUD, assign to class+subject (`teacher_class_subjects`)
- Staff: CRUD
- Parents: CRUD, link to students (`parent_student`, supports multiple children)
- Students: CRUD, move between classes, search by name/admission no.
- Tables: `teachers`, `staff`, `parents`, `students`, `parent_student`,
  `teacher_class_subjects`

### `modules/attendance` — Teacher writes, Parent/Admin read
- `POST /attendance/mark` (bulk, one class/date at a time)
- `GET /attendance?student_id=&from=&to=`
- `GET /attendance/class/{class_id}/summary?date=`
- Tables: `attendance`

### `modules/marks` — Teacher writes (own subjects only), Admin writes (any), Parent reads
- `GET /marks/student/{id}`
- `PUT /marks/{student_id}/{subject_id}` — service layer must check the
  teacher actually teaches that subject in that class before allowing write
- Tables: `marks`, cross-checked against `teacher_class_subjects`

### `modules/timetable` — Admin/Teacher write, everyone reads
- `GET /timetable/class/{class_id}`
- `PUT /timetable/entry/{id}` — subject/teacher/holiday-override
- `PATCH /periods/{id}` — edit period time (affects all classes)
- `PATCH /holidays/{school_id}/{day}` — toggle working/holiday
- Tables: `timetable_entries`, `periods`, `holidays`

### `modules/transport` — Admin manages, Pilot operates, Parent tracks
- Routes: CRUD, assign/remove students
- Stops: CRUD pickup/drop points, ordering
- Pilot live status: `PATCH /routes/{id}/students/{student_id}/status`
  (pending/picked/dropped) — this is your best candidate for a **WebSocket**
  or Server-Sent Events channel so Parent's app updates live instead of polling
- Tables: `routes`, `route_stops`, `route_students`

### `modules/leave` — shared across roles, Admin approves
- `POST /leave` (role-agnostic: Teacher/Student/Staff/Pilot as `requester_type`)
- `GET /leave?school_id=&status=`
- `PATCH /leave/{id}/approve`, `PATCH /leave/{id}/reject`
- Tables: `leave_requests`

### `modules/communication`
- Broadcasts: `POST /broadcasts` (scope: school/class/pilot), `GET /broadcasts?scope=&class_id=`
- Media (Insta Class): `POST /media` (upload + metadata), `GET /media?class_id=`
- Tables: `broadcasts`, `media`
- This module is the natural home for file upload handling (or delegate to
  a shared `shared/utils/storage.py` wrapping S3/GCS/local disk)

### `modules/barter`
- Simple CRUD marketplace scoped to `school_id`
- `POST /barter`, `GET /barter?school_id=`, `PATCH/DELETE /barter/{id}`
- Tables: `barter_listings`

### `modules/activities`
- Content-team-authored feed, read-heavy
- `GET /activities?school_id=`, `POST /activities` (admin/content role)
- Tables: `activities`

### `modules/website` — School Admin builds, public/anonymous reads
- Settings: `GET/PUT /website/{school_id}/settings` (font/size/color/header/footer)
- Pages: `GET/PUT /website/{school_id}/pages/{slug}` (home/about/academics/admissions/contact)
- Testimonials: CRUD
- Consider a **separate public router** with no auth (`/public/sites/{school_id}`)
  since this content is meant to be a real public-facing page, distinct from
  the authenticated app API
- Tables: `website_settings`, `website_pages`, `website_testimonials`

### `modules/notifications`
- `school_notifications`: Master Admin → School (Dues/Activation/General)
- This module is also the right place for a **generic notification dispatcher**
  service (in-app + email/SMS/push abstraction) that other modules call into
  — e.g. `attendance` module calls `notifications.send(parent_id, "absent_today")`
  rather than each module reinventing delivery logic
- Tables: `school_notifications` (+ future generic `notifications` table if
  you want in-app notifications for all roles, not just school-level)

### `modules/reports` — mostly Master Admin & School Admin dashboards
- Aggregation-only module, no writes
- `GET /reports/school/{id}/overview` (headcounts, attendance %, pending leave, etc.)
- `GET /reports/class/{id}/attendance-trend`
- Keep this thin at first — it's just read queries across other modules'
  tables. Don't let it grow business logic; it's a consumer, not an owner,
  of the other modules' data.

---

## 4. Access control matrix (who calls what)

| Module | Parent | Teacher | School Admin | Pilot | Master Admin |
|---|---|---|---|---|---|
| schools | – | – | read own | – | full |
| academics | read | read | full | – | – |
| people | read own child | read own class | full | – | – |
| attendance | read own child | write own class | read all | – | – |
| marks | read own child | write own subjects | write all | – | – |
| timetable | read own child's class | read + write own periods | full | – | – |
| transport | read own child's route | – | full | write own route status | – |
| leave | write (for child) | write (self) | approve/reject | write (self) | – |
| communication | read | write own class | write school-wide | write route incidents | – |
| barter | full (own listings) | – | – | – | – |
| activities | read | – | write | – | – |
| website | – | – | full | – | – |
| notifications | – | – | read | – | write |
| reports | – | – | own school | – | all schools |

This table is worth encoding directly as your `permissions.py` — one
dict/lookup per (module, action) → allowed roles, checked in a single
`require_permission()` dependency rather than scattering `if role == ...`
checks through route handlers.

---

## 5. Suggested tech choices

- **ORM**: SQLAlchemy 2.0 (async) + Alembic for migrations — generate models
  from `schoolers_schema.sql` once, then let Alembic own schema changes going forward
- **Validation**: Pydantic v2, separate `*Create` / `*Update` / `*Read` schemas per resource
- **Auth**: JWT (short-lived access + refresh token), `python-jose` or `pyjwt`,
  `passlib[bcrypt]` for password hashing
- **Background jobs**: FastAPI `BackgroundTasks` for simple fire-and-forget
  (e.g. sending one notification); move to Celery/RQ + Redis once you have
  real scheduled jobs (nightly attendance digest, dues reminders)
- **Live transport tracking**: WebSocket endpoint under `modules/transport`,
  or Server-Sent Events if you want to keep it simpler than full-duplex WS
- **File storage**: abstract behind `shared/utils/storage.py` (local disk in
  dev, S3-compatible in prod) — used by `communication` (media) and `website`
  (banner/icon images)
- **Testing**: `pytest` + `httpx.AsyncClient` against a test DB (or
  transactional rollback per test) — one test module per feature module,
  mirroring the `app/modules/` structure

---

## 6. Suggested build order

1. `core` (config, db, security, dependencies) — nothing else works without this
2. `auth` + `people` (teachers/staff/parents/students) — you need users before anything else is meaningful
3. `schools` (Master Admin) — since everything else is scoped by `school_id`
4. `academics` (classes/subjects/periods/holidays) — needed before timetable/attendance/marks can exist
5. `attendance`, `marks`, `timetable` — the daily-use core loop
6. `transport`, `leave`, `communication` — next tier of features
7. `barter`, `activities`, `website`, `notifications` — lower-traffic, can trail
8. `reports` — last, since it just reads what everything else has already built
