# -*- coding: utf-8 -*-
"""统一的自绘方格勾选控件。

保留历史 ``ToggleSwitch`` 类名，避免通道卡片、分组开关、设置页和通道对比
区域修改业务代码。控件继承 ``QCheckBox``，继续兼容 ``setChecked``、
``isChecked``、``toggled`` 和 ``stateChanged`` 等原有接口。

控件完全使用 ``QPainter`` 绘制方框、对勾和半选横线，不依赖系统原生样式、
外部图片或临时资源文件，便于 PyInstaller 的 onedir / onefile、x86 / x64
发布包保持一致。
"""

from __future__ import annotations

from PyQt5.QtCore import QLineF, QPointF, QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QFontMetrics, QPainter, QPalette, QPen
from PyQt5.QtWidgets import QCheckBox

from ui.theme import Theme


class ToggleSwitch(QCheckBox):
    """方格勾选框（保留旧的 ToggleSwitch 名称和调用接口）。"""

    INDICATOR_SIZE = 14       # 方框边长（像素）
    INDICATOR_RADIUS = 3      # 轻微圆角，仍保持方格视觉
    INDICATOR_PEN_WIDTH = 1.2
    CHECK_PEN_WIDTH = 2.0
    SPACING = 6                # 方框与文字之间的间距
    CHIP_PAD_X = 8             # 胶囊底水平内边距（set_chip 启用后计入 sizeHint）
    CHIP_PAD_Y = 3             # 胶囊底垂直内边距
    CHIP_RADIUS = 8            # 胶囊圆角（与状态胶囊 pill 同档）

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        # 胶囊底为通道对比勾选专用的可选外观，默认关闭，
        # 其余使用方（通道卡片/分组/设置页）保持直排无底
        self._chip_bg = None
        self._chip_border = None

    def set_chip(self, bg, border=None) -> None:
        """启用/关闭浅色胶囊底；bg 传 None 关闭并恢复直排几何。"""
        self._chip_bg = bg
        self._chip_border = border or bg
        self.updateGeometry()
        self.update()

    # ------------------------------------------------------------------ #
    #  尺寸与点击状态
    # ------------------------------------------------------------------ #

    def sizeHint(self) -> QSize:
        """返回方框和文字所需的最小尺寸。"""
        fm = QFontMetrics(self.font())
        text_width = fm.horizontalAdvance(self.text()) if self.text() else 0
        width = self.INDICATOR_SIZE
        if text_width:
            width += self.SPACING + text_width
        height = max(self.INDICATOR_SIZE, fm.height()) + 2
        if self._chip_bg is not None:
            width += 2 * self.CHIP_PAD_X
            height += 2 * self.CHIP_PAD_Y
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:
        """与 sizeHint 一致的最小尺寸。"""
        return self.sizeHint()

    def nextCheckState(self) -> None:
        """点击时只在未选中和选中之间切换，半选点击后变为全选。"""
        if self.checkState() == Qt.PartiallyChecked:
            self.setChecked(True)
        else:
            self.setChecked(not self.isChecked())

    # ------------------------------------------------------------------ #
    #  绘制
    # ------------------------------------------------------------------ #

    @staticmethod
    def _disabled_color(color: str) -> QColor:
        """把主题色向辅助文字色靠拢，得到禁用态颜色。"""
        source = QColor(color)
        target = QColor(Theme.TEXT_MUTED)
        return QColor(
            round(source.red() * 0.45 + target.red() * 0.55),
            round(source.green() * 0.45 + target.green() * 0.55),
            round(source.blue() * 0.45 + target.blue() * 0.55),
        )

    def _draw_indicator(self, painter: QPainter, rect: QRectF):
        """绘制未选中、选中或半选状态的方格。"""
        enabled = self.isEnabled()
        state = self.checkState()
        checked = state == Qt.Checked
        partial = state == Qt.PartiallyChecked

        border_color = QColor(Theme.ACCENT if checked or partial
                              else Theme.INDICATOR_BORDER)
        background_color = QColor(Theme.BG_INPUT)
        mark_color = QColor(Theme.ACCENT if checked else Theme.TEXT)

        if not enabled:
            border_color = self._disabled_color(border_color.name())
            background_color = self._disabled_color(background_color.name())
            mark_color = self._disabled_color(mark_color.name())

        painter.setBrush(background_color)
        painter.setPen(QPen(border_color, self.INDICATOR_PEN_WIDTH))
        painter.drawRoundedRect(
            rect, self.INDICATOR_RADIUS, self.INDICATOR_RADIUS)

        if checked:
            # 对勾采用折线绘制，不依赖 PNG 或 Qt 样式引擎。
            pen = QPen(mark_color, self.CHECK_PEN_WIDTH,
                       Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            left = rect.left() + rect.width() * 0.22
            middle_x = rect.left() + rect.width() * 0.45
            right = rect.left() + rect.width() * 0.80
            middle_y = rect.top() + rect.height() * 0.68
            # 注意：本机 PyQt5 的 drawLine(QPointF, QPointF) 重载原生崩溃，
            # 一律经 QLineF 构造走 drawLine(QLineF) 安全路径（见开发台账）
            painter.drawLine(QLineF(
                QPointF(left, rect.top() + rect.height() * 0.50),
                QPointF(middle_x, middle_y)))
            painter.drawLine(QLineF(
                QPointF(middle_x, middle_y),
                QPointF(right, rect.top() + rect.height() * 0.28)))
        elif partial:
            # 半选状态使用中间横线，便于表达“本组部分通道已启用”。
            pen = QPen(mark_color, self.CHECK_PEN_WIDTH,
                       Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            y = rect.center().y()
            painter.drawLine(QLineF(
                QPointF(rect.left() + rect.width() * 0.25, y),
                QPointF(rect.right() - rect.width() * 0.25, y)))

    def paintEvent(self, event) -> None:
        """绘制方格、对勾/横线与文字（QSS 无法表达的内容）。"""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        left = 0
        if self._chip_bg is not None:
            # 浅色胶囊底：整控件铺同色相浅底 + 1px 描边，
            # 让通道色文字在白底卡上仍有可读衬底与色相身份
            chip = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            bg = QColor(self._chip_bg)
            bd = QColor(self._chip_border or self._chip_bg)
            if not self.isEnabled():
                bg = self._disabled_color(bg.name())
                bd = self._disabled_color(bd.name())
            painter.setBrush(bg)
            painter.setPen(QPen(bd, 1))
            painter.drawRoundedRect(chip, self.CHIP_RADIUS, self.CHIP_RADIUS)
            left = self.CHIP_PAD_X

        indicator_size = min(self.INDICATOR_SIZE, self.height() - 2)
        indicator_size = max(1, indicator_size)
        indicator = QRectF(
            left + 0.5,
            (self.height() - indicator_size) / 2.0 + 0.5,
            indicator_size - 1,
            indicator_size - 1,
        )
        self._draw_indicator(painter, indicator)

        if self.text():
            fm = QFontMetrics(self.font())
            text_x = left + self.INDICATOR_SIZE + self.SPACING
            text_y = (self.height() + fm.ascent() - fm.descent()) / 2.0
            text_color = self.palette().color(QPalette.WindowText)
            if not self.isEnabled():
                text_color = self._disabled_color(text_color.name())
            painter.setFont(self.font())
            painter.setPen(text_color)
            painter.drawText(int(text_x), int(text_y), self.text())

        painter.end()

    def set_scale(self, scale) -> None:
        """按原调用接口缩放方框和文字间距。"""
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            scale = 1.0
        scale = max(0.9, min(1.35, scale))
        self.INDICATOR_SIZE = max(12, round(14 * scale))
        self.INDICATOR_RADIUS = max(2, round(3 * scale))
        self.INDICATOR_PEN_WIDTH = max(1.0, 1.2 * scale)
        self.CHECK_PEN_WIDTH = max(1.6, 2.0 * scale)
        self.SPACING = max(4, round(6 * scale))
        self.updateGeometry()
        self.update()
