# -*- coding: utf-8 -*-
"""
main_window.py — 主窗口纯编排器

职责：创建布局、实例化子模块、连接信号/槽。
不直接干具体活——所有功能委托给子模块。
"""
from __future__ import annotations

import os
import time
from dataclasses import replace as _dc_replace
from typing import Optional

from PyQt5.QtCore import (Qt, QTimer, QTime, QPropertyAnimation,
                          QParallelAnimationGroup, QEasingCurve, QRect,
                          QPoint, QThread, QEvent, pyqtSignal,
                          QVariantAnimation)
from PyQt5.QtGui import (QFontMetrics, QGuiApplication, QKeySequence,
                         QColor, QBrush, QPainter, QPen, QRadialGradient)
from PyQt5.QtWidgets import (
    QAction, QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QTabBar, QFrame, QToolBar, QToolButton,
    QMessageBox, QSplitter, QAbstractItemView, QSizePolicy,
    QComboBox, QMenu, QSystemTrayIcon, QDialog, QShortcut,
)


def acq_status_label(mode: str, port: str = "") -> str:
    """拼底部状态栏主文本：端口 + 状态一体（无「●」前缀）。

    在线/心跳这层语义由状态栏心跳灯承担，文字只保留信息。mode 取值：
    idle / connected / acquiring / paused / disconnected / failed；未知模式
    与无端口场景回退为不带端口的通用文案，保证状态栏不出现空串。
    """
    p = (port or "").strip()
    if mode == "connected":
        return f"已连接 {p}" if p else "已连接"
    if mode == "acquiring":
        return f"{p} 采集中" if p else "采集中"
    if mode == "paused":
        return f"{p} 已暂停" if p else "已暂停"
    if mode == "disconnected":
        return f"{p} 连接断开" if p else "连接断开"
    if mode == "failed":
        return f"连接失败：{p}" if p else "连接失败"
    return "未连接"


class PortComboBox(QComboBox):
    """串口下拉框：点击弹出下拉时自动刷新端口列表。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._port_refresher = None

    def set_port_refresher(self, fn) -> None:
        """设置端口刷新回调（在 showPopup 前调用）。"""
        self._port_refresher = fn

    def sizeHint(self):
        """宽度上限内给出 sizeHint（QComboBox 原生 hint 随最长条目无限增长）。

        工具栏按子控件 sizeHint 排布/报所需宽度，若不钳制，端口描述
        撑大的 hint 会顶飞工具栏整体宽度（按钮被截断），且默认窗口
        宽度无法预计算。
        """
        hint = super().sizeHint()
        max_w = self.maximumWidth()
        if max_w < 16777215:
            hint.setWidth(max_w)
        return hint

    def showPopup(self) -> None:
        """弹出下拉前先刷新端口列表，再交给父类处理。

        本体宽度固定（保证工具栏完整显示、按钮不被挤压），完整设备
        描述在弹出列表中按内容加宽展示。
        """
        if self._port_refresher is not None:
            try:
                self._port_refresher()
            except Exception:
                pass
        super().showPopup()
        view = self.view()
        if view is not None and self.count():
            fm = self.fontMetrics()
            measure = getattr(fm, "horizontalAdvance", fm.width)
            widest = max(measure(self.itemText(i)) for i in range(self.count()))
            # 上限 480：极长描述也不至于撑出屏幕
            view.setMinimumWidth(min(widest + 40, 480))

from utils import core
from ui.theme import Theme
from utils.config_io import ConfigIO, LIVE_WINDOW_SEC_DEFAULT
from utils.export import ExportManager, _EXPORT_THEME_NOT_SET
from utils.helpers import FONT_PRESETS, FONT_LEVEL_NAMES, _build_font_dict, _fmt_win_int
from utils.alarm_sound import start_alarm_loop, stop_alarm_loop, alarm_loop_active
from utils.modbus_rtu import build_write_single_coil
from ui.dialogs.export_dialog import ExportDialog

from device.datastore import store
from device.datastore.pipeline import Pipeline
from device.datastore.session import Session

from device.acquisition import (SerialPortManager, AcquisitionWorker, READ_SETDATA,
                          READ_CHANONOFF, SET_CHANONOFF, SET_SAMP_CH_OLD,
                          TPID)

from chart.chart_tab import PlotTab
from ui.widgets.channel_panel import ChannelPanel
from ui.widgets.axis_panel import AxisBasePanel
from chart.chart_renderer import ChartRenderer
from ui.widgets.stat_panel import StatPanel
from ui.widgets.combo_panel import ComboPanel
from ui.widgets.compare_panel import ComparePanel
from ui.widgets.toggle_switch import ToggleSwitch
from ui.widgets.theme_busy import ThemeBusyPopup
from ui.widgets.live_overview_popup import (
    FloatingBall,
    LiveOverviewPopup,
    max_temp_level,
)
from ui.widgets.live_channel_panel import LiveChannelPanel
from ui.widgets.eco_cabin import (
    aggregate, celebrate_edge, channel_level, fluct_flags,
    warn_buffer)
from ui.widgets.status_info_panel import StatusInfoPanel
from ui.widgets.acquisition_step_card import AcquisitionStepCard
from ui.widgets.busy_card import BusyCard
from ui.widgets.status_toast import StatusToast
from ui.widgets.heartbeat_indicator import (STATE_CONNECTING, STATE_OFF,
                                         STATE_ONLINE, STATE_PAUSED)


CONFIG_DIR = ConfigIO._resolve_config_dir() if hasattr(ConfigIO, '_resolve_config_dir') else \
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")


# ── 内置配色方案（通道管理页勾选切换）──────────────────────────────
# 高对比配色拆分为独立方案行，不再随主题自动合并：
#   统一默认（适用于所有主题）+ 四套场景主题专属配色 + 温度色谱。
# variant 字段标记配色来源：universal → Theme.default_high_contrast_colors()，
# 其余为 Theme.HIGH_CONTRAST_CHANNELS 的主题键。颜色由 _load_schemes() 填充，
# 各方案颜色固定，切换主题不再改写当前方案颜色。
BUILTIN_COLOR_SCHEMES = [
    {"name": "高对比配色 · 统一默认", "mode": "categorical", "variant": "universal"},
    # 五套场景主题各配专属通道色板（对各自画布 ≥3.0:1、两两 ΔE≥8.0），
    # 激活某主题时其专属行即推荐项。
    {"name": "高对比配色 · 办公明亮", "mode": "categorical", "variant": "office_light"},
    {"name": "高对比配色 · 办公专注", "mode": "categorical", "variant": "office_focus"},
    {"name": "高对比配色 · 产线标准", "mode": "categorical", "variant": "production_standard"},
    {"name": "高对比配色 · 产线低照度", "mode": "categorical", "variant": "production_low_light"},
    {"name": "高对比配色 · 浅灰工业", "mode": "categorical", "variant": "grey_industrial"},
    {"name": "温度色谱（随值渐变）", "mode": "thermal"},
]

# 已废弃的历史分类调色板方案名（精简前的内置 10 套 + 旧「高对比配色」单行版）。
# 旧 color_schemes.json 中可能残留这些记录：_load_schemes() 会过滤掉它们，
# 旧配置的 scheme 字段在 _current_scheme_name() 中归并到
# 「高对比配色 · 统一默认」，后续 _mark_scheme_active() 重写列表时渐进清理。
_LEGACY_SCHEME_NAMES = {
    "默认", "暖色系", "冷色系", "色盲友好配色", "莫兰迪",
    "商务蓝", "科技渐变", "彩虹", "灰度", "高对比配色",
    # 2026-08-30 主题重构后退役的内置行（同名的工业浅灰/工业中灰由内置
    # 新 variant 覆盖，其余旧行名按废弃清理）
    "高对比配色 · 钢蓝灰", "高对比配色 · 石墨深灰", "高对比配色 · 暖银灰",
    "高对比配色 · 工业蓝灰", "高对比配色 · 工业浅灰",
    "高对比配色 · 工业中灰", "高对比配色 · 净白精工",
    "高对比配色 · 冰蓝玻璃", "高对比配色 · 石墨中黑",
    # 2026-08-31 晴空浅蓝退役（旧配置残留行过滤；主题键迁移见 theme.py）
    "高对比配色 · 晴空浅蓝",
    "高对比配色 · 经典工业浅灰", "高对比配色 · 经典工业中灰",
    "高对比配色 · 经典钢蓝灰", "高对比配色 · 经典石墨深灰",
    "高对比配色 · 经典暖银灰",
}


class _BackgroundWorker(QThread):
    """通用后台工作线程：子线程执行 fn，结果/异常经信号回 UI 线程（C1 不变量）。

    用于把文件解析、导出、历史加载等 >100ms 的操作移出 UI 线程。fn 必须是
    线程安全的纯函数（不触碰 UI 控件、不改 UI 线程持有的共享可变状态）；对
    结果的 UI 变更一律在 done/failed 信号的槽（UI 线程）里完成。
    """
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as e:
            import traceback
            self.failed.emit(f"{e}\n{traceback.format_exc()}")


class _RemoteLoadWorker(QThread):
    """后台远程数据拉取：通道配置 + 全量温度数据（分批，进度信号回 UI）。

    C1：远程大会话拉取移至后台线程，消除 UI 假死与 processEvents 重入。
    纯网络拉取（不触碰 store/UI），结果经信号回 UI 线程挂载。
    """
    progress = pyqtSignal(int, int)     # done, total
    done = pyqtSignal(object, object)   # channels, data
    failed = pyqtSignal(str)

    def __init__(self, client, session_id: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._session_id = session_id

    def run(self):
        try:
            channels = self._client.get_channel_config(self._session_id)
            data = self._client.get_all_temperature_data(
                self._session_id, progress_cb=self._emit_progress)
            self.done.emit(channels, data)
        except Exception as e:
            import traceback
            self.failed.emit(f"{e}\n{traceback.format_exc()}")

    def _emit_progress(self, done: int, total) -> None:
        """进度回调（后台线程）→ 队列信号到 UI 线程。"""
        self.progress.emit(int(done), int(total))


def _process_memory_bytes():
    """当前进程工作集内存（字节）；非 Windows 或查询失败返回 None。

    仅用标准库 ctypes + psapi 的 GetProcessMemoryInfo（WorkingSetSize），
    不引入任何第三方依赖。调用频率 1/5s，成本可忽略；
    显式声明 argtypes / restype，避免 x64 下 HANDLE 伪句柄被 c_int
    截断。任何异常都降级为 None（状态卡显示「--」），不影响主流程。
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        if not handle:
            return None
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        if not psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb):
            return None
        working_set = counters.WorkingSetSize
        return int(working_set) if working_set > 0 else None
    except Exception:
        return None


class _FadeMotionOverlay(QWidget):
    """动效覆盖层基类：全屏透明置顶层，按动画进度回调子类重绘。

    主窗口做退出/最小化动效时叠加的视觉层：穿透鼠标、不抢焦点、
    不进任务栏；进度驱动 valueChanged(0.0→1.0)，动画结束自毁。
    子类实现 _paint_motion(painter, t) 完成具体绘制；_PADDING 为
    光晕/描边超出目标矩形的余量，保证覆盖层不裁切 visuals。
    """
    _PADDING = 48

    def __init__(self, rect: QRect, duration: int):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool
                         | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setGeometry(rect.adjusted(-self._PADDING, -self._PADDING,
                                       self._PADDING, self._PADDING))
        self._origin = rect.topLeft()   # 全局→本地坐标换算基准
        self._progress = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.valueChanged.connect(self._on_progress)
        self._anim.finished.connect(self.close)

    def start(self) -> None:
        """显示覆盖层并播放动效（由主窗口随淡出/淡入动画一同触发）。"""
        self.show()
        self.raise_()
        self._anim.start()

    def _on_progress(self, value) -> None:
        self._progress = float(value)
        self.update()

    def _local(self, p: QPoint) -> QPoint:
        """全局坐标点换算到覆盖层本地坐标。"""
        return p - self._origin

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self._paint_motion(painter, self._progress)


