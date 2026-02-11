-- Create normalized tables for OCR post-processing
-- Only applies if ocr_raw_texts has an integer id column.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'ocr_raw_texts'
          AND column_name = 'id'
    ) THEN
        -- Table for document-level metadata (mapped from 'card' object and linked to raw text)
        CREATE TABLE IF NOT EXISTS public.ocr_documents (
            id SERIAL PRIMARY KEY,
            raw_text_id INTEGER REFERENCES public.ocr_raw_texts(id) ON DELETE CASCADE,
            document_type TEXT,
            language TEXT,
            confidence FLOAT,
            issues TEXT[],
            processing_status TEXT DEFAULT 'NEW',
            processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_ocr_documents_raw_text_id ON public.ocr_documents(raw_text_id);

        -- Table for individual entries extracted from the document
        CREATE TABLE IF NOT EXISTS public.ocr_entries (
            id SERIAL PRIMARY KEY,
            document_id INTEGER REFERENCES public.ocr_documents(id) ON DELETE CASCADE,
            entry_seq INTEGER,
            entry_id TEXT,
            entry_type_guess TEXT,
            content_original TEXT,
            content_translation TEXT,
            people_data JSONB,
            places_data JSONB,
            date_str TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_ocr_entries_document_id ON public.ocr_entries(document_id);
    END IF;
END$$;
