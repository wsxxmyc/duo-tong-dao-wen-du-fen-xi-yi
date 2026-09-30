# -*- coding: utf-8 -*-
"""跨会话通道趋势对比窗口。

左侧多选历史会话 + 勾选通道，右侧把每个「会话 × 通道」组合画成一条
曲线放在同一张 matplotlib 图上对比（如 A/B/C 会话的 CH1 趋势对比）。
附每条曲线的最高/最低/平均统计表、停住悬停取值（光标停下约 0.2 秒
后才显示取值卡，不遮挡曲线）、滚轮缩放/拖动平移/双击复位、PNG 导出。
X 轴支持相对时间（各会话从 0 分钟对齐，默认）与绝对时间（墙钟时间）
两种口径切换。

数据只读本机历史库（temperature_history.db，所有会话同库按 session_id
区分），全部查询走 ``HistoryDatabase`` 的 ``@_synchronized`` 门面方法
（RLock 串行化，可与采集写入线程并发）；不触碰 ``store.active``，不
影响实时采集。取数与序列组装在 ``_CompareLoadWorker`` 后台线程完成
（C1：UI 线程不跑 DB 查询），仅绘图在 UI 线程。

配色约定：颜色区分会话（Theme 高对比通道色板循环）、线型区分通道
（8 种线型循环），与「同号通道跨会话对比」的主场景对齐。
"""
from __future__ import annotations

import datetime as _dt
import math
import os
import time

os.environ.setdefault("QT_API", "pyqt5")
import matplotlib
matplotlib.use("Qt5Agg")

from typing import Dict, List, Optional

import numpy as np
from PyQt5.QtCore import QEvent, Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea,
    QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from device.database.history_db import HistoryDatabase, default_db_path
from device.datastore.channel import channel_seq_number, seq_label, seq_prefix
from ui.theme import Theme
from chart.chart_tab import HoverInfoCard
from chart.series_sampler import SeriesSampler

# X 轴时间模式
MODE_RELATIVE = "relative"   # 各会话相对分钟（0 起，对齐温升过程）
MODE_ABSOLUTE = "absolute"   # 墙钟时间（仅会话时间重叠时有对比意义）

# 线型按通道循环（颜色按会话循环）：前 4 档标准线型，后 4 档自定义
# 划点序列——通道多于 4 个时同会话曲线仍可区分（同色不同线型）
CHANNEL_LINESTYLES = (
    "-",
    "--",
    "-.",
    ":",
    (0, (6, 1, 1, 1)),          # 长划-点
    (0, (4, 1, 1, 1, 1, 1)),    # 划-双点
    (0, (2, 2)),                # 短划
    (0, (5, 1, 2, 1, 2, 1)),    # 划-点-划-双点
)

# 悬停取值卡刷新节流（与主趋势图 30ms 口径一致）：无节流时高回报率
# 鼠标每秒触发上百次 set_content（内含 adjustSize + setStyleSheet），
# 卡片反复重绘闪烁、文字无法看清
HOVER_THROTTLE_SEC = 0.03

# 悬停取值卡显示时机：鼠标停住 180ms 后才出现。跟随模式下卡片贴着
# 光标移动，既遮挡曲线又随光标频繁换位，读数被打断
HOVER_DWELL_MS = 180

# 取值卡与光标的安全间距：卡片不压住光标附近的曲线
HOVER_CURSOR_GAP_X = 28
HOVER_CURSOR_GAP_Y = 22

# 每条曲线的绘制抽样上限（架构 C7：渲染恒降采样；统计用全量数组）
MAX_DRAW_POINTS = 2000

# 勾选变化后的加载防抖（连续勾选多个会话只触发一次查询）
LOAD_DEBOUNCE_MS = 400

# 相对分钟轴的候选刻度步长（10 秒 ~ 4 天）
REL_STEPS_MINUTES = (
    1.0 / 6, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 240, 360, 720, 1440, 2880,
    5760,
)
# 绝对时间轴的候选刻度步长（秒：10 秒 ~ 7 天）
ABS_STEPS_SECONDS = (
    10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200,
    86400, 172800, 604800,
)

# 图例标签截断长度（会话名·CHn·自定义名 复合标签较长）
LABEL_ELIDE = 16


