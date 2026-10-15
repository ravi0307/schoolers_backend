-- Session marks lifecycle: workflow/assessment status, formats, sessions (Phase 4).
--
-- Legacy term-scoped behavior is preserved exactly: existing rows are
-- backfilled as published/present/marks with max 100 (the only maximum
-- that ever validated them), so parent-visible counts do not move.
-- New session-scoped writes start as drafts and publish explicitly.
--
-- Every statement is idempotent, so this file is safe to run more than once.
-- Nothing existing is dropped except the two constraints this design
-- replaces (uniqueness becomes session-aware partial indexes; the 0-100
-- ceiling becomes per-session data validated at the API).

-- 1. New nullable columns first; existing rows survive untouched.
ALTER TABLE IF EXISTS schoolers.marks
    ADD COLUMN IF NOT EXISTS examination_session_id INTEGER
        REFERENCES schoolers.examination_sessions(session_id) ON DELETE RESTRICT;
ALTER TABLE IF EXISTS schoolers.marks
    ADD COLUMN IF NOT EXISTS max_marks INTEGER;
ALTER TABLE IF EXISTS schoolers.marks
    ADD COLUMN IF NOT EXISTS grade_level_id INTEGER
        REFERENCES schoolers.grade_levels(level_id) ON DELETE RESTRICT;
ALTER TABLE IF EXISTS schoolers.marks
    ADD COLUMN IF NOT EXISTS result_format VARCHAR(10);
ALTER TABLE IF EXISTS schoolers.marks
    ADD COLUMN IF NOT EXISTS workflow_status VARCHAR(20);
ALTER TABLE IF EXISTS schoolers.marks
    ADD COLUMN IF NOT EXISTS assessment_status VARCHAR(20);
ALTER TABLE IF EXISTS schoolers.marks
    ADD COLUMN IF NOT EXISTS is_withheld BOOLEAN;
ALTER TABLE IF EXISTS schoolers.marks
    ADD COLUMN IF NOT EXISTS withheld_reason VARCHAR(255);
