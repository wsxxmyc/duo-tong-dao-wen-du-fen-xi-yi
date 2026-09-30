# -*- coding: utf-8 -*-
"""报警副作用后台执行器（P0-3：DB 事件写 + Modbus 线圈输出移出 GUI 线程）。

背景（整改计划 §2.2 E5/E6）：旧实现每条报警边沿在 GUI 线程做单条
SQLite INSERT+COMMIT 与 pyserial 同步写；pyserial 3.5 Windows 后端在
``write_timeout=None`` 时为**无上限阻塞**，边沿风暴下直接把事件循环泵死。

本模块提供单一后台线程 + 合并队列：
- 报警事件：按 (session_id, ch_key, atype, status) 合并队列尾部连续重复，
  后台串行 ``insert_alarm_event``（HistoryDatabase 自带 RLock，与批量写互斥安全）。
- Modbus 线圈：只保留**最新目标电平**（写线圈是幂等电平语义，合并无损），
  与上次成功发送一致则不重复发；串口 open/write 全部在本线程，
  write_timeout=0.5s，失败重连一次，连续 3 轮失败降级停发并日志。
GUI 侧 enqueue/set 仅置数据 + wake，绝不等待。
"""
import threading
import time
from collections import deque
from typing import Callable, Optional

from device.acquisition import SerialPortManager
from utils.modbus_rtu import build_write_single_coil

# 线圈后台写参数（电平语义幂等，无需逐事件发送）
COIL_WRITE_TIMEOUT = 0.5    # 单次串口写超时（秒）
COIL_FAIL_LIMIT = 3         # 连续失败达到后降级停发
POLL_INTERVAL_SEC = 0.2     # 无事件时的兜底轮询间隔（防 wake 丢失）


class AlarmSideEffects:
    """报警副作用串行执行器。生命周期随采集启停。

    Args:
        db_provider: 无参回调 → ``HistoryDatabase | None``（每次取用现值，
            兼容运行中建库/重建）。
        cfg_provider: 无参回调 → ``AlarmConfig``（线圈目标串口/从站/线圈号
            运行期可变，取用现值）。
    """

    def __init__(self, db_provider: Callable[[], object],
                 cfg_provider: Callable[[], object]):
        self._db_provider = db_provider
        self._cfg_provider = cfg_provider
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._events = deque()            # insert_alarm_event 位置参数元组
        self._coil_target: Optional[bool] = None
        self._coil_sent: Optional[bool] = None
        self._serial: Optional[SerialPortManager] = None
        self._coil_fail = 0
        self._coil_degraded = False
        self._thread: Optional[threading.Thread] = None
        self.dropped_events = 0           # 观测计数：db 缺失/异常丢弃的事件数

    # ---- GUI 侧接口：只投递，不等待 ----

    def start(self) -> None:
        """启动后台线程（幂等）。重启时复位线圈会话态（设备电平已未知）。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._coil_sent = None
            self._coil_fail = 0
            self._coil_degraded = False
            self._thread = threading.Thread(
                target=self._run, name="AlarmSideEffects", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """停止：最终排空一次队列后有界 join；超时也不挂死调用方。

        join 超时后线程作为 daemon 自行了断（下一轮循环见 _stop 退出），
        串口由其持有线程关闭，本方法不重复关闭以防双关竞态。
        """
        with self._lock:
            th = self._thread
            self._thread = None
        self._stop.set()
        self._wake.set()
        if th is not None:
            th.join(timeout=timeout)

    def enqueue_alarm_event(self, *insert_args) -> None:
        """投递一条 insert_alarm_event 位置参数元组；尾部同键连续重复合并。"""
        row = tuple(insert_args)
        # 行结构：(session_id, ch_key, ts, atype, threshold, actual, status)
        # 合并键：session_id/ch_key/atype/status 相同的连续尾项只保留最新
        with self._lock:
            if self._events and self._events[-1][::6] == row[::6] \
                    and self._events[-1][1] == row[1] \
                    and self._events[-1][3] == row[3]:
                self._events[-1] = row
            else:
                self._events.append(row)
        self._wake.set()

    def set_coil_target(self, on: Optional[bool]) -> None:
        """投递线圈目标电平（None=撤销目标）；只保留最新值，天然合并。"""
        with self._lock:
            if self._coil_target is on:
                return
            self._coil_target = on
        self._wake.set()

    # ---- 后台线程 ----

    def _run(self) -> None:
        while not self._stop.is_set():
            self._drain_events()
            self._sync_coil()
            self._wake.wait(POLL_INTERVAL_SEC)
            self._wake.clear()
        self._drain_events()   # 退出前最后一轮排空
        self._sync_coil()
        self._close_serial()

    def _drain_events(self) -> None:
        while True:
            with self._lock:
                if not self._events:
                    return
                row = self._events.popleft()
            db = self._db_provider()
            if db is None:
                self.dropped_events += 1
                continue
            try:
                db.insert_alarm_event(*row)
            except Exception as exc:
                self.dropped_events += 1
                print(f"[AlarmFX] 写历史失败（已丢弃，计数 {self.dropped_events}）"
                      f"：{exc}", flush=True)

    def _sync_coil(self) -> None:
        with self._lock:
            target = self._coil_target
            if target is None or target == self._coil_sent:
                return
            if self._coil_degraded:
                return
        cfg = self._cfg_provider()
        if not getattr(cfg, "act_serial", False) or not cfg.serial_port:
            return
        ok = self._coil_write_with_retry(cfg, target)
        if ok:
            with self._lock:
                self._coil_sent = target
                self._coil_fail = 0

    def _coil_write_with_retry(self, cfg, on: bool) -> bool:
        """写线圈；失败关闭串口重开一次重试（同一目标幂等，重发安全）。"""
        for attempt in (0, 1):
            if self._stop.is_set():
                return False
            try:
                if not self._ensure_serial(cfg):
                    raise OSError(f"报警串口 {cfg.serial_port} 打开失败")
                frame = build_write_single_coil(
                    cfg.modbus_slave, cfg.modbus_coil, on)
                # SerialPortManager.write 内部吞异常返回 False（含写超时）
                if not self._serial.write(frame):
                    raise OSError("线圈帧写入失败/写超时")
                return True
            except Exception as exc:
                self._close_serial()
                with self._lock:
                    self._coil_fail += 1
                    degraded = self._coil_fail >= COIL_FAIL_LIMIT
                    if degraded:
                        self._coil_degraded = True
                print(f"[AlarmFX] 线圈写失败(第{self._coil_fail}次)：{exc}",
                      flush=True)
                if degraded:
                    print("[AlarmFX] 连续失败达上限，线圈输出降级停发"
                          "（重启采集恢复）", flush=True)
                    return False
        return False

    def _ensure_serial(self, cfg) -> bool:
        if self._serial is None:
            self._serial = SerialPortManager()
        if not self._serial.is_open:
            return self._serial.open(cfg.serial_port, cfg.serial_baud,
                                     write_timeout=COIL_WRITE_TIMEOUT)
        return True

    def _close_serial(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None
