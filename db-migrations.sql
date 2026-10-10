-- Keep the seeded database compatible with the current SQLAlchemy models.
ALTER TABLE IF EXISTS schoolers.schools
    ADD COLUMN IF NOT EXISTS logo_url VARCHAR(255);

ALTER TABLE IF EXISTS schoolers.periods
    ALTER COLUMN period_time TYPE VARCHAR(31);

ALTER TABLE IF EXISTS schoolers.timetable_entries
    ADD COLUMN IF NOT EXISTS school_id INTEGER,
    ADD COLUMN IF NOT EXISTS period_start_time TIME,
    ADD COLUMN IF NOT EXISTS period_end_time TIME,
    ADD COLUMN IF NOT EXISTS created_on TIMESTAMP DEFAULT timezone('Asia/Kolkata', now()),
    ADD COLUMN IF NOT EXISTS created_by INTEGER;

UPDATE schoolers.timetable_entries te
SET school_id = c.school_id
FROM schoolers.classes c
WHERE te.class_id = c.class_id
  AND te.school_id IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'timetable_entries_school_id_fkey'
          AND conrelid = 'schoolers.timetable_entries'::regclass
    ) THEN
        ALTER TABLE schoolers.timetable_entries
            ADD CONSTRAINT timetable_entries_school_id_fkey
            FOREIGN KEY (school_id) REFERENCES schoolers.schools(school_id)
            ON DELETE CASCADE;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'timetable_entries_created_by_fkey'
          AND conrelid = 'schoolers.timetable_entries'::regclass
    ) THEN
        ALTER TABLE schoolers.timetable_entries
            ADD CONSTRAINT timetable_entries_created_by_fkey
            FOREIGN KEY (created_by) REFERENCES schoolers.users(user_id)
            ON DELETE SET NULL;
    END IF;
END $$;

ALTER TABLE IF EXISTS schoolers.timetable_entries
    ALTER COLUMN school_id SET NOT NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'schoolers'
          AND table_name = 'broadcasts'
          AND column_name = 'from_name'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'schoolers'
          AND table_name = 'broadcasts'
          AND column_name = 'role_name'
    ) THEN
        ALTER TABLE schoolers.broadcasts RENAME COLUMN from_name TO role_name;
    END IF;
END $$;

ALTER TABLE IF EXISTS schoolers.broadcasts
    ADD COLUMN IF NOT EXISTS sender_name VARCHAR(100) NOT NULL DEFAULT '';

-- Who wrote each broadcast. sender_name cannot answer "is this mine?": two
-- admins both post as "Admin", so a client comparing names files one admin's
-- messages under another's (or, when the name it holds differs from the label
-- the server stored, under nobody's, which is every message ending up in
-- Received). Existing rows stay NULL -- they predate authorship and are not
-- guessed at, since guessing wrong is the bug being fixed.
ALTER TABLE IF EXISTS schoolers.broadcasts
    ADD COLUMN IF NOT EXISTS sender_user_id INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS broadcasts_sender_user_idx
    ON schoolers.broadcasts (sender_user_id);

ALTER TABLE IF EXISTS schoolers.broadcasts
    ALTER COLUMN created_at SET DEFAULT timezone('Asia/Kolkata', now());

CREATE OR REPLACE FUNCTION schoolers.set_broadcast_created_at_ist()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.created_at := timezone('Asia/Kolkata', now());
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS broadcasts_set_created_at_ist ON schoolers.broadcasts;

CREATE TRIGGER broadcasts_set_created_at_ist
BEFORE UPDATE ON schoolers.broadcasts
FOR EACH ROW
EXECUTE FUNCTION schoolers.set_broadcast_created_at_ist();

ALTER TABLE IF EXISTS schoolers.staff
    ADD COLUMN IF NOT EXISTS email VARCHAR(120),
    ADD COLUMN IF NOT EXISTS date_of_birth DATE,
    ADD COLUMN IF NOT EXISTS marital_status VARCHAR(20),
    ADD COLUMN IF NOT EXISTS gender VARCHAR(20),
    ADD COLUMN IF NOT EXISTS present_address VARCHAR(255),
    ADD COLUMN IF NOT EXISTS permanent_address VARCHAR(255),
    ADD COLUMN IF NOT EXISTS aadhaar_card VARCHAR(30),
    ADD COLUMN IF NOT EXISTS emergency_number VARCHAR(30),
    ADD COLUMN IF NOT EXISTS driving_license VARCHAR(40),
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE IF EXISTS schoolers.parents
    ADD COLUMN IF NOT EXISTS address VARCHAR(255),
    ADD COLUMN IF NOT EXISTS emergency_number VARCHAR(30);

