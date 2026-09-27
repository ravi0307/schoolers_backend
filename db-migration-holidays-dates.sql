-- Holidays become named, date-specific rows.
--
-- Previously `holidays` was a recurring weekday template: one row per
-- (school_id, day_of_week) with an is_holiday flag, so a school could only
-- express "we are closed every Saturday". The admin portal now manages real
-- calendar dates ("Diwali", "2026-11-08"), which is a different thing and
-- cannot be stored in the old shape.
--
-- The table is repurposed rather than dropped and recreated so that the primary
-- key sequence, the school FK, and the grants survive. Every statement is
-- idempotent, so this file is safe to run more than once.
--
-- NOTE: the recurring rows cannot be translated into dated holidays, so they
-- are discarded. A school that was closed every Saturday/Sunday must re-add
-- those as dated rows.

-- 1. Drop the constraints that reference the old columns. Both must go before
--    the columns themselves, or the DROP COLUMN below would fail.
ALTER TABLE IF EXISTS schoolers.holidays
    DROP CONSTRAINT IF EXISTS holidays_school_id_day_of_week_key;
ALTER TABLE IF EXISTS schoolers.holidays
    DROP CONSTRAINT IF EXISTS holidays_day_of_week_check;

-- 2. Add the new columns as nullable so existing rows survive the swap.
ALTER TABLE IF EXISTS schoolers.holidays
    ADD COLUMN IF NOT EXISTS occasion VARCHAR(120);
ALTER TABLE IF EXISTS schoolers.holidays
    ADD COLUMN IF NOT EXISTS holiday_date DATE;

-- 3. Discard the recurring rows. They carry a weekday and a flag but no name
--    and no date, so there is nothing to convert.
DELETE FROM schoolers.holidays
WHERE occasion IS NULL OR holiday_date IS NULL;

-- 4. Make the new columns mandatory, matching the model.
ALTER TABLE IF EXISTS schoolers.holidays
    ALTER COLUMN occasion SET NOT NULL;
ALTER TABLE IF EXISTS schoolers.holidays
    ALTER COLUMN holiday_date SET NOT NULL;

-- 5. Remove the recurring columns.
ALTER TABLE IF EXISTS schoolers.holidays
    DROP COLUMN IF EXISTS day_of_week;
ALTER TABLE IF EXISTS schoolers.holidays
    DROP COLUMN IF EXISTS is_holiday;

-- 6. A school cannot have two holidays on the same date; this is what stops a
--    duplicate row from marking one calendar day twice in the timetable.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'holidays_school_id_holiday_date_key'
          AND conrelid = 'schoolers.holidays'::regclass
    ) THEN
        ALTER TABLE schoolers.holidays
            ADD CONSTRAINT holidays_school_id_holiday_date_key
            UNIQUE (school_id, holiday_date);
    END IF;
END
$$;
