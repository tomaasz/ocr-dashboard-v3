-- queries.sql (C2)
-- Canonical SQL snippets for queue operations.
-- Intended to be used by pipeline worker with explicit transactions.

-- 1) Enqueue job: create job + optional entries (e1..eN)
-- NOTE: entries are optional at enqueue time; can be added later.
-- Params:
--   :job_dir (text)
--   :entry_ids (text[]) optional, e.g. ARRAY['e1','e2']
WITH j AS (
  INSERT INTO jobs (job_dir, state)
  VALUES (:job_dir, 'READY')
  RETURNING job_id
),
ins_entries AS (
  INSERT INTO job_entries (job_id, entry_id, state)
  SELECT j.job_id, e.entry_id, 'READY'
  FROM j
  JOIN LATERAL (
    SELECT unnest(COALESCE(:entry_ids, ARRAY[]::text[])) AS entry_id
  ) e ON true
  RETURNING 1
)
SELECT job_id FROM j;

-- 2) Fetch next READY job (claim): READY -> RUNNING (SKIP LOCKED)
-- Returns 0 rows if none available.
-- Must run inside a transaction.
WITH picked AS (
  SELECT job_id
  FROM jobs
  WHERE state = 'READY'
  ORDER BY updated_at ASC
  FOR UPDATE SKIP LOCKED
  LIMIT 1
)
UPDATE jobs
SET state = 'RUNNING',
    updated_at = now()
WHERE job_id IN (SELECT job_id FROM picked)
RETURNING *;

-- 3) Mark job DONE
-- Params:
--   :job_id (uuid)
UPDATE jobs
SET state = 'DONE',
    updated_at = now(),
    last_error = NULL
WHERE job_id = :job_id
RETURNING *;

-- 4) Mark job FAILED
-- Params:
--   :job_id (uuid)
--   :last_error (text)
UPDATE jobs
SET state = 'FAILED',
    updated_at = now(),
    last_error = :last_error
WHERE job_id = :job_id
RETURNING *;