ALTER TABLE IF EXISTS schoolers.teachers
    ADD COLUMN IF NOT EXISTS staff_id INTEGER UNIQUE REFERENCES schoolers.staff(staff_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS present_address VARCHAR(255),
    ADD COLUMN IF NOT EXISTS permanent_address VARCHAR(255),
    ADD COLUMN IF NOT EXISTS date_of_birth DATE,
    ADD COLUMN IF NOT EXISTS emergency_number VARCHAR(30),
    ADD COLUMN IF NOT EXISTS gender VARCHAR(20);

ALTER TABLE IF EXISTS schoolers.route_students
    ADD COLUMN IF NOT EXISTS status VARCHAR(10) NOT NULL DEFAULT 'pending';

CREATE TABLE IF NOT EXISTS schoolers.vehicles (
    vehicle_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    vehicle_number VARCHAR(30) NOT NULL,
    vehicle_type VARCHAR(40),
    registration_number VARCHAR(40) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT vehicles_school_vehicle_number_key UNIQUE (school_id, vehicle_number)
);

CREATE TABLE IF NOT EXISTS schoolers.pilots (
    pilot_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES schoolers.users(user_id) ON DELETE CASCADE,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    full_name VARCHAR(100) NOT NULL,
    email VARCHAR(120),
    phone VARCHAR(30) NOT NULL,
    present_address VARCHAR(255),
    permanent_address VARCHAR(255),
    aadhaar_number VARCHAR(30),
    dl_number VARCHAR(40),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE IF EXISTS schoolers.pilots
    ADD COLUMN IF NOT EXISTS staff_id INTEGER UNIQUE
    REFERENCES schoolers.staff(staff_id) ON DELETE SET NULL;

-- A student may belong to only one transport route at a time.
DELETE FROM schoolers.route_students older
USING schoolers.route_students newer
WHERE older.student_id = newer.student_id
  AND older.id > newer.id;

CREATE UNIQUE INDEX IF NOT EXISTS route_students_student_id_key
    ON schoolers.route_students (student_id);

-- Route-scoped broadcasts: a pilot can broadcast to the admins, teachers, and
-- the parents of the students riding a specific route.
ALTER TABLE IF EXISTS schoolers.broadcasts
    ADD COLUMN IF NOT EXISTS route_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'broadcasts_route_id_fkey'
          AND conrelid = 'schoolers.broadcasts'::regclass
    ) THEN
        ALTER TABLE schoolers.broadcasts
            ADD CONSTRAINT broadcasts_route_id_fkey
            FOREIGN KEY (route_id) REFERENCES schoolers.routes(route_id)
            ON DELETE CASCADE;
    END IF;
END $$;

ALTER TABLE IF EXISTS schoolers.broadcasts
    DROP CONSTRAINT IF EXISTS broadcasts_scope_check,
    ADD CONSTRAINT broadcasts_scope_check
    CHECK (scope IN ('school', 'class', 'route', 'pilot'));

-- Attendance must be one row per (student, day): the bulk /attendance/mark
-- endpoint upserts with ON CONFLICT (student_id, date), which requires a
-- matching unique constraint. Tables created before this migration may also
-- lack a primary key on attendance_id, so backfill both idempotently.
DELETE FROM schoolers.attendance a
USING schoolers.attendance b
WHERE a.student_id = b.student_id
  AND a.date = b.date
  AND (a.attendance_id < b.attendance_id
       OR (a.attendance_id = b.attendance_id AND a.ctid < b.ctid));

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'attendance_pkey'
          AND conrelid = 'schoolers.attendance'::regclass
    ) THEN
        ALTER TABLE schoolers.attendance
            ADD PRIMARY KEY (attendance_id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'attendance_student_date_key'
          AND conrelid = 'schoolers.attendance'::regclass
    ) THEN
        ALTER TABLE schoolers.attendance
            ADD CONSTRAINT attendance_student_date_key UNIQUE (student_id, date);
    END IF;
END $$;

-- Audit: record which user account last changed a mark (teacher edits also
-- keep updated_by = teachers.teacher_id; admins have no teacher row).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'schoolers'
          AND table_name = 'marks'
          AND column_name = 'updated_by_user'
    ) THEN
        ALTER TABLE schoolers.marks ADD COLUMN updated_by_user INTEGER;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'marks_updated_by_user_fkey'
          AND conrelid = 'schoolers.marks'::regclass
    ) THEN
        ALTER TABLE schoolers.marks
            ADD CONSTRAINT marks_updated_by_user_fkey
            FOREIGN KEY (updated_by_user) REFERENCES schoolers.users(user_id);
    END IF;
END $$;

-- Password reset: one-time token (stored hashed) plus its expiry on the
-- users row, so resetting a password requires the token, not just the
-- identifier.
ALTER TABLE IF EXISTS schoolers.users
    ADD COLUMN IF NOT EXISTS email VARCHAR(255);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'schoolers'
          AND table_name = 'users'
          AND column_name = 'password_reset_token'
    ) THEN
        ALTER TABLE schoolers.users ADD COLUMN password_reset_token VARCHAR(255);
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'schoolers'
          AND table_name = 'users'
          AND column_name = 'password_reset_token_expires_at'
    ) THEN
        ALTER TABLE schoolers.users ADD COLUMN password_reset_token_expires_at TIMESTAMP;
    END IF;
