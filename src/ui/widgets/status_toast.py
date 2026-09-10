# -*- coding: utf-8 -*-
"""
StatusToast — 操作结果一级轻提示（居中浮层自动淡出）。

连接 / 采集 / 断开等瞬时动作的成功、失败与进行中反馈都通过它给出
醒目但不打断的提示：语义色边框 + 圆点 + 主文字，主窗口客户区正中
浮层展示，淡入上滑、停留后自动淡出；连续调用时覆盖式重播（旧消息
被新消息替换，不等队列）。

与居中反馈卡片（采集步骤卡 / 加载卡）共存时自动让位：父窗口提供
centerFeedbackCards 属性（可迭代的居中卡片控件）时，提示落到可见
卡片的下方，避免与卡片重叠。

主题相关颜色在每次 show_message 时按当前主题重建（读取 Theme 语义色），
因此主题即时切换后下一条提示即为新主题配色，无需额外挂刷新事件。

依赖:
  - theme.py: Theme（状态栏表面色 / 语义色 / 语义文字）
"""
from PyQt5.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PyQt5.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel

from ui.theme import Theme

# 停留时长（毫秒）
_DURATION_MS = 4000
# 淡入 / 淡出时长（毫秒）
_FADE_IN_MS = 320
_FADE_OUT_MS = 320
# 入场向上滑动距离（像素）
_SLIDE_IN_PX = 12
# 与可见居中卡片的让位间距（像素）
_CARD_GAP_PX = 12
# 语义角色白名单
_ROLES = ("success", "warning", "error", "info")


class StatusToast(QFrame):
    """主窗口居中的操作结果浮层提示（不拦截鼠标事件）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusToast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 14, 7)
        layout.setSpacing(8)
        self._dot = QFrame(self)
        self._dot.setFixedSize(8, 8)
        layout.addWidget(self._dot, 0, Qt.AlignVCenter)
        self._label = QLabel(self)
        layout.addWidget(self._label)

        # 透明度淡入 + 入场上滑（并行动画组），淡出仅透明度
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_fade_done)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._intro = QParallelAnimationGroup(self)
        self._intro.addAnimation(self._fade)
        self._intro.addAnimation(self._slide)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._begin_fade_out)

    # ---- 公开接口 ----

    def show_message(self, text: str, role: str = "info") -> None:
        """显示一条轻提示；已有提示立即被替换（覆盖式重播）。"""
        if role not in _ROLES:
            role = "info"
        color = Theme.semantic_color(role)
        surface = Theme.statusbar_surface()
        fg = Theme.semantic_text(role, surface)
        self._intro.stop()
        self._hide_timer.stop()
        self._dot.setStyleSheet(f"background:{color};border-radius:4px;")
        self._label.setText(text)
        self._label.setStyleSheet(
            f"color:{fg};background:transparent;font-size:11pt;"
            "font-weight:600;letter-spacing:0.4px;")
        self.setStyleSheet(
            f"QFrame#statusToast {{ background:{surface};"
            f"border:1px solid {color};border-radius:10px; }}")
        self.adjustSize()
        final = self._place()
        self.show()
        self.raise_()
        self._opacity_effect.setOpacity(0.0)
        self._fade.setDuration(_FADE_IN_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._slide.setDuration(_FADE_IN_MS)
        self._slide.setStartValue(final + QPoint(0, _SLIDE_IN_PX))
        self._slide.setEndValue(final)
        self._intro.start()
        self._hide_timer.start(_DURATION_MS)

    # ---- 内部 ----

    def _place(self) -> QPoint:
        """主窗口客户区正中（水平 + 垂直居中），窄窗口内收防越界。

        提示落在视野正中央最为醒目，配合淡入上滑形成明确的动画引导；
        父窗口存在可见的居中反馈卡片（centerFeedbackCards 属性）时，
        落到卡片下方避免叠卡；无父窗口时不定位。
        """
        parent = self.parentWidget()
        if parent is None:
            return QPoint(0, 0)
        width = min(self.width(), max(120, parent.width() - 24))
        x = max((parent.width() - width) // 2, 12)
        y = max((parent.height() - self.height()) // 2, 12)
        bottom = y + self.height()
        for card in getattr(parent, "centerFeedbackCards", ()) or ():
            if card is None or card is self:
                continue
            try:
                # isVisibleTo(parent)：父窗口当前是否显示不影响判断
                #（子控件在未 show 的主窗口内仍按"显示态"参与让位）
                if not card.isVisibleTo(parent):
                    continue
                card_geo = card.geometry()
            except RuntimeError:   # 卡片已被 C++ 侧销毁
                continue
            card_bottom = card_geo.y() + card_geo.height()
            if y < card_bottom + _CARD_GAP_PX:
                y = card_bottom + _CARD_GAP_PX
                bottom = y + self.height()
        if bottom > parent.height() - 12:
            y = max(12, parent.height() - self.height() - 12)
        pos = QPoint(x, y)
        self.move(pos)
        return pos

    def _begin_fade_out(self) -> None:
        self._fade.stop()
        self._slide.stop()
        self._fade.setDuration(_FADE_OUT_MS)
        self._fade.setStartValue(self._opacity_effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_done(self) -> None:
        if self._opacity_effect.opacity() <= 0.01:
            self.hide()
