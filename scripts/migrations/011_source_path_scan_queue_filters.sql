-- Migration 011: Filter out system files from scan queue

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
  AND f.file_name NOT IN ('Thumbs.db', '.DS_Store')
  AND NOT EXISTS (
      SELECT 1
      FROM public.ocr_raw_texts o
      WHERE o.source_path = f.source_path
        AND o.file_name = f.file_name
  )
ORDER BY f.source_path ASC, f.file_name ASC;