# ======================================================================
# 纯函数（不依赖 Qt 实例，可直接单元测试）
# ======================================================================
def format_relative_minutes(minutes, pos=None):
    """相对分钟 → 轴刻度文本：<1 小时 MM:SS，≥1 小时 H:MM:SS。

    与 ChartRenderer._format_axis_minutes 同口径（本模块不导入主绘图
    类，独立实现避免拖入 MainWindow 依赖）。
    """
    try:
        total_seconds = int(round(float(minutes) * 60.0))
    except (TypeError, ValueError, OverflowError):
        return ""
    sign = "-" if total_seconds < 0 else ""
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{sign}{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{sign}{minutes:02d}:{seconds:02d}"


def format_relative_duration(minutes):
    """相对分钟 → 悬停取值的完整时分秒文本（H:MM:SS）。"""
    try:
        total_seconds = int(round(float(minutes) * 60.0))
    except (TypeError, ValueError, OverflowError):
        return "时间无效"
    sign = "-" if total_seconds < 0 else ""
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_absolute_seconds(seconds):
    """Unix 秒 → 本地时间完整文本（悬停取值用）。"""
    try:
        return _dt.datetime.fromtimestamp(float(seconds)).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return "时间无效"


def choose_tick_step(span, steps, target=8):
    """按跨度选不超过 target 个刻度的人步长刻度步。"""
    if span <= 0:
        return steps[0]
    for step in steps:
        if span / step <= target:
            return step
    return steps[-1]


def tick_positions_for_step(xmin, xmax, step):
    """返回 [xmin, xmax] 内 step 整数倍处的刻度位置列表。"""
    if step <= 0 or xmax <= xmin:
        return []
    start = math.ceil(xmin / step) * step
    count = int(math.floor((xmax - start) / step)) + 1
    if count <= 0:
        return []
    return [round(start + i * step, 6) for i in range(min(count, 64))]


def relative_axis_ticks(xmin, xmax):
    """相对分钟轴：返回 (刻度位置, 刻度标签)。"""
    step = choose_tick_step(xmax - xmin, REL_STEPS_MINUTES)
    positions = tick_positions_for_step(xmin, xmax, step)
    return positions, [format_relative_minutes(p) for p in positions]


def absolute_axis_ticks(xmin, xmax):
    """绝对时间轴：返回 (刻度位置, 刻度标签)，标签按跨度选粒度。

    刻度位置仍用 Unix 秒数值（不转 matplotlib 日期数，规避 mdates
    epoch/时区随版本的差异），标签本地时区格式化。
    """
    span = xmax - xmin
    step = choose_tick_step(span, ABS_STEPS_SECONDS)
    positions = tick_positions_for_step(xmin, xmax, step)
    if step >= 86400:
        fmt = "%m-%d"
    elif span > 1.5 * 86400:
        fmt = "%m-%d %H:%M"
    else:
        fmt = "%H:%M"
    labels = []
    for p in positions:
        try:
            labels.append(_dt.datetime.fromtimestamp(p).strftime(fmt))
        except (TypeError, ValueError, OverflowError, OSError):
            labels.append("")
    return positions, labels


def session_series_label(session_name, channel_key, channel_name):
    """图例/统计标签：会话名·CHn·自定义名（会话名缺省「未命名会话」）。"""
    name = (session_name or "").strip() or "未命名会话"
    return f"{name}·{seq_label(channel_key, channel_name or '')}"


def elide_text(text, limit=LABEL_ELIDE):
    """超长标签截断加省略号。"""
    text = str(text)
    if limit <= 1 or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def series_stats(values):
    """NaN 安全统计：{max/min/avg/count}；无有效点返回 None。"""
    y = np.asarray(values, dtype=float)
    finite = np.isfinite(y)
    count = int(np.count_nonzero(finite))
    if count == 0:
        return None
    vals = y[finite]
    return {
        "max": float(np.max(vals)),
        "min": float(np.min(vals)),
        "avg": float(np.mean(vals)),
        "count": count,
    }


def session_colors(count, palette=None):
    """会话配色：高对比通道色板循环（绘制时按当前主题取色）。"""
    palette = palette or Theme.high_contrast_colors()
    return [palette[i % len(palette)] for i in range(max(count, 0))]


def channel_linestyles(count):
    """通道线型：实线/虚线/点划线/点线循环。"""
    return [CHANNEL_LINESTYLES[i % len(CHANNEL_LINESTYLES)]
            for i in range(max(count, 0))]


def build_series_payload(hdb, session_ids, channel_keys,
                         max_points=MAX_DRAW_POINTS):
    """后台线程执行：逐会话查询元数据/通道配置/温度数组并组装绘图序列。

    只调用 ``HistoryDatabase`` 的 ``@_synchronized`` 读方法（线程安全）。
    每个序列包含：
    - ``x_rel``/``x_abs``：抽样后的相对分钟 / Unix 秒（同一批抽样点，
      相对↔绝对为仿射变换，抽样按索引分桶与模式无关）
    - ``y``：抽样后的温度（NaN 断线）；``stats``：全量数组统计
    - ``session_index``/``channel_index``：绘制时的配色/线型档位
    会话已被删除（get_session_by_id 返回 None）或无数据时跳过该序列。
    """
    session_pos = {sid: i for i, sid in enumerate(session_ids)}
    channel_pos = {key: j for j, key in enumerate(channel_keys)}
    series = []
    for sid in session_ids:
        db_sess = hdb.get_session_by_id(sid)
        if db_sess is None:
            continue
        channel_cfgs = hdb.get_channel_config(sid)
        if not channel_cfgs:
            continue
        name_by_key = {c.channel_key: (c.channel_name or "")
                       for c in channel_cfgs}
        started_at = float(db_sess.started_at or 0.0)
        keys_present = [k for k in channel_keys if k in name_by_key]
        if not keys_present:
            continue
        arrays = hdb.get_temperature_arrays(sid, channels=keys_present)
        for key in keys_present:
            arr = arrays.get(key)
            if arr is None:
                continue
            ts, temp = arr
            if ts is None or ts.size == 0:
                continue
            x_rel = (np.asarray(ts, dtype=float) - started_at) / 60.0
            xs, ys = SeriesSampler.sample(x_rel, temp, int(max_points))
            series.append({
                "session_id": sid,
                "session_name": db_sess.session_name or "",
                "channel_key": key,
                "channel_name": name_by_key.get(key, ""),
                "label": session_series_label(
                    db_sess.session_name, key, name_by_key.get(key, "")),
                "x_rel": xs,
                "x_abs": xs * 60.0 + started_at,
                "y": ys,
                "stats": series_stats(temp),
                "session_index": session_pos.get(sid, 0),
                "channel_index": channel_pos.get(key, 0),
            })
    return series


# ======================================================================
# 后台加载线程
# ======================================================================
class _CompareLoadWorker(QThread):
    """后台线程组装对比序列（DB 查询 + numpy 组装），结果信号回 UI 线程。"""

    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, hdb, session_ids, channel_keys, parent=None):
        super().__init__(parent)
        self._hdb = hdb
        self._session_ids = list(session_ids)
        self._channel_keys = list(channel_keys)

    def run(self):
        try:
            series = build_series_payload(
                self._hdb, self._session_ids, self._channel_keys)
            self.loaded.emit(series)
        except Exception as e:  # 后台线程兜底：原因经 failed 信号回 UI 呈现
            self.failed.emit(f"{type(e).__name__}: {e}")


