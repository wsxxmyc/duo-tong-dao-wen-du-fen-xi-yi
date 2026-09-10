"""
历史数据库管理器

提供温度历史数据的统一访问接口，支持 WAL 模式和并发读写。
"""

import sqlite3
import os
import sys
import csv
import threading
import functools
from typing import Optional, List, Dict
import time

from .models import Session, Channel, TemperatureReading, ChannelStats
from .dao import SessionDAO, ChannelDAO, ReadingDAO


def default_db_path() -> str:
    """默认数据库文件路径：<软件根>/用户数据/data/temperature_history.db。

    绿色版所有用户数据统一存软件根目录（见 utils.helpers._resolve_data_dir）。
    供 HistoryDatabase 与 HistoryDialog 共用，避免路径逻辑分裂。
    """
    from utils.helpers import _resolve_data_dir
    return os.path.join(_resolve_data_dir(), "temperature_history.db")


# 保留策略：历史会话硬编码保留最近 30 天（用户决策：不做设置项）。
# 启动时后台执行 purge_expired_sessions，过期未锁定会话自动删除并回收磁盘。
RETENTION_DAYS = 30


def _synchronized(method):
    """保证数据库方法在连接锁下执行。

    网络服务器为每个客户端连接创建独立线程，多个线程共享同一个
    sqlite3 连接（check_same_thread=False）并发查询时会出现游标
    状态竞态、偶发返回空结果。通过 RLock 将每个数据库操作串行化。
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class HistoryDatabase:
    """
    历史数据库管理器

    提供温度历史数据的存储、查询和导出功能。
    使用 SQLite WAL 模式支持并发读写。
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        初始化数据库连接

        参数:
            db_path: 数据库文件路径，如果为 None 则使用默认路径
        """
        # 确定数据库文件路径
        if db_path is None:
            db_path = self._get_default_db_path()

        # 确保目录存在
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self.db_path = db_path
        self._lock = threading.RLock()
        self.conn = None
        self.session_dao = None
        self.channel_dao = None
        self.reading_dao = None

        # 初始化数据库
        self._connect()
        self._enable_wal_mode()
        self._create_tables()
        self._migrate_lock_column()
        self._init_daos()

    def _get_default_db_path(self) -> str:
        """默认数据库路径（委托给模块级 default_db_path，单一数据源）。"""
        return default_db_path()

    def _connect(self):
        """建立数据库连接"""
        try:
            # 设置超时和检测类型
            # check_same_thread=False 允许多线程访问（WAL 模式下安全）
            self.conn = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
                check_same_thread=False
            )
            # 启用外键约束
            self.conn.execute('PRAGMA foreign_keys = ON')
        except sqlite3.Error as e:
            raise RuntimeError(f"无法连接数据库: {e}")

    def _enable_wal_mode(self):
        """
        启用 WAL 模式

        WAL 模式优势：
        - 读写并发，读操作不会被写操作阻塞
        - 性能提升，减少磁盘 IO
        - 适合批量写入场景
        """
        try:
            # 启用 WAL 模式
            self.conn.execute('PRAGMA journal_mode=WAL')
            # 降低同步频率（NORMAL 比 FULL 快，但安全性略低）
            self.conn.execute('PRAGMA synchronous=NORMAL')
            # 增大缓存（10000 页，约 40MB）
            self.conn.execute('PRAGMA cache_size=10000')
            # 临时表存内存
            self.conn.execute('PRAGMA temp_store=MEMORY')
            # 锁定模式（NORMAL 允许多进程访问）
            self.conn.execute('PRAGMA locking_mode=NORMAL')
        except sqlite3.Error as e:
            print(f"警告: 启用 WAL 模式失败: {e}")

    def _migrations_dir(self) -> str:
        """定位数据库迁移 SQL 目录（兼容 PyInstaller 打包环境）。

        打包后模块位于 base_library.zip 内，os.path.dirname(__file__)
        无法定位磁盘上的数据文件，需改用 sys._MEIPASS。
        """
        candidates = []
        frozen_root = getattr(sys, "_MEIPASS", None)
        if frozen_root:
            candidates.append(os.path.join(frozen_root, "device", "database", "migrations"))
        candidates.append(os.path.join(os.path.dirname(__file__), "migrations"))
        for c in candidates:
            if os.path.isdir(c):
                return c
        return candidates[0]

    def _create_tables(self):
        """创建表结构（如果不存在）"""
        try:
            # 读取初始化 SQL 脚本
            sql_path = os.path.join(
                self._migrations_dir(),
                'v1_initial.sql'
            )

            if not os.path.exists(sql_path):
                raise FileNotFoundError(f"数据库初始化脚本不存在: {sql_path}")

            with open(sql_path, 'r', encoding='utf-8') as f:
                sql_script = f.read()

            # 执行 SQL 脚本
            self.conn.executescript(sql_script)
            self.conn.commit()
        except Exception as e:
            raise RuntimeError(f"创建表结构失败: {e}")

    def _migrate_lock_column(self):
        """v2 迁移：旧库 sessions 表补 locked 列（锁定白名单）。

        新库由 v1_initial.sql 直接建列；此处幂等（先 PRAGMA 检查再
        ALTER），兼容 30 天保留策略上线前的历史数据库文件。
        """
        try:
            cur = self.conn.execute('PRAGMA table_info(sessions)')
            cols = {row[1] for row in cur.fetchall()}
            if 'locked' not in cols:
                self.conn.execute(
                    'ALTER TABLE sessions '
                    'ADD COLUMN locked INTEGER NOT NULL DEFAULT 0')
                self.conn.commit()
        except sqlite3.Error as e:
            raise RuntimeError(f"迁移 sessions.locked 列失败: {e}")

    def _init_daos(self):
        """初始化 DAO 对象"""
        self.session_dao = SessionDAO(self.conn)
        self.channel_dao = ChannelDAO(self.conn)
        self.reading_dao = ReadingDAO(self.conn)

    # ─────────────────────────────────────────────────────
    # 会话管理
    # ─────────────────────────────────────────────────────

    @_synchronized
    def create_session(self, session_id: str, session_name: str,
                      started_at: float, channel_count: int,
                      interval: float, source_type: str = 'live',
                      source_path: Optional[str] = None) -> bool:
        """
        创建新的测量会话记录

        参数:
            session_id: 会话 ID（UUID）
            session_name: 会话名称
            started_at: 开始时间戳
            channel_count: 通道数量
            interval: 采集间隔（秒）
            source_type: 来源类型（'live' 或 'file'）
            source_path: 原始文件路径

        返回:
            bool: 是否成功
        """
        session = Session(
            session_id=session_id,
            session_name=session_name,
            started_at=started_at,
            channel_count=channel_count,
            interval_seconds=interval,
            source_type=source_type,
            source_path=source_path
        )
        return self.session_dao.create(session)

    @_synchronized
    def update_session_stop(self, session_id: str, stopped_at: float) -> bool:
        """
        更新会话停止时间

        参数:
            session_id: 会话 ID
            stopped_at: 停止时间戳

        返回:
            bool: 是否成功
        """
        return self.session_dao.update_stop_time(session_id, stopped_at)

    @_synchronized
    def end_session(self, session_id: str, stopped_at: Optional[float] = None) -> bool:
        """结束一个仍处于「采集中」状态的会话。

        结束时间优先取温度数据的最后时间戳（真实停止时刻），
        无数据时使用传入值或当前时间。

        参数:
            session_id: 会话 ID
            stopped_at: 指定的结束时间戳（可选）

        返回:
            bool: 是否成功
        """
        if stopped_at is None:
            stopped_at = self.get_latest_timestamp(session_id) or time.time()
        return self.update_session_stop(session_id, stopped_at)

    @_synchronized
    def rename_session(self, session_id: str, new_name: str) -> bool:
        """重命名会话（仅修改名称，时间戳不可修改）。

        参数:
            session_id: 会话 ID
            new_name: 新的会话名称（非空）

        返回:
            bool: 是否成功
        """
        new_name = (new_name or "").strip()
        if not new_name:
            return False
        return self.session_dao.update_name(session_id, new_name)

    @_synchronized
    def set_session_locked(self, session_id: str, locked: bool) -> bool:
        """设置会话锁定状态（锁定 = 白名单，30 天保留清理跳过）。

        参数:
            session_id: 会话 ID
            locked: True 锁定 / False 解锁

        返回:
            bool: 是否成功
        """
        return self.session_dao.set_locked(session_id, locked)

    @_synchronized
    def import_remote_session(self, session_id: str, session_name: str,
                              started_at: float, channel_count: int,
                              interval: float, source_path: str,
                              channels: List[Dict], readings: List[Dict],
                              stopped_at: Optional[float] = None,
                              source_type: str = 'remote') -> str:
        """把一次远端会话自动保存到本机历史数据库（统一管理用）。

        以远端 session_id 作为本机主键，重复导入时跳过（不覆盖、不重复）。
        传入 stopped_at（远端会话结束时间）可避免「查看历史」把完整会话
        误判为「异常中断」。

        参数:
            session_id: 远端会话 ID（同时作为本机会话 ID）
            session_name: 会话名称（沿用远端名称）
            started_at: 会话开始时间戳
            channel_count: 通道数量
            interval: 采集间隔（秒）
            source_path: 远程来源路径，形如 remote://<ip>:<port>/<会话ID>
            channels: 通道配置字典列表（channel_key / channel_name / color ...）
            readings: 温度数据字典列表（channel_key / timestamp / temperature / is_valid）
            stopped_at: 远端会话结束时间戳（可选，来自远端会话元数据）
            source_type: 来源类型标记，默认 'remote'；外部库导入传 'import'

        返回:
            str: 'imported'（新导入）/'exists'（已存在，跳过）/'failed'（失败）
        """
        # 已存在则跳过（远端 ID 作为去重键）
        if self.session_dao.get_by_id(session_id) is not None:
            return 'exists'

        try:
            if not self.create_session(
                    session_id, session_name, started_at, channel_count,
                    interval, source_type=source_type,
                    source_path=source_path):
                return 'failed'
            if not self.save_channel_config(session_id, channels):
                return 'failed'
            if not self.insert_readings_batch(session_id, readings):
                return 'failed'
            if stopped_at:
                self.update_session_stop(session_id, stopped_at)
            return 'imported'
        except Exception as e:
            print(f"导入远端会话失败: {e}")
            return 'failed'

    @_synchronized
    def import_external_sessions(self, source_path: str,
                                 progress_cb=None) -> tuple:
        """把外部数据库（A 设备库）的全部会话合并导入本机库（B 设备库）。

        解决「A 设备数据库文件复制到 B 设备文件夹无法识别」：应用只打开
        固定单库，复制文件不识别；本方法按记录逐条合并进本机默认库。

        - 源库以 sqlite3 只读 URI 打开：绝不建表/写入，外部文件零污染。
        - 逐会话按 session_id 去重：本机已存在跳过（不覆盖、不重复）。
        - 记录级复制（会话元数据 + 通道配置 + 读数 + 停止时间），复用
          import_remote_session 的合并原语，source_type 记 'import'。

        参数:
            source_path: 外部 .db 文件路径
            progress_cb: 进度回调 progress_cb(done, total)（可选）

        返回:
            (imported, skipped, failed): 新导入 / 已存在跳过 / 失败计数。

        异常:
            ValueError: 文件不存在、打不开或不是本程序数据库。
        """
        import sqlite3
        if not os.path.exists(source_path):
            raise ValueError(f"文件不存在：{source_path}")
        # 只读 URI 打开；路径中的 ?/# 需转义，避免被当作 URI 参数
        esc = source_path.replace("?", "%3F").replace("#", "%23")
        try:
            src = sqlite3.connect(f"file:{esc}?mode=ro", uri=True)
        except sqlite3.Error as e:
            raise ValueError(f"无法打开外部数据库：{e}")
        try:
            try:
                cur = src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND "
                    "name IN ('sessions','channels','temperature_readings')")
                tables = {row[0] for row in cur.fetchall()}
            except sqlite3.Error as e:
                # 垃圾/损坏文件：connect 惰性打开不报错，首条查询才抛
                raise ValueError(f"无法读取外部数据库：{e}")
            if not {"sessions", "channels",
                    "temperature_readings"} <= tables:
                raise ValueError("不是本程序的历史数据库文件（缺少数据表）")

            rows = src.execute(
                "SELECT session_id, session_name, started_at, stopped_at, "
                "channel_count, interval_seconds, source_path "
                "FROM sessions ORDER BY started_at").fetchall()
            total = len(rows)
            imported = skipped = failed = 0
            for done, row in enumerate(rows, 1):
                (sid, name, started_at, stopped_at, ch_count,
                 interval, src_path) = row
                try:
                    channels = [
                        {"channel_key": r[0], "channel_name": r[1],
                         "color": r[2], "physical_name": r[3],
                         "visible": r[4]}
                        for r in src.execute(
                            "SELECT channel_key, channel_name, color, "
                            "physical_name, visible FROM channels "
                            "WHERE session_id=? ORDER BY channel_id", (sid,))
                    ]
                    readings = [
                        {"channel_key": r[0], "timestamp": r[1],
                         "temperature": r[2], "is_valid": r[3]}
                        for r in src.execute(
                            "SELECT channel_key, timestamp, temperature, "
                            "is_valid FROM temperature_readings "
                            "WHERE session_id=? ORDER BY timestamp", (sid,))
                    ]
                    result = self.import_remote_session(
                        sid, name or sid, float(started_at or 0.0),
                        int(ch_count or 1), float(interval or 1.0),
                        src_path or f"import://{os.path.basename(source_path)}",
                        channels, readings, stopped_at=stopped_at,
                        source_type='import')
                    if result == 'imported':
                        imported += 1
                    elif result == 'exists':
                        skipped += 1
                    else:
                        failed += 1
                except Exception as e:
                    print(f"导入外部会话 {sid} 失败: {e}")
                    failed += 1
                if progress_cb:
                    try:
                        progress_cb(done, total)
                    except Exception:
                        pass
            return imported, skipped, failed
        finally:
            src.close()

    @_synchronized
    def get_latest_timestamp(self, session_id: str) -> Optional[float]:
        """查询会话温度数据的最后时间戳；无数据返回 None。"""
        try:
            _min_ts, max_ts = self.reading_dao.get_time_range(session_id)
            return max_ts if max_ts else None
        except Exception:
            return None

    @_synchronized
    def get_sessions(self, limit: int = 100, start_time: Optional[float] = None,
                    end_time: Optional[float] = None) -> List[Session]:
        """
        查询历史会话列表

        参数:
            limit: 返回数量限制
            start_time: 开始时间过滤
            end_time: 结束时间过滤

        返回:
            List[Session]: 会话列表
        """
        return self.session_dao.get_list(limit, start_time, end_time)

    @_synchronized
    def get_session_by_id(self, session_id: str) -> Optional[Session]:
        """
        根据 ID 查询会话

        参数:
            session_id: 会话 ID

        返回:
            Session: 会话数据，如果不存在返回 None
        """
        return self.session_dao.get_by_id(session_id)

    @_synchronized
    def delete_session(self, session_id: str) -> bool:
        """
        删除会话及其关联数据（温度数据 / 通道配置 / 统计信息级联删除）

        参数:
            session_id: 会话 ID

        返回:
            bool: 是否成功
        """
        return self.session_dao.delete(session_id)

    # ─────────────────────────────────────────────────────
    # 通道配置
    # ─────────────────────────────────────────────────────

    @_synchronized
    def save_channel_config(self, session_id: str, channels: List[Dict]) -> bool:
        """
        保存通道配置

        参数:
            session_id: 会话 ID
            channels: 通道配置列表（字典格式）

        返回:
            bool: 是否成功
        """
        channel_models = []
        for ch in channels:
            channel = Channel(
                session_id=session_id,
                channel_key=ch.get('channel_key', ''),
                channel_name=ch.get('channel_name'),
                color=ch.get('color'),
                physical_name=ch.get('physical_name'),
                unit=ch.get('unit', '°C'),
                visible=ch.get('visible', True)
            )
            channel_models.append(channel)

        return self.channel_dao.create_batch(channel_models)

    @_synchronized
    def get_channel_config(self, session_id: str) -> List[Channel]:
        """
        获取会话的通道配置

        参数:
            session_id: 会话 ID

        返回:
            List[Channel]: 通道配置列表
        """
        return self.channel_dao.get_by_session(session_id)

    # ─────────────────────────────────────────────────────
    # 温度数据
    # ─────────────────────────────────────────────────────

    @_synchronized
    def insert_readings_batch(self, session_id: str, readings: List[Dict]) -> bool:
        """
        批量插入温度数据（使用事务，性能优化）

        参数:
            session_id: 会话 ID
            readings: 温度数据列表（字典格式）

        返回:
            bool: 是否成功
        """
        reading_models = []
        for rd in readings:
            reading = TemperatureReading(
                session_id=session_id,
                channel_key=rd.get('channel_key', ''),
                timestamp=rd.get('timestamp', 0.0),
                temperature=rd.get('temperature'),
                is_valid=rd.get('is_valid', True)
            )
            reading_models.append(reading)

        return self.reading_dao.create_batch(reading_models)

    @_synchronized
    def insert_alarm_event(self, session_id: str, channel_key: str,
                           timestamp: float, alarm_type: str,
                           threshold: Optional[float] = None,
                           actual_value: Optional[float] = None,
                           status: str = 'active') -> bool:
        """插入一条报警事件记录（active / cleared）。

        报警为低频事件，直接单条写入；复用 ``_synchronized`` 锁，
        与温度批量写并发安全。
        """
        try:
            self.conn.execute(
                """INSERT INTO alarm_events
                   (session_id, channel_key, timestamp, alarm_type,
                    threshold, actual_value, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, channel_key, timestamp, alarm_type,
                 threshold, actual_value, status))
            self.conn.commit()
            return True
        except Exception:
            return False

    @_synchronized
    def get_temperature_arrays(self, session_id, channels=None,
                               start_time=None, end_time=None, limit=0):
        """按通道返回 ``(timestamp, temperature)`` numpy 数组。

        跳过 ORM 物化，供历史会话大数据加载（``store.load_session_from_db``）。
        返回 ``{channel_key: (ts_ndarray, temp_ndarray)}``，温度 None 转 NaN。
        """
        try:
            return self.reading_dao.get_channels_arrays(
                session_id, list(channels or []), start_time, end_time, limit)
        except Exception:
            return {}

    @_synchronized
    def get_temperature_data(self, session_id: str,
                            channels: Optional[List[str]] = None,
                            start_time: Optional[float] = None,
                            end_time: Optional[float] = None,
                            limit: int = 10000,
                            offset: int = 0) -> Dict:
        """
        查询温度数据（支持分页和通道过滤）

        参数:
            session_id: 会话 ID
            channels: 通道键列表（可选）
            start_time: 开始时间戳（可选）
            end_time: 结束时间戳（可选）
            limit: 返回数量限制
            offset: 偏移量

        返回:
            Dict: 包含 'data' 和 'total' 字段
        """
        if channels:
            # 查询指定通道的数据
            channels_data = self.reading_dao.get_channels_data(
                session_id, channels, start_time, end_time, limit
            )
            return {
                'data': channels_data,
                'total': sum(len(v) for v in channels_data.values())
            }
        else:
            # 查询所有通道的数据
            if start_time is not None and end_time is not None:
                # 有时间范围，使用时间范围查询
                readings = self.reading_dao.get_by_time_range(
                    session_id, start_time, end_time, limit
                )
                # 时间范围查询返回的实际数据量
                total = len(readings)
            else:
                # 无时间范围，使用分页查询
                readings = self.reading_dao.get_by_session(session_id, limit, offset)
                total = self.reading_dao.count_by_session(session_id)

            return {
                'data': readings,
                'total': total
            }

    # ─────────────────────────────────────────────────────
    # 统计信息
    # ─────────────────────────────────────────────────────

    @_synchronized
    def get_session_stats(self, session_id: str) -> Dict[str, ChannelStats]:
        """
        获取会话的统计信息

        参数:
            session_id: 会话 ID

        返回:
            Dict[str, ChannelStats]: 按通道键分组的统计信息
        """
        cursor = self.conn.execute('''
            SELECT * FROM channel_stats WHERE session_id = ?
        ''', (session_id,))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        stats_dict = {}
        for row in rows:
            data = dict(zip(columns, row))
            stats = ChannelStats.from_dict(data)
            stats_dict[stats.channel_key] = stats

        return stats_dict

    # ─────────────────────────────────────────────────────
    # 批量记录数查询
    # ─────────────────────────────────────────────────────

    @_synchronized
    def get_record_counts(self, session_ids) -> Dict[str, int]:
        """批量查询会话的温度记录数，返回 {session_id: count}。

        不存在的 session_id 不会出现在结果中；按 500 个 ID 分块，
        避免超出 SQLite 变量数量上限（兼容低版本 SQLite / Python 3.8）。
        """
        counts: Dict[str, int] = {}
        ids = list(session_ids or ())
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            placeholders = ','.join('?' * len(chunk))
            cursor = self.conn.execute(
                f'SELECT session_id, COUNT(*) FROM temperature_readings '
                f'WHERE session_id IN ({placeholders}) GROUP BY session_id',
                chunk)
            for sid, cnt in cursor.fetchall():
                counts[sid] = cnt
        return counts

    @_synchronized
    def delete_sessions_by_range(self, start_time: float, end_time: float,
                                 exclude_ids=(), include_locked: bool = False) -> tuple:
        """按会话开始时间范围批量删除会话（外键级联清理关联数据）。

        默认跳过锁定会话（白名单不删除）；采集中会话经 exclude_ids 排除。

        参数:
            start_time: 开始时间戳（含）
            end_time: 结束时间戳（含）
            exclude_ids: 需要保留的会话 ID 集合（如本机正在采集的会话）
            include_locked: True 时锁定会话也参与删除（默认白名单受保护）

        返回:
            (deleted_count, skipped_count)：skipped 含排除会话与锁定会话
        """
        exclude = set(exclude_ids or ())
        cond_locked = '' if include_locked else ' AND locked = 0'
        cursor = self.conn.execute(
            'SELECT session_id FROM sessions '
            'WHERE started_at >= ? AND started_at <= ?' + cond_locked,
            (start_time, end_time))
        ids = [row[0] for row in cursor.fetchall()]
        skipped = sum(1 for sid in ids if sid in exclude)
        if not include_locked:
            # 锁定会话计入 skipped，向调用方如实报告保护数量
            cur_locked = self.conn.execute(
                'SELECT COUNT(*) FROM sessions '
                'WHERE started_at >= ? AND started_at <= ? AND locked = 1',
                (start_time, end_time))
            skipped += cur_locked.fetchone()[0]
        deleted = 0
        try:
            for sid in ids:
                if sid in exclude:
                    continue
                cur = self.conn.execute(
                    'DELETE FROM sessions WHERE session_id = ?', (sid,))
                deleted += cur.rowcount
            self.conn.commit()
            return deleted, skipped
        except sqlite3.Error:
            self.conn.rollback()
            return 0, skipped

    @_synchronized
    def purge_expired_sessions(self, retention_days: float = RETENTION_DAYS,
                               exclude_ids=()) -> tuple:
        """执行保留策略：删除保留期前的未锁定会话并回收磁盘。

        硬编码保留最近 RETENTION_DAYS = 30 天（用户决策，不做设置项），
        应用启动时后台线程调用。锁定会话（白名单）与 exclude_ids 指定
        会话跳过。有会话被删除时自动 VACUUM——SQLite 删除数据不收缩
        文件（本机 102MB 空壳库即此问题），必须显式回收。

        参数:
            retention_days: 保留天数（默认 RETENTION_DAYS）
            exclude_ids: 额外保护会话 ID 集合（如采集中会话）

        返回:
            (deleted, skipped)：删除数 / 跳过数（锁定 + 显式排除）
        """
        cutoff = time.time() - float(retention_days) * 86400.0
        deleted, skipped = self.delete_sessions_by_range(
            0.0, cutoff, exclude_ids=exclude_ids)
        # 有删除必回收；无删除但历史碎片超阈值（约 4MB）也回收——
        # 兼容保留策略上线前遗留的空闲页（SQLite 文件只增不减问题）
        if deleted > 0 or self._freelist_pages() > 1024:
            self._vacuum()
        return deleted, skipped

    def _freelist_pages(self) -> int:
        """数据库空闲页数量（删除会话后遗留的未回收页）。"""
        try:
            return self.conn.execute('PRAGMA freelist_count').fetchone()[0]
        except sqlite3.Error:
            return 0

    def _vacuum(self) -> None:
        """VACUUM 重建数据库文件，回收删除后的空闲页。

        WAL 模式下 VACUUM 结果先写入 WAL，主库文件要等 checkpoint 才
        收缩；此处跟随 TRUNCATE checkpoint，保证删除+压缩后磁盘立即
        真实回收。调用方须处于无事务状态（删除路径均已 commit/rollback）；
        大库耗时秒级，应在后台线程调用，不阻塞 GUI。
        """
        try:
            self.conn.execute('VACUUM')
            self.conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except sqlite3.Error as e:
            print(f"VACUUM 回收数据库空间失败: {e}")

    # ─────────────────────────────────────────────────────
    # 导出功能
    # ─────────────────────────────────────────────────────

    @_synchronized
    def export_to_csv(self, session_id: str, output_path: str,
                      channels: Optional[List[str]] = None,
                      start_time: Optional[float] = None,
                      end_time: Optional[float] = None,
                      use_display_name: bool = False) -> bool:
        """
        将会话数据导出为 CSV 文件（英文逗号分隔，utf-8-sig 编码，WPS 可直接打开）。

        表头：时间 + 各通道列（默认按物理通道号升序，列顺序固定）；
        时间格式：H:mm:ss（小时不补零，如 0:05:30）；温度保留两位小数；
        每条采样数据单独一行。

        参数:
            session_id: 会话 ID
            output_path: 输出文件路径
            channels: 要导出的通道列表（None 表示全部通道，按物理通道号排序）
            start_time: 开始时间戳（可选）
            end_time: 结束时间戳（可选）
            use_display_name: 是否使用显示名称（True 用通道名，False 用物理通道号）

        返回:
            bool: 是否成功
        """
        try:
            # 获取会话信息
            session = self.session_dao.get_by_id(session_id)
            if not session:
                print(f"会话不存在: {session_id}")
                return False

            # 获取通道配置（已按物理通道号数值排序，列顺序固定）
            channel_configs = self.channel_dao.get_by_session(session_id)
            channel_map = {ch.channel_key: ch for ch in channel_configs}

            # 如果未指定通道，使用全部通道（保持物理通道号升序）
            if channels is None:
                channels = [ch.channel_key for ch in channel_configs]

            # 查询温度数据
            readings_data = self.reading_dao.get_channels_data(
                session_id, channels, start_time, end_time, limit=1000000
            )

            if not readings_data:
                print(f"无数据可导出: {session_id}")
                return False

            # 准备数据结构
            # 按 timestamp 组织数据
            time_data = {}  # {timestamp: {channel_key: temperature}}

            for ch_key, readings in readings_data.items():
                for reading in readings:
                    ts = reading.timestamp
                    if ts not in time_data:
                        time_data[ts] = {}
                    time_data[ts][ch_key] = reading.temperature

            # 排序时间戳
            sorted_timestamps = sorted(time_data.keys())

            # 构建表头（时间在前，通道列顺序固定）
            header = ["时间"]
            for ch_key in channels:
                if use_display_name and ch_key in channel_map:
                    ch_name = channel_map[ch_key].channel_name or ch_key
                else:
                    ch_name = ch_key
                header.append(ch_name)

            # 写入文件（utf-8-sig 编码，防止 WPS / Excel 打开中文乱码）
            with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, lineterminator="\n")
                writer.writerow(header)

                for ts in sorted_timestamps:
                    # 转换为相对时间（H:mm:ss，小时不补零）
                    rel_time = ts - session.started_at
                    if rel_time < 0:
                        rel_time = 0.0

                    h = int(rel_time // 3600)
                    m = int((rel_time % 3600) // 60)
                    s = rel_time - h * 3600 - m * 60

                    # 根据采样间隔决定是否保留小数
                    if session.interval_seconds and abs(session.interval_seconds - round(session.interval_seconds)) > 1e-6:
                        time_str = f"{h}:{m:02d}:{s:06.3f}"
                    else:
                        time_str = f"{h}:{m:02d}:{int(round(s)):02d}"

                    # 构建数据行（每条采样数据单独一行）
                    row = [time_str]
                    ts_data = time_data[ts]
                    for ch_key in channels:
                        temp = ts_data.get(ch_key)
                        if temp is None:
                            row.append("")  # 缺测值
                        else:
                            row.append(f"{temp:.2f}")

                    writer.writerow(row)

            print(f"导出成功: {output_path} ({len(sorted_timestamps)} 行数据)")
            return True

        except Exception as e:
            print(f"导出失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    # ─────────────────────────────────────────────────────
    # 维护功能
    # ─────────────────────────────────────────────────────

    @_synchronized
    def backup(self, backup_path: str) -> bool:
        """
        备份数据库

        参数:
            backup_path: 备份文件路径

        返回:
            bool: 是否成功
        """
        try:
            # 确保备份目录存在
            backup_dir = os.path.dirname(backup_path)
            if backup_dir:
                os.makedirs(backup_dir, exist_ok=True)

            # 使用 SQLite 的 backup API
            backup_conn = sqlite3.connect(backup_path)
            self.conn.backup(backup_conn)
            backup_conn.close()
            return True
        except Exception as e:
            print(f"备份数据库失败: {e}")
            return False

    def get_db_size(self) -> int:
        """
        获取数据库文件大小（字节）

        返回:
            int: 文件大小
        """
        if os.path.exists(self.db_path):
            return os.path.getsize(self.db_path)
        return 0

    @_synchronized
    def close(self) -> None:
        """关闭数据库连接"""
        if self.conn:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            finally:
                self.conn = None

    def __del__(self):
        """析构函数，确保连接关闭"""
        if hasattr(self, 'conn'):
            self.close()

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出"""
        self.close()