ALTER TABLE IF EXISTS schoolers.marks
    ADD COLUMN IF NOT EXISTS withheld_by INTEGER
        REFERENCES schoolers.users(user_id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS schoolers.marks
    ADD COLUMN IF NOT EXISTS withheld_at TIMESTAMP;
ALTER TABLE IF EXISTS schoolers.marks
    ALTER COLUMN score DROP NOT NULL;

-- 2. Backfill: legacy rows are the visible-today results, so they stay
-- visible (published/present/marks, max 100). No year, exam, or class is
-- guessed; session-linked columns stay NULL on legacy rows.
UPDATE schoolers.marks SET workflow_status = 'published' WHERE workflow_status IS NULL;
UPDATE schoolers.marks SET assessment_status = 'present' WHERE assessment_status IS NULL;
UPDATE schoolers.marks SET result_format = 'marks' WHERE result_format IS NULL;
UPDATE schoolers.marks SET max_marks = 100 WHERE max_marks IS NULL AND score IS NOT NULL;
UPDATE schoolers.marks SET is_withheld = FALSE WHERE is_withheld IS NULL;

-- 3. Tighten nullability once every row carries values.
ALTER TABLE IF EXISTS schoolers.marks
    ALTER COLUMN result_format SET NOT NULL,
    ALTER COLUMN result_format SET DEFAULT 'marks',
    ALTER COLUMN workflow_status SET NOT NULL,
    ALTER COLUMN workflow_status SET DEFAULT 'draft',
    ALTER COLUMN assessment_status SET NOT NULL,
    ALTER COLUMN assessment_status SET DEFAULT 'present',
    ALTER COLUMN is_withheld SET NOT NULL,
    ALTER COLUMN is_withheld SET DEFAULT FALSE;

-- 4. Replace the legacy uniqueness + ceiling with session-aware rules.
ALTER TABLE IF EXISTS schoolers.marks
    DROP CONSTRAINT IF EXISTS marks_student_id_subject_id_term_key;
ALTER TABLE IF EXISTS schoolers.marks
    DROP CONSTRAINT IF EXISTS marks_score_check;
CREATE UNIQUE INDEX IF NOT EXISTS marks_legacy_unique
    ON schoolers.marks (student_id, subject_id, term)
    WHERE examination_session_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS marks_session_unique
    ON schoolers.marks (examination_session_id, student_id)
    WHERE examination_session_id IS NOT NULL;
ALTER TABLE IF EXISTS schoolers.marks
    DROP CONSTRAINT IF EXISTS marks_score_nonnegative;
ALTER TABLE IF EXISTS schoolers.marks
    ADD CONSTRAINT marks_score_nonnegative CHECK (score IS NULL OR score >= 0);
ALTER TABLE IF EXISTS schoolers.marks
    DROP CONSTRAINT IF EXISTS marks_max_positive;
ALTER TABLE IF EXISTS schoolers.marks
    ADD CONSTRAINT marks_max_positive CHECK (max_marks IS NULL OR max_marks > 0);
ALTER TABLE IF EXISTS schoolers.marks
    DROP CONSTRAINT IF EXISTS marks_format_check;
ALTER TABLE IF EXISTS schoolers.marks
    ADD CONSTRAINT marks_format_check CHECK (result_format IN ('marks', 'grade'));
ALTER TABLE IF EXISTS schoolers.marks
    DROP CONSTRAINT IF EXISTS marks_workflow_check;
ALTER TABLE IF EXISTS schoolers.marks
    ADD CONSTRAINT marks_workflow_check
    CHECK (workflow_status IN ('draft', 'submitted', 'published'));
ALTER TABLE IF EXISTS schoolers.marks
    DROP CONSTRAINT IF EXISTS marks_assessment_check;
ALTER TABLE IF EXISTS schoolers.marks
    ADD CONSTRAINT marks_assessment_check
    CHECK (assessment_status IN ('present', 'absent', 'missing', 'exempt'));
ALTER TABLE IF EXISTS schoolers.marks
    DROP CONSTRAINT IF EXISTS marks_payload_coherence;
ALTER TABLE IF EXISTS schoolers.marks
    ADD CONSTRAINT marks_payload_coherence CHECK (
        (assessment_status = 'present' AND result_format = 'marks'
         AND score IS NOT NULL AND max_marks IS NOT NULL AND grade_level_id IS NULL)
        OR (assessment_status = 'present' AND result_format = 'grade'
         AND grade_level_id IS NOT NULL AND score IS NULL)
        OR (assessment_status IN ('absent', 'missing', 'exempt')
         AND score IS NULL AND grade_level_id IS NULL)
    );

-- 5. Post-publish corrections (append-only; originals stay auditable).
CREATE TABLE IF NOT EXISTS schoolers.mark_corrections (
    correction_id SERIAL PRIMARY KEY,
    mark_id INTEGER NOT NULL REFERENCES schoolers.marks(mark_id) ON DELETE CASCADE,
    old_score INTEGER,
    old_grade_level_id INTEGER REFERENCES schoolers.grade_levels(level_id) ON DELETE RESTRICT,
    new_score INTEGER,
    new_grade_level_id INTEGER REFERENCES schoolers.grade_levels(level_id) ON DELETE RESTRICT,
    reason VARCHAR(255) NOT NULL,
    corrected_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    corrected_at TIMESTAMP DEFAULT now(),
    approved_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    approved_at TIMESTAMP,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mark_corrections_mark
    ON schoolers.mark_corrections (mark_id);

-- 6. Import batches (idempotent retry) and row-level verdicts.
CREATE TABLE IF NOT EXISTS schoolers.mark_import_batches (
    batch_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    uploaded_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    source_filename VARCHAR(255) NOT NULL,
    file_hash VARCHAR(64) NOT NULL,
    scope_hash VARCHAR(64) NOT NULL,
    session_id INTEGER NOT NULL REFERENCES schoolers.examination_sessions(session_id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'preview',
    created_at TIMESTAMP DEFAULT now(),
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT mark_import_batches_scope_key UNIQUE (school_id, file_hash, scope_hash),
    CONSTRAINT mark_import_batches_status_check CHECK (status IN ('preview', 'committed', 'failed'))
);
CREATE TABLE IF NOT EXISTS schoolers.mark_import_rows (
    row_id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES schoolers.mark_import_batches(batch_id) ON DELETE CASCADE,
    row_no INTEGER NOT NULL,
    student_id INTEGER REFERENCES schoolers.students(student_id) ON DELETE CASCADE,
    subject_id INTEGER REFERENCES schoolers.subjects(subject_id) ON DELETE CASCADE,
    payload JSON,
    verdict VARCHAR(20) NOT NULL,
    errors JSON,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now(),
    CONSTRAINT mark_import_rows_verdict_check CHECK (verdict IN ('valid', 'invalid', 'warning'))
);
CREATE INDEX IF NOT EXISTS idx_mark_import_rows_batch
    ON schoolers.mark_import_rows (batch_id);

-- Verification (run after applying):
--   SELECT workflow_status, assessment_status, result_format, COUNT(*)
--     FROM schoolers.marks GROUP BY 1, 2, 3;
--   SELECT COUNT(*) FROM schoolers.marks WHERE examination_session_id IS NOT NULL;  -- 0 fresh
--   SELECT COUNT(*) FROM schoolers.marks WHERE max_marks IS NULL AND score IS NOT NULL;  -- 0
--   Parent-visible parity: SELECT COUNT(*) FROM schoolers.marks
--     WHERE workflow_status = 'published' AND is_withheld = FALSE;
--     -- must equal the pre-migration row count.
