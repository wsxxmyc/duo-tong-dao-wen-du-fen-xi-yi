# -*- coding: utf-8 -*-
"""报警声音播放（winsound 封装，非 Windows / 不可用时静默降级）。

winsound 为 Python 内置 Windows 专有模块，Win7 x86/x64 全兼容、零新依赖。
其他平台或组件缺失时静默跳过，绝不抛异常——报警判定在采集主线程，
声音失败不得阻断采集与界面刷新。

P0-2 非阻塞化（2026-09-23 报警风暴整改）：旧实现 stop 在 GUI 线程
``join(timeout=2.0)`` 等待不可中断的 ``winsound.Beep``，与报警边沿风暴
叠加成为卡死点之一。现改为"需求标志 + 单一后台线程"：start/stop 只在
锁内置标志、绝不 join；旧线程未退出时由标志自然续响，不会双响叠鸣。

提供两种用法：
- ``play_alarm``：一次性蜂鸣 / 播放 .wav（异步）。
- ``start_alarm_loop`` / ``stop_alarm_loop``：循环蜂鸣（幂等，非阻塞）。
"""
import threading

_loop_lock = threading.Lock()
_loop_wanted = False        # 需求标志：True=应当持续响
_loop_thread = None         # 唯一后台循环线程
_loop_signal = threading.Event()  # set=唤醒两响间隙，让 stop 尽快生效


def play_alarm(sound_file=""):
    """播放一次报警声音（异步 .wav 或短蜂鸣）。"""
    try:
        import winsound
        if sound_file:
            winsound.PlaySound(
                sound_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            winsound.Beep(2000, 300)
    except Exception:
        pass


def _retire_if_current():
    """线程退出清理：仅当自己仍是登记线程时置 None（持锁调用）。"""
    global _loop_thread, _loop_wanted
    if _loop_thread is threading.current_thread():
        _loop_thread = None
    _loop_wanted = False


def _run(freq, on_ms, off_ms):
    while True:
        with _loop_lock:
            if not _loop_wanted:
                _retire_if_current()
                return
            _loop_signal.clear()   # 消费上一轮 stop 唤醒，保住两响间隙
        try:
            import winsound
            winsound.Beep(freq, on_ms)   # 不可中断（最长 on_ms）
        except Exception:
            with _loop_lock:
                _retire_if_current()
            return
        # 间隙等待 stop 信号；Beep 本身不可中断，停止延迟 ≤ on_ms+off_ms
        _loop_signal.wait(off_ms / 1000.0)


def start_alarm_loop(freq=2000, on_ms=400, off_ms=400):
    """启动/维持循环蜂鸣（非阻塞、幂等）。

    已有存活线程时仅置需求标志（由它自然续响，不新建第二线程防双响）；
    无存活线程时新建。GUI 线程不做任何 join/等待。
    """
    global _loop_wanted, _loop_thread
    with _loop_lock:
        _loop_wanted = True
        if _loop_thread is not None and _loop_thread.is_alive():
            return
        _loop_signal.clear()   # 新线程独占信号（旧线程已确认不在）
        _loop_thread = threading.Thread(
            target=_run, args=(freq, on_ms, off_ms), daemon=True)
        _loop_thread.start()


def stop_alarm_loop():
    """停止循环蜂鸣（非阻塞：只撤需求标志并唤醒间隙）。

    后台线程在当前 Beep/间隙结束后自行退出，最长延迟 on_ms+off_ms。
    """
    global _loop_wanted
    with _loop_lock:
        _loop_wanted = False
    _loop_signal.set()


def alarm_loop_active():
    """循环蜂鸣是否处于"应当响"状态（需求标志，即时反映 start/stop）。"""
    with _loop_lock:
        return _loop_wanted
