-- Migration 013: Base ocr_raw_texts table
-- Ensures the core OCR results table exists for fresh V3 installations.
-- Previously this table was created manually or inherited from earlier versions.

CREATE TABLE IF NOT EXISTS public.ocr_raw_texts (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_id TEXT,
    file_name TEXT,
    source_path TEXT,
    page_no INTEGER,
    raw_text TEXT,
    card_id TEXT,
    browser_id TEXT,
    ocr_duration_sec NUMERIC(10, 3),
    start_ts TIMESTAMPTZ,
    end_ts TIMESTAMPTZ,
    browser_profile TEXT,
    model_label TEXT,
    execution_mode TEXT,

    -- Columns from migration 002 (included here for fresh installs)
    processed_json JSONB,
    document_type TEXT,
    entries JSONB,
    processing_status TEXT DEFAULT 'NEW',
    processed_at TIMESTAMP
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_ocr_raw_texts_source_path ON public.ocr_raw_texts(source_path);
CREATE INDEX IF NOT EXISTS idx_ocr_raw_texts_file_name ON public.ocr_raw_texts(file_name);
CREATE INDEX IF NOT EXISTS idx_ocr_raw_texts_batch_id ON public.ocr_raw_texts(batch_id);
CREATE INDEX IF NOT EXISTS idx_ocr_raw_texts_created_at ON public.ocr_raw_texts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ocr_raw_texts_browser_profile ON public.ocr_raw_texts(browser_profile);
CREATE INDEX IF NOT EXISTS idx_processing_status ON public.ocr_raw_texts(processing_status);
CREATE INDEX IF NOT EXISTS ix_ocr_raw_texts_execution_mode ON public.ocr_raw_texts(execution_mode);

COMMENT ON TABLE public.ocr_raw_texts IS 'Core OCR results table storing raw text extracted from scanned images';
