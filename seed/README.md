# Schoolers dev seeder

Resets the local `schoolersdb` database and builds two complete schools by
driving the **real gateway HTTP API** as logged-in roles (no direct table
writes except where the app has no flow — see *Known gaps*).

## Safety

`reset` refuses to run unless the environment is explicitly opted in:

```sh
ALLOW_DB_RESET=true PYTHONPATH=. venv/bin/python -m seed.run <cmd>
```

It also refuses non-local / production hosts and takes a `pg_dump` backup into
`backups/<timestamp>/` before deleting anything (use `--no-backup` to skip).

## Commands

```sh
# wipe tenant data (keeps master rows), then seed + verify
ALLOW_DB_RESET=true PYTHONPATH=. venv/bin/python -m seed.run all --no-backup

PYTHONPATH=. venv/bin/python -m seed.run verify     # assert state, no writes
```

`reset | seed | all | verify`; `--dry-run` and `--no-backup` apply to the
mutating commands.

## Examination fixtures (`exam-fixtures`)

```sh
PYTHONPATH=. venv/bin/python -m seed.run exam-fixtures            # writes
PYTHONPATH=. venv/bin/python -m seed.run exam-fixtures --dry-run  # plan only
```

Runs **after** `seed` (needs Blue Horizon Academy and Grade I to exist).
Append-only and idempotent: re-running duplicates nothing and never deletes,
rewrites passwords, touches other schools, or flips
`schools.examinations_enabled` (both schools must still be `false`; the run
aborts if it finds the flag on). No `ALLOW_DB_RESET` needed because nothing is
destroyed.

It creates, for Blue Horizon Academy only (school 1):

- Academic year `2026-27` with periods `Term 1` / `Term 2`.
- Examination type `Half-Yearly` and grade scale `Alpha` v1 (A 80–100,
  B 60–79).
- Examination `Half-Yearly` (**draft**) with sessions Grade I × Mathematics
  and × Hindi, and examination `Term 2 Weekly` (**published**, Term 2) with
  sessions × English and × Science (all 2026-12-15..18, 09:00–12:00,
  Hall A, max 100).
- Session marks across every state: submitted (Mathematics, with the last
  student reported missing then entered as a late `exempt` draft), published
  (English, including one `correct_mark` correction 67→72 and one withheld
  mark), and submitted with `present`/`absent`/`missing` rows (Science).
- Class enrollment history (`class_enrollments`, status `enrolled`) and
  subject enrollment (`student_subjects`) for every active BH student.
- CSV fixture files in `.logs/exam_csv/`: `valid.csv`, `invalid.csv`,
  `missing-mark.csv` (omits one student so commit+submit reports them via
  `missing_student_ids`), and `duplicate.csv` (byte-identical to `valid.csv`,
  demonstrating the duplicate-receipt path). One **previewed** import batch
  is recorded for the Hindi session from `valid.csv`.

Every configured value is taken verbatim from approved test fixtures
(`tests/test_marks_import.py`, `tests/test_marks_results.py`,
`tests/test_examination_scheduling.py`); see the `seed/exams.py` module
docstring for the provenance table. Direct table writes are used only because
the corresponding routers are gated by `require_examinations_enabled`; mark
workflow moves go through `services/marks_service/repository.py` so product
validation runs unchanged. Mark rows and import batches are created through
the same service functions the API uses.

## What it creates (per school)

8 subjects, 6 classes (Grade I–VI), 8 teachers, 4 non-teaching staff,
12 parents, 18 students, 3 pilots, 3 vehicles, 3 routes (2 stops / 4 students
each), 270 timetable entries over a normalised set of 9 periods, attendance,
72 marks, staff attendance, salaries (36) + fees (54) for 3 months, 5 holidays,
6 leave requests, 4 broadcasts, 6 gallery media, 4 activities, 2 support tickets, 3 barter listings,
3 notifications. Totals are asserted by `verify`.

## Accounts

| Role    | Username          | Password       |
|---------|-------------------|----------------|
| master  | `meera.nair`,`ravi` | `Master@12345` |
| admin   | `blue.horizon` (BH), `green.valley` (GV) | `Admin@12345` |
| teacher | `<bh\|gv>.teacher1..8` | `Teacher@12345` |
| staff   | `<bh\|gv>.staff1..4`   | `Staff@12345`   |
| parent  | `<bh\|gv>.parent1..12` | `Parent@12345`  |
| pilot   | `<bh\|gv>.pilot1..3`   | `Pilot@12345`   |

Schools: id 1 = Blue Horizon Academy (Bengaluru), id 2 = Green Valley Public
School (Pune).

## Mock SMTP

The seeder sends enrollment/notification mail. To keep it off the wire, run a
local sink and point the services at it (env only, never edit `.env`):

```sh
venv/bin/python -m seed.mock_smtp            # listens on 127.0.0.1:2525, logs to .logs/mock_smtp.log
```

Start the backend with `SMTP_HOST=127.0.0.1 SMTP_PORT=2525 SMTP_USE_TLS=false
SMTP_USER= SMTP_PASSWORD= SMTP_FROM=seed@schoolers.local`, then restart **without**
those overrides when done.

## Known gaps (why some rows are inserted directly)

- Teachers, staff and parents have **no** in-app login-creation flow, so their
  `users` rows are inserted with the app's `hash_password`. Admins and pilots
  are created through the API.
- The `Role` enum in `common/enums.py` omits `staff`, yet the DB and login
  happily use `role='staff'`.
- `auth_service.service.user_email_address` returns `None` for masters, so
  forgot-password email is impossible for them; master passwords are set
  directly (password field only).
- Every `POST /timetable/class/{id}/period` allocates a new global `Period`;
  `seed.seed.normalize_periods` consolidates duplicates into 9 canonical rows.
- Gallery files land in `UPLOAD_LOCAL_PATH` (see `common/.env`).
- The CSV import summary counts a `warning` verdict and the admin UI renders
  it, but no rule in `services/marks_service/bulk.py` can currently produce
  one, so `exam-fixtures` cannot generate a warning CSV case. Adding that
  verdict needs a product rule decision first.

## Restore

See `backups/<timestamp>/RESTORE.md`. Use the Postgres 17 `pg_dump`/`psql`
(`/Applications/Postgres.app/Contents/Versions/17/bin`) against server 17.
