-- Case-insensitive authentication and password-recovery lookups.
-- Every statement is idempotent and safe to run more than once.

CREATE INDEX IF NOT EXISTS users_lower_username_idx
    ON schoolers.users (lower(username));

CREATE INDEX IF NOT EXISTS users_lower_email_idx
    ON schoolers.users (lower(email));

CREATE INDEX IF NOT EXISTS staff_lower_email_idx
    ON schoolers.staff (lower(email));

CREATE INDEX IF NOT EXISTS parents_lower_email_idx
    ON schoolers.parents (lower(email));
