-- Migration script for limit_checks history table
-- Run this on the OCR PostgreSQL database
-- Version 2: Extended with detailed timing and status tracking

CREATE TABLE IF NOT EXISTS limit_checks (
    id SERIAL PRIMARY KEY,

    -- Run identification
    run_id VARCHAR(32) NOT NULL,               -- e.g. "20260113_132500"
    check_id UUID DEFAULT gen_random_uuid(),   -- unique ID for each check

    -- Profile info
    profile_name VARCHAR(128) NOT NULL,
    profile_path VARCHAR(512),                  -- full path to profile directory
    profile_type VARCHAR(32),                   -- "gemini"

    -- Result
    is_limited BOOLEAN NOT NULL DEFAULT FALSE,
    reset_time TIMESTAMPTZ,                     -- when limit resets (if limited)
    limit_detected_method VARCHAR(64),          -- "banner", "menu_text", "model_forced_fast", "prompt_response"

    -- Model info
    model_initial VARCHAR(64),                  -- model detected before any changes
    model_after_switch VARCHAR(64),             -- model after switch attempt
    model_final VARCHAR(64),                    -- final model at end of check
    model_is_pro BOOLEAN,                       -- was Pro model active at end?
    model_switch_needed BOOLEAN,                -- did we need to switch model?
    model_switch_success BOOLEAN,               -- was switch successful?
    model_switch_attempts INTEGER DEFAULT 0,    -- number of switch attempts

    -- Login/Session status
    session_valid BOOLEAN,                      -- was user logged in?
    login_detected BOOLEAN,                     -- did we detect login screen?
    login_provider VARCHAR(32),                 -- "google", "workspace"
    account_email VARCHAR(256),                 -- detected account email (if visible)

    -- Chat/Page status
    chat_opened BOOLEAN,                        -- did chat page open successfully?
    chat_ready BOOLEAN,                         -- was chat input ready?
    prompt_box_found BOOLEAN,                   -- did we find prompt input?
    prompt_sent BOOLEAN,                        -- did we send test prompt?
    prompt_response_received BOOLEAN,           -- did we get response to prompt?

    -- Status/Error
    status VARCHAR(64),                         -- OK, LIMIT, ERROR, SKIPPED, etc.
    error_message TEXT,
    error_stage VARCHAR(64),                    -- "browser_launch", "navigation", "login", "model_switch", "prompt"

    -- Detailed timings (all in milliseconds)
    check_duration_ms INTEGER,                  -- total check duration
    browser_launch_ms INTEGER,                  -- time to launch browser
    navigation_ms INTEGER,                      -- time to navigate to gemini.google.com
    page_load_ms INTEGER,                       -- time for page to be interactive
    login_check_ms INTEGER,                     -- time to verify login status
    model_detect_ms INTEGER,                    -- time to detect current model
    model_switch_ms INTEGER,                    -- time to switch model (if needed)
    prompt_send_ms INTEGER,                     -- time to send test prompt
    prompt_response_ms INTEGER,                 -- time to receive response
    limit_detect_ms INTEGER,                    -- time to detect limit banner
    screenshot_ms INTEGER,                      -- time to capture screenshot

    -- Worker info
    worker_host VARCHAR(128),                   -- hostname of the worker
    worker_ip VARCHAR(45),                      -- IP address
    worker_type VARCHAR(32),                    -- "local", "remote-wsl", "remote-docker"
    worker_os VARCHAR(64),                      -- OS info
    worker_python_version VARCHAR(32),          -- Python version
    playwright_version VARCHAR(32),             -- Playwright version

    -- Browser info
    browser_headed BOOLEAN DEFAULT FALSE,
    browser_timeout_ms INTEGER,
    browser_user_agent TEXT,
    browser_viewport_width INTEGER,
    browser_viewport_height INTEGER,

    -- Screenshot
    screenshot_path VARCHAR(512),
    screenshot_saved BOOLEAN DEFAULT FALSE,
    screenshot_size_bytes INTEGER,

    -- Source tracking
    source_application VARCHAR(64),             -- "dashboard", "precheck_script", "limit_worker"
    triggered_by VARCHAR(128),                  -- who/what triggered the check

    -- Pause file info
    pause_written BOOLEAN DEFAULT FALSE,
    pause_until TIMESTAMPTZ,
    pause_cleared BOOLEAN DEFAULT FALSE,
    pause_reason VARCHAR(128),

    -- Page content analysis
    page_title VARCHAR(256),                    -- detected page title
    page_language VARCHAR(16),                  -- detected language (pl, en, etc.)
    limit_banner_text TEXT,                     -- raw text of limit banner
    menu_text TEXT,                             -- raw text of model menu

    -- Retry tracking
    retry_count INTEGER DEFAULT 0,              -- number of retries within this check
    total_attempts INTEGER DEFAULT 1,           -- total attempts including retries

    -- Additional metadata (JSON for flexibility)
    metadata JSONB DEFAULT '{}',
    timings_breakdown JSONB DEFAULT '{}',       -- detailed timing breakdown
    raw_body_text_sample TEXT,                  -- first 500 chars of body text

    -- Timestamps
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_limit_checks_profile_name ON limit_checks(profile_name);
CREATE INDEX IF NOT EXISTS idx_limit_checks_checked_at ON limit_checks(checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_limit_checks_run_id ON limit_checks(run_id);
CREATE INDEX IF NOT EXISTS idx_limit_checks_is_limited ON limit_checks(is_limited);
CREATE INDEX IF NOT EXISTS idx_limit_checks_profile_checked ON limit_checks(profile_name, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_limit_checks_status ON limit_checks(status);
CREATE INDEX IF NOT EXISTS idx_limit_checks_session_valid ON limit_checks(session_valid);
CREATE INDEX IF NOT EXISTS idx_limit_checks_model_switch ON limit_checks(model_switch_needed);

-- View for latest check per profile
CREATE OR REPLACE VIEW v_latest_limit_checks AS
SELECT DISTINCT ON (profile_name)
    id,
    profile_name,
    is_limited,
    reset_time,
    model_final as model_detected,
    model_switch_needed,
    session_valid,
    chat_opened,
    status,
    error_message,
    error_stage,
    checked_at,
    check_duration_ms,
    browser_launch_ms,
    navigation_ms,
    prompt_response_ms,
    worker_host,
    pause_until
FROM limit_checks
ORDER BY profile_name, checked_at DESC;

-- View for daily summary
CREATE OR REPLACE VIEW v_limit_checks_daily AS
SELECT 
    DATE(checked_at) as check_date,
    profile_name,
    COUNT(*) as total_checks,
    SUM(CASE WHEN is_limited THEN 1 ELSE 0 END) as limit_count,
    SUM(CASE WHEN NOT is_limited AND error_message IS NULL THEN 1 ELSE 0 END) as ok_count,
    SUM(CASE WHEN error_message IS NOT NULL THEN 1 ELSE 0 END) as error_count,
    SUM(CASE WHEN session_valid = FALSE THEN 1 ELSE 0 END) as login_issues,
    SUM(CASE WHEN model_switch_needed THEN 1 ELSE 0 END) as model_switches,
    MIN(checked_at) as first_check,
    MAX(checked_at) as last_check,
    AVG(check_duration_ms)::INTEGER as avg_duration_ms,
    AVG(browser_launch_ms)::INTEGER as avg_browser_ms,
    AVG(navigation_ms)::INTEGER as avg_nav_ms,
    AVG(prompt_response_ms)::INTEGER as avg_prompt_ms
FROM limit_checks
GROUP BY DATE(checked_at), profile_name
ORDER BY check_date DESC, profile_name;

-- View for performance analysis
CREATE OR REPLACE VIEW v_limit_checks_performance AS
SELECT 
    worker_type,
    browser_headed,
    COUNT(*) as total_checks,
    AVG(check_duration_ms)::INTEGER as avg_total_ms,
    AVG(browser_launch_ms)::INTEGER as avg_browser_ms,
    AVG(navigation_ms)::INTEGER as avg_nav_ms,
    AVG(page_load_ms)::INTEGER as avg_load_ms,
    AVG(model_switch_ms)::INTEGER as avg_switch_ms,
    AVG(prompt_response_ms)::INTEGER as avg_prompt_ms,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY check_duration_ms)::INTEGER as median_total_ms,
    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY check_duration_ms)::INTEGER as p95_total_ms
FROM limit_checks
WHERE checked_at > NOW() - INTERVAL '7 days'
GROUP BY worker_type, browser_headed;

-- Comments
COMMENT ON TABLE limit_checks IS 'Detailed history of all limit checks performed on Gemini profiles';
COMMENT ON COLUMN limit_checks.run_id IS 'Groups checks that were executed together in a single run';
COMMENT ON COLUMN limit_checks.is_limited IS 'TRUE if Pro limit was detected during this check';
COMMENT ON COLUMN limit_checks.model_switch_needed IS 'TRUE if model was on Flash/Fast and we tried to switch to Pro';
COMMENT ON COLUMN limit_checks.session_valid IS 'TRUE if user was properly logged in, FALSE if login required';
COMMENT ON COLUMN limit_checks.chat_opened IS 'TRUE if Gemini chat page loaded successfully';
COMMENT ON COLUMN limit_checks.browser_launch_ms IS 'Time in ms from start to browser context ready';
COMMENT ON COLUMN limit_checks.navigation_ms IS 'Time in ms for page.goto() to complete';
COMMENT ON COLUMN limit_checks.prompt_response_ms IS 'Time in ms from sending test prompt to receiving response';
COMMENT ON COLUMN limit_checks.timings_breakdown IS 'JSON object with detailed timing for each step';