END $$;

-- Audit trail: record who modified each row and when. Applied uniformly to
-- every domain table. modified_by references users.user_id and is left NULL
-- for seed/system writes; the application stamps it via common.audit per
-- request, and modified_at is bumped server-side on every update.
ALTER TABLE IF EXISTS schoolers.schools
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.school_notifications
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.subjects
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.classes
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.periods
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.holidays
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.teachers
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.teacher_class_subjects
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.staff
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.parents
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.students
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.parent_student
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.routes
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.vehicles
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.route_stops
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.route_students
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.timetable_entries
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.attendance
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.marks
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.leave_requests
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.broadcasts
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.media
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.barter_listings
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.activities
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.users
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

ALTER TABLE IF EXISTS schoolers.pilots
    ADD COLUMN IF NOT EXISTS modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS modified_at TIMESTAMP DEFAULT now();

-- Media/gallery: store the actual file behind each album entry.
ALTER TABLE IF EXISTS schoolers.media
    ADD COLUMN IF NOT EXISTS file_url VARCHAR(255),
    ADD COLUMN IF NOT EXISTS media_kind VARCHAR(10);

-- Who uploaded each gallery item. Staff may only edit or remove media they
-- uploaded, which needs a user id to compare against: posted_by is a display
-- label, so two teachers sharing a name (or a renamed teacher) would make a
-- name comparison attribute one person's upload to another. Existing rows stay
-- NULL -- they predate authorship and are deliberately not guessed at, so they
-- remain admin-only.
ALTER TABLE IF EXISTS schoolers.media
    ADD COLUMN IF NOT EXISTS uploader_user_id INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS media_uploader_user_idx
    ON schoolers.media (uploader_user_id);

-- The visual builder replaces the legacy multi-page website. Existing website
-- settings, pages, and testimonials are intentionally retired with the old UI.
DROP TABLE IF EXISTS schoolers.website_pages CASCADE;
DROP TABLE IF EXISTS schoolers.website_testimonials CASCADE;
DROP TABLE IF EXISTS schoolers.website_settings CASCADE;

