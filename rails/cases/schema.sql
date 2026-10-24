CREATE TABLE IF NOT EXISTS agent_cases (
 id TEXT PRIMARY KEY,
 owner_id TEXT NOT NULL,
 manager_id TEXT NOT NULL,
 vendor_id TEXT NOT NULL,
 invoice_id TEXT NOT NULL,
 version INTEGER NOT NULL,
 body JSONB NOT NULL,
 UNIQUE(owner_id,manager_id,vendor_id,invoice_id)
);
CREATE TABLE IF NOT EXISTS scheduled_jobs (
 id TEXT PRIMARY KEY,
 case_id TEXT NOT NULL REFERENCES agent_cases(id),
 schedule_key TEXT NOT NULL,
 due_at TIMESTAMPTZ NOT NULL,
 payload JSONB NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT,
 lease_until TIMESTAMPTZ,
 last_error TEXT,
 UNIQUE(case_id,schedule_key)
);
CREATE INDEX IF NOT EXISTS scheduled_jobs_due ON scheduled_jobs(status,due_at);
