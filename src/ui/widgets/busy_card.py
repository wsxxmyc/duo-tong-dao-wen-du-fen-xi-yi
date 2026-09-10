# -*- coding: utf-8 -*-
"""BusyCard — 统一加载反馈卡片（居中转圈 + 阶段文字）。

历史会话加载、文件导入、批量导出、远程落库等耗时操作的统一过程
反馈：主窗口客户区正中显示深色半透明圆角卡片 + 旋转圆弧 + 阶段
文字，配等待光标；过程跨阶段时经 set_text 更新文字（并立即重绘），
结束时 finish() 恢复光标并淡出。

设计要点（与 ThemeBusyPopup 同一惯例）：
- 配色跨主题固定（深色半透明 + 白字），任何画布背景上可读；
- begin()/set_text() 内部各 processEvents 一次，保证 UI 线程随后被
  重活冻结前卡片与文字已经画出（否则转圈只闪一瞬）；
- 等待光标成对设置/恢复（_cursor_active 守卫），begin 可重入（重复
  begin 仅换文字，不叠加光标）；
- UI 线程被冻结期间定时器不触发，圆弧可能短暂停帧，卡片与文字始终
  可见（与 ThemeBusyPopup 相同的已知限制）。

用法（后台 worker 流程，begin 与 finish 分居回调）:
    self._busy_card.begin("正在加载历史会话…")
    worker.done → self._busy_card.set_text("正在重建曲线与统计…")
                  ... 重活 ...
                  self._busy_card.finish()

用法（UI 线程同步重活，支持 with 块）:
    with self._busy_card.begin("正在导出…"):
        heavy_render()

依赖:
  - 无 Theme 依赖（配色跨主题固定）。
"""
from PyQt5.QtCore import (
    QEasingCurve,
    QRectF,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect

CARD_BG = QColor(0, 0, 0, 190)          # 中性深色半透明卡片底
BORDER = QColor(255, 255, 255, 38)
SPINNER_COLOR = QColor(255, 255, 255, 230)
TEXT_COLOR = QColor(255, 255, 255)
CARD_RADIUS = 12.0
SPINNER_RADIUS = 16.0
SPINNER_WIDTH = 3.5
ARC_SPAN = 90                            # 圆弧跨度（度）
STEP_DEG = 30                            # 每次 tick 旋转角度
INTERVAL_MS = 40
_CARD_WIDTH = 260
_CARD_HEIGHT = 100
_FADE_IN_MS = 200
_FADE_OUT_MS = 250


class BusyCard(QFrame):
    """主窗口居中的加载反馈卡片（不拦截鼠标事件，配等待光标）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("busyCard")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(_CARD_WIDTH, _CARD_HEIGHT)
        self.hide()

        self._text = ""
        self._angle = 0
        self._cursor_active = False
        self._spinner_timer = QTimer(self, interval=INTERVAL_MS,
                                     timeout=self._tick)
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_fade_done)

    # ---- 公开接口 ----

    def begin(self, text: str, wait_cursor: bool = True) -> "BusyCard":
        """开始加载反馈：居中显示卡片（可重入：仅换文字），返回 self。"""
        self._text = str(text)
        self._fade.stop()
        self._opacity_effect.setOpacity(1.0)
        self._center_at()
        self.show()
        self.raise_()
        self._angle = 0
        if not self._spinner_timer.isActive():
            self._spinner_timer.start()
        if wait_cursor and not self._cursor_active:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._cursor_active = True
        self.update()
        # 让卡片与文字先画出来，再做耗时工作（否则转圈只闪一瞬）
        QApplication.processEvents()
        return self

    def set_text(self, text: str) -> None:
        """更新阶段文字并立即重绘（进入下一阶段前调用）。"""
        text = str(text)
        if text == self._text:
            return
        self._text = text
        self.update()
        QApplication.processEvents()

    def finish(self) -> None:
        """加载结束：恢复光标并淡出退场（未显示时仅确保光标恢复）。"""
        if self._cursor_active:
            QApplication.restoreOverrideCursor()
            self._cursor_active = False
        self._spinner_timer.stop()
        if not self.isVisible():
            return
        self._fade.stop()
        self._fade.setDuration(_FADE_OUT_MS)
        self._fade.setStartValue(self._opacity_effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def is_busy(self) -> bool:
        """卡片是否处于显示中的加载反馈状态。"""
        return self.isVisible()

    # ---- with 块支持（UI 线程同步重活） ----

    def __enter__(self) -> "BusyCard":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.finish()
        return False   # 不吞异常

    # ---- 内部 ----

    def _tick(self) -> None:
        self._angle = (self._angle + STEP_DEG) % 360
        self.update()

    def _center_at(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        x = max((parent.width() - self.width()) // 2, 12)
        y = max((parent.height() - self.height()) // 2, 12)
        self.move(x, y)

    def _on_fade_done(self) -> None:
        if self._opacity_effect.opacity() <= 0.01:
            self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # 半透明圆角卡片 + 细边框
        painter.setPen(Qt.NoPen)
        painter.setBrush(CARD_BG)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1),
                                CARD_RADIUS, CARD_RADIUS)
        painter.setPen(QPen(BORDER, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1),
                                CARD_RADIUS, CARD_RADIUS)
        # 旋转圆弧圆圈（上部居中）
        cx = w / 2.0
        spinner_rect = QRectF(cx - SPINNER_RADIUS, 14.0,
                              SPINNER_RADIUS * 2, SPINNER_RADIUS * 2)
        pen = QPen(SPINNER_COLOR, SPINNER_WIDTH)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(spinner_rect, self._angle * 16, ARC_SPAN * 16)
        # 阶段文字（下部居中）
        painter.setPen(TEXT_COLOR)
        painter.setFont(QFont(self.font().family(), 9))
        painter.drawText(
            self.rect().adjusted(12, 52, -12, -10),
            Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap, self._text)
        painter.end()
