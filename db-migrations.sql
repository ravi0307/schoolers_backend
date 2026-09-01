-- Keep the seeded database compatible with the current SQLAlchemy models.
ALTER TABLE IF EXISTS schoolers.website_settings
    ADD COLUMN IF NOT EXISTS icon_url VARCHAR(255);

ALTER TABLE IF EXISTS schoolers.schools
    ADD COLUMN IF NOT EXISTS logo_url VARCHAR(255);

ALTER TABLE IF EXISTS schoolers.staff
    ADD COLUMN IF NOT EXISTS email VARCHAR(120),
    ADD COLUMN IF NOT EXISTS present_address VARCHAR(255),
    ADD COLUMN IF NOT EXISTS permanent_address VARCHAR(255),
    ADD COLUMN IF NOT EXISTS aadhaar_card VARCHAR(30),
    ADD COLUMN IF NOT EXISTS emergency_number VARCHAR(30);

ALTER TABLE IF EXISTS schoolers.parents
    ADD COLUMN IF NOT EXISTS address VARCHAR(255),
    ADD COLUMN IF NOT EXISTS emergency_number VARCHAR(30);
