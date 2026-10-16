-- Enrollment history, promotion batches, subject offerings (Phase 5).
--
-- students.class_id stays the CURRENT-class cache; class_enrollments is
-- the history. No existing rows are modified by this file, and no
-- enrollment rows are created here: the founding year and first roster
-- come from explicit admin reconciliation (preview then commit).
--
-- Every statement is idempotent, so this file is safe to run more than once.
-- Additive only: no existing table, column, or constraint is altered here.

-- 1. Promotion batches (idempotent retry keys).
CREATE TABLE IF NOT EXISTS schoolers.promotion_batches (
    batch_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    source_year_id INTEGER REFERENCES schoolers.academic_years(year_id) ON DELETE SET NULL,
    target_year_id INTEGER NOT NULL REFERENCES schoolers.academic_years(year_id) ON DELETE CASCADE,
    created_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT now(),
    status VARCHAR(20) NOT NULL DEFAULT 'committed',
    idempotency_key VARCHAR(64) NOT NULL,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT promotion_batches_school_id_key_key UNIQUE (school_id, idempotency_key),
    CONSTRAINT promotion_batches_status_check CHECK (status IN ('committed', 'failed'))
);
CREATE INDEX IF NOT EXISTS idx_promotion_batches_school
    ON schoolers.promotion_batches (school_id);

-- 2. Class enrollment history (one active row per student-year).
CREATE TABLE IF NOT EXISTS schoolers.class_enrollments (
    enroll_id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES schoolers.students(student_id) ON DELETE CASCADE,
    class_id INTEGER NOT NULL REFERENCES schoolers.classes(class_id) ON DELETE RESTRICT,
    academic_year_id INTEGER NOT NULL REFERENCES schoolers.academic_years(year_id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL,
    batch_id INTEGER REFERENCES schoolers.promotion_batches(batch_id) ON DELETE SET NULL,
    decided_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    decided_at TIMESTAMP DEFAULT now(),
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT class_enrollments_status_check CHECK (
        status IN ('enrolled', 'promoted', 'retained', 'transferred', 'withdrawn', 'new')
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS class_enrollments_active_unique
    ON schoolers.class_enrollments (student_id, academic_year_id)
    WHERE status IN ('enrolled', 'promoted', 'retained', 'new');
CREATE INDEX IF NOT EXISTS idx_class_enrollments_class_year
    ON schoolers.class_enrollments (class_id, academic_year_id);

-- 3. Subject offerings per class.
CREATE TABLE IF NOT EXISTS schoolers.class_subjects (
    class_id INTEGER NOT NULL REFERENCES schoolers.classes(class_id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES schoolers.subjects(subject_id) ON DELETE CASCADE,
    is_required BOOLEAN NOT NULL DEFAULT TRUE,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT class_subjects_pkey PRIMARY KEY (class_id, subject_id)
);

-- 4. Per-student subject enrollment per year.
CREATE TABLE IF NOT EXISTS schoolers.student_subjects (
    id SERIAL PRIMARY KEY,
    student_id INTEGER NOT NULL REFERENCES schoolers.students(student_id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES schoolers.subjects(subject_id) ON DELETE CASCADE,
    academic_year_id INTEGER NOT NULL REFERENCES schoolers.academic_years(year_id) ON DELETE CASCADE,
    state VARCHAR(20) NOT NULL DEFAULT 'enrolled',
    is_optional BOOLEAN NOT NULL DEFAULT FALSE,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT student_subjects_student_id_key UNIQUE (student_id, subject_id, academic_year_id),
    CONSTRAINT student_subjects_state_check CHECK (state IN ('enrolled', 'dropped'))
);
CREATE INDEX IF NOT EXISTS idx_student_subjects_student_year
    ON schoolers.student_subjects (student_id, academic_year_id);

-- Verification (run after applying):
--   SELECT COUNT(*) FROM schoolers.class_enrollments;  -- 0 fresh: reconciliation creates rows
--   SELECT COUNT(*) FROM schoolers.promotion_batches;  -- 0 fresh
--   SELECT COUNT(*) FROM schoolers.students;  -- unchanged by this file
