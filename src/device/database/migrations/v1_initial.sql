-- 多通道温度分析仪软件数据库初始化脚本
-- 版本: v1.0
-- 日期: 2026-08-10
-- 说明: 创建历史数据存储所需的表结构和索引

-- ─────────────────────────────────────────────────────
-- 会话表 (sessions)
-- ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,          -- 会话 ID（UUID）
    session_name TEXT,                    -- 会话名称（用户可编辑）
    started_at REAL NOT NULL,             -- 开始时间（Unix 时间戳）
    stopped_at REAL,                      -- 停止时间（NULL 表示采集中）
    duration_seconds REAL,                -- 持续时间（秒）
    channel_count INTEGER NOT NULL,       -- 通道数量
    interval_seconds REAL NOT NULL,       -- 采集间隔（秒）
    source_type TEXT NOT NULL,            -- 来源类型：'live' | 'file'
    source_path TEXT,                     -- 原始文件路径
    notes TEXT,                           -- 用户备注
    tags TEXT,                            -- 标签（JSON 数组）
    created_at REAL DEFAULT (strftime('%s', 'now')),
    updated_at REAL DEFAULT (strftime('%s', 'now')),
    locked INTEGER NOT NULL DEFAULT 0     -- 锁定白名单（30 天保留策略跳过，1=锁定）
);

-- 会话表索引（按开始时间倒序查询）
CREATE INDEX IF NOT EXISTS idx_sessions_time ON sessions(started_at DESC);

-- ─────────────────────────────────────────────────────
-- 通道配置表 (channels)
-- ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS channels (
    channel_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,            -- CH1, CH2, ..., CH64
    channel_name TEXT,                    -- 显示名称
    color TEXT,                           -- 颜色（#RRGGBB）
    physical_name TEXT,                   -- 物理通道名
    unit TEXT DEFAULT '°C',               -- 单位
    visible INTEGER DEFAULT 1,            -- 是否显示
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

-- 通道表索引
CREATE INDEX IF NOT EXISTS idx_channels_session ON channels(session_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_unique ON channels(session_id, channel_key);

-- ─────────────────────────────────────────────────────
-- 温度数据表 (temperature_readings)
-- ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS temperature_readings (
    reading_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    timestamp REAL NOT NULL,              -- 时间戳（Unix 时间戳）
    temperature REAL,                     -- 温度值（NaN 存储为 NULL）
    is_valid INTEGER DEFAULT 1,           -- 是否有效（处理开路、溢出等）
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

-- 温度数据表索引（支持按会话和时间查询，支持按会话和通道查询）
CREATE INDEX IF NOT EXISTS idx_readings_session_time
    ON temperature_readings(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_readings_channel
    ON temperature_readings(session_id, channel_key);

-- ─────────────────────────────────────────────────────
-- 设备连接历史表 (device_connections)
-- ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS device_connections (
    connection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    com_port TEXT,
    device_type TEXT,
    baud_rate INTEGER,
    connected_at REAL,
    disconnected_at REAL,
    connection_status TEXT,                -- 'success' | 'timeout' | 'error'
    error_message TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
);

-- 设备连接历史索引（按连接时间倒序）
CREATE INDEX IF NOT EXISTS idx_connections_time ON device_connections(connected_at DESC);

-- ─────────────────────────────────────────────────────
-- 导出记录表 (export_history)
-- ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS export_history (
    export_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    export_type TEXT,                     -- 'excel' | 'html' | 'png'
    export_path TEXT,
    exported_at REAL DEFAULT (strftime('%s', 'now')),
    file_size_bytes INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE SET NULL
);

-- ─────────────────────────────────────────────────────
-- 通道统计信息表 (channel_stats)
-- ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS channel_stats (
    stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    channel_key TEXT NOT NULL,
    min_temp REAL,
    max_temp REAL,
    avg_temp REAL,
    std_dev REAL,
    valid_count INTEGER,
    invalid_count INTEGER,
    rise_rate REAL,                       -- 温升速率（°C/s）
    rise_time_seconds REAL,               -- 温升时间（秒）
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);

-- 统计信息表索引
CREATE UNIQUE INDEX IF NOT EXISTS idx_stats_unique ON channel_stats(session_id, channel_key);

-- ─────────────────────────────────────────────────────
-- 报警事件表 (alarm_events)
-- ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alarm_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    channel_key TEXT,                        -- '__diff__' 表示通道间温差
    timestamp REAL NOT NULL,                 -- 报警时刻 Unix 时间戳
    alarm_type TEXT NOT NULL,                -- 'high'/'low'/'rate'/'diff'
    threshold REAL,
    actual_value REAL,
    status TEXT DEFAULT 'active'             -- 'active'/'cleared'
);

CREATE INDEX IF NOT EXISTS idx_alarm_session_time ON alarm_events(session_id, timestamp);