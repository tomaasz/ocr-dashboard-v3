-- Migration: Add execution_mode column to ocr_raw_texts
-- Date: 2026-01-22
-- Purpose: Track how each OCR job was executed (local vs remote, browser vs worker)

BEGIN;

-- Add execution_mode column
ALTER TABLE ocr_raw_texts 
ADD COLUMN IF NOT EXISTS execution_mode TEXT;

-- Set default value for existing rows
UPDATE ocr_raw_texts 
SET execution_mode = 'local' 
WHERE execution_mode IS NULL;

-- Create index for query performance
CREATE INDEX IF NOT EXISTS ix_ocr_raw_texts_execution_mode 
ON ocr_raw_texts(execution_mode);

-- Add comment
COMMENT ON COLUMN ocr_raw_texts.execution_mode IS 
'Execution mode: local, remote_browser_wsl, remote_worker_wsl, remote_browser_desktop, remote_worker_desktop';

COMMIT;
