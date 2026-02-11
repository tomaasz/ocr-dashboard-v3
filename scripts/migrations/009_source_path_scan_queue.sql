-- Migration 009: Source path scan queue support
-- Adds folder_file_entries and v_source_path_scan_queue for missing OCR files

CREATE TABLE IF NOT EXISTS public.folder_file_entries (
    source_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    full_path TEXT NOT NULL,
    mtime_epoch DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (source_path, file_name)
);

CREATE INDEX IF NOT EXISTS idx_folder_file_entries_source_path
    ON public.folder_file_entries (source_path);

CREATE OR REPLACE VIEW public.v_source_path_scan_queue AS
SELECT
    f.source_path,
    f.file_name,
    f.full_path,
    f.mtime_epoch
FROM public.folder_file_entries f
JOIN public.v_source_path_stats v
    ON v.source_path = f.source_path
WHERE v.remaining_to_ocr > 0
  AND NOT EXISTS (
      SELECT 1
      FROM public.ocr_raw_texts o
      WHERE o.source_path = f.source_path
        AND o.file_name = f.file_name
  )
ORDER BY f.source_path ASC, f.file_name ASC;
