# -*- coding: utf-8 -*-
"""
HeartbeatIndicator — 设备连接心跳指示灯。

每收到一帧温度数据调用 beat()，指示灯亮起后 0.5s 渐隐；
超过自适应超时收不到数据，看门狗将状态切换为 NO_RESPONSE（灰红缓慢闪烁）。
纯 Qt 自绘实心圆点（状态色填充 + 1px 指示边框），无采集模块依赖，
可独立复用与测试。
"""
import time

from PyQt5.QtCore import QPropertyAnimation, QTimer, Qt, pyqtProperty
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from ui.theme import Theme

# 状态常量
STATE_OFF = "off"                  # 离线：灰色常亮
STATE_CONNECTING = "connecting"    # 连接中：橙色常亮
STATE_ONLINE = "online"            # 在线心跳：绿色亮起后渐隐
STATE_NO_RESPONSE = "no_response"  # 无响应：灰红缓慢闪烁
STATE_PAUSED = "paused"            # 已暂停：橙色常亮

_ALL_STATES = (STATE_OFF, STATE_CONNECTING, STATE_ONLINE,
               STATE_NO_RESPONSE, STATE_PAUSED)

# 心跳渐隐时长（毫秒）
_FADE_MS = 500
# 看门狗轮询间隔（毫秒）
_WATCHDOG_TICK_MS = 250


class HeartbeatIndicator(QWidget):
    """设备连接心跳指示灯（16×16 自绘实心圆点 + 指示边框）。"""

    def __init__(self, size: int = 16, parent: QWidget = None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._state = STATE_OFF
        self._intensity = 1.0        # 当前亮度 0..1
        self._last_beat = 0.0        # 最近一次心跳时间（monotonic 秒）
        self._timeout = 3.0          # 无响应超时（秒）
        self._noresp_blink = False   # 无响应闪烁相位

        # 渐隐动画
        self._fade = QPropertyAnimation(self, b"intensity", self)
        self._fade.setDuration(_FADE_MS)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)

        # 看门狗：ONLINE 持续收不到数据 → NO_RESPONSE
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(_WATCHDOG_TICK_MS)
        self._watchdog.timeout.connect(self._on_watchdog_tick)
        self._watchdog.start()

    # ---- 公开接口 ----

    def beat(self) -> None:
        """收到一帧数据：亮起、刷新看门狗，并从连接中/无响应恢复为在线。"""
        self._last_beat = time.monotonic()
        if self._state in (STATE_CONNECTING, STATE_NO_RESPONSE):
            self._state = STATE_ONLINE
        self._intensity = 1.0
        if self._state == STATE_ONLINE:
            self._fade.stop()
            self._fade.start()
        self.update()

    def set_state(self, state: str) -> None:
        """手动切换状态；切到 ONLINE 时重置看门狗计时。"""
        if state not in _ALL_STATES:
            return
        self._state = state
        if state == STATE_ONLINE:
            self._last_beat = time.monotonic()
        self._intensity = 1.0
        self._fade.stop()
        self.update()

    def set_timeout(self, seconds: float) -> None:
        """设置无响应超时（秒），最小 0.5s。"""
        self._timeout = max(0.5, float(seconds))

    @property
    def state(self) -> str:
        """当前状态。"""
        return self._state

    # ---- 动画属性 ----

    def _get_intensity(self) -> float:
        return self._intensity

    def _set_intensity(self, value: float) -> None:
        self._intensity = max(0.0, min(1.0, float(value)))
        self.update()

    intensity = pyqtProperty(float, fget=_get_intensity, fset=_set_intensity)

    # ---- 看门狗 ----

    def _on_watchdog_tick(self) -> None:
        if self._state == STATE_ONLINE:
            if time.monotonic() - self._last_beat > self._timeout:
                self._state = STATE_NO_RESPONSE
                self._fade.stop()
        elif self._state == STATE_NO_RESPONSE:
            self._noresp_blink = not self._noresp_blink
        else:
            return
        self.update()

    # ---- 绘制 ----

    def _base_color(self) -> str:
        return {
            STATE_OFF: Theme.TEXT_MUTED,
            STATE_CONNECTING: Theme.ORANGE,
            STATE_ONLINE: Theme.GREEN,
            STATE_NO_RESPONSE: Theme.RED,
            STATE_PAUSED: Theme.ORANGE,
        }[self._state]

    def paintEvent(self, event) -> None:
        """纯色实心圆点 + 1px 指示边框（G0-2 裁定：不用径向渐变辉光）。

        状态→颜色映射、在线渐隐（alpha 随 intensity）与无响应闪烁
        相位逻辑全部保留，仅去掉渐变辉光的绘制方式。
        """
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        color = self._base_color()
        if self._state == STATE_ONLINE:
            alpha = int(255 * self._intensity)
        elif self._state == STATE_NO_RESPONSE:
            color = Theme.RED if self._noresp_blink else Theme.TEXT_MUTED
            alpha = 255
        else:
            alpha = 255

        dot_rect = rect.adjusted(1, 1, -1, -1)

        # 实心圆点：状态色直接填充（渐隐/闪烁经 alpha 表达）
        fill = QColor(color)
        fill.setAlpha(alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawEllipse(dot_rect)

        # 外圈 1px 指示边框（主题语义色）
        border = QColor(Theme.INDICATOR_BORDER)
        border.setAlpha(180)
        p.setPen(QPen(border, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(dot_rect)
