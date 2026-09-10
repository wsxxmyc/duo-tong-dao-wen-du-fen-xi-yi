# -*- coding: utf-8 -*-
"""
PlotTab — 单个 matplotlib 画布标签页组件。
从 app.py 提取，保持完全不变。
"""
import os
os.environ["QT_API"] = "pyqt5"
import matplotlib
matplotlib.use("Qt5Agg")
from PyQt5.QtWidgets import QWidget, QVBoxLayout
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from ui.theme import Theme


class HoverInfoCard(QFrame):
    """整体趋势图画布上的 Qt 悬浮信息卡片。"""

    CARD_MAX_WIDTH = 380

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("trendHoverInfoCard")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.column_gap = Theme.SPACE + 4
        self.data_rows = []
        # hover 每 30ms 刷新一次内容，行控件跨刷新复用，避免频繁销毁重建
        self._row_pool = []
        # 无全局 QSS 时卡内标签继承此字体；列宽测量见 render_metrics
        font = QFont(Theme.font("text"))
        font.setBold(False)
        self.setFont(font)

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(12, 10, 12, 10)
        self._root.setSpacing(6)

        self.time_label = self._make_label("")
        self._root.addWidget(self.time_label)

        self.header_row, self.header_name_label, self.header_value_label = (
            self._build_row("通道名称", "温度(℃)"))

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(5)
        self._rows_layout.addWidget(self.header_row)
        self._root.addLayout(self._rows_layout)
        self.apply_theme()
        self.hide()

    @staticmethod
    def _make_label(text):
        label = QLabel(text)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        font = label.font()
        font.setBold(False)
        label.setFont(font)
        return label

    def render_metrics(self):
        """卡内文本的渲染字体度量：先 polish 名称标签再取其 fontMetrics。

        全局 QSS 的 QWidget 规则把卡片自身字体换成 UI 字体，只有 QLabel
        规则命中正文字体，用卡片 fontMetrics 测量会偏窄约一成；控件
        polish 前 fontMetrics 又按回退字体测量同样不可靠。polish 后的
        标签字体与最终渲染严格一致，列宽测量与省略号生成共用。
        """
        self.header_name_label.ensurePolished()
        return self.header_name_label.fontMetrics()

    def _build_row(self, name, value):
        row = QWidget(self)
        row.setObjectName("trendHoverDataRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        name_host = QWidget(row)
        name_host.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        name_host_layout = QHBoxLayout(name_host)
        name_host_layout.setContentsMargins(0, 0, 0, 0)
        name_host_layout.setSpacing(0)
        name_label = self._make_label(name)
        name_host_layout.addWidget(name_label)
        layout.addWidget(name_host)
        layout.addSpacing(self.column_gap)

        value_label = self._make_label(value)
        value_label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        layout.addWidget(value_label)
        return row, name_label, value_label

    def set_content(self, time_text, rows):
        """更新显示内容；传入的温度字符串已由绘图逻辑格式化。

        行控件从上一轮内容复用，多出的行隐藏备用。列宽测量必须用卡内
        QLabel 的渲染字体（见 render_metrics）：用卡片自身 fontMetrics
        测量会偏窄约一成，末字被宿主控件硬裁且省略号一并裁掉。
        卡片宽度贴合内容（不设固定最小宽度，避免数值列右侧大片空白），
        最长名称超过上限档可用宽度才省略；行控件 1px 边框占 2px，
        计入宽度预算，防止选中行数值列被边框挤窄。
        """
        self.time_label.setText(f"时间：{time_text}")
        metrics = self.render_metrics()
        border = 2  # 卡片左右各 1px 边框
        row_border = 2  # 行控件左右各 1px 边框（选中行可见、普通行透明）
        margins = (self._root.contentsMargins().left()
                   + self._root.contentsMargins().right())
        inner_max = self.CARD_MAX_WIDTH - margins - border - row_border
        name_need = metrics.horizontalAdvance("通道名称")
        value_need = metrics.horizontalAdvance("温度(℃)")
        for row in rows:
            name_need = max(name_need,
                            metrics.horizontalAdvance(str(row["name"])))
            value_need = max(value_need,
                             metrics.horizontalAdvance(str(row["value"])))
        name_column = min(name_need, inner_max - self.column_gap - value_need)
        inner_need = name_column + self.column_gap + value_need
        width = min(inner_need + row_border + margins + border,
                    self.CARD_MAX_WIDTH)
        self.setFixedWidth(width)
        self.name_column_width = min(
            name_need,
            width - margins - border - row_border - self.column_gap - value_need)

        previous = list(self.data_rows)
        active = []
        for index, row in enumerate(rows):
            if index < len(previous):
                entry = previous[index]
            elif self._row_pool:
                entry = self._row_pool.pop()
            else:
                widget = self._build_row("", "")
                self._rows_layout.addWidget(widget[0])
                entry = {"row_widget": widget[0], "name_label": widget[1],
                         "value_label": widget[2]}
            row_widget, name_label, value_label = (entry["row_widget"],
                                                   entry["name_label"],
                                                   entry["value_label"])
            row_widget.show()
            row_widget.setObjectName("trendHoverSelectedRow" if row.get("selected")
                                     else "trendHoverDataRow")
            name_text = str(row["name"])
            if metrics.horizontalAdvance(name_text) > self.name_column_width:
                name_text = metrics.elidedText(
                    name_text, Qt.ElideRight, self.name_column_width)
            name_label.setText(name_text)
            value_label.setText(str(row["value"]))
            row_widget.layout().itemAt(0).widget().setFixedWidth(
                self.name_column_width)
            active.append({
                "row_widget": row_widget,
                "layout": row_widget.layout(),
                "name_label": name_label,
                "value_label": value_label,
            })
        # 倒序入池，回升行数时按原顺序取回
        for entry in reversed(previous[len(rows):]):
            entry["row_widget"].hide()
            self._row_pool.append(entry)
        self.data_rows = active

        self.header_row.layout().itemAt(0).widget().setFixedWidth(
            self.name_column_width)
        # 先强制布局生效再收缩：行数减少时隐藏行仍占旧高度，
        # 直接 adjustSize 会把卡片留在上一帧的高度
        self._root.activate()
        self.adjustSize()
        self.apply_theme()

    def apply_theme(self):
        background = QColor(Theme.PLOT_FACE)
        self.setStyleSheet(
            "QFrame#trendHoverInfoCard {"
            f"background: rgba({background.red()}, {background.green()}, {background.blue()}, 224);"
            f"border: 1px solid {Theme.PLOT_AXIS};"
            f"border-radius: {Theme.CARD_RADIUS}px;"
            "}"
            # 卡内裸 QWidget（名称宿主等无 objectName 子控件）兜底透明，
            # 防止全局 QSS 的 QWidget 页面底色规则透进卡片
            "QWidget {"
            "background: transparent; border: none;"
            "}"
            "QLabel {"
            f"color: {Theme.PLOT_TEXT};"
            f"font-family: {Theme.font('text')};"
            "font-weight: normal;"
            "background: transparent; border: none;"
            "}"
            "QWidget#trendHoverDataRow {"
            "background: transparent; border: 1px solid transparent;"
            "}"
            "QWidget#trendHoverSelectedRow {"
            f"background: transparent; border: 1px solid {Theme.HOVER_HIGHLIGHT};"
            "}"
        )


class PlotTab(QWidget):
    """单个 matplotlib 画布标签页组件。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_mode = "single"
        # 画布组两分区：Figure 底面归 PLOT_FACE，不借用骨架色 BG_CARD
        self.fig = Figure(figsize=(7, 4.5), dpi=Theme.screen_dpi(),
                          facecolor=Theme.PLOT_FACE)
        self.canvas = FigureCanvas(self.fig)
        self.hover_card = HoverInfoCard(self.canvas)
        self.ax = self.fig.add_subplot(111)
        self.ax_right = None
        self.axes = [self.ax]
        lay = QVBoxLayout(self)
        # The chart renderer owns the only intentional chart margins. This
        # host layout must not add a second inset around the canvas.
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.canvas)

    def set_layout(self, mode):
        """切换坐标系布局（single=单轴；dual=左主轴+右辅轴），同模式为 no-op。"""
        if mode not in ("single", "dual"):
            raise ValueError(f"不支持的布局模式: {mode!r}")
        if mode == self.layout_mode:
            return
        self.layout_mode = mode
        self.clear()

    def clear(self):
        self.fig.clear()
        if self.layout_mode == "dual":
            # 左 0.7 主轴 + 右 0.3 辅轴；样式由渲染器每次绘制时统一刷新
            gs = self.fig.add_gridspec(1, 2, width_ratios=(0.7, 0.3), wspace=0.04)
            self.ax = self.fig.add_subplot(gs[0, 0])
            self.ax_right = self.fig.add_subplot(gs[0, 1])
            self.axes = [self.ax, self.ax_right]
        else:
            self.ax = self.fig.add_subplot(111)
            self.ax_right = None
            self.axes = [self.ax]