CREATE TABLE IF NOT EXISTS schoolers.website_builder_sites (
    school_id INTEGER PRIMARY KEY REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    draft JSONB NOT NULL,
    published JSONB,
    published_at TIMESTAMP,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS schoolers.website_queries (
    query_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    name VARCHAR(120) NOT NULL,
    email VARCHAR(254) NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS website_queries_school_created_idx
    ON schoolers.website_queries (school_id, created_at DESC, query_id DESC);

-- Master portal: school admin's name, used to generate the school login.
ALTER TABLE IF EXISTS schoolers.schools
    ADD COLUMN IF NOT EXISTS first_name VARCHAR(100),
    ADD COLUMN IF NOT EXISTS last_name VARCHAR(100);

-- Per-school subject catalogs: subjects are no longer a single global list.
-- Each school manages its own (school_id + name unique, name alone is not).
-- Existing global rows are claimed by the primary seeded school (id 1).
ALTER TABLE IF EXISTS schoolers.subjects
    ADD COLUMN IF NOT EXISTS school_id INTEGER;

UPDATE schoolers.subjects SET school_id = 1 WHERE school_id IS NULL;

ALTER TABLE IF EXISTS schoolers.subjects
    ALTER COLUMN school_id SET NOT NULL,
    ADD CONSTRAINT subjects_school_id_fkey
        FOREIGN KEY (school_id) REFERENCES schoolers.schools(school_id) ON DELETE CASCADE;

ALTER TABLE IF EXISTS schoolers.subjects
    DROP CONSTRAINT IF EXISTS subjects_name_key;

ALTER TABLE IF EXISTS schoolers.subjects
    ADD CONSTRAINT subjects_school_id_name_key UNIQUE (school_id, name);

-- Subjects are soft-deletable: deactivating one hides it from new assignments
-- while keeping marks/timetable references intact; it can be reactivated.
ALTER TABLE IF EXISTS schoolers.subjects
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

-- ============================================================================
-- STAFF / TEACHER / PILOT UNIFICATION
-- Phase 1 (additive): new staff columns, pilot driver fields, staff attendance.
-- ============================================================================

-- staff becomes the single record for every employee. role stays free text
-- (the "Other staff" form sends a custom label); person_type is the reliable
-- discriminator that teaching assignments and reporting filter on.
ALTER TABLE IF EXISTS schoolers.staff
    ADD COLUMN IF NOT EXISTS role_title VARCHAR(100),
    ADD COLUMN IF NOT EXISTS person_type VARCHAR(20) NOT NULL DEFAULT 'staff';

ALTER TABLE IF EXISTS schoolers.staff
    DROP CONSTRAINT IF EXISTS staff_person_type_check;

ALTER TABLE IF EXISTS schoolers.staff
    ADD CONSTRAINT staff_person_type_check
        CHECK (person_type::text = ANY (ARRAY[
            'teacher'::character varying, 'pilot'::character varying,
            'admin'::character varying, 'staff'::character varying
        ]::text[]));

CREATE INDEX IF NOT EXISTS idx_staff_person_type
    ON schoolers.staff(school_id, person_type);

-- Driver-specific fields that a generic staff row should not carry.
ALTER TABLE IF EXISTS schoolers.pilots
    ADD COLUMN IF NOT EXISTS license_expiry DATE,
    ADD COLUMN IF NOT EXISTS route_id INTEGER;

ALTER TABLE IF EXISTS schoolers.pilots
    DROP CONSTRAINT IF EXISTS pilots_route_id_fkey;

ALTER TABLE IF EXISTS schoolers.pilots
    ADD CONSTRAINT pilots_route_id_fkey
        FOREIGN KEY (route_id) REFERENCES schoolers.routes(route_id) ON DELETE SET NULL;

-- One driver per route. Postgres allows repeated NULLs, so unassigned pilots
-- are unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pilots_route_id
    ON schoolers.pilots(route_id) WHERE route_id IS NOT NULL;

-- pilots.user_id becomes optional: the pilot account is now reachable through
-- users.linked_person_id -> staff.staff_id, so the dedicated link is redundant.
ALTER TABLE IF EXISTS schoolers.pilots
    ALTER COLUMN user_id DROP NOT NULL;

-- Dated attendance for every staff member. This replaces the single-value
-- teachers.attendance_status column, which could not record history.
CREATE TABLE IF NOT EXISTS schoolers.staff_attendance (
    attendance_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    staff_id INTEGER NOT NULL REFERENCES schoolers.staff(staff_id) ON DELETE CASCADE,
    date DATE NOT NULL,
    status VARCHAR(10) NOT NULL,
    check_in TIME,
    check_out TIME,
    remarks VARCHAR(255),
    marked_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT staff_attendance_staff_id_date_key UNIQUE (staff_id, date),
    CONSTRAINT staff_attendance_status_check
        CHECK (status::text = ANY (ARRAY[
            'Present'::character varying, 'Absent'::character varying,
            'On leave'::character varying, 'Half day'::character varying
        ]::text[]))
);

CREATE INDEX IF NOT EXISTS idx_staff_attendance_school_date
    ON schoolers.staff_attendance(school_id, date);

-- ============================================================================
-- STAFF / TEACHER / PILOT UNIFICATION
-- Phase 2-4 (destructive): fold teachers + pilots into staff.
--
-- teacher_id is retired as a column everywhere and survives only as a
-- read-only API alias of staff_id, so existing clients keep working.
-- ============================================================================

-- A1. Give every teacher a staff row. Teachers that already had staff_id NULL
--     (API-created ones never got a staff row) are matched on school+name+phone.
INSERT INTO schoolers.staff
    (school_id, name, role, role_title, person_type, phone, email,
     present_address, permanent_address, date_of_birth, emergency_number,
     gender, is_active, created_at)
SELECT t.school_id, t.name, 'Teacher', t.role_title, 'teacher', t.phone, t.email,
       t.present_address, t.permanent_address, t.date_of_birth, t.emergency_number,
       t.gender, t.is_active, COALESCE(t.created_at, now())
FROM schoolers.teachers t
WHERE t.staff_id IS NULL;

UPDATE schoolers.teachers t
SET staff_id = s.staff_id
FROM schoolers.staff s
WHERE t.staff_id IS NULL
  AND s.school_id = t.school_id AND s.name = t.name AND s.phone = t.phone;

-- A2. Mark the staff rows that are teachers, carrying over the job title.
UPDATE schoolers.staff s
SET person_type = 'teacher',
    role_title = COALESCE(s.role_title, t.role_title)
FROM schoolers.teachers t
WHERE t.staff_id = s.staff_id;

-- A3. Adopt the generic staff identity onto the pilot rows, then drop the
--     duplicated personal columns. dl_number/aadhaar already exist on staff.
UPDATE schoolers.pilots p
SET full_name = s.name,
    email = s.email,
    phone = s.phone,
    present_address = s.present_address,
    permanent_address = s.permanent_address,
    aadhaar_number = s.aadhaar_card
FROM schoolers.staff s
WHERE p.staff_id = s.staff_id;

-- A4. Pilot logins now resolve through users.linked_person_id -> staff.staff_id.
UPDATE schoolers.users u
SET linked_person_id = p.staff_id
FROM schoolers.pilots p
WHERE p.user_id = u.user_id;

-- A5. Teacher logins resolve to the same staff space.
UPDATE schoolers.users u
SET linked_person_id = t.staff_id
FROM schoolers.teachers t
WHERE t.teacher_id = u.linked_person_id
  AND u.role = 'teacher'
  AND t.staff_id IS NOT NULL;

-- B0. Rewrite the stored teacher ids to the new staff ids BEFORE the columns
--     are renamed. A bare RENAME keeps the old numbers, which would silently
--     point every assignment at an unrelated staff row (teacher 2 -> staff 2).
--     This must run while schoolers.teachers still exists.
UPDATE schoolers.teacher_class_subjects tcs
SET teacher_id = t.staff_id
FROM schoolers.teachers t
WHERE tcs.teacher_id = t.teacher_id AND t.staff_id IS NOT NULL;

UPDATE schoolers.timetable_entries te
SET teacher_id = t.staff_id
FROM schoolers.teachers t
WHERE te.teacher_id = t.teacher_id AND t.staff_id IS NOT NULL;

UPDATE schoolers.classes c
SET class_teacher_id = t.staff_id
FROM schoolers.teachers t
WHERE c.class_teacher_id = t.teacher_id AND t.staff_id IS NOT NULL;

-- Any teacher id with no matching staff row would be left dangling; null it out
-- so the new SET NULL / cascade semantics apply instead of a wrong reference.
UPDATE schoolers.teacher_class_subjects tcs
SET teacher_id = NULL
WHERE teacher_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM schoolers.teachers t WHERE t.teacher_id = tcs.teacher_id);

UPDATE schoolers.timetable_entries te
SET teacher_id = NULL
WHERE teacher_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM schoolers.teachers t WHERE t.teacher_id = te.teacher_id);

