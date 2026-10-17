-- Examinations and sessions: scheduling source of truth (Phase 3).
--
-- An examination holds academic context (year, period, type, format);
-- examination_sessions hold the schedulable units (class x subject x
-- date/time/venue). Amendments to published sessions append rows to
-- examination_session_versions; the live row is always current.
-- Marks link to sessions in Phase 4; nothing references sessions yet.
--
-- Every statement is idempotent, so this file is safe to run more than once.
-- Additive only: no existing table, column, or constraint is altered here.

-- 1. Examinations (academic context, no scheduling fields).
CREATE TABLE IF NOT EXISTS schoolers.examinations (
    exam_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    type_id INTEGER NOT NULL REFERENCES schoolers.examination_types(type_id) ON DELETE RESTRICT,
    year_id INTEGER NOT NULL REFERENCES schoolers.academic_years(year_id) ON DELETE RESTRICT,
    period_id INTEGER REFERENCES schoolers.academic_periods(period_id) ON DELETE SET NULL,
    name VARCHAR(120) NOT NULL,
    description VARCHAR(255),
    result_format_expectation VARCHAR(10) NOT NULL DEFAULT 'marks',
    grade_scale_id INTEGER REFERENCES schoolers.grade_scales(scale_id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    created_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT examinations_school_id_year_id_name_key UNIQUE (school_id, year_id, name),
    CONSTRAINT examinations_format_check CHECK (result_format_expectation IN ('marks', 'grade')),
    CONSTRAINT examinations_status_check CHECK (status IN ('draft', 'published', 'cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_examinations_school
    ON schoolers.examinations (school_id);
CREATE INDEX IF NOT EXISTS idx_examinations_year
    ON schoolers.examinations (year_id);

-- 2. Sessions (the schedulable units; conflict checks read these).
CREATE TABLE IF NOT EXISTS schoolers.examination_sessions (
    session_id SERIAL PRIMARY KEY,
    exam_id INTEGER NOT NULL REFERENCES schoolers.examinations(exam_id) ON DELETE CASCADE,
    class_id INTEGER NOT NULL REFERENCES schoolers.classes(class_id) ON DELETE RESTRICT,
    subject_id INTEGER NOT NULL REFERENCES schoolers.subjects(subject_id) ON DELETE RESTRICT,
    date DATE NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    venue VARCHAR(120),
    max_marks INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'scheduled',
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT examination_sessions_time_check CHECK (end_time > start_time),
    CONSTRAINT examination_sessions_max_check CHECK (max_marks > 0),
    CONSTRAINT examination_sessions_status_check CHECK (
        status IN ('scheduled', 'rescheduled', 'cancelled', 'completed')
    )
);
CREATE INDEX IF NOT EXISTS idx_examination_sessions_exam
    ON schoolers.examination_sessions (exam_id);
CREATE INDEX IF NOT EXISTS idx_examination_sessions_class_date
    ON schoolers.examination_sessions (class_id, date);
CREATE INDEX IF NOT EXISTS idx_examination_sessions_venue_date
    ON schoolers.examination_sessions (venue, date);

-- 3. Append-only amendment history for published sessions.
CREATE TABLE IF NOT EXISTS schoolers.examination_session_versions (
    version_id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES schoolers.examination_sessions(session_id) ON DELETE CASCADE,
    changed_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    changed_at TIMESTAMP DEFAULT now(),
    reason VARCHAR(255),
    old_row JSON,
    new_row JSON,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_examination_session_versions_session
    ON schoolers.examination_session_versions (session_id);

-- Verification (run after applying):
--   SELECT COUNT(*) FROM schoolers.examinations;
--   SELECT COUNT(*) FROM schoolers.examination_sessions;
--   SELECT COUNT(*) FROM schoolers.examination_session_versions;
--   SELECT COUNT(*) FROM schoolers.marks;  -- unchanged by this file
