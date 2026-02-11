-- Migration script for farm health monitoring
-- Tracks farm health status with detailed metrics
-- Version 1.0

-- Main health check table
CREATE TABLE IF NOT EXISTS farm_health_checks (
    id SERIAL PRIMARY KEY,

    -- Check timing
    check_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Overall health status
    is_healthy BOOLEAN NOT NULL,

    -- Process metrics
    farm_processes_count INTEGER DEFAULT 0,
    active_profiles JSONB DEFAULT '[]',

    -- Web API metrics
    web_api_responsive BOOLEAN DEFAULT FALSE,
    web_api_response_time_ms INTEGER,
    web_api_error TEXT,

    -- System metrics
    system_load JSONB DEFAULT '{}',

    -- Error tracking
    error_details TEXT,

    -- Additional metadata
    metadata JSONB DEFAULT '{}',

    -- Record creation
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_health_timestamp ON farm_health_checks(check_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_health_status ON farm_health_checks(is_healthy);
CREATE INDEX IF NOT EXISTS idx_health_timestamp_status ON farm_health_checks(check_timestamp DESC, is_healthy);

-- View for recent health checks (last 100)
CREATE OR REPLACE VIEW v_recent_health_checks AS
SELECT 
    id,
    check_timestamp,
    is_healthy,
    farm_processes_count,
    active_profiles,
    web_api_responsive,
    web_api_response_time_ms,
    error_details
FROM farm_health_checks
ORDER BY check_timestamp DESC
LIMIT 100;

-- View for health summary statistics
CREATE OR REPLACE VIEW v_farm_health_summary AS
SELECT 
    COUNT(*) as total_checks,
    COUNT(*) FILTER (WHERE is_healthy = true) as healthy_checks,
    COUNT(*) FILTER (WHERE is_healthy = false) as unhealthy_checks,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_healthy = true) / NULLIF(COUNT(*), 0), 2) as uptime_percentage,
    AVG(farm_processes_count) FILTER (WHERE is_healthy = true)::INTEGER as avg_processes_when_healthy,
    AVG(web_api_response_time_ms) FILTER (WHERE web_api_responsive = true)::INTEGER as avg_api_response_ms,
    MAX(check_timestamp) as last_check,
    MAX(check_timestamp) FILTER (WHERE is_healthy = true) as last_healthy_check,
    MAX(check_timestamp) FILTER (WHERE is_healthy = false) as last_unhealthy_check
FROM farm_health_checks;

-- View for hourly health statistics (last 7 days)
CREATE OR REPLACE VIEW v_farm_health_hourly AS
SELECT 
    DATE_TRUNC('hour', check_timestamp) as hour,
    COUNT(*) as checks_count,
    COUNT(*) FILTER (WHERE is_healthy = true) as healthy_count,
    COUNT(*) FILTER (WHERE is_healthy = false) as unhealthy_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_healthy = true) / NULLIF(COUNT(*), 0), 2) as uptime_percentage,
    AVG(farm_processes_count)::INTEGER as avg_processes,
    AVG(web_api_response_time_ms) FILTER (WHERE web_api_responsive = true)::INTEGER as avg_api_response_ms
FROM farm_health_checks
WHERE check_timestamp > NOW() - INTERVAL '7 days'
GROUP BY DATE_TRUNC('hour', check_timestamp)
ORDER BY hour DESC;

-- View for daily health statistics
CREATE OR REPLACE VIEW v_farm_health_daily AS
SELECT 
    DATE(check_timestamp) as date,
    COUNT(*) as checks_count,
    COUNT(*) FILTER (WHERE is_healthy = true) as healthy_count,
    COUNT(*) FILTER (WHERE is_healthy = false) as unhealthy_count,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_healthy = true) / NULLIF(COUNT(*), 0), 2) as uptime_percentage,
    AVG(farm_processes_count)::INTEGER as avg_processes,
    MAX(farm_processes_count) as max_processes,
    MIN(farm_processes_count) FILTER (WHERE farm_processes_count > 0) as min_processes,
    AVG(web_api_response_time_ms) FILTER (WHERE web_api_responsive = true)::INTEGER as avg_api_response_ms
FROM farm_health_checks
GROUP BY DATE(check_timestamp)
ORDER BY date DESC;

-- View for detecting downtime periods
CREATE OR REPLACE VIEW v_farm_downtime_periods AS
WITH health_changes AS (
    SELECT 
        id,
        check_timestamp,
        is_healthy,
        LAG(is_healthy) OVER (ORDER BY check_timestamp) as prev_healthy,
        LEAD(is_healthy) OVER (ORDER BY check_timestamp) as next_healthy
    FROM farm_health_checks
    ORDER BY check_timestamp
),

downtime_starts AS (
    SELECT 
        check_timestamp as downtime_start,
        LEAD(check_timestamp) OVER (ORDER BY check_timestamp) as downtime_end
    FROM health_changes
    WHERE is_healthy = false AND (prev_healthy = true OR prev_healthy IS NULL)
)
SELECT 
    downtime_start,
    downtime_end,
    EXTRACT(EPOCH FROM (downtime_end - downtime_start))::INTEGER as downtime_seconds,
    CASE 
        WHEN downtime_end IS NULL THEN 'ONGOING'
        ELSE 'RESOLVED'
    END as status
FROM downtime_starts
WHERE downtime_start > NOW() - INTERVAL '30 days'
ORDER BY downtime_start DESC;

-- Comments
COMMENT ON TABLE farm_health_checks IS 'Periodic health checks of the OCR farm to track uptime and detect issues';
COMMENT ON COLUMN farm_health_checks.is_healthy IS 'TRUE if farm is running and responsive, FALSE otherwise';
COMMENT ON COLUMN farm_health_checks.farm_processes_count IS 'Number of active run.py processes detected';
COMMENT ON COLUMN farm_health_checks.active_profiles IS 'JSON array of active profile names detected from processes';
COMMENT ON COLUMN farm_health_checks.web_api_responsive IS 'TRUE if web dashboard API responded successfully';
COMMENT ON COLUMN farm_health_checks.web_api_response_time_ms IS 'Response time of web dashboard API in milliseconds';
COMMENT ON COLUMN farm_health_checks.system_load IS 'JSON object with system metrics: cpu_percent, memory_percent, disk_percent';
COMMENT ON COLUMN farm_health_checks.error_details IS 'Detailed error message if health check failed';