UPDATE schoolers.classes c
SET class_teacher_id = NULL
WHERE class_teacher_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM schoolers.teachers t WHERE t.teacher_id = c.class_teacher_id);

-- B. Repoint the five foreign keys off teachers.teacher_id.
ALTER TABLE schoolers.teacher_class_subjects DROP CONSTRAINT IF EXISTS teacher_class_subjects_teacher_id_fkey;
ALTER TABLE schoolers.teacher_class_subjects RENAME COLUMN teacher_id TO staff_id;
ALTER TABLE schoolers.teacher_class_subjects DROP CONSTRAINT IF EXISTS teacher_class_subjects_teacher_id_class_id_subject_id_key;
ALTER TABLE schoolers.teacher_class_subjects ADD CONSTRAINT teacher_class_subjects_staff_id_class_id_subject_id_key UNIQUE (staff_id, class_id, subject_id);
ALTER TABLE schoolers.teacher_class_subjects ADD CONSTRAINT teacher_class_subjects_staff_id_fkey FOREIGN KEY (staff_id) REFERENCES schoolers.staff(staff_id) ON DELETE CASCADE;

ALTER TABLE schoolers.timetable_entries DROP CONSTRAINT IF EXISTS timetable_entries_teacher_id_fkey;
ALTER TABLE schoolers.timetable_entries RENAME COLUMN teacher_id TO staff_id;
ALTER TABLE schoolers.timetable_entries ADD CONSTRAINT timetable_entries_staff_id_fkey FOREIGN KEY (staff_id) REFERENCES schoolers.staff(staff_id) ON DELETE SET NULL;

ALTER TABLE schoolers.classes DROP CONSTRAINT IF EXISTS classes_class_teacher_id_fkey;
ALTER TABLE schoolers.classes DROP CONSTRAINT IF EXISTS fk_classes_teacher;
ALTER TABLE schoolers.classes RENAME COLUMN class_teacher_id TO class_teacher_staff_id;
ALTER TABLE schoolers.classes ADD CONSTRAINT classes_class_teacher_staff_id_fkey FOREIGN KEY (class_teacher_staff_id) REFERENCES schoolers.staff(staff_id) ON DELETE SET NULL;

ALTER TABLE schoolers.attendance DROP CONSTRAINT IF EXISTS attendance_marked_by_fkey;
ALTER TABLE schoolers.attendance DROP COLUMN IF EXISTS marked_by;
ALTER TABLE schoolers.attendance ADD COLUMN IF NOT EXISTS marked_by INTEGER;
ALTER TABLE schoolers.attendance ADD CONSTRAINT attendance_marked_by_fkey FOREIGN KEY (marked_by) REFERENCES schoolers.staff(staff_id) ON DELETE SET NULL;

ALTER TABLE schoolers.marks DROP CONSTRAINT IF EXISTS marks_updated_by_fkey;
ALTER TABLE schoolers.marks DROP COLUMN IF EXISTS updated_by;
ALTER TABLE schoolers.marks ADD COLUMN IF NOT EXISTS updated_by INTEGER;
ALTER TABLE schoolers.marks ADD CONSTRAINT marks_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES schoolers.staff(staff_id) ON DELETE SET NULL;

-- C. Retire the teachers table and the free-text route driver.
DROP TABLE IF EXISTS schoolers.teachers;
ALTER TABLE schoolers.routes DROP COLUMN IF EXISTS driver_name;

