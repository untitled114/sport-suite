-- Intelligence schema
SET search_path TO intelligence, public;

-- ==========================================================================
-- INJURY REPORT
-- ==========================================================================
CREATE TABLE injury_report (
    injury_id SERIAL PRIMARY KEY,
    player_id INTEGER NOT NULL,
    report_date DATE NOT NULL,
    status VARCHAR(50),
    injury_type VARCHAR(100),
    expected_return DATE,
    confidence DECIMAL(3,2),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    -- one report per player per day (the loader upserts on it)
    CONSTRAINT uq_injury_report_player_date UNIQUE (player_id, report_date)
);

CREATE INDEX idx_injury_player ON injury_report(player_id);
CREATE INDEX idx_injury_date ON injury_report(report_date);
CREATE INDEX idx_injury_status ON injury_report(status);
