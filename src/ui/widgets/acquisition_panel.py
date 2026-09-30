# -*- coding: utf-8 -*-
"""
AcquisitionPanel — 在线采集标签页组件。
提供串口配置、实时温度曲线、通道面板、状态栏等完整的在线采集 UI。

.. deprecated:: 遗留死组件，全工程无人 import（四层重组后采集 UI 已并入
   main_window / 现行标签页体系）。本文件不参与在线采集链路，其内部对
   ``worker.data_received`` 的连接为死接线。整改计划 P1 明确本批不实施
   该路径；清理删除另立批次，禁止在此基础上新增功能。

依赖:
  - theme.py: Theme 类（颜色常量）
  - acquisition.py: SerialPortManager, AcquisitionProtocol, AcquisitionWorker
  - widgets/chart_tab.py: PlotTab
  - widgets/channel_panel.py: ChannelPanel
  - matplotlib
"""
from __future__ import annotations

import time
import os
import datetime as dt

import numpy as np
from PyQt5.QtCore import Qt, QTimer, QPoint
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QFrame, QScrollArea, QGridLayout,
    QSizePolicy, QGroupBox, QStackedWidget,
)
from PyQt5.QtGui import QFont, QColor, QPainter, QPolygon

from matplotlib.lines import Line2D
from matplotlib.backend_bases import MouseButton

from ui.theme import Theme
from ui.widgets.toggle_switch import ToggleSwitch
from ui.widgets.heartbeat_indicator import (HeartbeatIndicator,
                                         STATE_OFF, STATE_CONNECTING,
                                         STATE_ONLINE, STATE_PAUSED)
from device.acquisition import SerialPortManager, AcquisitionProtocol, AcquisitionWorker
from chart.chart_tab import PlotTab


# ========================================================================
# 通道默认配色（与 HTML 原型一致）
# ========================================================================
CHANNEL_COLORS = [
    "#E74C3C", "#3498DB", "#2ECC71", "#F39C12",
    "#9B59B6", "#1ABC9C", "#E67E22", "#FF6B9D",
]

# 默认通道名（与主窗口名称池一致：设备实际测点名称）
DEFAULT_CHANNEL_NAMES = ["准直保护", "准直镜片", "反射镜片", "场镜保护",
                         "Y轴电机", "X轴电机", "腔体气温", "聚焦组件",
                         "准直组件", "Y轴腔体", "X轴腔体"]

# 预设波特率
BAUDRATES = ["2400", "4800", "9600", "19200", "38400", "57600", "115200"]

# 采样间隔选项 (秒)
SAMPLE_INTERVALS = [("0.5s", 0.5), ("1s", 1), ("2s", 2),
                    ("3s", 3), ("5s", 5), ("10s", 10)]

# 保存间隔选项 (秒)
SAVE_INTERVALS = [("5s", 5), ("10s", 10), ("30s", 30), ("60s", 60)]

# 默认显示窗口（秒）
DEFAULT_WINDOW_SEC = 300   # 5 分钟
MIN_WINDOW_SEC = 30        # 最短 30 秒
MAX_WINDOW_SEC = 1800      # 最长 30 分钟

# 最大存储点数
MAX_POINTS = 5000

# 默认组数
DEFAULT_GROUP_COUNT = 8


