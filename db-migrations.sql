-- Keep the seeded database compatible with the current SQLAlchemy models.
ALTER TABLE IF EXISTS schoolers.website_settings
    ADD COLUMN IF NOT EXISTS icon_url VARCHAR(255);
