-- B1 rollout gate: per-school opt-in for the examination framework.
--
-- Default off: existing schools see zero behavior change, and legacy marks
-- paths never read this column, so the flag cannot disable legacy
-- functionality by construction. A master flips one school at a time via
-- PATCH /schools/:id/features (the existing feature-flag convention).
--
-- Every statement is idempotent, so this file is safe to run more than once.
-- Additive only: nothing existing is altered.

ALTER TABLE IF EXISTS schoolers.schools
    ADD COLUMN IF NOT EXISTS examinations_enabled BOOLEAN NOT NULL DEFAULT FALSE;

-- Verification (run after applying):
--   SELECT school_id, examinations_enabled FROM schoolers.schools;
--   -- all rows FALSE on fresh apply; legacy marks reads/writes unaffected.