# ======================================================================
# 对话框
# ======================================================================
class CrossSessionCompareDialog(QDialog):
    """跨会话通道趋势对比窗口（非模态、实例复用、关闭仅隐藏）。"""

    def __init__(self, db_path=None, parent=None):
        super().__init__(parent)
        self.setObjectName("crossCompareDialog")
        self.setWindowTitle("跨会话通道对比")
        # 允许最大化/最小化与自由缩放（QDialog 默认不带最大化管理按钮），
        # 双击标题栏或点右上角最大化即可全屏铺满
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.setMinimumSize(940, 600)
        self.resize(1280, 800)

        self._db_path = db_path or default_db_path()
        self._history_db: Optional[HistoryDatabase] = None
        self._sessions: List = []              # models.Session 列表
        self._row_by_sid: Dict[str, QListWidgetItem] = {}
        self._sid_by_row: Dict[int, str] = {}
        self._ch_name_cache: Dict[str, Dict[str, str]] = {}
        self._channel_checks: Dict[str, QCheckBox] = {}
        self._channels_built = False           # 首建默认勾选第一个通道
        self._series: List[dict] = []          # 已加载序列（worker 产物）
        self._worker: Optional[_CompareLoadWorker] = None
        self._pending_reload = False
        self._suppress_list_signals = False
        self._panning = False
        self._pan_start_px = 0
        self._pan_start_xlim = (0.0, 1.0)
        self._full_xlim = (0.0, 1.0)           # 双击复位的全览范围
        self._ax = None                        # 首次 _redraw 时创建
        self._hover_throttle_ts = 0.0          # 悬停刷新节流时间戳
        self._hover_last_pos = None            # 最近一次鼠标位置（停住后在此取值）
        self._hover_dwell_timer = QTimer(self) # 停住 HOVER_DWELL_MS 后才显示取值卡
        self._hover_dwell_timer.setSingleShot(True)
        self._hover_dwell_timer.setInterval(HOVER_DWELL_MS)
        self._hover_dwell_timer.timeout.connect(self._show_hover_dwell)

        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(LOAD_DEBOUNCE_MS)
        self._load_timer.timeout.connect(self._start_load)

        self._init_ui()
        self._apply_mpl_theme()
        self.refresh_theme()
        self._open_db()

    # ------------------------------------------------------------------
    #  UI 构建
    # ------------------------------------------------------------------
    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        self._lbl_title = QLabel("跨会话通道对比")
        self._lbl_db = QLabel("")
        header.addWidget(self._lbl_title)
        header.addStretch(1)
        header.addWidget(self._lbl_db)
        root.addLayout(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 900])
        root.addWidget(splitter, 1)

    def _build_left_panel(self):
        """左侧控制区：会话区 / 通道区纵向分割条分隔，可拖动调节占比。"""
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._splitter_left = QSplitter(Qt.Vertical)
        self._splitter_left.setHandleWidth(6)
        self._splitter_left.setChildrenCollapsible(False)

        sessions_group = QWidget()
        sv = QVBoxLayout(sessions_group)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(6)
        row1 = QHBoxLayout()
        lbl_sessions = QLabel("会话（勾选参与对比）")
        self._btn_refresh_list = QPushButton("🔄 刷新列表")
        self._btn_refresh_list.clicked.connect(self._on_refresh_list)
        row1.addWidget(lbl_sessions)
        row1.addStretch(1)
        row1.addWidget(self._btn_refresh_list)
        sv.addLayout(row1)

        self._session_list = QListWidget()
        self._session_list.setWordWrap(True)
        self._session_list.itemChanged.connect(self._on_session_item_changed)
        sv.addWidget(self._session_list, 1)

        channels_group = QWidget()
        cv = QVBoxLayout(channels_group)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(6)
        lbl_channels = QLabel("通道（同号跨会话对比）")
        cv.addWidget(lbl_channels)

        self._channel_scroll = QScrollArea()
        self._channel_scroll.setWidgetResizable(True)
        self._channel_host = QWidget()
        self._channel_grid = QGridLayout(self._channel_host)
        self._channel_grid.setContentsMargins(0, 0, 0, 0)
        self._channel_grid.setSpacing(4)
        self._channel_scroll.setWidget(self._channel_host)
        cv.addWidget(self._channel_scroll, 1)

        self._lbl_channel_hint = QLabel("")
        self._lbl_channel_hint.setWordWrap(True)
        cv.addWidget(self._lbl_channel_hint)

        self._splitter_left.addWidget(sessions_group)
        self._splitter_left.addWidget(channels_group)
        self._splitter_left.setStretchFactor(0, 3)
        self._splitter_left.setStretchFactor(1, 2)
        self._splitter_left.setSizes([300, 200])
        v.addWidget(self._splitter_left)
        return panel

    def _build_right_panel(self):
        """右侧展示区：图表 / 统计表纵向分割条分隔，拖小统计表让趋势图最大化。"""
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        bar = QHBoxLayout()
        self._cmb_mode = QComboBox()
        self._cmb_mode.addItem("相对时间（各会话从 0 分钟对齐）", MODE_RELATIVE)
        self._cmb_mode.addItem("绝对时间（墙钟时间）", MODE_ABSOLUTE)
        self._cmb_mode.currentIndexChanged.connect(self._on_mode_changed)
        bar.addWidget(self._cmb_mode)

        self._btn_refresh_data = QPushButton("🔄 刷新数据")
        self._btn_refresh_data.setToolTip(
            "重新从数据库取数（采集中会话取已落盘的最新部分，"
            "可能滞后实时缓冲一个落盘周期）")
        self._btn_refresh_data.clicked.connect(self._start_load)
        bar.addWidget(self._btn_refresh_data)

        self._btn_export = QPushButton("📤 导出 PNG")
        self._btn_export.clicked.connect(self._export_png)
        bar.addWidget(self._btn_export)

        self._lbl_status = QLabel("")
        self._lbl_status.setWordWrap(True)
        bar.addStretch(1)
        bar.addWidget(self._lbl_status, 1)
        v.addLayout(bar)

        self._splitter_right = QSplitter(Qt.Vertical)
        self._splitter_right.setHandleWidth(6)
        self._splitter_right.setChildrenCollapsible(False)

        # 图表区：画布 + 悬浮取值卡（Qt 控件覆盖在画布上）
        self._chart_host = QWidget()
        chart_layout = QVBoxLayout(self._chart_host)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        self.fig = Figure(figsize=(9, 5), dpi=Theme.screen_dpi(),
                          facecolor=Theme.PLOT_FACE)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.installEventFilter(self)
        self.canvas.setMouseTracking(True)
        chart_layout.addWidget(self.canvas)
        self._hover_card = HoverInfoCard(self._chart_host)

        self._tbl_stats = QTableWidget(0, 6)
        self._tbl_stats.setHorizontalHeaderLabels(
            ["", "曲线", "最高(℃)", "最低(℃)", "平均(℃)", "有效点"])
        self._tbl_stats.verticalHeader().setVisible(False)
        self._tbl_stats.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl_stats.setSelectionMode(QAbstractItemView.NoSelection)
        header = self._tbl_stats.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)

        self._splitter_right.addWidget(self._chart_host)
        self._splitter_right.addWidget(self._tbl_stats)
        self._splitter_right.setStretchFactor(0, 1)
        self._splitter_right.setStretchFactor(1, 0)
        # 初始统计表只占紧凑一行多的高度，趋势图拿走剩余全部空间
        self._splitter_right.setSizes([520, 150])
        v.addWidget(self._splitter_right, 1)
        return panel

    # ------------------------------------------------------------------
    #  数据库 / 会话列表
    # ------------------------------------------------------------------
    def _open_db(self):
        """打开（或重开）本机历史库连接。"""
        if self._history_db is not None:
            try:
                self._history_db.close()
            except Exception:
                pass
            self._history_db = None
        try:
            self._history_db = HistoryDatabase(self._db_path)
        except Exception as e:
            self._history_db = None
            self._set_status(f"无法打开数据库：{e}")
            return
        self._lbl_db.setText(f"数据库：{self._db_path}")
        self._refresh_sessions()

    def _on_refresh_list(self):
        """手动刷新会话列表（含删除/新增的会话）。"""
        self._refresh_sessions()
        self._set_status("会话列表已刷新")

    def _refresh_sessions(self):
        """重建会话列表，保留原有勾选状态。"""
        if self._history_db is None:
            return
        try:
            sessions = self._history_db.get_sessions(limit=5000)
            counts = self._history_db.get_record_counts(
                [s.session_id for s in sessions]) or {}
        except Exception as e:
            self._set_status(f"读取会话列表失败：{e}")
            return

        prev_checked = set(self._checked_session_ids())
        self._suppress_list_signals = True
        self._session_list.clear()
        self._row_by_sid.clear()
        self._sid_by_row.clear()
        self._ch_name_cache.clear()
        self._sessions = sessions
        for s in sessions:
            live = s.stopped_at is None
            if live:
                dur_text = "采集中"
            elif s.duration_seconds:
                dur_text = format_relative_duration(
                    s.duration_seconds / 60.0)
            else:
                dur_text = "—"
            try:
                date_text = _dt.datetime.fromtimestamp(
                    float(s.started_at or 0.0)).strftime("%m-%d %H:%M")
            except (TypeError, ValueError, OverflowError, OSError):
                date_text = "—"
            count = counts.get(s.session_id)
            count_text = f"{count}点" if count is not None else ""
            parts = [s.session_name or "未命名会话", date_text, dur_text,
                     f"{s.channel_count}通道"]
            if count_text:
                parts.append(count_text)
            text = " · ".join(p for p in parts if p)
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked if s.session_id in prev_checked else Qt.Unchecked)
            tip = (f"会话：{s.session_name or s.session_id}\n"
                   f"开始：{date_text}\n来源：{s.source_type}")
            if live:
                tip += "\n采集中：曲线取已落盘部分，点「刷新数据」更新"
            item.setToolTip(tip)
            self._session_list.addItem(item)
            self._row_by_sid[s.session_id] = item
            self._sid_by_row[id(item)] = s.session_id
        self._suppress_list_signals = False
        if not sessions:
            self._set_status("历史库中没有会话")
        self._rebuild_channel_panel()

    def _on_session_item_changed(self, item):
        if self._suppress_list_signals:
            return
        self._rebuild_channel_panel()

    def _checked_session_ids(self):
        """按列表顺序返回勾选的会话 ID。"""
        result = []
        for sid, row in self._row_by_sid.items():
            if row is None:
                continue
            if row.checkState() == Qt.Checked:
                result.append(sid)
        return result

    def _get_channel_names(self, sid):
        """会话通道配置 {key: name}（缓存；查询失败返回 None）。"""
        if sid in self._ch_name_cache:
            return self._ch_name_cache[sid]
        if self._history_db is None:
            return None
        try:
            cfgs = self._history_db.get_channel_config(sid)
        except Exception:
            return None
        names = {c.channel_key: (c.channel_name or "") for c in cfgs}
        self._ch_name_cache[sid] = names
        return names

    # ------------------------------------------------------------------
    #  通道勾选区
    # ------------------------------------------------------------------
    def _rebuild_channel_panel(self):
        """按勾选会话重建通道并集勾选区，保留已勾选通道。"""
        # 清空现有复选框
        while self._channel_grid.count():
            item = self._channel_grid.takeAt(0)
            w = item.widget() if item else None
            if w is not None:
                w.deleteLater()
        self._channel_checks.clear()

        sids = self._checked_session_ids()
        union: Dict[str, List] = {}
        total = 0
        for sid in sids:
            names = self._get_channel_names(sid)
            if not names:
                continue
            total += 1
            for key in names:
                entry = union.setdefault(key, [0, names[key]])
                entry[0] += 1

        prev_checked = set(getattr(self, "_last_checked_channels", set()))
        first_build = not self._channels_built
        keys_sorted = sorted(union, key=channel_seq_number)
        default_key = keys_sorted[0] if keys_sorted else None
        for index, key in enumerate(keys_sorted):
            count, sample_name = union[key]
            cb = QCheckBox(f"{seq_prefix(key)}（{count}/{total}）")
            cb.setToolTip(
                f"{seq_label(key, sample_name)}\n可用：{count}/{total} 个勾选会话")
            checked = key in prev_checked or (
                first_build and key == default_key)
            cb.setChecked(checked)
            cb.toggled.connect(self._on_channel_toggled)
            self._channel_grid.addWidget(cb, index // 3, index % 3)
            self._channel_checks[key] = cb
        # 首建标记只在真正出现过可选通道时置位：构造期空会话不算，
        # 否则默认勾选第一个通道永远不会触发
        if union:
            self._channels_built = True
        self._store_checked_channels()

        if not sids:
            self._lbl_channel_hint.setText("先在上方勾选参与对比的会话")
        elif not union:
            self._lbl_channel_hint.setText("勾选的会话没有通道配置")
        else:
            self._lbl_channel_hint.setText(
                f"{len(union)} 个通道可用（按物理通道号排序）")
        self._schedule_load()

    def _store_checked_channels(self):
        self._last_checked_channels = set(self._checked_channel_keys())

    def _checked_channel_keys(self):
        """按通道面板顺序返回勾选的通道键。"""
        result = []
        keys_sorted = sorted(self._channel_checks, key=channel_seq_number)
        for key in keys_sorted:
            cb = self._channel_checks.get(key)
            if cb is not None and cb.isChecked():
                result.append(key)
        return result

    def _on_channel_toggled(self, _checked):
        self._store_checked_channels()
        self._schedule_load()

    # ------------------------------------------------------------------
    #  加载
    # ------------------------------------------------------------------
    def _schedule_load(self):
        self._load_timer.start()

    def _start_load(self):
        """启动后台加载（防抖后或「刷新数据」直接调用）。"""
        sids = self._checked_session_ids()
        keys = self._checked_channel_keys()
        if not sids or not keys:
            self._series = []
            self._redraw()
            self._update_stats_table()
            self._set_status("请勾选会话与通道")
            return
        if self._history_db is None:
            self._open_db()
            if self._history_db is None:
                return
        if self._worker is not None and self._worker.isRunning():
            # 加载中再次触发（连续勾选/点刷新）：记待重载，完成后自动重启
            self._pending_reload = True
            return
        self._btn_refresh_data.setEnabled(False)
        self._set_status(f"正在加载 {len(sids)} 个会话 × {len(keys)} 个通道…")
        QApplication.processEvents()
        self._worker = _CompareLoadWorker(
            self._history_db, sids, keys, self)
        self._worker.loaded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_load_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_loaded(self, series):
        self._series = list(series or [])
        self._redraw()
        self._update_stats_table()
        n_sessions = len({s["session_id"] for s in self._series})
        self._set_status(
            f"已加载 {n_sessions} 个会话 · {len(self._series)} 条曲线"
            f"（{self._cmb_mode.currentText()}）")

    def _on_load_failed(self, message):
        self._series = []
        self._redraw()
        self._update_stats_table()
        self._set_status(f"加载失败：{message}")

    def _on_worker_finished(self):
        self._btn_refresh_data.setEnabled(True)
        worker = self._worker
        if worker is not None:
            # 释放已结束的线程对象：每次加载都新建 QThread 且挂在对话框
            # 名下，不释放会随刷新次数无限累积
            worker.deleteLater()
        self._worker = None
        if self._pending_reload:
            self._pending_reload = False
            self._schedule_load()

    # ------------------------------------------------------------------
    #  绘图
    # ------------------------------------------------------------------
    def _apply_mpl_theme(self):
        matplotlib.rcParams.update(Theme.mpl_params())
        self.fig.set_facecolor(Theme.PLOT_FACE)

    def _apply_axes_style(self, ax):
        """画布底色/网格/边框：与主趋势视窗同口径（_style_axes_canvas）。"""
        ax.set_facecolor(Theme.PLOT_FACE)
        ax.set_axisbelow(True)
        ax.grid(True, which="major", linestyle="--", color=Theme.PLOT_GRID,
                linewidth=0.9, alpha=1.0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color(Theme.PLOT_AXIS)
        ax.spines["bottom"].set_color(Theme.PLOT_AXIS)
        ax.tick_params(colors=Theme.PLOT_TEXT, labelsize=9)
        ax.xaxis.label.set_color(Theme.PLOT_TEXT)
        ax.yaxis.label.set_color(Theme.PLOT_TEXT)
        ax.xaxis.label.set_fontsize(Theme.PLOT_LABEL_SIZE)
        ax.yaxis.label.set_fontsize(Theme.PLOT_LABEL_SIZE)
        ax.xaxis.label.set_fontfamily(Theme.font("plot"))
        ax.yaxis.label.set_fontfamily(Theme.font("plot"))

    def _current_x(self, s):
        """按当前模式取序列的 x 数组。"""
        return s["x_rel"] if self._cmb_mode.currentData() == MODE_RELATIVE \
            else s["x_abs"]

    def _redraw(self):
        """全量重绘对比图（加载完成/模式切换/主题刷新后调用）。"""
        if self._ax is None:
            self._ax = self.fig.add_subplot(111)
        ax = self._ax
        self._dismiss_hover()      # 重绘后旧取值内容/位置失效
        ax.clear()
        self._apply_axes_style(ax)
        relative = self._cmb_mode.currentData() == MODE_RELATIVE
        ax.set_xlabel("时间 (分钟)" if relative else "时间")
        ax.set_ylabel("温度 (℃)")
        ax.set_title(
            "跨会话通道对比 · " + ("相对时间（各会话从 0 分钟对齐）"
                                if relative else "绝对时间（墙钟时间）"),
            loc="left", fontsize=Theme.PLOT_LABEL_SIZE,
            color=Theme.PLOT_TEXT, fontfamily=Theme.font("plot"),
            fontweight=Theme.PLOT_TITLE_WEIGHT, pad=10)

        series = self._series
        if not series:
            self._dismiss_hover()
            ax.text(0.5, 0.5, "勾选左侧会话与通道后自动加载对比曲线",
                    ha="center", va="center", transform=ax.transAxes,
                    color=Theme.PLOT_TEXT, fontsize=11, alpha=0.8)
            self.fig.canvas.draw_idle()
            return

        colors = session_colors(
            max(s["session_index"] for s in series) + 1)
        styles = channel_linestyles(
            max(s["channel_index"] for s in series) + 1)
        for s in series:
            color = colors[s["session_index"]]
            s["_color"] = color
            ax.plot(self._current_x(s), s["y"], color=color,
                    linestyle=styles[s["channel_index"]], linewidth=1.4,
                    label=elide_text(s["label"]), solid_capstyle="round")

        xall = np.concatenate(
            [self._current_x(s) for s in series if self._current_x(s).size])
        if xall.size:
            xmin, xmax = float(np.min(xall)), float(np.max(xall))
            if xmax - xmin < 1e-9:
                xmax = xmin + 1.0
            pad = (xmax - xmin) * (0.02 if relative else 0.01)
            if relative:
                self._full_xlim = (min(0.0, xmin), xmax + pad)
            else:
                self._full_xlim = (xmin - pad, xmax + pad)
        else:
            self._full_xlim = (0.0, 1.0)
        ax.set_xlim(self._full_xlim)
        self._apply_x_ticks()
        self._autoscale_y()
        legend = ax.legend(
            loc="lower left", bbox_to_anchor=(0.0, 1.06),
            ncol=min(4, len(series)), frameon=False,
            fontsize=Theme.PLOT_LEGEND_SIZE, handlelength=2.4,
            columnspacing=1.2)
        if legend is not None:
            for text in legend.get_texts():
                text.set_color(Theme.PLOT_TEXT)
                text.set_fontfamily(Theme.font("plot"))
        self.fig.tight_layout(rect=(0, 0, 1, 0.94))
        self.fig.canvas.draw_idle()

    def _apply_x_ticks(self):
        """按当前可见范围重设 X 刻度（自选人步长，随缩放变化）。"""
        ax = self._ax
        xmin, xmax = ax.get_xlim()
        if self._cmb_mode.currentData() == MODE_RELATIVE:
            positions, labels = relative_axis_ticks(xmin, xmax)
        else:
            positions, labels = absolute_axis_ticks(xmin, xmax)
        ax.set_xticks(positions)
        ax.set_xticklabels(labels)

    def _autoscale_y(self):
        """按可见 X 窗口自适应 Y 范围（全序列并集，5% 余量）。"""
        ax = self._ax
        xmin, xmax = ax.get_xlim()
        lo, hi = None, None
        for s in self._series:
            x = self._current_x(s)
            y = s["y"]
            if x.size == 0 or y.size == 0:
                continue
            mask = (x >= xmin) & (x <= xmax)
            vals = y[mask]
            finite = vals[np.isfinite(vals)]
            if finite.size == 0:
                continue
            cur_lo, cur_hi = float(np.min(finite)), float(np.max(finite))
            lo = cur_lo if lo is None else min(lo, cur_lo)
            hi = cur_hi if hi is None else max(hi, cur_hi)
        if lo is None or hi is None:
            return
        if hi - lo < 1e-6:
            hi = lo + 1.0
        pad = (hi - lo) * 0.05
        ax.set_ylim(lo - pad, hi + pad)

    def _reset_view(self):
        """双击复位：回到全览范围并重算刻度。"""
        self._dismiss_hover()
        self._ax.set_xlim(self._full_xlim)
        self._apply_x_ticks()
        self._autoscale_y()
        self.fig.canvas.draw_idle()

    # ------------------------------------------------------------------
    #  画布交互（事件过滤器：滚轮缩放 / 拖动平移 / 悬停取值 / 双击复位）
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        # 首帧绘制前（构造后加载防抖窗口内）画布可能收到滚轮/移动事件，
        # 此时 _ax 尚未创建，直接放行避免 AttributeError
        if obj is not self.canvas or self._ax is None:
            return super().eventFilter(obj, event)
        etype = event.type()
        if etype == QEvent.Wheel:
            self._on_wheel(event)
            return True
        if etype == QEvent.MouseButtonDblClick:
            self._reset_view()
            return True
        if etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._dismiss_hover()
            self._panning = True
            self._pan_start_px = event.x()
            self._pan_start_xlim = self._ax.get_xlim()
            return True
        if etype == QEvent.MouseMove:
            if self._panning:
                self._on_pan(event)
            else:
                # 停住才显示：移动中隐藏并重置停住计时，光标停下
                # HOVER_DWELL_MS 后才在停住位置取值显示
                self._hover_card.hide()
                self._hover_last_pos = (event.x(), event.y())
                self._hover_dwell_timer.start()
            return True
        if etype == QEvent.MouseButtonRelease and self._panning:
            self._panning = False
            return True
        if etype == QEvent.Leave:
            self._dismiss_hover()
            return True
        return super().eventFilter(obj, event)

    def _data_x_at(self, px):
        """画布像素 X → 数据 X（y 取画布中部，仅 x 分量有效）。"""
        ax = self._ax
        try:
            pt = ax.transData.inverted().transform(
                (float(px), float(self.canvas.height()) / 2.0))
            return float(pt[0])
        except Exception:
            return None

    def _pixel_x_at(self, data_x):
        """数据 X → 画布像素 X。"""
        try:
            pt = self._ax.transData.transform((float(data_x), 0.0))
            return float(pt[0])
        except Exception:
            return None

    def _on_wheel(self, event):
        self._dismiss_hover()      # 交互期间旧取值内容/位置失效
        ax = self._ax
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 0.8 if delta > 0 else 1.25
        x0, x1 = ax.get_xlim()
        span = x1 - x0
        full_span = self._full_xlim[1] - self._full_xlim[0]
        new_span = span * factor
        # 缩放边界：不小于数据跨度的 1e-4、不大于全览的 1.4 倍
        #（与平移钳制 ±20% 配套：缩得更远会让平移边界推导出空区间）
        if new_span < max(full_span * 1e-4, 1e-6) or new_span > full_span * 1.4:
            return
        xd = self._data_x_at(event.x())
        if xd is None:
            xd = (x0 + x1) / 2.0
        nx0 = xd - (xd - x0) * factor
        nx1 = xd + (x1 - xd) * factor
        ax.set_xlim(nx0, nx1)
        self._apply_x_ticks()
        self._autoscale_y()
        self.fig.canvas.draw_idle()

    def _on_pan(self, event):
        self._dismiss_hover()      # 拖动期间旧取值内容/位置失效
        ax = self._ax
        s0, s1 = self._pan_start_xlim
        width = max(self.canvas.width(), 1)
        frac = (event.x() - self._pan_start_px) / width
        shift = -frac * (s1 - s0)
        # 平移边界：全览范围 ±20% 内
        full_span = self._full_xlim[1] - self._full_xlim[0]
        lo_limit = self._full_xlim[0] - full_span * 0.2
        hi_limit = self._full_xlim[1] + full_span * 0.2
        nx0 = min(max(s0 + shift, lo_limit), hi_limit - (s1 - s0))
        ax.set_xlim(nx0, nx0 + (s1 - s0))
        self._apply_x_ticks()
        self._autoscale_y()
        self.fig.canvas.draw_idle()

    def _dismiss_hover(self):
        """隐藏取值卡并取消停住显示（交互/离开/重绘/关闭时调用）。"""
        self._hover_dwell_timer.stop()
        self._hover_card.hide()
        self._hover_last_pos = None

    def _show_hover_dwell(self):
        """鼠标停住（HOVER_DWELL_MS 无移动）后在停住位置显示取值卡。"""
        pos = self._hover_last_pos
        if pos is None:
            return
        self._update_hover_at(pos[0], pos[1])

    def _update_hover_at(self, px, py):
        """在像素位置 (px, py) 取值：最近 X 采样点处各曲线温度 + 时间文本。

        含 30ms 节流（与主趋势图同口径）：set_content 内含 adjustSize 与
        setStyleSheet，高频重建会闪烁。
        """
        if not self._series:
            self._dismiss_hover()
            return
        now = time.monotonic()
        if now - self._hover_throttle_ts < HOVER_THROTTLE_SEC:
            return
        self._hover_throttle_ts = now
        xd = self._data_x_at(px)
        if xd is None:
            self._dismiss_hover()
            return
        rows = []
        best = None  # (像素距离, rows 下标, 采样下标, 序列)
        for s in self._series:
            x = self._current_x(s)
            if x.size == 0:
                continue
            i = int(np.searchsorted(x, xd))
            i = min(max(i, 0), x.size - 1)
            if 0 < i and abs(x[i - 1] - xd) < abs(x[i] - xd):
                i -= 1
            sp = self._pixel_x_at(x[i])
            dist = abs(sp - px) if sp is not None else float("inf")
            value = float(s["y"][i])
            rows_index = len(rows)   # rows 下标与序列下标在有空序列时错位
            rows.append({
                "name": elide_text(s["label"], 20),
                "value": ("无效" if not math.isfinite(value)
                          else f"{value:.1f} ℃"),
                "selected": False,
            })
            if best is None or dist < best[0]:
                best = (dist, rows_index, i, s)
        if not rows or best is None or best[0] > 40:
            self._dismiss_hover()
            return
        _, best_row, best_i, s = best
        rows[best_row]["selected"] = True
        x = self._current_x(s)[best_i]
        if self._cmb_mode.currentData() == MODE_RELATIVE:
            time_text = format_relative_duration(float(x))
        else:
            time_text = format_absolute_seconds(float(x))
        self._hover_card.set_content(time_text, rows)
        self._position_hover_card(px, py)

    def _position_hover_card(self, px, py):
        """取值卡在光标安全间距外显示，右/下边缘自动翻到左侧/上方。"""
        card = self._hover_card
        host_w = self._chart_host.width()
        host_h = self._chart_host.height()
        x = px + HOVER_CURSOR_GAP_X
        y = py + HOVER_CURSOR_GAP_Y
        if x + card.width() > host_w - 4:
            x = max(px - card.width() - HOVER_CURSOR_GAP_X, 4)
        if y + card.height() > host_h - 4:
            y = max(py - card.height() - HOVER_CURSOR_GAP_Y, 4)
        card.move(int(x), int(y))
        card.show()
        card.raise_()

    # ------------------------------------------------------------------
    #  模式切换 / 统计表 / 状态
    # ------------------------------------------------------------------
    def _on_mode_changed(self, _index):
        self._redraw()

    def _update_stats_table(self):
        tbl = self._tbl_stats
        tbl.setRowCount(len(self._series))
        for row, s in enumerate(self._series):
            color = QColor(s.get("_color", "#ffffff"))
            dot = QTableWidgetItem("●")
            dot.setForeground(color)
            dot.setTextAlignment(Qt.AlignCenter)
            dot.setFlags(Qt.ItemIsEnabled)
            tbl.setItem(row, 0, dot)

            name = QTableWidgetItem(s["label"])
            name.setFlags(Qt.ItemIsEnabled)
            tbl.setItem(row, 1, name)

            stats = s.get("stats")
            if stats:
                texts = (f"{stats['max']:.1f}", f"{stats['min']:.1f}",
                         f"{stats['avg']:.1f}", str(stats["count"]))
            else:
                texts = ("无有效数据", "—", "—", "0")
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled)
                if col in (2, 3, 4):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                tbl.setItem(row, 2 + col, item)

    def _set_status(self, text):
        self._lbl_status.setText(text)

    # ------------------------------------------------------------------
    #  导出
    # ------------------------------------------------------------------
    def _export_png(self):
        if not self._series:
            QMessageBox.information(self, "无法导出", "当前没有可导出的对比曲线")
            return
        default = os.path.join(
            os.path.expanduser("~"),
            f"跨会话对比_{_dt.datetime.now():%Y%m%d_%H%M%S}.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出对比图 PNG", default, "PNG 图片 (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        try:
            from utils.export import ExportManager
            # ExportManager 仅在 save_report_figure 链路上不使用主窗口引用
            ExportManager(None).save_report_figure(
                self.fig, path, export_theme=Theme.active())
            self._set_status(f"已导出：{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    #  主题
    # ------------------------------------------------------------------
    def refresh_theme(self):
        """主题切换后重放局部样式并重绘（主窗口 _apply_theme_chain 调用）。"""
        self._lbl_title.setStyleSheet(
            f"color:{Theme.TEXT};font-size:12pt;font-weight:bold;")
        self._lbl_db.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};font-size:9pt;")
        self._lbl_channel_hint.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};font-size:9pt;")
        self._lbl_status.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};font-size:9pt;")

        list_qss = f"""
            QListWidget {{
                background: {Theme.BG_CARD};
                color: {Theme.TEXT};
                border: 1px solid {Theme.BORDER};
                border-radius: {Theme.RADIUS}px;
                outline: none;
            }}
            QListWidget::item {{ padding: 5px 6px; border: none; }}
            QListWidget::item:hover {{ background: {Theme.BG_HOVER}; }}
            QScrollArea {{ border: 1px solid {Theme.BORDER};
                           border-radius: {Theme.RADIUS}px; }}
            QCheckBox {{ color: {Theme.TEXT}; }}
        """
        self._session_list.setStyleSheet(list_qss)
        self._channel_scroll.setStyleSheet(list_qss)

        face = Theme.table_face()
        if face:
            self._tbl_stats.setStyleSheet(f"""
                QTableWidget {{
                    background: {face['base']};
                    alternate-background-color: {face['alt']};
                    color: {face['text']};
                    border: 1px solid {Theme.BORDER};
                    border-radius: {Theme.RADIUS}px;
                    gridline-color: {Theme.BORDER};
                    selection-background-color: {face['sel_bg']};
                }}
                QHeaderView::section {{
                    background: {face['header_bg']};
                    color: {face['header_text']};
                    border: none;
                    border-bottom: 1px solid {Theme.BORDER};
                    padding: 5px;
                    font-weight: bold;
                }}
            """)
        self._btn_refresh_list.setStyleSheet(Theme.styled_button())
        self._btn_refresh_data.setStyleSheet(Theme.styled_button("primary"))
        self._btn_export.setStyleSheet(Theme.styled_button())
        self._hover_card.apply_theme()
        self._apply_mpl_theme()
        if self._ax is not None:
            self._redraw()
            # 色点在 _redraw 里按新主题色板重算，统计表须同步刷新，
            # 否则停留旧主题色
            self._update_stats_table()

    # ------------------------------------------------------------------
    #  显示 / 关闭（实例复用：关闭仅隐藏并释放连接，重开重连）
    # ------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        if self._history_db is None:
            self._open_db()
        else:
            self._refresh_sessions()

    def closeEvent(self, event):
        """关闭：等待在跑的加载线程（防 QThread 析构崩溃）并释放数据库连接。

        等待期间保持事件循环响应（大查询超过原 2s 盲等时限会把连接从
        工作线程脚下关掉，sqlite 跨线程关闭有崩溃风险）；10s 硬上限
        防死等。
        """
        worker = self._worker
        if worker is not None and worker.isRunning():
            deadline = time.monotonic() + 10.0
            while worker.isRunning() and time.monotonic() < deadline:
                QApplication.processEvents()
                QThread.msleep(10)
        if self._history_db is not None:
            try:
                self._history_db.close()
            except Exception:
                pass
            self._history_db = None
        self._dismiss_hover()
        super().closeEvent(event)
