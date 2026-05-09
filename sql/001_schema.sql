-- Arena.ai Leaderboard Tracker — Core Schema

CREATE TABLE models (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name  TEXT NOT NULL UNIQUE,
    organization    TEXT,
    license_type    TEXT,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE model_aliases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id        UUID NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    alias_name      TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_model_aliases_model_id ON model_aliases(model_id);

CREATE TABLE snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scraped_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_url          TEXT NOT NULL,
    total_models        INTEGER,
    total_votes         BIGINT,
    scrape_duration_ms  INTEGER,
    status              TEXT NOT NULL DEFAULT 'success',
    error_message       TEXT,
    raw_html_hash       TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_snapshots_scraped_at ON snapshots(scraped_at DESC);
CREATE INDEX idx_snapshots_status ON snapshots(status);

CREATE TABLE rankings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id         UUID NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    model_id            UUID NOT NULL REFERENCES models(id),
    rank                INTEGER NOT NULL,
    rank_upper          INTEGER,
    rank_lower          INTEGER,
    score               NUMERIC(7,1) NOT NULL,
    score_ci            NUMERIC(5,1),
    votes               BIGINT NOT NULL,
    raw_model_name      TEXT NOT NULL,
    raw_organization    TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(snapshot_id, model_id)
);
CREATE INDEX idx_rankings_model_id ON rankings(model_id);
CREATE INDEX idx_rankings_snapshot_id ON rankings(snapshot_id);
CREATE INDEX idx_rankings_model_created ON rankings(model_id, created_at DESC);
