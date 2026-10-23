-- The Sub-Admin role ('subadmin').
--
-- Sub-Admin is created through the ordinary Add Staff workflow (the staff
-- role label decides the login role in services/people_service), so role
-- admission again comes down to code (common/authz.py role_allowed +
-- require_role in common/dependencies.py) plus two CHECK constraints that
-- predate the role and would reject it outright:
--
--   * users_role_check — only exists on databases whose schema predates the
--     constraint's removal from the schema files (it survives in pg dumps,
--     e.g. schoolersdb_backup.sql); a fresh build from db-migrations.sql has
--     no users role check at all.
--   * support_ticket_messages_role_check — declared inline in
--     db-migrations.sql; widened here for databases that already have it, and
--     updated there for fresh builds (a Sub-Admin replies in the support
--     inbox with author_role 'subadmin').
--
-- Every statement is idempotent: each constraint is rebuilt only when it
-- exists AND does not already list 'subadmin', so running this file again
-- is a no-op. The rebuilt lists carry every role (including 'submaster'), so
-- this file is safe whether or not db-migration-submaster-role.sql has run
-- first. Apply explicitly to the target database (local dev / prod) — the
-- test suite builds its schema from the SQLAlchemy models, not this file.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'users_role_check'
          AND conrelid = 'schoolers.users'::regclass
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'users_role_check'
          AND conrelid = 'schoolers.users'::regclass
          AND pg_get_constraintdef(oid) LIKE '%subadmin%'
    ) THEN
        ALTER TABLE schoolers.users DROP CONSTRAINT users_role_check;
        ALTER TABLE schoolers.users ADD CONSTRAINT users_role_check
            CHECK (role IN ('parent','teacher','admin','pilot','master','staff','submaster','subadmin'));
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'support_ticket_messages_role_check'
          AND conrelid = 'schoolers.support_ticket_messages'::regclass
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'support_ticket_messages_role_check'
          AND conrelid = 'schoolers.support_ticket_messages'::regclass
          AND pg_get_constraintdef(oid) LIKE '%subadmin%'
    ) THEN
        ALTER TABLE schoolers.support_ticket_messages
            DROP CONSTRAINT support_ticket_messages_role_check;
        ALTER TABLE schoolers.support_ticket_messages
            ADD CONSTRAINT support_ticket_messages_role_check
                CHECK (author_role IN ('admin','master','submaster','subadmin'));
    END IF;
END $$;
