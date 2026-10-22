-- Durable delivery states for outbound email.
--
-- Every logical outbound email gets exactly one `email_deliveries` row,
-- keyed by a hash of its payload (school, recipients, subject, body). The
-- row moves through explicit states — Pending → Sent | Failed — so:
--
--   * a retry after an ambiguous failure reuses the row instead of creating
--     a duplicate logical delivery, and
--   * a database commit that is followed by an SMTP failure is visible as a
--     Failed row rather than an unexplained total failure.
--
-- Password-reset OTPs and temporary admin credentials are sent directly and
-- never written here: their bodies carry live secrets that must not be
-- stored at rest.
--
-- Every statement is idempotent, so this file is safe to run more than once.

CREATE TABLE IF NOT EXISTS schoolers.email_deliveries (
    delivery_id   SERIAL PRIMARY KEY,
    idempotency_key VARCHAR(64)  NOT NULL UNIQUE,
    recipients    VARCHAR(512) NOT NULL,
    subject       VARCHAR(512) NOT NULL,
    body_text     TEXT         NOT NULL,
    body_html     TEXT,
    state         VARCHAR(16)  NOT NULL DEFAULT 'Pending',
    attempts      INTEGER      NOT NULL DEFAULT 0,
    last_error    VARCHAR(256),
    created_at    TIMESTAMP    DEFAULT now(),
    updated_at    TIMESTAMP    NOT NULL DEFAULT now(),
    sent_at       TIMESTAMP,
    modified_by   INTEGER REFERENCES schoolers.users(user_id) ON DELETE SET NULL,
    modified_at   TIMESTAMP DEFAULT now(),
    CONSTRAINT email_deliveries_state_check
        CHECK (state IN ('Pending', 'Sent', 'Failed'))
);

-- The opportunistic retry sweep reads failed/pending rows by state and age.
CREATE INDEX IF NOT EXISTS email_deliveries_state_updated_idx
    ON schoolers.email_deliveries (state, updated_at);
