-- Academic calendar: years, periods, dated non-closure events (Phase 1).
--
-- Previously terms were free-text on marks rows ("Term 1") with no backing
-- entity, and `Period` is the daily bell schedule, not an academic term.
-- The holidays table already stores dated closures and is left untouched;
-- `academic_events` covers the rest (exam windows, revision weeks, result
-- publication dates, other dated periods).
--
-- Every statement is idempotent, so this file is safe to run more than once.
-- Additive only: no existing table, column, or constraint is altered here.

-- 1. Academic years, one row per school year (e.g. "2026-27").
CREATE TABLE IF NOT EXISTS schoolers.academic_years (
    year_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    name VARCHAR(20) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_founding BOOLEAN NOT NULL DEFAULT FALSE,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT academic_years_school_id_name_key UNIQUE (school_id, name),
    CONSTRAINT academic_years_date_check CHECK (end_date > start_date)
);
CREATE INDEX IF NOT EXISTS idx_academic_years_school
    ON schoolers.academic_years (school_id);

-- 2. Academic periods (terms/semesters) inside a year.
CREATE TABLE IF NOT EXISTS schoolers.academic_periods (
    period_id SERIAL PRIMARY KEY,
    year_id INTEGER NOT NULL REFERENCES schoolers.academic_years(year_id) ON DELETE CASCADE,
    name VARCHAR(40) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT academic_periods_year_id_name_key UNIQUE (year_id, name),
    CONSTRAINT academic_periods_date_check CHECK (end_date > start_date)
);
CREATE INDEX IF NOT EXISTS idx_academic_periods_year
    ON schoolers.academic_periods (year_id);

-- 3. Dated non-closure events (exam windows, revision weeks, result dates).
CREATE TABLE IF NOT EXISTS schoolers.academic_events (
    event_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    year_id INTEGER REFERENCES schoolers.academic_years(year_id) ON DELETE SET NULL,
    event_type VARCHAR(20) NOT NULL,
    title VARCHAR(150) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT academic_events_date_check CHECK (end_date >= start_date),
    CONSTRAINT academic_events_type_check CHECK (
        event_type IN ('exam_period', 'revision', 'result_date', 'other')
    )
);
CREATE INDEX IF NOT EXISTS idx_academic_events_school
    ON schoolers.academic_events (school_id);
CREATE INDEX IF NOT EXISTS idx_academic_events_year
    ON schoolers.academic_events (year_id);

-- Verification (run after applying; all must hold):
--   SELECT COUNT(*) FROM schoolers.academic_years;
--   SELECT COUNT(*) FROM schoolers.academic_periods;
--   SELECT COUNT(*) FROM schoolers.academic_events;
--   SELECT COUNT(*) FROM schoolers.holidays;  -- unchanged by this file
