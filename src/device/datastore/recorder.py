# -*- coding: utf-8 -*-
"""
Recorder —— 实时数据库写入器（仅数据库存储，UI 线程零阻塞）。

采集数据批量写入 SQLite 历史数据库。自整改计划 P1 起线程模型为：

    任意线程 write_frame() → 内存缓冲（_db_lock 保护，只做 list.append）
        → 专职 flusher 线程按秒级间隔批量提交（不依赖 GUI 事件循环）
        → 写库连续失败/缓冲超限 → spill.py 旁路落盘 JSONL
        → 写库恢复 → 自动回灌 spill；启动扫描兜底残留（main_window 提示）

设计不变量：
1. UI 线程上没有任何数据库 I/O 或 join；GUI 冻结/卡死不影响逐秒落库。
2. write_frame 调用方只付出一次加锁 append 的代价（微秒级）。
3. 批次要么进库、要么进 spill、要么回到内存缓冲，不存在静默丢弃路径。
4. 仅数据库模式，不再生成 Excel / 制表符文本采集文件。
"""
from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING, List, Dict, Optional

from . import spill as _spill

if TYPE_CHECKING:
    from device.database.history_db import HistoryDatabase
    from .channel import Channel
    from .session import Session

DB_WRITE_INTERVAL_SEC = 1.0   # 默认落库间隔（秒）：逐秒持久化底线
# 提前刷盘的缓冲行数上限（高吞吐时不必等满间隔）
FLUSH_EARLY_ROWS = 1000
# 连续失败达到该次数 → 失败批次连同缓冲整体旁路进 spill 文件
SPILL_AFTER_CONSECUTIVE_FAILS = 3
# 内存缓冲行数超限 → 整体旁路到 spill，防止无界增长
SPILL_MAX_BUFFER_ROWS = 50000


