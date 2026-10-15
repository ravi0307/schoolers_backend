-- Examination setup: per-school marking system, examination types,
-- versioned grade scales (Phase 2).
--
-- `schools.marking_system` defaults to 'marks', which is exactly the
-- current behavior (numeric entry 0-100, global grade display), so existing
-- schools see zero behavior change from this file.
--
-- Grade scales are versioned by copy-on-write (see plan): the levels of a
-- scale referenced by published marks become immutable, and further edits
-- require a new version row. Nothing references scales yet (marks gain
-- grade_level_id in Phase 4), so all Phase-2 scales are freely editable.
--
-- Every statement is idempotent, so this file is safe to run more than once.
-- Additive only: no existing table, column, or constraint is altered here.

-- 1. Per-school marking system on schools.
ALTER TABLE IF EXISTS schoolers.schools
    ADD COLUMN IF NOT EXISTS marking_system VARCHAR(10) NOT NULL DEFAULT 'marks';
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'schools_marking_system_check'
    ) THEN
        ALTER TABLE schoolers.schools
            ADD CONSTRAINT schools_marking_system_check
            CHECK (marking_system IN ('marks', 'grades'));
    END IF;
END $$;

-- 2. School-owned examination types.
CREATE TABLE IF NOT EXISTS schoolers.examination_types (
    type_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    name VARCHAR(60) NOT NULL,
    description VARCHAR(255),
    default_max_marks INTEGER NOT NULL DEFAULT 100,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT examination_types_school_id_name_key UNIQUE (school_id, name),
    CONSTRAINT examination_types_max_check CHECK (default_max_marks > 0)
);
CREATE INDEX IF NOT EXISTS idx_examination_types_school
    ON schoolers.examination_types (school_id);

-- 3. Versioned grade scales.
CREATE TABLE IF NOT EXISTS schoolers.grade_scales (
    scale_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    name VARCHAR(60) NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    supersedes_id INTEGER REFERENCES schoolers.grade_scales(scale_id) ON DELETE SET NULL,
    effective_from DATE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT grade_scales_school_id_name_version_key UNIQUE (school_id, name, version)
);
CREATE INDEX IF NOT EXISTS idx_grade_scales_school
    ON schoolers.grade_scales (school_id);

-- 4. Grade levels within a scale version.
CREATE TABLE IF NOT EXISTS schoolers.grade_levels (
    level_id SERIAL PRIMARY KEY,
    scale_id INTEGER NOT NULL REFERENCES schoolers.grade_scales(scale_id) ON DELETE CASCADE,
    label VARCHAR(10) NOT NULL,
    rank INTEGER NOT NULL DEFAULT 0,
    min_score INTEGER,
    max_score INTEGER,
    description VARCHAR(255),
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT grade_levels_scale_id_label_key UNIQUE (scale_id, label)
);
CREATE INDEX IF NOT EXISTS idx_grade_levels_scale
    ON schoolers.grade_levels (scale_id);

-- Verification (run after applying):
--   SELECT column_name FROM information_schema.columns
--    WHERE table_schema = 'schoolers' AND table_name = 'schools' AND column_name = 'marking_system';
--   SELECT COUNT(*) FROM schoolers.examination_types;
--   SELECT COUNT(*) FROM schoolers.grade_scales;
--   SELECT COUNT(*) FROM schoolers.grade_levels;
