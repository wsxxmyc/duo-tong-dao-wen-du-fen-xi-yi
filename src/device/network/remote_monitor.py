# -*- coding: utf-8 -*-
"""
RemoteMonitor —— 远程「采集中」会话的实时监控引擎。

使用场景：办公室端（客户端）在「查看历史 → 远程数据」里选中远程服务端
正在采集的会话（stopped_at 为 NULL），启动监控后：

1. 全量拉取当前数据并挂载到主界面（曲线立即显示）；
2. 每 50 秒增量拉取一次（服务端空闲超时 60 秒，50 秒间隔兼作保活心跳，
   用户感知为"约每分钟更新"）；新增数据追加进内存会话，经
   store.data_appended 信号驱动图表增量刷新；
3. 检测到远端会话完结（get_session_list 里该会话 stopped_at 非空）
   或手动停止时，把内存中的完整数据一次性落本地历史库。

线程模型：QObject 亲和主线程；网络拉取在 QThread 后台执行，结果经
队列信号回主线程槽；被替换的线程放入退役列表保活，防止运行中被
Python GC 销毁导致 Qt abort。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal

import logging

logger = logging.getLogger(__name__)

# 监控状态
STATE_STARTING = "starting"      # 全量拉取中
STATE_MONITORING = "monitoring"  # 监控中（定时增量）
STATE_FINISHED = "finished"      # 远端会话已完结，监控结束并落库
STATE_STOPPED = "stopped"        # 用户手动停止，已落库
STATE_ERROR = "error"            # 拉取失败终止

# 增量拉取通道数上限（per-channel limit，服务端语义）
INC_LIMIT = 10000


class _FullFetchWorker(QThread):
    """全量拉取：通道配置 + 全部温度数据（复用现有分页协议）。"""

    done = pyqtSignal(object, object)   # channels, data
    failed = pyqtSignal(str)

    def __init__(self, client, session_id: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._sid = session_id

    def run(self):
        try:
            channels = self._client.get_channel_config(self._sid)
            data = self._client.get_all_temperature_data(self._sid)
            if not channels or not data:
                self.failed.emit("远程设备未返回通道配置或数据")
                return
            self.done.emit(channels, data)
        except Exception as e:
            import traceback
            self.failed.emit(f"{e}\n{traceback.format_exc()}")


class _IncrementWorker(QThread):
    """增量拉取：start_time 之后的新数据 + 会话元数据（完结检测）。"""

    done = pyqtSignal(object, object)   # data, session_info(dict|None)
    failed = pyqtSignal(str)

    def __init__(self, client, session_id: str, channels: list,
                 start_ts: float, parent=None):
        super().__init__(parent)
        self._client = client
        self._sid = session_id
        self._channels = channels
        self._start_ts = start_ts

    def run(self):
        try:
            # 服务端时间过滤为双闭区间且要求 start/end 同时提供；
            # end 给足余量（远端写盘有约 60s 批量延迟）
            data = self._client.get_temperature_data(
                self._sid, channels=self._channels,
                start_time=self._start_ts,
                end_time=time.time() + 300.0,
                limit=INC_LIMIT)
            if data is None:
                self.failed.emit("连接已断开或超时")
                return
            # 顺带取会话元数据（检测 stopped_at 是否已写入）
            session_info = None
            sessions = self._client.get_session_list(limit=200)
            if sessions:
                for s in sessions:
                    if s.get('session_id') == self._sid:
                        session_info = s
                        break
            self.done.emit(data, session_info)
        except Exception as e:
            import traceback
            self.failed.emit(f"{e}\n{traceback.format_exc()}")


class RemoteMonitor(QObject):
    """单个远程采集中会话的监控器（一次只监控一个会话）。

    依赖注入（避免 device/network 反向依赖 device/datastore）：
    store_mod 提供 load_session_from_remote / append_remote_frames /
    parse_remote_temperature_data / merge_channel_readings_to_frames，
    由调用方（MainWindow）传入 services.datastore.store 模块或测试替身。
    """

    # 状态变化（state, message）；供主窗口更新状态栏与远程 Tab
    state_changed = pyqtSignal(str, str)
    # 一轮增量新增行数（供界面提示）
    rows_appended = pyqtSignal(int)

    TICK_MS = 50_000  # 约 1 分钟；小于服务端 60s 空闲超时，兼作保活

    def __init__(self, client, session_info: dict, device_label: str,
                 store_mod, persist_fn: Callable,
                 message_fn: Optional[Callable] = None,
                 parent=None):
        """初始化监控器（不启动；调用 start() 开始）。

        参数:
            client: 已连接的 DataClient（生命周期由调用方管理，监控期间保持连接）
            session_info: 远端会话元数据（含 session_id / started_at / stopped_at）
            device_label: 设备标识 "IP:端口"（用于状态提示与落库来源）
            store_mod: services.datastore.store 模块（或等价替身）
            persist_fn: 落库回调 (session, session_info, device_label) -> str
                        （'imported'/'exists'/'failed'/'skipped'）
            message_fn: 可选状态栏消息回调 (text, timeout_ms)
        """
        super().__init__(parent)
        self._client = client
        self._session_info = dict(session_info or {})
        self._device_label = device_label
        self._store = store_mod
        self._persist_fn = persist_fn
        self._message_fn = message_fn

        self._session_id = self._session_info.get('session_id', '')
        self._session = None            # 挂载后的内存 Session
        self._started_at = None         # 会话开始绝对时间戳
        self._last_ts = 0.0             # 已拉取的最大绝对时间戳
        self._channel_keys = []         # 通道顺序（与内存 Session 对齐）
        self._state = ""
        self._full_worker = None
        self._inc_worker = None
        self._retired = []              # 退役线程保活（防 GC 销毁运行中线程）
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ── 状态查询 ───────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def device_label(self) -> str:
        return self._device_label

    @property
    def monitored_session(self):
        """监控中的内存 Session（挂载完成前为 None）。"""
        return self._session

    def is_active(self) -> bool:
        """是否处于启动中/监控中（尚未结束）。"""
        return self._state in (STATE_STARTING, STATE_MONITORING)

    def is_monitoring(self) -> bool:
        return self._state == STATE_MONITORING

    def stop_and_wait(self, timeout_s: float = 8.0) -> None:
        """停止并同步等待落库完成（应用退出时使用）。

        stop() 的最后一次增量与落库在后台线程执行；退出路径必须
        泵事件循环等待终态，否则落库会被中断丢失。
        """
        self.stop()
        if not self.is_active():
            return
        try:
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
        except Exception:
            app = None
        deadline = time.time() + timeout_s
        while self.is_active() and time.time() < deadline:
            if app is not None:
                app.processEvents()
            time.sleep(0.05)
        self.shutdown()

    # ── 生命周期 ───────────────────────────────────────────────

    def start(self) -> None:
        """启动监控：全量拉取 → 挂载 → 定时增量。"""
        if self.is_active():
            return
        self._set_state(STATE_STARTING,
                        f"正在从 {self._device_label} 获取会话当前数据…")
        self._full_worker = _FullFetchWorker(self._client, self._session_id)
        self._full_worker.done.connect(self._on_full_done)
        self._full_worker.failed.connect(self._on_full_failed)
        self._full_worker.start()

    def stop(self) -> None:
        """手动停止监控：拉最后一次增量后落库（正在启动则直接放弃）。"""
        if not self.is_active():
            return
        if self._state == STATE_STARTING:
            # 全量尚未完成：无数据可落，直接终止
            self._set_state(STATE_STOPPED, "已取消远程监控")
            return
        self._timer.stop()
        self._set_state(STATE_STOPPED, "正在停止监控并保存到本地历史…")
        # 最后一次增量（尽力补齐最新数据），完成后落库
        self._spawn_increment(final=True)

    def shutdown(self) -> None:
        """释放资源：停定时器、等待后台线程退出（不落库）。"""
        self._timer.stop()
        for t in [self._full_worker, self._inc_worker, *self._retired]:
            if t is not None and t.isRunning():
                t.quit()
                t.wait(1000)

    # ── 内部：全量阶段 ─────────────────────────────────────────

    def _on_full_done(self, channels, data) -> None:
        parsed = self._store.parse_remote_temperature_data(data)
        if not parsed:
            self._set_state(STATE_ERROR, "远端会话没有可解析的温度数据")
            return
        # 记录时间基准与通道顺序
        self._started_at = (float(self._session_info.get('started_at') or 0.0)
                            or time.time())
        self._channel_keys = [ch.get('channel_key', f'CH{i + 1}')
                              for i, ch in enumerate(channels)]
        # 挂载会话（activate=True → 主界面立即显示）
        session = self._store.store.load_session_from_remote(
            self._session_info, channels, data, self._device_label)
        if session is None:
            self._set_state(STATE_ERROR, "远端数据解析失败，无法监控")
            return
        self._session = session
        self._last_ts = self._max_ts(parsed)
        self._set_state(
            STATE_MONITORING,
            f"远程监控中：{self._device_label} · 每分钟自动更新"
            f"（当前 {session.n} 条）")
        self._timer.start()

    def _on_full_failed(self, msg: str) -> None:
        self._set_state(STATE_ERROR, f"获取远程数据失败：{msg}")

    @staticmethod
    def _max_ts(parsed: dict) -> float:
        ts = 0.0
        for rows in parsed.values():
            for row in rows or []:
                if row and row[0] and row[0] > ts:
                    ts = row[0]
        return ts

    # ── 内部：增量阶段 ─────────────────────────────────────────

    def _tick(self) -> None:
        """定时增量拉取（监控中每 50 秒一次，兼作保活）。"""
        if not self.is_monitoring() or self._inc_worker is not None:
            return
        self._spawn_increment(final=False)

    def _spawn_increment(self, final: bool) -> None:
        if self._inc_worker is not None and self._inc_worker.isRunning():
            return
        self._inc_worker = _IncrementWorker(
            self._client, self._session_id, self._channel_keys, self._last_ts)
        self._inc_worker.done.connect(
            lambda data, info, _f=final: self._on_increment(data, info, _f))
        self._inc_worker.failed.connect(
            lambda err, _f=final: self._on_increment_failed(err, _f))
        self._inc_worker.start()

    def _retire(self, thread) -> None:
        """把已完成的线程放入退役列表，待其完全结束后移除（防 GC 崩溃）。"""
        if thread is None:
            return
        self._retired.append(thread)
        thread.finished.connect(
            lambda t=thread: self._retired.remove(t)
            if t in self._retired else None)

    def _on_increment(self, data, session_info: Optional[dict],
                      final: bool) -> None:
        worker, self._inc_worker = self._inc_worker, None
        self._retire(worker)

        finished_by_remote = False
        if session_info is not None:
            # 远端已写入停止时间 → 会话完结（或被删除 → session_info 为 None
            # 时无法判定，只有显式非空才算完结）
            if session_info.get('stopped_at'):
                finished_by_remote = True
            else:
                self._session_info.update(session_info)

        # 解析增量并追加（去重边界行；缺通道 NaN）
        appended = 0
        if self._session is not None:
            parsed = self._store.parse_remote_temperature_data(data or {})
            times, rows = self._store.merge_channel_readings_to_frames(
                parsed, self._last_ts, self._channel_keys)
            if times:
                rel_times = [t - self._started_at for t in times]
                _start, appended = self._store.store.append_remote_frames(
                    self._session, rel_times, rows)
                self._last_ts = times[-1]
                self.rows_appended.emit(appended)

        if finished_by_remote:
            self._timer.stop()
            result = self._persist()
            self._set_state(
                STATE_FINISHED,
                f"远程会话已结束（共 {self._session.n if self._session else 0} 条），"
                f"已保存到本地历史{self._persist_hint(result)}")
        elif final:
            # 手动停止的最后一次增量完成 → 落库
            result = self._persist()
            self._set_state(
                STATE_STOPPED,
                f"远程监控已停止（共 {self._session.n if self._session else 0} 条），"
                f"已保存到本地历史{self._persist_hint(result)}")
        elif appended:
            self._message(
                f"远程监控：新增 {appended} 条数据"
                f"（{self._device_label}，共 {self._session.n} 条）", 3000)

    def _on_increment_failed(self, msg: str, final: bool) -> None:
        worker, self._inc_worker = self._inc_worker, None
        self._retire(worker)
        if final:
            # 手动停止时的补拉失败：直接用内存数据落库
            result = self._persist()
            self._set_state(
                STATE_STOPPED,
                f"远程监控已停止（最后增量获取失败：{msg}），已按已有数据保存")
            return
        # 监控中的增量失败：多为连接被断开——保持监控态提示，等待下一轮
        # 重试；连续失败由用户决定停止（避免一次网络抖动就丢监控）
        self._message(f"远程监控增量获取失败（将自动重试）：{msg}", 5000)

    # ── 内部：落库与提示 ───────────────────────────────────────

    def _persist(self) -> str:
        if self._session is None or self._persist_fn is None:
            return 'skipped'
        try:
            return self._persist_fn(
                self._session, self._session_info, self._device_label)
        except Exception as e:
            logger.error(f"远程监控落库失败: {e}")
            return 'failed'

    @staticmethod
    def _persist_hint(result: str) -> str:
        return {
            'imported': '', 'exists': '（本地已有该会话，跳过保存）',
            'failed': '失败！', 'skipped': '',
        }.get(result, '')

    def _message(self, text: str, timeout_ms: int = 0) -> None:
        if self._message_fn is not None:
            try:
                self._message_fn(text, timeout_ms)
            except Exception:
                pass

    def _set_state(self, state: str, message: str) -> None:
        self._state = state
        self.state_changed.emit(state, message)
        self._message(message, 5000 if state in (STATE_FINISHED, STATE_STOPPED,
                                                 STATE_ERROR) else 0)