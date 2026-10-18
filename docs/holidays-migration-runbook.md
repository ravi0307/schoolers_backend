# Holidays date-migration runbook (operator)

Migration: `db-migration-holidays-dates.sql` — converts `schoolers.holidays`
from the weekly recurring template (`day_of_week`, `is_holiday`) to dated
rows (`occasion`, `holiday_date`).

**Status: NOT run in production. Destructive to weekly rows. Requires
authorized operator approval.**

## What it does

1. Drops the weekly constraints.
2. Adds nullable `occasion` / `holiday_date`.
3. **`DELETE`s every recurring weekly row** (`occasion IS NULL OR holiday_date IS NULL`).
4. Sets the new columns NOT NULL, drops `day_of_week` / `is_holiday`,
5. Adds `UNIQUE (school_id, holiday_date)`.

Weekly rows cannot be translated automatically — every school must
re-enter recurring closures (e.g. "closed every Saturday") as dated rows.

## Steps (in order)

1. **Preflight (read-only):**
   ```bash
   python scripts/holidays_preflight.py \
     --database-url postgresql://USER@HOST:5432/DB \
     --export-csv /tmp/holidays_before.csv
   ```
   Attach the printed report to the change ticket. The CSV preserves the
   weekly rows on disk before anything is deleted.
2. **Backup:**
   ```bash
   pg_dump -Fc -d DB -t schoolers.holidays -f holidays_before.dump
   pg_restore --list holidays_before.dump | head   # verify the dump
   ```
3. **Approval:** authorized operator signs the checklist printed by the
   preflight script (name + timestamp recorded in the change ticket).
4. **Run the migration** (idempotent, safe to re-run):
   ```bash
   psql -d DB -f db-migration-holidays-dates.sql
   ```
5. **Post-migration verification:**
   ```sql
   \d schoolers.holidays                      -- new columns present, old gone
   SELECT count(*) FROM schoolers.holidays
     WHERE occasion IS NULL OR holiday_date IS NULL;   -- must be 0
   SELECT school_id, count(*) FROM schoolers.holidays
     GROUP BY school_id;                      -- compare with preflight counts
   ```
   Then confirm session scheduling works across a dated holiday
   (`check_session_conflicts` in academics service).
6. **Schools re-enter recurring closures** as dated rows via the admin
   Academic Calendar.

## Rollback

Restore the pre-migration dump:

```bash
pg_restore --clean --if-exists -d DB holidays_before.dump
```

(Forward recovery only — there is no automatic downgrade script.)

## Confirmation checklist

- [ ] Fresh backup taken and restore verified
- [ ] Preflight report attached to the change ticket
- [ ] Per-school weekly row counts confirmed with each school
- [ ] Plan in place to re-enter recurring closures as dated rows
- [ ] Deployed application version supports dated holidays
- [ ] Authorized operator approval recorded (name + timestamp)
- [ ] Migration window scheduled with rollback ready
