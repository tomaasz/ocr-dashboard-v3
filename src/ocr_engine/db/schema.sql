-- Minimal Postgres schema for job queue (C1)
-- Scope: schema only (no ops helpers, no cleanup/recovery, no triggers)
-- Queue pattern relies on SELECT ... FOR UPDATE SKIP LOCKED (documented in README)

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'job_state') THEN
    CREATE TYPE job_state AS ENUM ('NEW', 'READY', 'RUNNING', 'DONE', 'FAILED');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'entry_state') THEN
    CREATE TYPE entry_state AS ENUM ('NEW', 'READY', 'RUNNING', 'DONE', 'FAILED');
  END IF;
END$$;

CREATE TABLE IF NOT EXISTS jobs (
  job_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_dir     text NOT NULL,
  state       job_state NOT NULL DEFAULT 'NEW',
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  last_error  text
);

CREATE INDEX IF NOT EXISTS idx_jobs_state_updated
  ON jobs (state, updated_at);

CREATE TABLE IF NOT EXISTS job_entries (
  job_id      uuid NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  entry_id    text NOT NULL,
  state       entry_state NOT NULL DEFAULT 'NEW',
  updated_at  timestamptz NOT NULL DEFAULT now(),
  last_error  text,
  PRIMARY KEY (job_id, entry_id)
);

CREATE INDEX IF NOT EXISTS idx_job_entries_state_updated
  ON job_entries (state, updated_at);

CREATE TABLE IF NOT EXISTS job_runs (
  run_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id      uuid NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  started_at  timestamptz NOT NULL DEFAULT now(),
  ended_at    timestamptz,
  status      text NOT NULL DEFAULT 'RUNNING',
  error       text
);

CREATE INDEX IF NOT EXISTS idx_job_runs_job_started
  ON job_runs (job_id, started_at DESC);
