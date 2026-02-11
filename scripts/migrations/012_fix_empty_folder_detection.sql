-- Fix empty folder detection bug by decoupling scan queue from stats view
-- and using FULL OUTER JOIN in stats view to include unstarted folders

-- 1. Fix v_source_path_scan_queue
-- Remove dependency on v_source_path_stats which caused folders without OCR records to be hidden
CREATE OR REPLACE VIEW v_source_path_scan_queue AS
SELECT f.source_path,
       f.file_name,
       f.full_path,
       f.mtime_epoch
FROM folder_file_entries f
WHERE f.file_name NOT IN ('Thumbs.db', '.DS_Store')
  AND NOT EXISTS (
    SELECT 1 FROM ocr_raw_texts o
    WHERE o.source_path = f.source_path 
      AND o.file_name = f.file_name
  )
ORDER BY f.source_path, f.file_name;

-- 2. Fix v_source_path_stats
-- Use FULL OUTER JOIN to ensure folders with files but no OCR records are included in stats
-- This fixes the "remaining_to_ocr" count for unstarted folders
CREATE OR REPLACE VIEW v_source_path_stats AS
SELECT COALESCE(t.source_path, f.source_path) AS source_path,
       COALESCE(t.cnt, 0) AS records_in_db,
       COALESCE(f.file_count, 0) AS files_on_disk,
       (COALESCE(f.file_count, 0) - COALESCE(t.cnt, 0)) AS remaining_to_ocr
FROM folder_file_counts f
FULL OUTER JOIN (
    SELECT source_path, count(*) AS cnt 
    FROM ocr_raw_texts 
    GROUP BY source_path
) t ON f.source_path = t.source_path
ORDER BY source_path;
