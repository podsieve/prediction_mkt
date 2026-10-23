-- Parsed rows from scrapes that were refused or failed to store, kept so they
-- can be replayed (python -m src.replay) once the cause is fixed — e.g. after
-- aliasing models Arena renamed. Only refused scrapes are saved, and identical
-- content is saved once, so this stays small.

CREATE TABLE raw_scrapes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category        TEXT NOT NULL,
    scraped_at      TIMESTAMPTZ NOT NULL,
    source_url      TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    error_message   TEXT,
    payload         JSONB NOT NULL,
    replayed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (category, content_hash)
);
CREATE INDEX idx_raw_scrapes_pending ON raw_scrapes(scraped_at) WHERE replayed_at IS NULL;
