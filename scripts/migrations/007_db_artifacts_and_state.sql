-- Migration 007: Artifacts and Profile State
-- Adds table for binary debugging artifacts (traces, screenshots)
-- Adds table for centralized profile state management (replacing JSON files)

-- Table for large binary objects (error screenshots, playwright traces)
CREATE TABLE IF NOT EXISTS ocr_debug_artifacts (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT,
    file_name TEXT,
    profile_name TEXT NOT NULL,
    artifact_type VARCHAR(32) NOT NULL, -- 'trace_zip', 'screenshot_png', 'html_dump'
    content BYTEA,                      -- Binary content
    created_at TIMESTAMPTZ DEFAULT NOW(),
    meta JSONB DEFAULT '{}'             -- Additional metadata (e.g. error reason)
);

-- Indexes for efficient cleanup and lookup
CREATE INDEX IF NOT EXISTS idx_artifacts_created_at ON ocr_debug_artifacts(created_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_batch_file ON ocr_debug_artifacts(batch_id, file_name);
CREATE INDEX IF NOT EXISTS idx_artifacts_profile ON ocr_debug_artifacts(profile_name);


-- Table for profile runtime state (replaces pro_pause_until_*.json files)
CREATE TABLE IF NOT EXISTS profile_runtime_state (
    profile_name TEXT PRIMARY KEY,
    is_paused BOOLEAN DEFAULT FALSE,
    pause_until TIMESTAMPTZ,
    pause_reason TEXT,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    active_worker_pid INTEGER,
    current_action TEXT,

    -- Additional metadata from handler (source, run_id, etc.)
    meta JSONB DEFAULT '{}'
);

-- Index for finding paused profiles
CREATE INDEX IF NOT EXISTS idx_profile_state_paused ON profile_runtime_state(is_paused) WHERE is_paused = TRUE;


-- Comment for documentation
COMMENT ON TABLE ocr_debug_artifacts IS 
    'Stores binary artifacts like screenshots and Playwright traces for debugging.';

COMMENT ON TABLE profile_runtime_state IS 
    'Tracks runtime state of OCR profiles, replacing file-based JSON pause files.';
