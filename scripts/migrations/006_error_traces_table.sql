-- Migration 006: Error Traces Table
-- Adds logging for Playwright error traces to enable analytics and monitoring

CREATE TABLE IF NOT EXISTS public.error_traces (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    page_no INTEGER,
    browser_profile TEXT NOT NULL,
    browser_id TEXT,
    worker_id INTEGER,
    error_type TEXT,  -- 'timeout', 'api_error', 'exception', etc.
    error_message TEXT,
    trace_file_path TEXT NOT NULL,  -- Relative path to trace .zip file
    trace_file_size_bytes BIGINT,
    model_label TEXT,
    execution_mode TEXT,
    ocr_duration_sec NUMERIC(10, 3)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_error_traces_batch 
    ON public.error_traces(batch_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_error_traces_file 
    ON public.error_traces(file_name);

CREATE INDEX IF NOT EXISTS idx_error_traces_profile 
    ON public.error_traces(browser_profile, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_error_traces_error_type 
    ON public.error_traces(error_type, created_at DESC);

-- Comment for documentation
COMMENT ON TABLE public.error_traces IS 
    'Logs metadata for Playwright error traces captured during OCR operations. Used for error analytics and monitoring.';
