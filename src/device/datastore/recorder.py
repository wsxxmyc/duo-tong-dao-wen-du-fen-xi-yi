# -*- coding: utf-8 -*-
"""
Recorder —— 实时数据库写入器（仅数据库存储）。

采集数据批量写入 SQLite 历史数据库：每帧进入内存缓冲区，
达到写入间隔或缓冲区上限时批量提交（事务）。停止采集时
补充提交剩余数据并写入会话停止时间。

设计：
1. 仅数据库模式，不再生成 Excel / 制表符文本采集文件。
2. 批量写入（事务提交）避免逐条 insert 的性能损耗。
3. 采集数据与历史记录、远程访问共用同一数据库链路。
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, List, Dict, Optional

if TYPE_CHECKING:
    from device.database.history_db import HistoryDatabase
    from .channel import Channel
    from .session import Session

DB_WRITE_INTERVAL_SEC = 60.0  # 数据库批量写入间隔


class Recorder:
    """把实时采集数据批量写入数据库（仅 database_only 模式）。"""

    def __init__(self, history_db: Optional["HistoryDatabase"] = None,
                 db_write_interval: float = DB_WRITE_INTERVAL_SEC):
        """
        初始化 Recorder

        参数:
            history_db: 数据库管理器实例
            db_write_interval: 数据库批量写入间隔（秒），默认 60 秒
        """
        # 数据库相关
        self._history_db = history_db
        self._db_write_interval = db_write_interval
        self._session_id: str = ""
        self._keys: list[str] = []
        self._db_buffer: List[Dict] = []  # 数据库批量写入缓冲区
        self._last_db_flush = 0.0
        self._db_rows = 0  # 已写入数据库的行数
        self._error: str = ""
        self._db_lock = threading.Lock()        # 缓冲区并发保护（C1/A16 后台插入）
        self._flush_in_progress = False          # 后台批量插入是否进行中

    # ==================================================================
    @property
    def is_active(self) -> bool:
        """是否有输出目标：数据库会话已初始化。"""
        return bool(self._session_id)

    @property
    def db_rows_written(self) -> int:
        """已写入数据库的行数"""
        return self._db_rows

    @property
    def history_db(self) -> Optional["HistoryDatabase"]:
        """数据库管理器实例"""
        return self._history_db

    @history_db.setter
    def history_db(self, value) -> None:
        """运行中更新数据库管理器引用（数据库模式初始化时使用）。"""
        self._history_db = value

    @property
    def db_write_interval(self) -> float:
        """数据库批量写入间隔（秒）"""
        return self._db_write_interval

    @db_write_interval.setter
    def db_write_interval(self, value) -> None:
        """运行中更新数据库批量写入间隔（秒）。"""
        self._db_write_interval = float(value)

    @property
    def last_error(self) -> str:
        """最近一次错误信息（无错误时为空字符串）"""
        return self._error

    # ==================================================================
    def open(self, channels: "list[Channel]", interval: float = 1.0,
             session_id: str = "",
             session_name: Optional[str] = None) -> bool:
        """打开数据库录制。

        Args:
            channels: list[Channel]，通道配置
            interval: 采样间隔（默认 1.0）
            session_id: 数据库会话 ID（可选，不传则自动生成）
            session_name: 会话名称（可选；不传则按「采集_时间」自动命名）
        """
        self.close()
        self._error = ""
        if not self._init_db_session(channels, interval, session_id,
                                     session_name):
            return False
        self._keys = [c.key for c in channels]
        self._last_db_flush = time.time()
        return True

    def _init_db_session(self, channels, interval, session_id="",
                         session_name: Optional[str] = None) -> bool:
        """初始化数据库会话"""
        if not self._history_db:
            self._error = "数据库管理器未初始化"
            return False

        try:
            # 生成会话 ID（如果未提供）
            if not session_id:
                import uuid
                session_id = str(uuid.uuid4())

            self._session_id = session_id

            # 创建会话记录：自定义名优先，否则按时间自动命名
            if not (session_name or "").strip():
                session_name = time.strftime("采集_%Y%m%d_%H%M%S",
                                             time.localtime())
            started_at = time.time()

            success = self._history_db.create_session(
                session_id=self._session_id,
                session_name=session_name,
                started_at=started_at,
                channel_count=len(channels),
                interval=interval,
                source_type='live'
            )

            if not success:
                self._error = "创建数据库会话记录失败"
                return False

            # 保存通道配置
            channel_configs = []
            for c in channels:
                channel_configs.append({
                    'channel_key': c.key,
                    'channel_name': getattr(c, 'display_name', c.key),
                    'color': getattr(c, 'color', None),
                    'physical_name': c.key,
                    'visible': True
                })

            if not self._history_db.save_channel_config(self._session_id, channel_configs):
                self._error = "保存通道配置失败"
                return False

            # 初始化缓冲区
            self._db_buffer = []
            self._last_db_flush = time.time()
            return True

        except Exception as e:
            self._error = f"数据库初始化异常: {e}"
            return False

    # ==================================================================
    def write_frame(self, t: float, values, timestamp: float | None = None) -> bool:
        """写一行。t 为相对秒；values 为各通道数值（None → 空）。

        Args:
            t: 相对时间（秒）
            values: 各通道数值列表
            timestamp: 绝对时间戳（可选，用于数据库写入）
        """
        if not self._history_db or not self._session_id:
            self._error = "数据库会话未初始化"
            return False

        try:
            # 使用传入的 timestamp 或当前时间
            abs_time = timestamp if timestamp is not None else time.time()

            # 添加到缓冲区（加锁：与后台 flush 换出/失败重排互斥，C1/A16）
            with self._db_lock:
                for i, key in enumerate(self._keys):
                    v = values[i] if i < len(values) else None
                    temp = float(v) if v is not None and v == v else None
                    self._db_buffer.append({
                        'channel_key': key,
                        'timestamp': abs_time,
                        'temperature': temp,
                        'is_valid': temp is not None
                    })
                buffer_size = len(self._db_buffer)

            # 批量写入：达到间隔或缓冲区过大时提交（flush_db 现为换出+后台插入）
            now = time.time()
            if (now - self._last_db_flush >= self._db_write_interval
                    or buffer_size >= 1000):  # 缓冲区超过 1000 条立即提交
                self._last_db_flush = now
                self.flush_db()

            return True

        except Exception as e:
            self._error = f"数据库写入失败: {e}"
            return False

    def _flush_db(self, batch) -> bool:
        """把给定批次同步写入数据库（纯插入，不触碰 _db_buffer）。返回是否成功。"""
        if not batch:
            return True
        if not self._history_db or not self._session_id:
            return False
        try:
            return bool(self._history_db.insert_readings_batch(self._session_id, batch))
        except Exception as e:
            self._error = f"数据库批量写入异常: {e}"
            return False

    def _insert_bg(self, batch) -> None:
        """后台批量插入线程入口（C1/A16）。失败把批次重排回缓冲，不丢数据。"""
        try:
            if self._flush_db(batch):
                self._db_rows += len(batch)
            else:
                with self._db_lock:
                    self._db_buffer[:0] = batch
                self._error = "批量插入数据库失败"
        except Exception as e:
            with self._db_lock:
                self._db_buffer[:0] = batch
            self._error = f"数据库批量写入异常: {e}"
        finally:
            self._flush_in_progress = False

    def write_session_tail(self, session: "Session", from_row: int) -> int:
        """把会话中 from_row 之后的数据补写进去（断点续录 / 首次挂载）。

        时间戳按「会话开始时间 + 相对秒」换算，保证与实时写入一致。
        """
        if not self._session_id:
            return 0
        t = session.buffer.time
        mat = session.buffer.matrix
        base = float(session.started_at or time.time())
        cnt = 0
        for i in range(max(0, from_row), len(t)):
            if self.write_frame(float(t[i]), mat[i], timestamp=base + float(t[i])):
                cnt += 1
        return cnt

    def flush_db(self) -> None:
        """换出缓冲区并交后台线程批量插入（C1/A16：UI 线程不阻塞）。

        上一次插入未完成时把本批并回缓冲尾部，等下轮重试，保证数据不丢。
        """
        with self._db_lock:
            if not self._db_buffer:
                return
            batch = self._db_buffer
            self._db_buffer = []
        if self._flush_in_progress:
            with self._db_lock:
                self._db_buffer[:0] = batch
            return
        self._flush_in_progress = True
        threading.Thread(target=self._insert_bg, args=(batch,), daemon=True).start()

    def close(self) -> None:
        """关闭录制器，提交剩余数据并写入会话停止时间。"""
        # 等待后台插入完成（有界），避免退出丢数据或残留线程
        deadline = time.time() + 5.0
        while self._flush_in_progress and time.time() < deadline:
            time.sleep(0.02)
        with self._db_lock:
            batch = self._db_buffer
            self._db_buffer = []
        if batch:
            if self._flush_db(batch):
                self._db_rows += len(batch)

        # 更新数据库会话停止时间
        if self._history_db and self._session_id:
            try:
                self._history_db.update_session_stop(
                    self._session_id,
                    time.time()
                )
            except Exception:
                pass  # 不影响关闭流程

        self._session_id = ""
