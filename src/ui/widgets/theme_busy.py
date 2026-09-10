# -*- coding: utf-8 -*-
"""ThemeBusyPopup — 主题切换等待提示弹窗。

主题切换在 GUI 线程同步执行（全局 QSS 重生成 + 全部 matplotlib 画布
重绘 + 通道卡片重建 + 各持久弹窗刷新），数据量大时秒级无反馈，用户
无法确认点击是否生效。本弹窗在切换开始前立即显示（半透明圆角卡片 +
旋转圆圈 + 「正在切换主题」文字），切换结束后由 _apply_theme 关闭。

配色刻意不取自 Theme：切换过程中新旧主题交替，固定中性深色底 + 白字
在任何主题任何切换阶段都可读。GUI 线程被重绘阻塞期间定时器不触发，
圆圈可能短暂停帧，但弹窗与文字始终可见（已知限制，见计划书）。
"""
from PyQt5.QtCore import Qt, QRect, QRectF, QTimer
from PyQt5.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QDialog


class ThemeBusyPopup(QDialog):
    """非模态置顶的「正在切换主题」提示：无边框圆角卡片 + 旋转圆弧。"""

    MESSAGE_TEXT = "正在切换主题，请稍候…"
    CARD_BG = QColor(0, 0, 0, 185)      # 中性深色半透明卡片底
    SPINNER_COLOR = QColor(255, 255, 255, 230)
    TEXT_COLOR = QColor(255, 255, 255)
    CARD_RADIUS = 10.0
    SPINNER_RADIUS = 16.0
    SPINNER_WIDTH = 3.5
    ARC_SPAN = 90                       # 圆弧跨度（度）
    STEP_DEG = 30                       # 每次 tick 旋转角度
    INTERVAL_MS = 40

    def __init__(self, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 纯提示不抢焦点：主题切换期间用户无需也不应与任何窗口交互
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setModal(False)
        self.setFixedSize(240, 110)
        self.message = self.MESSAGE_TEXT
        self._angle = 0
        self._timer = QTimer(self, interval=self.INTERVAL_MS,
                             timeout=self._tick)

    def set_message(self, text: str) -> None:
        """设置提示文字（默认「正在切换主题，请稍候…」；供其他启动期
        重活复用，如绿色版数据迁移）。"""
        self.message = str(text)
        self.update()

    @classmethod
    def show_busy(cls) -> "ThemeBusyPopup":
        """创建并显示弹窗（居中于激活窗口/主屏可用区域），返回实例供稍后关闭。"""
        popup = cls()
        popup.show()
        return popup

    @staticmethod
    def _target_rect(exclude=None) -> "QRect":
        """定位基准矩形：优先当前激活窗口，无则回退主屏可用区域。

        多显示器场景下用户可能在副屏操作设置弹窗，居中于激活窗口能让
        提示出现在用户视线所在的屏幕；启动早期/offscreen 等无激活窗口
        时回退主屏（与旧行为一致）。exclude 用于把弹窗自身排除
        （WA_ShowWithoutActivating 下通常不会成为激活窗口，防御性保留）。
        """
        active = QApplication.activeWindow()
        if active is not None and active is not exclude and active.isVisible():
            return active.geometry()
        screen = QGuiApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else QRect()

    def showEvent(self, event):
        super().showEvent(event)
        target = self._target_rect(exclude=self)
        self.move(target.center().x() - self.width() // 2,
                  target.center().y() - self.height() // 2)
        self._angle = 0
        self._timer.start()

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self):
        self._angle = (self._angle + self.STEP_DEG) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 半透明圆角卡片
        painter.setPen(Qt.NoPen)
        painter.setBrush(self.CARD_BG)
        painter.drawRoundedRect(QRectF(self.rect()), self.CARD_RADIUS,
                                self.CARD_RADIUS)
        # 旋转圆弧圆圈（上半区居中）
        cx = self.width() / 2.0
        arc_rect = QRectF(cx - self.SPINNER_RADIUS, 16.0,
                          self.SPINNER_RADIUS * 2, self.SPINNER_RADIUS * 2)
        pen = QPen(self.SPINNER_COLOR, self.SPINNER_WIDTH)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(arc_rect, self._angle * 16, self.ARC_SPAN * 16)
        # 提示文字（下半区居中）
        painter.setPen(self.TEXT_COLOR)
        painter.setFont(QFont(self.font().family(), 10))
        painter.drawText(QRectF(0, 52.0, self.width(), 40.0),
                         Qt.AlignHCenter | Qt.AlignVCenter, self.message)
        painter.end()