class _TrayGuideOverlay(_FadeMotionOverlay):
    """最小化到托盘的引导光点：从窗口中心飞向托盘图标位置。

    主窗口原位淡出时给出「窗口去哪儿了」的方向指引，配合结束后的
    托盘气泡完成双重提示；全程不改主窗口几何与窗口状态。
    """
    _DOT_RADIUS = 7       # 光点半径（px）
    _TRAIL_STEPS = 6      # 彗尾采样点数
    _TRAIL_STEP = 0.045   # 相邻彗尾样本的进度回退量

    def __init__(self, start: QPoint, end: QPoint, duration: int):
        super().__init__(QRect(QPoint(min(start.x(), end.x()),
                                      min(start.y(), end.y())),
                               QPoint(max(start.x(), end.x()),
                                      max(start.y(), end.y()))),
                         duration)
        self._start = self._local(start)
        self._end = self._local(end)

    def _point_at(self, t: float) -> QPoint:
        return QPoint(round(self._start.x()
                            + (self._end.x() - self._start.x()) * t),
                      round(self._start.y()
                            + (self._end.y() - self._start.y()) * t))

    def _paint_motion(self, painter, t: float) -> None:
        for i in range(self._TRAIL_STEPS, 0, -1):
            tt = t - i * self._TRAIL_STEP
            if tt <= 0.0:
                continue
            fade = 1.0 - i / (self._TRAIL_STEPS + 1.0)
            self._draw_dot(painter, self._point_at(tt),
                           self._DOT_RADIUS * fade, int(150 * fade))
        self._draw_dot(painter, self._point_at(t), self._DOT_RADIUS, 230)

    def _draw_dot(self, painter, pos: QPoint, radius: float,
                  alpha: int) -> None:
        if radius < 0.5 or alpha <= 0:
            return
        halo = QColor(Theme.HOVER_HIGHLIGHT)
        gradient = QRadialGradient(pos, radius * 3)
        gradient.setColorAt(0.0, QColor(255, 255, 255, alpha))
        gradient.setColorAt(0.35, QColor(halo.red(), halo.green(),
                                         halo.blue(), alpha))
        gradient.setColorAt(1.0, QColor(halo.red(), halo.green(),
                                        halo.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(pos, int(radius * 3), int(radius * 3))


class _CloseCrossOverlay(_FadeMotionOverlay):
    """退出动效的打叉层：在窗口区域上依次画出两笔对角线。

    前半程画「\\」、后半程画「/」，末段随主窗口淡出整体渐隐，给
    「程序正在关闭」的明确视觉确认；全程不改主窗口几何与窗口状态。
    """
    _PEN_WIDTH = 7

    def _paint_motion(self, painter, t: float) -> None:
        left, top = self._PADDING, self._PADDING
        right, bottom = self.width() - self._PADDING, self.height() - self._PADDING
        tl, br = QPoint(left, top), QPoint(right, bottom)
        tr, bl = QPoint(right, top), QPoint(left, bottom)
        alpha = 235 if t < 0.85 else int(235 * (1.0 - (t - 0.85) / 0.15))
        color = QColor(Theme.HOVER_HIGHLIGHT)
        color.setAlpha(max(alpha, 0))
        pen = QPen(color, self._PEN_WIDTH)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        if t <= 0.5:
            painter.drawLine(tl, self._lerp(tl, br, t / 0.5))
        else:
            painter.drawLine(tl, br)
            painter.drawLine(tr, self._lerp(tr, bl, (t - 0.5) / 0.5))

    @staticmethod
    def _lerp(a: QPoint, b: QPoint, t: float) -> QPoint:
        return QPoint(round(a.x() + (b.x() - a.x()) * t),
                      round(a.y() + (b.y() - a.y()) * t))


class MainWindow(QMainWindow):
    """主窗口 —— 纯编排器，不直接干具体活。

    职责：创建布局、实例化子模块、连接信号/槽。
    """
    # 唯一的常规窗口尺寸：启动尺寸 = 还原按钮回落尺寸 = 窗口最小尺寸。
    # 最大化/还原按钮只在该尺寸与最大化之间切换（见 changeEvent）。
    DEFAULT_GEOMETRY = (1420, 920)
    # 左侧通道面板基础宽度：266px 固定卡片 + 左右边距 2×2px。
    # 滚动条出现时按需加宽（额外分配滚动条空间，不侵入卡片区域）。
    LEFT_PANEL_WIDTH = 270
    # 底部实时信息面板：两行高对比指标，一级显示点数/IP/采集时长。
    STATUSBAR_HEIGHT = 78
    STATUSBAR_NET_MIN_WIDTH = 180
    STATUSBAR_NET_MAX_WIDTH = 440

    # ── 常量 ──
    # 配置已统一收敛到 settings.json（ConfigIO.load_section/save_section）；
    # 不再维护分散的遗留 JSON 文件常量。参数预设/通道预设同样走统一配置段。

    def __init__(self):
        super().__init__()
        # ── 数据状态 ──
        # dataset / processed 已由 datastore 统一管道替代：
        #   store.active (Session) 是唯一数据源
        #   self.pipeline 负责处理（全量 recompute / 增量 on_appended）
        self.pipeline = Pipeline()
        # 会话编辑参数（重采样/平滑按会话生效；仅"不在录制中"的会话读取，
        # 录制中的实时会话固定走全局显示平滑、禁用重采样——见 _get_pipeline_params）
        self._session_edit_params: dict = {}
        self._acq_worker = None          # AcquisitionWorker 实例
        self._acq_workers_exiting = []   # stop() 超时未退出的滞留线程引用（C4 防销毁）
        self._data_mode = "file"         # "live" 或 "file"
        self.color_mode = "categorical"
        self.win_front = [10.0, 20.0, 30.0]
        # 整体趋势图固定显示统计栏；前 N 分钟导出图由设置项控制。
        self.show_window_stats = True
        self._loading = False
        self._channel_cycle_idx = 0
        self._layout_ovr = {}
        self._combo_cbar_ax = None

        # ── 实时刷新节流（合并高频 data_appended 信号）──
        # 100ms 合并刷新；配合 ChartRenderer 增量绘制（set_data 复用，不做全量重建），
        # 高频重绘不会卡顿，保证实时曲线流畅。
        self._live_dirty = False
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(100)  # 100ms 合并刷新（约 10Hz 上限）
        self._live_timer.timeout.connect(self._flush_live)
        # 温升统计重算节流时间戳（见 _STATS_REFRESH_INTERVAL 注释）
        self._last_stats_update = 0.0
        self._was_maximized = False   # changeEvent 两态切换（最大化↔常规）判定
        # COM 口自动获取：无端口时每 3s 自动重试枚举，插上新设备无需手动刷新
        self._port_refresh_timer = QTimer(self)
        self._port_refresh_timer.setInterval(3000)
        self._port_refresh_timer.timeout.connect(self._auto_refresh_ports)
        self._port_refresh_timer.start()
        # 左侧状态卡「内存占用」行：5s 周期刷新当前进程工作集。
        # 采集未运行也照刷（GetProcessMemoryInfo 成本可忽略）；
        # 首刷在 _init_ui 之后显式调 _refresh_memory_usage()（标签构造完成后）。
        self._mem_usage_timer = QTimer(self)
        self._mem_usage_timer.setInterval(5000)
        self._mem_usage_timer.timeout.connect(self._refresh_memory_usage)
        self._mem_usage_timer.start()

        # ── 串口管理器 ──
        self._serial_mgr = SerialPortManager()
        self._acq_baud = 2400         # 波特率（设置页配置，默认2400）
        self._acq_interval_ms = 1000  # 采样间隔毫秒（设置页配置）
        self._acq_max_groups = 0      # 采集组数（0=自动=读取仪器配置组数；1~8=软件指定）
        self._acq_group_switch = 0xFF
        self._acq_channel_masks = [0xFF] * 8
        self._acq_state = "ready"
        self._acq_started_at = None

        # ── 数据库和 ServiceManager ──
        self._history_db = None
        self._service_manager = None  # 多模式网络服务管理器
        self._last_local_ip = "127.0.0.1"  # 本机 IP 缓存（服务管理器创建时刷新）
        # 窗口标题基础文案（不含 TCP 网络信息段）；各业务场景经
        # _set_window_title 更新，TCP 段随服务启停自动拼接/移除
        self._title_base = "多通道温度分析仪"
        self._db_write_interval = 1.0  # 数据库批量写入间隔（分钟），默认 1 分钟
        # 客户端模式标志：已从远程设备加载数据即为「客户端模式」。
        # 服务端模式（网络服务运行中）与客户端模式硬互斥，横幅经
        # 「数据」弹窗服务页 set_client_active 同步
        #（_sync_client_mode_banner）。
        self._client_mode_active = False
        # 「数据」对话框实例（复用：三标签 本机数据/远程数据/服务 +
        # 打开文件角标按钮；关闭只隐藏）
        self._history_dialog = None
        # 远程「采集中」会话实时监控器（一次一个；被替换的转退役列表保活）
        self._remote_monitor = None
        self._retired_monitors = []

        # ── 轴配置 ──
        self.ax_time_mode = "auto"
        self.ax_time_min = 0.0
        self.ax_time_max = 60.0
        self.ax_time_step = 5.0
        # 温度轴统一智能模式：基础窗口 + 越界扩展系数
        self.ax_temp_base_lo = 20.0    # 基础窗口下限（℃）
        self.ax_temp_base_hi = 40.0    # 基础窗口上限（℃）
        self.ax_temp_lo_factor = 0.90  # 下界扩展系数（越界时 × 最低温度）
        self.ax_temp_hi_factor = 1.30  # 上界扩展系数（越界时 × 最高温度）
        # 整体趋势双区视图：开关 + 右区实时窗宽度（秒）；
        # 由 load_axis_config 从 settings.json 读取覆盖（缺键回默认）
        self.ax_dual_view_enabled = False
        self.ax_live_window_sec = LIVE_WINDOW_SEC_DEFAULT

        # ── A4 配置 ──
        self.a4_custom1 = (0.0, 5.0)
        self.a4_custom2 = (0.0, 10.0)
        self.a4_mark = True

        # ── 温升三阶段自适应分析参数 ──
        self.rise_config = core.RiseAnalysisConfig()

        # ── 温度报警参数（仅实时采集判定，详见 core.AlarmConfig）──
        self.alarm_config = core.AlarmConfig()
        # 报警判定状态机：{(channel_idx|None, alarm_type): "normal"|"active"}
        # channel_idx 为 None 表示帧级报警（通道间温差）
        self._alarm_states = {}
        # 各通道上一有效点 {channel_idx: (timestamp, value)}，供变化率判定
        self._alarm_prev = {}
        # Modbus 报警输出串口（独立于采集串口，零侵入采集线程）
        self._alarm_serial = None
        self._alarm_serial_on = False
        # 报警确认标记：复位（确认静音）后 active 的 (ch,type) 加入此集合，
        # 不再响声 / 弹窗，直到温度恢复（cleared）移除后再次超限才重新提醒
        self._alarm_acked = set()
        # 非模态报警对话框（按需创建）
        self._alarm_dialog = None
        # 通道命名弹窗（面板顶部入口打开；复用实例，关闭仅隐藏）
        self._naming_dialog = None

        # ── 字体 ──
        self._font_level = "默认"
        self._font = _build_font_dict(FONT_PRESETS[self._font_level])
        # 别名供 SettingsDialog 引用
        self._current_font = self._font

        # ── 通道名称池 ──
        self.name_list = ["准直保护", "准直镜片", "反射镜片", "场镜保护",
                          "Y轴电机", "X轴电机", "腔体气温", "聚焦组件",
                          "准直组件", "Y轴腔体", "X轴腔体"]

        # ── 子模块（设置前先创建 UI，但模块实例先创建供回调使用） ──
        self.channel_panel = None
        self.chart_renderer = None
        self.stat_panel = None
        self.combo_panel = None
        self.compare_panel = None
        self.export_mgr = None

        # ── cfg 加载 ──
        self._load_configs()

        # ── 系统托盘 ──
        self._tray_icon = None
        self._tray_enabled = True  # 可在设置中关闭
        # 悬浮球总开关缓存（托盘底部勾选项 ↔ live_monitor.ball_enabled）：
        # 必须在 _init_ui（托盘菜单构造）之前就绪，勾选初值才能与配置一致；
        # 热更新/设置页保存经 _apply_live_monitor_cfg 回写
        try:
            self._lm_ball_enabled = bool(
                ConfigIO.load_live_monitor_config()
                .get("ball_enabled", True))
        except Exception:
            self._lm_ball_enabled = True
        self._tray_ball_action = None
        self._tray_menu = None
        # 退出/最小化路由标志（改动 10）：_force_exit=True 时 closeEvent
        # 走真关闭链路；否则点 X 淡出最小化到托盘
        self._force_exit = False
        self._tray_anim_active = False
        self._tray_anim = None
        self._tray_guide = None        # 最小化引导光点层（_TrayGuideOverlay）
        self._close_anim_active = False
        self._close_anim = None        # 退出打叉动效组（原位淡出 + 覆盖层）
        self._close_cross = None       # 退出打叉覆盖层（_CloseCrossOverlay）
        self._restore_anim = None      # 托盘恢复淡入动画

        self._init_ui()
        # 双区视图配置（settings.json axis 分区）应用到渲染器：
        # _load_configs 在 _init_ui 之前执行，renderer 尚不存在，这里补应用
        self._sync_dual_view_config()
        # 内存占用行首刷：5s 定时器首次触发前先显示当前值（标签已构造）
        self._refresh_memory_usage()

        # ── 连接 datastore 统一管道信号 ──
        store.active_changed.connect(self._on_active_changed)
        store.data_appended.connect(self._on_data_appended)
        store.recording_changed.connect(self._on_recording_changed)
        store.channels_changed.connect(self._on_channels_changed)
        # 绑定方法（而非 lambda）：窗口销毁后 Qt 自动断开，
        # 避免 store 单例上残留指向已删窗口的连接
        store.error.connect(self._on_store_error)

        # ── 配置热重载（settings.json / secrets.json 外部修改即时生效）──
        # 必须在 __init__ 直接调用——曾在 6681a42 被误吞进 _on_store_error
        # 导致热重载全程失效，勿再缩进进其它方法
        self._init_config_watcher()

        # 实时监控悬浮窗：无边框圆角趋势弹窗 + 桌面常驻可拖拽悬浮球。
        # 悬浮球是唯一开关入口（旧工具栏「监控悬浮窗」勾选按钮已移除，避免与球重复）。
        # 开关意图用 _live_overview_enabled 标志承载，跨会话切换时记忆用户意愿。
        # 显隐规则：仅在线采集中（_acq_state ∈ acquiring/paused）显示悬浮球族，
        # 无在线采集（含刚启动、已结束、文件会话）一律隐藏。
        self._live_overview_enabled = False
        self._live_overview_popup = LiveOverviewPopup(self)
        self._live_overview_popup.closed.connect(self._on_live_overview_closed)
        # 悬浮球点击切换弹窗；拖拽移动不触发点击；弹窗显隐回灌小球高亮。
        self._live_monitor_ball = FloatingBall()
        self._live_monitor_ball.clicked.connect(self._toggle_live_overview)
        self._live_overview_popup.set_anchor(self._live_monitor_ball)
        self._live_overview_popup.window_sec_changed.connect(
            self._on_live_monitor_window_changed)
        self._live_overview_popup.set_restore_hook(
            self._sync_live_ball_visibility)
        self._place_live_monitor_ball()
        self._sync_live_ball_visibility()

        # 全通道悬浮面板：与悬浮球刚体关联的第三块悬浮显示——贴球外侧展开、
        # 随球移动；显示开关与外观走 live_monitor 的 panel_* 配置；实时会话
        # 显示、非实时自动隐藏，刷新挂在 _flush_live 合并节流点上。
        self._live_channel_panel = LiveChannelPanel()
        self._live_channel_panel.set_host(self)
        self._live_channel_panel.closeRequested.connect(
            self._on_channel_panel_close_requested)
        self._live_channel_panel.skinChanged.connect(
            self._on_channel_panel_skin_changed)
        self._live_monitor_ball.panelToggleRequested.connect(
            self._toggle_channel_panel_from_ball)
        self._live_monitor_ball.doubleClicked.connect(
            self._toggle_channel_panel_from_ball)
        self._live_monitor_ball.leftButtonClicked.connect(
            self._toggle_channel_panel_from_ball)
        self._live_monitor_ball.rightButtonClicked.connect(
            self._toggle_live_overview)
        self._live_monitor_ball.moved.connect(self._follow_ball_move_panel)
        # 生态舱手势：单击切换展开模式（方向感应协调器接管两面板显隐）
        self._live_monitor_ball.expandToggleRequested.connect(
            self._on_ball_expand_toggle)
        # —— 组合悬浮窗状态（单击开合；分段 × 由各窗信号接管）——
        self._ball_expand_mode = False
        # 开关状态缓存：避免 _flush_live（最高 10Hz）在 GUI 线程反复整读
        # settings.json（球心参数 _lm_ball_* 同为缓存惯例）；热更新/设置页
        # 经 _apply_live_monitor_cfg 刷新。
        try:
            self._lm_panel_enabled = bool(
                ConfigIO.load_live_monitor_config()
                .get("panel_enabled", False))
        except Exception:
            self._lm_panel_enabled = False
        # —— 生态舱推送状态（pet_style=cabin 才消费；缓存经 _apply_live_monitor_cfg）——
        self._pet_style = "dino"
        self._cabin_fluct_rate = 2.0
        self._cabin_highs = {}                 # {通道稳定键: ℃} 显示阈值覆盖
        self._ball_feed_prev = None            # (ts, [temps]) 波动检测基线
        self._ball_levels_prev = None          # 迟滞状态跨拍保持
        self._celebrate_last = 0.0
        self._celebrate_prev_agg = "idle"
        self._refresh_channel_panel_visibility()

    def _on_store_error(self, msg: str) -> None:
        """数据总线错误 → 状态栏提示。"""
        self.statusBar().showMessage(f"错误：{msg}", 5000)

    # ==================================================================
    #  配置热重载（ConfigWatcher）
    # ==================================================================
    # 支持运行时即时生效的配置段 → 处理器方法名
    _HOT_APPLY = {
        "theme":       "_hot_apply_theme",
        "axis":        "_hot_apply_axis",
        "a4":          "_hot_apply_a4",
        "overview":    "_hot_apply_overview",
        "rise":        "_hot_apply_rise",
        "alarm":       "_hot_apply_alarm",
        "channel_view": "_hot_apply_channel_view",
        "live_monitor": "_hot_apply_live_monitor",
    }
    # 由应用自身维护、无需热应用也无需提示的段
    _SILENT_SECTIONS = {
        "channels", "color", "color_schemes",
        "stat", "last_path", "settings_dialog_geometry",
        "naming_dialog_geometry",
    }

    def _init_config_watcher(self):
        try:
            from utils.config_watcher import ConfigWatcher
            self._config_watcher = ConfigWatcher(self)
            self._config_watcher.section_changed.connect(
                self._on_config_file_changed)
            self._config_watcher.start()
        except Exception as exc:
            print(f"[ConfigWatcher] 初始化失败：{exc}", flush=True)
            self._config_watcher = None

    def _on_config_file_changed(self, sections):
        """settings.json / secrets.json 被外部修改时的即时生效入口。"""
        for sec in sections:
            handler = self._HOT_APPLY.get(sec)
            if handler:
                try:
                    getattr(self, handler)()
                except Exception as exc:
                    self.statusBar().showMessage(
                        f"配置「{sec}」热更新失败：{exc}", 5000)
            elif sec in self._SILENT_SECTIONS:
                continue
            else:
                self.statusBar().showMessage(
                    f"配置「{sec}」已变更，需重启应用后生效", 5000)

    def _hot_apply_theme(self):
        name = Theme.DEFAULT_THEME
        try:
            d = ConfigIO.load_section("theme", {}, ())
            if isinstance(d, dict):
                name = Theme._resolve_theme_name(d.get("theme"))
        except Exception:
            pass
        self._apply_theme(name, silent=True)

    def _sync_dual_view_config(self):
        """把双区视图配置（开关 + 右区实时窗宽度，秒）应用到渲染器。

        启动加载 / 轴设置应用 / 配置热重载共用。配置文件可能被手工改出
        非法窗宽（set_dual_view_config 对非正数抛 ValueError），此时回退
        默认档（60 秒），避免启动或热重载被单条坏配置打断。
        """
        renderer = getattr(self, "chart_renderer", None)
        if renderer is None:
            return
        try:
            renderer.set_dual_view_config(
                bool(self.ax_dual_view_enabled), self.ax_live_window_sec)
        except (ValueError, TypeError) as e:
            print(f"[CONFIG] 双区视图窗宽配置无效，回退默认 "
                  f"{LIVE_WINDOW_SEC_DEFAULT} 秒: {e}",
                  flush=True)
            renderer.set_dual_view_config(
                bool(self.ax_dual_view_enabled), LIVE_WINDOW_SEC_DEFAULT)
        # 左侧状态卡「双区视图」行同步（启动 / 设置应用 / 热重载共用本方法）
        self._refresh_dual_view_info()

    def _refresh_dual_view_info(self):
        """左侧状态卡「双区视图」行：按渲染器当前配置刷新文案。

        开启 → 「右窗 {live_window_sec} 秒」（:g 格式与渲染器标题一致，
        60.0 显示为 60）；关闭 → 「单视图」。
        """
        lbl = getattr(self, "lbl_dual_view_info", None)
        renderer = getattr(self, "chart_renderer", None)
        if lbl is None or renderer is None:
            return
        if renderer.dual_view_enabled:
            lbl.setText(f"右窗 {renderer.live_window_sec:g} 秒")
            self._set_status_label_semantic(lbl, "current")
        else:
            lbl.setText("单视图")
            self._set_status_label_semantic(lbl, "info")

    def _refresh_memory_usage(self):
        """左侧状态卡「内存占用」行：刷新当前进程工作集显示。

        查询失败 / 非 Windows 显示「--」；显示格式「{MB:.1f} MB」。
        """
        lbl = getattr(self, "lbl_mem_usage", None)
        if lbl is None:
            return
        nbytes = _process_memory_bytes()
        if nbytes is None:
            lbl.setText("--")
            self._set_status_label_semantic(lbl, "info")
            return
        lbl.setText(f"{nbytes / (1024 * 1024):.1f} MB")
        self._set_status_label_semantic(lbl, "info")

    def _hot_apply_axis(self):
        ConfigIO.load_axis_config(self)
        self._sync_dual_view_config()
        self._sync_alarm_high_to_axis()
        if getattr(self, "chart_renderer", None) is not None:
            self.refresh_plots()

    def _hot_apply_a4(self):
        ConfigIO.load_a4_config(self)

    def _hot_apply_overview(self):
        ConfigIO.load_overview_config(self)
        if getattr(self, "stat_panel", None) is not None:
            self.stat_panel.update_stats()

    def _hot_apply_rise(self):
        ConfigIO.load_rise_config(self)

    def _hot_apply_alarm(self):
        ConfigIO.load_alarm_config(self)
        if getattr(self, "chart_renderer", None) is not None:
            self.refresh_plots()

    # ==================================================================
    #  dataset 兼容属性 — 返回当前活跃 Session
    # ==================================================================
    @property
    def dataset(self) -> Optional[Session]:
        """统一管道：返回当前活跃 Session（文件或采集），兼容旧代码 self.dataset 引用。"""
        return store.active

    @property
    def processed(self) -> dict:
        """兼容属性：返回 Session 的 processed 缓存（由 Pipeline 写入，键=通道 index）。"""
        s = store.active
        return s.processed if s is not None else {}

    def _on_live_group_toggled(self, group_idx: int, enabled: bool) -> None:
        """左面板组启停 → 采集组数即时联动（仅采集中生效）。

        用户取消某组勾选 = 不启用该组：把「采集组数」从自动(0)调整为实际
        启用的组数，通知采集线程只上报启用组，并持久化；全部启用时恢复
        自动(0=按设备实测组数)。离线/未采集时组开关只控制显示，不动采集组数。
        """
        if self._acq_state != "acquiring":
            return
        panel = self.channel_panel
        enabled_groups = [g for g, grp in panel.groups.items() if grp.enabled]
        if not enabled_groups:
            return  # 至少保留一组
        all_enabled = len(enabled_groups) == len(panel.groups)
        new_max = max(enabled_groups) + 1
        self._acq_max_groups = 0 if all_enabled else new_max
        self._save_acq_config()
        if self._acq_worker is not None and self._acq_worker.isRunning():
            self._acq_worker.set_active_groups(
                self._acq_worker._group_count if all_enabled else new_max)
        self.statusBar().showMessage(
            f"采集组数已联动为 {'自动(按设备)' if all_enabled else str(new_max) + ' 组'}",
            3000)

    def _on_active_changed(self, session):
        """活跃会话切换（新文件导入 / 开始采集 / 切换会话）→ 刷新通道表 + 全量重算 + 重绘。"""
        if self.stat_panel is not None:
            self.stat_panel.clear_result_cache()
        renderer = getattr(self, "chart_renderer", None)
        if renderer is not None:
            # 会话切换后不沿用旧会话的手动横轴范围，避免状态串到新会话。
            renderer.live_view_mode = "auto"
            renderer._manual_xlim = None
            # 双区暂停冻结点 / 左区浏览窗引用旧会话时间轴，一并清空，
            # 避免新会话错误冻结在旧切分点（不重渲染，下方统一重绘）
            renderer.reset_dual_view_state()
            # 增量/报警线缓存一并失效：新会话强制全量重建，避免复用旧 artist
            renderer._live_inc.clear()
            renderer._alarm_lines.clear()
        if session is None:
            # 会话关闭 → 通道面板清空显示空提示
            self.channel_panel.populate()
            if renderer is not None:
                renderer.update_live_status_labels()
            self._update_live_view_controls()
            self._update_edit_button_state()
            self._sync_live_overview_popup(None)
            self.statusBar().showMessage("就绪")
            return
        self.pipeline.set_params(self._get_pipeline_params(session))
        self.pipeline.recompute(session)
        self._loading = True
        self.channel_panel.populate()
        self._loading = False
        self._refresh_single_combo()
        self.refresh_plots()
        self._update_stats()
        self._update_source_label()
        # 更新会话切换条选中态
        self._update_session_tab_selection()
        self._update_edit_button_state()
        self._sync_live_overview_popup(session)

    def _toggle_live_overview(self):
        """悬浮球点击：翻转开关意图，再按会话类型决定弹窗显隐。

        生态舱展开模式期间让位给方向感应协调器（本槽是 dino 手势路径）。
        """
        if getattr(self, "_ball_expand_mode", False):
            return
        self._live_overview_enabled = not self._live_overview_enabled
        self._apply_live_overview()

    def _on_live_overview_closed(self):
        """弹窗 × 关闭：清开关意图并取消小球高亮。

        组合窗展开模式：× 只关趋势段——面板段仍在则保持展开；两段全关
        整体收起（不动 _live_overview_enabled 意图，避免越权改用户开关）。
        """
        if getattr(self, "_ball_expand_mode", False):
            panel = getattr(self, "_live_channel_panel", None)
            if panel is None or not panel.isVisible():
                self._collapse_expand_combo()
            return
        self._live_overview_enabled = False
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is not None:
            ball.set_active(False)

    def _apply_live_overview(self):
        """按「开关意图 + 球总开关 + 在线采集中 + 当前会话是否实时」决定弹窗显隐，并回灌小球高亮。"""
        popup = getattr(self, "_live_overview_popup", None)
        if popup is None:
            return
        if getattr(self, "_ball_expand_mode", False):
            return          # 展开模式：弹窗显隐归方向感应协调器接管
        session = store.active
        want_show = (
            self._live_overview_enabled
            and bool(getattr(self, "_lm_ball_enabled", True))
            and self._is_live_acquiring()
            and session is not None
            and bool(getattr(session, "is_live", False))
        )
        if want_show:
            popup.show_popup()
            # 趋势弹窗后开：可见面板重新贴球定位，自动翻边避开弹窗（两图分居两侧）
            panel = getattr(self, "_live_channel_panel", None)
            if panel is not None and panel.isVisible():
                self._anchor_channel_panel_to_ball()
        else:
            popup.hide()
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is not None:
            ball.set_active(popup.isVisible())

    def _is_live_acquiring(self) -> bool:
        """当前是否在线采集中（含暂停：串口仍连接、采集会话仍存活）。"""
        return getattr(self, "_acq_state", "ready") in ("acquiring", "paused")

    def _sync_live_ball_visibility(self):
        """按「仅在线采集中显示」规则同步悬浮球族显隐（幂等，可反复调用）。

        托盘「显示悬浮球」总开关关闭（_lm_ball_enabled=False）时悬浮球族
        无条件退场——即使在线采集中；重新勾选后按采集态与既有意愿复显。
        无在线采集（未开始 / 已结束 / 文件会话）时悬浮球必须隐藏；刚体同族
        的趋势弹窗与全通道面板一并收回——球退场后二者失去锚点入口，继续
        悬浮与规则语义矛盾。弹窗的 _live_overview_enabled 开关意图保留
        （与既有「跨会话记忆用户意愿」语义一致），下次采集恢复时按意愿复显；
        面板经 _refresh_channel_panel_visibility 的采集态守卫自动收起。
        模态让位（suspend_for_modal）期间本方法照常生效：让位隐藏与本规则
        隐藏方向一致；模态恢复后经 set_restore_hook 回调重申规则，防止按
        旧让位记录复显已结束采集的悬浮族。
        """
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is None:
            return
        if not getattr(self, "_lm_ball_enabled", True):
            # 总开关关闭：与「无在线采集」同路径整体退场，开关意图照旧保留
            if ball.isVisible():
                ball.hide()
            ball.set_active(False)
            popup = getattr(self, "_live_overview_popup", None)
            if popup is not None and popup.isVisible():
                popup.hide()
            self._refresh_channel_panel_visibility()
            return
        if self._is_live_acquiring():
            if not ball.isVisible():
                ball.show()
            # 采集态成立：按既有意愿规则复显弹窗与面板（start_acquisition
            # 里 start_recording 的会话切换早于 _acq_state 置 acquiring，
            # 复显必须在这一步之后统一兜底）
            self._apply_live_overview()
            self._refresh_channel_panel_visibility()
            return
        if ball.isVisible():
            ball.hide()
        ball.set_active(False)
        # 球退场即退出展开模式（面板随族收，标志清零防采集恢复后幽灵接管）
        self._exit_expand_silently()
        popup = getattr(self, "_live_overview_popup", None)
        if popup is not None and popup.isVisible():
            popup.hide()
        self._refresh_channel_panel_visibility()

    def _sync_live_overview_popup(self, session):
        """会话切换后同步：保留开关意图，仅实时会话实际显示；球心温度随之校正。"""
        self._apply_live_overview()
        self._refresh_ball_max_temp()
        self._refresh_channel_panel()

    def _anchor_channel_panel_to_ball(self):
        """面板贴球定位：优先球外侧展开，趋势弹窗占用优先边时自动翻边。"""
        panel = getattr(self, "_live_channel_panel", None)
        ball = getattr(self, "_live_monitor_ball", None)
        if panel is None or ball is None:
            return
        if getattr(self, "_ball_expand_mode", False):
            # 组合窗拼装：面板紧贴趋势弹窗外侧（两段合成一张宽卡）
            self._expand_combo_anchor_panel()
            return
        panel.anchor_to(ball, avoid=getattr(self, "_live_overview_popup", None))

    def _place_live_monitor_ball(self):
        """悬浮球放置：注入宿主并按持久化偏好恢复（默认停在屏幕工作区正中，可在整个桌面拖动）。"""
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is None:
            return
        ball.set_host(self)
        ball.restore_initial()

    def _apply_live_monitor_cfg(self, cfg):
        """把外观配置推送到弹窗与悬浮球（单色/透明度/大小/时间窗口即时生效）。

        同时缓存球心最高温显示参数（show/warn_pct/high）与球总开关
        （ball_enabled，托盘勾选同步）；关闭显示时立即回图标；总开关值
        变化时重申悬浮族显隐规则（热更新外部关球即刻退场）。
        """
        popup = getattr(self, "_live_overview_popup", None)
        ball = getattr(self, "_live_monitor_ball", None)
        prev_ball_enabled = getattr(self, "_lm_ball_enabled", True)
        self._lm_ball_show_max = bool(cfg.get("ball_show_max_temp", True))
        self._lm_ball_enabled = bool(cfg.get("ball_enabled", True))
        self._lm_ball_warn_pct = int(cfg.get("ball_temp_warn_pct", 90))
        self._lm_ball_warn_high = float(cfg.get("ball_temp_warn_high", 0.0))
        self._pet_style = cfg.get("pet_style", "dino")
        self._cabin_fluct_rate = float(cfg.get("cabin_fluct_rate", 2.0))
        self._cabin_highs = dict(cfg.get("cabin_channel_highs", {}))
        if popup is not None:
            popup.set_appearance(cfg)
        if ball is not None:
            ball.set_pet_style(self._pet_style)
            ball.set_diameter(cfg.get("ball_size", 56))
            ball.set_ball_alpha(cfg.get("ball_alpha", 1.0))
            if cfg.get("ball_corner") in ball.CORNERS:
                ball.apply_corner_pref(cfg["ball_corner"])
            if not self._lm_ball_show_max:
                ball.clear_max_temp()
                self._cabin_clear_push(ball)
        panel = getattr(self, "_live_channel_panel", None)
        if panel is not None:
            panel.set_panel_alpha(cfg.get("panel_alpha", 0.85))
            panel.set_panel_theme(cfg.get("panel_theme", "light"))
            self._lm_panel_enabled = bool(cfg.get("panel_enabled", False))
            self._refresh_channel_panel_visibility()
        if self._lm_ball_enabled != prev_ball_enabled:
            # 总开关被热更新/落盘路径改变：同步托盘勾选态并重申显隐规则
            action = getattr(self, "_tray_ball_action", None)
            if action is not None and action.isChecked() != self._lm_ball_enabled:
                action.setChecked(self._lm_ball_enabled)
            self._sync_live_ball_visibility()

    def _apply_live_monitor_page(self):
        """设置·悬浮监控页：读取全部控件值，落盘并即时应用到弹窗/球。"""
        dlg = getattr(self, "settings_dlg", None)
        if dlg is None:
            return
        try:
            cfg = {
                "card_color": dlg.lm_card_color,
                # PercentLineEdit.value() 返回 0~100 的百分比，除以 100 得 0.0~1.0
                "card_alpha": dlg.lm_card_alpha_in.value() / 100.0,
                # 空串=跟随主题强调色，非空=用户自定义趋势色（优先于主题色）
                "line_color": getattr(dlg, "lm_line_color", ""),
                "ball_size": dlg.lm_ball_size_sp.value(),
                "ball_alpha": dlg.lm_ball_alpha_sp.value() / 100.0,
                "window_sec": dlg.lm_window_sec_sp.value(),
                "ball_corner": getattr(
                    dlg.lm_corner_cmb, "currentData", lambda: "center")(),
                "ball_show_max_temp": dlg.lm_ball_show_cb.isChecked(),
                "ball_temp_warn_pct": dlg.lm_ball_warn_pct_sp.value(),
                # 球总开关无对应设置页控件（托盘底部勾选项）：整段重建落盘时
                # 回写当前缓存，防止设置页保存把托盘开关静默重置为默认开启
                "ball_enabled": bool(getattr(self, "_lm_ball_enabled", True)),
                # 全通道面板：开关 + 皮肤 + 不透明度（Spinbox 返回 40~100，除以 100 得 0.40~1.00）
                "panel_enabled": dlg.lm_panel_enabled_cb.isChecked(),
                "panel_theme": getattr(
                    dlg.lm_panel_theme_cmb, "currentData", lambda: "light")(),
                "panel_alpha": dlg.lm_panel_alpha_sp.value() / 100.0,
                # 生态舱：形象/波动灵敏度/逐通道显示阈值（控件随页构建存在，
                # getattr 防御半构建态；缺控件时回退当前缓存值不破坏既有配置）
                "pet_style": getattr(
                    getattr(dlg, "lm_style_cmb", None),
                    "currentData", lambda: self._pet_style)(),
                "cabin_fluct_rate": getattr(
                    getattr(dlg, "lm_cabin_fluct_sp", None),
                    "value", lambda: self._cabin_fluct_rate)(),
                "cabin_channel_highs": {
                    f"CH{i + 1}": float(sp.value())
                    for i, (cb, sp) in enumerate(zip(
                        getattr(dlg, "lm_cabin_thr_cb", []),
                        getattr(dlg, "lm_cabin_thr_sp", [])))
                    if cb.isChecked()},
            }
        except Exception:
            return
        ConfigIO.save_live_monitor_config(cfg)
        self._apply_live_monitor_cfg(cfg)
        self._refresh_ball_max_temp()

    def _on_live_monitor_window_changed(self, value):
        """弹窗顶部滑块松手：持久化时间窗口并回灌设置页（不回推弹窗避免回环）。"""
        cfg = ConfigIO.load_live_monitor_config()
        cfg["window_sec"] = int(value)
        ConfigIO.save_live_monitor_config(cfg)
        dlg = getattr(self, "settings_dlg", None)
        if dlg is not None and hasattr(dlg, "sync_live_monitor_widgets"):
            dlg.sync_live_monitor_widgets(cfg)

    def _hot_apply_live_monitor(self):
        """外部改 settings.json 的 live_monitor 段 → 重载并应用到弹窗/球与设置页。"""
        cfg = ConfigIO.load_live_monitor_config()
        self._apply_live_monitor_cfg(cfg)
        dlg = getattr(self, "settings_dlg", None)
        if dlg is not None and hasattr(dlg, "sync_live_monitor_widgets"):
            dlg.sync_live_monitor_widgets(cfg)

    # ------------------------------------------------------------------
    #  会话切换条（多会话时显示，点击切换 active 会话）
    # ------------------------------------------------------------------
    def _on_session_added(self, session):
        """新会话加入 store → 刷新会话切换条。"""
        self._refresh_session_tabs()

    def _on_session_closed(self, session_id):
        """会话关闭 → 刷新会话切换条；释放该会话的编辑参数。"""
        self._session_edit_params.pop(session_id, None)
        self._refresh_session_tabs()
        self._update_edit_button_state()

    def _on_session_tab_changed(self, index):
        """会话切换条标签点击 → 切换 active 会话。"""
        if index < 0:
            return
        self._set_current_tab_semantic(self._session_tab_bar, index)
        session_id = self._session_tab_bar.tabData(index)
        if session_id is None:
            return
        if session_id == "live":
            store.activate_live()
        else:
            store.activate(session_id)

    def _on_session_tab_close_requested(self, index):
        """会话标签关闭按钮 → 关闭对应会话（live 标签无关闭按钮，不会触发）。

        复用 store.close_session：拒绝关闭录制中会话并经 error 信号提示，
        关闭后 session_closed 信号驱动切换条自动刷新。
        """
        if index < 0:
            return
        session_id = self._session_tab_bar.tabData(index)
        if session_id in (None, "live"):
            return
        store.close_session(session_id)

    def _refresh_session_tabs(self):
        """全量刷新会话切换条标签：会话数 >1 才显示。"""
        bar = self._session_tab_bar
        if bar is None:
            return

        bar.blockSignals(True)
        while bar.count():
            bar.removeTab(0)

        sessions = store.sessions
        live = store.live
        if len(sessions) <= 1:
            bar.hide()
        else:
            active = store.active
            # 实时会话（固定在最左，不可关闭）
            if live is not None:
                if live.title.startswith("实时采集"):
                    live_text = f"● {live.title}"
                else:
                    live_text = f"● 实时采集·{live.title}"
                idx = bar.addTab(live_text)
                bar.setTabData(idx, "live")
                # 移除 live 标签的关闭按钮（采集不能从这里关闭）
                bar.setTabButton(idx, QTabBar.RightSide, None)
                bar.setTabButton(idx, QTabBar.LeftSide, None)
                if active is not None and active.id == live.id:
                    bar.setCurrentIndex(idx)

            # 历史/导入会话（可关闭）
            for s in sessions:
                if live is not None and s.id == live.id:
                    continue
                idx = bar.addTab(s.title or s.id[:8])
                bar.setTabData(idx, s.id)
                if active is not None and s.id == active.id:
                    bar.setCurrentIndex(idx)

            bar.show()

        bar.blockSignals(False)
        self._set_current_tab_semantic(bar, bar.currentIndex())

    def _update_session_tab_selection(self):
        """active_changed 回调：同步更新会话切换条选中态。"""
        bar = self._session_tab_bar
        if bar is None or bar.isHidden():
            return

        active = store.active
        if active is None:
            return
        live = store.live
        if live is not None and active.id == live.id:
            target_id = "live"
        else:
            target_id = active.id
        for i in range(bar.count()):
            if bar.tabData(i) == target_id:
                bar.setCurrentIndex(i)
                self._set_current_tab_semantic(bar, i)
                break

    def _on_data_appended(self, session_id, start_row, count):
        """实时数据到达 → 增量处理 → 节流刷新当前标签页。"""
        try:
            s = store.active
            if s is None or s.id != session_id:
                return
            self.pipeline.on_appended(s, start_row, count)
            self._live_dirty = True
            if not self._live_timer.isActive():
                self._live_timer.start()
        except Exception as e:
            print(f"[ACQ] 数据处理异常: {e}", flush=True)

    # 温升统计重算节流（秒）：analyze_rise 是全量 O(n) 分析，数据越久越慢
    # （实测 16 通道 8 小时 ≈ 0.7s/次），若随 100ms 合并刷新每帧重算会占满
    # UI 线程导致整个界面冻结。阶段汇总本身是秒级稳定信息，5s 节流足够
    # 实时且把单次重算的卡顿摊薄到可接受。
    _STATS_REFRESH_INTERVAL = 5.0

    def _flush_live(self):
        """合并多次 data_appended 为一次 UI 刷新。"""
        if not self._live_dirty:
            return
        self._live_dirty = False
        try:
            self.refresh_plots()
            # 温升统计仅在对应标签页可见时更新，且按 _STATS_REFRESH_INTERVAL
            # 节流（避免每次刷新都全量重算统计表格，拖慢实时刷新）
            if (self.tabs.currentIndex() == self.idx_stat
                    and time.time() - self._last_stats_update
                    >= self._STATS_REFRESH_INTERVAL):
                self._last_stats_update = time.time()
                self._update_stats()
            self._update_source_label()
            # 更新左侧通道表格实时温度
            if hasattr(self, "channel_panel"):
                self.channel_panel.update_temperatures()
            popup = getattr(self, "_live_overview_popup", None)
            if popup is not None:
                popup.refresh_curves()
            # 悬浮球球心最高温数值直显（仅实时采集，走同一合并刷新节流点）
            self._refresh_ball_max_temp()
            # 全通道悬浮面板数据刷新（同一合并节流点，非实时时内部自隐藏）
            self._refresh_channel_panel()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[ACQ] 刷新异常: {e}", flush=True)

    def _refresh_ball_max_temp(self):
        """把可见通道『最新值』最高温推送到球心并判定警示档位。

        数据源：channel_panel.current_max_temperature（O(通道数)，µs 级）；
        非实时会话 / 关闭显示 / 无有效值 → 球回折线图标。
        """
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is None:
            return
        if not bool(getattr(self, "_lm_ball_show_max", True)):
            ball.clear_max_temp()
            self._cabin_clear_push(ball)
            return
        s = store.active
        if s is None or not getattr(s, "is_live", False):
            ball.clear_max_temp()
            self._cabin_clear_push(ball)
            return
        cp = self.channel_panel
        if cp is None:
            return
        mt = cp.current_max_temperature(True)
        if mt is None:
            ball.clear_max_temp()
            self._cabin_clear_push(ball)
            return
        temp, ch = mt
        custom_high = float(getattr(self, "_lm_ball_warn_high", 0.0))
        high = (custom_high if custom_high > 0.0
                else getattr(self.alarm_config, "temp_high", 100.0))
        level = max_temp_level(
            temp, int(getattr(self, "_lm_ball_warn_pct", 90)), float(high))
        ball.set_max_temp(temp, level, ch.display_name)
        if self._pet_style == "cabin":
            self._refresh_ball_channels(ball, s)

    # ------------------------------------------------------------------
    #  生态舱逐通道推送（pet_style=cabin；仅 _refresh_ball_max_temp 调用）
    # ------------------------------------------------------------------
    def _cabin_clear_push(self, ball):
        """无数据早退点同步通知舱休眠（dino 模式球内自动空转，双保险）。"""
        if getattr(self, "_pet_style", "dino") == "cabin":
            ball.set_channels([], (), (), (), paused=True)

    def _cabin_global_high(self):
        """舱默认显示上限：自定义警示上限优先，否则跟随全局报警上限（与曲线同源）。"""
        base = float(getattr(self, "_lm_ball_warn_high", 0.0))
        return (base if base > 0.0
                else float(getattr(self.alarm_config, "temp_high", 100.0)))

    def _cabin_resolve_high(self, key, global_high):
        try:
            v = float(self._cabin_highs.get(key, 0))
            return v if v > 0 else global_high
        except (TypeError, ValueError):
            return global_high

    def _refresh_ball_channels(self, ball, s):
        """把可见通道最新值/档位/波动推送到生态舱（100ms 合并节流点内，O(通道数)）。

        档位迟滞状态存于主窗（_ball_levels_prev），波动以相邻两推送帧的
        实际时间差 × cabin_fluct_rate 判定；「异常→平稳」恢复边沿触发一次
        舱庆祝（celebrate_edge 内置冷却防阈值徘徊抽搐）。
        """
        channels = s.visible_channels()
        temps = [c.last_value for c in channels]
        now = time.monotonic()          # 高精度单调钟：Windows time.time() ~16ms
        #                                       粒度会让高频帧 elapsed=0，波动/庆祝判定失效
        gh = self._cabin_global_high()
        highs = [self._cabin_resolve_high(c.key, gh) for c in channels]
        warn_pct = int(getattr(self, "_lm_ball_warn_pct", 90))
        abs_custom = float(getattr(self, "_lm_ball_warn_high", 0.0))
        bufs = [warn_buffer(h, warn_pct,
                            abs_custom if (abs_custom > 0 and h == gh) else 0.0)
                for h in highs]
        prev_levels = self._ball_levels_prev
        levels = [
            channel_level(
                t, h, b,
                prev=(prev_levels[i] if prev_levels and i < len(prev_levels)
                      else "normal"))
            for i, (t, h, b) in enumerate(zip(temps, highs, bufs))]
        self._ball_levels_prev = levels
        prev = self._ball_feed_prev
        if prev is not None and now > prev[0]:
            flags = fluct_flags(prev[1], temps, now - prev[0],
                                self._cabin_fluct_rate)
        else:
            flags = [False] * len(temps)
        self._ball_feed_prev = (now, temps)
        paused = self._acq_state == "paused"
        ball.set_channels(temps, levels, flags, highs, paused=paused)
        agg = aggregate(levels, flags, paused)
        go, self._celebrate_last = celebrate_edge(
            self._celebrate_prev_agg, agg, now, self._celebrate_last)
        if go:
            ball.trigger_celebrate()
        self._celebrate_prev_agg = agg

    def _refresh_channel_panel_visibility(self):
        """按「面板开关 + 球总开关 + 在线采集中 + 当前会话是否实时」决定全通道面板显隐。

        面板开关读缓存 ``_lm_panel_enabled``（构造与 _apply_live_monitor_cfg
        时刷新，避免高频 flush 整读 settings.json）；球总开关（托盘底部
        勾选项）关闭时面板随悬浮球族一并退场；会话语义与弹窗显隐
        一致：仅在线采集中的实时会话显示，采集已结束/历史文件会话/无会话
        自动隐藏（悬浮球同规则退场，面板不单独残留）。
        两类挂起优先于自动显隐：模态让位期间（suspend_for_modal）直接让位
        ——退出确认的 exec 事件循环里 _flush_live 仍会触发，若无此守卫面板
        会在模态框期间被高频刷新重新弹出盖住确认框；「– 收起」态（本次
        会话主动收起）同样不自动重显，须由球右键/设置页重新打开。
        显示前先贴球定位（刚体关联，自动避让趋势弹窗）。
        """
        panel = getattr(self, "_live_channel_panel", None)
        if panel is None:
            return
        if getattr(self, "_ball_expand_mode", False):
            return          # 展开模式：面板显隐归方向感应协调器接管
        if panel.is_suspended() or panel.is_collapsed():
            return
        enabled = bool(getattr(self, "_lm_panel_enabled", False))
        session = store.active
        want_show = (enabled
                     and bool(getattr(self, "_lm_ball_enabled", True))
                     and self._is_live_acquiring()
                     and session is not None
                     and bool(getattr(session, "is_live", False)))
        if want_show:
            if not panel.isVisible():
                self._anchor_channel_panel_to_ball()
                panel.show_panel()
        elif panel.isVisible():
            panel.hide_panel()

    def _on_channel_panel_close_requested(self):
        """面板 × 关闭：落盘 panel_enabled=false 并即时应用（含设置页回灌）。

        展开模式期间 × 仅收起面板（本次交互层关闭），不改配置——面板归
        方向协调器管，落盘会把用户的常驻开关误关掉。
        """
        if getattr(self, "_ball_expand_mode", False):
            panel = getattr(self, "_live_channel_panel", None)
            if panel is not None:
                panel.hide_panel()
            popup = getattr(self, "_live_overview_popup", None)
            if popup is None or not popup.isVisible():
                self._collapse_expand_combo()
            return
        try:
            cfg = ConfigIO.load_live_monitor_config()
            cfg["panel_enabled"] = False
            ConfigIO.save_live_monitor_config(cfg)
        except Exception:
            pass
        self._hot_apply_live_monitor()

    def _on_channel_panel_skin_changed(self, name):
        """面板 ◐ 皮肤切换：落盘 panel_theme 并即时应用（含设置页回灌）。"""
        try:
            cfg = ConfigIO.load_live_monitor_config()
            cfg["panel_theme"] = str(name)
            ConfigIO.save_live_monitor_config(cfg)
        except Exception:
            pass
        dlg = getattr(self, "settings_dlg", None)
        if dlg is not None and hasattr(dlg, "sync_live_monitor_widgets"):
            dlg.sync_live_monitor_widgets(
                ConfigIO.load_live_monitor_config())

    def _toggle_channel_panel_from_ball(self):
        """球右键「显示/隐藏全通道面板」：显示中→收起；隐藏中→开启并贴球。

        隐藏中重新显示会把 panel_enabled 写回 true（用户主动找回 = 意愿
        持久化）；主动找回必须显式清「– 收起」标志并直接显示——自动显隐
        链路的收起守卫会挡住常规路径（收起正是靠它不被高频刷新打穿）。
        展开模式期间让位方向感应协调器（生态舱单击手势路径）。
        """
        if getattr(self, "_ball_expand_mode", False):
            return
        panel = getattr(self, "_live_channel_panel", None)
        if panel is None:
            return
        if panel.isVisible():
            panel.collapse()      # 仅本次收起，不动配置
            return
        try:
            cfg = ConfigIO.load_live_monitor_config()
            cfg["panel_enabled"] = True
            ConfigIO.save_live_monitor_config(cfg)
        except Exception:
            pass
        self._lm_panel_enabled = True
        self._anchor_channel_panel_to_ball()
        panel.show_panel()
        dlg = getattr(self, "settings_dlg", None)
        if dlg is not None and hasattr(dlg, "sync_live_monitor_widgets"):
            dlg.sync_live_monitor_widgets(
                ConfigIO.load_live_monitor_config())

    def _follow_ball_move_panel(self):
        """球拖动中：可见面板实时跟随（刚体关联，同趋势弹窗跟随球的节奏）。"""
        panel = getattr(self, "_live_channel_panel", None)
        if panel is not None and panel.isVisible():
            self._anchor_channel_panel_to_ball()

    # ------------------------------------------------------------------
    #  生态舱组合悬浮窗（单击开合）：左段全通道面板 + 右段迷你趋势。
    #  两窗保持独立顶级窗紧贴拼装为一张宽卡，各自带 × 可单独关闭、趋势
    #  段仍可拖动/八向缩放；两段全关或再单击球体整体收起。dino 手势路径
    #  （双击/球面按钮）不受影响。
    # ------------------------------------------------------------------
    COMBO_GAP_PX = 2          # 组合窗两段拼装缝隙（px）

    def _on_ball_expand_toggle(self):
        """生态舱单击：开/关组合悬浮窗。

        开：趋势弹窗与全通道面板立即同现并紧贴拼装（无 280ms 双击判定）。
        两处动画会破坏拼装几何，展开路径显式停用（其余路径不变）：
        弹窗开窗动画把自身先挪开 ±24px 再滑回，面板锚定读到的是动画中间
        位置；面板滑入动画在主窗定位后会把面板拉回锚定前的旧位置——
        真机上表现为两张卡片相互重叠。
        关：两段整体收起。分段 × 由 popup.closed 与面板 closeRequested
        分别接管（单关一段，两段全关自动整体收起）。
        """
        if getattr(self, "_ball_expand_mode", False):
            self._collapse_expand_combo()
            return
        s = store.active
        if not (self._is_live_acquiring() and s is not None
                and getattr(s, "is_live", False)):
            return
        popup = getattr(self, "_live_overview_popup", None)
        panel = getattr(self, "_live_channel_panel", None)
        if popup is None and panel is None:
            return
        self._ball_expand_mode = True
        if popup is not None:
            popup.show_popup()
            # 停掉开窗动画并立即落到最终停靠矩形（两段一次成装）
            popup._stop_anim()
            popup.setGeometry(popup._docked_rect())
            popup.setWindowOpacity(popup._target_opacity())
        if panel is not None:
            # 滑入动画终点=锚定前旧位置，展开路径必须跳过（animate=False）
            panel.show_panel(animate=False)
            # 组合拼装锚定后置：贴弹窗最终停靠矩形外侧
            self._anchor_channel_panel_to_ball()
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is not None:
            ball.set_active(True)

    def _collapse_expand_combo(self):
        """整体收起组合窗（再单击球 / 分段全关 / 球退场共用）。"""
        if not getattr(self, "_ball_expand_mode", False):
            return
        self._ball_expand_mode = False
        popup = getattr(self, "_live_overview_popup", None)
        panel = getattr(self, "_live_channel_panel", None)
        if popup is not None and popup.isVisible():
            popup.hide()
        if panel is not None and panel.isVisible():
            panel.hide_panel()
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is not None:
            ball.set_active(False)

    def _exit_expand_silently(self):
        """球退场/采集结束等路径清展开标志（调用方自会收族，不在此重复收窗）。"""
        self._ball_expand_mode = False

    def _expand_combo_anchor_panel(self):
        """组合拼装定位：面板紧贴趋势弹窗外侧（缝隙 COMBO_GAP_PX）。

        弹窗位置取 ``_docked_rect()``（最终停靠矩形）而非 frameGeometry()
        ——后者在开窗动画期间是中间态。弹窗实际落位在球左侧时面板自动去
        右侧，反之亦然（两窗合成一张宽卡、永不分居）；首选边屏幕放不下
        时整组翻到球的另一侧。
        """
        panel = getattr(self, "_live_channel_panel", None)
        ball = getattr(self, "_live_monitor_ball", None)
        popup = getattr(self, "_live_overview_popup", None)
        if panel is None or ball is None:
            return
        screen = (QGuiApplication.screenAt(ball.frameGeometry().center())
                  or QGuiApplication.primaryScreen())
        if screen is None:
            return
        avail = screen.availableGeometry()
        ag = ball.frameGeometry()
        w = panel.width()
        h = panel.height()
        gap = self.COMBO_GAP_PX
        popup_visible = popup is not None and popup.isVisible()
        popup_on_left = True
        if popup_visible:
            pg = popup._docked_rect()
            popup_on_left = pg.center().x() <= ag.center().x()
            x = (pg.right() + gap) if popup_on_left else (pg.left() - gap - w)
        else:
            x = ag.right() + 1 + getattr(panel, "CORNER_GAP", 8)
        if x < avail.left() or x + w > avail.right():
            if popup_visible:
                x = ((pg.left() - gap - w) if popup_on_left
                     else (pg.right() + gap))
            else:
                x = (ag.left() - getattr(panel, "CORNER_GAP", 8) - w)
        x = max(avail.left(), min(x, avail.right() - w))
        y = min(max(ag.top(), avail.top()), avail.bottom() - h + 1)
        panel.move(x, y)

    def _refresh_channel_panel(self):
        """把全部可见通道的最新值/峰值推送到全通道面板并同步显隐。

        数据源与球心最高温同源同门禁（store.active 的 Channel 元数据）；
        量程与琥珀档与球心共用（报警上限/温度轴同源），无效通道由面板
        以 "--" 灰态呈现；采样点数取会话 buffer 行数，时刻取本次刷新时刻。
        """
        panel = getattr(self, "_live_channel_panel", None)
        if panel is None:
            return
        self._refresh_channel_panel_visibility()
        s = store.active
        if s is None or not getattr(s, "is_live", False):
            return
        if not panel.isVisible():
            return
        custom_high = float(getattr(self, "_lm_ball_warn_high", 0.0))
        high = (custom_high if custom_high > 0.0
                else getattr(self.alarm_config, "temp_high", 100.0))
        panel.set_range(high, int(getattr(self, "_lm_ball_warn_pct", 90)))
        items = []
        for c in s.channels:
            if not (getattr(c, "visible", True) and getattr(c, "enabled", True)):
                continue
            items.append({
                "key": c.key,
                "name": c.display_name,
                "color": c.color or Theme.ACCENT,
                "value": c.last_value,
                "peak": c.peak,
            })
        panel.set_channels(items)
        panel.set_sample_info(int(s.n), time.strftime("%H:%M:%S"))

    def _on_channels_changed(self, _keys):
        """通道配置变化（改名/改色）→ 刷新通道表 + 重绘。"""
        if not self._loading:
            self.channel_panel.populate()
            self.refresh_plots()

    # ==================================================================
    #  从配置文件恢复状态
    # ==================================================================
    def _load_configs(self):
        # 初始化 datastore 统一管道的通道配置（名称/颜色/显隐持久化）
        store.init_config(CONFIG_DIR)
        try:
            ConfigIO.load_axis_config(self)
        except Exception:
            pass
        try:
            ConfigIO.load_a4_config(self)
        except Exception:
            pass
        try:
            ConfigIO.load_overview_config(self)
        except Exception:
            pass
        try:
            ConfigIO.load_rise_config(self)
        except Exception:
            pass
        try:
            ConfigIO.load_alarm_config(self)
        except Exception:
            pass
        try:
            ConfigIO.load_combo_layout_config(self)
        except Exception:
            pass
        self._load_font_config()
        self._load_name_list()
        self._load_color_config()
        self._load_acq_config()
        self._load_param_config()
        self._load_theme_config()
        self._load_channel_view_mode()
        self._load_storage_config()
        # 恢复波特率菜单勾选状态
        self._set_acq_baud(self._acq_baud)
        # 启动校正：跟随模式下报警上限对齐当前轴基础窗口（chart_renderer
        # 尚未创建时由 _sync_alarm_high_to_axis 内部守卫安全跳过刷新）
        self._sync_alarm_high_to_axis()

    def _load_font_config(self):
        try:
            d = ConfigIO.load_section("font", {}, ())
            level = d.get("level", "默认")
            if level in FONT_LEVEL_NAMES:
                self._font_level = level
                self._font = _build_font_dict(FONT_PRESETS[level])
                self._current_font = self._font
        except Exception:
            pass

    def _load_name_list(self):
        try:
            lst = ConfigIO.load_section("name_list", [], ())
            if isinstance(lst, list) and lst:
                self.name_list = lst
        except Exception:
            pass

    def _save_name_list(self):
        try:
            ConfigIO.save_section("name_list", self.name_list)
        except Exception:
            pass

    def _load_color_config(self):
        """加载配色模式。channels.json 的 color_mode 是权威来源（避免与
        color_config.json 各持一份导致模式切换不生效）。"""
        if store.config is not None:
            self.color_mode = store.config.color_mode
            return
        try:
            d = ConfigIO.load_section("color", {}, ())
            self.color_mode = d.get("mode", "categorical")
        except Exception:
            pass

    # ==================================================================
    #  UI 构建
    # ==================================================================
    def _init_ui(self):
        self._set_window_title("多通道温度分析仪")
        
        # 设置窗口图标
        try:
            from main import get_icon_path
            icon_path = get_icon_path("app", 256)
            if icon_path:
                from PyQt5.QtGui import QIcon
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass
        
        # 最小尺寸 = 启动/还原尺寸：该尺寸下内容水平、垂直均完整不截断。
        self.setMinimumSize(*self.DEFAULT_GEOMETRY)
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        # 初始化系统托盘
        self._init_tray_icon()

        # 真正退出应用前结束采集并关闭数据库（保证会话写入停止时间）
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_about_to_quit)

        # 子模块实例化
        self.chart_renderer = ChartRenderer(self)
        self.chart_renderer.bind_overview_hover(self.tab_all)
        self.stat_panel = StatPanel(
            self,
            view_host=getattr(self, "stat_detail_host", None),
        )
        self.combo_panel = ComboPanel(self)
        self.compare_panel = ComparePanel(self)
        self.export_mgr = ExportManager(self)
        self._build_export_drawer()

        # 连接组合图 resize/draw 信号
        self.tab_combo.canvas.mpl_connect("resize_event", self.chart_renderer._on_combo_resize)
        self.tab_combo.canvas.mpl_connect("draw_event", self.chart_renderer._on_combo_draw)

        self._apply_default_geometry()
        self._update_live_view_controls()

        # 连接 store 会话信号以更新会话切换条
        #（active_changed 已在 __init__ 统一连接，这里不重复连接避免双重触发）
        store.session_added.connect(self._on_session_added)
        store.session_closed.connect(self._on_session_closed)
        self._refresh_session_tabs()  # 初始更新

    def _init_tray_icon(self):
        """初始化系统托盘图标。"""
        try:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                self._tray_enabled = False
                return
            
            from PyQt5.QtGui import QIcon
            from main import get_icon_path
            
            tray_path = get_icon_path("tray", 64)
            if not tray_path:
                self._tray_enabled = False
                return
            
            self._tray_icon = QSystemTrayIcon(QIcon(tray_path), self)
            self._tray_icon.setToolTip("多通道温度分析仪")
            
            # 托盘菜单
            tray_menu = QMenu(self)
            
            show_action = tray_menu.addAction("显示主窗口")
            show_action.triggered.connect(self._show_from_tray)
            
            tray_menu.addSeparator()
            
            quit_action = tray_menu.addAction("退出")
            quit_action.triggered.connect(self._quit_from_tray)

            # 托盘底部「显示悬浮球」总开关：勾选=按「仅在线采集中显示」规则
            # 显示；取消勾选=悬浮球族即使在线采集中也退场。状态持久化到
            # live_monitor.ball_enabled，初值取 __init__ 里已就绪的缓存
            tray_menu.addSeparator()
            ball_action = tray_menu.addAction("显示悬浮球")
            ball_action.setCheckable(True)
            ball_action.setChecked(getattr(self, "_lm_ball_enabled", True))
            ball_action.triggered.connect(self._on_tray_ball_toggled)
            self._tray_ball_action = ball_action

            self._tray_menu = tray_menu
            self._tray_icon.setContextMenu(tray_menu)
            
            # 单击或双击托盘图标恢复窗口
            self._tray_icon.activated.connect(self._on_tray_activated)
            
            self._tray_icon.show()
            
        except Exception:
            self._tray_enabled = False
            self._tray_icon = None
    
    def _on_tray_activated(self, reason):
        """托盘图标激活事件：单击或双击立即显示主窗口。

        Windows 下左键单击触发 Trigger，右键点击由托盘弹出
        右键菜单，这里不处理 Context / MiddleClick。
        """
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()
    
    def _show_from_tray(self):
        """从托盘恢复主窗口：淡入呈现，与最小化原位淡出互为镜像。

        若恢复发生在最小化动效尚未播完时，先停掉淡出组（手动 stop 不触发
        finished，不会把刚恢复的窗口再隐藏）并收回引导光点层。
        """
        if getattr(self, "_tray_anim_active", False):
            if self._tray_anim is not None:
                self._tray_anim.stop()
            self._tray_anim_active = False
            self._tray_anim = None
            guide = self._tray_guide
            self._tray_guide = None
            if guide is not None:
                guide.close()
        self.showNormal()
        self.activateWindow()
        self.raise_()
        if self._restore_anim is not None:
            self._restore_anim.stop()   # 连续触发：重启淡入而非叠加
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(280)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._restore_anim = anim
        anim.start()
    
    def _quit_from_tray(self):
        """从托盘退出应用。"""
        if self._tray_icon:
            self._tray_icon.hide()
        QApplication.quit()

    def _on_tray_ball_toggled(self, checked):
        """托盘底部「显示悬浮球」开关：落盘 live_monitor.ball_enabled 并即时生效。

        load→改→save 保留段内其余键（与面板 panel_enabled 同契约）；显隐
        统一走 _sync_live_ball_visibility——取消勾选时悬浮球族即刻退场
        （趋势弹窗与全通道面板一并收回），重新勾选后按采集态与既有意愿复显。
        """
        self._lm_ball_enabled = bool(checked)
        try:
            cfg = ConfigIO.load_live_monitor_config()
            cfg["ball_enabled"] = bool(checked)
            ConfigIO.save_live_monitor_config(cfg)
        except Exception:
            pass
        self._sync_live_ball_visibility()
    
    def _build_toolbar(self):
        """创建简洁式综合工具栏，保留原有动作和业务槽函数。"""
        tb = QToolBar("综合工具栏")
        tb.setObjectName("mainToolbar")
        tb.setProperty("toolbarVariant", "flat")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        def style_action_button(action, object_name):
            """统一 QAction 在工具栏中生成的实际按钮尺寸。"""
            button = tb.widgetForAction(action)
            if button is None:
                return
            button.setObjectName(object_name)
            button.setMinimumHeight(30)

        # 数据唯一入口：打开文件 / 网络服务已迁入「数据」弹窗
        #（三标签 本机数据/远程数据/服务 + 「📂 打开文件」角标按钮）
        a_data = QAction("🗂 数据", self)
        a_data.setToolTip("打开数据弹窗：本机历史 / 远程获取 / 网络服务 / 导入文件")
        a_data.triggered.connect(self._open_history)
        tb.addAction(a_data)
        style_action_button(a_data, "toolbarHistoryButton")

        # Ctrl+O 全局快捷键：文件导入入口已迁入「数据」弹窗角标按钮
        self._shortcut_open_file = QShortcut(QKeySequence("Ctrl+O"), self)
        self._shortcut_open_file.activated.connect(self.on_open)

        # 串口和采集控制
        lbl_port = QLabel("串口:")
        # 样式由全局 QLabel#toolbarFieldLabel 规则提供，随主题自动跟随
        # （局部固化样式切主题后停留旧色）
        lbl_port.setObjectName("toolbarFieldLabel")
        tb.addWidget(lbl_port)
        self.cmb_port = PortComboBox()
        self.cmb_port.setEditable(True)  # 允许手动输入端口名
        self.cmb_port.setInsertPolicy(QComboBox.NoInsert)  # 输入不新增选项
        self.cmb_port.setSizeAdjustPolicy(QComboBox.AdjustToContents)  # 宽度自适应内容
        self.cmb_port.setMinimumContentsLength(5)
        # 宽度固定：串口描述（如 COM3 — USB-SERIAL CH340）长度不定，若随
        # 内容自适应会异步撑宽工具栏（启动扫描完成后），导致默认窗口宽度
        # 追不上、按钮被截断；完整描述由弹出列表加宽展示（showPopup）。
        self.cmb_port.setFixedWidth(260)
        self.cmb_port.set_port_refresher(self._refresh_ports)  # 弹出下拉前自动刷新
        self.cmb_port.activated.connect(self._on_port_selected)
        self._style_port_combo()
        self._start_port_scan_async()  # 启动时后台扫描 COM 口，不阻塞启动
        tb.addWidget(self.cmb_port)

        # 连接按钮保持原对象和槽函数，便于其他代码继续更新状态。
        self.btn_connect = QToolButton()
        self.btn_connect.setObjectName("toolbarConnectButton")
        self.btn_connect.setMinimumHeight(30)
        self.btn_connect.setText("🔌 连接")
        self.btn_connect.setToolTip("连接 / 断开串口设备")
        self.btn_connect.clicked.connect(self._toggle_connection)
        tb.addWidget(self.btn_connect)

        self.btn_start_acq = QToolButton()
        self.btn_start_acq.setObjectName("toolbarStartButton")
        self.btn_start_acq.setMinimumHeight(30)
        self.btn_start_acq.setText("▶ 开始采集")
        self.btn_start_acq.setToolTip("开始采集")
        self.btn_start_acq.clicked.connect(self._start_acquisition)
        tb.addWidget(self.btn_start_acq)

        self.btn_pause_acq = QToolButton()
        self.btn_pause_acq.setObjectName("toolbarPauseButton")
        self.btn_pause_acq.setMinimumHeight(30)
        self.btn_pause_acq.setText("⏸ 暂停")
        self.btn_pause_acq.setCheckable(True)
        self.btn_pause_acq.setEnabled(False)
        self.btn_pause_acq.clicked.connect(self._toggle_pause)
        tb.addWidget(self.btn_pause_acq)

        self.btn_stop_acq = QToolButton()
        self.btn_stop_acq.setObjectName("toolbarStopButton")
        self.btn_stop_acq.setMinimumHeight(30)
        self.btn_stop_acq.setText("⏹ 结束")
        self.btn_stop_acq.setEnabled(False)
        self.btn_stop_acq.setToolTip("结束采集并保存本次数据")
        # 仅按钮路径弹确认；设备拔出/关窗/退出等自动停止路径不弹（见 _stop_acquisition）
        self.btn_stop_acq.clicked.connect(
            lambda: self._stop_acquisition(ask_confirm=True))
        tb.addWidget(self.btn_stop_acq)

        self.btn_restart_acq = QToolButton()
        self.btn_restart_acq.setObjectName("toolbarRestartButton")
        self.btn_restart_acq.setMinimumHeight(30)
        self.btn_restart_acq.setText("🔄 重新采集")
        self.btn_restart_acq.setVisible(False)
        self.btn_restart_acq.setToolTip("清空当前数据，重新开始采集")
        self.btn_restart_acq.clicked.connect(self._restart_acquisition)
        tb.addWidget(self.btn_restart_acq)

        # 数据平滑总开关：设置页和工具栏共用同一处理参数。
        self.btn_smooth_toolbar = QToolButton()
        self.btn_smooth_toolbar.setObjectName("toolbarSmoothSwitch")
        self.btn_smooth_toolbar.setMinimumHeight(30)
        self.btn_smooth_toolbar.setText("平滑：关")
        self.btn_smooth_toolbar.setToolTip("打开后对处理后的温度曲线进行整体平滑")
        self.btn_smooth_toolbar.setCheckable(True)
        self.btn_smooth_toolbar.setChecked(bool(getattr(self, "_saved_params", {}).get("smooth", False)))
        self.btn_smooth_toolbar.toggled.connect(self._on_toolbar_smooth_toggled)
        tb.addWidget(self.btn_smooth_toolbar)

        # 回到最新：唯一实时视图操作按钮，点击后以全局趋势图（0～最新）展示。
        self.btn_return_latest = QToolButton()
        self.btn_return_latest.setObjectName("toolbarReturnLatestButton")
        self.btn_return_latest.setMinimumHeight(30)
        self.btn_return_latest.setText("回到最新")
        self.btn_return_latest.setToolTip("回到最新：以全局趋势图（从0到当前最新）展示")
        self.btn_return_latest.setProperty("highlighted", "false")  # 显式设置初始值，确保属性选择器正确匹配
        self.btn_return_latest.clicked.connect(self._return_to_latest)
        tb.addWidget(self.btn_return_latest)

        # 历史数据编辑：对已加载/已结束会话做重采样等后处理（录制中不可用）
        self.btn_edit_data = QToolButton()
        self.btn_edit_data.setObjectName("toolbarEditDataButton")
        self.btn_edit_data.setMinimumHeight(30)
        self.btn_edit_data.setText("🛠 编辑")
        self.btn_edit_data.setToolTip(
            "编辑当前会话数据：重采样、平滑（实时采集进行中不可用，"
            "不影响真实采集值与报警）")
        self.btn_edit_data.setEnabled(False)
        self.btn_edit_data.clicked.connect(self._open_data_edit_dialog)
        tb.addWidget(self.btn_edit_data)
        # 注：不在此处调用 _update_live_view_controls()，因为 chart_renderer 还未创建
        # 该调用移至 __init__ 最后（第 782 行），确保所有组件都已创建

        # 导出、设置
        btn_export = QToolButton()
        btn_export.setObjectName("toolbarExportButton")
        btn_export.setMinimumHeight(30)
        btn_export.setText("📤 导出")
        btn_export.setToolTip("导出图表 / 数据 / 报告")
        self.btn_export = btn_export
        btn_export.clicked.connect(self.open_export_dialog)
        tb.addWidget(btn_export)

        a_sett = QAction("⚙ 设置", self)
        a_sett.setShortcut("Ctrl+,")
        a_sett.setToolTip("参数设置 (Ctrl+,)")
        a_sett.triggered.connect(self.open_settings)
        tb.addAction(a_sett)
        style_action_button(a_sett, "toolbarSettingsButton")

        # 退出入口收口：工具栏不放退出按钮（点 X 改为询问「最小化还是退出」），
        # 仅保留 Ctrl+Q 快捷键直达退出确认（action 不上工具栏，界面无按钮）
        a_quit = QAction("退出程序", self)
        a_quit.setShortcut("Ctrl+Q")
        a_quit.setToolTip("退出程序 (Ctrl+Q)")
        a_quit.triggered.connect(self._confirm_exit_and_close)
        self.addAction(a_quit)

        # 右侧空白撑开：让工具栏按钮靠左对齐
        # 注：spacer 设置透明背景，避免空白区域显示不协调的颜色
        _spacer = QWidget()
        _spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        _spacer.setStyleSheet("background: transparent;")
        tb.addWidget(_spacer)
        # 持有引用：首次打开默认窗口宽度按工具栏完整显示计算
        self.main_toolbar = tb

    def _build_statusbar(self):
        status_bar = self.statusBar()
        # 状态栏是实时信息窗口，不再仅承载辅助文字：专属面板使用两行
        # 深底亮字布局，临时 showMessage 轨道仍由 QStatusBar 保留。
        status_bar.setFixedHeight(self.STATUSBAR_HEIGHT)
        status_bar.setSizeGripEnabled(False)

        self.status_info_panel = StatusInfoPanel(status_bar)
        self.lbl_acq_status = self.status_info_panel.lbl_acq_status
        self.lbl_point_count = self.status_info_panel.lbl_point_count
        self.lbl_dual_view_info = self.status_info_panel.lbl_dual_view_info
        self.lbl_mem_usage = self.status_info_panel.lbl_mem_usage
        self.lbl_live_status = self.status_info_panel.lbl_live_status
        self.lbl_elapsed = self.status_info_panel.lbl_elapsed
        self._alarm_label = self.status_info_panel._alarm_label
        self.lbl_net_status = self.status_info_panel.lbl_net_status
        self.lbl_source = self.status_info_panel.lbl_source
        self._hb = self.status_info_panel.heartbeat
        self.lbl_point_count.setObjectName("pointCountLabel")
        self.lbl_elapsed.setObjectName("elapsedLabel")
        self._alarm_label.setObjectName("alarmStatusLabel")
        self.lbl_net_status.setObjectName("netStatusLabel")
        self.lbl_live_status.setObjectName("liveStatusLabel")
        status_bar.addPermanentWidget(self.status_info_panel, 1)
        status_bar.showMessage("就绪")
        # 操作结果一级轻提示（连接/采集/断开等瞬时反馈；_toast 未建时 _notify 静默）
        self._toast = StatusToast(self)
        # 采集启动三步进度卡片（真实事件驱动打勾；点击开始采集即居中显示）
        self._acq_step_card = AcquisitionStepCard(self)
        # 统一加载反馈卡片（历史加载/导入/导出/重算/远程落库等耗时操作）
        self._busy_card = BusyCard(self)
        # 居中反馈卡片清单：轻提示显示时自动让位到可见卡片下方（status_toast）
        self.centerFeedbackCards = (self._acq_step_card, self._busy_card)

    def _update_net_status_label(self):
        """刷新状态栏网络角色指示：服务端（运行服务+对外 IP）/ 客户端（加载远程数据）/ 未启用。"""
        label = getattr(self, "lbl_net_status", None)
        if label is None:
            return

        def _set_network_text(full_text: str, role: str) -> None:
            """显示网络角色；长地址在状态栏内省略，完整值放入 tooltip。"""
            label.setToolTip(full_text)
            max_width = label.maximumWidth()
            if max_width >= 16777215:
                display_text = full_text
            else:
                display_text = QFontMetrics(label.font()).elidedText(
                    full_text, Qt.ElideRight, max(1, max_width))
            label.setText(display_text)
            self._set_status_label_semantic(label, role, emphasis=True)

        try:
            if self._client_mode_active:
                # 客户端模式：附上当前连接设备地址（远程数据来源一目了然）
                device = getattr(self, "_remote_device_label", "")
                text = f"📡 客户端 · {device}" if device else "📡 客户端"
                _set_network_text(text, "current")
                return
            running = []
            tcp_port = None
            if self._service_manager is not None:
                for name in ("tcp", "http", "pairing_code"):
                    st = self._service_manager.get_service_status(name)
                    if st.enabled:
                        running.append(name)
                        if name == "tcp":
                            tcp_port = int(st.port) if st.port else None
            if running:
                # 对外 IP 与窗口标题/服务弹窗一致：通告地址优先，其次本机 IP
                ip = (getattr(self._service_manager, "advertise_ip", "") or "").strip()
                ip = ip or (getattr(self, "_last_local_ip", "") or "127.0.0.1")
                if tcp_port:
                    ip += f":{tcp_port}"
                _set_network_text(f"📡 网络服务端 · {ip}", "success")
            else:
                _set_network_text("网络：未启用", "info")
        except Exception:
            # 状态刷新失败不阻塞主流程
            _set_network_text("网络：未启用", "info")

    def _return_to_latest(self):
        if self.chart_renderer is None:
            return
        if self.dataset is None:
            self._update_live_view_controls()
            return
        self.chart_renderer.return_to_latest()
        self._update_live_view_controls()

    @staticmethod
    def _set_current_tab_semantic(tab_widget, index):
        """记录标签当前项并重放 QSS，让当前区域始终保持高亮。"""
        if tab_widget is None:
            return
        tab_widget.setProperty("semanticRole", "current")
        tab_widget.setProperty("currentTabSemanticRole", "current")
        tab_widget.setProperty("currentTabIndex", int(index))
        style = tab_widget.style()
        if style is not None:
            style.unpolish(tab_widget)
            style.polish(tab_widget)
        tab_widget.update()

    def _sync_live_view_actions(self, mode):
        self._update_live_view_controls()

    def _style_live_status_labels(self, tab, status):
        """根据实时状态给底部状态栏实时提示着色。"""
        label = getattr(self, "lbl_live_status", None)
        if label is None:
            return
        if not status.get("is_live", False):
            state_role = "info"
        elif not status.get("is_recording", False):
            state_role = "warning"
        elif status.get("mode") == "manual":
            state_role = "current"
        else:
            state_role = "success"
        self._set_status_label_semantic(label, state_role)
        # 累计采集时长胶囊：录制中绿、暂停/已结束橙、非实时隐藏
        elapsed_label = getattr(self, "lbl_elapsed", None)
        if elapsed_label is not None:
            if not elapsed_label.text():
                elapsed_label.setVisible(False)
            else:
                # 单色文字 + 同色边框、无底色填充（2026-08-30 定稿）；
                # 录制中绿、暂停/离线橙，前景按卡底自动补偿保证可读
                role = ("success" if status.get("is_recording", False)
                        else "warning")
                self._set_status_label_semantic(elapsed_label, role, emphasis=True)
                elapsed_label.setVisible(True)
        # 采集点数标签（状态栏采集分区行2）：采集中绿、暂停橙、历史文件
        # 主题色；纯色文字不加粗（与行1同规则），无内容时隐藏
        count_label = getattr(self, "lbl_point_count", None)
        if count_label is None:
            return
        if not count_label.text():
            count_label.setVisible(False)
            return
        if status.get("is_live", False) and not status.get("is_recording", False):
            count_role = "warning"
        elif status.get("is_live", False):
            count_role = "success"
        else:
            count_role = "info"
        self._set_status_label_semantic(count_label, count_role, emphasis=True)
        count_label.setVisible(True)

    def _update_live_view_controls(self):
        """同步"回到最新"按钮状态：实时与离线数据均可用。"""
        renderer = getattr(self, "chart_renderer", None)
        try:
            status = renderer.live_view_status() if renderer is not None else None
        except Exception:
            status = None
        try:
            mode = (renderer._normalized_live_view_mode()
                    if renderer is not None else "auto")
        except Exception:
            mode = "auto"

        return_button = getattr(self, "btn_return_latest", None)
        if return_button is None:
            return
        has_data = bool(self.dataset is not None)
        is_live = bool(status and status.get("is_live", False))
        return_button.setEnabled(has_data)
        highlighted = bool(has_data and mode == "manual")
        return_button.setProperty("highlighted", highlighted)
        return_button.setProperty(
            "semanticRole", "current" if highlighted else "info")
        if not has_data:
            tooltip = "当前没有可展示的数据"
        elif is_live:
            tooltip = "回到最新：以全局趋势图（从0到当前最新）展示"
        else:
            tooltip = "回到最新：复位到全量视图（显示全部数据）"
        return_button.setToolTip(tooltip)
        # 刷新样式：强制更新确保样式完全应用
        style = return_button.style()
        if style is not None:
            style.unpolish(return_button)
            style.polish(return_button)
            return_button.setAttribute(Qt.WA_StyledBackground, True)  # 确保背景样式正确应用
            return_button.updateGeometry()  # 强制更新几何布局
            return_button.update()

    def _on_recording_changed(self, _is_recording, _path):
        """录制开始/停止后同步实时标签与工具栏状态。"""
        renderer = getattr(self, "chart_renderer", None)
        if renderer is not None:
            renderer.update_live_status_labels()
        self._update_live_view_controls()
        self._update_edit_button_state()

    def _build_central(self):
        """主体：左侧通道面板 | 右侧标签页画布"""
        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("mainSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        # ── 左侧：通道面板 ──
        left = self._build_left_panel()
        left.setMinimumWidth(self.LEFT_PANEL_WIDTH)
        left.setMaximumWidth(self.LEFT_PANEL_WIDTH)
        self._left_panel_host = left   # 滚动条占位联动时调整其宽度
        splitter.addWidget(left)

        # ── 右侧：标签页画布 ──
        right = self._build_canvas()
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([270, 1010])
        # 左侧内容区固定，窗口额外空间全部交给右侧画布。
        splitter.handle(1).setEnabled(False)

        self.main_splitter = splitter
        # 使用覆盖容器承载 QSplitter 和右侧导出抽屉，避免 QSplitter
        # 重新布局时覆盖抽屉的动画位置。
        host = QWidget()
        host.setObjectName("mainContentHost")
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)
        host_layout.addWidget(splitter)
        self.main_content_host = host
        self.setCentralWidget(host)

    # ==================================================================
    #  左面板
    # ==================================================================
    def _build_left_panel(self) -> QWidget:
        """左侧面板：通道信息。

        采集、连接、双区视图、内存占用等状态均集中在底部状态栏；
        左侧仅保留通道卡片列表与温度轴快速调节区。
        """
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(2, 2, 2, 2)
        vbox.setSpacing(2)

        # 状态栏已承载所有状态信息，左侧直接进入通道分区。
        # ── 通道分区标题（固定高度，避免无 stretch 时被布局拉伸占满空白）──
        # 样式由全局 QLabel#leftSectionLabel 规则提供，随主题自动跟随
        lbl_channels = QLabel("通道")
        lbl_channels.setObjectName("leftSectionLabel")
        lbl_channels.setFixedHeight(16)
        vbox.addWidget(lbl_channels)

        # ── 通道卡片列表（显隐 / 名称 / 颜色 / 当前值 / 最高·平均；
        # 风格=卡表融合行卡或经典大卡，见 channel_view.mode）──
        self.channel_panel = ChannelPanel(self)
        # 启动加载的视图模式在 populate 之前下发（决定 ChannelGroup 建哪类卡）
        self.channel_panel.set_view_mode(self._channel_view_mode)
        # 通道区填满左侧剩余高度；通道过多时只由内部 QScrollArea 滚动。
        vbox.addWidget(self.channel_panel, 1)
        # 滚动条出现 / 消失 → 面板宽度联动（滚动条空间额外分配，不压卡片）
        self.channel_panel.scrollbar_space_changed.connect(
            self._on_left_scrollbar_space)

        # ── 底部：温度轴基础窗口快速调节区（温度轴统一智能模式）──
        self.axis_quick_panel = AxisBasePanel()
        self.axis_quick_panel.set_base(
            self.ax_temp_base_lo, self.ax_temp_base_hi)
        self.axis_quick_panel.valueChanged.connect(self._on_axis_quick_changed)
        vbox.addWidget(self.axis_quick_panel)
        return panel

    def _on_left_scrollbar_space(self, needed: bool) -> None:
        """左面板滚动需求变化 → 面板额外增 / 减滚动条宽度。

        266px 固定卡片恰好铺满 270px 面板，滚动条出现若不加宽会从视口
        里挤占卡片空间（卡片右侧被压住）。此处把滚动条实际宽度额外
        分配给面板：有滚动 = 270 + 滚动条宽，无滚动 = 270（最大化等
        无滚动条场景观感与原来完全一致）。
        """
        sb = self.channel_panel._scroll.verticalScrollBar()
        width = self.LEFT_PANEL_WIDTH + (sb.sizeHint().width() if needed else 0)
        panel = getattr(self, "_left_panel_host", None)
        if panel is None:
            return
        panel.setMinimumWidth(width)
        panel.setMaximumWidth(width)
        # min/max 变化不必然触发 splitter 重排，显式重分配（右侧吃掉剩余空间）
        splitter = getattr(self, "main_splitter", None)
        if splitter is not None:
            splitter.setSizes([width, max(0, splitter.width() - width)])

    def _on_axis_quick_changed(self):
        """左侧底部基础窗口调节 → 保存配置 → 仅刷新温度轴范围。

        曲线数据未变，走轻量路径只重算 ylim（refresh_axis_only），
        避免大文件 / 大数据量下每次微调都整图重建造成卡顿。

        健壮性：整段链路（取数 / 落盘 / 报警跟随 / 轴重算 / 标签回写 /
        状态栏）统一 try/except，任何一步异常都记录 [AXIS] 日志并仍刷新
        “当前生效范围”标签与状态栏，保证按钮“点了就有反馈”；在线 / 离线
        共用同一路径，状态回写与刷新语义一致。
        """
        try:
            lo, hi = self.axis_quick_panel.base()
            old_lo, old_hi = self.ax_temp_base_lo, self.ax_temp_base_hi
            if lo != old_lo or hi != old_hi:
                print(f"[AXIS] 用户调节温度轴基础窗口: "
                      f"{old_lo:g}~{old_hi:g} → {lo:g}~{hi:g}℃", flush=True)
            self.ax_temp_base_lo, self.ax_temp_base_hi = lo, hi
            ConfigIO.save_axis_config(self)
            self._sync_alarm_high_to_axis()
            # 用户显式调窗口的强意图复位已在 refresh_axis_only 入口完成
            # （retarget_temp_axis），此处保持单入口调用即可
            if getattr(self, "chart_renderer", None) is not None:
                self.chart_renderer.refresh_axis_only()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[AXIS] 温度轴基础窗口调节处理异常: {e}", flush=True)
        finally:
            # 无论刷新是否成功都同步只读“当前生效范围”标签与状态栏，
            # 避免出现“按钮失效且无任何反馈”的观感
            self._update_axis_quick_range()
            bar = getattr(self, "statusBar", None)
            if bar is not None:
                bar().showMessage(
                    f"温度轴基础窗口: {self.ax_temp_base_lo:g}"
                    f" ~ {self.ax_temp_base_hi:g}℃", 2000)

    def _sync_axis_quick_panel(self):
        """从当前配置刷新左侧底部调节区（设置页应用后调用）。"""
        if getattr(self, "axis_quick_panel", None) is None:
            return
        self.axis_quick_panel.set_base(
            self.ax_temp_base_lo, self.ax_temp_base_hi)
        self._update_axis_quick_range()

    def _update_axis_quick_range(self):
        """把图表当前实际生效的温度轴范围同步到只读标签。"""
        if getattr(self, "axis_quick_panel", None) is None:
            return
        renderer = getattr(self, "chart_renderer", None)
        cur = getattr(renderer, "_auto_temp_range", None)
        if cur is None:
            return
        self.axis_quick_panel.set_current_range(cur[0], cur[1])

    # ==================================================================
    #  右侧画布
    # ==================================================================
    def _build_canvas(self) -> QWidget:
        """右侧画布：会话切换条（多会话时）+ 标签页画布。"""
        wrapper = QWidget()
        vbox = QVBoxLayout(wrapper)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        # ── 会话切换条（多会话时显示）──
        tab_row = QWidget()
        tab_row_layout = QHBoxLayout(tab_row)
        tab_row_layout.setContentsMargins(0, 0, 0, 0)
        tab_row_layout.setSpacing(4)
        self._session_tab_bar = QTabBar()
        self._session_tab_bar.setDocumentMode(True)
        self._session_tab_bar.setDrawBase(False)
        self._session_tab_bar.setExpanding(False)
        self._session_tab_bar.setTabsClosable(True)
        self._session_tab_bar.hide()  # 会话数 ≤1 时隐藏
        self._session_tab_bar.currentChanged.connect(self._on_session_tab_changed)
        self._session_tab_bar.tabCloseRequested.connect(
            self._on_session_tab_close_requested)
        tab_row_layout.addWidget(self._session_tab_bar)
        # 旧「监控悬浮窗」工具栏勾选按钮已移除：桌面悬浮球是唯一开关入口，
        # 二者并存会重复（用户反馈「两个已经重叠了」）。
        tab_row_layout.addStretch(1)
        vbox.addWidget(tab_row)

        # ── 标签页 ──
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # 收集所有 PlotTab（主题切换时统一刷新 Figure/Axes 面颜色）
        self._plot_tabs: list = []

        # Tab 0~3：整体趋势 + 前N分钟
        self.tab_all = PlotTab()
        self.tabs.addTab(self.tab_all, "整体趋势")
        self._plot_tabs.append(self.tab_all)

        self.tab_10 = PlotTab()
        self.tabs.addTab(self.tab_10, f"前{_fmt_win_int(self.win_front[0])}分钟")
        self._plot_tabs.append(self.tab_10)

        self.tab_20 = PlotTab()
        self.tabs.addTab(self.tab_20, f"前{_fmt_win_int(self.win_front[1])}分钟")
        self._plot_tabs.append(self.tab_20)

        self.tab_30 = PlotTab()
        self.tabs.addTab(self.tab_30, f"前{_fmt_win_int(self.win_front[2])}分钟")
        self._plot_tabs.append(self.tab_30)

        # Tab 4：单通道对比
        self.idx_single = self.tabs.count()
        self.tab_single = PlotTab()
        single_wrapper = self._build_single_tab()
        self.tabs.addTab(single_wrapper, "单通道对比")
        self._plot_tabs.append(self.tab_single)

        # Tab 5：组合图
        self.idx_combo = self.tabs.count()
        self.tab_combo = PlotTab()
        self.tabs.addTab(self.tab_combo, "组合图")
        self._plot_tabs.append(self.tab_combo)
        # 组合图最小画布尺寸：任何窗口大小下边距/间隔不被 clamp 导致文字重叠
        # （chart_renderer 实例此时尚未创建，用类方法）
        min_w, min_h = ChartRenderer.combo_min_canvas_size()
        self.tab_combo.setMinimumSize(min_w, min_h)

        # Tab 6：温升统计
        self.idx_stat = self.tabs.count()
        stat_widget = self._build_stat_tab()
        self.tabs.addTab(stat_widget, "温升统计")

        # 标签切换信号
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._set_current_tab_semantic(self.tabs, self.tabs.currentIndex())

        vbox.addWidget(self.tabs, 1)
        return wrapper

    def _build_single_tab(self) -> QWidget:
        """单通道对比标签：画布 + 上方勾选框"""
        wrapper = QWidget()
        vbox = QVBoxLayout(wrapper)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)

        # 勾选框容器
        self.chk_compare_host = QWidget()
        self.chk_compare_lay = QHBoxLayout(self.chk_compare_host)
        self.chk_compare_lay.setContentsMargins(8, 4, 8, 4)
        self.chk_compare_lay.setSpacing(8)
        self.chk_compare_lay.addStretch(1)

        vbox.addWidget(self.chk_compare_host)
        vbox.addWidget(self.tab_single, 1)
        return wrapper

    def _build_stat_tab(self) -> QWidget:
        """温升统计标签：汇总表格 + 选中通道的阶段状态图。"""
        wrapper = QWidget()
        vbox = QVBoxLayout(wrapper)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(6)

        # 统计表格
        self.tbl_stat = QTableWidget(0, 13)
        headers = ["通道", "状态", "起始温度", "快速转慢", "慢转稳态",
                    "快段时长", "慢段时长", "稳态时长", "快段速率",
                    "慢段速率", "稳态均值", "稳态波动", "剔除点数"]
        self.tbl_stat.setHorizontalHeaderLabels(headers)
        sh = self.tbl_stat.horizontalHeader()
        # Columns fill the available width; at narrow window sizes the table
        # keeps readable minimums and lets the user scroll horizontally.
        sh.setMinimumSectionSize(78)
        sh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for i in range(1, 13):
            sh.setSectionResizeMode(i, QHeaderView.Stretch)
        self.tbl_stat.setColumnWidth(0, 120)
        self.tbl_stat.setWordWrap(False)
        self.tbl_stat.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tbl_stat.setSizeAdjustPolicy(QTableWidget.AdjustToContentsOnFirstShow)
        self.tbl_stat.verticalHeader().setVisible(False)
        self.tbl_stat.verticalHeader().setDefaultSectionSize(22)
        self.tbl_stat.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # 统计表按通道整行单选，避免逐单元格焦点框造成视觉噪声。
        self.tbl_stat.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_stat.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl_stat.setMinimumHeight(200)
        vbox.addWidget(self.tbl_stat)

        # 下方由 StatPanel 填充：通道选择 + 原始/平滑曲线 + 三阶段着色。
        self.stat_detail_host = QWidget()
        self.stat_detail_host.setObjectName("riseAnalysisDetailHost")
        vbox.addWidget(self.stat_detail_host, 1)

        return wrapper

    # ==================================================================
    #  菜单/工具栏动作
    # ==================================================================
    def on_open(self) -> None:
        """文件导入 → 后台解析 → store.add_session → active_changed 自动刷新通道表和图表。

        C1：pandas 解析移至后台线程，避免大文件冻结 UI；store.add_session（改 store +
        发信号）留在 UI 线程槽里完成，避免并发竞态。
        """
        if self._reject_if_busy("导入文件"):
            return
        last_dir = ConfigIO.load_last_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "打开温度数据文件", last_dir,
            "数据文件 (*.xlsx *.xls *.csv *.txt *.tpx);;所有文件 (*.*)")
        if not path:
            return
        self._start_import(path)

    def _start_import(self, path: str) -> None:
        """直接导入指定路径（文件关联双击 / on_open 共用；不弹文件对话框）。"""
        # 打开本地文件 → 停止远程监控（异步落库）并退出客户端角色
        self._stop_remote_monitor()
        self._client_mode_active = False
        self._update_net_status_label()
        self._sync_client_mode_banner()
        ConfigIO.save_last_dir(path)
        # 解析移至后台线程；add_session 留在 _on_file_loaded（UI 线程）
        self._importing = True
        self._import_path = path
        self.statusBar().showMessage("正在导入，请稍候…", 0)
        # 统一加载反馈：解析期间转圈；挂载阶段在 _on_file_loaded 细分
        self._busy_card.begin(f"正在导入 {os.path.basename(path)}…")
        from device.datastore import loader
        self._file_worker = _BackgroundWorker(loader.load_session, path, config=store.config)
        self._file_worker.done.connect(self._on_file_loaded)
        self._file_worker.failed.connect(self._on_file_load_failed)
        self._file_worker.finished.connect(self._file_worker.deleteLater)
        self._file_worker.start()

    def _on_file_loaded(self, s) -> None:
        """文件解析完成（UI 线程）：挂载会话并刷新。"""
        self._importing = False
        self.statusBar().clearMessage()
        if s is None:
            self._busy_card.finish()
            return
        # add_session → active_changed 同步执行全量重算 + 首绘 + 统计表
        #（UI 线程重活）：先换阶段文字并重绘，再干重活，结束后收卡
        self._busy_card.set_text("正在重建曲线与统计…")
        try:
            store.add_session(s, activate=True)
        finally:
            self._busy_card.finish()
        self._data_mode = "file"
        path = getattr(self, "_import_path", "")
        self._set_window_title(f"多通道温度分析仪 — {os.path.basename(path)}")
        self.statusBar().showMessage(f"已加载：{os.path.basename(path)} ({s.n} 条记录)")
        # 完成反馈升级为居中轻提示（加载卡片收起后给出明确结果）
        self._notify(
            f"已加载 {os.path.basename(path)}（{s.n} 条记录）", "success")
        # 导入成功 → 关闭「数据」弹窗（与本机会话加载后行为对齐；
        # 角标「打开文件」路径导入时弹窗正开着）
        dlg = getattr(self, "_history_dialog", None)
        if dlg is not None and dlg.isVisible():
            dlg.close()

    def _on_file_load_failed(self, msg: str) -> None:
        """文件解析失败（UI 线程）。"""
        self._importing = False
        self._busy_card.finish()
        self.statusBar().clearMessage()
        store.error.emit(f"无法加载文件：\n{getattr(self, '_import_path', '')}\n\n{msg}")

    def _any_service_running(self) -> bool:
        """本机是否有网络服务在运行（远程页互斥与状态栏指示共用）。"""
        if self._service_manager is None:
            return False
        try:
            return any(
                self._service_manager.get_service_status(n).enabled
                for n in ("tcp", "http", "pairing_code"))
        except Exception:
            return False

    def _open_history(self):
        """打开「数据」对话框（三标签 本机数据/远程数据/服务 + 打开文件
        角标按钮；实例复用）。

        对话框由主窗口持有：关闭只隐藏；本机数据库连接在关闭时释放，
        重开时重新打开；远程页连接由用户管理（监控不受关窗影响），
        重开时自动重连上次设备；服务页（原「🌐 服务」窗口内容）注入
        服务管理器，服务启停经 service_changed 回流同步状态栏与标题。
        """
        from ui.dialogs.history_dialog import HistoryDialog
        dlg = getattr(self, "_history_dialog", None)
        if dlg is None:
            dlg = HistoryDialog(parent=self)
            dlg.load_requested.connect(self._on_history_load_requested)
            dlg.remote_connected.connect(self._on_remote_connected)
            dlg.remote_disconnected.connect(self._on_remote_disconnected)
            dlg.remote_fetch_requested.connect(
                self._on_remote_fetch_requested)
            dlg.remote_stop_monitor.connect(self._stop_remote_monitor)
            # 角标「📂 打开文件」→ 与原工具栏文件入口同一处理链路
            dlg.open_file_requested.connect(self.on_open)
            dlg.service_changed.connect(self._on_service_changed)
            self._history_dialog = dlg

        # 重开时重新打开本机数据库（closeEvent 已释放连接）
        if dlg._history_db is None:
            dlg._open_db(dlg._db_path)
        # 服务页：注入服务管理器（启动时已初始化；注入只记录状态，
        # 服务页懒创建到首次切到「服务」标签）
        dlg.set_service_manager(self._service_manager)
        # 同步服务端互斥：本机服务运行中 → 远程页禁用连接
        dlg.set_remote_server_active(self._any_service_running())
        # 同步客户端模式互斥横幅（服务页未创建时仅记录状态）
        dlg.set_client_mode_active(self._client_mode_active)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        dlg.maybe_reconnect_remote()

    def _sync_client_mode_banner(self) -> None:
        """把客户端模式互斥状态推送到「数据」弹窗服务页（未打开时静默）。"""
        dlg = getattr(self, "_history_dialog", None)
        if dlg is not None:
            dlg.set_client_mode_active(self._client_mode_active)

    def _on_history_load_requested(self, info: dict):
        """处理历史会话加载请求：用户确认 → 关闭历史对话框 → 后台解析 → UI 线程挂载。

        C1：DB 查询 + 数组构建移至后台线程（store.parse_session_from_db，纯解析
        线程安全）；add_session 与界面刷新在 _on_history_loaded（UI 线程）完成。
        加载结果通过 sender._last_load_ok 回写对话框。
        """
        sender = self.sender()
        session_id = info.get('session_id', '')
        session_name = info.get('session_name', '') or session_id
        if not session_id:
            self._mark_history_load(sender, False)
            return

        # 用户确认加载：采集中附注「采集将继续进行不受影响」
        acq_note = ""
        live = store.live
        if live is not None and live.is_recording:
            acq_note = "\n当前正在采集中，采集将继续进行不受影响。"

        reply = QMessageBox.question(
            self, "确认加载",
            f"即将加载会话「{session_name}」并打开为独立页面。{acq_note}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply == QMessageBox.No:
            self._mark_history_load(sender, False)
            return

        # 加载本机历史 → 停止远程监控（异步落库后切换）
        self._stop_remote_monitor()

        # 关闭历史对话框（复用隐藏机制）
        if hasattr(sender, "close"):
            try:
                sender.close()
            except Exception:
                pass

        db_path = info.get('db_path', '')
        from device.database.history_db import HistoryDatabase
        try:
            hdb = HistoryDatabase(db_path)
        except Exception as e:
            QMessageBox.warning(self, "打开数据库失败", f"无法打开数据库：\n{e}")
            self._mark_history_load(sender, False)
            return

        # 解析移至后台；hdb 在槽里关闭，info/sender 暂存供槽使用
        self._history_info = info
        self._history_sender = sender
        self._history_hdb = hdb
        self._history_loading = True
        self.statusBar().showMessage("正在从历史数据库加载…", 0)
        # 统一加载反馈：查询阶段转圈；_on_history_loaded 内细分到重建阶段
        self._busy_card.begin("正在从历史数据库加载会话…")
        self._history_worker = _BackgroundWorker(store.parse_session_from_db, hdb, session_id)
        self._history_worker.done.connect(self._on_history_loaded)
        self._history_worker.failed.connect(self._on_history_load_failed)
        self._history_worker.finished.connect(self._history_worker.deleteLater)
        self._history_worker.start()

    @staticmethod
    def _mark_history_load(sender, ok: bool) -> None:
        if sender is not None:
            try:
                sender._last_load_ok = bool(ok)
            except Exception:
                pass

    def _close_history_hdb(self) -> None:
        hdb = getattr(self, "_history_hdb", None)
        if hdb is not None:
            try:
                hdb.close()
            except Exception:
                pass
            self._history_hdb = None

    def _on_history_loaded(self, sess) -> None:
        """历史会话解析完成（UI 线程）：关闭 hdb、挂载会话、刷新界面。"""
        self._close_history_hdb()
        self.statusBar().clearMessage()
        info = getattr(self, "_history_info", {}) or {}
        sender = getattr(self, "_history_sender", None)
        if sess is None:
            self._busy_card.finish()
            self._history_loading = False
            QMessageBox.warning(self, "加载失败", "无法从数据库加载该会话")
            self._mark_history_load(sender, False)
            return
        # add_session → active_changed 同步执行全量重算 + 首绘 + 统计表
        #（UI 线程重活）：先换阶段文字并重绘，再干重活，结束后收卡
        self._busy_card.set_text("正在重建曲线与统计…")
        try:
            store.add_session(sess, activate=True)
        finally:
            self._busy_card.finish()
            self._history_loading = False
        self._data_mode = "file"
        name = info.get('session_name', '') or sess.title
        self._set_window_title(f"多通道温度分析仪 — 历史: {name}")
        self.statusBar().showMessage(f"已从历史数据库加载: {name}", 5000)
        # 完成反馈升级为居中轻提示（加载卡片收起后给出明确结果）
        self._notify(f"已加载历史会话：{name}", "success")
        # 加载本地历史会话 → 回到本地模式（退出客户端角色）
        self._client_mode_active = False
        self._update_net_status_label()
        self._sync_client_mode_banner()
        self._mark_history_load(sender, True)

    def _on_history_load_failed(self, msg: str) -> None:
        """历史会话解析失败（UI 线程）。"""
        self._close_history_hdb()
        self._busy_card.finish()
        self._history_loading = False
        self.statusBar().clearMessage()
        self._mark_history_load(getattr(self, "_history_sender", None), False)
        store.error.emit(f"历史数据库加载失败：\n{msg}")

    def _on_service_changed(self):
        """服务状态变更：持久化当前勾选配置并更新状态栏。"""
        if self._service_manager:
            # 持久化：以实际运行状态为准（勾选即启停，状态已同步）
            try:
                tcp_st = self._service_manager.get_service_status('tcp')
                http_st = self._service_manager.get_service_status('http')
                pair_st = self._service_manager.get_service_status('pairing_code')
                ConfigIO.save_network_service_config({
                    "tcp_enabled": bool(tcp_st.enabled),
                    "tcp_port": int(tcp_st.port or 9527),
                    "http_enabled": bool(http_st.enabled),
                    "http_port": int(http_st.port or 8080),
                    "pairing_enabled": bool(pair_st.enabled),
                })
            except Exception as e:
                print(f"[SERVER] 保存服务配置失败: {e}", flush=True)

            # 更新状态栏显示
            tcp_status = self._service_manager.get_service_status('tcp')
            if tcp_status.enabled and tcp_status.port:
                import socket
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    local_ip = s.getsockname()[0]
                    s.close()
                except Exception:
                    local_ip = "127.0.0.1"
                self.statusBar().showMessage(
                    f"TCP 服务运行中：{local_ip}:{tcp_status.port}",
                    2000
                )

            # 刷新状态栏网络角色指示
            self._update_net_status_label()

        # 服务启停 → 实时同步「数据」弹窗远程页禁用态（服务运行中禁止
        # 本机连本机；覆盖弹窗开着改服务的场景，打开弹窗时另有一次性同步）
        dlg = getattr(self, "_history_dialog", None)
        if dlg is not None:
            dlg.set_remote_server_active(self._any_service_running())

        # 服务状态变化（弹窗内启停 TCP 等）：标题同步 TCP 连接信息段
        self._refresh_window_title()

    def _on_remote_connected(self, device_info: dict):
        """处理远程连接成功：记录连接状态（会话列表在客户端页内选择）。

        注意：仅连接尚未获取数据时不进入「客户端模式」——该模式表示
        已加载远程数据（与服务端服务硬互斥），只在获取成功后设置，
        避免连一下就永久禁用服务端页。
        """
        try:
            client = device_info.get('client')
            # 设备标识：优先用 IP:端口（服务端会话列表无 device_name 字段）
            ip = device_info.get('ip', '')
            port = device_info.get('port', '')
            device_label = f"{ip}:{port}" if ip else device_info.get('device_name', '未知设备')

            # 记录当前客户端连接（供会话拉取复用；切换设备时页面会断开旧连接）
            self._remote_client = client
            self._remote_device_label = device_label

            self.statusBar().showMessage(
                f"已连接远程设备 {device_label}，请在「设备会话」中选取要获取的会话",
                5000)

        except Exception as e:
            QMessageBox.warning(self, "连接失败", f"连接远程设备时发生错误：\n{str(e)}")

    def _on_remote_load_requested(self, session_info: dict, client, device_label: str):
        """客户端页点「获取数据」：后台拉取该会话全量数据。"""
        session_id = session_info.get('session_id')
        if not session_id:
            QMessageBox.warning(self, "获取失败", "会话信息不完整，无法获取")
            return
        if getattr(self, "_remote_loading", False):
            return  # 防重入
        self._remote_loading = True
        self._remote_client = client
        self._remote_session_info = session_info
        self._remote_device_label = device_label
        self.statusBar().showMessage("正在从远程设备获取数据…", 0)
        self._remote_worker = _RemoteLoadWorker(client, session_id)
        self._remote_worker.progress.connect(self._on_remote_load_progress)
        self._remote_worker.done.connect(self._on_remote_load_done)
        self._remote_worker.failed.connect(self._on_remote_load_failed)
        self._remote_worker.finished.connect(self._remote_worker.deleteLater)
        self._remote_worker.start()

    def _on_remote_load_progress(self, done: int, total) -> None:
        """远程拉取进度（UI 线程槽，无 processEvents——消除重入）。"""
        if total:
            self.statusBar().showMessage(
                f"正在从远程设备加载数据… 已拉取 {done} / {total} 条", 0)
        else:
            self.statusBar().showMessage(
                f"正在从远程设备加载数据… 已拉取 {done} 条", 0)

    def _on_remote_load_done(self, channels, data) -> None:
        """远程拉取完成（UI 线程）：挂载会话 + 持久化 + 刷新界面。

        连接保持不断开（直到用户切换设备或关闭窗口）；页面会话行状态
        与加载记录经 set_load_finished 回写。
        """
        self._remote_loading = False
        self.statusBar().clearMessage()
        client = getattr(self, "_remote_client", None)
        session_info = getattr(self, "_remote_session_info", {}) or {}
        device_label = getattr(self, "_remote_device_label", "")
        session_id = session_info.get('session_id', '')
        if not data or not channels:
            QMessageBox.warning(self, "加载失败", "无法从远程设备加载数据")
            self._finish_remote_load_failed(session_id, "远程设备未返回数据")
            return
        # 创建 Session 对象并加入 DataStore（active_changed 信号自动刷新界面）
        session = store.load_session_from_remote(
            session_info, channels, data, device_label)
        if session is None:
            QMessageBox.warning(self, "加载失败", "远端数据解析失败，无法加载")
            self._finish_remote_load_failed(session_id, "远端数据解析失败")
            return
        # 自动保存到本机历史数据库（source_type='remote'，远端 ID 去重）；
        # 大会话整库写入为 UI 线程同步操作，加载卡片给出过程反馈
        with self._busy_card.begin("正在保存到本地数据库…"):
            persist_result = self._persist_remote_session(
                session_id, session_info, channels, data, device_label)
        persist_hint = {
            'imported': '，已自动保存到本地数据库',
            'exists': '（本地数据库已有该会话，跳过保存）',
            'failed': '，保存到本地数据库失败',
            'skipped': '',
        }.get(persist_result, '')
        self._data_mode = "file"
        record_count = (data.get('total') or session.n
                        or session_info.get('record_count') or 0)
        self._set_window_title(f"多通道温度分析仪 — 远程: {device_label}")
        self.statusBar().showMessage(
            f"已从远程设备获取: {device_label} ({record_count} 条记录){persist_hint}")
        # 状态栏来源标签：远程数据
        self._update_source_label()
        # 进入客户端角色：与服务端（网络服务运行中）硬互斥
        self._client_mode_active = True
        self._update_net_status_label()
        self._sync_client_mode_banner()
        # 回写客户端页：会话行标记「已加载」+ 记录加载日志
        self._notify_page_load_finished(session_id, True, session_info)

    def _finish_remote_load_failed(self, session_id: str, reason: str) -> None:
        """远程拉取失败：提示并回写页面状态（保持连接不断开）。"""
        self._remote_loading = False
        self.statusBar().showMessage("远程数据获取失败")
        self._notify_page_load_finished(session_id, False)

    def _notify_page_load_finished(self, session_id: str, ok: bool,
                                   session_info: Optional[dict] = None) -> None:
        """把远程一次性获取结果回写「查看历史 → 远程数据」页行标识。"""
        dlg = getattr(self, "_history_dialog", None)
        tab = getattr(dlg, "_remote_tab", None) if dlg is not None else None
        if tab is None:
            return
        hint = "已保存到历史，可在「本机数据」页打开该会话" if ok else "获取失败"
        tab.set_fetch_result(session_id, ok, hint)

    def _on_remote_load_failed(self, msg: str) -> None:
        """远程拉取失败（UI 线程）。"""
        self._remote_loading = False
        self.statusBar().clearMessage()
        session_info = getattr(self, "_remote_session_info", {}) or {}
        session_id = session_info.get('session_id', '')
        QMessageBox.warning(self, "获取失败", f"从远程设备获取数据失败：\n{msg}")
        self._notify_page_load_finished(session_id, False)

    def _on_remote_disconnected(self) -> None:
        """远程数据页断开连接：清引用（监控不受影响，由主窗口管理）。"""
        self._remote_client = None
        self._remote_device_label = ""

    def _on_remote_fetch_requested(self, session_info: dict,
                                   monitor: bool) -> None:
        """「查看历史 → 远程数据」获取请求：
        采集中会话 → 实时监控；已完结会话 → 一次性获取。"""
        client = self._remote_client
        if client is None:
            QMessageBox.warning(self, "获取失败", "尚未连接远程设备，请先连接")
            return
        device_label = getattr(self, "_remote_device_label", "") or "远程设备"
        if monitor:
            self._start_remote_monitor(session_info, client, device_label)
        else:
            self._on_remote_load_requested(session_info, client, device_label)

    def _persist_remote_session(self, session_id: str, session_info: dict,
                                channels: list, data: dict,
                                device_label: str) -> str:
        """把远端会话自动保存到本机历史数据库（source_type='remote'）。

        复用模块级 parse_remote_temperature_data 提取带绝对时间戳的温度数据；
        以远端 session_id 作为本机主键，重复导入由 HistoryDatabase 自动跳过。

        返回:
            str: 'imported' / 'exists' / 'failed' / 'skipped'（数据为空或库不可用）
        """
        hdb = self._history_db
        if hdb is None:
            try:
                from device.database.history_db import HistoryDatabase
                hdb = HistoryDatabase()
                self._history_db = hdb
            except Exception as e:
                print(f"[REMOTE] 打开本地数据库失败，跳过保存: {e}", flush=True)
                return 'skipped'

        from device.datastore.store import parse_remote_temperature_data
        parsed = parse_remote_temperature_data(data)
        if not parsed:
            return 'skipped'

        readings = []
        for ch_key, recs in parsed.items():
            for ts, temp, is_valid in recs:
                readings.append({
                    'channel_key': ch_key,
                    'timestamp': ts,
                    'temperature': None if temp != temp else temp,  # NaN → NULL
                    'is_valid': is_valid,
                })
        if not readings:
            return 'skipped'

        return hdb.import_remote_session(
            session_id=session_id,
            session_name=session_info.get('session_name') or f"远程_{session_id}",
            started_at=float(session_info.get('started_at') or 0.0) or time.time(),
            channel_count=len(channels),
            interval=float(session_info.get('interval_seconds') or 1.0),
            source_path=f"remote://{device_label}/{session_id}",
            channels=[
                {
                    'channel_key': ch.get('channel_key', f'CH{i + 1}'),
                    'channel_name': ch.get('channel_name'),
                    'color': ch.get('color') or ch.get('channel_color'),
                    'physical_name': ch.get('physical_name'),
                    'unit': ch.get('unit', '°C'),
                    'visible': ch.get('visible', True),
                }
                for i, ch in enumerate(channels)
            ],
            readings=readings,
            stopped_at=session_info.get('stopped_at') or None,
        )

    # ==================================================================
    #  远程「采集中」会话实时监控
    # ==================================================================
    def _start_remote_monitor(self, session_info: dict, client,
                              device_label: str) -> None:
        """启动远程实时监控（「查看历史 → 远程数据」选中的采集中会话）。

        一次只监控一个会话：已有监控先停止（异步落库）并转入退役列表，
        等其后台收尾（线程/落库）完成后自动移除引用。
        """
        old = getattr(self, "_remote_monitor", None)
        if old is not None:
            if old.is_active():
                old.stop()
            self._retired_monitors.append(old)

        from device.network.remote_monitor import RemoteMonitor
        # 注意：datastore/__init__ 把 store 单例绑到包属性 store 上，
        # `import ... as` 会拿到实例；必须用 import_module 取真模块
        # （monitor 需要模块级 parse/merge 函数 + .store 单例方法）
        import importlib
        store_mod = importlib.import_module('device.datastore.store')
        # 监控启动即进入客户端角色，复用连接时记录的地址显示网络状态
        self._remote_device_label = device_label
        mon = RemoteMonitor(
            client=client, session_info=session_info,
            device_label=device_label, store_mod=store_mod,
            persist_fn=self._persist_remote_session_from_memory,
            message_fn=lambda text, ms=0: (
                self.statusBar().showMessage(text, int(ms)) if ms
                else self.statusBar().showMessage(text, 0)),
            parent=self)
        mon.state_changed.connect(self._on_remote_monitor_state)
        self._remote_monitor = mon
        mon.start()

    def _stop_remote_monitor(self) -> None:
        """停止当前远程监控（异步：最后一次增量 + 落库完成后通知）。"""
        mon = getattr(self, "_remote_monitor", None)
        if mon is not None and mon.is_active():
            mon.stop()

    def _on_remote_monitor_state(self, state: str, message: str) -> None:
        """监控状态变化：刷新状态栏来源标签、客户端模式与远程页行标识。"""
        mon = self.sender()
        if state == "monitoring":
            self._client_mode_active = True
            self._sync_client_mode_banner()
        # 回写「查看历史 → 远程数据」页：会话行「● 监控中」/「已获取」标识
        dlg = getattr(self, "_history_dialog", None)
        tab = getattr(dlg, "_remote_tab", None) if dlg is not None else None
        if tab is not None and mon is not None:
            if state == "monitoring":
                tab.set_monitor_active(True, mon.session_id)
            elif state in ("finished", "stopped"):
                tab.set_monitor_active(False)
                tab.set_fetch_result(mon.session_id, True, message)
            elif state == "error":
                tab.set_monitor_active(False)
                tab.set_fetch_result(mon.session_id, False, message)
        if state in ("finished", "stopped", "error") and mon is not None:
            # 终态：从退役列表移除；当前监控结束仍保留引用供界面查询
            if mon in self._retired_monitors:
                self._retired_monitors.remove(mon)
        self._update_net_status_label()
        self._update_source_label()

    def _persist_remote_session_from_memory(self, session, session_info: dict,
                                            device_label: str) -> str:
        """把监控结束时的内存远程会话一次性落本地历史库。

        数据来源是内存 Session（相对秒时间轴），这里换算回绝对时间戳；
        以远端 session_id 作为本机主键，重复导入由 HistoryDatabase 跳过。

        返回:
            str: 'imported' / 'exists' / 'failed' / 'skipped'
        """
        if session is None or not session.n:
            return 'skipped'
        hdb = self._history_db
        if hdb is None:
            try:
                from device.database.history_db import HistoryDatabase
                hdb = HistoryDatabase()
                self._history_db = hdb
            except Exception as e:
                print(f"[REMOTE] 打开本地数据库失败，跳过保存: {e}", flush=True)
                return 'skipped'

        session_id = session_info.get('session_id', '')
        started_at = (float(session_info.get('started_at') or 0.0)
                      or session.created_at)
        times = session.time_sec
        readings = []
        for i in range(session.n):
            ts = started_at + float(times[i])
            for c in session.channels:
                v = float(session.values(c)[i])
                readings.append({
                    'channel_key': c.key,
                    'timestamp': ts,
                    'temperature': None if v != v else v,  # NaN → NULL
                    'is_valid': True,
                })
        if not readings:
            return 'skipped'

        return hdb.import_remote_session(
            session_id=session_id,
            session_name=session_info.get('session_name') or f"远程_{session_id}",
            started_at=started_at,
            channel_count=len(session.channels),
            interval=float(session_info.get('interval_seconds') or 1.0),
            source_path=f"remote://{device_label}/{session_id}",
            channels=[
                {
                    'channel_key': c.key,
                    'channel_name': c.name or c.key,
                    'color': c.color,
                    'physical_name': c.source_label or c.key,
                    'unit': '°C',
                    'visible': c.visible,
                }
                for c in session.channels
            ],
            readings=readings,
            stopped_at=session_info.get('stopped_at') or None,
        )

    # ==================================================================
    #  在线采集控制（顶部工具栏）
    # ==================================================================
    def _set_status_label_semantic(self, label, role: str,
                                   emphasis: bool = None) -> None:
        """由专属状态面板统一重放语义色，避免业务层写入具体颜色。"""
        panel = getattr(self, "status_info_panel", None)
        if panel is not None:
            panel.set_semantic(label, role, emphasis)
            return
        label.setProperty("semanticRole", role)
        label.setStyleSheet(f"color:{Theme.semantic_text(role)};")

    def _set_acq_status(self, text: str, role: str):
        """更新底部状态栏采集分区主文本（颜色跟随主题）。同时记录最近状态，
        主题切换时用于按当前状态重放配色。"""
        self._acq_status = (text, role)
        if not hasattr(self, "lbl_acq_status"):
            return
        self.lbl_acq_status.setText(text)
        self._set_status_label_semantic(
            self.lbl_acq_status, role, emphasis=True)

    def _refresh_acq_status_style(self):
        """主题切换后按最近一次状态重放主文本颜色。"""
        if not hasattr(self, "_acq_status"):
            return
        text, role = self._acq_status
        if hasattr(self, "lbl_acq_status"):
            self.lbl_acq_status.setText(text)
            self._set_status_label_semantic(
                self.lbl_acq_status, role, emphasis=True)

    def _heavy_op_busy(self) -> bool:
        """是否有重量级操作进行中（C2：同一时刻最多一个）。"""
        return (getattr(self, "_importing", False)
                or getattr(self, "_exporting", False)
                or getattr(self, "_recomputing", False)
                or getattr(self, "_history_loading", False)
                or self._acq_state in ("acquiring", "paused", "finishing"))

    def _reject_if_busy(self, action_name: str) -> bool:
        """重量级操作进行中时提示并返回 True（调用方应 return）。"""
        if self._heavy_op_busy():
            QMessageBox.information(
                self, "请稍候",
                f"{action_name}：当前有采集/导入/导出/重算正在进行，请先完成或停止。")
            return True
        return False

    def _toggle_connection(self):
        """连接 / 断开串口设备。"""
        if self._acq_state in ("acquiring", "paused"):
            QMessageBox.information(self, "无法操作", "请先停止在线采集，再连接/断开串口。")
            return
        if self._serial_mgr.is_open:
            port = self._acq_port_name()
            self._serial_mgr.close()
            self._sync_connect_button()
            self._set_acq_status(acq_status_label("idle"), "info")
            self._acq_step_card.show_notice(f"已断开 {port}")
            self.statusBar().showMessage("串口已断开", 2000)
            return
        port = self._selected_port()
        if not port or port == "(无可用端口)":
            QMessageBox.information(self, "提示", "请选择有效的 COM 口")
            return
        card = self._acq_step_card
        card.start_connect(port)   # 连接旅程第 1 步「打开串口」进行中
        if not self._serial_mgr.open(port, self._acq_baud):
            # 卡片红色失败态显示原因（原 toast + critical 弹窗集中到旅程卡）
            card.mark_port_failed(f"无法打开串口 {port}")
            return
        card.mark_port_done()   # →「已连接 {port}」提示下一步
        self._sync_connect_button()
        self._set_acq_status(acq_status_label("connected", port), "success")
        self.statusBar().showMessage(f"已连接 {port} @ {self._acq_baud}", 2000)

    def _notify(self, text: str, role: str = "info") -> None:
        """操作结果轻提示：_toast 未就绪（构建期）时静默降级。"""
        toast = getattr(self, "_toast", None)
        if toast is not None:
            toast.show_message(text, role)

    def _sync_connect_button(self):
        """按串口/采集实际状态同步连接按钮与串口下拉的视觉与可用性。

        开始采集自动开串口、采集结束/握手失败/命名取消/落库失败自动关串口
        等隐式路径不经过 _toggle_connection，统一在此同步，防止按钮文字
        与真实连接状态漂移；同时驱动 connState 动态属性着色（连接成功转
        绿实底「断开」）与采集期间锁定（按钮/下拉禁用，端口不可再切换）。
        """
        connected = bool(self._serial_mgr.is_open)
        locked = getattr(self, "_acq_state", "ready") in (
            "acquiring", "paused", "finishing")
        self.btn_connect.setText("🔌 断开" if connected else "🔌 连接")
        self.btn_connect.setEnabled(not locked)
        self.btn_connect.setProperty(
            "connState", "connected" if connected else "idle")
        self._repolish(self.btn_connect)
        if hasattr(self, "cmb_port"):
            self.cmb_port.setEnabled(not locked)
            self.cmb_port.setProperty(
                "connState", "connected" if connected else "idle")
            self._repolish(self.cmb_port)

    @staticmethod
    def _repolish(widget) -> None:
        """重放控件动态属性样式（property + repolish 供 QSS 属性选择器命中）。"""
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
        widget.update()

    def _update_source_label(self):
        """更新底部状态栏右侧的数据来源指示（已隐藏：来源信息与窗口标题重叠）。

        来源信息与窗口内信息重复，已隐藏以避免重叠。
        网络服务端 IP 信息通过 lbl_net_status 显示在状态栏中。
        """
        if not hasattr(self, "lbl_source"):
            return
        # 始终清空，不显示来源信息（与窗口标题重叠）
        self.lbl_source.setText("")
        self._set_status_label_semantic(self.lbl_source, "info")
    def _style_port_combo(self):
        """应用串口下拉框本地样式（含下拉箭头图标）。

        本控件带本地 QSS，会屏蔽全局 QSS 的 QComboBox 规则，因此箭头必须
        在这里显式指定；主题切换后需重新调用以跟随箭头颜色。
        """
        if not hasattr(self, "cmb_port"):
            return
        arrow = Theme._ensure_arrow_png()
        arrow = arrow.replace("\\", "/") if arrow else ""
        arrow_rule = (
            f"QComboBox::down-arrow {{ image: url({arrow}); width: 10px; height: 6px; }}"
            if arrow else ""
        )
        self.cmb_port.setStyleSheet(
            "QComboBox { padding:3px 20px 3px 7px; min-height:30px; }"
            # 连接成功后当前活跃端口染语义绿边框（connState 由 _sync_connect_button 维护）
            'QComboBox[connState="connected"] {'
            f" border:1px solid {Theme.RUN_FILL};"
            " }"
            "QComboBox::drop-down { border:none; width:24px; }"
            # 可编辑下拉的内嵌 QLineEdit 局部去边框：全局 QSS 的 QLineEdit
            # 规则会给内嵌行编辑再描一圈，与 QComboBox 外框叠加成双边框
            "QComboBox QLineEdit { border:none;"
            " background:transparent; padding:0; }"
            + arrow_rule)

    def _selected_port(self) -> str:
        """取当前选中的端口设备名（裸 COM 名）。

        详细枚举项显示「COM3 · 描述」且 UserRole 存裸设备名，currentData
        优先；手输的裸端口名没有 UserRole，回退 currentText 原文识别。
        """
        data = self.cmb_port.currentData()
        if isinstance(data, str) and data:
            return data
        return self.cmb_port.currentText().strip()

    def _acq_port_name(self) -> str:
        """当前串口裸名（COM1）：串口打开时取现选端口，否则回退记忆端口。"""
        try:
            if (getattr(self, "_serial_mgr", None) is not None
                    and self._serial_mgr.is_open):
                return self._selected_port()
        except Exception:
            pass
        return str(getattr(self, "_last_port", "") or "")

    def _apply_ports(self, ports) -> None:
        """把端口列表填入下拉框（含记忆恢复与 USB 自动选中），供同步/异步刷新共用。

        ports 兼容两种输入：
        - list[str]：裸端口名（旧路径，显示即设备名、UserRole 不设）
        - list[tuple(device, description, vid, pid)]：详细枚举（新路径，
          USB 转串口优先排序、显示「设备 · 描述」、UserRole 存裸设备名）
        选中优先级：配置记忆端口（_last_port）> 当前选中项 >
        唯一 USB 转串口候选（自动识别，见改动 8）> 列表第一项。
        """
        if not hasattr(self, "cmb_port"):
            return
        detailed = bool(ports) and isinstance(ports[0], tuple)
        score_fn = None
        if detailed:
            from device.acquisition import (
                sort_ports_detailed, port_score)
            score_fn = port_score
            ports = sort_ports_detailed(ports)
        remember = (getattr(self, "_last_port", "")
                    or self._selected_port())
        self.cmb_port.clear()
        if not ports:
            self.cmb_port.addItem("(无可用端口)")
            return
        devices = []
        for item in ports:
            if isinstance(item, tuple):
                dev, desc = item[0], item[1]
                text = f"{dev} · {desc}" if desc else dev
                self.cmb_port.addItem(text, dev)
            else:
                dev = str(item)
                text = dev
                self.cmb_port.addItem(text)
            devices.append(dev)
        if remember and remember in devices:
            self.cmb_port.setCurrentIndex(devices.index(remember))
            return
        # 记忆端口不在列表：唯一 USB 转串口候选时自动选中（多候选不猜）
        if score_fn is not None:
            usb_candidates = [i for i, it in enumerate(ports)
                              if score_fn(it[1], it[2], it[3]) >= 2]
            if len(usb_candidates) == 1:
                self.cmb_port.setCurrentIndex(usb_candidates[0])

    def _refresh_ports(self):
        """同步刷新 COM 口下拉列表（点击下拉时调用，走详细枚举）。"""
        self._apply_ports(self._serial_mgr.list_ports_detailed())

    def _start_port_scan_async(self) -> None:
        """启动时后台线程扫描 COM 口（不阻塞启动；插拔设备由 _auto_refresh_ports 兜底）。"""
        self._port_scan_worker = _BackgroundWorker(
            self._serial_mgr.list_ports_detailed)
        self._port_scan_worker.done.connect(self._apply_ports)
        self._port_scan_worker.failed.connect(lambda _m: None)
        self._port_scan_worker.finished.connect(self._port_scan_worker.deleteLater)
        self._port_scan_worker.start()

    def _auto_refresh_ports(self) -> None:
        """定时自动枚举 COM 口：仅未连接且无可用端口时重试，避免打扰已选中的用户。

        修复「不是每一次都寻找 COM 口」：插上新设备后下拉框自动出现端口，
        无需手动点开下拉。
        """
        if self._acq_state in ("acquiring", "paused", "finishing"):
            return
        if self._serial_mgr.is_open:
            return
        cur = self._selected_port()
        if cur and cur != "(无可用端口)":
            return  # 已有可用端口且用户已选中，不打扰
        self._refresh_ports()

    def _on_port_selected(self, _idx):
        """串口选择后立即保存到配置。"""
        self._save_acq_config()

    def _toggle_pause(self):
        """暂停 / 继续采集。"""
        if self._acq_worker is None or self._acq_state not in ("acquiring", "paused"):
            return
        if self.btn_pause_acq.isChecked():
            self._acq_worker.pause()
            self._acq_state = "paused"
            self.btn_pause_acq.setText("▶ 继续")
            self._set_acq_status(
                acq_status_label("paused", self._acq_port_name()), "warning")
            self._hb.set_state(STATE_PAUSED)
            self._acq_step_card.show_notice(
                "已暂停：数据仍在自动保存", "warning")
            self.statusBar().showMessage("采集已暂停，数据仍在自动保存", 3000)
        else:
            self._acq_worker.resume()
            self._acq_state = "acquiring"
            self.btn_pause_acq.setText("⏸ 暂停")
            self._set_acq_status(
                acq_status_label("acquiring", self._acq_port_name()), "success")
            self._hb.set_state(STATE_ONLINE)
            self._acq_step_card.show_notice("采集已继续", "success")
            self.statusBar().showMessage("采集已继续", 2000)

    def _set_acq_baud(self, rate: int):
        """设置波特率（设置页配置，自动持久化）。"""
        self._acq_baud = rate
        self._save_acq_config()
        self.statusBar().showMessage(f"波特率: {rate}", 2000)

    def _set_acq_interval(self, ms: int):
        """设置采样间隔（设置页配置，自动持久化）。"""
        self._acq_interval_ms = ms
        self._save_acq_config()
        self.statusBar().showMessage(f"采样间隔: {ms}ms", 2000)

    def _set_acq_max_groups(self, groups: int):
        """设置采集组数（0=自动=读取仪器配置组数；1~8=软件指定）。自动持久化。"""
        self._acq_max_groups = max(0, min(8, int(groups)))
        self._save_acq_config()
        if self._acq_max_groups == 0:
            self.statusBar().showMessage("采集组数: 自动（读取仪器配置）", 2000)
        else:
            self.statusBar().showMessage(
                f"采集组数: {self._acq_max_groups}组（{self._acq_max_groups * 8}通道）", 2000)

    def _set_acq_protocol(self, protocol: str):
        """设置采集协议版本（auto 自动检测 / new 新版 / old 旧版）。"""
        self._acq_protocol = protocol if protocol in ("auto", "new", "old") else "auto"
        self._save_acq_config()
        # 「读取采样通道」仅新版协议支持；auto 模式下连接成功后才确定，故默认禁用，
        # 连接后若识别为新协议再启用。
        dlg = getattr(self, "settings_dlg", None)
        if dlg is not None and hasattr(dlg, "btn_read_sampling"):
            dlg.btn_read_sampling.setEnabled(self._acq_protocol == "new")
        msg = {"auto": "已设为自动检测协议",
               "new": "已固定使用新版协议",
               "old": "已固定使用旧版协议"}[self._acq_protocol]
        self.statusBar().showMessage(msg, 2000)

    def _save_sampling_config(self, dlg=None):
        """保存组开关和通道掩码，确保关闭设置窗口后仍能恢复。"""
        dlg = dlg or getattr(self, "settings_dlg", None)
        if dlg is not None and hasattr(dlg, "sampling_masks"):
            self._acq_channel_masks = [int(mask) & 0xFF for mask in dlg.sampling_masks()]
            self._acq_channel_masks += [0] * (8 - len(self._acq_channel_masks))
            self._acq_channel_masks = self._acq_channel_masks[:8]
            self._acq_group_switch = int(dlg.sampling_group_switch()) & 0xFF
        self._save_acq_config()

    def _sampling_action_guard(self):
        """校验采样通道设置是否可以占用串口。"""
        if self._acq_worker is not None and self._acq_worker.isRunning():
            QMessageBox.information(self, "无法操作", "请先停止在线采集，再读取或写入采样通道。")
            return False
        if not self._serial_mgr.is_open:
            QMessageBox.information(self, "未连接", "请先连接串口设备，再操作采样通道。")
            return False
        return True

    def _read_sampling_channels(self):
        """按固定协议读取采样通道；旧协议没有读取通道配置指令。

        串口往返最长可冻结 UI 十余秒：加载卡片 + 等待光标给出过程反馈，
        _sampling_io_busy 防止冻结期间点击排队导致重复读写。
        """
        if not self._sampling_action_guard():
            return
        dlg = getattr(self, "settings_dlg", None)
        if dlg is None:
            return
        if self._acq_protocol != "new":
            dlg.lbl_sampling_status.setText("旧版协议不支持读取仪器通道配置")
            return
        if getattr(self, "_sampling_io_busy", False):
            return
        self._sampling_io_busy = True
        try:
            with self._busy_card.begin("正在读取仪器采样通道…"):
                self._read_sampling_channels_impl(dlg)
        finally:
            self._sampling_io_busy = False

    def _read_sampling_channels_impl(self, dlg):
        """读取采样通道实际串口往返（UI 线程同步，由加载卡片包裹）。"""
        from device.acquisition import AcquisitionProtocol
        protocol = AcquisitionProtocol()
        try:
            connect = self._serial_mgr.send_and_receive(protocol.build_connect_command(), 5)
            if (not connect or connect[0] != TPID or connect[1] != 0x11
                    or not protocol.check_crc16(connect)):
                dlg.lbl_sampling_status.setText("读取失败：设备不支持新协议采样通道读取")
                return
            group_count = max(1, min(8, connect[2]))
            command = protocol.build_read_chanonoff_command()
            response = self._serial_mgr.send_and_receive(
                command, 13, resp_cmd=READ_CHANONOFF)
            masks = protocol.parse_chanonoff_response(response) if response else None
            if masks is None or not protocol.check_crc16(response):
                dlg.lbl_sampling_status.setText("读取失败：READ_CHANONOFF 响应或 CRC 校验错误")
                return
            setdata_cmd = protocol.build_read_setdata_command()
            # 真机 READ_SETDATA 只返 TPID+CMD+X8+CRC = 12 字节（不含 Y/Z）
            setdata_response = self._serial_mgr.send_and_receive(setdata_cmd, 12)
            setdata = protocol.parse_setdata_response(setdata_response) if setdata_response else None
            if setdata is None:
                dlg.lbl_sampling_status.setText("读取失败：READ_SETDATA 响应或 CRC 校验错误")
                return
            masks = masks[:group_count]
            dlg.update_sampling_channels(
                masks,
                f"读取成功：{group_count} 组，已开启 {sum(m.bit_count() for m in masks)} 个通道",
                group_switch=setdata["group_switch"])
            self._save_sampling_config(dlg)
            self.statusBar().showMessage("已从仪器读取采样通道", 2000)
        except Exception as exc:
            dlg.lbl_sampling_status.setText(f"读取失败：{exc}")

    def _write_sampling_channels(self):
        """按固定协议写入采样通道，并回读确认新版协议结果。

        串口往返最长可冻结 UI 十余秒：反馈与防重入同 _read_sampling_channels。
        """
        if not self._sampling_action_guard():
            return
        dlg = getattr(self, "settings_dlg", None)
        if dlg is None:
            return
        from device.acquisition import AcquisitionProtocol
        protocol = AcquisitionProtocol()
        masks = dlg.sampling_masks()
        if not masks:
            return
        if getattr(self, "_sampling_io_busy", False):
            return
        self._sampling_io_busy = True
        try:
            with self._busy_card.begin("正在写入采样通道到仪器…"):
                self._write_sampling_channels_impl(dlg, protocol, masks)
        finally:
            self._sampling_io_busy = False

    def _write_sampling_channels_impl(self, dlg, protocol, masks):
        """写入采样通道实际串口往返（UI 线程同步，由加载卡片包裹）。"""
        try:
            if self._acq_protocol == "new":
                connect = self._serial_mgr.send_and_receive(protocol.build_connect_command(), 5)
            else:
                connect = self._serial_mgr.send_and_receive(
                    protocol.build_connect_old_command(), 3)

            if self._acq_protocol == "new" and (
                    connect and connect[0] == TPID and connect[1] == 0x11
                    and protocol.check_crc16(connect)):
                masks9 = masks[:9] + [0] * max(0, 9 - len(masks))
                group_switch = dlg.sampling_group_switch()
                setdata_cmd = protocol.build_read_setdata_command()
                # 真机 READ_SETDATA 只返 TPID+CMD+X8+CRC = 12 字节（不含 Y/Z）
                setdata_response = self._serial_mgr.send_and_receive(setdata_cmd, 12)
                setdata = protocol.parse_setdata_response(setdata_response) if setdata_response else None
                if setdata is None:
                    dlg.lbl_sampling_status.setText("写入失败：无法读取设备当前参数")
                    return
                para_cmd = protocol.build_set_para_command(
                    group_switch, setdata["storage_flag"], 0,
                    setdata["store_hour"], setdata["store_min"], setdata["store_sec"])
                para_response = self._serial_mgr.send_and_receive(
                    para_cmd, 4, resp_cmd=READ_SETDATA)
                if (not para_response
                        or not protocol.check_crc16(para_response)):
                    dlg.lbl_sampling_status.setText("写入失败：组开关 SET_PARA 无确认响应")
                    return
                command = protocol.build_set_chanonoff_command(masks9)
                response = self._serial_mgr.send_and_receive(
                    command, 4, resp_cmd=SET_CHANONOFF)
                if not response or not protocol.check_crc16(response):
                    dlg.lbl_sampling_status.setText("写入失败：SET_CHANONOFF 响应或 CRC 校验错误")
                    return
                # 回读确认：同一次写入反馈内完成，绕过 _sampling_io_busy 守卫
                self._read_sampling_channels_impl(dlg)
                self._save_sampling_config(dlg)
                dlg.lbl_sampling_status.setText("写入成功，已重新读取设备状态确认")
                self.statusBar().showMessage("采样通道已写入仪器", 2000)
                return

            if self._acq_protocol == "old" and connect and connect[0] == TPID and connect[1] == 0x01:
                channel_value = sum((mask & 0xFF) << (8 * i)
                                    for i, mask in enumerate(masks[:2]))
                command = protocol.build_set_samp_ch_old_command(
                    channel_value, channel_bytes=2 if len(masks) >= 2 else 1)
                response = self._serial_mgr.send_and_receive(
                    command, 2, resp_cmd=SET_SAMP_CH_OLD)
                if response:
                    self._save_sampling_config(dlg)
                    dlg.lbl_sampling_status.setText(
                        "旧协议写入成功；协议不支持读取采样通道，请以仪器面板为准")
                    self.statusBar().showMessage("采样通道已写入旧协议仪器", 2000)
                else:
                    dlg.lbl_sampling_status.setText("写入失败：旧协议设备无确认响应")
                return
            dlg.lbl_sampling_status.setText(
                f"写入失败：{self._acq_protocol} 协议设备握手失败")
        except Exception as exc:
            dlg.lbl_sampling_status.setText(f"写入失败：{exc}")

    def _choose_channel_naming(self):
        """采集前弹窗：会话命名（自动/自定义）+ 通道命名（沿用/重置）。

        返回 (session_name, reset_names)；用户取消返回 None。
        - 会话命名：按时间自动命名时 session_name 为 None（保持「采集_时间」）；
          自定义命名时返回去空格后的名称。
        - 通道命名：reset_names 为 True 时调用方应重置逐通道自定义名。
        """
        from ui.dialogs.acquisition_setup_dialog import AcquisitionSetupDialog
        dlg = AcquisitionSetupDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return None
        # 只返回用户选择，通道命名重置等副作用由调用方处理
        return dlg.session_name(), dlg.reset_channel_names()

    def _start_acquisition(self):
        """开始采集：自动连接串口 → 后台握手（UI 不冻结）→ 命名 → 启动采集线程。"""
        if getattr(self, "_connecting", False):
            return  # 已在连接中，防重入
        # 开始本地采集 → 停止远程监控（异步落库，不阻塞采集启动）
        self._stop_remote_monitor()
        card = self._acq_step_card
        # 自动连接串口（打开本身很快，留 UI 线程）；三步卡片从点击即显示
        if not self._serial_mgr.is_open:
            port = self._selected_port()
            if not port or port == "(无可用端口)":
                QMessageBox.information(self, "提示", "请选择有效的 COM 口")
                return
            card.start(port)   # 第 1 步「打开串口」进行中
            if not self._serial_mgr.open(port, self._acq_baud):
                card.mark_port_failed(f"无法打开串口 {port}")
                QMessageBox.critical(self, "连接失败", f"无法打开串口 {port}")
                return
            card.mark_port_done()
            # 自动开串口成功 → 连接按钮同步为「断开」态
            self._sync_connect_button()
        else:
            # 串口已开（手动「连接」过）：第 1 步直接完成接力（不重播转圈）
            card.resume_connected(self._acq_port_name())

        # C1/A11：握手（detect_protocol / send_and_receive 重试×超时）移至后台线程，
        # 避免设备无响应或参数不匹配（波特率/协议/设备型号）时 UI 卡死；
        # 期间显示连接动画，失败弹窗引导调整参数。
        self._connecting = True
        self._last_port = (port if not self._serial_mgr.is_open
                           else self._selected_port())
        self._start_connect_animation()
        self._acq_handshake_worker = _BackgroundWorker(
            self._run_handshake, self._serial_mgr, self._acq_protocol, self._acq_baud)
        self._acq_handshake_worker.done.connect(self._on_handshake_done)
        self._acq_handshake_worker.failed.connect(self._on_handshake_failed)
        self._acq_handshake_worker.finished.connect(self._acq_handshake_worker.deleteLater)
        self._acq_handshake_worker.start()

    @staticmethod
    def _run_handshake(serial_mgr, user_proto, current_baud):
        """后台握手：自动遍历波特率，返回 (proto, group_count, working_baud)。

        波特率自动选择：当前波特率握手失败时依次尝试 2400/4800/9600/19200，
        直到成功或全部失败。全部在后台线程执行（C1），UI 保持响应并显示
        连接动画；失败抛 RuntimeError(用户可读信息)。
        """
        from device.acquisition import AcquisitionProtocol as _P
        bauds = [int(current_baud)] if current_baud else []
        for b in (2400, 4800, 9600, 19200):
            if b not in bauds:
                bauds.append(b)
        last_err = "设备无响应，请检查线缆、供电、串口号和波特率"
        for baud in bauds:
            if not serial_mgr.set_baudrate(baud):
                continue
            try:
                if user_proto == "auto":
                    # 自动检测：先试新协议(0x11)，失败回退旧协议(0x01)
                    result = _P.detect_protocol(serial_mgr)
                    if result is None:
                        last_err = f"设备无响应（已尝试波特率 {baud}）"
                        continue
                    det_proto, info = result
                    if det_proto == "mismatch":
                        last_err = ("设备在线但新/旧协议握手均失败。\n"
                                    "请在「设置 → 协议版本」手动选择对应协议。")
                        continue
                    return det_proto, info["group_count"], baud
                if user_proto == "new":
                    resp = serial_mgr.send_and_receive(_P.build_connect_command(), 5)
                    if resp and len(resp) >= 5 and resp[0] == 0x85 \
                            and resp[1] == 0x11 and _P.check_crc16(resp):
                        return "new", max(1, min(8, resp[2])), baud
                    last_err = f"设备对「新版协议」无响应（已尝试波特率 {baud}）"
                else:  # old
                    resp = serial_mgr.send_and_receive(_P.build_connect_old_command(), 3)
                    if resp and len(resp) >= 3 and resp[0] == 0x85 and resp[1] == 0x01:
                        return "old", 1 if resp[2] == 0 else 2, baud
                    last_err = f"设备对「旧版协议」无响应（已尝试波特率 {baud}）"
            except Exception as e:  # 单波特率异常继续尝试下一个
                last_err = f"握手异常（波特率 {baud}）：{e}"
        raise RuntimeError(last_err)

    def _on_handshake_done(self, result) -> None:
        """握手成功（UI 线程）：记忆成功连接参数，继续创建会话并启动采集。"""
        self._connecting = False
        self._stop_connect_animation()
        proto, gc, baud = result
        # 步骤卡第 2 步打勾：展示真实识别出的协议与波特率
        self._acq_step_card.mark_handshake_done(
            {"new": "新版", "old": "旧版"}.get(proto, str(proto)), baud)
        # 记忆成功连接：自动选中的工作波特率 + 端口 → 下次启动直接使用
        if baud:
            self._acq_baud = int(baud)
        self._save_acq_config()
        self._continue_start_acquisition(proto, gc)

    def _on_handshake_failed(self, msg: str) -> None:
        """握手失败（UI 线程）：恢复界面、关闭串口，并引导用户调整参数。"""
        self._connecting = False
        self._stop_connect_animation()
        # 步骤卡第 2 步标红并显示原因（卡片短暂停留后自动淡出）
        self._acq_step_card.mark_handshake_failed(str(msg))
        self._serial_mgr.close()
        self._sync_connect_button()
        ret = QMessageBox.question(
            self, "连接失败",
            f"{msg}\n\n是否打开设置，调整波特率 / 协议版本 / 设备型号？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret == QMessageBox.Yes:
            self.open_settings()

    def _start_connect_animation(self) -> None:
        """连接中动画：采集按钮禁用 + 状态栏旋转点提示（UI 不卡死）。"""
        self.btn_start_acq.setEnabled(False)
        self._connect_anim_step = 0
        self.statusBar().showMessage("正在连接设备", 0)
        # 一级反馈：步骤卡第 2 步转圈（真实握手结束才打勾，替代原 toast）
        self._acq_step_card.mark_handshake_active()
        self._connect_anim_timer = QTimer(self)
        self._connect_anim_timer.timeout.connect(self._tick_connect_animation)
        self._connect_anim_timer.start(400)

    def _tick_connect_animation(self) -> None:
        self._connect_anim_step = (self._connect_anim_step + 1) % 4
        self.statusBar().showMessage("正在连接设备" + "." * self._connect_anim_step, 0)

    def _stop_connect_animation(self) -> None:
        if getattr(self, "_connect_anim_timer", None) is not None:
            self._connect_anim_timer.stop()
            self._connect_anim_timer = None
        self.statusBar().clearMessage()
        self.btn_start_acq.setEnabled(True)

    def _continue_start_acquisition(self, proto: str, gc: int) -> None:
        """握手成功后（UI 线程）：应用组数设置 → 命名 → 创建会话 → 启动采集线程。"""
        from device.acquisition import AcquisitionProtocol as _P
        # ── 应用后台"采集组数"设置（0=自动=读取仪器配置组数）──
        use_groups = gc
        if self._acq_max_groups and self._acq_max_groups > 0:
            use_groups = min(gc, self._acq_max_groups)

        # 开始采集只握手，不修改设备参数或通道开关。
        # use_groups 仅用于软件侧限制读取/展示的通道数量，避免启动时
        # 发送任何改变设备状态的配置指令。

        n_channels = use_groups * 8
        interval_sec = self._acq_interval_ms / 1000.0
        keys = [f"CH{i+1}" for i in range(n_channels)]

        # 会话命名 + 通道命名：弹窗选择；取消则终止本次采集。
        # 命名弹窗为模态且同在窗口中央：卡片先让位隐藏，关闭后恢复。
        self._acq_step_card.suspend()
        naming = self._choose_channel_naming()
        if naming is None:
            self._acq_step_card.dismiss()
            self._serial_mgr.close()
            self._sync_connect_button()
            return
        self._acq_step_card.resume()
        session_name, reset_names = naming
        if reset_names:
            store.reset_channel_names()

        # 按实际启用的通道数创建实时会话
        store.create_live_session(n_channels, interval=interval_sec, keys=keys)
        proto_label = {"new": "新版", "old": "旧版"}.get(proto, proto)
        if use_groups < gc:
            self.statusBar().showMessage(
                f"设备已识别：{proto_label}协议 {gc}组，按设置只启用前 {use_groups}组（{n_channels}通道）")
        else:
            self.statusBar().showMessage(f"设备已识别：{proto_label}协议 {gc}组 {n_channels}通道")

        # 心跳指示：按采样间隔自适应无响应超时
        self._hb.set_timeout(max(1.5, 3.0 * self._acq_interval_ms / 1000.0))
        self._hb.set_state(STATE_CONNECTING)

        # 启动后台采集线程（读取完整帧，但只上报前 use_groups 组）
        self._acq_worker = AcquisitionWorker(
            self._serial_mgr, interval_ms=self._acq_interval_ms,
            group_count=gc, active_groups=use_groups, protocol=_P(),
            proto_mode="new" if proto == "new" else "old",
            instrument_type=0 if gc == 1 else 1)
        self._acq_worker.data_received.connect(self._on_acq_data)
        self._acq_worker.connection_changed.connect(self._on_acq_connection)
        self._acq_worker.error_occurred.connect(
            lambda msg: self.statusBar().showMessage(f"采集错误：{msg}", 5000))
        self._acq_started_at = time.time()
        if not store.start_recording(session_name=session_name):
            self._acq_step_card.mark_acquire_failed("数据库写入失败，采集未开始")
            QMessageBox.critical(
                self, "保存失败",
                "无法写入数据库，采集未开始。\n"
                f"{store.recorder.last_error}\n\n"
                "当前仅允许数据库存储，请检查数据库是否可用。")
            self._hb.set_state(STATE_OFF)
            self._serial_mgr.close()
            self._sync_connect_button()
            return
        self._acq_worker.start()
        # 步骤卡第 3 步进行中：等待首帧数据到达（真实“已连上”信号）
        self._acq_step_card.mark_acquire_active(n_channels)

        self.btn_start_acq.setEnabled(False)
        self.btn_pause_acq.setEnabled(True)
        self.btn_pause_acq.setChecked(False)
        self.btn_pause_acq.setText("⏸ 暂停")
        self.btn_stop_acq.setEnabled(True)
        self.btn_restart_acq.setVisible(False)
        self._data_mode = "live"
        self._acq_state = "acquiring"
        # 新采集会话：重置生态舱波动基线与迟滞状态（通道数/语义可能变化）
        self._ball_feed_prev = None
        self._ball_levels_prev = None
        self._celebrate_prev_agg = "idle"
        # 采集进行中：锁定串口下拉与连接按钮，端口不可再切换
        self._sync_connect_button()
        # 在线采集已建立：按显隐规则亮出悬浮球（弹窗/面板按各自意愿复显）
        self._sync_live_ball_visibility()
        if session_name:
            self._set_window_title(
                f"多通道温度分析仪 — 实时采集中 {session_name} ({n_channels}通道)")
        else:
            self._set_window_title(
                f"多通道温度分析仪 — 实时采集中 ({n_channels}通道)")
        self.statusBar().showMessage("采集已开始，正在记录到数据库", 5000)
        # 采集开始的成功反馈由步骤卡承担（全部打勾后停留并自动淡出），
        # 不再叠加顶部 toast；通道数见卡片标题与窗口标题。

        # 进入服务端角色：清除客户端模式（远程数据加载状态）
        self._client_mode_active = False
        self._update_net_status_label()
        self._sync_client_mode_banner()

        # 启动 DataServer：点击「开始采集」时按配置自动激活网络服务
        self._start_data_server()

    def _stop_acquisition(self, ask_confirm: bool = False,
                          notify_done: bool = True):
        """结束采集：停线程、最终落盘并进入已结束状态。

        ask_confirm: 仅「⏹ 结束」按钮路径传 True，先弹确认弹窗；
            设备拔出自动停、closeEvent、aboutToQuit 等自动路径不弹，
            避免卡住退出链路。
        notify_done: 完成后弹「采集已结束」提示；退出链路（closeEvent /
            aboutToQuit）传 False，防止模态弹窗阻塞应用退出。
        """
        if self._acq_state not in ("acquiring", "paused"):
            return
        if ask_confirm:
            ret = QMessageBox.question(
                self, "确认结束采集",
                "确认结束采集？\n结束后数据将保存到数据库，串口将断开。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret != QMessageBox.Yes:
                return
        self._acq_state = "finishing"
        if self._acq_worker is not None:
            stopped = self._acq_worker.stop()
            if stopped:
                self._acq_worker = None
            else:
                # C4：线程未干净退出（卡在阻塞串口/握手）。挪到 exiting 池持有引用，
                # 待 finished 信号清理；避免 Python 释放仍运行的 QThread → 闪退。
                w = self._acq_worker
                self._acq_workers_exiting.append(w)
                self._acq_worker = None
                w.finished.connect(self._on_acq_worker_finished)

        # 关闭串口
        if self._serial_mgr.is_open:
            self._serial_mgr.close()
        # 采集结束自动断开串口 → 连接按钮同步回「连接」态
        self._sync_connect_button()

        self.btn_start_acq.setEnabled(True)
        self.btn_pause_acq.setEnabled(False)
        self.btn_pause_acq.setChecked(False)
        self.btn_pause_acq.setText("⏸ 暂停")
        self.btn_stop_acq.setEnabled(False)
        self.btn_restart_acq.setVisible(True)
        # 落库前记住会话名（stop_recording 后 live 会话仍在但停止录制）
        stopped = store.live
        stopped_name = stopped.title if stopped is not None else ""
        store.stop_recording()
        self._data_mode = "file"
        self._acq_state = "finished"
        self._set_acq_status(acq_status_label("idle"), "info")
        # 已结束态：恢复串口下拉与连接按钮（未连接·可操作）
        self._sync_connect_button()
        # 采集结束：悬浮球球心最高温回图标（无实时帧后不显示陈旧数字）
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is not None:
            ball.clear_max_temp()
        # 采集已结束即无在线采集：悬浮球族整体退场（新显隐规则）
        self._sync_live_ball_visibility()
        self._acq_step_card.show_notice("采集已结束，数据已保存")
        # 直接停止采集路径也会复位心跳指示灯（连接断开路径在 _on_acq_connection 中已复位，重复调用幂等）
        self._hb.set_state(STATE_OFF)
        # 清理报警判定状态、曲线高亮与状态栏指示
        self._alarm_states = {}
        self._alarm_prev = {}
        if getattr(self, "chart_renderer", None) is not None:
            self.chart_renderer._alarm_channels.clear()
        if hasattr(self, "_alarm_label"):
            self._alarm_label.setText("")
        # 停止循环声音、隐藏报警窗、清确认标记
        try:
            stop_alarm_loop()
        except Exception:
            pass
        self._hide_alarm_dialog()
        self._alarm_acked = set()
        # 关闭报警输出串口（如有）
        if getattr(self, "_alarm_serial", None) is not None:
            try:
                if self._alarm_serial.is_open:
                    self._alarm_serial.close()
            except Exception:
                pass
            self._alarm_serial = None
        self._alarm_serial_on = False

        # 服务保持运行：采集结束不停止，远程客户端可继续查询刚采集的数据；
        # 用户可随时在工具栏「服务」窗口手动停止或切换。
        self.statusBar().showMessage(
            "采集已结束，数据已安全保存到数据库；网络服务保持运行，可手动关闭", 5000)
        # 完成提示（退出链路 notify_done=False 不弹，避免阻塞应用退出）
        if notify_done:
            name_part = f"会话「{stopped_name}」" if stopped_name else "本次采集"
            QMessageBox.information(
                self, "采集已结束",
                f"{name_part}已结束，数据已保存到数据库。")

    def _restart_acquisition(self):
        """重新采集：清空当前会话数据，重新开始。"""
        # 关闭旧会话（清空数据）
        s = store.active
        if s is not None:
            store.close_session(s.id)
        # 重新开始采集
        self._start_acquisition()
        self.statusBar().showMessage("已重新开始采集", 2000)

    # ==================================================================
    #  DataServer 管理
    # ==================================================================

    def _set_window_title(self, base: str) -> None:
        """设置窗口标题（统一入口）；TCP 服务运行中时追加网络连接段。

        各业务场景（打开文件 / 历史 / 远程 / 实时采集）只更新基础文案，
        TCP 段由服务状态决定：服务未启动不显示，运行中追加
        「 | TCP:IP:端口」——IP 取对外通告网卡地址（与网络服务弹窗显示
        一致），未配置通告时用本机默认路由出口 IP。
        """
        self._title_base = base
        self.setWindowTitle(base + self._tcp_title_suffix())

    def _tcp_title_suffix(self) -> str:
        """TCP 服务运行中 → 「 | TCP:IP:端口」；未启动 / 无管理器 → 空串。"""
        mgr = self._service_manager
        if mgr is None:
            return ""
        try:
            st = mgr.get_service_status('tcp')
            if not (st.enabled and st.port):
                return ""
            ip = (getattr(mgr, "advertise_ip", "") or "").strip()
            ip = ip or (getattr(self, "_last_local_ip", "") or "127.0.0.1")
            return f" | TCP:{ip}:{int(st.port)}"
        except Exception:
            return ""

    def _refresh_window_title(self) -> None:
        """TCP 服务状态变化后，按当前基础文案重建窗口标题。"""
        self._set_window_title(getattr(self, "_title_base", "多通道温度分析仪"))

    def _init_service_manager(self) -> str:
        """创建 ServiceManager 实例（不启动任何服务），返回本机 IP。

        服务配置对话框与采集启动共用此入口，保证管理器只创建一次。
        """
        if self._service_manager is not None:
            return self._last_local_ip
        try:
            from device.network import ServiceManager
            import socket

            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                local_ip = "127.0.0.1"

            self._service_manager = ServiceManager(
                history_db=self._history_db,
                device_name=f"多通道温度分析仪-{local_ip}",
                advertise_ip=ConfigIO.load_network_service_config()
                .get("advertise_ip", "")
            )
            self._last_local_ip = local_ip
            return local_ip
        except Exception as e:
            print(f"[SERVER] 初始化服务管理器失败: {e}", flush=True)
            self._service_manager = None
            return "127.0.0.1"

    def _start_data_server(self):
        """按持久化配置启动网络服务（TCP、HTTP、配对码），本机进入「服务端模式」。

        服务生命周期（用户确认的行为）：
        - 应用启动时不自动启动服务；
        - 用户点击「开始采集」时自动激活已勾选的服务；
        - 采集结束后服务保持运行（远程客户端可继续查询数据），可手动停止/切换。
        """
        # 仅数据库存储：服务依赖历史数据库提供查询能力
        if self._history_db is None:
            print("[SERVER] 数据库未初始化，跳过启动服务", flush=True)
            return

        try:
            local_ip = self._init_service_manager()
            if self._service_manager is None:
                return

            # 按持久化配置启动勾选的服务（已运行的服务自动跳过）
            cfg = ConfigIO.load_network_service_config()
            # 同步最新对外网卡配置（服务端页修改后下次启动/重启服务即生效）
            self._service_manager.advertise_ip = cfg.get("advertise_ip", "")
            if cfg.get("tcp_enabled"):
                self._service_manager.start_service('tcp', int(cfg.get("tcp_port", 9527)))
            if cfg.get("http_enabled"):
                self._service_manager.start_service('http', int(cfg.get("http_port", 8080)))
            if cfg.get("pairing_enabled"):
                self._service_manager.start_service('pairing_code')

            # 汇总当前运行状态
            running = []
            for name in ("tcp", "http", "pairing_code"):
                st = self._service_manager.get_service_status(name)
                if st.enabled and st.port:
                    running.append(f"{name}:{st.port}")

            if running:
                self.statusBar().showMessage(
                    f"网络服务已启动：{local_ip} {' '.join(running)}",
                    3000
                )
                print(f"[SERVER] 服务运行中: {', '.join(running)}", flush=True)
            else:
                self.statusBar().showMessage("未启用任何网络服务", 3000)
                print("[SERVER] 未勾选任何服务，网络服务保持关闭", flush=True)

            # 刷新状态栏网络角色指示
            self._update_net_status_label()

        except Exception as e:
            print(f"[SERVER] 启动服务失败: {e}", flush=True)
            self._service_manager = None

        # 服务状态已定：标题追加/移除 TCP 连接信息段
        self._refresh_window_title()

    def _stop_data_server(self):
        """停止所有网络服务（仅应用退出时调用；采集结束不自动停止服务）。"""
        if self._service_manager is None:
            return

        try:
            self._service_manager.stop_all_services()
            print("[SERVER] 所有网络服务已停止", flush=True)
        except Exception as e:
            print(f"[SERVER] 停止服务失败: {e}", flush=True)

        self._service_manager = None
        self._refresh_window_title()

    def _on_acq_data(self, timestamp: float, values: list):
        """采集数据到达 → 送入统一管道（只存有效通道数）。"""
        # 步骤卡第 3 步：首帧数据真实到达即打勾（内部有状态守卫，逐帧调用安全）
        self._acq_step_card.mark_acquire_done()
        # 采集线程发出 Unix 时间戳；统一数据总线的时间轴使用相对秒数。
        live = store.live
        start_time = getattr(live, "started_at", None) if live is not None else None
        abs_timestamp = float(timestamp)  # 保存原始时间戳用于数据库
        if start_time is not None:
            timestamp = float(timestamp) - float(start_time)
        store.append_live(timestamp, values, timestamp=abs_timestamp)
        # 温度报警判定（仅实时采集；判定逻辑在 core.step_alarm，主线程执行）
        if self._data_mode == "live":
            self._check_alarms(abs_timestamp, values)
        self._hb.beat()

    # ==================================================================
    #  温度报警判定（仅实时采集）
    # ==================================================================
    def _check_alarms(self, abs_timestamp, values):
        """推进报警判定与锁定状态机（仅主线程，异常不得影响采集）。"""
        try:
            events, new_prev, new_states = core.step_alarm(
                values, self._alarm_prev, self._alarm_states,
                abs_timestamp, self.alarm_config)
        except Exception as exc:
            print(f"[Alarm] 判定异常：{exc}", flush=True)
            return
        self._alarm_prev = new_prev
        self._alarm_states = new_states
        for ch_idx, atype, kind, actual in events:
            if kind == "active":
                self._on_alarm_active(ch_idx, atype, actual)
            else:
                self._on_alarm_cleared(ch_idx, atype, actual)

    @staticmethod
    def _alarm_channel_label(ch_idx):
        return "通道间温差" if ch_idx is None else f"CH{ch_idx + 1}"

    def _channel_index_to_name(self, idx):
        """通道索引（values 列表下标）→ 曲线显示名（与 color_of 匹配的 display_name）。"""
        cp = self.channel_panel
        if cp is None:
            return None
        card = cp.cards.get(idx)
        if card is None or not hasattr(card, "current_name"):
            return None
        # 卡片纯文本即 display_name（未自定义 = CHn，自定义 = CHn·名称）
        return card.current_name()

    def _channel_has_any_alarm(self, idx):
        """该通道是否仍有任意类型的 active 报警（决定是否清除高亮）。"""
        return any(k[0] == idx and st == "active"
                   for k, st in self._alarm_states.items())

    def _on_alarm_active(self, ch_idx, atype, actual):
        """报警进入 active：高亮 + 历史 + 循环声音 + 报警窗 + 状态栏 + 串口。"""
        ch = self._alarm_channel_label(ch_idx)
        type_zh = self._alarm_type_label(atype)
        print(f"[Alarm] {ch} 触发 {type_zh} 报警（值={actual:.2f}）", flush=True)
        cfg = self.alarm_config
        if cfg.act_highlight and ch_idx is not None:
            name = self._channel_index_to_name(ch_idx)
            if name and getattr(self, "chart_renderer", None) is not None:
                self.chart_renderer._alarm_channels.add(name)
        if cfg.act_log:
            self._record_alarm_event(ch_idx, atype, actual, "active")
        self._refresh_alarm_label()
        # 新报警（未确认）→ 报警窗 + 循环声音
        self._show_alarm_dialog()
        if cfg.act_sound and not alarm_loop_active():
            start_alarm_loop()
        self._update_alarm_serial_output()

    def _on_alarm_cleared(self, ch_idx, atype, actual):
        """报警恢复正常：清除该类型高亮 + 写 cleared + 移除确认标记 + 刷新弹窗 / 声音。"""
        ch = self._alarm_channel_label(ch_idx)
        print(f"[Alarm] {ch} 的 {self._alarm_type_label(atype)} 报警已恢复", flush=True)
        if ch_idx is not None and not self._channel_has_any_alarm(ch_idx):
            name = self._channel_index_to_name(ch_idx)
            if name and getattr(self, "chart_renderer", None) is not None:
                self.chart_renderer._alarm_channels.discard(name)
        if self.alarm_config.act_log:
            self._record_alarm_event(ch_idx, atype, actual, "cleared")
        self._alarm_acked.discard((ch_idx, atype))   # 恢复后清除确认标记
        self._refresh_alarm_label()
        if self._has_unacked_active():
            self._show_alarm_dialog()    # 更新列表
        else:
            stop_alarm_loop()
            self._hide_alarm_dialog()
        self._update_alarm_serial_output()

    def _refresh_alarm_label(self):
        """汇总当前 active 报警，更新状态栏持久指示标签。"""
        if not hasattr(self, "_alarm_label"):
            return
        active = sorted(
            ((idx, atype) for (idx, atype), st in self._alarm_states.items()
             if st == "active"),
            key=lambda e: ((e[0] if e[0] is not None else -1), e[1]))
        if not active:
            self._alarm_label.setText("")
            self._set_status_label_semantic(self._alarm_label, "warning")
            return
        parts = []
        for idx, atype in active:
            if idx is None and atype == "diff":
                parts.append("温差")
                continue
            tag = self._alarm_channel_label(idx)
            parts.append(f"{tag} {self._alarm_type_label(atype)}")
        self._alarm_label.setText("⚠ " + " / ".join(parts))
        self._set_status_label_semantic(self._alarm_label, "error")

    @staticmethod
    def _alarm_type_label(atype):
        """报警类型英文 → 中文（用于状态栏与弹窗文案）。"""
        return {"high": "超上限",
                "rate": "变化率", "diff": "温差"}.get(atype, atype)

    def _alarm_threshold_for(self, atype):
        """取某类型对应的当前阈值（写历史库时记录）。"""
        c = self.alarm_config
        return {"high": c.temp_high,
                "rate": c.rate_threshold,
                "diff": c.diff_threshold}.get(atype)

    def _record_alarm_event(self, ch_idx, atype, actual, status):
        """写一条报警事件到历史库；无历史库或非实时会话则跳过。"""
        try:
            db = getattr(self, "_history_db", None)
            if db is None:
                return
            s = store.active
            if s is None or not getattr(s, "is_live", False):
                return
            session_id = getattr(s, "session_id", "") or ""
            if not session_id:
                return
            if ch_idx is None:
                ch_key = "__diff__"
            else:
                ch = s.channel_by_index(ch_idx)
                ch_key = ch.key if ch is not None else f"CH{ch_idx + 1}"
            db.insert_alarm_event(
                session_id, ch_key, time.time(), atype,
                self._alarm_threshold_for(atype), actual, status)
        except Exception as exc:
            print(f"[Alarm] 写历史失败：{exc}", flush=True)

    def _ack_alarms(self):
        """复位（确认静音）：所有 active 报警标记已确认 → 停声音、关弹窗。

        曲线高亮与状态栏指示保持不变，直到温度真正恢复正常后自动清除。
        """
        for key, st in self._alarm_states.items():
            if st == "active":
                self._alarm_acked.add(key)
        stop_alarm_loop()
        self._hide_alarm_dialog()
        self.statusBar().showMessage(
            "报警已复位（确认静音），高亮保持到温度恢复正常", 3000)

    def _has_unacked_active(self):
        """是否存在未确认（未复位）的 active 报警。"""
        return any(st == "active" and k not in self._alarm_acked
                   for k, st in self._alarm_states.items())

    def _unacked_active_items(self):
        """生成未确认 active 报警的显示文本列表（供报警窗）。"""
        items = []
        for (idx, atype), st in self._alarm_states.items():
            if st != "active" or (idx, atype) in self._alarm_acked:
                continue
            ch = "通道间温差" if idx is None else self._alarm_channel_label(idx)
            items.append(f"{ch}    {self._alarm_type_label(atype)}")
        return items

    def _show_alarm_dialog(self):
        """显示 / 更新非模态报警窗（act_popup 控制开关）。"""
        if not self.alarm_config.act_popup:
            return
        items = self._unacked_active_items()
        if not items:
            return
        if self._alarm_dialog is None:
            from ui.widgets.alarm_dialog import AlarmDialog
            self._alarm_dialog = AlarmDialog(self)
            self._alarm_dialog.acked.connect(self._ack_alarms)
        self._alarm_dialog.set_alarms(items)

    def _hide_alarm_dialog(self):
        """隐藏报警窗（不清除状态，温度恢复后由 _show 重开）。"""
        if self._alarm_dialog is not None:
            self._alarm_dialog.hide()

    def _update_alarm_serial_output(self):
        """按总报警状态聚合输出 Modbus 线圈（任一 active→ON，全部恢复→OFF）。"""
        cfg = self.alarm_config
        if not cfg.act_serial or not cfg.serial_port:
            return
        has_active = any(st == "active" for st in self._alarm_states.values())
        if has_active and not self._alarm_serial_on:
            self._send_alarm_coil(True)
            self._alarm_serial_on = True
        elif not has_active and self._alarm_serial_on:
            self._send_alarm_coil(False)
            self._alarm_serial_on = False

    def _send_alarm_coil(self, on):
        """通过独立报警串口发送 Modbus 写线圈帧；失败静默不阻断采集。"""
        cfg = self.alarm_config
        try:
            self._ensure_alarm_serial()
            if self._alarm_serial is None:
                return
            frame = build_write_single_coil(
                cfg.modbus_slave, cfg.modbus_coil, on)
            self._alarm_serial.write(frame)
        except Exception as exc:
            print(f"[Alarm] 串口输出失败：{exc}", flush=True)

    def _ensure_alarm_serial(self):
        """按需创建并打开独立报警串口（绝不复用采集串口）。"""
        cfg = self.alarm_config
        if not cfg.act_serial or not cfg.serial_port:
            return
        if self._alarm_serial is None:
            from device.acquisition import SerialPortManager
            self._alarm_serial = SerialPortManager()
        if not self._alarm_serial.is_open:
            if not self._alarm_serial.open(cfg.serial_port, cfg.serial_baud):
                print(f"[Alarm] 报警串口 {cfg.serial_port} 打开失败", flush=True)

    def _on_acq_worker_finished(self):
        """后台采集线程最终退出后，从 exiting 池移除（C4 兜底）。"""
        w = self.sender()
        try:
            self._acq_workers_exiting.remove(w)
        except ValueError:
            pass

    def _on_acq_connection(self, connected: bool):
        """采集连接状态变化。"""
        if connected:
            # 仅当正在采集中才更新状态，避免暂停/停止瞬间迟到的
            # connection_changed(True) 覆盖已暂停/已停止的心跳状态
            if self._acq_state == "acquiring":
                self._set_acq_status(
                    acq_status_label("acquiring", self._acq_port_name()), "success")
                self._hb.set_state(STATE_ONLINE)
            elif self._acq_state == "paused":
                self._set_acq_status(
                    acq_status_label("paused", self._acq_port_name()), "warning")
                self._hb.set_state(STATE_PAUSED)
        else:
            self._set_acq_status(
                acq_status_label("disconnected", self._acq_port_name()), "error")
            self._hb.set_state(STATE_OFF)
            self._acq_step_card.dismiss()   # 等首帧的步骤卡让位给断开提示
            self._acq_step_card.show_notice(
                "设备连接断开，正在结束并保存采集数据", "error")
            self.statusBar().showMessage("设备连接断开，正在结束并保存采集数据", 5000)
            if self._acq_state in ("acquiring", "paused"):
                self._stop_acquisition()
            self._acq_worker = None

    def _open_channel_naming_dialog(self) -> None:
        """打开通道命名弹窗（面板顶部「自定义各通道名称」入口）。

        复用实例、关闭仅隐藏；每次打开按当前活跃会话重建通道行，
        名称池改动实时生效（refresh_pool / refresh_channel_candidates）。
        """
        dlg = getattr(self, "_naming_dialog", None)
        if dlg is None:
            from ui.dialogs.channel_naming_dialog import ChannelNamingDialog
            dlg = ChannelNamingDialog(self)
            self._naming_dialog = dlg
        dlg.open_dialog()

    def open_settings(self) -> None:
        """打开参数设置弹窗"""
        from ui.dialogs.settings_dialog import SettingsDialog

        # 构建导航树和 stack（每次打开重建以确保与当前状态同步）
        from PyQt5.QtWidgets import QTreeWidget, QTreeWidgetItem, QStackedWidget
        nav = QTreeWidget()
        nav.setHeaderHidden(True)
        nav.setFixedWidth(160)
        nav.setIndentation(8)
        nav.setObjectName("navBar")

        stack = QStackedWidget()

        # 扁平单级导航：7 个设置页全部作为一级项，消除分组与页面同名、
        # 关键词重复以及单子项分组的冗余层级。页面顺序与
        # SettingsDialog._build_all_pages 保持一致。
        page_names = [
            "通道管理", "采集设置", "采集时间", "数据处理",
            "轴设置", "组合图", "温升统计", "报警设置", "悬浮监控",
        ]
        page_indexes = {name: index for index, name in enumerate(page_names)}

        self._nav_page_map = {}
        for page_name in page_names:
            item = QTreeWidgetItem(nav, [page_name])
            self._nav_page_map[id(item)] = page_indexes[page_name]

        dlg = SettingsDialog(self, nav, stack)
        dlg._apply_style()
        dlg._apply_nav_style()
        nav.currentItemChanged.connect(dlg._on_nav_tree)
        # Select first page
        if nav.topLevelItemCount() > 0:
            nav.setCurrentItem(nav.topLevelItem(0))
            dlg.update_page_header(0)

        self.settings_dlg = dlg
        # 应用上次保存的核心处理参数（若无数据则不刷新）
        if getattr(self, "_saved_params", None):
            dlg._apply_param_state(self._saved_params)
            dlg.set_page_status("自动保存", "info")
        self._sync_smoothing_switches(dlg.chk_smooth.isChecked())
        dlg.show()

    def _sync_smoothing_switches(self, enabled):
        """同步设置页与工具栏的整体平滑开关，避免两套状态分叉。"""
        enabled = bool(enabled)
        toolbar = getattr(self, "btn_smooth_toolbar", None)
        dlg = getattr(self, "settings_dlg", None)
        if toolbar is not None and toolbar.isChecked() != enabled:
            toolbar.blockSignals(True)
            toolbar.setChecked(enabled)
            toolbar.blockSignals(False)
        if toolbar is not None:
            toolbar.setText("平滑：开" if enabled else "平滑：关")
        if dlg is not None and hasattr(dlg, "chk_smooth") and dlg.chk_smooth.isChecked() != enabled:
            dlg.chk_smooth.blockSignals(True)
            dlg.chk_smooth.setChecked(enabled)
            dlg.chk_smooth.blockSignals(False)

    def _on_toolbar_smooth_toggled(self, enabled):
        """工具栏切换平滑开关：同步 UI、持久化、立即应用。"""
        self._sync_smoothing_switches(enabled)
        saved = getattr(self, "_saved_params", None)
        if isinstance(saved, dict) and saved:
            saved["smooth"] = bool(enabled)
            try:
                ConfigIO.save_section("parameters", saved)
            except Exception:
                pass
        self.apply_and_refresh()

    def _open_data_edit_dialog(self):
        """工具栏「编辑」：打开当前会话的数据编辑弹窗（录制中的实时会话不可编辑）。

        已打开时复用前置，避免连点/双击堆叠出多个同款弹窗。
        """
        s = store.active
        if s is None:
            QMessageBox.information(self, "提示", "请先打开历史数据或结束当前采集")
            return
        if s.is_live and s.is_recording:
            QMessageBox.information(
                self, "提示", "实时采集进行中不能编辑数据；请先结束采集")
            return
        dlg = getattr(self, "_data_edit_dialog", None)
        if dlg is not None and dlg.isVisible():
            dlg.raise_()
            dlg.activateWindow()
            return
        from ui.dialogs.data_edit_dialog import DataEditDialog
        self._data_edit_dialog = DataEditDialog(self, s)
        self._data_edit_dialog.show()

    def _update_edit_button_state(self):
        """同步工具栏「编辑」按钮可用态；并关闭指向已切换会话的编辑弹窗。

        活跃会话切换后旧弹窗仍在编辑旧会话，应用会写错目标，故主动关闭。
        """
        s = store.active
        dlg = getattr(self, "_data_edit_dialog", None)
        if dlg is not None and dlg.isVisible() and getattr(dlg, "session", None) is not s:
            dlg.close()
        btn = getattr(self, "btn_edit_data", None)
        if btn is None:
            return
        btn.setEnabled(s is not None and not (s.is_live and s.is_recording))

    def _on_settings_smooth_toggled(self, enabled):
        """设置页切换平滑开关后同步工具栏并立即应用。"""
        self._sync_smoothing_switches(enabled)
        self.apply_and_refresh()

    def export_current_canvas(self):
        """单图导出：UI 线程 savefig，加载卡片反馈并纳入导出守卫。"""
        if self._reject_if_busy("导出当前图"):
            return
        self._exporting = True
        try:
            with self._busy_card.begin("正在导出当前图…"):
                self.export_mgr.export_current_canvas()
        finally:
            self._exporting = False

    def _build_export_drawer(self):
        """创建右侧非模态导出抽屉，固定宽度并默认隐藏。"""
        host = self.main_content_host
        self.export_drawer = ExportDialog(
            host, self.export_mgr.available_tasks(),
            export_theme_options=self.export_mgr.export_theme_options())
        self.export_drawer_animation = QPropertyAnimation(
            self.export_drawer, b"geometry", self)
        self.export_drawer_animation.setDuration(180)
        self.export_drawer_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.export_drawer.export_requested.connect(self._export_selected_tasks)
        self.export_drawer.close_requested.connect(self.close_export_drawer)
        self.export_drawer.hide()

    def _drawer_geometry(self):
        """计算抽屉在中央内容区右侧的固定位置。"""
        host = self.main_content_host
        width = self.export_drawer.width()
        height = host.height()
        return host.width() - width, 0, width, height

    def _sync_export_drawer_geometry(self):
        """窗口变化时保持抽屉贴右并占满中央内容区高度。"""
        if not hasattr(self, "export_drawer"):
            return
        x, y, width, height = self._drawer_geometry()
        self.export_drawer.setGeometry(x, y, width, height)

    def open_export_dialog(self) -> None:
        """展开或收回右侧非模态导出抽屉。"""
        if self.export_drawer.isVisible():
            self.close_export_drawer()
            return
        self._disconnect_export_drawer_hide()
        target = QRect(*self._drawer_geometry())
        start = QRect(target)
        start.moveLeft(self.main_content_host.width())
        self.export_drawer_animation.stop()
        self.export_drawer.setGeometry(start)
        self.export_drawer.show()
        self.export_drawer.raise_()
        self.export_drawer_animation.setStartValue(start)
        self.export_drawer_animation.setEndValue(target)
        self.export_drawer_animation.start()

    def close_export_drawer(self) -> None:
        """播放收回动画，结束后隐藏抽屉。"""
        if not hasattr(self, "export_drawer") or not self.export_drawer.isVisible():
            return
        current = self.export_drawer.geometry()
        end = QRect(current)
        end.moveLeft(self.main_content_host.width())
        self.export_drawer_animation.stop()
        self.export_drawer_animation.setStartValue(current)
        self.export_drawer_animation.setEndValue(end)
        try:
            self._disconnect_export_drawer_hide()
        except TypeError:
            pass
        self.export_drawer_animation.finished.connect(self.export_drawer.hide)
        self.export_drawer_animation.start()

    def _disconnect_export_drawer_hide(self):
        """解除收回动画的隐藏回调，避免影响下一次展开动画。"""
        try:
            self.export_drawer_animation.finished.disconnect(
                self.export_drawer.hide)
        except (TypeError, RuntimeError):
            pass

    def _export_selected_tasks(self, task_ids, directory, remember_path,
                               image_background="light", image_format="svg",
                               export_theme=_EXPORT_THEME_NOT_SET):
        """执行统一导出任务（C1：建图 + savefig 移至后台线程，UI 不冻结）。

        export_selected 内部无 GUI 操作（dialog 已由抽屉收集 directory），
        仅建 Figure + FigureCanvasAgg 渲染 + 写文件，可安全在 worker 跑；
        结果回 UI 线程槽汇总提示。单导出方法（export_png/a4 等）内含 QFileDialog，
        仍走 UI 线程。
        """
        if self._reject_if_busy("导出"):
            return  # C2：采集/导入/重算进行中时拒绝；含 _exporting 防重入
        self._exporting = True
        self.statusBar().showMessage("正在导出，请稍候…", 0)
        self._busy_card.begin("正在导出，请稍候…")
        self._export_worker = _BackgroundWorker(
            self.export_mgr.export_selected,
            task_ids, directory,
            remember_path=remember_path,
            image_background=image_background,
            image_format=image_format,
            export_theme=export_theme)
        self._export_worker.done.connect(self._on_export_done)
        self._export_worker.failed.connect(self._on_export_failed)
        self._export_worker.finished.connect(self._export_worker.deleteLater)
        self._export_worker.start()

    def _on_export_done(self, result: dict) -> None:
        """批量导出完成（UI 线程）：汇总提示。"""
        self._exporting = False
        self._busy_card.finish()
        self.statusBar().clearMessage()
        successes = result.get("successes", [])
        failures = result.get("failures", [])
        lines = [f"成功导出 {len(successes)} 项。"]
        folder = result.get("folder")
        if folder:
            lines.append(f"导出文件夹：{folder}")
        if successes:
            lines.append("\n".join(item["path"] for item in successes))
        if failures:
            lines.append("\n失败项目：")
            lines.extend(f"{item['name']}：{item['error']}" for item in failures)
        if failures:
            QMessageBox.warning(self, "导出完成（部分失败）", "\n".join(lines))
        else:
            QMessageBox.information(self, "导出完成", "\n".join(lines))
        self.statusBar().showMessage(
            f"导出完成：成功 {len(successes)} 项，失败 {len(failures)} 项", 5000)
        # 勾选状态已持久化记忆，导出完成后保留勾选不清空
        self.close_export_drawer()

    def _on_export_failed(self, msg: str) -> None:
        """批量导出异常（UI 线程）。"""
        self._exporting = False
        self._busy_card.finish()
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "导出失败", msg)

    def export_png(self):
        """单导出三张总览图：UI 线程 3 次 savefig，加载卡片反馈。"""
        if self._reject_if_busy("导出图片"):
            return
        self._exporting = True
        try:
            with self._busy_card.begin("正在导出图片，请稍候…"):
                self.export_mgr.export_png()
        finally:
            self._exporting = False

    def export_a4(self):
        """单导出 A4 报告：UI 线程建图 + 300DPI 渲染，加载卡片反馈。"""
        if self._reject_if_busy("导出 A4 报告"):
            return
        self._exporting = True
        try:
            with self._busy_card.begin("正在生成 A4 报告，请稍候…"):
                self.export_mgr.export_a4()
        finally:
            self._exporting = False

    def export_data(self):
        """单导出处理数据：UI 线程插值 + to_excel/csv，加载卡片反馈。"""
        if self._reject_if_busy("导出数据"):
            return
        self._exporting = True
        try:
            with self._busy_card.begin("正在导出处理数据，请稍候…"):
                self.export_mgr.export_data()
        finally:
            self._exporting = False

    # ==================================================================
    #  核心工作流
    # ==================================================================
    def apply_and_refresh(self) -> None:
        """参数变化 → Pipeline 全量重算 → refresh_plots() → update_stats()。

        C1：离线模式重算移至后台线程（buffer 静态、无并发竞态），避免大文件
        参数调整冻结 UI；live 模式重算与 on_appended 增量强耦合，留 UI 线程
        保持串行化（避免全量重算覆盖增量结果）。
        """
        s = store.active
        if s is None:
            return
        self.pipeline.set_params(self._get_pipeline_params())
        if self._data_mode == "live":
            # live 重算与增量耦合留 UI 线程：加载卡片给出过程反馈
            with self._busy_card.begin("正在应用参数并重算…"):
                self.pipeline.recompute(s)
            self._finish_recompute()
        else:
            self._recompute_async(s)

    def _finish_recompute(self) -> None:
        """重算完成后刷新界面（UI 线程）。"""
        self.refresh_plots()
        self._update_stats()
        self._save_param_config()  # 参数应用时自动持久化
        self.statusBar().showMessage("已刷新", 2000)
        self._set_settings_page_status("已应用并刷新", "success")

    def _recompute_async(self, s) -> None:
        """离线模式：后台全量重算（C1）。buffer 静态，worker 安全。"""
        if getattr(self, "_recomputing", False):
            return
        self._recomputing = True
        self.statusBar().showMessage("正在重算，请稍候…", 0)
        self._busy_card.begin("正在重算，请稍候…")
        self._recompute_worker = _BackgroundWorker(self.pipeline.recompute, s)
        self._recompute_worker.done.connect(self._on_recompute_done)
        self._recompute_worker.failed.connect(self._on_recompute_failed)
        self._recompute_worker.finished.connect(self._recompute_worker.deleteLater)
        self._recompute_worker.start()

    def _on_recompute_done(self, _out) -> None:
        """后台重算完成（UI 线程）。"""
        self._recomputing = False
        self._busy_card.finish()
        self.statusBar().clearMessage()
        self._finish_recompute()

    def _on_recompute_failed(self, msg: str) -> None:
        """后台重算失败（UI 线程）。"""
        self._recomputing = False
        self._busy_card.finish()
        self.statusBar().showMessage("重算失败", 5000)
        store.error.emit(f"数据重算失败：\n{msg}")

    def _set_settings_page_status(self, text, level="info"):
        """向当前设置页反馈操作结果；设置页关闭时安全忽略。"""
        dialog = getattr(self, "settings_dlg", None)
        if dialog is not None and hasattr(dialog, "set_page_status"):
            dialog.set_page_status(text, level)

    def refresh_plots(self) -> None:
        """委托给 ChartRenderer"""
        self.chart_renderer.refresh_plots()
        self._update_axis_quick_range()

    def _guard(self) -> bool:
        """检查是否有活跃会话（供 ExportManager 等调用）"""
        if store.active is None:
            QMessageBox.information(self, "提示", "请先打开数据文件或开始采集")
            return False
        return True

    # ==================================================================
    #  数据计算
    # ==================================================================
    def _compute_processed(self):
        """委托给 Pipeline 全量重算（兼容旧调用方，如 ChartRenderer.refresh_plots）。"""
        s = store.active
        if s is None:
            return
        self.pipeline.set_params(self._get_pipeline_params())
        self.pipeline.recompute(s)

    def _session_uses_edit_params(self, s) -> bool:
        """会话是否走"编辑参数"作用域：历史/文件/已结束的实时会话。

        录制中的实时会话必须反映真实温度（报警判定依赖原始帧值），
        只允许显示层平滑（全局开关）；重采样永不参与实时增量链路。
        """
        return not (getattr(s, "is_live", False)
                    and getattr(s, "is_recording", False))

    def _live_smooth_params(self):
        """实时显示平滑的全局参数来源。

        设置页可见时读设置页控件；否则读持久化的 parameters 分区快照。
        （修复：此前设置页从未打开时 _get_current_params 返回硬编码默认值，
        工具栏平滑开关实际不生效。）
        """
        dlg = getattr(self, "settings_dlg", None)
        if (dlg is not None and hasattr(dlg, "chk_smooth")
                and dlg.isVisible()):
            return bool(dlg.chk_smooth.isChecked()), int(dlg.sp_smooth.value())
        saved = getattr(self, "_saved_params", None) or {}
        try:
            win = int(saved.get("smooth_w", 5))
        except (TypeError, ValueError):
            win = 5
        return bool(saved.get("smooth", False)), max(1, win)

    def _default_edit_params(self) -> dict:
        """会话编辑参数默认值：重采样关闭；平滑沿用当前全局设置，
        保证"结束采集/加载历史"瞬间曲线观感连续、不跳变。"""
        enabled, win = self._live_smooth_params()
        saved = getattr(self, "_saved_params", None) or {}
        try:
            interval = float(saved.get("resample_int", 30.0))
        except (TypeError, ValueError):
            interval = 30.0
        return {"resample": False, "resample_int": max(0.5, interval),
                "resample_method": 0, "smooth": enabled, "smooth_w": win}

    def _edit_params_for(self, s) -> dict:
        """读取（首次访问时冻结默认值）某会话的编辑参数。"""
        if s.id not in self._session_edit_params:
            self._session_edit_params[s.id] = self._default_edit_params()
        return self._session_edit_params[s.id]

    def _get_pipeline_params(self, session=None) -> dict:
        """把处理参数转为 Pipeline 格式（start_time QTime → start_sec float）。

        作用域规则（2026-08-22 平滑/重采样拆分）：
          - 录制中的实时会话：平滑=全局显示滤波；重采样恒关；
          - 其余会话（历史/文件/已结束采集）：读该会话的编辑参数
            （MainWindow._session_edit_params，由"编辑"弹窗维护）。
        """
        s = session if session is not None else store.active
        p = self._get_current_params()
        st = p["start_time"]
        start_sec = st.hour() * 3600 + st.minute() * 60 + st.second()
        params = {
            "interval": p["interval"],
            "start_sec": start_sec,
            "relabel": p["relabel"],
            "diff_en": p["diff_en"], "diff_thr": p["diff_thr"],
            "z_en": p["z_en"], "z_win": p["z_win"], "z_sig": p["z_sig"],
            "slope_en": p["slope_en"], "slope_thr": p["slope_thr"],
            "fill_k": p["fill_k"],
        }
        if s is not None and self._session_uses_edit_params(s):
            edit = self._edit_params_for(s)
            params["resample"] = bool(edit["resample"])
            params["resample_int"] = float(edit["resample_int"])
            params["resample_method"] = int(edit["resample_method"])
            params["smooth_en"] = bool(edit["smooth"])
            params["smooth_win"] = int(edit["smooth_w"])
        else:
            smooth_en, smooth_win = self._live_smooth_params()
            params["resample"] = False
            params["resample_int"] = 0.0
            params["resample_method"] = 0
            params["smooth_en"] = smooth_en
            params["smooth_win"] = smooth_win
        return params

    def _get_current_params(self) -> dict:
        """获取当前处理参数（从 settings_dlg 或默认值）"""
        dlg = getattr(self, 'settings_dlg', None)
        if dlg is not None and hasattr(dlg, 'sp_interval'):
            return {
                "interval": dlg.sp_interval.value(),
                "start_time": dlg.ed_start.time(),
                "relabel": dlg.chk_relabel.isChecked(),
                "diff_en": dlg._anomaly_method() == "diff",
                "diff_thr": dlg.sp_diff.value(),
                "z_en": dlg._anomaly_method() == "z",
                "z_win": dlg.sp_zwin.value(),
                "z_sig": dlg.sp_zsig.value(),
                "slope_en": dlg._anomaly_method() == "slope",
                "slope_thr": dlg.sp_slope.value(),
                "fill_k": dlg.sp_fill.value(),
            }
        # 默认值
        return {
            "interval": 10.0,
            "start_time": QTime(0, 0, 0),
            "relabel": False,
            "diff_en": False, "diff_thr": 1.5,
            "z_en": True, "z_win": 11, "z_sig": 3.0,
            "slope_en": False, "slope_thr": 0.15,
            "fill_k": 3,
        }

    # ==================================================================
    #  信号处理
    # ==================================================================
    def _on_tab_changed(self, idx):
        """切换标签页时委托给 ChartRenderer 渲染当前页"""
        self._set_current_tab_semantic(self.tabs, idx)
        self.chart_renderer._render_visible_tab()
        if idx == self.idx_stat:
            self.stat_panel.update_stats()

    # ==================================================================
    #  统计
    # ==================================================================
    def _update_stats(self):
        """更新温升统计表格"""
        self.stat_panel.update_stats()

    # ==================================================================
    #  设置页面动作（被 SettingsDialog 回调）
    # ==================================================================
    def _apply_axis_page(self):
        """应用轴设置 → 保存配置 → 刷新"""
        dlg = getattr(self, 'settings_dlg', None)
        if dlg is not None:
            self.ax_time_mode = "custom" if dlg.ax_time_custom_rb.isChecked() else "auto"
            self.ax_time_min = dlg.ax_time_min_sp.value()
            self.ax_time_max = dlg.ax_time_max_sp.value()
            self.ax_time_step = dlg.ax_time_step_sp.value()
            # 温度轴统一智能模式：扩展系数在设置页调节，基础窗口在左侧底部调节区
            self.ax_temp_lo_factor = dlg.ax_temp_lo_factor_sp.value()
            self.ax_temp_hi_factor = dlg.ax_temp_hi_factor_sp.value()
            # 双区视图：开关 + 右区实时窗宽度（下拉档位 userData 存秒数）
            self.ax_dual_view_enabled = bool(dlg.ax_dual_view_chk.isChecked())
            self.ax_live_window_sec = int(dlg.ax_live_window_cmb.currentData())
        ConfigIO.save_axis_config(self)
        # 开关/窗宽变化使左右切分点与接缝改变：set_dual_view_config 内部
        # 清增量/悬停/布局缓存并重绘可见页
        self._sync_dual_view_config()
        self._sync_alarm_high_to_axis()
        self._sync_axis_quick_panel()
        self.refresh_plots()
        self.statusBar().showMessage("轴设置已应用", 2000)
        self._set_settings_page_status("已应用并保存", "success")

    def _apply_a4_page(self):
        """应用 A4 配置 → 保存 → 刷新"""
        dlg = getattr(self, 'settings_dlg', None)
        if dlg is not None:
            self.a4_custom1 = (dlg.a4_e1a.value(),
                               None if dlg.a4_c1end.isChecked() else dlg.a4_e1b.value())
            self.a4_custom2 = (dlg.a4_e2a.value(),
                               None if dlg.a4_c2end.isChecked() else dlg.a4_e2b.value())
            self.a4_mark = dlg.a4_chk_mark.isChecked()
        ConfigIO.save_a4_config(self)
        self.refresh_plots()
        self.statusBar().showMessage("A4 配置已应用", 2000)
        self._set_settings_page_status("已应用并保存", "success")

    def _apply_overview_page(self):
        """应用概览配置 → 保存 → 刷新"""
        dlg = getattr(self, 'settings_dlg', None)
        if dlg is not None:
            self.win_front = [dlg.ov_w1.value(), dlg.ov_w2.value(), dlg.ov_w3.value()]
            self.show_window_stats = bool(dlg.ov_show_window_stats.isChecked())
            # 更新标签名
            for i, tab in enumerate([self.tab_10, self.tab_20, self.tab_30]):
                if i < len(self.win_front):
                    self.tabs.setTabText(i + 1, f"前{_fmt_win_int(self.win_front[i])}分钟")
        ConfigIO.save_overview_config(self)
        self.refresh_plots()
        self.statusBar().showMessage("概览配置已应用", 2000)
        self._set_settings_page_status("已应用并保存", "success")

    def _apply_stat_page(self):
        """应用温升自适应参数 → 保存 → 重算"""
        dlg = getattr(self, 'settings_dlg', None)
        if dlg is not None:
            self.rise_config = core.RiseAnalysisConfig(
                filter_window_sec=dlg.rise_filter_window.value(),
                steady_duration_sec=dlg.rise_steady_duration.value(),
                slope_decay_sensitivity=dlg.rise_slope_decay.value(),
            ).normalized()
        ConfigIO.save_rise_config(self)
        if self.stat_panel is not None:
            self.stat_panel.clear_result_cache()
        self._update_stats()
        self.statusBar().showMessage("温升分析参数已应用", 2000)
        self._set_settings_page_status("已应用并保存", "success")

    def _sync_alarm_high_to_axis(self):
        """报警上限智能跟随：follow_axis 开启时 上限 = 温度轴基础窗口上限 - 偏移。

        覆盖轴基础窗口全部写路径（左快调区 / 设置·轴设置页应用 / 配置热重载）
        与启动加载；自定义模式不干预。图表经 refresh_plots 重画阈值线。
        """
        cfg = self.alarm_config
        if not cfg.follow_axis:
            return
        try:
            new_high = round(float(self.ax_temp_base_hi)
                             - float(cfg.follow_offset), 2)
        except (TypeError, ValueError):
            return
        if abs(cfg.temp_high - new_high) < 1e-9:
            return
        self.alarm_config = _dc_replace(cfg, temp_high=new_high).normalized()
        ConfigIO.save_alarm_config(self)
        if getattr(self, "chart_renderer", None) is not None:
            self.refresh_plots()
        dlg = getattr(self, "settings_dlg", None)
        if (dlg is not None and dlg.isVisible()
                and hasattr(dlg, "alm_high")):
            dlg.alm_high.blockSignals(True)
            dlg.alm_high.setValue(self.alarm_config.temp_high)
            dlg.alm_high.blockSignals(False)

    def _apply_alarm_page(self):
        """应用报警设置页参数 → 保存 → 热生效。

        跟随模式（follow_axis）下上限由「轴基础窗口上限 - 偏移」计算得出，
        忽略置灰的上限输入框当前值。
        """
        dlg = getattr(self, 'settings_dlg', None)
        if dlg is not None:
            follow = dlg.alm_follow_axis.isChecked()
            offset = dlg.alm_follow_offset.value()
            high = (round(float(self.ax_temp_base_hi) - offset, 2) if follow
                    else dlg.alm_high.value())
            self.alarm_config = core.AlarmConfig(
                enabled=dlg.alm_enabled.isChecked(),
                temp_high=high,
                follow_axis=follow,
                follow_offset=offset,
                rate_threshold=dlg.alm_rate.value(),
                diff_threshold=dlg.alm_diff.value(),
                act_highlight=dlg.alm_act_highlight.isChecked(),
                act_sound=dlg.alm_act_sound.isChecked(),
                act_popup=dlg.alm_act_popup.isChecked(),
                act_log=dlg.alm_act_log.isChecked(),
                act_serial=dlg.alm_act_serial.isChecked(),
                serial_port=dlg.alm_serial_port.currentText().strip(),
                serial_baud=dlg.alm_serial_baud.value(),
                modbus_slave=dlg.alm_modbus_slave.value(),
                modbus_coil=dlg.alm_modbus_coil.value(),
                sound_file=self.alarm_config.sound_file,
            ).normalized()
        self._sync_alarm_high_to_axis()
        ConfigIO.save_alarm_config(self)
        self.statusBar().showMessage("报警参数已应用", 2000)
        self._set_settings_page_status("已应用并保存", "success")

    def _save_combo_layout_config(self, ovr):
        """保存组合图布局配置"""
        ConfigIO.save_combo_layout_config(self, ovr)

    def reset_params(self) -> None:
        """重置为初始参数"""
        if store.active is None:
            return
        dlg = getattr(self, 'settings_dlg', None)
        if dlg is not None:
            dlg.sp_interval.setValue(10.0)
            dlg.ed_start.setTime(QTime(0, 0, 0))
            dlg.chk_relabel.setChecked(False)
            dlg._set_anomaly_method("z")
            dlg.sp_diff.setValue(1.5)
            dlg.sp_zwin.setValue(11); dlg.sp_zsig.setValue(3.0)
            dlg.sp_slope.setValue(0.15)
            dlg.sp_fill.setValue(3)
            dlg.chk_smooth.setChecked(False); dlg.sp_smooth.setValue(5)
        self.apply_and_refresh()
        self.statusBar().showMessage("参数已重置为默认值", 3000)

    # ==================================================================
    #  通道名称列表管理（被 SettingsDialog / ChannelNamingDialog 调用）
    # ==================================================================
    def _populate_name_list_table(self, dlg=None):
        """填充名称列表表格。dlg 可显式传入（SettingsDialog 构造期间 self.settings_dlg 尚未赋值）。

        重填期间屏蔽 itemChanged：编辑回调链（_on_name_list_changed →
        _notify_name_list_changed → 本方法）会重填表格，程序化 setItem
        同样触发 itemChanged，不屏蔽将造成无限递归。
        """
        dlg = dlg or getattr(self, 'settings_dlg', None)
        if dlg is None or not hasattr(dlg, 'tbl_name_list'):
            return
        dlg.tbl_name_list.blockSignals(True)
        dlg.tbl_name_list.setRowCount(0)
        for nm in self.name_list:
            row = dlg.tbl_name_list.rowCount()
            dlg.tbl_name_list.insertRow(row)
            dlg.tbl_name_list.setItem(row, 0, QTableWidgetItem(nm))
        dlg.tbl_name_list.blockSignals(False)

    def _on_name_list_changed(self, item):
        """名称列表编辑完成"""
        if item is None:
            return
        row = item.row()
        new_name = item.text().strip()
        if new_name and 0 <= row < len(self.name_list):
            self.name_list[row] = new_name
        self._save_name_list()
        self._notify_name_list_changed()

    def _add_name_to_list(self, source_dlg=None):
        """添加名称，并在命名弹窗中直接进入新行编辑。"""
        self.name_list.append("新名称")
        self._save_name_list()
        self._notify_name_list_changed()
        if source_dlg is not None and hasattr(source_dlg, "edit_name_list_row"):
            source_dlg.edit_name_list_row(len(self.name_list) - 1)

    def _remove_name_from_list(self, source_dlg=None):
        """从列表删除所选名称。source_dlg 指明取行的表格（设置弹窗 / 命名弹窗）。"""
        dlg = source_dlg or getattr(self, 'settings_dlg', None)
        if dlg is None or not hasattr(dlg, 'tbl_name_list'):
            return
        row = dlg.tbl_name_list.currentRow()
        if 0 <= row < len(self.name_list):
            self.name_list.pop(row)
            self._save_name_list()
        self._notify_name_list_changed()

    def _notify_name_list_changed(self):
        """名称池变化广播：刷新已打开的名称消费界面。

        此前设置弹窗改名称列表只落盘 settings.json，需重新连接/打开文件
        触发整面板重建才生效；这里在每次增删改后立即刷新：
          - 通道命名弹窗（若存在）重填名称池表格并重算各行下拉候选
            （左侧卡片已是纯文本，候选只存在于弹窗中）；
          - 设置弹窗名称列表表格保持两处编辑入口一致。
        """
        dlg = getattr(self, "_naming_dialog", None)
        if dlg is not None and hasattr(dlg, "refresh_pool"):
            dlg.refresh_pool()
        self._populate_name_list_table()

    # ==================================================================
    #  配色方案管理（被 SettingsDialog 调用）
    # ==================================================================
    def _load_schemes(self) -> list:
        """读取配色方案列表：内置方案 + 用户自定义方案合并（内置优先、按名去重）。

        内置高对比配色各方案的 colors 由 variant 标记从 Theme 静态获取
        （统一默认 / 各主题专属），各方案颜色固定、不随当前主题变化。
        返回副本，调用方增删 active 标记不影响模块级 BUILTIN_COLOR_SCHEMES。
        """
        file_schemes = []
        try:
            data = ConfigIO.load_section("color_schemes", [], ())
            if isinstance(data, list):
                # 过滤已废弃的历史分类方案（旧配置残留），避免它们重新出现在列表里
                file_schemes = [s for s in data if isinstance(s, dict) and s.get("name")
                                and s.get("name") not in _LEGACY_SCHEME_NAMES]
        except Exception:
            pass
        merged, seen = [], set()
        for s in list(BUILTIN_COLOR_SCHEMES) + file_schemes:
            nm = s.get("name")
            if nm and nm not in seen:
                item = dict(s)
                # 内置高对比配色：按 variant 填充固定颜色（不随主题变化）
                if item.get("mode") == "categorical" and not item.get("colors"):
                    variant = item.get("variant")
                    if variant == "universal":
                        item["colors"] = Theme.default_high_contrast_colors()
                    elif variant in Theme.HIGH_CONTRAST_CHANNELS:
                        item["colors"] = Theme.high_contrast_colors(variant)
                    else:
                        item["colors"] = Theme.default_high_contrast_colors()
                merged.append(item)
                seen.add(nm)
        # 文件版若带 active 标记，覆盖到同名内置项（供重启恢复勾选兜底）
        for fs in file_schemes:
            if fs.get("active"):
                for s in merged:
                    if s.get("name") == fs.get("name"):
                        s["active"] = True
                        break
        return merged

    def _current_scheme_name(self) -> str:
        """当前激活方案名（恢复勾选用）。

        优先级：color_config.json 的 scheme 字段 → color_schemes.json 的 active 标记
        → 按 color_mode 兜底（thermal → 温度色谱；否则高对比配色 · 统一默认）。
        兼容旧版本：历史分类调色板名（默认 / 暖色系 / 冷色系等）和旧的
        「高对比配色」单行方案名一律归并到「高对比配色 · 统一默认」。
        """
        try:
            d = ConfigIO.load_section("color", {}, ())
            sch = d.get("scheme")
            if sch:
                if sch == "温度色谱（随值渐变）":
                    return sch
                for s in self._load_schemes():
                    if s.get("name") == sch:
                        return sch
                # 旧分类方案名 / 旧「高对比配色」→ 归并到统一默认
                return "高对比配色 · 统一默认"
        except Exception:
            pass
        for s in self._load_schemes():
            if s.get("active"):
                return s.get("name", "")
        if self.color_mode == "thermal":
            return "温度色谱（随值渐变）"
        return BUILTIN_COLOR_SCHEMES[0].get("name", "高对比配色 · 统一默认")

    def _mark_scheme_active(self, name):
        """把当前激活方案标记写回 color_schemes.json，供重启后恢复勾选。"""
        schemes = self._load_schemes()
        for s in schemes:
            s["active"] = (s.get("name") == name)
        try:
            ConfigIO.save_section("color_schemes", schemes)
        except Exception:
            pass

    def _populate_scheme_table(self, dlg=None):
        """填充配色方案表格。dlg 可显式传入（SettingsDialog 构造期间 self.settings_dlg 尚未赋值）。"""
        dlg = dlg or getattr(self, 'settings_dlg', None)
        if dlg is None or not hasattr(dlg, 'tbl_scheme'):
            return
        table = dlg.tbl_scheme
        table.setRowCount(0)
        schemes = self._load_schemes()
        active = self._current_scheme_name()
        for s in schemes:
            row = table.rowCount()
            table.insertRow(row)
            # 列 0：使用统一的自绘方格勾选框，替代原生 checkbox。
            # 先 setChecked 再 connect：填充过程不触发应用逻辑（避免打开设置页即重刷）。
            tgl = ToggleSwitch()
            tgl.setToolTip("启用该配色方案")
            tgl.setChecked(s.get("name") == active)
            tgl.toggled.connect(
                lambda on, name=s.get("name", ""): self._on_scheme_toggled(name, on))
            wrap = QWidget()
            wrap.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            wrap.setStyleSheet(f"background:{Theme.BG_CARD};")
            wl = QHBoxLayout(wrap)
            wl.setContentsMargins(8, 0, 8, 0)
            wl.addWidget(tgl, 0, Qt.AlignCenter)
            table.setCellWidget(row, 0, wrap)
            # 占位 item：保证单元格布局正常
            holder = QTableWidgetItem("")
            holder.setFlags(Qt.ItemIsEnabled)
            table.setItem(row, 0, holder)
            # 当前主题的专属配色行标「（推荐）」：各主题专属色板按其画布
            # 对比度设计（统一默认不保证全部画布达标）；设置弹窗打开中切
            # 主题由 _apply_theme_chain 重填本表保持标记实时
            item_name = QTableWidgetItem(s.get("name", ""))
            if s.get("variant") == Theme.active():
                item_name.setText(s.get("name", "") + "（推荐）")
            item_name.setFlags(Qt.ItemIsEnabled)
            table.setItem(row, 1, item_name)

    def _on_scheme_toggled(self, name, on):
        """配色方案开关：打开即应用（内置/自定义调色板或温度色谱），全局重新分配通道颜色。"""
        if not on:
            return
        dlg = getattr(self, 'settings_dlg', None)
        if dlg is None or not hasattr(dlg, 'tbl_scheme'):
            return
        sch = next((s for s in self._load_schemes() if s.get("name") == name), None)
        if sch is None:
            return
        # 取消其他方案开关（blockSignals 包裹，避免逐个触发 toggled 回调；
        # 跳过当前方案行，保持其开关开启。用 startswith 匹配：激活行文案
        # 可能带「（推荐）」后缀，精确比较会把自己也取消——勾选框显示
        # 关闭而配色已应用，观感即「切了不生效」）
        table = dlg.tbl_scheme
        for r in range(table.rowCount()):
            it_name = table.item(r, 1)
            if it_name is not None and it_name.text().startswith(name):
                continue
            w = table.cellWidget(r, 0)
            tgl = w.findChild(ToggleSwitch) if w is not None else None
            if tgl is not None:
                tgl.blockSignals(True)
                tgl.setChecked(False)
                tgl.blockSignals(False)
        self._apply_scheme(sch)
        self._save_color_config(scheme=name)
        self._mark_scheme_active(name)

    def _apply_scheme(self, sch):
        """按方案对象下发通道配色并重绘（勾选应用与主题自动跟随共用核心）。"""
        mode = sch.get("mode", "categorical")
        self.color_mode = mode
        # 同步到全局 ChannelConfig（channels.json）并重新下发通道颜色。
        # 用 batch(notify=False) 抑制 channels_changed：切配色无需整面板重建，
        # 通道数量不变，只把新颜色刷到既有卡片上（局部刷新，省去重建开销）。
        if store.config is not None:
            colors = sch.get("colors")
            with store.config.batch(notify=False):
                if mode == "thermal":
                    store.config.set_color_mode("thermal")
                else:
                    if colors:
                        store.config.palette = [str(c) for c in colors]
                    store.config.set_color_mode("categorical")
            self._apply_channel_colors_local()
        # 手动重绘图表（一次）。缓存 key 已含配色指纹，曲线颜色随方案更新。
        self.refresh_plots()
        # 通道色变了，对比页勾选文字/胶囊底同步重放
        if getattr(self, "compare_panel", None) is not None:
            self.compare_panel.refresh_text_colors()

    def _sync_scheme_with_theme(self, theme_key):
        """主题切换自动搭配通道配色（用户 2026-08-31 反馈）。

        规则：仅当用户从未手动勾选过配色方案（color 配置无 scheme 字段）
        且非温度色谱模式时，自动切到该主题的专属配色行；一旦手动勾选过
        任何方案（含统一默认），尊重用户选择不再跟随。不写 scheme 字段，
        保持「未手动选择」状态，后续换主题可继续自动搭配。
        """
        try:
            d = ConfigIO.load_section("color", {}, ())
        except Exception:
            return
        if d.get("scheme") or self.color_mode == "thermal":
            return
        sch = next((s for s in self._load_schemes()
                    if s.get("variant") == theme_key), None)
        if sch is None:
            return
        self._apply_scheme(sch)
        self._mark_scheme_active(sch.get("name", ""))
        # 设置弹窗打开中：重建方案表，同步勾选框与「（推荐）」标记
        dlg = getattr(self, 'settings_dlg', None)
        if dlg is not None and hasattr(dlg, 'tbl_scheme'):
            self._populate_scheme_table(dlg)

    def _apply_channel_colors_local(self):
        """把新配色下发到通道对象，并局部刷新卡片色块/温度颜色（不重建面板）。"""
        s = store.active
        if s is None or store.config is None:
            return
        store.config.apply_all(s.channels)
        for idx, card in self.channel_panel.cards.items():
            ch = s.channel_by_index(idx)
            if ch is not None:
                card.set_color_swatch(ch.color)

    def _save_color_config(self, scheme=None):
        payload = {"mode": self.color_mode}
        if scheme:
            payload["scheme"] = scheme
        try:
            ConfigIO.save_section("color", payload)
        except Exception:
            pass

    # ==================================================================
    #  采集配置持久化（波特率、采样间隔、上次端口）
    # ==================================================================
    def _load_acq_config(self):
        """加载上次保存的采集配置（含上次串口号，用于启动后恢复）。"""
        self._last_port = ""   # 记住上次串口号；cmb_port 在 _init_ui 才构建，
        self._acq_protocol = "auto"
                               # 端口恢复放到 _refresh_ports（构建后被调用）执行
        try:
            d = ConfigIO.load_section("acquisition", {}, ())
            if isinstance(d, dict):
                self._acq_baud = int(d.get("baud", 2400))
                self._acq_interval_ms = int(d.get("interval", 1000))
                self._acq_max_groups = int(d.get("max_groups", 0))
                self._acq_protocol = d.get("protocol", "auto")
                if self._acq_protocol not in ("auto", "new", "old"):
                    self._acq_protocol = "auto"
                self._acq_group_switch = int(d.get("group_switch", 0xFF)) & 0xFF
                masks = d.get("channel_masks", [0xFF] * 8)
                if isinstance(masks, list):
                    self._acq_channel_masks = [int(mask) & 0xFF for mask in masks[:8]]
                    self._acq_channel_masks += [0] * (8 - len(self._acq_channel_masks))
                self._last_port = d.get("port", "")
        except Exception:
            pass

    def _save_acq_config(self):
        """保存当前采集配置。"""
        try:
            port = ""
            if hasattr(self, "cmb_port"):
                # 存裸设备名（详细项 UserRole），避免带描述的显示文本污染配置
                port = self._selected_port()
            else:
                # cmb_port 尚未构建（_load_configs 在 _init_ui 之前）：
                # 保留已加载的 _last_port，避免把记忆的串口覆盖成空
                port = getattr(self, "_last_port", "")
            ConfigIO.save_section("acquisition", {
                "baud": self._acq_baud,
                "interval": self._acq_interval_ms,
                "max_groups": self._acq_max_groups,
                "protocol": self._acq_protocol,
                "group_switch": self._acq_group_switch,
                "channel_masks": self._acq_channel_masks,
                "port": port,
            })
        except Exception:
            pass

    # ==================================================================
    #  界面主题（统一主题系统：文字 / 背景色全部由 Theme 一处控制）
    # ==================================================================
    def _load_theme_config(self):
        """启动时恢复上次选择的主题。此时窗口尚未构建（_load_configs 在
        _init_ui 之前），只需激活配色并刷新全局 QSS——各控件在构建时读取
        Theme 颜色，自然跟随所选主题，无需逐个重建。"""
        name = Theme.DEFAULT_THEME
        try:
            d = ConfigIO.load_section("theme", {}, ())
            if isinstance(d, dict):
                name = Theme._resolve_theme_name(d.get("theme"))
        except Exception:
            pass
        if name != Theme.active():
            Theme.activate(name)
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(Theme.qss())
            # 同步 matplotlib 配色（坐标轴文字/刻度/网格颜色在绘制时从 rcParams
            # 解析，若不更新会残留上一主题的深色文字，浅色画布上不可见）
            try:
                import matplotlib.pyplot as plt
                plt.rcParams.update(Theme.mpl_params())
            except Exception:
                pass

    def _save_theme_config(self):
        try:
            ConfigIO.save_section("theme", {"theme": Theme.active()})
        except Exception:
            pass

    # ==================================================================
    #  通道列表视图模式（卡表融合 table ⇄ 经典卡片 classic）
    # ==================================================================
    _CHANNEL_VIEW_MODES = ("table", "classic")

    def _load_channel_view_mode(self):
        """启动时恢复通道列表视图模式（channel_view.mode）。

        在 _load_configs 中调用（先于 _init_ui 构建左面板），只记录值；
        面板创建后由 _build_left_panel 经 set_view_mode 下发。键缺失 /
        非法时回退 "table"（与主题段同款容错）。
        """
        mode = "table"
        try:
            d = ConfigIO.load_section("channel_view", {}, ())
            if isinstance(d, dict) and d.get("mode") in self._CHANNEL_VIEW_MODES:
                mode = d["mode"]
        except Exception:
            pass
        self._channel_view_mode = mode

    def _apply_channel_view_mode(self, mode: str, silent: bool = False) -> None:
        """切换通道列表视图并持久化（设置页即时生效 / 热重载共用）。

        与主题段同款配置方式：写 channel_view 分区 {"mode": mode}。
        模式未变时只写盘（幂等）直接返回，不重灌面板；切换需整面板
        重建（两类卡片结构不同），走 set_view_mode + populate()。
        """
        mode = mode if mode in self._CHANNEL_VIEW_MODES else "table"
        try:
            ConfigIO.save_section("channel_view", {"mode": mode})
        except Exception:
            pass
        if mode == self._channel_view_mode:
            return
        self._channel_view_mode = mode
        self.channel_panel.set_view_mode(mode)
        self.channel_panel.populate()
        if not silent:
            self.statusBar().showMessage(
                "通道列表已切换为经典卡片视图" if mode == "classic"
                else "通道列表已切换为卡表融合视图", 5000)

    def _hot_apply_channel_view(self):
        """channel_view 分区被外部修改时的即时生效入口（对照 _hot_apply_theme）。"""
        mode = "table"
        try:
            d = ConfigIO.load_section("channel_view", {}, ())
            if isinstance(d, dict) and d.get("mode") in self._CHANNEL_VIEW_MODES:
                mode = d["mode"]
        except Exception:
            pass
        # 模式未变时 _apply_channel_view_mode 内部直接 return，不重灌面板
        self._apply_channel_view_mode(mode, silent=True)

    def _load_storage_config(self):
        """加载数据存储配置：写入间隔固定为 1 分钟，不再读取用户配置"""
        # 数据库批量写入间隔固定为 1 分钟，无需用户配置
        self._db_write_interval = 1.0

        # 初始化数据库
        self._init_history_database()

        # 网络服务：应用启动仅初始化服务管理器（不启动任何服务）。
        # 服务在用户点击「开始采集」时按持久化配置自动激活；
        # 未采集时可手动启动/关闭/切换（工具栏「服务」窗口）。
        self._init_service_manager()

        # 同步到数据总线（Recorder 运行时使用），保证配置真正生效
        from device.datastore import store
        store.set_storage_config(self._db_write_interval)

    def _init_history_database(self):
        """初始化历史数据库"""
        try:
            from device.database import HistoryDatabase
            self._history_db = HistoryDatabase()
            # 更新 datastore 的数据库引用
            from device.datastore import store
            if hasattr(store, 'recorder') and store.recorder:
                store.recorder.history_db = self._history_db
            # 启动即执行 30 天保留策略：后台删除过期会话并回收磁盘
            import threading
            threading.Thread(
                target=self._purge_expired_sessions_bg, daemon=True).start()
        except Exception as e:
            print(f"[DB] 初始化数据库失败: {e}", flush=True)
            self._history_db = None
            # 仅数据库存储模式下数据库不可用则禁止开始采集，
            # 避免采集数据无处落盘。
            print("[DB] 数据库不可用：采集将被禁止（数据仅允许保存到数据库）", flush=True)

    def _purge_expired_sessions_bg(self):
        """后台执行保留策略（硬编码保留最近 30 天）。

        保留天数以 history_db.RETENTION_DAYS 为单一事实源；启动早期采集
        尚未开始，无需排除采集中会话。删除与 VACUUM 均在数据库连接锁下
        串行执行，与后续采集写入天然互斥，不阻塞 GUI。
        """
        hdb = self._history_db
        if hdb is None:
            return
        try:
            from device.database import RETENTION_DAYS
            deleted, skipped = hdb.purge_expired_sessions()
            if deleted or skipped:
                print(f"[DB] 保留策略：已清理 {RETENTION_DAYS} 天前会话 "
                      f"{deleted} 个（跳过 {skipped} 个锁定/排除会话），"
                      f"磁盘空间已回收", flush=True)
        except Exception as e:
            print(f"[DB] 保留策略清理失败: {e}", flush=True)

    def _apply_theme(self, name: str, silent: bool = False):
        """运行时切换主题入口：先显示等待反馈，再执行切换链。

        切换全程 GUI 线程同步（QSS 重生成 + 画布重绘 + 卡片重建 + 弹窗
        刷新），数据量大时秒级无反馈；用户触发路径（silent=False）先弹
        「正在切换主题」提示并置等待光标，链中三处 processEvents 让提示
        转动、结束后关闭。silent=True（启动恢复/配置热载）不显示反馈。
        """
        name = Theme._resolve_theme_name(name)
        if name == Theme.active() and not silent:
            return
        # 重入守卫：切换链中的 processEvents 可能派发第二次主题下拉信号，
        # 递归调用在此直接返回，避免嵌套切换
        if getattr(self, "_applying_theme", False):
            return
        self._applying_theme = True
        busy = None
        try:
            if not silent:
                busy = ThemeBusyPopup.show_busy()
                QApplication.setOverrideCursor(Qt.WaitCursor)
                QApplication.processEvents()    # 强制提示先绘制再进入重活
            self._apply_theme_chain(name, silent)
        finally:
            if busy is not None:
                busy.close()
            if not silent:
                QApplication.restoreOverrideCursor()
            self._applying_theme = False

    def _apply_theme_chain(self, name: str, silent: bool = False):
        """主题切换执行链（由 _apply_theme 包装调用）。"""
        old_name = Theme.active()
        old_colors = dict(Theme._PALETTES.get(old_name, {}))
        Theme.activate(name)
        new_colors = dict(Theme._PALETTES.get(name, {}))
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(Theme.qss())
        if not silent:
            QApplication.processEvents()
        # 串口下拉框带本地样式，需单独刷新（箭头颜色跟随新主题）
        self._style_port_combo()
        # 左侧底部温度轴调节区带局部样式（含微调三角），随主题刷新
        if getattr(self, "axis_quick_panel", None) is not None:
            self.axis_quick_panel.refresh_theme()
        try:
            import matplotlib.pyplot as plt
            plt.rcParams.update(Theme.mpl_params())
        except Exception:
            pass
        # 配色方案已拆分为固定颜色（统一默认 / 各主题专属），切换主题不再
        # 改写当前方案颜色；曲线颜色由「配色方案」勾选决定，见 _on_scheme_toggled。
        # 画布（matplotlib Figure/Axes 面颜色在构造时固化，须显式刷新）
        self._apply_plot_theme()
        panel = getattr(self, "status_info_panel", None)
        if panel is not None:
            panel.refresh_theme()
        # 主窗口内联样式标签刷新（数据来源、采集状态）
        self._update_source_label()
        self._refresh_acq_status_style()
        # 状态栏状态色标签重放：网络/IP、实时状态、报警提示均为状态驱动
        # 着色（构造初值固化），切主题后按当前状态用新主题色重放一次
        self._update_net_status_label()
        self._refresh_alarm_label()
        renderer = getattr(self, "chart_renderer", None)
        if renderer is not None and hasattr(renderer, "live_view_status"):
            self._style_live_status_labels(None, renderer.live_view_status())
        # 通道卡片在构造时读取 Theme 颜色 → 重建以跟随新主题
        if getattr(self, "channel_panel", None) is not None:
            self.channel_panel.populate()
            # 命名入口卡片与空状态标签不在 populate 重建范围，
            # 其构造期固化样式须显式重放（卡片渐变 paintEvent 实时取色，
            # 文字 QSS/阴影色不重放会停留旧主题）
            self.channel_panel.refresh_theme()
        if getattr(self, "chart_renderer", None) is not None:
            self.refresh_plots()
        popup = getattr(self, "_live_overview_popup", None)
        if popup is not None:
            popup.apply_theme()
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is not None:
            ball.apply_theme()
        panel = getattr(self, "_live_channel_panel", None)
        if panel is not None:
            panel.apply_theme()
        if not silent:
            QApplication.processEvents()
        # 设置弹窗（若打开）同步刷新卡片 / 导航样式；update() 强制整窗重绘，
        # 让自绘控件（方格勾选框等）以新主题色重画
        dlg = getattr(self, "settings_dlg", None)
        if dlg is not None and dlg.isVisible():
            if hasattr(dlg, "refresh_theme_colors"):
                dlg.refresh_theme_colors(old_colors, new_colors)
            else:
                dlg._apply_style()
                dlg._apply_nav_style()
            if hasattr(dlg, "cmb_theme"):
                dlg.cmb_theme.blockSignals(True)
                idx = dlg.cmb_theme.findData(Theme.active())
                dlg.cmb_theme.setCurrentIndex(max(idx, 0))
                dlg.cmb_theme.blockSignals(False)
            # 重填配色方案表：经典主题的「（推荐）」标记跟随当前主题实时刷新
            if hasattr(dlg, "tbl_scheme"):
                self._populate_scheme_table(dlg)
            dlg.update()
        # 「数据」弹窗（实例复用、关闭仅隐藏）：本机会话树/月份列表、
        # 远程页与服务页局部样式在构造时固化；无条件刷新（不看
        # isVisible），重开隐藏中的弹窗样式也正确
        hist_dlg = getattr(self, "_history_dialog", None)
        if hist_dlg is not None and hasattr(hist_dlg, "refresh_theme"):
            hist_dlg.refresh_theme()
        # 通道命名弹窗（实例复用、关闭仅隐藏）：整窗 QSS 构造期 format
        # 固化且优先于全局 QSS，须显式重设
        naming_dlg = getattr(self, "_naming_dialog", None)
        if naming_dlg is not None and hasattr(naming_dlg, "refresh_theme"):
            naming_dlg.refresh_theme()
        # 报警弹窗（实例复用、关闭仅隐藏）：标题/提示/复位按钮局部样式
        # 构造期固化，重放防隐藏时切主题后重开停留旧配色
        alarm_dlg = getattr(self, "_alarm_dialog", None)
        if alarm_dlg is not None and hasattr(alarm_dlg, "refresh_theme"):
            alarm_dlg.refresh_theme()
        # 数据编辑弹窗（实例复用、关闭仅隐藏）：表格/按钮局部样式构造期
        # 固化，存活实例随主题链刷新（refresh_theme 由 T-C5 并行实现）
        if getattr(self, "_data_edit_dialog", None) is not None:
            self._data_edit_dialog.refresh_theme()
        # 主题切换自动搭配通道配色（未手动勾选过方案时跟随主题专属配色）
        self._sync_scheme_with_theme(name)
        # 通道对比勾选文字/胶囊底在构造期按旧主题卡底固化，
        # 深色→浅色切换后亮字残留白底（对比度 ~1.2:1），按新主题重放
        if getattr(self, "compare_panel", None) is not None:
            self.compare_panel.refresh_text_colors()
        self._save_theme_config()
        if not silent:
            self.statusBar().showMessage(f"已切换主题：{Theme.THEME_NAMES[name]}", 2000)

    def _apply_plot_theme(self):
        """主题切换后刷新 matplotlib 画布配色：Figure / 各 Axes 面颜色。

        PlotTab 的 Figure 在构造时用当时的 Theme.PLOT_FACE 固化 facecolor，
        仅更新 rcParams 不会改变已存在的 Figure，必须逐个显式重设。
        """
        for tab in getattr(self, "_plot_tabs", []):
            try:
                # 画布组两分区：Figure 底面归 PLOT_FACE，不借用骨架色 BG_CARD
                tab.fig.set_facecolor(Theme.PLOT_FACE)
                for ax in tab.fig.axes:
                    ax.set_facecolor(Theme.PLOT_FACE)
                    # 刻度线轻量化（BORDER 灰），刻度文字保持高对比纯黑
                    ax.tick_params(color=Theme.BORDER,
                                   labelcolor=Theme.PLOT_TEXT)
                    ax.xaxis.label.set_color(Theme.PLOT_TEXT)
                    ax.yaxis.label.set_color(Theme.PLOT_TEXT)
                    # 三个 title 都要刷新：loc="left" 的标题在 _left_title 上，
                    # 只改 ax.title 时左上角标题颜色/字体停留在旧值
                    # （与 export._apply_export_palette 的遍历写法一致）
                    for ttl in (ax.title, ax._left_title, ax._right_title):
                        ttl.set_color(Theme.PLOT_TEXT)
                        ttl.set_fontfamily(Theme.font("plot"))
                        ttl.set_fontweight(Theme.PLOT_TITLE_WEIGHT)
                    ax.xaxis.label.set_fontfamily(Theme.font("plot"))
                    ax.yaxis.label.set_fontfamily(Theme.font("plot"))
                    for tick in ax.get_xticklabels() + ax.get_yticklabels():
                        tick.set_fontfamily(Theme.font("mono"))
                    for spine in ax.spines.values():
                        spine.set_color(Theme.BORDER)
                    for grid_line in ax.get_xgridlines() + ax.get_ygridlines():
                        grid_line.set_color(Theme.GRID)
                    legend = ax.get_legend()
                    if legend is not None:
                        for text in legend.get_texts():
                            text.set_color(Theme.PLOT_TEXT)
                            text.set_fontfamily(Theme.font("plot"))
                    for child in ax.figure.axes:
                        if child is ax:
                            continue
                        child.tick_params(colors=Theme.PLOT_TEXT)
                        child.xaxis.label.set_color(Theme.PLOT_TEXT)
                        child.yaxis.label.set_color(Theme.PLOT_TEXT)
                tab.canvas.draw_idle()
            except Exception:
                pass
        stat_panel = getattr(self, "stat_panel", None)
        if stat_panel is not None:
            try:
                stat_panel.apply_plot_theme()
            except Exception:
                pass

    # ==================================================================
    #  核心处理参数持久化（重采样/异常剔除/平滑等设置项）
    # ==================================================================
    def _load_param_config(self):
        """加载上次保存的核心处理参数。"""
        self._saved_params = {}
        try:
            d = ConfigIO.load_section("parameters", {}, ())
            if isinstance(d, dict) and "interval" in d:
                self._saved_params = d
        except Exception:
            pass

    def _save_param_config(self):
        """保存当前核心处理参数（设置页可见时读页面，否则读内存快照）。"""
        dlg = getattr(self, "settings_dlg", None)
        try:
            if (dlg is not None and hasattr(dlg, "_param_state")
                    and dlg.isVisible()):
                self._saved_params = dlg._param_state()
            if isinstance(self._saved_params, dict) and self._saved_params:
                ConfigIO.save_section("parameters", self._saved_params)
        except Exception:
            pass

    # ==================================================================
    #  辅助
    # ==================================================================
    def _position_compare_panel(self):
        """定位单通道对比面板（占位，无操作）"""
        pass

    def _refresh_single_combo(self):
        """刷新单通道对比勾选框"""
        self.chart_renderer._refresh_single_combo()

    def _checked_compare_names(self):
        """获取勾选的通道名（供 ChartRenderer 调用）"""
        return self.compare_panel.checked_names()

    # ── ChartRenderer / ChannelPanel 委托（供 ExportManager 调用）──
    def _draw_lines(self, *a, **kw):
        return self.chart_renderer._draw_lines(*a, **kw)

    def _visible_series(self, *a, **kw):
        return self.channel_panel.visible_series(*a, **kw)

    def _enforce_fixed_margins(self, *a, **kw):
        return self.chart_renderer._enforce_fixed_margins(*a, **kw)

    def _draw_combo_frame(self, *a, **kw):
        return self.chart_renderer._draw_combo_frame(*a, **kw)

    def _draw_segment(self, *a, **kw):
        return self.chart_renderer._draw_segment(*a, **kw)

    def _visible_series_window(self, *a, **kw):
        return self.channel_panel.visible_series_window(*a, **kw)

    def _color_of(self, *a, **kw):
        return self.channel_panel.color_of(*a, **kw)

    def _apply_default_geometry(self):
        """启动几何：以 DEFAULT_GEOMETRY（=最小尺寸）在屏幕居中显示。

        不读写历史保存尺寸——每次打开都是固定居中的最小尺寸，
        最大化/还原按钮负责在「常规尺寸 ↔ 最大化」两态间切换。
        """
        self.resize(*self.DEFAULT_GEOMETRY)
        self._center_window(*self.DEFAULT_GEOMETRY)

    def _center_window(self, w: int, h: int):
        """将 (w, h) 尺寸的窗口居中；多屏时优先窗口当前所在屏。"""
        try:
            handle = self.windowHandle()
            screen = handle.screen() if handle is not None else None
            if screen is None:
                screen = QApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                x = (avail.width() - w) // 2 + avail.x()
                y = (avail.height() - h) // 2 + avail.y()
                # 小屏保护：窗口大于屏幕时居中结果为负，钳回可用区左上角
                x = max(avail.x(), x)
                y = max(avail.y(), y)
                self.move(x, y)
        except Exception:
            pass

    def changeEvent(self, event) -> None:
        """最大化/还原只在两种尺寸间切换：最大化 ↔ DEFAULT_GEOMETRY 居中。

        从最大化还原（点标题栏还原按钮）时，无论此前是否手动拖大过
        常规窗口，一律回落到 DEFAULT_GEOMETRY 并居中；最小化路径不干预
        （任务栏恢复会带原最大化状态回来）。
        """
        super().changeEvent(event)
        if event.type() != QEvent.Type.WindowStateChange:
            return
        state = self.windowState()
        now_maximized = bool(state & Qt.WindowMaximized)
        was_maximized = self._was_maximized
        self._was_maximized = now_maximized
        if (was_maximized and not now_maximized
                and not (state & Qt.WindowMinimized)):
            self._apply_default_geometry()

    def resizeEvent(self, event) -> None:
        """窗口尺寸变化：同步导出抽屉几何 + 悬浮球/全通道面板安全区校正。"""
        super().resizeEvent(event)
        self._sync_export_drawer_geometry()
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is not None:
            ball.on_host_geometry_changed()
        panel = getattr(self, "_live_channel_panel", None)
        if panel is not None:
            panel.on_host_geometry_changed()

    def moveEvent(self, event) -> None:
        """窗口移动：悬浮球/全通道面板安全区兜底自校正（球已桌面化，通常无操作）。"""
        super().moveEvent(event)
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is not None:
            ball.on_host_geometry_changed()
        panel = getattr(self, "_live_channel_panel", None)
        if panel is not None:
            panel.on_host_geometry_changed()

    def showEvent(self, event) -> None:
        """窗口显示事件，更新托盘状态。"""
        super().showEvent(event)
        if self._tray_icon and self._tray_icon.isVisible():
            self._tray_icon.setToolTip("多通道温度分析仪")
    
    # ==================================================================
    #  退出与最小化到托盘（改动 10）
    # ==================================================================
    def _confirm_exit_and_close(self):
        """退出确认（Ctrl+Q / 托盘不可用时点 X；工具栏已无退出按钮）。

        确认 → 播打叉淡出退出动效，结束后置 _force_exit 标志 close()，
        复用现有真关闭链路（停采集 → closeEvent → aboutToQuit 收尾）；
        取消不动作。

        弹确认框前先让悬浮监控让位（suspend_for_modal）：悬浮窗/球是置顶窗，
        可能正好盖住居中弹出的确认框导致点不到无法退出；取消时按原状态恢复，
        确认退出则清除让位状态直接走关闭链路。
        """
        popup = getattr(self, "_live_overview_popup", None)
        if popup is not None:
            popup.suspend_for_modal()
        panel = getattr(self, "_live_channel_panel", None)
        if panel is not None:
            panel.suspend_for_modal()
        acq_note = ""
        if self._acq_state in ("acquiring", "paused"):
            acq_note = "\n正在采集中，退出将结束采集并保存数据。"
        ret = QMessageBox.No
        try:
            ret = QMessageBox.question(
                self, "确认退出", f"确认退出程序？{acq_note}",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        finally:
            if popup is not None:
                if ret == QMessageBox.Yes:
                    popup.cancel_suspend()   # 即将真退出，不再恢复显示
                else:
                    popup.restore_after_modal()
            if panel is not None:
                if ret == QMessageBox.Yes:
                    panel.cancel_suspend()
                else:
                    panel.restore_after_modal()
        if ret != QMessageBox.Yes:
            # 取消退出：模态让位恢复可能带着旧可见性记录（让位期间采集
            # 可能已因拔线自动结束），按当前采集态重申悬浮球族显隐规则。
            self._sync_live_ball_visibility()
            return
        self._exit_with_close_anim()

    def _tray_icon_target_rect(self):
        """托盘图标处的小矩形（引导光点飞行终点）；取不到几何时回退屏幕右下角。"""
        size = 48
        tray_geo = self._tray_icon.geometry() if self._tray_icon else QRect()
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        if tray_geo.isValid() and tray_geo.width() > 0:
            center = tray_geo.center()
        else:
            center = QPoint(avail.right() - size, avail.bottom() - size)
        return QRect(max(avail.left(), center.x() - size // 2),
                     max(avail.top(), center.y() - size // 2), size, size)

    def _minimize_to_tray_animated(self):
        """最小化到托盘：窗口原位淡出 + 光点飞向托盘引导，结束后隐藏。

        与旧版「几何收缩飞向托盘」不同：主窗口全程保持原位不动（不改
        位置、尺寸与窗口状态），仅淡出；同时一枚光点从窗口中心飞向托盘
        图标位置，配合结束后的托盘气泡给出「去哪儿了」的双重指引。
        """
        if (getattr(self, "_tray_anim_active", False)
                or getattr(self, "_close_anim_active", False)):
            return  # 守卫：任一动效播放中防快速重复触发
        self._tray_anim_active = True
        group = QParallelAnimationGroup(self)
        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(650)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InQuad)
        group.addAnimation(fade)
        group.finished.connect(self._on_tray_anim_finished)
        self._tray_anim = group
        self._tray_guide = _TrayGuideOverlay(
            self.geometry().center(),
            self._tray_icon_target_rect().center(),
            group.duration())
        group.start()
        self._tray_guide.start()

    def _on_tray_anim_finished(self):
        """引导动画结束：原位隐藏 + 复原不透明度 + 托盘气泡提示。

        窗口几何与状态全程未动，无需复原；不透明度回 1.0，托盘恢复时
        直接完整显示；引导光点层在此一并关闭（其自身动画结束也会自毁）。
        """
        self.hide()
        self.setWindowOpacity(1.0)
        self._tray_anim_active = False
        guide = self._tray_guide
        self._tray_guide = None
        if guide is not None:
            guide.close()
        if self._tray_icon is not None:
            self._tray_icon.showMessage(
                "多通道温度分析仪",
                "已最小化到系统托盘，双击图标恢复。",
                QSystemTrayIcon.Information,
                3000
            )

    def _exit_with_close_anim(self):
        """退出动效：窗口原位淡出 + 窗口区域打叉，结束后置 _force_exit 真关闭。

        「最小化还是退出」询问框与退出确认框共用的收尾路径；动效只做
        视觉反馈，不改窗口几何与窗口状态，结束后按既有真关闭链路退出。
        """
        if (getattr(self, "_close_anim_active", False)
                or getattr(self, "_tray_anim_active", False)):
            return  # 守卫：任一动效播放中防快速重复触发
        self._close_anim_active = True
        group = QParallelAnimationGroup(self)
        fade = QPropertyAnimation(self, b"windowOpacity")
        fade.setDuration(420)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.InQuad)
        group.addAnimation(fade)
        group.finished.connect(self._on_close_anim_finished)
        self._close_anim = group
        self._close_cross = _CloseCrossOverlay(self.geometry(), 420)
        group.start()
        self._close_cross.start()

    def _on_close_anim_finished(self):
        """退出动效结束：关闭打叉层、复原不透明度后走既有真关闭链路。"""
        self._close_anim_active = False
        cross = self._close_cross
        self._close_cross = None
        if cross is not None:
            cross.close()
        self.setWindowOpacity(1.0)
        self._force_exit = True
        self.close()

    def _close_for_real(self, event) -> None:
        """真关闭：先关闭悬浮窗、悬浮球与全通道面板、停采集，再交给父类处理。"""
        popup = getattr(self, "_live_overview_popup", None)
        if popup is not None:
            popup.close_popup()
        ball = getattr(self, "_live_monitor_ball", None)
        if ball is not None:
            ball.hide()
        panel = getattr(self, "_live_channel_panel", None)
        if panel is not None:
            panel.hide_panel()
        if self._acq_state in ("acquiring", "paused"):
            self._stop_acquisition(notify_done=False)
        super().closeEvent(event)

    def closeEvent(self, event) -> None:
        """关闭请求路由：真退出（_force_exit）走关闭链路；否则询问最小化或退出。

        点 X（无 _force_exit）：托盘可用 → 弹「最小化还是退出」询问框，
        选最小化则原位淡出 + 光点飞向托盘引导后隐藏（采集中后台继续录
        制，比静默自动停止更安全，不误停不丢数据），选退出打叉淡出动效
        后走真关闭链路；托盘不可用 →
        弹退出确认回退（不静默退出、不卡窗口）。窗口不可见时不弹询问
        （用户看不到模态框，无确认意义），直接走真关闭。

        询问框必须经 singleShot(0) 延后到本次 closeEvent 返回之后再弹：
        Qt 对 closeEvent 处理栈内重入的 close() 不再派发第二次 QCloseEvent
        （实测重入 close() 返回 True 但窗口保持可见），询问框「退出」分支
        的 self.close() 会静默失效，表现为点退出无响应、需再点一次 X 才能
        退出。
        """
        if getattr(self, "_force_exit", False):
            self._close_for_real(event)
            return

        if self._tray_enabled and self._tray_icon and self._tray_icon.isVisible():
            event.ignore()
            QTimer.singleShot(0, self._ask_minimize_or_exit)
        elif self.isVisible():
            # 托盘不可用/未启用：不静默退出，弹确认（确认后置 _force_exit 再次 close）
            event.ignore()
            QTimer.singleShot(0, self._confirm_exit_and_close)
        else:
            # 窗口不可见（已隐藏/自动化环境）：无确认意义，直接真关闭
            self._close_for_real(event)

    def _build_close_choice_box(self):
        """构建「最小化还是退出」询问框，返回 (box, btn_min, btn_exit)。

        取消按钮由 QMessageBox 的 RejectRole 承担（Esc / × / 取消同义），
        采集中附注两条路径对采集数据的差异，避免误选丢录制的担忧。
        """
        box = QMessageBox(self)
        box.setWindowTitle("关闭窗口")
        acq_note = ""
        if self._acq_state in ("acquiring", "paused"):
            acq_note = ("\n\n正在采集中：最小化将在后台继续采集录制；"
                        "退出将结束采集并保存数据。")
        box.setText(f"最小化到系统托盘还是退出程序？{acq_note}")
        btn_min = box.addButton("最小化", QMessageBox.ActionRole)
        btn_exit = box.addButton("退出程序", QMessageBox.DestructiveRole)
        btn_cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(btn_cancel)
        box.setEscapeButton(btn_cancel)
        return box, btn_min, btn_exit

    def _ask_minimize_or_exit(self):
        """点 X（托盘可用）：询问最小化到托盘还是退出程序。

        最小化 → 原位淡出 + 光点飞向托盘引导后隐藏，悬浮球族保持桌面
        常驻（监控采集）；退出 → 打叉淡出动效结束后复用真关闭链路；
        取消 → 不动作。
        弹询问框前让悬浮监控族让位（置顶窗可能盖住询问框），按选择恢复。
        """
        popup = getattr(self, "_live_overview_popup", None)
        if popup is not None:
            popup.suspend_for_modal()
        panel = getattr(self, "_live_channel_panel", None)
        if panel is not None:
            panel.suspend_for_modal()
        box, btn_min, btn_exit = self._build_close_choice_box()
        clicked = None
        try:
            box.exec_()
            clicked = box.clickedButton()
            if clicked is btn_exit:
                if popup is not None:
                    popup.cancel_suspend()   # 即将真退出，不再恢复显示
                if panel is not None:
                    panel.cancel_suspend()
                self._exit_with_close_anim()
                return
            if clicked is btn_min:
                self._minimize_to_tray_animated()
        finally:
            # 按选择收尾让位（restore 幂等）：取消 → 恢复悬浮族显示；
            # 最小化 → 恢复后随主窗常驻桌面继续监控；退出 → cancel_suspend
            # 已清除让位记录，restore 不复活。最后按采集态重申显隐规则。
            if popup is not None:
                popup.restore_after_modal()
            if panel is not None:
                panel.restore_after_modal()
            self._sync_live_ball_visibility()

    def _on_about_to_quit(self):
        """应用退出前收尾：关闭悬浮窗、结束采集、关闭数据库。"""
        try:
            popup = getattr(self, "_live_overview_popup", None)
            if popup is not None and popup.isVisible():
                popup.close_popup()
            ball = getattr(self, "_live_monitor_ball", None)
            if ball is not None:
                ball.hide()
            panel = getattr(self, "_live_channel_panel", None)
            if panel is not None:
                panel.hide_panel()
        except Exception as e:
            print(f"[UI] 退出时关闭监控悬浮窗失败: {e}", flush=True)
        try:
            if self._acq_state in ("acquiring", "paused"):
                self._stop_acquisition(notify_done=False)
            # C4：给 stop() 超时未退出的滞留线程最后一次有界等待，
            # 避免退出时销毁仍运行的 QThread（通常 0 个，握手卡顿窗口最多 1 个）
            for w in list(self._acq_workers_exiting):
                w.wait(3000)
            # C1：连接中关窗口时，等待后台握手 worker 有界退出
            hw = getattr(self, "_acq_handshake_worker", None)
            if hw is not None and hw.isRunning():
                hw.wait(3000)
            # C1：远程拉取中关窗口时，等待后台拉取 worker 有界退出
            rw = getattr(self, "_remote_worker", None)
            if rw is not None and rw.isRunning():
                rw.wait(3000)
            # 远程监控退出前停止并等待落库完成（stop_and_wait 泵事件循环，
            # 最长 8+3 秒）：显示保存反馈，避免退出像无响应
            self._busy_card.begin("正在保存监控数据，请稍候…")
            try:
                mon = getattr(self, "_remote_monitor", None)
                if mon is not None:
                    mon.stop_and_wait(8.0)
                for old in list(getattr(self, "_retired_monitors", [])
                                or []):
                    old.stop_and_wait(3.0)
            except Exception as e:
                print(f"[REMOTE] 退出时停止远程监控失败: {e}", flush=True)
            finally:
                self._busy_card.finish()
        except Exception as e:
            print(f"[ACQ] 退出时结束采集失败: {e}", flush=True)
        try:
            store.shutdown()
        except Exception as e:
            print(f"[ACQ] 退出时关闭数据总线失败: {e}", flush=True)
        # 服务常驻：应用退出前统一停止，避免端口残留
        try:
            self._stop_data_server()
        except Exception as e:
            print(f"[SERVER] 退出时停止服务失败: {e}", flush=True)
        # 关闭「数据」弹窗（释放服务页定时器、本机库连接与远程页线程）
        try:
            if self._history_dialog is not None:
                self._history_dialog.close()
        except Exception as e:
            print(f"[NET] 退出时关闭数据弹窗失败: {e}", flush=True)
