-- Migration 014: Programmatic tables migration
-- Ensures tables that were previously only created by db_locking.py init methods
-- are also available via run_migrations.py for fresh installations or environments
-- where the OCR engine hasn't run yet.
-- 1. OCR file locks (used for cross-worker coordination)
CREATE TABLE IF NOT EXISTS public.ocr_locks (
    file_name TEXT PRIMARY KEY,
    worker_profile TEXT,
    locked_at TIMESTAMP DEFAULT NOW ()
);

CREATE INDEX IF NOT EXISTS idx_locks_time ON public.ocr_locks (locked_at);

COMMENT ON TABLE public.ocr_locks IS 'File-level locks to prevent duplicate OCR processing across workers';

-- 2. Token usage tracking (per-scan Gemini token consumption)
CREATE TABLE IF NOT EXISTS public.ocr_token_usage (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT NOW (),
    batch_id TEXT,
    file_name TEXT,
    source_path TEXT,
    page_no INT,
    browser_profile TEXT,
    browser_id TEXT,
    model_label TEXT,
    tok_in INT,
    tok_out INT,
    tok_total INT,
    chars_in INT,
    chars_out INT,
    ocr_duration_sec NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_token_usage_created_at ON public.ocr_token_usage (created_at);

CREATE INDEX IF NOT EXISTS idx_token_usage_profile ON public.ocr_token_usage (browser_profile);

COMMENT ON TABLE public.ocr_token_usage IS 'Per-scan token usage tracking for Gemini API consumption monitoring';

-- 3. Critical events (requires-attention alerts from OCR engine)
CREATE TABLE IF NOT EXISTS public.critical_events (
    id BIGSERIAL PRIMARY KEY,
    profile_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT,
    requires_action BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW (),
    resolved_at TIMESTAMPTZ,
    meta JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_critical_events_profile ON public.critical_events (profile_name);

CREATE INDEX IF NOT EXISTS idx_critical_events_unresolved ON public.critical_events (resolved_at)
WHERE
    resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_critical_events_created ON public.critical_events (created_at);

COMMENT ON TABLE public.critical_events IS 'Critical events requiring attention: session_expired, ui_change, captcha, etc.';