-- D. Reduce pilots to the driver-specific fields only.
ALTER TABLE schoolers.pilots DROP CONSTRAINT IF EXISTS pilots_user_id_fkey;
ALTER TABLE schoolers.pilots DROP CONSTRAINT IF EXISTS pilots_school_id_fkey;
ALTER TABLE schoolers.pilots DROP COLUMN IF EXISTS user_id;
ALTER TABLE schoolers.pilots DROP COLUMN IF EXISTS school_id;
ALTER TABLE schoolers.pilots DROP COLUMN IF EXISTS full_name;
ALTER TABLE schoolers.pilots DROP COLUMN IF EXISTS email;
ALTER TABLE schoolers.pilots DROP COLUMN IF EXISTS phone;
ALTER TABLE schoolers.pilots DROP COLUMN IF EXISTS present_address;
ALTER TABLE schoolers.pilots DROP COLUMN IF EXISTS permanent_address;
ALTER TABLE schoolers.pilots DROP COLUMN IF EXISTS aadhaar_number;
ALTER TABLE schoolers.pilots DROP COLUMN IF EXISTS dl_number;
ALTER TABLE schoolers.pilots DROP COLUMN IF EXISTS created_at;

-- staff_id is now mandatory and cascading, matching the model.
DELETE FROM schoolers.pilots WHERE staff_id IS NULL;
ALTER TABLE schoolers.pilots DROP CONSTRAINT IF EXISTS pilots_staff_id_fkey;
ALTER TABLE schoolers.pilots ALTER COLUMN staff_id SET NOT NULL;
ALTER TABLE schoolers.pilots ADD CONSTRAINT pilots_staff_id_fkey FOREIGN KEY (staff_id) REFERENCES schoolers.staff(staff_id) ON DELETE CASCADE;

-- E. The denormalised single-value attendance column lived on teachers and
--    goes away with the table; the dated staff_attendance table replaces it.

-- ============================================================================
-- ACCOUNTS: staff salaries and student fees
-- ============================================================================
-- One row per person per calendar month. `month` is a 'YYYY-MM' string because
-- the admin view is a six-column month grid and a month bucket has no day.
-- The unique constraints make a second payment for the same month overwrite
-- the first rather than add a row, which is what the grid expects: one figure
-- per cell.
--
-- school_id is denormalised even though staff and students are already
-- school-scoped: the sheet read is a single query filtered by school, and the
-- column makes the ownership of a payment explicit rather than something a
-- join has to be trusted to derive.
--
-- amount is NUMERIC, never float: money does not belong in binary floating
-- point. The API returns it as a JSON number for the admin display total.

