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

## What it creates (per school)

8 subjects, 6 classes (Grade I–VI), 8 teachers, 4 non-teaching staff,
12 parents, 18 students, 3 pilots, 3 vehicles, 3 routes (2 stops / 4 students
each), 270 timetable entries over a normalised set of 9 periods, attendance,
72 marks, staff attendance, salaries (36) + fees (54) for 3 months, 5 holidays,
6 leave requests, 4 broadcasts, 6 gallery media, website settings + 5 pages +
4 testimonials + go-live, 4 activities, 2 support tickets, 3 barter listings,
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

## Restore

See `backups/<timestamp>/RESTORE.md`. Use the Postgres 17 `pg_dump`/`psql`
(`/Applications/Postgres.app/Contents/Versions/17/bin`) against server 17.
