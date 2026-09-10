# -*- coding: utf-8 -*-
"""报警声音播放（winsound 封装，非 Windows / 不可用时静默降级）。

winsound 为 Python 内置 Windows 专有模块，Win7 x86/x64 全兼容、零新依赖。
其他平台或组件缺失时静默跳过，绝不抛异常——报警判定在采集主线程，
声音失败不得阻断采集与界面刷新。

提供两种用法：
- ``play_alarm``：一次性蜂鸣 / 播放 .wav（异步）。
- ``start_alarm_loop`` / ``stop_alarm_loop``：循环蜂鸣，后台线程播放，
  直到调用 stop；用于「持续响直到复位」的工业报警交互。
"""
import threading

# 循环播放的停止信号与线程句柄（模块级单例）
_loop_stop = threading.Event()
_loop_thread = None


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


def start_alarm_loop(freq=2000, on_ms=400, off_ms=400):
    """开始循环蜂鸣（后台线程，直到 stop_alarm_loop）。

    非 Windows 或 winsound 不可用时静默降级；已在响时先停旧再启新。
    """
    global _loop_thread
    stop_alarm_loop()
    try:
        import winsound  # noqa: F401  验证可用
    except Exception:
        return

    _loop_stop.clear()

    def _run():
        try:
            import winsound
            while not _loop_stop.is_set():
                winsound.Beep(freq, on_ms)
                # off_ms 间隙可被 stop 中断；Beep 本身不可中断（最长 on_ms）
                _loop_stop.wait(off_ms / 1000.0)
        except Exception:
            pass

    _loop_thread = threading.Thread(target=_run, daemon=True)
    _loop_thread.start()


def stop_alarm_loop():
    """停止循环蜂鸣（等待当前 Beep 完成，最长约 on_ms）。"""
    global _loop_thread
    _loop_stop.set()
    if _loop_thread is not None:
        try:
            _loop_thread.join(timeout=2.0)
        except Exception:
            pass
        _loop_thread = None
    _loop_stop.clear()   # 重置供下次 start 复用


def alarm_loop_active():
    """循环蜂鸣是否正在进行。"""
    return _loop_thread is not None and _loop_thread.is_alive()
