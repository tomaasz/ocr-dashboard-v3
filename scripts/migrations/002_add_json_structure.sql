-- Migration: Add JSON structure columns for post-processing
-- Target Table: public.ocr_raw_texts

DO $$
BEGIN
    -- 1. Full JSON result from Gemini
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='ocr_raw_texts' AND column_name='processed_json') THEN
        ALTER TABLE public.ocr_raw_texts ADD COLUMN processed_json JSONB;
    END IF;

    -- 2. Extracted high-level classification (shortcut for easy querying)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='ocr_raw_texts' AND column_name='document_type') THEN
        ALTER TABLE public.ocr_raw_texts ADD COLUMN document_type TEXT;
    END IF;

    -- 3. Extracted entries list (shortcut)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='ocr_raw_texts' AND column_name='entries') THEN
        ALTER TABLE public.ocr_raw_texts ADD COLUMN entries JSONB;
    END IF;

    -- 4. Status tracking for post-processing worker
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='ocr_raw_texts' AND column_name='processing_status') THEN
        ALTER TABLE public.ocr_raw_texts ADD COLUMN processing_status TEXT DEFAULT 'NEW';
        CREATE INDEX IF NOT EXISTS idx_processing_status ON public.ocr_raw_texts (processing_status);
    END IF;

    -- 5. Timestamps for processing
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='ocr_raw_texts' AND column_name='processed_at') THEN
        ALTER TABLE public.ocr_raw_texts ADD COLUMN processed_at TIMESTAMP;
    END IF;

END$$;