CREATE TABLE IF NOT EXISTS schoolers.staff_salaries (
    salary_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    staff_id INTEGER NOT NULL REFERENCES schoolers.staff(staff_id) ON DELETE CASCADE,
    month VARCHAR(7) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    paid_on DATE,
    note VARCHAR(200),
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT staff_salaries_staff_id_month_key UNIQUE (staff_id, month),
    CONSTRAINT staff_salaries_month_check
        CHECK (month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    CONSTRAINT staff_salaries_amount_check CHECK (amount >= 0)
);

CREATE TABLE IF NOT EXISTS schoolers.student_fees (
    fee_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES schoolers.students(student_id) ON DELETE CASCADE,
    month VARCHAR(7) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    paid_on DATE,
    note VARCHAR(200),
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT student_fees_student_id_month_key UNIQUE (student_id, month),
    CONSTRAINT student_fees_month_check
        CHECK (month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    CONSTRAINT student_fees_amount_check CHECK (amount >= 0)
);

-- The accounts grid is useless without an index per school over the month
-- column: the sheet read filters on school_id AND month IN (six values).
CREATE INDEX IF NOT EXISTS staff_salaries_school_month_idx
    ON schoolers.staff_salaries (school_id, month);
CREATE INDEX IF NOT EXISTS student_fees_school_month_idx
    ON schoolers.student_fees (school_id, month);

-- ============================================================================
-- SUPPORT: admin-raised tickets to the master admin, with a message thread
-- ============================================================================
-- A school admin raises an issue about the portal and the master admin works
-- it. The ticket is the listable summary (school, subject, status); the
-- conversation lives on support_ticket_messages so a ticket carries its whole
-- trail without the list query dragging every body along.
--
-- created_by_name / author_name are denormalised for the same reason
-- leave_requests.requester_name is: the display name lives on the linked staff
-- row and is resolved server-side, and a ticket should stay readable even if
-- that account is later removed. attachments is a JSON list of URLs minted by
-- the existing upload endpoints -- support stores references, never files.

CREATE TABLE IF NOT EXISTS schoolers.support_tickets (
    ticket_id SERIAL PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    created_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    created_by_name VARCHAR(100) NOT NULL,
    subject VARCHAR(200) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'Open',
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT support_tickets_status_check
        CHECK (status IN ('Open','In progress','Assigned','Completed','Cancelled'))
);

CREATE TABLE IF NOT EXISTS schoolers.support_ticket_messages (
    message_id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES schoolers.support_tickets(ticket_id) ON DELETE CASCADE,
    author_user_id INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    author_role VARCHAR(20) NOT NULL,
    author_name VARCHAR(100) NOT NULL,
    body TEXT NOT NULL,
    attachments JSON NOT NULL DEFAULT '[]',
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT support_ticket_messages_role_check
        CHECK (author_role IN ('admin','master'))
);

-- The admin queue filters school + status and orders newest-first; the master
-- inbox filters status alone across every school. One index per read path.
CREATE INDEX IF NOT EXISTS support_tickets_school_status_idx
    ON schoolers.support_tickets (school_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS support_tickets_status_idx
    ON schoolers.support_tickets (status, created_at DESC);
CREATE INDEX IF NOT EXISTS support_ticket_messages_ticket_idx
    ON schoolers.support_ticket_messages (ticket_id, created_at);

-- ============================================================================
-- TRANSPORT TRIP HISTORY: durable historical trip records
-- ============================================================================
-- The existing pick/drop model tracks only the *current* student status on a
-- route (route_students.status, overwritten on every pilot update) and has no
-- record of when that status changed or which calendar day it applied to. A
-- parent asking "what happened on this route for this child on this specific
-- day" cannot be answered from route_students alone.
--
-- This migration adds two immutable-history tables:
--
--   trips            - one row per route run on a specific date, per leg
--                      (direction). Snapshots the route, pilot and vehicle at
--                      the time the trip was created, so a later
--                      route/pilot/vehicle change cannot rewrite history.
--
--   trip_students    - one row per (trip, student) participation. Records the
--                      boarding outcome, drop outcome and the actual times, so
--                      multiple days and multiple status events are preserved
--                      instead of being overwritten.
--
-- Design notes (decisions from the trip-history discovery phase):
--   * direction is 'pickup' | 'drop' - the transport domain's own vocabulary
--     (RouteStop.stop_type pickup/drop, pickup_time/drop_time). There is no
--     morning/evening concept anywhere in the application, so that draft value
--     was replaced rather than assumed.
--   * vehicle snapshots Route.vehicle, the string the parent pick/drop payloads
--     display; the separate `vehicles` catalog references no route and is not
--     part of the commute flow.
--   * pilot_id references pilots.pilot_id for "who drove" filtering, and
--     driver_name snapshots the driver's Staff.name so history survives staff
--     renames/deactivations (deleting a staff row cascades its pilots row and
--     would NULL pilot_id; the displayed name must remain).
--   * One trip per (route, trip_date, direction): a day has at most one
--     scheduled boarding run and one scheduled drop run per route. A cancelled
--     run is reopened on the same row rather than creating a replacement trip,
--     so this uniqueness holds even across cancellations.
--   * boarding_status uses 'picked' (the live RouteStudent.status word) plus
--     'did_not_board'; drop_status uses 'dropped' plus 'drop_not_recorded'.
--
-- Both tables are scoped to a school (school_id) and carry the audit columns
-- every domain table has. Nothing here touches routes, route_stops,
-- route_students or pilots: those keep working exactly as before.
-- ============================================================================

-- Older backups may already contain trips with the previous trip_type,
-- vehicle_number and cancelled_reason names. Upgrade those rows in place
-- before the current table definition below is applied.
DO $$
BEGIN
    IF to_regclass('schoolers.trips') IS NOT NULL THEN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'schoolers' AND table_name = 'trips'
              AND column_name = 'trip_type'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'schoolers' AND table_name = 'trips'
              AND column_name = 'direction'
        ) THEN
            ALTER TABLE schoolers.trips RENAME COLUMN trip_type TO direction;
        END IF;
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'schoolers' AND table_name = 'trips'
              AND column_name = 'vehicle_number'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'schoolers' AND table_name = 'trips'
              AND column_name = 'vehicle'
        ) THEN
            ALTER TABLE schoolers.trips RENAME COLUMN vehicle_number TO vehicle;
        END IF;
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'schoolers' AND table_name = 'trips'
              AND column_name = 'cancelled_reason'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'schoolers' AND table_name = 'trips'
              AND column_name = 'cancellation_reason'
        ) THEN
            ALTER TABLE schoolers.trips RENAME COLUMN cancelled_reason TO cancellation_reason;
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'schoolers.trips'::regclass
              AND conname = 'trips_type_check'
        ) AND NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'schoolers.trips'::regclass
              AND conname = 'trips_direction_check'
        ) THEN
            ALTER TABLE schoolers.trips
                RENAME CONSTRAINT trips_type_check TO trips_direction_check;
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'schoolers.trips'::regclass
              AND conname = 'trips_route_type_date_key'
        ) AND NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'schoolers.trips'::regclass
              AND conname = 'uq_trips_route_date_direction'
        ) THEN
            ALTER TABLE schoolers.trips
                RENAME CONSTRAINT trips_route_type_date_key
                TO uq_trips_route_date_direction;
        END IF;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS schoolers.trips (
    trip_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    school_id INTEGER NOT NULL REFERENCES schoolers.schools(school_id) ON DELETE CASCADE,
    route_id INTEGER NOT NULL REFERENCES schoolers.routes(route_id) ON DELETE CASCADE,
    trip_date DATE NOT NULL,
    direction VARCHAR(10) NOT NULL DEFAULT 'pickup',
    pilot_id INTEGER REFERENCES schoolers.pilots(pilot_id) ON DELETE SET NULL,
    driver_name VARCHAR(100),
    vehicle VARCHAR(60) NOT NULL,
    status VARCHAR(15) NOT NULL DEFAULT 'scheduled',
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    cancelled_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    cancellation_reason VARCHAR(255),
    reopened_at TIMESTAMP,
    reopened_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    reopen_reason VARCHAR(255),
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT trips_direction_check
        CHECK (direction IN ('pickup','drop')),
    CONSTRAINT trips_status_check
        CHECK (status IN ('scheduled','in_progress','completed','cancelled'))
);

