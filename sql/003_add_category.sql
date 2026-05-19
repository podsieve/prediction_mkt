-- Add category support for multi-leaderboard scraping (overall, coding, etc.)

ALTER TABLE snapshots
    ADD COLUMN category TEXT NOT NULL DEFAULT 'overall';

CREATE INDEX idx_snapshots_category ON snapshots(category);

-- Composite index for common query pattern: latest snapshot per category
CREATE INDEX idx_snapshots_category_scraped ON snapshots(category, scraped_at DESC);

-- Must drop views before recreating with new columns
DROP VIEW IF EXISTS new_model_appearances;
DROP VIEW IF EXISTS model_trajectory;
DROP VIEW IF EXISTS latest_rankings;

CREATE OR REPLACE VIEW latest_rankings AS
SELECT DISTINCT ON (r.model_id, s.category)
    r.id,
    r.model_id,
    m.canonical_name,
    m.organization,
    r.rank,
    r.rank_upper,
    r.rank_lower,
    r.score,
    r.score_ci,
    r.votes,
    s.scraped_at,
    s.total_votes AS total_site_votes,
    s.category
FROM rankings r
JOIN models m ON m.id = r.model_id
JOIN snapshots s ON s.id = r.snapshot_id
WHERE s.status = 'success'
ORDER BY r.model_id, s.category, s.scraped_at DESC;

CREATE OR REPLACE VIEW model_trajectory AS
SELECT
    m.canonical_name,
    m.organization,
    r.model_id,
    s.scraped_at,
    r.rank,
    r.score,
    r.score_ci,
    r.votes,
    s.total_votes AS total_site_votes,
    s.category,
    r.votes::NUMERIC / NULLIF(s.total_votes, 0) * 100 AS vote_share_pct,
    r.score - LAG(r.score) OVER w AS score_delta,
    r.votes - LAG(r.votes) OVER w AS votes_delta,
    r.rank - LAG(r.rank) OVER w AS rank_delta,
    EXTRACT(EPOCH FROM s.scraped_at - LAG(s.scraped_at) OVER w) / 3600.0 AS hours_elapsed
FROM rankings r
JOIN models m ON m.id = r.model_id
JOIN snapshots s ON s.id = r.snapshot_id
WHERE s.status = 'success'
WINDOW w AS (PARTITION BY r.model_id, s.category ORDER BY s.scraped_at)
ORDER BY m.canonical_name, s.category, s.scraped_at;

CREATE OR REPLACE VIEW new_model_appearances AS
SELECT
    m.id AS model_id,
    m.canonical_name,
    m.organization,
    m.first_seen_at,
    r.rank AS initial_rank,
    r.score AS initial_score,
    r.score_ci AS initial_ci,
    r.votes AS initial_votes,
    s.category
FROM models m
JOIN rankings r ON r.model_id = m.id
JOIN snapshots s ON s.id = r.snapshot_id
WHERE s.scraped_at = (
    SELECT MIN(s2.scraped_at)
    FROM snapshots s2
    JOIN rankings r2 ON r2.snapshot_id = s2.id
    WHERE r2.model_id = m.id AND s2.status = 'success'
      AND s2.category = s.category
)
ORDER BY m.first_seen_at DESC;