class ArrowComboBox(QComboBox):
    """带下拉箭头图标的 QComboBox。

    自定义 QSS（background / border / padding）生效后，Qt 不再绘制
    原生下拉箭头，右侧只剩空白。这里在 paintEvent 中手动绘制一个
    倒三角，颜色跟随主题文字色，样式与全局暗色主题保持一致。
    """

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            # 右侧下拉按钮区域（全局主题预留 24px）中心位置绘制倒三角
            cx = self.rect().right() - 12
            cy = self.rect().center().y() + 1
            half = 5
            points = QPolygon([
                QPoint(cx - half, cy - half // 2),
                QPoint(cx + half, cy - half // 2),
                QPoint(cx, cy + half // 2 + 1),
            ])
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(Theme.TEXT_MUTED))
            painter.drawPolygon(points)
        finally:
            painter.end()


class _ChannelItemWidget(QWidget):
    """通道列表中的单个通道条目（checkbox + 颜色条 + 名称 + 数值 + 峰值 + 状态）。"""

    def __init__(self, ch_idx: int, color: str, name: str,
                 on_toggle, parent=None):
        super().__init__(parent)
        self._ch_idx = ch_idx
        self._on_toggle = on_toggle
        self.setFixedHeight(36)
        self.setStyleSheet(f"""
            _ChannelItemWidget {{
                background: transparent;
                border-bottom: 1px solid {Theme.BG_INPUT};
            }}
            _ChannelItemWidget:hover {{
                background: {Theme.BG_INPUT};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(8)

        # 颜色条
        self._color_bar = QFrame()
        self._color_bar.setFixedSize(4, 20)
        self._color_bar.setStyleSheet(f"background:{color}; border-radius:2px;")
        layout.addWidget(self._color_bar)

        # 启用开关（滑块）
        self._check = ToggleSwitch()
        self._check.setChecked(True)
        self._check.stateChanged.connect(self._on_check_changed)
        layout.addWidget(self._check)

        # 通道名
        self._name_label = QLabel(name)
        self._name_label.setStyleSheet(f"color:{Theme.TEXT}; font-size:{Theme.FONT_SIZE}pt;")
        layout.addWidget(self._name_label, 1)

        # 当前值
        self._value_label = QLabel("---")
        self._value_label.setStyleSheet(
            f"color:{Theme.TEXT}; font-size:{Theme.FONT_SIZE}pt; font-weight:600; min-width:55px;"
        )
        self._value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._value_label)

        # 单位
        unit_label = QLabel("°C")
        unit_label.setStyleSheet(f"color:{Theme.TEXT_MUTED}; font-size:8pt;")
        layout.addWidget(unit_label)

        # 峰值
        self._peak_label = QLabel("")
        self._peak_label.setStyleSheet(f"color:{Theme.RED}; font-size:8pt; min-width:40px;")
        self._peak_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._peak_label)

        # 状态
        self._status_label = QLabel("正常")
        self._status_label.setFixedWidth(34)
        self._status_label.setAlignment(Qt.AlignCenter)
        self._set_status(True)
        layout.addWidget(self._status_label)

    def _on_check_changed(self, state):
        self._on_toggle(self._ch_idx, state == Qt.Checked)

    def update_value(self, value, peak):
        if value is None:
            self._value_label.setText("---")
            self._peak_label.setText("")
            self._set_status(False)
        else:
            self._value_label.setText(f"{value:.1f}")
            self._peak_label.setText(f"\u2191{peak:.1f}")
            self._set_status(True)

    def _set_status(self, ok):
        if ok:
            self._status_label.setText("正常")
            self._status_label.setStyleSheet(
                "background:#1a3a2a; color:#2ecc71; font-size:8pt;"
                " padding:1px 4px; border-radius:3px;"
            )
        else:
            self._status_label.setText("关闭")
            self._status_label.setStyleSheet(
                "background:#3a1a1a; color:#e74c3c; font-size:8pt;"
                " padding:1px 4px; border-radius:3px;"
            )

    @property
    def is_checked(self) -> bool:
        return self._check.isChecked()


class AcquisitionPanel(QWidget):
    """在线采集标签页"""

    def __init__(self, mw):
        """
        Args:
            mw: MainWindow 的引用
        """
        super().__init__()
        self.mw = mw
        self.serial_mgr = SerialPortManager()
        self.protocol = AcquisitionProtocol()
        self.worker = None

        # ── 数据缓冲区 ──
        self._timestamps = []            # list[float], 绝对时间戳 (time.time)
        self._ch_values = []             # list[list[float|None]], 每通道数据
        self._ch_peaks = []              # list[float], 每通道峰值
        self._ch_visible = []            # list[bool], 每通道可见性
        self._ch_names = []              # list[str], 每通道名称
        self._ch_colors = []             # list[str], 每通道颜色
        self._group_count = DEFAULT_GROUP_COUNT
        self._n_channels = self._group_count * 8

        self._init_channel_data()

        # ── 运行时状态 ──
        self._connected = False
        self._running = False
        self._paused = False
        self._start_time = None          # 采集开始时间戳
        self._window_sec = DEFAULT_WINDOW_SEC
        self._data_file_path = None      # 当前保存文件路径
        self._saved_count = 0            # 已保存点数
        self._last_save_time = 0         # 上次保存时间戳
        self._save_interval_sec = 10     # 保存间隔（秒）

        # ── 十字光标状态 ──
        self._crosshair_vline = None
        self._crosshair_hline = None
        self._crosshair_annot = None
        self._last_mouse_xdata = None

        # ── 图表线对象 ──
        self._line_objects = {}

        # ── 定时器（定期同步数据到 mw.dataset） ──
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(2000)    # 每 2 秒同步一次
        self._sync_timer.timeout.connect(self._sync_to_mw_dataset)

        # ── 状态栏更新定时器 ──
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._update_status_bar)

        # ── 保存数据定时器 ──
        self._save_timer = QTimer(self)
        self._save_timer.timeout.connect(self._save_data)

        self._setup_ui()
        # 初始化时自动扫描串口
        QTimer.singleShot(100, self.refresh_ports)

    # ==============================================================
    #  通道数据初始化
    # ==============================================================

    def _init_channel_data(self):
        """初始化通道数据缓冲区。"""
        self._ch_values = [[] for _ in range(self._n_channels)]
        self._ch_peaks = [float("-inf")] * self._n_channels
        self._ch_visible = [True] * self._n_channels
        self._ch_names = list(DEFAULT_CHANNEL_NAMES)
        self._ch_colors = list(CHANNEL_COLORS)
        # 补全颜色（如果通道数超过预定义颜色数）
        while len(self._ch_colors) < self._n_channels:
            # 使用色相循环生成补充颜色
            idx = len(self._ch_colors)
            hue = (idx * 0.618033988749895) % 1.0  # 黄金比例分布
            self._ch_colors.append(f"#{(int(hue * 360 * 6) % 256):02x}00ff")

    # ==============================================================
    #  UI 构建
    # ==============================================================

    def _setup_ui(self):
        """构建完整布局。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部：控制栏
        layout.addLayout(self._build_control_bar())

        # 中间：图表 + 右侧通道面板
        middle = QWidget()
        middle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        middle_layout = QHBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(0)

        # 图表区（占大部分空间）
        chart_widget = self._build_chart_area()
        middle_layout.addWidget(chart_widget, 1)

        # 右侧通道面板
        side_panel = self._build_side_panel()
        side_panel.setFixedWidth(280)
        middle_layout.addWidget(side_panel)

        layout.addWidget(middle, 1)

        # 底部：状态栏
        layout.addLayout(self._build_status_bar())

    # ----------------------------------------------------------
    #  控制栏（顶部）
    # ----------------------------------------------------------

    def _build_control_bar(self) -> QHBoxLayout:
        """串口配置 + 采集控制。"""
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 8, 12, 8)
        bar.setSpacing(8)

        # COM 口
        bar.addWidget(self._make_label("串口"))
        self._combo_com = ArrowComboBox()
        self._combo_com.setMinimumWidth(80)
        self._combo_com.setStyleSheet(f"""
            QComboBox {{
                background:{Theme.BG_INPUT}; color:{Theme.TEXT};
                border:1px solid {Theme.BORDER}; border-radius:4px;
                padding:4px 8px; font-size:{Theme.FONT_SIZE}pt;
            }}
            QComboBox:hover {{ border-color:{Theme.ACCENT}; }}
        """)
        bar.addWidget(self._combo_com)

        # 波特率
        bar.addWidget(self._make_label("波特率"))
        self._combo_baud = ArrowComboBox()
        for b in BAUDRATES:
            self._combo_baud.addItem(b, int(b))
        self._combo_baud.setCurrentText("2400")
        self._combo_baud.setStyleSheet(self._combo_com.styleSheet())
        bar.addWidget(self._combo_baud)

        # 采样间隔
        bar.addWidget(self._make_label("采样"))
        self._combo_interval = ArrowComboBox()
        for text, value in SAMPLE_INTERVALS:
            self._combo_interval.addItem(text, value)
        self._combo_interval.setCurrentText("1s")
        self._combo_interval.setStyleSheet(self._combo_com.styleSheet())
        bar.addWidget(self._combo_interval)

        # 保存间隔
        bar.addWidget(self._make_label("保存"))
        self._combo_save = ArrowComboBox()
        for text, value in SAVE_INTERVALS:
            self._combo_save.addItem(text, value)
        self._combo_save.setCurrentText("10s")
        self._combo_save.setStyleSheet(self._combo_com.styleSheet())
        bar.addWidget(self._combo_save)

        # 弹性空间
        bar.addStretch(1)

        # 连接按钮
        self._btn_connect = QPushButton("连接")
        self._btn_connect.setStyleSheet(Theme.styled_button("success"))
        self._btn_connect.clicked.connect(self._on_connect_clicked)
        bar.addWidget(self._btn_connect)

        # 开始采集按钮
        self._btn_start = QPushButton("▶ 采集")
        self._btn_start.setStyleSheet(Theme.styled_button("primary"))
        self._btn_start.setEnabled(False)
        self._btn_start.clicked.connect(self._on_start_clicked)
        bar.addWidget(self._btn_start)

        # 停止按钮
        self._btn_stop = QPushButton("■ 停止")
        self._btn_stop.setStyleSheet(Theme.styled_button("danger"))
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self.stop_acquisition)
        bar.addWidget(self._btn_stop)

        # 连接状态指示灯（心跳）
        self._hb = HeartbeatIndicator(16)
        self._hb.set_state(STATE_OFF)
        self._conn_label = QLabel("未连接")
        self._conn_label.setStyleSheet(f"color:{Theme.TEXT_MUTED}; font-size:{Theme.FONT_SIZE}pt;")
        bar.addWidget(self._hb)
        bar.addWidget(self._conn_label)

        return bar

    # ----------------------------------------------------------
    #  图表区
    # ----------------------------------------------------------

    def _build_chart_area(self) -> QWidget:
        """实时温度曲线图。"""
        container = QWidget()
        container.setStyleSheet(f"background:{Theme.BG_PRIMARY};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # 标题栏
        title_bar = QHBoxLayout()
        title_label = QLabel("实时温度曲线 —")
        title_label.setStyleSheet(f"color:{Theme.TEXT_MUTED}; font-size:{Theme.FONT_SIZE}pt;")
        title_bar.addWidget(title_label)

        self._window_label = QLabel("最近 5 分钟")
        self._window_label.setStyleSheet(f"color:{Theme.TEXT}; font-weight:600; font-size:{Theme.FONT_SIZE}pt;")
        title_bar.addWidget(self._window_label)

        title_bar.addStretch(1)

        hint_label = QLabel("鼠标悬停显示精确值  ·  滚轮缩放时间窗口")
        hint_label.setStyleSheet(f"color:{Theme.TEXT_MUTED}; font-size:8pt;")
        title_bar.addWidget(hint_label)

        layout.addLayout(title_bar)

        # PlotTab 图表
        self._plot_tab = PlotTab()
        self._plot_tab.fig.set_facecolor(Theme.BG_PRIMARY)
        ax = self._plot_tab.ax
        ax.set_facecolor(Theme.BG_CARD)
        ax.tick_params(colors=Theme.PLOT_TEXT, labelsize=Theme.PLOT_TICK_SIZE)
        ax.spines["bottom"].set_color(Theme.BORDER)
        ax.spines["left"].set_color(Theme.BORDER)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, linestyle="--", alpha=0.3, color=Theme.BG_INPUT)
        ax.set_xlabel("时间（秒）", color=Theme.PLOT_TEXT,
                      fontsize=Theme.PLOT_LABEL_SIZE, family=Theme.font("plot"))
        ax.set_ylabel("温度 (℃)", color=Theme.PLOT_TEXT,
                      fontsize=Theme.PLOT_LABEL_SIZE, family=Theme.font("plot"))
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontfamily(Theme.font("mono"))

        # 初始化占位文本
        ax.text(0.5, 0.5, "连接设备后点击「采集」开始实时测量",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=Theme.PLOT_LABEL_SIZE, family=Theme.font("plot"),
                color=Theme.PLOT_TEXT)

        self._plot_tab.canvas.setStyleSheet(f"background:{Theme.BG_PRIMARY}; border:none;")

        # 连接 matplotlib 事件
        self._plot_tab.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self._plot_tab.canvas.mpl_connect("scroll_event", self._on_scroll)
        self._plot_tab.canvas.mpl_connect("axes_leave_event", self._on_mouse_leave)
        self._plot_tab.canvas.mpl_connect("button_press_event", self._on_mouse_click)

        layout.addWidget(self._plot_tab, 1)

        return container

    # ----------------------------------------------------------
    #  右侧通道面板
    # ----------------------------------------------------------

    def _build_side_panel(self) -> QWidget:
        """右侧通道/数值/信息面板。"""
        panel = QWidget()
        panel.setStyleSheet(f"background:{Theme.BG_CARD}; border-left:1px solid {Theme.BORDER};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标签切换栏
        tab_bar = QHBoxLayout()
        tab_bar.setContentsMargins(0, 0, 0, 0)
        tab_bar.setSpacing(0)

        self._btn_panel_channels = QPushButton("通道")
        self._btn_panel_channels.setCheckable(True)
        self._btn_panel_channels.setChecked(True)
        self._btn_panel_values = QPushButton("数值")
        self._btn_panel_values.setCheckable(True)
        self._btn_panel_info = QPushButton("信息")
        self._btn_panel_info.setCheckable(True)

        panel_btn_style = (
            f"QPushButton {{"
            f"  background:{Theme.BG_CARD}; color:{Theme.TEXT_MUTED};"
            f"  border:none; border-bottom:2px solid transparent;"
            f"  padding:8px; font-size:{Theme.FONT_SIZE}pt;"
            f"}}"
            f"QPushButton:hover {{ color:{Theme.TEXT}; }}"
            f"QPushButton:checked {{"
            f"  color:{Theme.ACCENT}; border-bottom:2px solid {Theme.ACCENT};"
            f"}}"
        )
        self._btn_panel_channels.setStyleSheet(panel_btn_style)
        self._btn_panel_values.setStyleSheet(panel_btn_style)
        self._btn_panel_info.setStyleSheet(panel_btn_style)

        self._btn_panel_channels.clicked.connect(lambda: self._switch_panel_tab(0))
        self._btn_panel_values.clicked.connect(lambda: self._switch_panel_tab(1))
        self._btn_panel_info.clicked.connect(lambda: self._switch_panel_tab(2))

        tab_bar.addWidget(self._btn_panel_channels)
        tab_bar.addWidget(self._btn_panel_values)
        tab_bar.addWidget(self._btn_panel_info)
        layout.addLayout(tab_bar)

        # 堆叠内容面板
        self._panel_stack = QStackedWidget()
        self._panel_stack.setStyleSheet(f"background:{Theme.BG_CARD};")

        # --- 通道列表 ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(f"background:{Theme.BG_CARD}; border:none;")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        ch_list_widget = QWidget()
        self._ch_list_layout = QVBoxLayout(ch_list_widget)
        self._ch_list_layout.setContentsMargins(0, 0, 0, 0)
        self._ch_list_layout.setSpacing(0)
        self._ch_list_layout.addStretch(1)
        scroll.setWidget(ch_list_widget)
        self._panel_stack.addWidget(scroll)

        # --- 数值统计 ---
        self._values_widget = QWidget()
        self._values_layout = QVBoxLayout(self._values_widget)
        self._values_layout.setContentsMargins(12, 12, 12, 12)
        self._values_layout.setSpacing(4)
        placeholder = QLabel("开始采集后自动更新")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet(f"color:{Theme.TEXT_MUTED}; font-size:{Theme.FONT_SIZE}pt;")
        self._values_layout.addWidget(placeholder)
        self._values_layout.addStretch(1)
        self._panel_stack.addWidget(self._values_widget)

        # --- 信息面板 ---
        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(12, 12, 12, 12)
        info_layout.setSpacing(6)
        info_items = [
            ("设备", "多通道温度分析仪"),
            ("通道数", f"{self._n_channels}"),
            ("数据文件", "—"),
            ("已保存", "0 点"),
            ("窗口范围", f"{DEFAULT_WINDOW_SEC // 60}分{'' if DEFAULT_WINDOW_SEC % 60 == 0 else str(DEFAULT_WINDOW_SEC % 60) + '秒'}"),
        ]
        for k, v in info_items:
            row = QHBoxLayout()
            row.addWidget(self._make_label(f"{k}:", color=Theme.TEXT_MUTED))
            lbl = QLabel(v)
            lbl.setStyleSheet(f"color:{Theme.TEXT}; font-size:{Theme.FONT_SIZE}pt; font-weight:600;")
            row.addWidget(lbl, 1)
            info_layout.addLayout(row)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color:{Theme.BORDER};")
        info_layout.addWidget(sep)

        # 操作提示
        tips_title = QLabel("操作提示")
        tips_title.setStyleSheet(f"color:{Theme.TEXT}; font-weight:600; font-size:{Theme.FONT_SIZE}pt;")
        info_layout.addWidget(tips_title)
        for tip in ["· 滚轮缩放时间窗口", "· 鼠标悬停看精确值",
                    "· 勾选/取消通道显隐", "· 双击重置视图"]:
            tip_lbl = QLabel(tip)
            tip_lbl.setStyleSheet(f"color:{Theme.TEXT_MUTED}; font-size:8pt;")
            info_layout.addWidget(tip_lbl)

        info_layout.addStretch(1)
        self._panel_stack.addWidget(info_widget)

        layout.addWidget(self._panel_stack, 1)

        return panel

    # ----------------------------------------------------------
    #  状态栏（底部）
    # ----------------------------------------------------------

    def _build_status_bar(self) -> QHBoxLayout:
        """底部状态信息。"""
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 4, 12, 4)
        bar.setSpacing(12)

        self._status_points = QLabel("采集点数: 0")
        self._status_elapsed = QLabel("耗时: 00:00")
        self._status_file = QLabel("文件: 未保存")
        self._status_temp = QLabel("最新温度: —")

        st = f"color:{Theme.TEXT_MUTED}; font-size:{Theme.FONT_SIZE}pt;"
        self._status_points.setStyleSheet(st)
        self._status_elapsed.setStyleSheet(st)
        self._status_file.setStyleSheet(st)
        self._status_temp.setStyleSheet(st)

        bar.addWidget(self._status_points)
        bar.addWidget(self._make_sep())
        bar.addWidget(self._status_elapsed)
        bar.addWidget(self._make_sep())
        bar.addWidget(self._status_file, 1)
        bar.addWidget(self._make_sep())
        bar.addWidget(self._status_temp)

        return bar

    # ==============================================================
    #  工具方法
    # ==============================================================

    @staticmethod
    def _make_label(text: str, color: str = None) -> QLabel:
        lbl = QLabel(text)
        c = color or Theme.TEXT_MUTED
        lbl.setStyleSheet(f"color:{c}; font-size:{Theme.FONT_SIZE}pt; white-space:nowrap;")
        return lbl

    @staticmethod
    def _make_sep() -> QLabel:
        lbl = QLabel("|")
        lbl.setStyleSheet(f"color:{Theme.BORDER}; font-size:{Theme.FONT_SIZE}pt;")
        return lbl

    def _switch_panel_tab(self, idx: int):
        """切换右侧面板的子标签。"""
        self._panel_stack.setCurrentIndex(idx)
        for i, btn in enumerate([self._btn_panel_channels,
                                  self._btn_panel_values,
                                  self._btn_panel_info]):
            btn.setChecked(i == idx)

    def _make_status_msg(self, text: str, color: str = Theme.TEXT_MUTED) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{color}; font-size:{Theme.FONT_SIZE}pt;")
        return lbl

    # ==============================================================
    #  通道列表 UI 初始化
    # ==============================================================

    def _rebuild_channel_items(self):
        """（重新）创建通道列表中的条目。"""
        # 清除旧条目（保留最后的 stretch）
        while self._ch_list_layout.count() > 1:
            item = self._ch_list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self._channel_items = []
        for i in range(self._n_channels):
            visible = self._ch_visible[i] if i < len(self._ch_visible) else True
            name = self._ch_names[i] if i < len(self._ch_names) else f"通道{i+1}"
            color = self._ch_colors[i] if i < len(self._ch_colors) else CHANNEL_COLORS[i % len(CHANNEL_COLORS)]
            item = _ChannelItemWidget(i, color, name, self._on_channel_toggle)
            item._check.setChecked(visible)
            # 更新初始值
            if i < len(self._ch_values) and self._ch_values[i]:
                peak = self._ch_peaks[i] if self._ch_peaks[i] != float("-inf") else 0
                item.update_value(self._ch_values[i][-1], peak)
            self._ch_list_layout.insertWidget(i, item)
            self._channel_items.append(item)

    # ==============================================================
    #  通道勾选回调
    # ==============================================================

    def _on_channel_toggle(self, ch_idx: int, visible: bool):
        """通道勾选状态变化。"""
        if ch_idx < len(self._ch_visible):
            self._ch_visible[ch_idx] = visible
        self._update_chart_lines()

    # ==============================================================
    #  连接/断开
    # ==============================================================

    def _on_connect_clicked(self):
        """连接/断开按钮点击处理。"""
        if not self._connected:
            self.connect_device()
        else:
            self.disconnect_device()

    def connect_device(self):
        """连接设备。"""
        port = self._combo_com.currentText()
        baud = self._combo_baud.currentData()

        if not port:
            self._show_status("请选择串口")
            return

        self._btn_connect.setEnabled(False)
        self._btn_connect.setText("连接中...")
        self._hb.set_state(STATE_CONNECTING)
        self._conn_label.setText("连接中...")

        # 串口打开操作可能导致界面卡顿，但在主线程中执行
        # 对于真正的串口操作，应在工作线程中异步执行
        ok = self.serial_mgr.open(port, baud)

        if ok:
            self._connected = True
            self._on_connection(True)
            # 串口已打开但设备尚未握手验证，保持"连接中"（看门狗不启动）
            self._hb.set_state(STATE_CONNECTING)
        else:
            self._connected = False
            self._on_connection(False)
            self._show_status(f"无法连接到 {port}，请检查串口和连接线")
            self._on_error(f"串口 {port} 打开失败")

        self._btn_connect.setEnabled(True)

    def disconnect_device(self):
        """断开设备。"""
        if self._running or self._paused:
            self.stop_acquisition()

        self.serial_mgr.close()
        self._connected = False
        self._on_connection(False)

    def _on_connection(self, connected: bool):
        """连接状态变化 → 更新 UI 状态指示灯。"""
        if connected:
            self._btn_connect.setText("断开")
            self._btn_connect.setStyleSheet(Theme.styled_button("warning"))
            # 暂停瞬间迟到的连接成功信号不覆盖已暂停的心跳状态
            if not self._paused:
                self._hb.set_state(STATE_ONLINE)
            self._conn_label.setText("已连接")
            self._btn_start.setEnabled(True)
        else:
            self._btn_connect.setText("连接")
            self._btn_connect.setStyleSheet(Theme.styled_button("success"))
            self._hb.set_state(STATE_OFF)
            # worker 因连续失败断开时，主动结束采集并保存数据（与主窗口对称）
            if self._running or self._paused:
                self.stop_acquisition()
            self._conn_label.setText("未连接")
            self._btn_start.setEnabled(False)
            self._btn_stop.setEnabled(False)

    # ==============================================================
    #  采集控制
    # ==============================================================

    def _on_start_clicked(self):
        """开始/暂停按钮点击处理。"""
        if not self._running:
            self.start_acquisition()
        else:
            self.pause_acquisition()

    def start_acquisition(self):
        """创建 AcquisitionWorker 并启动。"""
        if self.worker is not None:
            return

        interval_ms = int(self._combo_interval.currentData() * 1000)
        self._save_interval_sec = self._combo_save.currentData()

        # 心跳指示：按采样间隔自适应无响应超时
        self._hb.set_timeout(max(1.5, 3.0 * interval_ms / 1000.0))
        self._hb.set_state(STATE_CONNECTING)

        # 生成文件名
        now = dt.datetime.now()
        fn = now.strftime("data_%Y%m%d_%H%M%S.tpx")
        self._data_file_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", fn
        )
        os.makedirs(os.path.dirname(self._data_file_path), exist_ok=True)

        self.worker = AcquisitionWorker(
            self.serial_mgr, self.protocol,
            interval_ms=interval_ms,
            group_count=self._group_count,
        )
        self.worker.data_received.connect(self._on_data)
        self.worker.connection_changed.connect(self._on_connection)
        self.worker.error_occurred.connect(self._on_error)
        self.worker.start()

        self._running = True
        self._paused = False
        self._start_time = time.time()
        self._last_save_time = time.time()
        self._saved_count = 0

        # UI 更新
        self._btn_start.setText("⏸ 暂停")
        self._btn_start.setStyleSheet(Theme.styled_button("warning"))
        self._btn_stop.setEnabled(True)
        self._combo_com.setEnabled(False)
        self._combo_baud.setEnabled(False)

        # 启动定时器
        self._sync_timer.start()
        self._status_timer.start()
        self._save_timer.start(int(self._save_interval_sec * 1000))

        self._show_status("开始在线采集")

    def pause_acquisition(self):
        """暂停采集。"""
        if self.worker:
            self.worker.pause()
        self._paused = True
        self._running = False
        self._btn_start.setText("▶ 继续")
        self._btn_start.setStyleSheet(Theme.styled_button("primary"))

        self._save_timer.stop()
        self._hb.set_state(STATE_PAUSED)

    def resume_acquisition(self):
        """恢复采集。"""
        if self.worker:
            self.worker.resume()
        self._running = True
        self._paused = False
        self._btn_start.setText("⏸ 暂停")
        self._btn_start.setStyleSheet(Theme.styled_button("warning"))

        self._save_timer.start(int(self._save_interval_sec * 1000))
        self._hb.set_state(STATE_ONLINE)

    def stop_acquisition(self):
        """停止采集。"""
        self._running = False
        self._paused = False

        self._sync_timer.stop()
        self._status_timer.stop()
        self._save_timer.stop()

        if self.worker:
            self.worker.stop()
            self.worker = None

        # 如有采集数据，保存剩余数据
        self._save_data_to_file()

        # UI 更新
        self._btn_start.setText("▶ 采集")
        self._btn_start.setStyleSheet(Theme.styled_button("primary"))
        self._btn_stop.setEnabled(False)
        self._combo_com.setEnabled(True)
        self._combo_baud.setEnabled(True)

        self._hb.set_state(STATE_OFF)
        self._show_status("采集已停止")

    # ==============================================================
    #  数据回调
    # ==============================================================

    def _on_data(self, timestamp: float, values: list):
        """收到新数据 → 更新实时曲线 + 状态。"""
        # 存储数据
        self._timestamps.append(timestamp)
        for i, v in enumerate(values):
            if i < len(self._ch_values):
                self._ch_values[i].append(v)
                if v is not None and v > self._ch_peaks[i]:
                    self._ch_peaks[i] = v

        # 限制内存：仅保留最近 MAX_POINTS 个数据点
        if len(self._timestamps) > MAX_POINTS:
            excess = len(self._timestamps) - MAX_POINTS
            self._timestamps = self._timestamps[excess:]
            for arr in self._ch_values:
                if len(arr) > excess:
                    del arr[:excess]

        # 增量更新图表
        self._update_chart_lines()

        # 更新通道面板
        self._update_channel_items()

        # 更新数值面板
        self._update_values_panel()

        # 心跳：每帧数据亮一次
        self._hb.beat()

    def _on_error(self, msg: str):
        """错误处理。"""
        self._show_status(f"错误: {msg}", is_error=True)
        from PyQt5.QtWidgets import QApplication
        # 通过状态栏显示错误（非阻塞）
        if hasattr(self.mw, '_status') and self.mw._status:
            self.mw._status.showMessage(f"采集错误: {msg}", 5000)

    # ==============================================================
    #  图表更新
    # ==============================================================

    def _update_chart_lines(self):
        """增量更新图表上的曲线。"""
        if not self._timestamps:
            return

        ax = self._plot_tab.ax
        ax.cla()
        ax.set_facecolor(Theme.BG_CARD)
        ax.tick_params(colors=Theme.PLOT_TEXT, labelsize=Theme.PLOT_TICK_SIZE)
        ax.spines["bottom"].set_color(Theme.BORDER)
        ax.spines["left"].set_color(Theme.BORDER)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(True, linestyle="--", alpha=0.3, color=Theme.BG_INPUT)
        ax.set_xlabel("时间（秒）", color=Theme.PLOT_TEXT,
                      fontsize=Theme.PLOT_LABEL_SIZE, family=Theme.font("plot"))
        ax.set_ylabel("温度 (℃)", color=Theme.PLOT_TEXT,
                      fontsize=Theme.PLOT_LABEL_SIZE, family=Theme.font("plot"))
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontfamily(Theme.font("mono"))

        # 使用相对时间（以最新数据点为 0）
        now_t = self._timestamps[-1]
        x_min = max(0, now_t - self._window_sec)
        x_min_rel = self._window_sec

        # X 轴刻度标签：负值表示过去
        ax.set_xlim(-self._window_sec, 0)
        from matplotlib.ticker import FuncFormatter
        ax.xaxis.set_major_formatter(FuncFormatter(
            lambda v, _: f"-{int(abs(v))}s" if v < -60 else (f"-{int(abs(v)/60)}m" if v < 0 else "现在")
        ))

        # 绘制每条可见通道
        y_min, y_max = float("inf"), float("-inf")
        has_data = False

        for i in range(self._n_channels):
            if i >= len(self._ch_visible) or not self._ch_visible[i]:
                continue
            if i >= len(self._ch_values) or not self._ch_values[i]:
                continue

            vals = list(self._ch_values[i])
            # 只取窗口内的数据
            n = len(self._timestamps)
            xs = []
            ys = []
            for j in range(n):
                t = self._timestamps[j]
                if t >= x_min and j < len(vals) and vals[j] is not None:
                    rel_t = t - now_t  # 负值：距现在的位置
                    xs.append(rel_t)
                    ys.append(vals[j])

            if not xs:
                continue

            color = self._ch_colors[i] if i < len(self._ch_colors) else CHANNEL_COLORS[i % len(CHANNEL_COLORS)]
            ax.plot(xs, ys, color=color, linewidth=1.5, label=self._ch_names[i] if i < len(self._ch_names) else f"CH{i+1}")

            y_min = min(y_min, min(ys))
            y_max = max(y_max, max(ys))
            has_data = True

        if has_data:
            # Y 轴留边
            margin = max(2, (y_max - y_min) * 0.15)
            ax.set_ylim(y_min - margin, y_max + margin)
        else:
            ax.set_ylim(0, 40)

        # 图例（底部）
        visible_count = sum(1 for v in self._ch_visible if v)
        if visible_count > 0:
            lines = ax.get_lines()
            if lines:
                ax.legend(
                    loc="lower right",
                    bbox_to_anchor=(1.0, 1.0),
                    ncol=min(visible_count, 4),
                    fontsize=7,
                    frameon=False,
                    handlelength=1.2,
                    handletextpad=0.3,
                    columnspacing=0.6,
                )

        # 恢复十字光标
        self._restore_crosshair(ax)

        self._plot_tab.canvas.draw_idle()

    def _restore_crosshair(self, ax):
        """在清除/重绘后恢复十字光标状态。"""
        if self._last_mouse_xdata is not None:
            self._crosshair_vline = ax.axvline(
                self._last_mouse_xdata, color="white", linestyle="--",
                alpha=0.4, linewidth=0.8, visible=True
            )

    # ==============================================================
    #  鼠标事件
    # ==============================================================

    def _on_mouse_move(self, event):
        """鼠标在图表上移动 → 十字光标显示精确温度值。"""
        if event.inaxes != self._plot_tab.ax:
            self._hide_crosshair()
            return

        if event.xdata is None or event.ydata is None:
            return

        ax = self._plot_tab.ax
        self._last_mouse_xdata = event.xdata

        # 移除旧的十字光标
        self._remove_crosshair_artists(ax)

        # 绘制新的十字线
        self._crosshair_vline = ax.axvline(
            event.xdata, color="white", linestyle="--",
            alpha=0.4, linewidth=0.8
        )
        self._crosshair_hline = ax.axhline(
            event.ydata, color="white", linestyle="--",
            alpha=0.4, linewidth=0.8
        )

        # 在右上角显示鼠标所在位置的近似温度值和通道信息
        # 查找鼠标 x 位置附近的实际数据点
        text_parts = []
        if self._timestamps:
            now_t = self._timestamps[-1]
            target_t = now_t + event.xdata  # event.xdata 是相对时间（负值）

            # 找到最近的时间点
            nearest_idx = None
            min_dist = float("inf")
            for j, t in enumerate(self._timestamps):
                dist = abs(t - target_t)
                if dist < min_dist:
                    min_dist = dist
                    nearest_idx = j

            if nearest_idx is not None:
                # 收集该时间点所有可见通道的温度
                for i in range(self._n_channels):
                    if i >= len(self._ch_visible) or not self._ch_visible[i]:
                        continue
                    if i >= len(self._ch_values) or nearest_idx >= len(self._ch_values[i]):
                        continue
                    v = self._ch_values[i][nearest_idx]
                    if v is not None:
                        name = self._ch_names[i] if i < len(self._ch_names) else f"CH{i+1}"
                        color = self._ch_colors[i] if i < len(self._ch_colors) else CHANNEL_COLORS[i % len(CHANNEL_COLORS)]
                        text_parts.append(
                            f"{name}: {v:.1f}\u00b0C"
                        )

        # 标注文本
        annot_text = "\n".join(text_parts) if text_parts else f"Y: {event.ydata:.1f}\u00b0C"
        if self._crosshair_annot:
            self._crosshair_annot.remove()
        self._crosshair_annot = ax.annotate(
            annot_text,
            xy=(0.98, 0.98), xycoords="axes fraction",
            ha="right", va="top",
            fontsize=7, color="white",
            bbox=dict(boxstyle="round,pad=0.3",
                      facecolor="black", edgecolor="gray",
                      alpha=0.75),
            zorder=100,
        )

        self._plot_tab.canvas.draw_idle()

    def _on_mouse_leave(self, event):
        """鼠标离开图表区。"""
        self._hide_crosshair()

    def _on_mouse_click(self, event):
        """鼠标点击（双击重置视图）。"""
        if event.dblclick and event.inaxes == self._plot_tab.ax:
            self._window_sec = DEFAULT_WINDOW_SEC
            self._window_label.setText("最近 5 分钟")
            self._update_chart_lines()

    def _remove_crosshair_artists(self, ax):
        """移除十字光标相关 artist。"""
        for artist in [self._crosshair_vline, self._crosshair_hline]:
            if artist is not None:
                try:
                    artist.remove()
                except (ValueError, AttributeError):
                    pass
        if self._crosshair_annot is not None:
            try:
                self._crosshair_annot.remove()
            except (ValueError, AttributeError):
                pass
        self._crosshair_vline = None
        self._crosshair_hline = None
        self._crosshair_annot = None

    def _hide_crosshair(self):
        """隐藏十字光标。"""
        self._last_mouse_xdata = None
        ax = self._plot_tab.ax
        self._remove_crosshair_artists(ax)
        self._plot_tab.canvas.draw_idle()

    def _on_scroll(self, event):
        """鼠标滚轮缩放时间窗口。"""
        if event.inaxes != self._plot_tab.ax:
            return

        # 滚轮方向：向上滚动缩小（数值变大），向下滚动放大（数值变小）
        delta = 30 if event.button == "up" else -30
        new_window = max(MIN_WINDOW_SEC, min(MAX_WINDOW_SEC, self._window_sec + delta))

        if new_window != self._window_sec:
            self._window_sec = new_window
            minutes = new_window / 60
            if minutes >= 1:
                self._window_label.setText(f"最近 {minutes:.0f} 分钟")
            else:
                self._window_label.setText(f"最近 {new_window:.0f} 秒")
            self._update_chart_lines()

    # ==============================================================
    #  通道列表更新
    # ==============================================================

    def _update_channel_items(self):
        """更新通道列表中的数值。"""
        if not hasattr(self, "_channel_items"):
            return

        for i, item in enumerate(self._channel_items):
            if i < len(self._ch_values) and self._ch_values[i]:
                val = self._ch_values[i][-1]
                peak = self._ch_peaks[i] if self._ch_peaks[i] != float("-inf") else 0
                item.update_value(val, peak)

    # ==============================================================
    #  数值面板更新
    # ==============================================================

    def _update_values_panel(self):
        """更新右侧数值统计面板。"""
        # 清空并重建
        while self._values_layout.count() > 1:
            item = self._values_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not self._timestamps:
            placeholder = QLabel("开始采集后自动更新")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(f"color:{Theme.TEXT_MUTED}; font-size:{Theme.FONT_SIZE}pt;")
            self._values_layout.insertWidget(0, placeholder)
            return

        for i in range(self._n_channels):
            vals = self._ch_values[i] if i < len(self._ch_values) else []
            if not vals:
                continue

            cur = vals[-1]
            if cur is None:
                continue

            name = self._ch_names[i] if i < len(self._ch_names) else f"CH{i+1}"
            color = self._ch_colors[i] if i < len(self._ch_colors) else CHANNEL_COLORS[i % len(CHANNEL_COLORS)]

            # 当前值行
            row1 = QHBoxLayout()
            row1.setContentsMargins(4, 2, 4, 2)
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{color}; font-size:8pt;")
            row1.addWidget(dot)
            row1.addWidget(self._make_label(name, Theme.TEXT))
            row1.addStretch(1)
            val_lbl = QLabel(f"{cur:.1f}°C")
            val_lbl.setStyleSheet(f"color:{Theme.TEXT}; font-weight:600; font-size:{Theme.FONT_SIZE}pt;")
            row1.addWidget(val_lbl)
            self._values_layout.insertLayout(self._values_layout.count() - 1, row1)

            # 峰值行
            peak = max(v for v in vals if v is not None) if any(v is not None for v in vals) else cur
            row2 = QHBoxLayout()
            row2.setContentsMargins(24, 0, 4, 0)
            row2.addWidget(self._make_label("峰值", Theme.TEXT_MUTED))
            row2.addStretch(1)
            peak_lbl = QLabel(f"{peak:.1f}°C")
            peak_lbl.setStyleSheet(f"color:{Theme.RED}; font-size:8pt;")
            row2.addWidget(peak_lbl)
            self._values_layout.insertLayout(self._values_layout.count() - 1, row2)

            # 均值行
            valid_vals = [v for v in vals if v is not None]
            avg = sum(valid_vals) / len(valid_vals) if valid_vals else 0
            row3 = QHBoxLayout()
            row3.setContentsMargins(24, 0, 4, 2)
            row3.addWidget(self._make_label("均值", Theme.TEXT_MUTED))
            row3.addStretch(1)
            avg_lbl = QLabel(f"{avg:.1f}°C")
            avg_lbl.setStyleSheet(f"color:{Theme.ORANGE}; font-size:8pt;")
            row3.addWidget(avg_lbl)
            self._values_layout.insertLayout(self._values_layout.count() - 1, row3)

    # ==============================================================
    #  状态栏更新
    # ==============================================================

    def _update_status_bar(self):
        """更新底部状态栏信息。"""
        n = len(self._timestamps)
        self._status_points.setText(f"采集点数: {n}")

        if self._start_time:
            elapsed = time.time() - self._start_time
            m = int(elapsed // 60)
            s = int(elapsed % 60)
            self._status_elapsed.setText(f"耗时: {m:02d}:{s:02d}")

        if self._data_file_path:
            fn = os.path.basename(self._data_file_path)
            if self._running or self._paused:
                self._status_file.setText(f"文件: {fn}")
            else:
                self._status_file.setText(f"文件: {fn} (已保存)")

        # 最新温度（所有可见通道的平均值）
        if self._timestamps:
            last_vals = []
            for i in range(self._n_channels):
                if i < len(self._ch_visible) and self._ch_visible[i]:
                    if i < len(self._ch_values) and self._ch_values[i]:
                        v = self._ch_values[i][-1]
                        if v is not None:
                            last_vals.append(v)
            if last_vals:
                avg = sum(last_vals) / len(last_vals)
                self._status_temp.setText(
                    f"最新温度: {avg:.1f}°C"
                )

    # ==============================================================
    #  数据保存
    # ==============================================================

    def _on_save_clicked(self):
        """手动保存按钮处理。"""
        self._save_data_to_file()

    def _save_data(self):
        """定时保存回调（由 _save_timer 触发）。"""
        if not self._running:
            return
        now = time.time()
        if now - self._last_save_time >= self._save_interval_sec:
            self._save_data_to_file()
            self._last_save_time = now

    def _save_data_to_file(self):
        """保存采集数据到文件。"""
        if not self._timestamps or self._data_file_path is None:
            return

        try:
            # 构建数据表：时间列 + 每通道一列
            lines = []
            # 表头
            header = ["时间(s)"] + [self._ch_names[i] if i < len(self._ch_names) else f"CH{i+1}"
                                      for i in range(self._n_channels)]
            lines.append("\t".join(header))

            for j in range(len(self._timestamps)):
                row = [f"{self._timestamps[j]:.3f}"]
                for i in range(self._n_channels):
                    if i < len(self._ch_values) and j < len(self._ch_values[i]):
                        v = self._ch_values[i][j]
                        row.append(f"{v:.2f}" if v is not None else "")
                    else:
                        row.append("")
                lines.append("\t".join(row))

            with open(self._data_file_path, "w", encoding="utf-8-sig") as f:
                f.write("\n".join(lines))

            self._saved_count = len(self._timestamps)
            self._status_file.setText(
                f"文件: {os.path.basename(self._data_file_path)} "
                f"(已保存 {self._saved_count} 点)"
            )
        except Exception as e:
            self._on_error(f"保存数据失败: {e}")

    # ==============================================================
    #  数据同步到 mw.dataset
    # ==============================================================

    def _sync_to_mw_dataset(self):
        """将采集数据同步到全局数据集（供其他标签页使用）。"""
        if not self._timestamps or self.mw is None:
            return

        dataset = getattr(self.mw, "dataset", None)
        if dataset is None:
            # 首次同步 - 创建数据集
            # 跳过，因为这里在子线程回调中，可能比较复杂
            # 实际中应由主线程处理
            return

        try:
            # 将时间轴转换为相对秒（从采集开始）
            if len(self._timestamps) < 2:
                return

            t0 = self._timestamps[0]
            time_sec = np.array([t - t0 for t in self._timestamps])

            # 对每个通道追加数据
            for i, ch in enumerate(dataset.channels):
                if i < len(self._ch_values) and len(self._ch_values[i]) > 0:
                    vals = np.array([
                        v if v is not None else np.nan
                        for v in self._ch_values[i]
                    ])
                    # 这里只做简单的替换
                    # 更复杂的合并逻辑需要视具体需求而定
                    pass
        except Exception:
            pass  # 静默失败，不影响采集流程

    # ==============================================================
    #  通知/状态提示
    # ==============================================================

    def _show_status(self, msg: str, is_error: bool = False):
        """在 MainWindow 状态栏显示消息。"""
        if self.mw and hasattr(self.mw, "_status") and self.mw._status:
            self.mw._status.showMessage(msg, 5000)

    # ==============================================================
    #  串口扫描
    # ==============================================================

    def refresh_ports(self):
        """刷新可用串口列表。"""
        current = self._combo_com.currentText()
        self._combo_com.clear()
        ports = self.serial_mgr.list_ports()
        for p in ports:
            self._combo_com.addItem(p)
        if ports:
            idx = self._combo_com.findText(current)
            if idx >= 0:
                self._combo_com.setCurrentIndex(idx)
        else:
            self._combo_com.addItem("（无可用串口）")

    # ==============================================================
    #  外部调用接口
    # ==============================================================

    def on_tab_selected(self):
        """当此标签页被选中时调用。"""
        self.refresh_ports()

    def cleanup(self):
        """清理资源（窗口关闭时调用）。"""
        if self._running or self._paused:
            self.stop_acquisition()
        if self._connected:
            self.disconnect_device()
        self._sync_timer.stop()
        self._status_timer.stop()
        self._save_timer.stop()
