# Schoolers — Microservices Architecture

This is the monolith backend (`schoolers_backend.zip`) rebuilt as 15
independent FastAPI microservices — one per module from `FASTAPI_MODULES.md`
— plus an API gateway and a single shared config package. **The frontend
(`schoolers_frontend.zip`) needs zero changes** — it still talks to one URL
(`http://localhost:8000/api/v1`), which is now the gateway instead of the
monolith.

## The "common config in a local folder" requirement

`common/` is a plain local Python package — no pip publishing, no remote
config service. Every one of the 15 services and the gateway does
`from common.config import settings`. That works regardless of which
service's directory the process is run from, because `common/config.py`
resolves its `.env` file by its own file location, not the caller's cwd:

```python
COMMON_DIR = Path(__file__).resolve().parent
ENV_FILE = COMMON_DIR / ".env"
```

So there is exactly **one** `.env` file, living at `common/.env`, and every
service — no matter where it's launched from — reads the same
`DATABASE_URL`, `JWT_SECRET_KEY`, CORS settings, and service registry from
it. Change a value once, every service picks it up.

`common/` also holds the shared SQLAlchemy models, JWT/security helpers,
auth dependencies, RBAC permission table, and exception types — anything
that would otherwise be copy-pasted across 15 services.

## Layout

```
schoolers/
├── common/                    # the ONE shared package + ONE shared .env
│   ├── .env                    # <- single config source for every service
│   ├── config.py
│   ├── database.py
│   ├── security.py
│   ├── dependencies.py
│   ├── permissions.py
│   ├── exceptions.py
│   ├── enums.py
│   └── models.py                # SQLAlchemy models (shared schema, see note below)
├── gateway/                    # single public entry point, port 8000
│   └── main.py                  # proxies /api/v1/* to the right service by path
├── services/
│   ├── auth_service/            (port 8001)
│   ├── schools_service/         (port 8002)
│   ├── academics_service/       (port 8003)
│   ├── people_service/          (port 8004)
│   ├── attendance_service/      (port 8005)
│   ├── marks_service/           (port 8006)
│   ├── timetable_service/       (port 8007)
│   ├── transport_service/       (port 8008)
│   ├── leave_service/           (port 8009)
│   ├── communication_service/   (port 8010)
│   ├── barter_service/          (port 8011)
│   ├── activities_service/      (port 8012)
│   ├── website_service/         (port 8013)
│   ├── notifications_service/   (port 8014)
│   └── reports_service/         (port 8015)
├── docker-compose.yml          # full stack incl. Postgres, one command
├── run_all.sh                   # local dev: launches everything without Docker
└── FASTAPI_MODULES.md            # original module-by-module design doc
```

Each `services/<name>_service/` is a **complete, independent FastAPI app**:
its own `main.py`, `router.py`, `schemas.py`, and `service.py`/`repository.py`,
its own `requirements.txt`, its own `Dockerfile`. It imports nothing from
any other service — only from `common`.

### Why one shared database, not one-per-service

"True" microservices often get a database each. That's deliberately **not**
done here: `attendance`, `marks`, and `timetable` all have foreign keys into
`students`/`classes`/`teachers` (owned by `people`/`academics`), and
`reports` reads across nearly everything. Splitting the DB would mean
either duplicating that data everywhere or adding synchronous cross-service
calls for every query — a real cost for no benefit at this stage. Each
service still owns its own code, its own process, its own deployment
lifecycle, and could migrate to its own DB later if it ever needs to; they
just currently share one Postgres instance and one schema, which is a very
common and pragmatic middle ground.

## Run it locally (no Docker)

```bash
cd schoolers
./run_all.sh
```

This starts all 15 services + the gateway, each reading `common/.env`.
Check what's up:
```bash
curl http://localhost:8000/health/services
```

## Run it with Docker Compose

```bash
cd schoolers
COMPOSE_PARALLEL_LIMIT=1 docker compose up --build
```

Spins up Postgres (seeded from `../schoolers_schema_and_data.sql`), all 15
services, and the gateway on port 8000. *(Note: Docker wasn't available in
the sandbox this was built in, so the compose file and Dockerfiles are
carefully constructed and YAML-validated, but not build-tested end-to-end —
the `run_all.sh` path below is the one that's actually been proven working.)*

`COMPOSE_PARALLEL_LIMIT=1` builds images one at a time, avoiding Docker
Desktop running out of memory while 15 `pip install` processes run
concurrently. The root `.dockerignore` also excludes local virtual
environments and caches from every build context.

## What's been verified end-to-end

Using `run_all.sh`'s exact launch pattern, tested live:
- Login through the gateway → routed to `auth_service`, returns a JWT
  (including `linked_person_id`)
- That JWT, issued by `auth_service`, accepted by `people_service` and
  `attendance_service` independently — proving the shared-secret JWT
  verification works with **no inter-service call** needed to validate a
  token
- A parent's own child data correctly fetched cross-service
  (`people_service`)
- RBAC still enforced correctly per-service (a parent's token correctly
  gets `403` from an admin-only endpoint on `schools_service`)
- Gateway returns a clean `503` when a request targets a service that
  isn't running, and a clean `404` for a path with no registered service —
  neither hangs nor crashes the gateway

The 15-service split was also import-tested individually (every
`main.py` boots without error), and generated directly from the monolith's
already-tested module code — the business logic didn't change, only how
it's deployed and run.

## Gateway routing table

The gateway forwards by the first path segment after `/api/v1/`. Most
services own a segment matching their name; a few own multiple, since their
original router had no single path prefix:

| Path segment(s) | Service |
|---|---|
| `auth` | auth_service |
| `schools` | schools_service |
| `classes`, `subjects`, `periods`, `holidays` | academics_service |
| `teachers`, `staff`, `parents`, `students` | people_service |
| `attendance` | attendance_service |
| `marks` | marks_service |
| `timetable` | timetable_service |
| `routes` | transport_service |
| `leave` | leave_service |
| `broadcasts`, `media` | communication_service |
| `barter` | barter_service |
| `activities` | activities_service |
| `website`, `public` (public site pages) | website_service |
| `notifications` | notifications_service |
| `reports` | reports_service |

## Regenerating a service from the monolith

`generate_services.py` is what produced `services/` from the monolith's
`app/modules/`. If you update the monolith first and want to re-split it,
re-run that script — it rewrites `app.core.*`/`app.shared.*` imports to
`common.*` and flattens each module's internal imports automatically.
(Edit the `MONOLITH_MODULES` path at the top of the script to point at your
own monolith checkout first.)
