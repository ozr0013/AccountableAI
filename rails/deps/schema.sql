-- Derived from the event log; safe to truncate and rebuild.
CREATE TABLE IF NOT EXISTS event_facts (
    event_id TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    PRIMARY KEY (event_id, fact_key)
);
CREATE TABLE IF NOT EXISTS dependencies (
    item_id  TEXT NOT NULL,          -- invoice id, or an alert/pattern id recorded explicitly
    fact_key TEXT NOT NULL,
    PRIMARY KEY (item_id, fact_key)
);
CREATE INDEX IF NOT EXISTS dependencies_fact_idx ON dependencies (fact_key);