ALTER TABLE schoolers.trips
    ADD COLUMN IF NOT EXISTS driver_name VARCHAR(100),
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS cancelled_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS reopened_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS reopened_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS reopen_reason VARCHAR(255);

ALTER TABLE schoolers.trips
    ALTER COLUMN cancellation_reason TYPE VARCHAR(255);

UPDATE schoolers.trips t
SET vehicle = r.vehicle
FROM schoolers.routes r
WHERE t.route_id = r.route_id AND t.vehicle IS NULL;

UPDATE schoolers.trips t
SET driver_name = s.name
FROM schoolers.pilots p
JOIN schoolers.staff s ON s.staff_id = p.staff_id
WHERE t.pilot_id = p.pilot_id AND t.driver_name IS NULL;

ALTER TABLE schoolers.trips
    ALTER COLUMN vehicle SET NOT NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'schoolers.trips'::regclass
          AND conname = 'trips_route_id_fkey'
          AND pg_get_constraintdef(oid) NOT LIKE '%ON DELETE CASCADE%'
    ) THEN
        ALTER TABLE schoolers.trips DROP CONSTRAINT trips_route_id_fkey;
        ALTER TABLE schoolers.trips
            ADD CONSTRAINT trips_route_id_fkey
            FOREIGN KEY (route_id) REFERENCES schoolers.routes(route_id) ON DELETE CASCADE;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS schoolers.trip_students (
    trip_student_id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    trip_id INTEGER NOT NULL REFERENCES schoolers.trips(trip_id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES schoolers.students(student_id) ON DELETE CASCADE,
    boarding_status VARCHAR(10) NOT NULL DEFAULT 'pending',
    boarding_at TIMESTAMP,
    boarding_stop_id INTEGER REFERENCES schoolers.route_stops(stop_id) ON DELETE SET NULL,
    drop_status VARCHAR(10) NOT NULL DEFAULT 'pending',
    drop_at TIMESTAMP,
    drop_stop_id INTEGER REFERENCES schoolers.route_stops(stop_id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    modified_by INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT trip_students_boarding_status_check
        CHECK (boarding_status IN ('pending','picked','did_not_board')),
    CONSTRAINT trip_students_drop_status_check
        CHECK (drop_status IN ('pending','dropped','drop_not_recorded'))
);

ALTER TABLE schoolers.trip_students
    ALTER COLUMN boarding_status TYPE VARCHAR(20),
    ALTER COLUMN drop_status TYPE VARCHAR(20);

-- One scheduled run per (route, date, leg). Prevents a pilot accidentally
-- starting two pickup trips for the same route on the same day; a cancelled
-- run is reopened in place, so this does not block recovery.
CREATE UNIQUE INDEX IF NOT EXISTS uq_trips_route_date_direction
    ON schoolers.trips (route_id, trip_date, direction);

-- A student participates at most once per trip; upserts update the same row.
CREATE UNIQUE INDEX IF NOT EXISTS uq_trip_students_trip_student
    ON schoolers.trip_students (trip_id, student_id);

-- Read paths: trip by school+date, trip by route+date, trip by pilot+date,
-- trip by status+date, trip student by trip+student, parent -> child -> trip.
CREATE INDEX IF NOT EXISTS idx_trips_school_date
    ON schoolers.trips (school_id, trip_date);
CREATE INDEX IF NOT EXISTS idx_trips_route_date
    ON schoolers.trips (route_id, trip_date);
CREATE INDEX IF NOT EXISTS idx_trips_pilot_date
    ON schoolers.trips (pilot_id, trip_date);
CREATE INDEX IF NOT EXISTS idx_trips_status_date
    ON schoolers.trips (status, trip_date);
CREATE INDEX IF NOT EXISTS idx_trip_students_trip_student
    ON schoolers.trip_students (trip_id, student_id);
