-- Migration script for system activity logging
-- Tracks all farm and web application lifecycle events
-- Version 1.0

CREATE TABLE IF NOT EXISTS system_activity_log (
    id SERIAL PRIMARY KEY,

    -- Event identification
    event_type VARCHAR(32) NOT NULL,
    component VARCHAR(64) NOT NULL,
    profile_name VARCHAR(128),

    -- Trigger information
    triggered_by VARCHAR(128) NOT NULL,
    trigger_user VARCHAR(64),
    reason TEXT,
    is_automatic BOOLEAN DEFAULT FALSE,

    -- Process information
    process_id INTEGER,
    parent_process_id INTEGER,

    -- System information
    hostname VARCHAR(128),
    ip_address VARCHAR(45),

    -- Configuration (for start events)
    configuration JSONB DEFAULT '{}',

    -- Exit information (for stop events)
    exit_code INTEGER,
    exit_signal VARCHAR(32),
    duration_seconds INTEGER,

    -- Error tracking
    error_message TEXT,

    -- Additional metadata
    metadata JSONB DEFAULT '{}',

    -- Timestamps
    event_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_activity_event_type ON system_activity_log(event_type);
CREATE INDEX IF NOT EXISTS idx_activity_component ON system_activity_log(component);
CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON system_activity_log(event_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_activity_profile ON system_activity_log(profile_name) WHERE profile_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_activity_automatic ON system_activity_log(is_automatic);
CREATE INDEX IF NOT EXISTS idx_activity_component_timestamp ON system_activity_log(component, event_timestamp DESC);

-- View for recent activity (last 100 events)
CREATE OR REPLACE VIEW v_recent_activity AS
SELECT 
    id,
    event_type,
    component,
    profile_name,
    triggered_by,
    trigger_user,
    reason,
    is_automatic,
    process_id,
    hostname,
    exit_code,
    duration_seconds,
    error_message,
    event_timestamp
FROM system_activity_log
ORDER BY event_timestamp DESC
LIMIT 100;

-- View for activity summary per component
CREATE OR REPLACE VIEW v_activity_summary AS
SELECT 
    component,
    COUNT(*) FILTER (WHERE event_type LIKE '%_start') as total_starts,
    COUNT(*) FILTER (WHERE event_type LIKE '%_stop') as total_stops,
    COUNT(*) FILTER (WHERE is_automatic = true) as automatic_restarts,
    COUNT(*) FILTER (WHERE error_message IS NOT NULL) as errors,
    MAX(event_timestamp) FILTER (WHERE event_type LIKE '%_start') as last_start,
    MAX(event_timestamp) FILTER (WHERE event_type LIKE '%_stop') as last_stop,
    AVG(duration_seconds) FILTER (WHERE duration_seconds IS NOT NULL)::INTEGER as avg_duration_seconds,
    MAX(duration_seconds) as max_duration_seconds,
    MIN(duration_seconds) FILTER (WHERE duration_seconds > 0) as min_duration_seconds
FROM system_activity_log
GROUP BY component
ORDER BY last_start DESC NULLS LAST;

-- View for uptime statistics
CREATE OR REPLACE VIEW v_uptime_stats AS
WITH recent_sessions AS (
    SELECT 
        component,
        profile_name,
        event_type,
        event_timestamp,
        duration_seconds,
        LAG(event_type) OVER (PARTITION BY component, profile_name ORDER BY event_timestamp) as prev_event,
        LAG(event_timestamp) OVER (PARTITION BY component, profile_name ORDER BY event_timestamp) as prev_timestamp
    FROM system_activity_log
    WHERE event_timestamp > NOW() - INTERVAL '7 days'
)
SELECT 
    component,
    profile_name,
    COUNT(*) FILTER (WHERE event_type LIKE '%_start') as starts_last_7d,
    COUNT(*) FILTER (WHERE event_type LIKE '%_stop') as stops_last_7d,
    SUM(duration_seconds)::INTEGER as total_uptime_seconds,
    AVG(duration_seconds)::INTEGER as avg_session_seconds,
    MAX(duration_seconds) as longest_session_seconds,
    COUNT(*) FILTER (WHERE prev_event LIKE '%_stop' AND event_type LIKE '%_start' 
                     AND EXTRACT(EPOCH FROM (event_timestamp - prev_timestamp)) < 60) as quick_restarts
FROM recent_sessions
GROUP BY component, profile_name
ORDER BY component, profile_name;

-- View for daily activity
CREATE OR REPLACE VIEW v_activity_daily AS
SELECT 
    DATE(event_timestamp) as activity_date,
    component,
    COUNT(*) FILTER (WHERE event_type LIKE '%_start') as starts,
    COUNT(*) FILTER (WHERE event_type LIKE '%_stop') as stops,
    COUNT(*) FILTER (WHERE is_automatic = true) as auto_restarts,
    COUNT(*) FILTER (WHERE error_message IS NOT NULL) as errors,
    SUM(duration_seconds)::INTEGER as total_uptime_seconds,
    COUNT(DISTINCT profile_name) FILTER (WHERE profile_name IS NOT NULL) as unique_profiles
FROM system_activity_log
GROUP BY DATE(event_timestamp), component
ORDER BY activity_date DESC, component;

-- Comments
COMMENT ON TABLE system_activity_log IS 'Comprehensive log of all farm and web application lifecycle events';
COMMENT ON COLUMN system_activity_log.event_type IS 'Type of event: farm_start, farm_stop, web_start, web_stop, worker_start, worker_stop, auto_restart';
COMMENT ON COLUMN system_activity_log.component IS 'Component that generated the event: farm, web_dashboard, limit_worker, profile_worker';
COMMENT ON COLUMN system_activity_log.triggered_by IS 'What triggered the event: user, system, auto_restart, api, script';
COMMENT ON COLUMN system_activity_log.is_automatic IS 'TRUE if this was an automatic restart, FALSE for manual actions';
COMMENT ON COLUMN system_activity_log.configuration IS 'JSON object with environment variables and parameters used at start';
COMMENT ON COLUMN system_activity_log.duration_seconds IS 'How long the component was running (calculated on stop events)';
COMMENT ON COLUMN system_activity_log.metadata IS 'Flexible JSON field for additional context-specific data';
