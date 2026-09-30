# -*- coding: utf-8 -*-
"""
DataStore —— 全局唯一的数据总线。

这是整个应用的**单一数据源（Single Source of Truth）**。
所有页面（整体趋势 / 前10·20·30 / 通道对比 / 组合图 / 温升统计 / 实时监控）
都只跟它打交道：订阅信号、读取 active session，**不再互相持有引用**。

它同时解决了重构的四个目标：

1. 统一数据层     —— 通道列表 / 名称 / 自定义只有 Session + ChannelConfig 一份
2. 实时追加写入   —— 采集线程 persist_frame() 投递持久化（Recorder 缓冲），
                    GUI 线程 append_live() 写内存 buffer 并广播 UI
3. 所有页面同源   —— 一个 data_appended 信号，所有页面同步刷新
4. 配置全局共享   —— 文件导入与在线采集走同一个 ChannelConfig

多会话模型
----------
sessions 是有序字典，可同时存在：
  - 一个 LIVE 会话（采集中，持续增长，后台不中断）
  - 若干 FILE 会话（打开的历史文件）
`active` 指针决定当前页面展示哪一个。**切换 active 不影响 LIVE 采集**，
这正是"大部分时间实时采集、偶尔查看历史分析"的使用场景。

线程模型
--------
采集子线程每帧先经 persist_frame()（线程安全，只入 Recorder 内存缓冲，
微秒级，不做 I/O、不发 Qt 信号）完成持久化投递；随后 data_received
经 Qt 队列连接派发到主线程的 append_live 槽，写内存 buffer 并广播 UI。
落库提交由 Recorder 专职 flusher 线程按秒级间隔执行——GUI 冻结/卡死
不影响数据逐秒入库（整改计划 P1）。
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Optional

import numpy as np

from PyQt5.QtCore import QObject, pyqtSignal

from .channel_config import load_or_migrate
from .session import Session, SOURCE_FILE
from .recorder import Recorder
from .tpx_writer import write_tpx
from . import loader

if TYPE_CHECKING:
    from device.database.history_db import HistoryDatabase


class DataStore(QObject):
    """全局数据总线（单例，通过模块级 store 访问）。"""

    # ── 会话生命周期 ──
    session_added = pyqtSignal(object)        # Session
    session_closed = pyqtSignal(str)          # session_id
    active_changed = pyqtSignal(object)       # Session | None

    # ── 数据变化 ──
    data_appended = pyqtSignal(str, int, int)  # session_id, start_row, count
    session_reloaded = pyqtSignal(str)         # session_id（整体重算，需全量重绘）

    # ── 通道配置变化（名称 / 颜色 / 显隐）──
    channels_changed = pyqtSignal(object)      # list[key] | None(整体)

    # ── 录制状态 ──
    recording_changed = pyqtSignal(bool, str)  # is_recording, path
    error = pyqtSignal(str)

    def __init__(self, config_dir: str = "",
                 history_db: Optional["HistoryDatabase"] = None,
                 db_write_interval: float = 1.0):
        """
        初始化数据总线

        参数:
            config_dir: 配置目录路径
            history_db: 数据库管理器实例（可选）
            db_write_interval: 数据库批量写入间隔（秒），默认 1 秒
        """
        super().__init__()
        self._sessions: "OrderedDict[str, Session]" = OrderedDict()
        self._active_id: str = ""
        self._live_id: str = ""
        self.config_dir = config_dir
        self.config = None
        self.recorder = Recorder(
            history_db=history_db,
            db_write_interval=db_write_interval
        )
        self._recorded_rows = 0
        self._history_db = history_db

        if config_dir:
            self.init_config(config_dir)

    # ==================================================================
    #  初始化
    # ==================================================================
    def init_config(self, config_dir: str) -> None:
        """加载/迁移通道配置。应在 QApplication 创建后尽早调用一次。"""
        self.config_dir = config_dir
        self.config = load_or_migrate(config_dir, unified=True)
        self.config.subscribe(self._on_config_changed)

    def _on_config_changed(self, keys):
        """ChannelConfig 变了 → 重新下发到所有会话 → 广播。"""
        for s in self._sessions.values():
            self.config.apply_all(s.channels)
        self.channels_changed.emit(keys)

    def set_storage_config(self, db_flush_sec: float) -> None:
        """更新数据库落库间隔（秒），运行时生效。

        钳制分两层：本入口经 Recorder 属性 setter 下限钳 0.2 秒；
        启动/热重载读配置路径（主窗口 _load_storage_config）另按
        storage.db_flush_sec 钳 [0.5, 300]。
        仅数据库存储：写入模式固定，落库间隔由主窗口初始化时固定同步。
        调用方：主窗口加载配置后。
        """
        self.recorder.db_write_interval = float(db_flush_sec)

    # ==================================================================
    #  会话访问
    # ==================================================================
    @property
    def sessions(self) -> list[Session]:
        """全部会话列表（按创建顺序）"""
        return list(self._sessions.values())

    @property
    def active(self) -> Session | None:
        """当前展示的会话（可能为 None）"""
        return self._sessions.get(self._active_id)

    @property
    def live(self) -> Session | None:
        """正在采集的会话（可能不是当前展示的那个）。"""
        return self._sessions.get(self._live_id)

    @property
    def has_live(self) -> bool:
        """是否存在实时采集会话"""
        return bool(self._live_id) and self._live_id in self._sessions

    # ── P1-4 持久化度量（供状态栏/诊断只读展示）──
    @property
    def pending_rows(self) -> int:
        """内存缓冲中尚未落库的采集行数。"""
        return self.recorder.pending_rows

    @property
    def spill_rows(self) -> int:
        """本会话已旁路写入兜底文件的行数（>0 表示写库发生过连续失败）。"""
        return self.recorder.spill_rows

    @property
    def last_flush_age_sec(self) -> float:
        """距最近一次成功落库的秒数（从未成功为 -1）。"""
        return self.recorder.last_flush_age_sec

    def get(self, session_id: str) -> Session | None:
        """按会话 ID 查找会话（不存在返回 None）"""
        return self._sessions.get(session_id)

    # 兼容旧代码：MainWindow.dataset
    @property
    def dataset(self) -> Session | None:
        """兼容旧代码：当前活动会话"""
        return self.active

    # ==================================================================
    #  会话管理
    # ==================================================================
    def add_session(self, s: Session, activate: bool = True) -> Session:
        self._sessions[s.id] = s
        if self.config:
            self.config.apply_all(s.channels)
        self.session_added.emit(s)
        if activate:
            self.activate(s.id)
        return s

    def activate(self, session_id: str) -> bool:
        """切换当前展示的会话。不会中断正在录制的 LIVE 会话。"""
        if session_id not in self._sessions:
            return False
        if session_id == self._active_id:
            return True
        self._active_id = session_id
        self.active_changed.emit(self._sessions[session_id])
        return True

    def activate_live(self) -> bool:
        """一键跳回实时会话。"""
        return self.activate(self._live_id) if self.has_live else False

    def close_session(self, session_id: str) -> bool:
        """关闭一个会话。正在录制的 LIVE 会话拒绝关闭。"""
        s = self._sessions.get(session_id)
        if s is None:
            return False
        if s.is_recording:
            self.error.emit("正在录制的会话不能关闭，请先停止采集")
            return False
        del self._sessions[session_id]
        if session_id == self._live_id:
            self._live_id = ""
        self.session_closed.emit(session_id)
        if session_id == self._active_id:
            self._active_id = ""
            nxt = next(iter(self._sessions), "")
            if nxt:
                self.activate(nxt)
            else:
                self.active_changed.emit(None)
        return True

    # ==================================================================
    #  文件导入
    # ==================================================================
    def open_file(self, path: str, activate: bool = True) -> Session | None:
        """打开历史文件为一个新会话。采集不受影响。"""
        try:
            s = loader.load_session(path, config=self.config)
        except Exception as e:
            self.error.emit(f"无法加载文件：\n{path}\n\n{e}")
            return None
        return self.add_session(s, activate=activate)

    # ==================================================================
    #  历史数据库加载
    # ==================================================================
    def load_session_from_db(self, history_db: Optional["HistoryDatabase"],
                             session_id: str,
                             activate: bool = True) -> Session | None:
        """把历史数据库中的一次采集会话加载为可分析会话（解析 + 挂载）。

        解析委托 parse_session_from_db（可安全在后台线程跑），随后在调用线程
        add_session 完成挂载并刷新界面。失败返回 None（错误经 error 信号发出）。
        """
        s = self.parse_session_from_db(history_db, session_id)
        if s is not None:
            self.add_session(s, activate=activate)
        return s

    def parse_session_from_db(self, history_db: Optional["HistoryDatabase"],
                              session_id: str) -> Session | None:
        """从历史数据库解析一次会话为 Session（线程安全的纯解析）。

        不改 store、不发 active_changed；失败经 error 信号通知（跨线程发射为
        队列连接，安全）。供后台线程调用，随后在 UI 线程 add_session 挂载。
        数据链路：会话元信息 → 通道配置 → 温度数组 → 本地 Session。
        """
        from .channel import Channel
        from .buffer import buffer_from_arrays
        import numpy as np

        if history_db is None:
            self.error.emit("数据库未初始化，无法加载历史会话")
            return None

        db_session = history_db.get_session_by_id(session_id)
        if db_session is None:
            self.error.emit(f"数据库中没有该会话：{session_id}")
            return None

        channel_configs = history_db.get_channel_config(session_id)
        if not channel_configs:
            self.error.emit(f"会话「{db_session.session_name}」没有通道配置")
            return None

        keys = [c.channel_key for c in channel_configs]
        # 直接取 (timestamp, temperature) 数组，跳过逐行 ORM 物化（大数据加载提速）
        arrays_by_ch = history_db.get_temperature_arrays(session_id, channels=keys)

        # 时间轴：绝对时间戳 - 会话开始时间 = 相对秒（与实时采集时间轴一致）
        started_at = float(db_session.started_at or 0.0)
        timestamps = None
        columns = []
        for ch in channel_configs:
            arr = arrays_by_ch.get(ch.channel_key)
            if arr is None:
                ts = np.array([], dtype=float)
                temp = np.array([], dtype=float)
            else:
                ts, temp = arr
            if timestamps is None:
                timestamps = ts
            columns.append(temp)

        if timestamps is None or timestamps.size == 0:
            self.error.emit(f"会话「{db_session.session_name}」没有温度数据")
            return None

        rel_time = timestamps - started_at

        # 通道对象（名称 / 颜色沿用采集时的配置）
        channels = []
        for i, ch in enumerate(channel_configs):
            c = Channel(
                key=ch.channel_key,
                index=i,
                source_label=ch.channel_name or ch.channel_key,
            )
            c.name = ch.channel_name or ch.channel_key
            c.color = ch.color or "#3498DB"
            channels.append(c)

        buf = buffer_from_arrays(rel_time, columns)
        session = Session(
            channels, buf,
            source=SOURCE_FILE,
            path=f"db://{session_id}",
            title=db_session.session_name or session_id,
            interval=db_session.interval_seconds or 1.0,
        )
        return session

    # ==================================================================
    #  远端会话加载
    # ==================================================================
    def load_session_from_remote(self, session_info: dict, channels: list,
                                 data: dict, device_label: str,
                                 activate: bool = True) -> Optional[Session]:
        """把远端服务端返回的一次采集会话加载为可分析会话。

        数据链路：远端会话元信息 → 通道配置 → 温度数据 → 本地 Session，
        与「打开文件 / 历史数据库加载」一致（source=file，
        path='remote://<设备标识>/<远端会话ID>'）。解析在调用线程执行，
        不阻塞采集。

        返回:
            Session: 加载成功返回会话；失败返回 None（错误经 error 信号发出）
        """
        from .channel import Channel
        from .buffer import buffer_from_arrays

        parsed = parse_remote_temperature_data(data)
        if not parsed:
            self.error.emit("远端数据为空或格式无法解析")
            return None

        session_id = session_info.get('session_id', '')
        started_at = float(session_info.get('started_at') or 0.0) or time.time()

        # 通道对象（名称 / 颜色沿用远端采集时的配置）
        session_channels = []
        for i, ch_info in enumerate(channels):
            key = ch_info.get('channel_key', f'CH{i + 1}')
            ch = Channel(
                key=key,
                index=i,
                source_label=ch_info.get('channel_name') or key,
            )
            ch.name = ch_info.get('channel_name') or key
            ch.color = (ch_info.get('color') or ch_info.get('channel_color')
                        or "#3498DB")
            session_channels.append(ch)

        # 时间轴：绝对时间戳 - 会话开始时间 = 相对秒（与实时采集时间轴一致）
        timestamps = None
        columns = []
        for ch in session_channels:
            recs = parsed.get(ch.key, []) or []
            ts = np.array([r[0] for r in recs], dtype=float)
            temp = np.array([r[1] for r in recs], dtype=float)
            if timestamps is None:
                timestamps = ts
            columns.append(temp)

        if timestamps is None or timestamps.size == 0:
            self.error.emit("远端会话没有温度数据")
            return None

        rel_time = timestamps - started_at
        buf = buffer_from_arrays(rel_time, columns)

        session = Session(
            session_channels, buf,
            source=SOURCE_FILE,
            path=f"remote://{device_label}/{session_id}",
            title=session_info.get('session_name') or session_id or "远程会话",
            interval=session_info.get('interval_seconds', 1.0),
        )
        self.add_session(session, activate=activate)
        return session

    # ==================================================================
    #  实时采集
    # ==================================================================
    def create_live_session(self, n_channels: int, interval: float = 1.0,
                            keys: list[str] | None = None,
                            activate: bool = True) -> Session:
        """新建实时采集会话。若已有 LIVE 会话且未在录制，则替换之。

        注意：是否重置逐通道自定义名由调用方（开始采集前的弹窗选择）决定，
        这里不强制清空 —— 选「使用以前的名称」时沿用历史映射。
        """
        old = self.live
        if old is not None and not old.is_recording and old.n == 0:
            self.close_session(old.id)

        s = Session.create_live(n_channels, interval=interval, config=self.config)
        if keys:
            for i, k in enumerate(keys[:len(s.channels)]):
                s.channels[i].key = k
                s.channels[i].source_label = k
            if self.config:
                self.config.apply_all(s.channels)
        s.started_at = time.time()
        self._live_id = s.id
        return self.add_session(s, activate=activate)

    def reset_channel_names(self) -> None:
        """清空全部逐通道自定义名（新采集默认显示原始通道名 CH1/CH2…）。

        名称列表（name_pool）保留作下拉候选；逐通道映射由用户采集后
        从下拉选用时重新建立。
        """
        if self.config is None:
            return
        for rec in self.config.channels.values():
            rec.pop("name", None)
        self.config.save()

    def persist_frame(self, timestamp: float, values) -> bool:
        """持久化入口：由采集工作线程直接调用（不经 GUI 事件循环）。

        只做一次加锁入队（Recorder 内存缓冲），不发任何 Qt 信号、
        不做任何 I/O；提交由 Recorder flusher 线程按秒级间隔完成。
        GUI 冻结/卡死/崩溃不影响本路径——这是"采集数据逐秒落库"的底线。

        参数:
            timestamp: 帧绝对时间戳（秒）
            values: 各通道数值列表（None → 无效）
        """
        s = self.live
        if s is None or not s.is_recording:
            return False
        if not self.recorder.is_active:
            return False
        return self.recorder.write_frame(0.0, values, timestamp=timestamp)

    def append_live(self, t: float, values, timestamp: float | None = None) -> int:
        """UI 线程数据入口（Qt 队列连接 data_received → 主线程执行）。

        自 P1 起只负责：写内存 buffer（绘图数据源）+ 广播增量信号。
        持久化已前移到采集线程的 persist_frame()，此处不再写 Recorder，
        避免同一帧双写。timestamp 参数保留仅为接口兼容。
        """
        s = self.live
        if s is None:
            return -1
        if len(values) > len(s.channels):
            s.ensure_channels(len(values), self.config)
        row = s.append_frame(t, values)
        self.data_appended.emit(s.id, row, 1)
        return row

    def append_remote_frames(self, session, times, rows) -> tuple:
        """向已挂载的远程会话批量追加帧（实时监控增量），广播 data_appended。

        与 append_live 的区别：目标可以是任意已挂载会话（远程监控会话），
        不写磁盘（监控数据在监控结束时一次性落库）、不扩展通道数。
        必须在 UI 线程调用（data_appended 下游假定主线程执行）。

        参数:
            session: 已挂载的 Session（远程监控目标，通道数与 rows 列数一致）
            times: 相对秒时间轴（与该会话 buffer 一致：绝对时间戳 - started_at）
            rows: 每帧各通道值列表（通道缺失填 NaN）

        返回:
            (start_row, count)：追加的起始行号与行数；空数据返回 (当前行数, 0)
        """
        if session is None:
            return -1, 0
        if not times:
            return session.n, 0
        start_row, count = session.buffer.extend(
            np.asarray(times, dtype=float),
            np.asarray(rows, dtype=float))
        # 通道最新值 / 峰值更新（对齐 append_frame 的行为）
        for i in range(count):
            row = rows[i]
            for c in session.channels:
                k = c.index
                if k >= len(row):
                    continue
                v = row[k]
                if v is None:
                    continue
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if fv != fv:      # NaN
                    continue
                c.last_value = fv
                if fv > c.peak:
                    c.peak = fv
        if count:
            self.data_appended.emit(session.id, start_row, count)
        return start_row, count

    # ==================================================================
    #  录制
    # ==================================================================
    def start_recording(self, session_id: str = "",
                        session_name: Optional[str] = None) -> bool:
        """开始数据库录制：初始化数据库会话并启动专职 flusher 线程。

        参数:
            session_id: 数据库会话 ID（可选，默认使用实时会话 ID）
            session_name: 会话名称（可选；None 时按「采集_时间」自动命名）
        """
        s = self.live
        if s is None:
            self.error.emit("没有实时会话，无法开始录制")
            return False

        # 使用会话 ID 或生成新的
        if not session_id:
            session_id = s.id

        ok = self.recorder.open(
            s.channels, interval=s.interval, session_id=session_id,
            session_name=session_name
        )
        if not ok:
            self.error.emit(f"无法写入数据库：{self.recorder.last_error}")
            return False

        # 已经在内存里但还没落盘的数据补写进去
        self._recorded_rows = self.recorder.write_session_tail(s, 0)
        if s.started_at is None:
            s.started_at = time.time()
        s.is_recording = True

        self.recording_changed.emit(True, "")
        return True

    def stop_recording(self, export_tpx_path: str | None = None) -> bool:
        """停止录制：提交剩余数据、写入会话停止时间。可另存一份原厂 .tpx。"""
        s = self.live
        if s is not None:
            # 先摘除采集线程持久化路径（persist_frame 判据），再关录制器，
            # 避免 close 排空后又有新帧入缓冲
            s.is_recording = False
        self.recorder.close()
        if s is not None:
            s.stopped_at = time.time()
            if export_tpx_path:
                ok, err = write_tpx(export_tpx_path, s)
                if not ok:
                    self.error.emit(f"导出 TPX 失败：{err}")
        self.recording_changed.emit(False, "")
        return True

    # ==================================================================
    #  通道配置代理（保证任何入口改动都全局生效）
    # ==================================================================
    def set_channel_name(self, key: str, name: str):
        if self.config:
            self.config.set_name(key, name)

    def set_color_mode(self, mode: str):
        if self.config:
            self.config.set_color_mode(mode)

    def apply_preset(self, preset_key: str) -> bool:
        return bool(self.config and self.config.apply_preset(preset_key))

    @property
    def name_pool(self) -> list[str]:
        return self.config.name_pool if self.config else []

    # ==================================================================
    #  导出
    # ==================================================================

    # ==================================================================
    def shutdown(self) -> None:
        """退出前收尾：确保落盘完整（Recorder.close 同步排空 + spill 兜底）。"""
        # 关闭录制器（提交剩余数据库缓冲区并写入会话停止时间）
        if self.recorder.is_active:
            self.recorder.close()

        # 关闭数据库连接
        if self._history_db:
            self._history_db.close()


# ======================================================================
#  远端数据解析（纯函数，供加载与落库复用）
# ======================================================================

def parse_remote_temperature_data(data: dict) -> dict:
    """解析远端服务端返回的温度数据为统一结构。

    兼容服务端两种返回格式（见 network/server.py _handle_get_temperature_data）：
    - format='list'：      data['data'] = [{'channel_key', 'timestamp',
                           'temperature', 'is_valid'}, ...]
    - format='by_channel'：data['data'] = {channel_key: [相同 dict 列表], ...}

    返回:
        Dict: {channel_key: [(绝对时间戳, 温度或 nan, is_valid), ...]}
    """
    raw = (data or {}).get('data')
    if isinstance(raw, dict):
        # by_channel 格式
        result = {}
        for ch_key, readings in raw.items():
            result[ch_key] = _readings_to_tuples(readings or [])
        return result
    elif isinstance(raw, list):
        # list 格式：按通道分组
        groups = {}
        for rd in raw:
            if not isinstance(rd, dict):
                continue
            groups.setdefault(rd.get('channel_key'), []).append(rd)
        result = {}
        for ch_key, readings in groups.items():
            result[ch_key] = _readings_to_tuples(readings)
        return result
    return {}


def _readings_to_tuples(readings: list) -> list:
    """把远端 reading 字典列表转换为 (绝对时间戳, 温度, 是否有效) 元组列表。"""
    out = []
    for rd in readings:
        if not isinstance(rd, dict):
            continue
        try:
            ts = float(rd.get('timestamp'))
        except (TypeError, ValueError):
            continue
        temp = rd.get('temperature')
        try:
            temp = float(temp) if temp is not None else float('nan')
        except (TypeError, ValueError):
            temp = float('nan')
        if temp != temp:  # NaN 归一
            temp = float('nan')
        out.append((ts, temp, bool(rd.get('is_valid', True))))
    return out


def merge_channel_readings_to_frames(parsed: dict, last_ts: float,
                                     channel_keys: list) -> tuple:
    """把增量拉取的按通道分组数据合并成按时间对齐的帧序列（纯函数）。

    供远程实时监控使用：服务端时间过滤是双闭区间（>= start_time），
    以 last_ts 作为 start_time 会把边界行重复带回，这里按
    「绝对时间戳 > last_ts」去重；某时间点缺失的通道填 NaN，
    与初始全量加载（load_session_from_remote 只取温度值、无效点为 NaN）
    的口径一致。

    参数:
        parsed: parse_remote_temperature_data 的输出
                {channel_key: [(绝对时间戳, 温度或 nan, is_valid), ...]}
        last_ts: 本地已有的最大绝对时间戳（None 视为 0，即全部保留）
        channel_keys: 目标通道顺序（与内存 Session.channels 对齐）

    返回:
        (times, rows)：times 为升序绝对时间戳列表；rows[i] 为第 i 帧
        各通道值列表（与 channel_keys 对齐，缺失/无效 → NaN）
    """
    if last_ts is None:
        last_ts = 0.0
    n_ch = len(channel_keys)
    frames = {}
    for idx, key in enumerate(channel_keys):
        for ts, temp, _is_valid in (parsed.get(key) or []):
            if ts is None or ts <= last_ts:
                continue  # 边界重复行 / 无效时间戳：丢弃
            row = frames.get(ts)
            if row is None:
                row = [float('nan')] * n_ch
                frames[ts] = row
            row[idx] = float(temp) if temp is not None else float('nan')
    times = sorted(frames)
    rows = [frames[t] for t in times]
    return times, rows


# ======================================================================
#  模块级单例
# ======================================================================
store = DataStore()
