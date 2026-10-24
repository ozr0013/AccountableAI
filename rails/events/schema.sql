CREATE TABLE IF NOT EXISTS events (
    seq          BIGSERIAL PRIMARY KEY,
    id           TEXT UNIQUE NOT NULL,
    type         TEXT NOT NULL,
    manager_id   TEXT,
    invoice_id   TEXT,
    valid_from   TIMESTAMPTZ NOT NULL,
    learned_at   TIMESTAMPTZ NOT NULL,
    supersedes   TEXT REFERENCES events(id),
    payload      JSONB NOT NULL DEFAULT '{}',
    source       JSONB,
    content_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_manager_idx ON events (manager_id);
CREATE INDEX IF NOT EXISTS events_invoice_idx ON events (invoice_id);
CREATE INDEX IF NOT EXISTS events_supersedes_idx ON events (supersedes);

CREATE OR REPLACE FUNCTION events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'events is append-only (% not allowed)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_no_change ON events;
CREATE TRIGGER events_no_change BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_append_only();

DROP TRIGGER IF EXISTS events_no_truncate ON events;
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION events_append_only();