class Recorder:
    """把实时采集数据批量写入数据库（仅 database_only 模式）。

    线程安全：write_frame / flush_db 可被任意线程调用；
    open / close 约定由 UI 线程在采集启停点调用。
    """

    def __init__(self, history_db: Optional["HistoryDatabase"] = None,
                 db_write_interval: float = DB_WRITE_INTERVAL_SEC,
                 data_dir: Optional[str] = None):
        """
        初始化 Recorder

        参数:
            history_db: 数据库管理器实例
            db_write_interval: 数据库批量写入间隔（秒），默认 1 秒
            data_dir: 兜底 spill 文件目录（None 时与数据库同目录）
        """
        self._history_db = history_db
        self._db_write_interval = max(0.2, float(db_write_interval))
        self._data_dir = data_dir
        self._session_id: str = ""
        self._keys: List[str] = []
        self._db_buffer: List[Dict] = []
        self._db_rows = 0          # 已写入数据库的行数
        self._error: str = ""
        self._db_lock = threading.Lock()   # 缓冲区并发保护
        # flusher 线程控制
        self._flusher: Optional[threading.Thread] = None
        self._wake = threading.Event()      # 提前唤醒（缓冲超阈值 / close）
        self._stop_evt = threading.Event()  # 停止信号
        self._flush_in_progress = False     # 一次批量提交是否进行中（诊断用）
        self._last_flush_ts = 0.0           # 最近一次成功提交时间
        self._flush_fail_streak = 0         # 连续提交失败次数
        self._spill_rows = 0                # 本会话已旁路落盘行数

    # ==================================================================
    #  属性
    # ==================================================================
    @property
    def is_active(self) -> bool:
        """是否有输出目标：数据库会话已初始化。"""
        return bool(self._session_id)

    @property
    def session_id(self) -> str:
        """当前录制的数据库会话 ID（整改 N-1：报警事件落库取此值；未录制为空串）。"""
        return self._session_id

    @property
    def db_rows_written(self) -> int:
        """已写入数据库的行数"""
        return self._db_rows

    @property
    def pending_rows(self) -> int:
        """内存缓冲中尚未落库的行数（P1-4 度量）。"""
        with self._db_lock:
            return len(self._db_buffer)

    @property
    def spill_rows(self) -> int:
        """本会话已旁路写入兜底文件的行数（P1-4 度量）。"""
        return self._spill_rows

    @property
    def last_flush_age_sec(self) -> float:
        """距最近一次成功落库的秒数（P1-4 度量；从未成功为 -1）。"""
        ts = self._last_flush_ts
        return (time.time() - ts) if ts > 0 else -1.0

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
        """运行中更新数据库批量写入间隔（秒），下限 0.2 秒。"""
        self._db_write_interval = max(0.2, float(value))

    @property
    def last_error(self) -> str:
        """最近一次错误信息（无错误时为空字符串）"""
        return self._error

    # ==================================================================
    #  生命周期
    # ==================================================================
    def open(self, channels: "list[Channel]", interval: float = 1.0,
             session_id: str = "",
             session_name: Optional[str] = None) -> bool:
        """打开数据库录制：建会话记录并启动 flusher 线程。

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
        self._start_flusher()
        return True

    def close(self) -> None:
        """关闭录制：停 flusher、同步排空缓冲（失败旁路 spill）、写停止时间。

        顺序保证（修复旧 close 竞态丢批问题）：最终排空与 spill 落盘
        都发生在 _session_id 清空之前；在途插入通过有界 join 等待，
        超时后残余照常走「写失败→重排→spill」路径，不丢。
        """
        self._stop_flusher()

        session_id = self._session_id
        # 最终排空：循环换出-写入，直到缓冲为空（写失败则 spill 兜底）
        while True:
            with self._db_lock:
                if not self._db_buffer:
                    break
                batch = self._db_buffer
                self._db_buffer = []
            if self._flush_db(batch):
                self._db_rows += len(batch)
                continue
            if not self._spill_or_requeue(session_id, batch):
                # spill 也写不进（如磁盘故障）：数据退回缓冲，停止排空，
                # 保留现场并报错——绝不在 close 里死循环
                self._error = ("关闭时数据库与兜底文件均写入失败，"
                               f"{len(batch)} 行保留在内存缓冲待下次回灌")
                print(f"[Recorder] {self._error}", flush=True)
                break

        # 回灌本会话残留兜底文件（尽力而为；失败保留待启动扫描再处理）
        self._try_replay_spill()

        # 更新数据库会话停止时间
        if self._history_db and session_id:
            try:
                self._history_db.update_session_stop(session_id, time.time())
            except Exception:
                pass  # 不影响关闭流程

        self._session_id = ""

    # ==================================================================
    #  写入路径（任意线程可调）
    # ==================================================================
    def write_frame(self, t: float, values, timestamp: float | None = None) -> bool:
        """写一行到内存缓冲（微秒级，不做任何 I/O）。

        Args:
            t: 相对时间（秒，未使用，仅为接口兼容）
            values: 各通道数值列表（None → 无效值）
            timestamp: 绝对时间戳（数据库写入时间列）
        """
        if not self._history_db or not self._session_id:
            self._error = "数据库会话未初始化"
            return False

        try:
            abs_time = timestamp if timestamp is not None else time.time()
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
            if buffer_size >= FLUSH_EARLY_ROWS:
                self._wake.set()   # 提前刷盘（仍在 flusher 线程执行）
            return True
        except Exception as e:
            self._error = f"数据库写入失败: {e}"
            return False

    def write_session_tail(self, session: "Session", from_row: int) -> int:
        """把会话中 from_row 之后的数据补写进去（开始录制时补齐已采内存帧）。

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

    def flush_db(self, wait: bool = False) -> None:
        """唤醒 flusher 立即提交一批（非阻塞）。wait=True 时有界等待排空。"""
        self._wake.set()
        if wait:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if not self.pending_rows and not self._flush_in_progress:
                    break
                time.sleep(0.02)

    # ==================================================================
    #  flusher 线程
    # ==================================================================
    def _start_flusher(self) -> None:
        if self._flusher is not None and self._flusher.is_alive():
            return
        self._stop_evt.clear()
        self._wake.clear()
        self._last_flush_ts = time.time()
        self._flush_fail_streak = 0
        self._spill_rows = 0
        self._flusher = threading.Thread(
            target=self._flusher_loop, daemon=True, name="RecorderFlusher")
        self._flusher.start()

    def _stop_flusher(self) -> None:
        th = self._flusher
        if th is None:
            return
        self._stop_evt.set()
        self._wake.set()
        th.join(timeout=5.0)
        self._flusher = None

    def _flusher_loop(self) -> None:
        while True:
            self._wake.wait(timeout=self._db_write_interval)
            self._wake.clear()
            if self._stop_evt.is_set():
                return
            try:
                self._pump_once()
            except Exception as e:      # flusher 绝不允许静默死亡（C5 教训）
                self._error = f"落库线程异常: {e}"
                print(f"[Recorder] 落库线程异常（已捕获，继续运行）: {e}",
                      flush=True)

    def _pump_once(self) -> None:
        """换出一批 → 提交 → 成功则回灌 spill；失败重排或旁路。"""
        with self._db_lock:
            if not self._db_buffer:
                # 缓冲为空也尝试回灌（DB 可能已恢复而失败批次早已 spill）
                self._try_replay_spill()
                return
            batch = self._db_buffer
            self._db_buffer = []
        self._flush_in_progress = True
        try:
            ok = self._flush_db(batch)
        finally:
            self._flush_in_progress = False
        if ok:
            self._db_rows += len(batch)
            self._flush_fail_streak = 0
            self._last_flush_ts = time.time()
            self._try_replay_spill()
            return
        self._flush_fail_streak += 1
        with self._db_lock:
            total = len(batch) + len(self._db_buffer)
        if (self._flush_fail_streak >= SPILL_AFTER_CONSECUTIVE_FAILS
                or total > SPILL_MAX_BUFFER_ROWS):
            # 失败批次连同当前缓冲整体旁路落盘（行不重复、缓冲不无界增长）
            with self._db_lock:
                rows = list(batch) + self._db_buffer
                self._db_buffer = []
            self._spill_or_requeue(self._session_id, rows)
        else:
            with self._db_lock:
                self._db_buffer[:0] = batch   # 失败批次重排队首，保持时序

    # ------------------------------------------------------------------
    def _flush_db(self, batch: List[Dict]) -> bool:
        """把给定批次同步写入数据库（纯插入，不触碰 _db_buffer）。返回是否成功。"""
        if not batch:
            return True
        if not self._history_db or not self._session_id:
            return False
        try:
            return bool(self._history_db.insert_readings_batch(
                self._session_id, batch))
        except Exception as e:
            self._error = f"数据库批量写入异常: {e}"
            return False

    def _spill_or_requeue(self, session_id: str, rows: List[Dict]) -> bool:
        """把行旁路写入兜底文件。返回 True 表示已持久化；
        spill 写也失败则放回内存缓冲并返回 False（最后兜底）。"""
        if not rows:
            return True
        if not session_id:
            # 无会话归属：spill 文件名无从生成，退回缓冲保留数据
            with self._db_lock:
                self._db_buffer[:0] = rows
            return False
        try:
            path = _spill.spill_path(session_id, self._data_dir)
            n = _spill.append_rows(path, session_id, rows)
            self._spill_rows += n
            self._error = f"数据库写入失败，已旁路落盘 {n} 行：{path}"
            print(f"[Recorder] {self._error}", flush=True)
            return True
        except Exception as e:
            with self._db_lock:
                self._db_buffer[:0] = rows
            self._error = f"兜底文件写入失败（数据退回内存缓冲重试）: {e}"
            print(f"[Recorder] {self._error}", flush=True)
            return False

    def _try_replay_spill(self) -> None:
        """DB 恢复后回灌本会话兜底文件（成功改名 .done；失败保留待下次）。"""
        if not self._history_db or not self._session_id:
            return
        try:
            path = _spill.spill_path(self._session_id, self._data_dir)
            if not os.path.exists(path):
                return
            n, err = _spill.replay_file(self._history_db, path)
            if err:
                self._error = f"兜底回灌未完成: {err}"
                print(f"[Recorder] {self._error}", flush=True)
            elif n:
                print(f"[Recorder] 兜底文件已回灌 {n} 行入库", flush=True)
        except Exception as e:
            print(f"[Recorder] 兜底回灌异常（保留文件）: {e}", flush=True)

    # ==================================================================
    #  数据库会话初始化
    # ==================================================================
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
            with self._db_lock:
                self._db_buffer = []
            self._db_rows = 0
            return True

        except Exception as e:
            self._error = f"数据库初始化异常: {e}"
            return False
