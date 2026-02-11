-- Migration 008: Source path stats support
-- Adds folder_file_counts and v_source_path_stats used by dashboard and workers

CREATE TABLE IF NOT EXISTS public.folder_file_counts (
    source_path TEXT PRIMARY KEY,
    file_count INTEGER NOT NULL DEFAULT 0,
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE VIEW public.v_source_path_stats AS
SELECT
    o.source_path,
    COUNT(*) AS records_in_db,
    COALESCE(f.file_count, 0) AS files_on_disk,
    GREATEST(COALESCE(f.file_count, 0) - COUNT(*), 0) AS remaining_to_ocr
FROM public.ocr_raw_texts o
LEFT JOIN public.folder_file_counts f
    ON o.source_path = f.source_path
GROUP BY o.source_path, f.file_count;
