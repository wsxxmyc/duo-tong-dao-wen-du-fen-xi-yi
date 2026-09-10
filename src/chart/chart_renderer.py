# -*- coding: utf-8 -*-
"""
ChartRenderer — 所有绘图逻辑的封装类。
从 app.py 提取 MainWindow 的绘图方法，通过 self.mw 引用 MainWindow。
"""
import os
os.environ["QT_API"] = "pyqt5"
import matplotlib
matplotlib.use("Qt5Agg")

import math
import time
import traceback

import numpy as np
from PyQt5.QtCore import QTimer
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable
from matplotlib.ticker import FixedLocator, FuncFormatter, MultipleLocator

from utils.helpers import _nice_step, _fmt_range
from utils.config_io import LIVE_WINDOW_SEC_DEFAULT
from chart.series_sampler import SeriesSampler
from device.datastore import store
from ui.theme import Theme

# THERMAL_CMAP — 温度色谱 colormap，与 app.py 顶层定义一致
from matplotlib.colors import LinearSegmentedColormap
THERMAL_CMAP = LinearSegmentedColormap.from_list(
    "温度色谱",
    ["#eaf6fb", "#bcd9ec", "#7fb3d5", "#f4d35e", "#ee9b3f",
     "#d9534f", "#7b1e1e"],
    N=256)


class ChartRenderer:
    """所有 matplotlib 绘图逻辑：趋势/统计/组合/对比各视图渲染与交互。"""
    # ---- 各视图固定轴标签边距（像素）—— 防止大数据量下标签被裁切 ----
    # One global pixel baseline for all ordinary trend views. Qt containers
    # contribute no inset; these are the only margins around the axes.
    FIXED_MARGIN_LEFT   = 64
    FIXED_MARGIN_RIGHT  = 18
    FIXED_MARGIN_TOP    = 44
    FIXED_MARGIN_BOTTOM = 46

    # ---- 组合图布局常量 ----
    COMBO_MARGIN_LEFT   = 70
    COMBO_MARGIN_RIGHT  = 10
    COMBO_MARGIN_TOP    = 65
    COMBO_MARGIN_BOTTOM = 55
    COMBO_GAP_H         = 55
    # CJK 最坏情况基线：上排 x 刻度/轴标签向下延伸 + 下排标题向上延伸 ≈66px @100dpi
    COMBO_GAP_V         = 70
    COMBO_CELL_MIN_W    = 100
    COMBO_CELL_MIN_H    = 100

    # 组合图字体档：前端（_plot_combo）与 A4 导出共用，保证导出 == 前端所见
    COMBO_FONTS = {"title": 12, "label": 9, "tick": 8, "legend": 7}

    LAYOUT_KEYS = ["margin_left", "margin_right", "margin_top",
                   "margin_bottom", "gap_h", "gap_v", "cbar_reserve"]

    # ---- 温度轴智能判断模式：基础窗口 + 越界扩展规则 ----
    AUTO_TEMP_BASE_LO = 20.0    # 基础窗口下限（℃），数据在界内时轴固定显示
    AUTO_TEMP_BASE_HI = 40.0    # 基础窗口上限（℃）
    AUTO_TEMP_LO_FACTOR = 0.90  # 越界扩展：最低温度 × 0.90（向下调 10%）
    AUTO_TEMP_HI_FACTOR = 1.30  # 越界扩展：最高温度 × 1.30（向上调 30%）
    AUTO_TEMP_HYSTERESIS = 1.0  # 滞回阈值（℃）：越过 20-H / 40+H 才触发扩展
    AUTO_TEMP_BASE_FRAMES = 3   # 连续 N 次刷新完全回到界内，才缩回基础窗口
    AUTO_TEMP_STEP1_MAX_SPAN = 30.0  # 轴跨度 ≤ 该值时主刻度用 1℃ 递增，更大则自动 nice 步长
    DEFAULT_LIVE_MAX_POINTS = 2400   # 曲线降采样上限（C7）：过大→hover/重绘卡；过小→细节丢失
    # hover 最近点显示节流窗（秒）：鼠标移动事件洪水时每窗最多刷新一次；
    # 提为类常量 + 实例属性，测试可显式置零以隔离时序抖动
    HOVER_THROTTLE_SECONDS = 0.03
    CLICK_TOLERANCE_PX = 5.0
    CLICK_HIT_TOLERANCE_PX = 12.0

    @staticmethod
    def line_style(name, hovered_name=None) -> dict:
        """返回曲线悬停状态对应的线宽、透明度和层级。"""
        if hovered_name is None:
            return {"linewidth": 1.4, "alpha": 1.0, "zorder": 2}
        if name == hovered_name:
            return {"linewidth": 2.8, "alpha": 1.0, "zorder": 3}
        return {"linewidth": 1.4, "alpha": 0.28, "zorder": 2}

    def __init__(self, mw):
        self.mw = mw  # MainWindow 引用
        # 实时增量绘制缓存：id(tab) -> {"key": (通道名, ...), "artists": [(name, artist), ...]}
        # 实时采集模式下复用已创建的 Line2D，用 set_data 更新，避免全量重建卡顿
        self._live_inc = {}
        # 报警高亮：处于 active 报警的通道名集合（categorical 模式曲线变红）
        self._alarm_channels = set()
        # 报警阈值虚线缓存：{(id(tab), pane): {"high":artist, "high_val":...}}
        # pane ∈ "single" / "dual_left" / "dual_right"（双区视图两轴各一条）
        self._alarm_lines = {}
        # 整体趋势图降采样后的 series 缓存，供鼠标悬停命中（画面实际显示的点）；
        # 键 = (id(tab), pane)，双区视图左/右轴各自缓存所在 pane 的序列
        self._overview_hover_series = {}
        # 双区视图（整体趋势页实时会话自动切换）：左=全历史 + 右=最近实时窗。
        # 默认 True（批 6 恢复）：旧断言已按双区语义同批修订（见
        # test_live_view_interaction 各用例 diff 注释）；关闭开关
        # （set_dual_view_config(False, W) / settings.json dual_view_enabled）
        # 仍可回退单图路径。
        self.dual_view_enabled = True
        self.live_window_sec = float(LIVE_WINDOW_SEC_DEFAULT)
        # 上次渲染所见布局记录：id(tab) -> tab.layout_mode。
        # 布局切换时 PlotTab.clear() 会重建坐标轴，旧 artist / 阈值线 /
        # 悬停缓存若不清空会被增量路径误复用（见 _ensure_tab_layout）。
        self._tab_layout = {}
        self._hover_throttle_ts = 0.0
        self._hover_throttle_sec = self.HOVER_THROTTLE_SECONDS
        # 整体趋势图悬浮交互图元，按 PlotTab 保存，避免重绘时串状态。
        self._hover_state = {}
        self.live_view_mode = "auto"   # auto=全局趋势跟随(0~最新)；manual=手动浏览
        self.live_max_points = self.DEFAULT_LIVE_MAX_POINTS
        self._manual_xlim = None
        self._drag_state = None
        self._drag_dual = False
        self._box_zoom = None
        # 双区视图左区浏览窗 (start, end) 分钟；None=左区跟随 [0, split]。
        # 左区滚轮/拖动/框选只改该窗口（右区始终实时跟随），不进入 manual
        # 模式；整体趋势页布局切换 / 回到最新 / 超时自动回归时清空。
        self._dual_left_xlim = None
        # 双区右区暂停跟随（批 5，D4/D9）：右轴滚轮/左键拖动/右键框选任一
        # 发生后左右两区整体冻结在暂停时刻（新采样不改变画面），状态栏提示
        # 「已暂停跟随」；「回到最新」或 2 分钟无操作自动恢复实时跟随。
        # _dual_hold_split/_dual_hold_t_end 为暂停时刻的切分点/最新点（分钟）。
        self._dual_paused = False
        self._dual_hold_split = None
        self._dual_hold_t_end = None
        # 右轴待触发按键（1=左键拖动 3=右键框选）：press 记录，move/释放时
        # 触发暂停；None=无待触发交互（不占用 _drag_state，单图语义不变）
        self._dual_right_press = None
        self._view_mode_callback = None

        # 组合图布局状态：完整重绘（_plot_combo）递增布局代次使边距缓存失效；
        # 边距缓存（配置 × DPI + 文字实测）在 resize / draw 间复用，
        # 保证边距不随窗口大小改变、无测量-布局反馈振荡。
        self._combo_layout_gen = 0
        self._combo_margin_cache = None
        self._combo_margin_key = None
        self._combo_margin_measured = False
        self._combo_relayouting = False

        # 手动浏览超时自动回到最新：实时采集下手动拖动/缩放后，
        # 2 分钟无任何拖动操作自动复位到全局趋势（0～最新）。
        self._auto_return_timer = QTimer()
        self._auto_return_timer.setSingleShot(True)
        self._auto_return_timer.setInterval(2 * 60 * 1000)
        self._auto_return_timer.timeout.connect(self._auto_return_to_latest)

        # 温度轴智能判断模式的自适应状态：仅启动时初始化一次，
        # 不随新会话 / 新文件 / 通道显隐变化重置（见 _auto_temp_axis）。
        self._auto_temp_state = "base"   # "base"（基础窗口）/ "expanded"（越界已扩展）
        self._auto_temp_range = (
            float(getattr(mw, "ax_temp_base_lo", self.AUTO_TEMP_BASE_LO)),
            float(getattr(mw, "ax_temp_base_hi", self.AUTO_TEMP_BASE_HI)))
        self._auto_temp_in_base_frames = 0  # 已连续完全回界的刷新次数

    def set_alarm_channels(self, names):
        """更新处于 active 报警的通道名集合，供曲线高亮读取。

        names 为可迭代的通道显示名；调用后需配合 refresh_plots 才能反映到画面。
        thermal 模式首版不支持报警高亮（保持温度映射色）。
        """
        self._alarm_channels = set(names or ())

    def set_dual_view_config(self, enabled, window_sec):
        """更新双区视图配置（开关 + 右区实时窗宽度，秒）。

        window_sec 必须为正的有限数，否则抛 ValueError（持久化接线批次
        传入配置值前应自行保证合法）。任一项变化都会使左右窗切分点与
        接缝位置改变，因此清空增量 artist / 报警阈值 / 悬停序列 / 布局
        记录等全部相关缓存后刷新可见页，防止旧 artist 被增量路径复用。
        """
        try:
            w = float(window_sec)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"实时窗口秒数无效: {window_sec!r}")
        if not np.isfinite(w) or w <= 0:
            raise ValueError(f"实时窗口秒数必须为正数: {window_sec!r}")
        self.dual_view_enabled = bool(enabled)
        self.live_window_sec = w
        self._live_inc.clear()
        self._alarm_lines.clear()
        self._overview_hover_series.clear()
        self._tab_layout.clear()
        self._dual_left_xlim = None  # 窗口宽度变化，旧左区浏览窗失去参照
        # 配置变化使暂停冻结点失去参照，恢复实时跟随（右轴待触发交互一并清）
        self._dual_paused = False
        self._dual_hold_split = None
        self._dual_hold_t_end = None
        self._dual_right_press = None
        self.refresh_plots()

    def reset_dual_view_state(self):
        """清空双区视图交互状态：暂停冻结点 + 左区浏览窗（不重渲染）。

        会话切换（新文件导入 / 开始采集 / 切换活跃会话）时由主窗口
        `_on_active_changed` 调用：暂停冻结 split/t_end 与浏览窗都是对旧
        会话时间轴的引用，跨会话残留会让新会话错误冻结在旧切分点或浏览
        不存在的区段。只清状态，缓存清理与重绘由调用方决定（与
        set_dual_view_config 的「清缓存＋刷新」重路径解耦，切换会话时
        本就有全量重算）。
        """
        self._dual_paused = False
        self._dual_hold_split = None
        self._dual_hold_t_end = None
        self._dual_left_xlim = None

    def _draw_alarm_thresholds(self, tab, pane="single"):
        """在趋势图上画报警上限虚线（报警启用时）。

        下限已于 2026-08-22 移除（业务决策：报警只保留上限）。
        全量重建后缓存失效（由 _plot_multi 在 tab.clear() 后 pop 缓存）；
        增量路径下检测阈值是否变化，未变则跳过、变化则更新。
        报警关闭时移除已有虚线。dual 双区视图对左右两轴各画一条，
        缓存按 (id(tab), pane) 分键。
        """
        ax = self._pane_axis(tab, pane)
        cfg = getattr(self.mw, "alarm_config", None)
        cache = self._alarm_lines.get((id(tab), pane))
        if cfg is None or not cfg.enabled:
            if cache:
                for ln in (cache.get("high"),):
                    try:
                        if ln is not None and getattr(ln, "axes", None) is ax:
                            ln.remove()
                    except Exception as e:
                        print(f"[CHART] 报警线清理异常: {e}", flush=True)
                self._alarm_lines.pop((id(tab), pane), None)
            return
        hi = cfg.temp_high
        # 阈值未变且 artist 仍存活 → 跳过
        if (cache and cache.get("high_val") == hi
                and cache.get("high") is not None
                and getattr(cache["high"], "axes", None) is ax):
            return
        if cache:
            for ln in (cache.get("high"),):
                try:
                    if ln is not None and getattr(ln, "axes", None) is ax:
                        ln.remove()
                except Exception as e:
                    print(f"[CHART] 绘制线清理异常: {e}", flush=True)
        hline = ax.axhline(hi, color=Theme.plot_alarm_color(), linestyle="--",
                           linewidth=1.0, alpha=0.55, zorder=1,
                           gid="alarm_bound")
        self._alarm_lines[(id(tab), pane)] = {"high": hline, "high_val": hi}

    def _pane_axis(self, tab, pane):
        """按 pane 角色返回对应坐标轴（single/dual_left=左主轴，dual_right=右辅轴）。"""
        if pane == "dual_right" and tab.ax_right is not None:
            return tab.ax_right
        return tab.ax

    @staticmethod
    def _nearest_sample_index(xs, target_x, max_distance=None):
        """返回最接近 target_x 的有限 X 点及距离。"""
        values = np.asarray(xs, dtype=float)
        if values.size == 0 or not np.isfinite(target_x):
            return None, None
        valid = np.flatnonzero(np.isfinite(values))
        if valid.size == 0:
            return None, None
        distances = np.abs(values[valid] - float(target_x))
        pos = int(np.argmin(distances))
        distance = float(distances[pos])
        if max_distance is not None and distance > float(max_distance):
            return None, None
        return int(valid[pos]), distance

    @staticmethod
    def _format_hover_text(time_seconds, values, selected_name=None):
        """格式化整体趋势图悬浮框中的时间和多通道温度。"""
        seconds = max(0, int(round(float(time_seconds))))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        time_text = (f"{hours:02d}:{minutes:02d}:{secs:02d}"
                     if hours else f"{minutes:02d}:{secs:02d}")
        lines = [f"时间：{time_text}"]
        for item in ChartRenderer._hover_lines(values, selected_name):
            lines.append(f"{item['name']}：{item['value']}")
        return "\n".join(lines)

    @staticmethod
    def _hover_lines(values, selected_name=None):
        """返回悬浮窗各通道行的文本和命中状态。"""
        lines = []
        for name, value in values:
            if value is None or not np.isfinite(value):
                value_text = "无效"
            else:
                value_text = f"{float(value):.1f} °C"
            lines.append({
                "name": name,
                "value": value_text,
                "selected": name == selected_name,
            })
        return lines

    @staticmethod
    def _hover_card_rows(time_seconds, rows, selected_name=None):
        """返回悬浮卡片的可渲染行元数据，供文本和激活行框共用。"""
        del time_seconds  # 时间标题仍由 _format_hover_detail_text 统一格式化
        result = []
        for line_index, (name, raw, _smooth) in enumerate(rows, start=1):
            value = (f"{float(raw):.1f}"
                     if raw is not None and np.isfinite(raw) else "--")
            result.append({
                "name": name,
                "value": value,
                "selected": name == selected_name,
                "line_index": line_index,
            })
        return result

    @staticmethod
    def _nearest_time_index(ts, t_target):
        """np.searchsorted 最近时间戳下标（tie 取后者）；空/无效输入返回 None。"""
        ts = np.asarray(ts, dtype=float)
        if ts.size == 0 or not np.isfinite(t_target):
            return None
        pos = int(np.searchsorted(ts, float(t_target)))
        if pos <= 0:
            return 0
        if pos >= ts.size:
            return ts.size - 1
        return (pos if abs(ts[pos] - t_target) <= abs(ts[pos - 1] - t_target)
                else pos - 1)

    def _hover_raw_smooth_rows(self, target_x_min, names):
        """悬停时刻（渲染分钟 x）各通道的实时温度行。

        第二列只读取 buffer 原始值；处理管道中的平滑值不属于点位信息，
        不再计算或展示。行序与传入 names（渲染序列 = 图例顺序）一致。
        """
        rows = [(name, None, None) for name in names]
        session = getattr(self.mw, "dataset", None)
        if session is None or not names:
            return rows
        buf = getattr(session, "buffer", None)
        t_all = (np.asarray(buf.time, dtype=float)
                 if buf is not None else np.array([]))
        lookup = getattr(session, "channel_by_label", None)
        for pos, name in enumerate(names):
            ch = lookup(name) if callable(lookup) else None
            idx = getattr(ch, "index", None)
            if ch is None or idx is None or t_all.size == 0:
                continue
            # 原始值：buffer 时间轴（秒）按最近时间戳取该通道列
            if 0 <= int(idx) < buf.n_channels:
                i = self._nearest_time_index(
                    t_all, float(t_all[0]) + float(target_x_min) * 60.0)
                if i is not None:
                    col = np.asarray(buf.column(int(idx)), dtype=float)
                    if i < col.size and np.isfinite(col[i]):
                        rows[pos] = (name, float(col[i]), rows[pos][2])
        return rows

    @staticmethod
    def _format_hover_detail_text(time_seconds, rows, selected_name=None):
        """格式化点位信息悬浮卡片：首行时刻，其后每通道一行实时温度。"""
        seconds = max(0, int(round(float(time_seconds))))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        time_text = (f"{hours:02d}:{minutes:02d}:{secs:02d}"
                     if hours else f"{minutes:02d}:{secs:02d}")
        lines = [f"时间：{time_text}"]
        for row in ChartRenderer._hover_card_rows(
                time_seconds, rows, selected_name):
            lines.append(f"{row['name']}：{row['value']}")
        return "\n".join(lines)

    @staticmethod
    def _nearest_overview_point(series, ax, mouse_x, mouse_y):
        """按屏幕二维距离返回鼠标最近的实际曲线点。"""
        best = None
        for name, xs, ys in series:
            xs = np.asarray(xs, dtype=float)
            ys = np.asarray(ys, dtype=float)
            count = min(len(xs), len(ys))
            if count == 0:
                continue
            valid = np.isfinite(xs[:count]) & np.isfinite(ys[:count])
            indices = np.flatnonzero(valid)
            if not indices.size:
                continue
            points = ax.transData.transform(
                np.column_stack((xs[indices], ys[indices])))
            distances = np.hypot(points[:, 0] - mouse_x,
                                 points[:, 1] - mouse_y)
            pos = int(np.argmin(distances))
            distance = float(distances[pos])
            index = int(indices[pos])
            candidate = (distance, name, index, float(xs[index]), float(ys[index]))
            if best is None or candidate[0] < best[0]:
                best = candidate
        if best is None:
            return None, None, None, None, None
        distance, name, index, point_x, point_y = best
        return name, index, point_x, point_y, distance

    @staticmethod
    def _clamp_xlim(left, right, keep_width=True):
        """限制横轴左边界最小为 0，拖动画布不允许出现负时间。

        keep_width=True（平移）：保留窗口宽度，左边界钳到 0 后整体停住；
        keep_width=False（缩放）：只把左边界钳到 0，右边界保持缩放计算结果，
        保证起点处无法出现负时间、又不浪费右侧扩展空间。
        """
        try:
            left = float(left)
            right = float(right)
        except (TypeError, ValueError, OverflowError):
            return left, right
        if not np.isfinite(left) or not np.isfinite(right):
            return left, right
        width = right - left
        if left < 0.0:
            left = 0.0
        if keep_width:
            right = left + width
        return left, right

    def bind_overview_hover(self, tab) -> None:
        """绑定整体趋势图的鼠标悬浮提示。"""
        state = self._hover_state.setdefault(id(tab), {"tab": tab})
        if state.get("motion_cid") is None:
            state["motion_cid"] = tab.canvas.mpl_connect(
                "motion_notify_event", self._on_overview_mouse_move)
            state["leave_cid"] = tab.canvas.mpl_connect(
                "axes_leave_event", self._on_overview_mouse_leave)
            state["scroll_cid"] = tab.canvas.mpl_connect(
                "scroll_event", self._on_overview_scroll)
            state["press_cid"] = tab.canvas.mpl_connect(
                "button_press_event", self._on_overview_press)
            state["release_cid"] = tab.canvas.mpl_connect(
                "button_release_event", self._on_overview_release)
            state["key_cid"] = tab.canvas.mpl_connect(
                "key_press_event", self._on_overview_key_press)

    def _hover_artists(self, tab):
        state = self._hover_state.setdefault(id(tab), {"tab": tab})
        state.setdefault("locked_channel", None)
        state.setdefault("press_point", None)
        state.setdefault("press_candidate", None)
        return state

    def _lock_hover_channel(self, tab, channel_name):
        """锁定趋势图当前通道，锁定只保存显示名，不保存数据索引。"""
        state = self._hover_artists(tab)
        locked_name = (str(channel_name).strip()
                       if channel_name is not None else None)
        if state.get("locked_channel") == locked_name:
            return
        state["locked_channel"] = locked_name
        self._sync_hover_focus(tab)
        self._refresh_for_hover_focus()

    def _clear_locked_channel(self, tab):
        """清除点击锁定，但保留当前鼠标临时命中状态。"""
        state = self._hover_artists(tab)
        if state.get("locked_channel") is None:
            return
        state["locked_channel"] = None
        self._sync_hover_focus(tab)
        self._refresh_for_hover_focus()

    def _refresh_for_hover_focus(self):
        """锁定焦点变更后立即重绘曲线，避免只更新左侧卡片。"""
        refresh = getattr(self.mw, "refresh_plots", None)
        if callable(refresh):
            refresh()

    def _effective_hover_channel(self, tab, transient_name=None):
        """返回统一视觉焦点：点击锁定 > 左侧卡片悬停 > 图表临时命中。"""
        state = self._hover_artists(tab)
        locked = state.get("locked_channel")
        panel = getattr(self.mw, "channel_panel", None)
        card_hover = getattr(panel, "_hover_channel_name", None)
        return locked or card_hover or transient_name

    def _sync_hover_focus(self, tab):
        """把当前锁定/卡片/图表焦点同步到左侧卡片。"""
        panel = getattr(self.mw, "channel_panel", None)
        if panel is None:
            return
        state = self._hover_artists(tab)
        focus = self._effective_hover_channel(tab, state.get("transient_channel"))
        lock_updater = getattr(panel, "set_locked_hover_channel", None)
        if callable(lock_updater):
            lock_updater(state.get("locked_channel"))
        updater = getattr(panel, "update_hover_channel", None)
        if callable(updater):
            updater(focus)

    def _clear_overview_hover(self, tab, draw=True, clear_panel=False):
        """移除整体趋势图悬浮线、标记和提示框。"""
        if (clear_panel and hasattr(self.mw, "channel_panel")
                and hasattr(self.mw.channel_panel, "clear_hover_temperature")):
            self.mw.channel_panel.clear_hover_temperature()
        state = self._hover_artists(tab)
        for key in ("vline", "hline", "time_label", "temp_label"):
            artist = state.pop(key, None)
            if artist is not None:
                try:
                    artist.remove()
                except (ValueError, AttributeError, NotImplementedError):
                    # NotImplementedError：artist 所属 figure/axes 已随布局
                    # 切换（dual ↔ single 的 clear）销毁，无需也无法移除
                    pass
        card = state.pop("tooltip_card", None)
        if card is not None:
            card.hide()
        state.pop("tooltip_text", None)
        for marker in state.pop("markers", []):
            try:
                marker.remove()
            except (ValueError, AttributeError, NotImplementedError):
                pass
        if draw:
            tab.canvas.draw_idle()

    def _hover_pane_for(self, tab, event):
        """按鼠标所在轴返回悬停缓存键的 pane 角色。"""
        if tab.ax_right is not None and event.inaxes is tab.ax_right:
            return "dual_right"
        return "dual_left" if tab.layout_mode == "dual" else "single"

    def _hover_series_for_event(self, tab, event):
        """返回事件所在 pane 的悬停序列，供点击锁定复用。"""
        pane = self._hover_pane_for(tab, event)
        series = self._overview_hover_series.get((id(tab), pane))
        if not series and pane == "single" and getattr(self.mw, "dataset", None):
            raw = self.mw.channel_panel.visible_series(None)
            series = (self._prepare_live_series(raw)
                      if self._series_exceeds_cap(raw) else raw)
        return series or []

    def _on_overview_mouse_move(self, event):
        """在整体趋势图中显示最近采集点及所有可见通道温度。"""
        tab = self.mw.tab_all
        state = self._hover_artists(tab)
        state["last_event"] = event
        box = self._box_zoom
        if (box is not None and event.inaxes is tab.ax
                and event.xdata is not None and event.ydata is not None):
            x0, y0 = box["x0"], box["y0"]
            x1, y1 = float(event.xdata), float(event.ydata)
            box["x1"], box["y1"] = x1, y1
            rect = box.get("rect")
            if rect is not None:
                try:
                    rect.set_x(min(x0, x1))
                    rect.set_y(min(y0, y1))
                    rect.set_width(abs(x1 - x0))
                    rect.set_height(abs(y1 - y0))
                    tab.canvas.draw_idle()
                except (ValueError, AttributeError):
                    pass
            return
        if (getattr(self, "_dual_right_press", None) == 1
                and tab.ax_right is not None and event.inaxes is tab.ax_right
                and self._dual_pane_active()
                and not getattr(self, "_dual_paused", False)):
            # 双区右轴左键拖动（press→move）：暂停跟随（批 5，D4/D9）
            self._dual_right_press = None
            self._dual_pane_pause_follow(tab)
            return
        if (self._drag_state is not None and self._drag_dual
                and self._dual_pane_active()
                and event.inaxes is tab.ax and event.xdata is not None):
            # 双区视图：左键拖拽只平移左区浏览窗（右区实时跟随、不进 manual）
            start_x, base = self._drag_state
            self._dual_pane_pan_left(start_x - float(event.xdata), base, tab)
            return
        if (self._drag_state is not None and event.inaxes is tab.ax
                and event.xdata is not None):
            start_x, (left, right) = self._drag_state
            delta = start_x - float(event.xdata)
            new_left, new_right = self._clamp_xlim(left + delta, right + delta)
            tab.ax.set_xlim(new_left, new_right)
            self._enter_manual_view(tab.ax)
            tab.canvas.draw_idle()
            return
        if (event.inaxes is not tab.ax and event.inaxes is not tab.ax_right) \
                or event.xdata is None or event.inaxes is None:
            state["transient_channel"] = None
            self._clear_overview_hover(tab, clear_panel=True)
            return
        # 节流：最近点显示每 30ms 最多一次，避免鼠标移动事件洪水卡顿
        now = time.monotonic()
        if now - self._hover_throttle_ts < self._hover_throttle_sec:
            return
        self._hover_throttle_ts = now
        # 悬停命中按鼠标所在 pane 取对应缓存序列（双区视图左右轴各一份）
        pane = self._hover_pane_for(tab, event)
        ax = event.inaxes
        # C7：缓存空时按需取 series——大数据（> live_max_points）降采样后再命中，
        # 避免百万点 O(n) 坐标变换卡死；小数据保留原始点，不影响命中精度。
        # 双区 pane 缓存由 _plot_dual_pane 每次渲染写入，为空时直接清理悬停。
        series = self._overview_hover_series.get((id(tab), pane))
        if not series and pane == "single" and getattr(self.mw, "dataset", None):
            raw = self.mw.channel_panel.visible_series(None)
            series = (self._prepare_live_series(raw)
                      if self._series_exceeds_cap(raw) else raw)
        if not series:
            self._clear_overview_hover(tab, clear_panel=True)
            return

        selected_name, selected_index, target_x, target_y, _ = (
            self._nearest_overview_point(series, ax, event.x, event.y))
        if selected_name is None:
            self._clear_overview_hover(tab, clear_panel=True)
            return
        # 点位信息悬浮卡片显示该时刻全部可见通道的实时温度：
        # names 与渲染序列（图例）同序；渲染 x 为分钟，原始 buffer 与处理
        # 序列时间为秒——由 _hover_raw_smooth_rows 按最近时间戳取值
        rows = self._hover_raw_smooth_rows(
            target_x, [name for name, _, _ in series])

        state["transient_channel"] = selected_name
        focus_name = self._effective_hover_channel(tab, selected_name)
        if hasattr(self.mw.channel_panel, "update_hover_channel"):
            self.mw.channel_panel.update_hover_channel(focus_name)
        if (not getattr(self.mw.dataset, "is_live", False)
                and hasattr(self.mw.channel_panel, "update_hover_temperature")):
            self.mw.channel_panel.update_hover_temperature(target_x)

        self._clear_overview_hover(tab, draw=False)
        # 十字线 / 标记 / 提示框画在鼠标所在的 pane 上（ax 已取 event.inaxes）
        # 画布组归位：线与注记落在深底 PLOT_FACE 上——强调色经 readable_text
        # 对画布补偿，横线/边框走画布轴键 PLOT_AXIS，不再借用骨架色
        state["vline"] = ax.axvline(
            target_x, color=Theme.readable_text(Theme.ACCENT, Theme.PLOT_FACE),
            linestyle="--", alpha=0.7, linewidth=0.9, zorder=90)
        state["hline"] = ax.axhline(
            target_y, color=Theme.PLOT_AXIS, linestyle=":", alpha=0.45,
            linewidth=0.8, zorder=89)
        state["time_label"] = ax.annotate(
            self._format_hover_text(target_x * 60.0, []).split("：", 1)[-1],
            xy=(target_x, ax.get_ylim()[0]), xycoords="data",
            xytext=(0, -8), textcoords="offset points",
            ha="center", va="top", fontsize=9, family=Theme.font("mono"),
            color=Theme.readable_text(Theme.ACCENT, Theme.PLOT_FACE),
            bbox=dict(boxstyle="round,pad=0.25", facecolor=Theme.PLOT_FACE,
                      edgecolor=Theme.PLOT_AXIS, linewidth=0.8, alpha=0.95),
            zorder=101)
        state["temp_label"] = ax.annotate(
            f"{target_y:.1f} °C",
            xy=(ax.get_xlim()[0], target_y), xycoords="data",
            xytext=(-8, 0), textcoords="offset points",
            ha="right", va="center", fontsize=9, family=Theme.font("mono"),
            color=Theme.PLOT_TEXT,
            bbox=dict(boxstyle="round,pad=0.25", facecolor=Theme.PLOT_FACE,
                      edgecolor=Theme.PLOT_AXIS, linewidth=0.8, alpha=0.95),
            zorder=101)
        state["markers"] = []
        color = self.mw.channel_panel.color_of(focus_name or selected_name)
        marker, = ax.plot(
            [target_x], [target_y], marker="o", markersize=7,
            markerfacecolor=Theme.PLOT_FACE, markeredgecolor=color,
            markeredgewidth=1.8, linestyle="None", zorder=95)
        state["markers"].append(marker)

        seconds = max(0, int(round(target_x * 60.0)))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        time_text = (f"{hours:02d}:{minutes:02d}:{secs:02d}"
                     if hours else f"{minutes:02d}:{secs:02d}")
        card_rows = self._hover_card_rows(
            target_x * 60.0, rows, selected_name=focus_name)
        card = getattr(tab, "hover_card", None)
        if card is not None:
            card.set_content(time_text, card_rows)
            canvas_width, canvas_height = tab.canvas.width(), tab.canvas.height()
            mouse_y = canvas_height - event.y
            x_offset, y_offset, ha, va = self.hover_tooltip_position(
                event.x, mouse_y, canvas_width, canvas_height,
                tooltip_width=card.width(), tooltip_height=card.height())
            x = int(round(event.x + (x_offset if ha == "left"
                                      else x_offset - card.width())))
            y = int(round(mouse_y + (y_offset if va == "bottom"
                                      else y_offset - card.height())))
            x = max(8, min(x, canvas_width - card.width() - 8))
            y = max(8, min(y, canvas_height - card.height() - 8))
            card.move(x, y)
            card.show()
            card.raise_()
            state["tooltip_card"] = card
            state["tooltip_position"] = (x, y, ha, va)
        state["tooltip_text"] = self._format_hover_detail_text(
            target_x * 60.0, rows, selected_name=focus_name)
        tab.canvas.draw_idle()

    @staticmethod
    def hover_tooltip_position(mouse_x, mouse_y, canvas_width, canvas_height,
                               tooltip_width=370, tooltip_height=300,
                               margin=8) -> tuple:
        """Choose a tooltip side that keeps the annotation inside the canvas."""
        right_space = canvas_width - mouse_x
        left_space = mouse_x
        if right_space >= tooltip_width + margin or right_space >= left_space:
            x_offset, ha = 14, "left"
        else:
            x_offset, ha = -14, "right"

        top_space = canvas_height - mouse_y
        bottom_space = mouse_y
        if top_space >= tooltip_height + margin or top_space >= bottom_space:
            return x_offset, 18, ha, "bottom"
        return x_offset, -18, ha, "top"

    def _on_overview_mouse_leave(self, event):
        tab = self.mw.tab_all
        state = self._hover_artists(tab)
        state["transient_channel"] = None
        self._clear_locked_channel(tab)
        self._clear_overview_hover(tab, clear_panel=True)

    def _on_overview_key_press(self, event):
        """趋势图获得键盘焦点时按 Escape 清除点击锁定。"""
        if str(getattr(event, "key", "")).lower() not in ("escape", "esc"):
            return
        tab = self.mw.tab_all
        self._clear_locked_channel(tab)

    def _is_hover_click(self, state, event):
        start = state.get("press_point")
        event_x = getattr(event, "x", None)
        event_y = getattr(event, "y", None)
        if start is None or event_x is None or event_y is None:
            return False
        return math.hypot(float(event_x) - start[0],
                          float(event_y) - start[1]) <= self.CLICK_TOLERANCE_PX

    # ---- combo layout defaults (property for dynamic access) ----
    @property
    def _combo_layout_defaults(self):
        return {
            "margin_left":   self.COMBO_MARGIN_LEFT,
            "margin_right":  self.COMBO_MARGIN_RIGHT,
            "margin_top":    self.COMBO_MARGIN_TOP,
            "margin_bottom": self.COMBO_MARGIN_BOTTOM,
            "gap_h":         self.COMBO_GAP_H,
            "gap_v":         self.COMBO_GAP_V,
            "cbar_reserve": 80,
        }

    # --------------------------------------------------------------- 核心渲染入口

    def _current_plot_tab(self):
        """返回当前可见标签页的 PlotTab（统计页无 PlotTab 返回 None）。"""
        idx = self.mw.tabs.currentIndex()
        tabs = {0: self.mw.tab_all, 1: self.mw.tab_10, 2: self.mw.tab_20,
                3: self.mw.tab_30, self.mw.idx_single: self.mw.tab_single,
                self.mw.idx_combo: self.mw.tab_combo}
        return tabs.get(idx)

    def zoom_in(self) -> None:
        """放大视图：缩小 x/y 范围 30%，聚焦局部细节。"""
        tab = self._current_plot_tab()
        if tab is None:
            return
        ax = tab.ax
        xl, xr = ax.get_xlim()
        yb, yt = ax.get_ylim()
        cx, cy = (xl + xr) / 2, (yb + yt) / 2
        dx = (xr - xl) * 0.15
        dy = (yt - yb) * 0.15
        new_left, new_right = self._clamp_xlim(cx - dx, cx + dx, keep_width=False)
        ax.set_xlim(new_left, new_right)
        ax.set_ylim(cy - dy, cy + dy)
        self._enter_manual_view(ax)
        tab.canvas.draw_idle()
        self.mw.statusBar().showMessage("已放大", 1500)

    def zoom_out(self) -> None:
        """缩小视图：扩大 x/y 范围 43%，还原全局视野。"""
        tab = self._current_plot_tab()
        if tab is None:
            return
        ax = tab.ax
        xl, xr = ax.get_xlim()
        yb, yt = ax.get_ylim()
        cx, cy = (xl + xr) / 2, (yb + yt) / 2
        dx = (xr - xl) * 0.715
        dy = (yt - yb) * 0.715
        new_left, new_right = self._clamp_xlim(cx - dx, cx + dx, keep_width=False)
        ax.set_xlim(new_left, new_right)
        ax.set_ylim(cy - dy, cy + dy)
        self._enter_manual_view(ax)
        tab.canvas.draw_idle()
        self.mw.statusBar().showMessage("已缩小", 1500)

    def reset_view(self) -> None:
        """自适应复位：重新自动缩放至全部数据可见。"""
        self._render_visible_tab()
        self.mw.statusBar().showMessage("视图已复位", 1500)

    def live_view_status(self) -> dict:
        """返回实时视图当前状态，不依赖 Qt 控件或全局存储。"""
        session = getattr(self.mw, "dataset", None)
        if session is None or not getattr(session, "is_live", False):
            return {
                "mode": "file",
                "is_live": False,
                "is_recording": False,
                "window_sec": None,
                "latest_min": None,
                "display_start_min": None,
                "display_end_min": None,
                "state_text": "历史文件 · 不在实时采集",
                "range_text": "当前显示：历史文件完整数据范围",
                "latest_text": "",
            }

        latest_min = self._latest_live_minute(session)
        mode = self._normalized_live_view_mode()
        is_recording = bool(getattr(session, "is_recording", False))
        is_finished = getattr(session, "stopped_at", None) is not None

        display_start = display_end = None
        if mode == "manual":
            display_start, display_end = self._manual_display_range()
        elif latest_min is not None:
            # 全局趋势：整条曲线从 0（采集开始）到当前最新点始终完整可见
            display_start = 0.0
            display_end = latest_min

        # 双区自动模式：状态词（state_text）保持原语义不动，双区信息
        # 放入 range_text 展示——既满足 D11「显示双区视图文案」，又不
        # 破坏依赖「正在跟随全局趋势 / 采集已结束」状态词的既有断言
        dual_text = (self._dual_pane_status_text(latest_min)
                     if mode == "auto" else None)

        return {
            "mode": mode,
            "is_live": True,
            "is_recording": is_recording,
            "window_sec": None,
            "latest_min": latest_min,
            "display_start_min": display_start,
            "display_end_min": display_end,
            "state_text": self._live_state_text(
                mode, is_recording, is_finished),
            "range_text": dual_text or self._format_live_range(
                display_start, display_end, mode, is_recording, is_finished),
            "latest_text": self._format_latest_text(latest_min),
        }

    def _dual_pane_status_text(self, latest_min):
        """dual 自动模式的「当前显示」文案；未激活返回 None（沿用原文案）。

        配置属性经 getattr 防御读取（容忍 __new__ 最小构造的实例，
        与 live_view_status「不依赖完整初始化」的既有测试契约一致）。
        """
        if latest_min is None or not getattr(self, "dual_view_enabled", False):
            return None
        session = self.mw.dataset
        if session is None or getattr(session, "source", "") != "live":
            return None
        window = getattr(self, "live_window_sec", 60)
        window_min = window / 60.0
        if latest_min <= window_min:
            return None
        if getattr(self, "_dual_paused", False):
            # 右区暂停跟随（可与浏览历史并列的独立分支，暂停优先展示）
            return "双区视图：已暂停跟随（点「回到最新」恢复）"
        split = max(0.0, latest_min - window_min)
        override = getattr(self, "_dual_left_xlim", None)
        if override is not None:
            try:
                lo, hi = float(override[0]), float(override[1])
            except (TypeError, ValueError, IndexError, OverflowError):
                lo = hi = None
            if (lo is not None and np.isfinite(lo) and np.isfinite(hi)
                    and hi > lo):
                return (f"双区视图：左 {lo:.0f}-{hi:.0f} 分（浏览历史） · "
                        f"右最近 {window:g} 秒")
        return (f"双区视图：左 0-{split:.0f} 分 · "
                f"右最近 {window:g} 秒")

    def update_live_status_labels(self, tab=None) -> None:
        """刷新主窗口底部状态栏的实时状态提示；采集点数在左侧采集状态旁独立高亮显示。"""
        status = self.live_view_status()
        session = getattr(self.mw, "dataset", None)
        status_label = getattr(self.mw, "lbl_live_status", None)
        if status_label is not None:
            status_label.setText(
                f"{status['state_text']}｜{status['range_text']}｜"
                f"{status['latest_text']}"
            )
        count_label = getattr(self.mw, "lbl_point_count", None)
        if count_label is not None:
            count_text = self._session_count_text(session)
            count_label.setText(count_text)
        elapsed_label = getattr(self.mw, "lbl_elapsed", None)
        if elapsed_label is not None:
            if status.get("is_live") and status.get("latest_min") is not None:
                # 实时采集：显示已采集时长
                elapsed_text = ChartRenderer._format_duration_text(
                    status["latest_min"])
                elapsed_label.setText(
                    f"⏱ 已采集 {elapsed_text}" if elapsed_text else "")
            elif session is not None and not getattr(session, "is_live", False):
                # 离线文件/数据库：显示总时长
                duration_min = self._get_session_duration_minutes(session)
                if duration_min is not None and duration_min > 0:
                    elapsed_text = ChartRenderer._format_duration_text(duration_min)
                    elapsed_label.setText(f"⏱ 总时长 {elapsed_text}")
                else:
                    elapsed_label.setText("")
            else:
                elapsed_label.setText("")
        styler = getattr(self.mw, "_style_live_status_labels", None)
        if callable(styler):
            styler(None, status)

    @staticmethod
    def _session_count_text(session):
        """返回状态栏点数文本（实时会话为“N 点”，历史会话为“共 N 点”）。"""
        n = getattr(session, "n", None)
        if n is None:
            return ""
        if getattr(session, "is_live", False):
            return f"{n} 点"
        return f"共 {n} 点"

    def _get_session_duration_minutes(self, session):
        """获取会话的总时长（分钟）。

        用于离线文件/数据库导入后在状态栏显示总时长。
        """
        try:
            time_sec = getattr(session, "time_sec", None)
            if time_sec is None or len(time_sec) == 0:
                return None
            valid = time_sec[np.isfinite(time_sec)]
            if valid.size == 0:
                return None
            return (valid.max() - valid.min()) / 60.0
        except Exception:
            return None

    def _normalized_live_view_mode(self):
        """返回状态和实际取数共用的有效实时视图模式。"""
        mode = getattr(self, "live_view_mode", "auto")
        if mode not in ("auto", "manual"):
            return "auto"
        if mode == "manual" and self._manual_display_range() is None:
            return "auto"
        return mode

    @staticmethod
    def _valid_live_time_mask(values):
        """返回实时曲线可使用的时间点掩码。"""
        try:
            values = np.asarray(values, dtype=float)
        except (TypeError, ValueError, OverflowError):
            return np.array([], dtype=bool)
        return np.isfinite(values) & (np.abs(values) <= 1.0e12)

    def _latest_live_minute(self, session):
        """返回实际可见实时曲线中最新的有效采样时间（分钟）。"""
        channel_panel = getattr(self.mw, "channel_panel", None)
        visible_series = getattr(channel_panel, "visible_series", None)
        if callable(visible_series):
            try:
                series = visible_series(None)
            except (AttributeError, TypeError, ValueError, OverflowError):
                return None
            latest_min = None
            for item in series or []:
                try:
                    time_min = np.asarray(item[1], dtype=float)
                    valid = self._valid_live_time_mask(time_min)
                    if valid.any():
                        current = float(time_min[valid].max())
                        latest_min = (current if latest_min is None
                                      else max(latest_min, current))
                except (IndexError, TypeError, ValueError, OverflowError):
                    continue
            return latest_min

        try:
            time_sec = np.asarray(session.buffer.time, dtype=float)
            valid = self._valid_live_time_mask(time_sec)
            if not valid.any():
                return None
            latest_min = float(time_sec[valid].max()) / 60.0
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None
        return latest_min if np.isfinite(latest_min) else None

    def _manual_display_range(self):
        """安全读取手动横轴范围，避免状态查询被异常值打断。"""
        manual_xlim = getattr(self, "_manual_xlim", None)
        if manual_xlim is None:
            return None
        try:
            start, end = (float(manual_xlim[0]), float(manual_xlim[1]))
        except (IndexError, TypeError, ValueError, OverflowError):
            return None
        if (not np.isfinite(start) or not np.isfinite(end)
                or start >= end):
            return None
        return start, end

    @staticmethod
    def _live_state_text(mode, is_recording=True, is_finished=False):
        mode_text = {
            "auto": "全局趋势",
            "manual": "手动浏览",
        }.get(mode, "全局趋势")
        if is_finished:
            return f"采集已结束 · {mode_text}"
        return {
            "auto": "正在跟随全局趋势",
            "manual": "手动浏览中",
        }.get(mode, "正在跟随全局趋势")

    @staticmethod
    def _format_live_range(start_min, end_min, mode, is_recording=True,
                           is_finished=False):
        """时间范围文案。跟随/浏览状态词只由 state_text 呈现一次
        （2026-08-22 去重：删除原括号内重复的长状态文案），
        mode/is_recording/is_finished 参数保留以兼容既有调用点。"""
        if start_min is None or end_min is None:
            return "当前显示：暂无实时数据"
        return (f"当前显示：{ChartRenderer._format_time_text(start_min)}"
                f" ～ {ChartRenderer._format_time_text(end_min)}")

    @staticmethod
    def _format_duration_text(minutes):
        """累计采集时长文本：分钟粒度，≥1 小时用「H小时M分钟」（小时可超 24）。"""
        try:
            total = int(round(float(minutes)))
        except (TypeError, ValueError, OverflowError):
            return ""
        if total < 1:
            return "不足1分钟"
        hours, minutes = divmod(total, 60)
        if hours == 0:
            return f"{minutes}分钟"
        return f"{hours}小时{minutes}分钟"

    @staticmethod
    def _format_latest_text(latest_min):
        if latest_min is None:
            return "最新采样：暂无"
        return f"最新采样：{ChartRenderer._format_time_text(latest_min)}"

    @staticmethod
    def _format_time_text(minutes):
        """把相对分钟数格式化为易读的时分秒文本。"""
        try:
            total_seconds = int(round(float(minutes) * 60.0))
        except (TypeError, ValueError, OverflowError):
            return "时间无效"
        if total_seconds < 0:
            sign = "-"
            total_seconds = abs(total_seconds)
        else:
            sign = ""
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _format_axis_minutes(minutes, pos=None):
        """双区 X 轴刻度标签：<1 小时用 MM:SS，≥1 小时用 H:MM:SS。

        窄实时窗（1~5 分钟）下刻度细到 10 秒级，原始分钟小数（如 59.2）
        不可读；左右两区共用同一连续时间轴（分界点数字无缝衔接）。
        pos 参数为 FuncFormatter 回调协议保留，不使用。
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

    def set_live_view_mode(self, mode):
        """切换实时画布模式：auto=全局趋势跟随(0~最新)；manual=手动浏览。"""
        if mode not in ("auto", "manual"):
            return
        self.live_view_mode = mode
        if mode != "manual":
            self._manual_xlim = None
            self._auto_return_timer.stop()
            # 模式切换退出暂停跟随：暂停冻结只属于 auto 双区态，manual 走
            # 单图路径且定时器已停，不清会让切回 auto 后永久冻结
            self._dual_paused = False
            self._dual_hold_split = None
            self._dual_hold_t_end = None
        # 模式切换清增量缓存：强制全量重建，避免复用旧 xlim/artist 状态
        self._live_inc.clear()
        self._render_visible_tab()

    def return_to_latest(self):
        """回到最新：切到整体趋势标签并恢复全局趋势视图（实时=0~最新；离线=全量数据）。

        交互设计（用户确认）：点击"回到最新"即回到整体趋势标签的全量视图，
        与按钮提示文案"以全局趋势图（从0到当前最新）展示"一致；在"前N分钟"
        等标签上也能一键回到完整全图。
        """
        self._dual_left_xlim = None  # 回到最新同时清除双区左区浏览窗
        # 双区右区暂停跟随一并恢复（清冻结点），走 auto 重渲染实时跟随
        self._dual_paused = False
        self._dual_hold_split = None
        self._dual_hold_t_end = None
        self.set_live_view_mode("auto")
        tabs = getattr(self.mw, "tabs", None)
        if tabs is not None and tabs.currentIndex() != 0:
            # 切换到整体趋势标签（currentChanged → _on_tab_changed 重绘全量）
            tabs.setCurrentIndex(0)
        if getattr(self.mw.dataset, "is_live", False):
            self.mw.statusBar().showMessage("已回到全局趋势", 1500)
        else:
            self.mw.statusBar().showMessage("已复位到全量视图", 1500)

    def _arm_auto_return(self):
        """重置 2 分钟自动回最新定时器（仅实时采集会话生效）。"""
        if not getattr(self.mw.dataset, "is_live", False):
            return
        self._auto_return_timer.start()

    def _auto_return_to_latest(self):
        """手动浏览 2 分钟无任何拖动操作后，自动回到最新状态（仅实时采集）。"""
        if not getattr(self.mw.dataset, "is_live", False):
            return
        if (self._dual_pane_active()
                and (getattr(self, "_dual_paused", False)
                     or getattr(self, "_dual_left_xlim", None) is not None)):
            # 双区浏览历史 / 右区暂停跟随超时：清左区浏览窗与暂停冻结点，
            # 完整回到实时跟随（不动 live_view_mode、不切标签页）
            self._dual_paused = False
            self._dual_hold_split = None
            self._dual_hold_t_end = None
            self._dual_left_xlim = None
            self._render_visible_tab()
            return
        if self._normalized_live_view_mode() != "manual":
            return
        self.return_to_latest()

    def _enter_manual_view(self, ax):
        """进入手动浏览：实时/离线数据都记录手动横轴范围，暂停自动跟随。"""
        if self.mw.dataset is None:
            return
        self.live_view_mode = "manual"
        self._manual_xlim = tuple(float(v) for v in ax.get_xlim())
        sync_mode = getattr(self.mw, "_sync_live_view_actions", None)
        if sync_mode is not None:
            sync_mode("manual")
        if getattr(self.mw.dataset, "is_live", False):
            self.mw.statusBar().showMessage("手动浏览 · 实时数据仍在采集", 2000)
            self._arm_auto_return()
        else:
            self.mw.statusBar().showMessage("手动浏览中", 2000)

    def _on_overview_scroll(self, event):
        tab = self.mw.tab_all
        if (event.inaxes is not None and tab.ax_right is not None
                and event.inaxes is tab.ax_right):
            # 双区右轴滚轮：暂停跟随（批 5，D4/D9）；暂停期间忽略（no-op）
            self._dual_pane_pause_follow(tab)
            return
        if event.inaxes is not tab.ax or event.xdata is None:
            return
        if self._dual_pane_active():
            # 双区视图：滚轮只缩放左区浏览窗（以鼠标 x 为中心 0.8/1.25 倍），
            # 结果钳在 [0, split]；右区实时跟随不受影响，不进入 manual 模式
            split = self._dual_pane_split()
            base = (self._dual_left_xlim
                    if self._dual_left_xlim is not None else (0.0, split))
            center = float(event.xdata)
            scale = 0.8 if event.button == "up" else 1.25
            left, right = self._clamp_dual_left_xlim(
                center - (center - base[0]) * scale,
                center + (base[1] - center) * scale,
                split, keep_width=False)
            self._dual_pane_apply_left_xlim((left, right), tab)
            return
        ax = tab.ax
        scale = 0.8 if event.button == "up" else 1.25
        left, right = ax.get_xlim()
        center = float(event.xdata)
        new_left, new_right = self._clamp_xlim(
            center - (center - left) * scale,
            center + (right - center) * scale,
            keep_width=False)
        ax.set_xlim(new_left, new_right)
        self._enter_manual_view(ax)
        self._plot_multi(self.mw.tab_all, None, "整体温度爬升趋势")

    def _on_overview_press(self, event):
        tab = self.mw.tab_all
        if tab.ax_right is not None and event.inaxes is tab.ax_right:
            # 双区右轴：只记录待触发按键（左键拖动 press→move / 右键框选
            # 释放时暂停跟随）；暂停期间一律忽略，不产生选框、不进拖拽
            if (self._dual_pane_active()
                    and not getattr(self, "_dual_paused", False)
                    and event.button in (1, 3)):
                self._dual_right_press = int(event.button)
            return
        ax = tab.ax
        if event.inaxes is not ax:
            return
        if event.button == 1:
            state = self._hover_artists(tab)
            event_x = getattr(event, "x", None)
            event_y = getattr(event, "y", None)
            state["press_point"] = (
                float(event_x), float(event_y)) if event_x is not None and event_y is not None else None
            state["press_candidate"] = None
            if state["press_point"] is not None:
                series = self._hover_series_for_event(tab, event)
                if series:
                    candidate, _, _, _, distance = self._nearest_overview_point(
                        series, ax, event.x, event.y)
                    if (distance is not None
                            and distance <= self.CLICK_HIT_TOLERANCE_PX):
                        state["press_candidate"] = candidate
            # 左键拖拽：平移视图（dual 激活时为左区浏览窗平移）
            self._drag_dual = self._dual_pane_active()
            self._drag_state = (float(event.xdata), ax.get_xlim())
        elif event.button == 3:
            # 右键拖拽：框选缩放（拖出矩形区域放大查看细节）
            if event.xdata is None or event.ydata is None:
                return
            from matplotlib.patches import Rectangle
            rect = Rectangle(
                (event.xdata, event.ydata), 0.0, 0.0,
                fill=True, facecolor=Theme.ACCENT, alpha=0.15,
                edgecolor=Theme.ACCENT, linewidth=1.0)
            ax.add_patch(rect)
            self._box_zoom = {
                "x0": float(event.xdata),
                "y0": float(event.ydata),
                "rect": rect,
            }
            ax.figure.canvas.draw_idle()

    def _on_overview_release(self, event):
        if event.button == 3:
            right_press = getattr(self, "_dual_right_press", None) == 3
            self._dual_right_press = None
            box = self._box_zoom
            if box is None:
                # 双区右轴右键框选释放（press 已记录、无选框）：暂停跟随
                if right_press:
                    self._dual_pane_pause_follow(self.mw.tab_all)
                return
            self._box_zoom = None
            rect = box.get("rect")
            ax = self.mw.tab_all.ax
            if rect is not None:
                try:
                    rect.remove()
                except (ValueError, AttributeError):
                    pass
            x0, x1 = box.get("x0"), box.get("x1")
            y0, y1 = box.get("y0"), box.get("y1")
            if (None not in (x0, x1, y0, y1)
                    and abs(x1 - x0) > 1e-9 and abs(y1 - y0) > 1e-9):
                if self._dual_pane_active():
                    # 双区视图：框选只取时间范围放大左区浏览窗（温度标尺恒
                    # 自动、忽略框选 Y 范围，不进 manual）；右轴起框在 press
                    # 阶段已被忽略，不会产生任何轴变化
                    left, right = self._clamp_dual_left_xlim(
                        min(x0, x1), max(x0, x1), self._dual_pane_split())
                    self._dual_pane_apply_left_xlim((left, right),
                                                    self.mw.tab_all)
                    return
                new_x0, new_x1 = self._clamp_xlim(min(x0, x1), max(x0, x1))
                ax.set_xlim(new_x0, new_x1)
                ax.set_ylim(min(y0, y1), max(y0, y1))
                self._enter_manual_view(ax)
                ax.figure.canvas.draw_idle()
                self.mw.statusBar().showMessage("已放大到选中区域", 1500)
            return
        if event.button == 1:
            tab = self.mw.tab_all
            state = self._hover_artists(tab)
            is_click = (event.inaxes is tab.ax
                        and self._is_hover_click(state, event)
                        and self._drag_state is not None)
            if is_click:
                candidate = state.get("press_candidate")
                if candidate is None:
                    self._clear_locked_channel(tab)
                else:
                    self._lock_hover_channel(tab, candidate)
            state["press_point"] = None
            state["press_candidate"] = None
            self._dual_right_press = None
            self._drag_state = None
            self._drag_dual = False
            if event.inaxes is self.mw.tab_all.ax:
                self._on_overview_mouse_move(event)

    def _prepare_live_series(self, series):
        """按当前视图模式截取并抽样，原始 series 不做修改。

        离线数据在自动（全量）模式下保持原始曲线；进入手动浏览时
        与实时数据一致，只截取手动范围对应的数据用于轴自适应。
        """
        if not self.mw.dataset:
            return series
        mode = self._normalized_live_view_mode()
        manual_range = (self._manual_display_range()
                        if mode == "manual" else None)
        # 离线大数据同样降采样到 live_max_points（仅显示层，保留局部极值；
        # 原始数据 / 统计 / 导出不受影响），避免全量 matplotlib 绘制卡顿。
        out = []
        for name, x, v in series:
            x = np.asarray(x, dtype=float)
            v = np.asarray(v, dtype=float)
            count = min(x.size, v.size)
            x, v = x[:count], v[:count]
            valid_time = self._valid_live_time_mask(x)
            x, v = x[valid_time], v[valid_time]
            if not x.size:
                continue
            start = end = None
            if mode == "manual" and manual_range is not None:
                start, end = manual_range
            if start is not None or end is not None:
                x, v = SeriesSampler.window(
                    x, v, start=start, end=end,
                    max_points=self.live_max_points)
            else:
                x, v = SeriesSampler.sample(x, v, self.live_max_points)
            if x.size:
                out.append((name, x, v))
        return out

    def _series_exceeds_cap(self, series) -> bool:
        """series 任一通道点数超过 live_max_points 即需降采样（C7 hover 命中用）。"""
        cap = self.live_max_points
        for _name, x, _v in series:
            try:
                if len(x) > cap:
                    return True
            except TypeError:
                continue
        return False

    def refresh_plots(self) -> None:
        """刷新当前可见标签页的曲线图——从统一管道取数。"""
        if self.mw.dataset is None:
            return
        s = self.mw.dataset
        if self.mw.pipeline.is_dirty or not s.processed:
            self.mw.pipeline.recompute(s)
        self._render_visible_tab()

    def retarget_temp_axis(self) -> None:
        """用户显式修改温度轴基础窗口 → 复位智能状态机（立即生效的强信号）。

        温度轴智能模式的回缩滞回（连续 AUTO_TEMP_BASE_FRAMES 帧完全回界才
        缩回）只用于吸收『数据』在基础窗口边界附近的抖动。用户在主界面左侧
        底部显式调节基础窗口是强意图信号，必须立即以新窗口为基准重算轴向；
        否则 expanded（越界已扩展）状态下把窗口调宽到能包住当前数据后，
        轴向要等连续 3 次刷新帧才缩回——而离线 / 暂停会话没有周期刷新帧，
        轴向会永久停在旧的扩展范围，表现为“按钮调了但趋势图温度轴不更新”
        的偶发失效（复现：refresh_axis_only 单帧后 _auto_temp_in_base_frames
        只到 1，轴不缩回）。
        """
        self._auto_temp_state = "base"
        self._auto_temp_in_base_frames = 0

    def refresh_axis_only(self) -> None:
        """仅按当前基础窗口重算可见标签页的温度轴范围，不重建任何曲线。

        调节温度轴上下限只影响 y 轴范围（_auto_temp_axis 状态机输出），
        曲线数据未变：跳过 clear / artist 重建 / 图例 / 色条，直接重算
        ylim 后 draw_idle —— 大文件 / 大数据量下连续调节不再整图重建。
        数据取数与绘制路径同源（visible_series 全量 min/max，显示层降采样
        保留极值），状态机结果与全量重建一致；ylim 无变化时不触发重绘。
        统计页无温度曲线，跳过。

        本方法是左侧底部“温度轴基础窗口”快调区的唯一刷新入口，即“用户
        显式调窗口”语义：入口处先复位智能状态机（retarget_temp_axis），
        确保 expanded 状态下本次刷新立即按新基础窗口生效（在线/离线一致）。
        异常一律记录 [AXIS] 日志并走 finally 收尾，避免按钮链路被异常打断
        后“无响应”且无法追踪。
        """
        self.retarget_temp_axis()
        if self.mw.dataset is None:
            print("[AXIS] 无活跃会话：跳过轴刷新（基础窗口配置已保存）",
                  flush=True)
            return
        idx = self.mw.tabs.currentIndex()
        tab = None
        changed = False
        try:
            if idx in (0, 1, 2, 3):
                tab = (self.mw.tab_all, self.mw.tab_10,
                       self.mw.tab_20, self.mw.tab_30)[idx]
                max_minutes = None if idx == 0 else self.mw.win_front[idx - 1]
                series = self.mw.channel_panel.visible_series(max_minutes)
                ys = (np.concatenate([v for _, _, v in series])
                      if series else np.array([]))
                # dual 双区视图：tab.axes=[左,右]，状态机每帧只推进一次、
                # 范围同步到两轴；单轴视图 tab.axes=[ax] 与原行为一致
                changed = self._apply_shared_temp_axis(tab.axes, ys) or changed
            elif idx == self.mw.idx_single:
                tab = self.mw.tab_single
                series = []
                for nm in self.mw._checked_compare_names():
                    series += self.mw.channel_panel.visible_series(None, only=nm)
                ys = (np.concatenate([v for _, _, v in series])
                      if series else np.array([]))
                changed = self._apply_temp_axis(tab.ax, ys) or changed
            elif idx == self.mw.idx_combo:
                tab = self.mw.tab_combo
                for ax, (smin, emax) in zip(
                        tab.fig.axes[:3],
                        [(None, None), self.mw.a4_custom1, self.mw.a4_custom2]):
                    series = self.mw.channel_panel.visible_series_window(
                        smin, emax)
                    ys = (np.concatenate([v for _, _, v in series])
                          if series else np.array([]))
                    changed = self._apply_temp_axis(ax, ys) or changed
                    self._bound_combo_temperature_ticks(ax)
            if changed and tab is not None:
                tab.canvas.draw_idle()
            if tab is not None:
                lo = getattr(self.mw, "ax_temp_base_lo", None)
                hi = getattr(self.mw, "ax_temp_base_hi", None)
                cur = getattr(self, "_auto_temp_range", None)
                print(f"[AXIS] 温度轴基础窗口 {lo:g}~{hi:g}℃ 已应用"
                      f"（页签 {idx}，生效范围={cur}，需重绘={changed}）",
                      flush=True)
        except Exception as e:
            traceback.print_exc()
            print(f"[AXIS] 仅轴刷新异常（页签 {idx}）: {e}", flush=True)
        finally:
            self.update_live_status_labels()
            sync_controls = getattr(self.mw, "_update_live_view_controls", None)
            if callable(sync_controls):
                sync_controls()

    def _render_visible_tab(self):
        """仅渲染当前显示的标签页图表。原 MainWindow._render_visible_tab"""
        idx = self.mw.tabs.currentIndex()
        if idx != 0 and hasattr(self.mw, "tab_all"):
            self._clear_overview_hover(self.mw.tab_all, draw=False)
        if idx == 0:
            if self._dual_pane_active():
                self._plot_dual_pane(self.mw.tab_all)
            else:
                self._plot_multi(self.mw.tab_all, None, "整体温度爬升趋势")
        elif idx == 1:
            self._plot_multi(self.mw.tab_10, self.mw.win_front[0],
                             f"前{_fmt_win_int(self.mw.win_front[0])}分钟温度爬升")
        elif idx == 2:
            self._plot_multi(self.mw.tab_20, self.mw.win_front[1],
                             f"前{_fmt_win_int(self.mw.win_front[1])}分钟温度爬升")
        elif idx == 3:
            self._plot_multi(self.mw.tab_30, self.mw.win_front[2],
                             f"前{_fmt_win_int(self.mw.win_front[2])}分钟温度爬升")
        elif idx == self.mw.idx_single:
            self._plot_single()
            self.mw._position_compare_panel()
        elif idx == self.mw.idx_combo:
            self._plot_combo()
        elif idx == self.mw.idx_stat:
            self.mw._update_stats()
        self.update_live_status_labels()
        sync_controls = getattr(self.mw, "_update_live_view_controls", None)
        if callable(sync_controls):
            sync_controls()

    # --------------------------------------------------------------- 绘制线条核心

    def _style_axes_canvas(self, ax):
        """统一趋势视窗底色/网格/边框（_draw_lines 与空态提示共用）。

        视窗分层（两分区）：绘图区用 PLOT_FACE（比浅色界面骨架深一档），
        与外围卡片形成「画布嵌套」层次；主网格走 PLOT_GRID（画布专属
        网格键，深底上克制可见），并经 axisbelow 垫在曲线之下；上/右
        边框隐藏、下/左边框转 PLOT_AXIS，刻度线轻量化而刻度文字保持
        PLOT_TEXT 纯白高对比——线轻字重，读数清晰。
        """
        ax.set_facecolor(Theme.PLOT_FACE)
        ax.set_axisbelow(True)
        # 主网格用带色虚线（PLOT_GRID 按各深画布调成克制可见的调子），
        # 叠在 PLOT_FACE 上可对照温度/时间读数；线宽与虚线密度刻意收敛，
        # 明显细于曲线（1.4px），保证网格辅助读数但不抢曲线风头
        ax.grid(True, which="major", linestyle="--", color=Theme.PLOT_GRID,
                linewidth=0.9, alpha=1.0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color(Theme.PLOT_AXIS)
        ax.spines["bottom"].set_color(Theme.PLOT_AXIS)

    def _draw_series_on(self, ax, series, title="", with_legend=True,
                        norm=None, fonts=None, title_loc="left",
                        hovered_name=None):
        """在给定 ax 上按当前配色模式绘制曲线（_draw_lines 的绘制核心）。

        从 _draw_lines 原样最小提取（轴样式/标题/逐通道曲线/图例），
        供单图路径（_draw_lines 在此之上追加通用 _apply_axis 与 thermal
        colorbar）与 dual 双区路径（每 pane 独立处理轴范围、共享 colorbar）
        复用；本方法不含 _apply_axis / colorbar。
        返回 (used_norm, collected, smap, xs, ys)：
        collected=[(name, artist)] 供实时增量复用；smap 仅 thermal 非空。
        """
        thermal = (self.mw.color_mode == "thermal")
        f = fonts or {}
        tfs = f.get("title", self.title_style(
            ax.figure.get_figwidth() * ax.figure.dpi)[0])
        lfs = f.get("label", Theme.PLOT_LABEL_SIZE)
        tkfs = f.get("tick", Theme.PLOT_TICK_SIZE)
        lgfs = f.get("legend", Theme.PLOT_LEGEND_SIZE)
        ax.set_xlabel("时间 (分钟)", fontsize=lfs, color=Theme.PLOT_TEXT)
        ax.set_ylabel("温度 (℃)", fontsize=lfs, labelpad=8,
                      color=Theme.PLOT_TEXT)
        ax.tick_params(labelsize=tkfs, pad=4, length=3.5, width=0.8,
                       color=Theme.PLOT_AXIS, labelcolor=Theme.PLOT_TEXT)
        ax.xaxis.label.set_fontfamily(Theme.font("plot"))
        ax.yaxis.label.set_fontfamily(Theme.font("plot"))
        for tick in ax.get_xticklabels() + ax.get_yticklabels():
            tick.set_fontfamily(Theme.font("mono"))
        if title:
            title_size, title_pad = self.title_style(
                ax.figure.get_figwidth() * ax.figure.dpi)
            # fontfamily 必须随 set_title 传入：loc="left" 的标题写入
            # ax._left_title，事后 ax.title.set_fontfamily() 只会改到
            # 空的中心标题，左上角标题将退回 rcParams 默认字体渲染中文
            ax.set_title(title, fontsize=min(tfs, title_size),
                         loc=title_loc, pad=title_pad, color=Theme.PLOT_TEXT,
                         fontweight=Theme.PLOT_TITLE_WEIGHT,
                         fontfamily=Theme.font("plot"))
        self._style_axes_canvas(ax)
        legend_handles = []
        collected = []          # [(name, artist)] —— 供实时增量复用
        used_norm = None
        smap = None
        xs = np.array([])
        ys = np.array([])
        if not series:
            return used_norm, collected, smap, xs, ys
        xs = np.concatenate([x for _, x, _ in series])
        ys = np.concatenate([v for _, _, v in series])
        xfin = xs[np.isfinite(xs)]
        yfin = ys[np.isfinite(ys)]
        if xfin.size:
            xmin, xmax = float(xfin.min()), float(xfin.max())
            if xmin == xmax:  # 单点时范围相同 → 扩展避免 matplotlib 奇异变换
                xmax = xmin + 1.0
            ax.set_xlim(xmin, xmax)
        if yfin.size:
            ymin, ymax = float(yfin.min()), float(yfin.max())
            if ymin == ymax:
                ymax = ymin + 1.0
            ax.set_ylim(ymin, ymax)
        if thermal:
            if norm is None:
                if yfin.size:
                    vmin, vmax = float(yfin.min()), float(yfin.max())
                else:
                    vmin, vmax = 0.0, 1.0
                if vmax - vmin < 1e-9:
                    vmax = vmin + 1.0
                norm = Normalize(vmin, vmax)
            used_norm = norm
            smap = ScalarMappable(norm=norm, cmap=THERMAL_CMAP)
            for name, x, v in series:
                style = self.line_style(name, hovered_name)
                if len(x) >= 2:
                    pts = np.column_stack([x, v])
                    segs = np.concatenate([pts[:-1, None, :], pts[1:, None, :]], axis=1)
                    lc = LineCollection(
                        segs, cmap=THERMAL_CMAP, norm=norm,
                        linewidth=style["linewidth"], alpha=style["alpha"],
                        zorder=style["zorder"])
                    lc.set_array(v)
                    ax.add_collection(lc)
                    collected.append((name, lc))
                else:
                    line, = ax.plot(
                        x, v, color=THERMAL_CMAP(norm(v[0])),
                        linewidth=style["linewidth"], alpha=style["alpha"],
                        zorder=style["zorder"])
                    collected.append((name, line))
                legend_handles.append(
                    Line2D([0], [0], color=THERMAL_CMAP(norm(v[np.argmax(v)])),
                           lw=2.4, label=name))
        else:
            for name, x, v in series:
                col = (Theme.plot_alarm_color() if name in self._alarm_channels
                       else self.mw.channel_panel.color_of(name))
                style = self.line_style(name, hovered_name)
                lines = ax.plot(x, v, color=col, label=name, **style)
                legend_handles.extend(lines)
                collected.append((name, lines[0]))
        if with_legend and legend_handles:
            MAX_CHANNELS = 8
            MAX_NAME_CHARS = 8
            shown = []
            for h in legend_handles[:MAX_CHANNELS]:
                name = h.get_label()
                if len(name) > MAX_NAME_CHARS:
                    h.set_label(name[:MAX_NAME_CHARS] + "\u2026")
                shown.append(h)
            if shown:
                legend = ax.legend(handles=shown,
                                   loc='lower right',
                                   bbox_to_anchor=(1.0, 1.0),
                                   ncol=len(shown),
                                   fontsize=lgfs,
                                   frameon=False,
                                   borderaxespad=0.0,
                                   handlelength=1.0,
                                   handletextpad=0.3,
                                   columnspacing=0.8)
                for text in legend.get_texts():
                    text.set_fontfamily(Theme.font("plot"))
                    text.set_color(Theme.PLOT_TEXT)
                legend.set_in_layout(False)
        return used_norm, collected, smap, xs, ys

    def _draw_lines(self, ax, series, title="", with_legend=True, with_cbar=True,
                    norm=None, fonts=None, cbar_pad=0.03, _collect=False,
                    title_loc="left", hovered_name=None):
        """按当前配色模式绘制曲线。原 MainWindow._draw_lines

        _collect=True 时收集实际绘制的 artist 列表 [(name, artist), ...]，
        供实时增量绘制复用（set_data 更新，避免全量重建）；此时返回 (norm, collected)。
        绘制核心提取为 _draw_series_on（dual 双区路径复用）；本方法在其上
        追加单图路径的通用轴范围（_apply_axis）与 thermal colorbar，
        对外签名、返回值与绘制结果保持不变。
        """
        used_norm, collected, smap, xs, ys = self._draw_series_on(
            ax, series, title=title, with_legend=with_legend, norm=norm,
            fonts=fonts, title_loc=title_loc, hovered_name=hovered_name)
        if series:
            self._apply_axis(ax, xs, ys)
        if smap is not None and with_cbar:
            self._attach_thermal_colorbar(ax, smap, fonts=fonts,
                                          cbar_pad=cbar_pad)
        return (used_norm, collected) if _collect else used_norm

    @staticmethod
    def _attach_thermal_colorbar(ax, smap, fonts=None, cbar_pad=0.03):
        """在 ax 右侧挂 thermal colorbar（_draw_lines 与 dual 双区共用）。"""
        f = fonts or {}
        lfs = f.get("label", Theme.PLOT_LABEL_SIZE)
        tkfs = f.get("tick", Theme.PLOT_TICK_SIZE)
        try:
            cb = ax.get_figure().colorbar(smap, ax=ax, fraction=0.04, pad=cbar_pad)
            cb.set_label("温度 (℃)", fontsize=lfs, labelpad=12)
            cb.ax.tick_params(labelsize=tkfs)
            cb.ax.yaxis.label.set_fontfamily(Theme.font("plot"))
            cb.ax.yaxis.label.set_color(Theme.PLOT_TEXT)
            for tick in cb.ax.get_yticklabels():
                tick.set_fontfamily(Theme.font("mono"))
            return cb
        except Exception as e:
            print(f"[CHART] 刻度字体设置异常: {e}", flush=True)
            return None

    # --------------------------------------------------------------- 轴设置应用

    def _auto_temp_axis(self, dmin, dmax):
        """温度轴智能判断模式的自适应范围（状态机）。

        基础窗口与扩展系数从配置读取（主界面左侧底部调基础窗口，设置页调系数）：
          - 基础窗口（默认 [20, 40]）：数据在界内（含滞回阈值）时轴固定不变；
          - 越界后按「最低温度 × 下界系数 / 最高温度 × 上界系数」扩展，实时只扩不缩；
          - 数据完全回到基础窗口且连续 AUTO_TEMP_BASE_FRAMES 次刷新后，
            才缩回基础窗口，避免临界值附近反复跳变。
        状态仅启动时初始化一次，不随新会话 / 新文件 / 通道显隐变化重置。
        """
        base_lo = float(self.mw.ax_temp_base_lo)
        base_hi = float(self.mw.ax_temp_base_hi)
        lo_factor = float(self.mw.ax_temp_lo_factor)
        hi_factor = float(self.mw.ax_temp_hi_factor)
        hys = self.AUTO_TEMP_HYSTERESIS
        if self._auto_temp_state == "base":
            if dmin >= base_lo - hys and dmax <= base_hi + hys:
                # 数据仍在基础窗口（含滞回余量）内 → 轴保持不动
                self._auto_temp_in_base_frames = 0
                self._auto_temp_range = (base_lo, base_hi)
            else:
                # 真正越界 → 切到扩展，按温度本身的比例扩展
                self._auto_temp_state = "expanded"
                self._auto_temp_in_base_frames = 0
                self._auto_temp_range = (
                    min(base_lo, dmin * lo_factor),
                    max(base_hi, dmax * hi_factor))
        else:  # expanded
            if dmin >= base_lo and dmax <= base_hi:
                # 数据完全回到基础窗口 → 连续计数，满 N 次才缩回
                self._auto_temp_in_base_frames += 1
                if self._auto_temp_in_base_frames >= self.AUTO_TEMP_BASE_FRAMES:
                    self._auto_temp_state = "base"
                    self._auto_temp_in_base_frames = 0
                    self._auto_temp_range = (base_lo, base_hi)
            else:
                # 跟随扩展，只扩不缩，防止实时抖动导致范围收缩
                self._auto_temp_in_base_frames = 0
                cur_lo, cur_hi = self._auto_temp_range
                self._auto_temp_range = (
                    min(cur_lo, dmin * lo_factor),
                    max(cur_hi, dmax * hi_factor))
        return self._auto_temp_range

    def _apply_axis(self, ax, xs, ys):
        """按“轴设置”页配置约束横轴/纵轴范围与主刻度。原 MainWindow._apply_axis

        实时采集模式（活跃会话为 live）下，智能判断时采用全局趋势跟随：
          - 时间轴：固定从 0（采集开始）开始，右边界 = 最新点 + 30% 余量，
            随采集实时推进，整条趋势曲线始终完整可见；
          - 温度轴：统一智能模式，按「基础窗口（配置可调）+ 越界扩展」状态机
            自适应（见 _auto_temp_axis），避免温度小幅波动导致整图跳变。
        """
        s = self.mw.dataset
        if self.mw.ax_time_mode == "custom":
            # 整体趋势标签恒显示全量（用户确认的交互设计）：自定义时间范围
            # 只作用于其余趋势标签（前N分钟/单通道对比等）
            overview_ax = getattr(getattr(self.mw, "tab_all", None), "ax", None)
            if ax is not overview_ax:
                if self.mw.ax_time_min < self.mw.ax_time_max:
                    ax.set_xlim(self.mw.ax_time_min, self.mw.ax_time_max)
                if self.mw.ax_time_step > 0:
                    ax.xaxis.set_major_locator(MultipleLocator(self.mw.ax_time_step))
        else:
            xf = xs[np.isfinite(xs)]
            if xf.size:
                xmin = float(xf.min())
                xmax = float(xf.max())
                if xmin >= xmax:
                    xmax = xmin + 1.0
                span = xmax - xmin
                step = _nice_step(span, target_ticks=6)
                top = math.ceil(xmax / step) * step
                if getattr(s, "is_live", False):
                    # 实时全局趋势：横轴固定从 0 开始，右边界 = 最新点 + 30% 余量
                    xmin = 0.0
                    top = max(top, xmax + max(xmax, 1.0) * 0.3)
                ax.set_xlim(xmin, top)
        # 温度轴统一智能模式：基础窗口 + 越界扩展（见 _auto_temp_axis）
        self._apply_temp_axis(ax, ys)

    def _temp_axis_range(self, ys):
        """温度轴状态机 + nice 取整，返回 (bot, top, step)；无有效值返回 None。

        注意 _auto_temp_axis 状态机每次调用推进一帧（回缩计数、只扩不缩）
        ——dual 双区等一次刷新只允许调用一次本方法，结果同步应用到多个轴
        （见 _apply_shared_temp_axis），否则状态机会被双倍推进。
        """
        yf = ys[np.isfinite(ys)]
        if not yf.size:
            return None
        ymin = float(yf.min())
        ymax = float(yf.max())
        if ymin >= ymax:
            ymax = ymin + 1.0
        bot, top = self._auto_temp_axis(ymin, ymax)
        span = top - bot
        # 小范围尽量用 1℃ 递增（25、26…），范围过大再自动取 nice 步长
        step = (1.0 if span <= self.AUTO_TEMP_STEP1_MAX_SPAN
                else _nice_step(span, target_ticks=7))
        bot = math.floor(bot / step) * step
        top = math.ceil(top / step) * step
        return bot, top, step

    @staticmethod
    def _set_temp_axis_range(ax, bot, top, step) -> bool:
        """把温度轴范围与主刻度步长应用到 ax，返回 ylim 是否变化。"""
        lo, hi = ax.get_ylim()
        changed = bool((abs(lo - bot) > 1e-9) or (abs(hi - top) > 1e-9))
        ax.yaxis.set_major_locator(MultipleLocator(step))
        ax.set_ylim(bot, top)
        return changed

    def _apply_temp_axis(self, ax, ys) -> bool:
        """按温度轴智能模式状态机设置 ax 的 y 轴范围，返回 ylim 是否变化。

        与 _apply_axis 的温度轴段完全等价（_apply_axis 内部调用本方法），
        供「仅轴更新」轻量路径复用：曲线数据未变时只重算 y 轴范围，
        无需重建任何 artist（大文件 / 大数据量下避免整图重建卡顿）。
        dual 双区共享温度轴请用 _apply_shared_temp_axis（状态机只推进一次）。
        """
        rng = self._temp_axis_range(ys)
        if rng is None:
            return False
        return self._set_temp_axis_range(ax, *rng)

    def _apply_shared_temp_axis(self, axes, ys) -> bool:
        """把温度轴范围应用到多个轴：状态机每帧只推进一次、范围同步。

        dual 双区的左右两轴共用同一温度轴状态机结果（同一 ylim 与主
        刻度步长）；单轴传入 [ax] 时与 _apply_temp_axis 完全等价。
        """
        rng = self._temp_axis_range(ys)
        if rng is None:
            return False
        changed = False
        for ax in axes:
            changed = self._set_temp_axis_range(ax, *rng) or changed
        return changed

    # --------------------------------------------------------------- 多通道图和单通道对比

    def _plot_multi(self, tab, max_minutes=None, title=""):
        """绘制多通道整体趋势。原 MainWindow._plot_multi"""
        self._ensure_tab_layout(tab, "single")
        state = self._hover_artists(tab)
        last_event = state.get("last_event")
        overview_tab = getattr(self.mw, "tab_all", None)
        live_mode = self._normalized_live_view_mode()
        manual_range = (self._manual_display_range()
                        if live_mode == "manual" else None)
        self._clear_overview_hover(tab, draw=False)
        series = self.mw.channel_panel.visible_series(max_minutes) if self.mw.dataset else []
        series = self._prepare_live_series(series)
        # 整体趋势图缓存降采样后的 series，供鼠标悬停命中（画面实际显示的点）
        if tab is overview_tab:
            self._overview_hover_series[(id(tab), "single")] = series
        # 实时增量路径：无结构性变化时只更新线条数据 + 轴范围，跳过全量重建
        if self._try_incremental(tab, series):
            if (tab is overview_tab
                    and live_mode == "manual" and manual_range is not None):
                tab.ax.set_xlim(*manual_range)
            if (tab is overview_tab
                    and getattr(self.mw.dataset, "is_live", False)
                    and last_event is not None and last_event.inaxes is tab.ax):
                self._on_overview_mouse_move(last_event)
            self._draw_alarm_thresholds(tab)
            self.update_live_status_labels()
            return
        tab.clear()
        self._alarm_lines.pop((id(tab), "single"), None)   # 全量重建，阈值线缓存失效
        ax = tab.ax
        self._style_axes_canvas(ax)
        if series:
            norm, collected = self._draw_lines(
                ax, series, title=title, _collect=True,
                hovered_name=self._effective_hover_channel(tab))
            self._store_inc(tab, series, collected, norm)
        self._enforce_fixed_margins(tab.fig)
        self._draw_alarm_thresholds(tab)
        if (tab is overview_tab
                and live_mode == "manual" and manual_range is not None):
            ax.set_xlim(*manual_range)
        tab.canvas.draw_idle()
        if (tab is overview_tab
                and getattr(self.mw.dataset, "is_live", False)
                and last_event is not None and last_event.inaxes is tab.ax):
            self._on_overview_mouse_move(last_event)
        self.update_live_status_labels()

    # --------------------------------------------------------------- 双区视图（实时会话整体趋势页）

    def _ensure_tab_layout(self, tab, mode):
        """渲染前确保 tab 处于目标布局；布局与上次记录不一致时失效该页缓存。

        PlotTab.set_layout 切换会 clear() 重建坐标轴——旧 _live_inc artist、
        报警阈值线、悬停序列缓存都指向已销毁的轴，若不清空会被增量路径
        误复用（审计点名问题）；_tab_layout 记录上次渲染所见布局。
        """
        if tab.layout_mode != mode:
            tab.set_layout(mode)
        if self._tab_layout.get(id(tab)) != tab.layout_mode:
            for key in [k for k in self._live_inc if k[0] == id(tab)]:
                self._live_inc.pop(key, None)
            for key in [k for k in self._alarm_lines if k[0] == id(tab)]:
                self._alarm_lines.pop(key, None)
            for key in [k for k in self._overview_hover_series
                        if k[0] == id(tab)]:
                self._overview_hover_series.pop(key, None)
            if tab is getattr(self.mw, "tab_all", None):
                # 整体趋势页布局切换（single↔dual）后左区浏览窗失去参照；
                # 其余标签页首次渲染不得误清整体趋势页的浏览状态
                self._dual_left_xlim = None
                # 暂停冻结点与右轴待触发交互同样失去参照，恢复实时跟随
                self._dual_paused = False
                self._dual_hold_split = None
                self._dual_hold_t_end = None
                self._dual_right_press = None
            self._tab_layout[id(tab)] = tab.layout_mode

    def _dual_pane_active(self):
        """整体趋势页是否进入双区视图。

        条件：双区开关开启 + 活跃会话为实时采集（source=live，停止后仍
        保持双区便于回看）+ 自动跟随模式（手动浏览沿用单图路径，右区
        手动交互由后续批次接入）+ 总时长超过右窗宽度。
        配置属性经 getattr 防御读取：状态查询契约要求容忍未完整初始化
        的实例（__new__ 最小构造），缺失时视为不启用双区（沿用单图）。
        """
        if not getattr(self, "dual_view_enabled", False):
            return False
        session = self.mw.dataset
        if session is None or getattr(session, "source", "") != "live":
            return False
        if self._normalized_live_view_mode() == "manual":
            return False
        latest = self._latest_live_minute(session)
        # 配置以秒存储，时间轴数组为分钟：比较与切分前统一换算
        window_min = getattr(self, "live_window_sec", 60) / 60.0
        return (latest is not None
                and latest > window_min)

    def _dual_pane_series(self):
        """取双区数据：返回 (t_end, split, series_left, series_right)。

        左区取全量后按 SeriesSampler.window 截到 [0, split]（降采样上限
        live_max_points）；右区取 [split, 最新] 窗口（visible_series_window）
        再按窗口边界截取抽样——窗内点数通常远小于预算（1Hz×20min≈1200
        点），即保留原始密度不被抽稀。两窗均为闭区间、在 split 处各含
        端点，接缝曲线视觉连续。
        """
        raw = (self.mw.channel_panel.visible_series(None)
               if self.mw.dataset else [])
        t_end = None
        cleaned = []
        for name, x, v in raw:
            x = np.asarray(x, dtype=float)
            v = np.asarray(v, dtype=float)
            count = min(x.size, v.size)
            x, v = x[:count], v[:count]
            valid_time = self._valid_live_time_mask(x)
            x, v = x[valid_time], v[valid_time]
            if not x.size:
                continue
            cleaned.append((name, x, v))
            end = float(x.max())
            t_end = end if t_end is None else max(t_end, end)
        # 右区暂停跟随：t_end 用暂停时刻冻结值（split 随之冻结），
        # 新采样不改变画面；无冻结值时沿用实时最新点
        _hold_split, hold_t_end = self._dual_hold_values()
        if hold_t_end is not None:
            t_end = hold_t_end
        if t_end is None:
            return None, 0.0, [], []
        # 配置以秒存储，t_end 为分钟：切分点按分钟计算
        split = max(0.0, t_end - self.live_window_sec / 60.0)
        ls, le = self._dual_pane_left_window(split)
        raw_right = dict(
            (name, (x, v)) for name, x, v in
            (self.mw.channel_panel.visible_series_window(
                split, t_end if hold_t_end is not None else None)
             if self.mw.dataset else []))
        series_left = []
        series_right = []
        for name, x, v in cleaned:
            lx, lv = SeriesSampler.window(x, v, ls, le, self.live_max_points)
            if lx.size:
                series_left.append((name, lx, lv))
            rx, rv = raw_right.get(name, (np.array([]), np.array([])))
            # 右窗与左窗同一时间有效性标准（|x|≤1e12 有效、NaN 时间剔除），
            # 避免无效时间点经 visible_series_window 漏进右窗
            rx = np.asarray(rx, dtype=float)
            rv = np.asarray(rv, dtype=float)
            count_r = min(rx.size, rv.size)
            rx, rv = rx[:count_r], rv[:count_r]
            valid_time_r = self._valid_live_time_mask(rx)
            rx, rv = rx[valid_time_r], rv[valid_time_r]
            rx, rv = SeriesSampler.window(rx, rv, split, None,
                                          self.live_max_points)
            if rx.size:
                series_right.append((name, rx, rv))
        if ls is not None and not series_left:
            # 浏览窗内无任何采样点（如框选落在采样间隔之间）：放弃浏览窗
            # 回落全左窗，避免 _plot_dual_pane 因空左窗回退单图打断双区布局
            self._dual_left_xlim = None
            for name, x, v in cleaned:
                lx, lv = SeriesSampler.window(x, v, None, split,
                                              self.live_max_points)
                if lx.size:
                    series_left.append((name, lx, lv))
        return t_end, split, series_left, series_right

    def _plot_dual_pane(self, tab):
        """整体趋势页双区渲染：左轴=全历史，右轴=最近实时窗。

        左轴保留原「整体温度爬升趋势」标题与图例；右轴标题标注实时窗
        宽度、无图例、隐藏 Y 刻度标签，相邻侧 spine 以淡虚线分界。
        thermal 模式左右各画 LineCollection、共享同一 norm，colorbar
        挂右轴（边距固定后创建，避免 subplots_adjust 覆盖挤占位置）。
        增量路径对左右两组 artist 分别 set_data，任一不匹配整页重建。
        """
        self._ensure_tab_layout(tab, "dual")
        state = self._hover_artists(tab)
        last_event = state.get("last_event")
        self._clear_overview_hover(tab, draw=False)
        t_end, split, series_left, series_right = self._dual_pane_series()
        if split <= 0 or not series_left or not series_right:
            # 数据异常收缩（如通道全部离开右窗）：回退单图路径
            self._plot_multi(tab, None, "整体温度爬升趋势")
            return
        # 悬停命中按 (id(tab), pane) 缓存左右窗实际显示的序列
        self._overview_hover_series[(id(tab), "dual_left")] = series_left
        self._overview_hover_series[(id(tab), "dual_right")] = series_right
        ys_all = (np.concatenate([v for _, _, v in series_left + series_right])
                  if (series_left or series_right) else np.array([]))
        # 增量路径：左右两组 artist 分别 set_data，任一不匹配整页重建
        if (self._try_incremental(tab, series_left, "dual_left",
                                  apply_axis=False)
                and self._try_incremental(tab, series_right, "dual_right",
                                          apply_axis=False)):
            left_entry = self._live_inc.get((id(tab), "dual_left"))
            if left_entry is not None and left_entry.get("thermal"):
                # 共享 norm：左右 LineCollection 与 colorbar 同一引用，
                # 按合并数据更新一次即可全联动
                self._update_thermal_norm(left_entry.get("norm"), ys_all)
            self._dual_pane_apply_axes(tab, split, t_end, ys_all)
            self._draw_alarm_thresholds(tab, pane="dual_left")
            self._draw_alarm_thresholds(tab, pane="dual_right")
            self.update_live_status_labels()
            return
        tab.clear()
        for pane in ("dual_left", "dual_right"):
            self._alarm_lines.pop((id(tab), pane), None)
        thermal = self.mw.color_mode == "thermal"
        norm = self._dual_pane_norm(ys_all) if thermal else None
        hovered = self._effective_hover_channel(tab)
        norm_left, coll_left, smap, _xs, _ys = self._draw_series_on(
            tab.ax, series_left, title="整体温度爬升趋势",
            with_legend=True, norm=norm, hovered_name=hovered)
        # 右轴复用左轴 norm（thermal 时为共享对象；非 thermal 为 None）
        norm_right, coll_right, _smap_r, _xs, _ys = self._draw_series_on(
            tab.ax_right, series_right,
            title=f"实时 · 最近 {self.live_window_sec:g} 秒",
            with_legend=False, norm=norm_left, hovered_name=hovered)
        # 右辅轴：Y 刻度标签只在左轴，重复的 ylabel 一并去掉
        tab.ax_right.tick_params(labelleft=False)
        tab.ax_right.set_ylabel("")
        self._dual_pane_style_boundaries(tab)
        self._store_inc(tab, series_left, coll_left, norm_left,
                        pane="dual_left")
        self._store_inc(tab, series_right, coll_right, norm_right,
                        pane="dual_right")
        self._enforce_fixed_margins(tab.fig)
        # 边距固定后再挂共享 colorbar（挂右轴），避免 subplots_adjust
        # 覆盖 colorbar 挤占后的轴位置
        if thermal and smap is not None:
            self._attach_thermal_colorbar(tab.ax_right, smap)
        self._dual_pane_apply_axes(tab, split, t_end, ys_all)
        self._draw_alarm_thresholds(tab, pane="dual_left")
        self._draw_alarm_thresholds(tab, pane="dual_right")
        tab.canvas.draw_idle()
        if (getattr(self.mw.dataset, "is_live", False)
                and last_event is not None and last_event.inaxes is tab.ax):
            self._on_overview_mouse_move(last_event)
        self.update_live_status_labels()

    def _dual_pane_apply_axes(self, tab, split, t_end, ys_all):
        """dual 双轴范围：左=浏览窗或 [0, split]；右 [split, 右界]；温度轴共享。

        左区存在 `_dual_left_xlim` 浏览窗（左区滚轮/拖动/框选写入）时左轴
        用浏览窗，否则 [0, split]；右区始终 [split, 右界] 实时跟随。
        采集中（未停止）右界加约窗口 3-5% 的 nice 取整余量，避免新点
        贴边；已停止则冻结右界不再增长。X 刻度用 matplotlib 默认 nice
        步长按各自范围定位，标签统一 MM:SS / H:MM:SS（窄窗下刻度细到
        10 秒级，分钟小数不可读）。温度轴状态机每帧只推进一次，范围同步
        两轴。
        """
        right = float(t_end)
        session = self.mw.dataset
        if (getattr(session, "is_live", False)
                and getattr(session, "stopped_at", None) is None):
            window = max(right - split, 1e-9)
            margin = window * 0.04
            step = _nice_step(margin, target_ticks=1)
            right = math.ceil((right + margin) / step) * step
        ls, le = self._dual_pane_left_window(split)
        tab.ax.set_xlim(0.0 if ls is None else ls, le)
        tab.ax_right.set_xlim(float(split), right)
        # 每次幂等重设：整页重建（tab.clear 重置 formatter）与增量路径
        # （不清轴）都经本方法统一覆盖，两轴共用同一无状态 formatter。
        tick_fmt = FuncFormatter(ChartRenderer._format_axis_minutes)
        for ax in (tab.ax, tab.ax_right):
            ax.xaxis.set_major_formatter(tick_fmt)
        self._apply_shared_temp_axis(tab.axes, ys_all)

    def _dual_hold_values(self):
        """读取右区暂停冻结值 (split, t_end)（分钟）；未暂停或损坏返回 (None, None)。

        与 _dual_pane_left_window 同风格防御：容忍 __new__ 最小构造实例与
        非数值残留，损坏时按未暂停处理（回退实时 split/t_end）。
        """
        if not getattr(self, "_dual_paused", False):
            return None, None
        try:
            split = float(getattr(self, "_dual_hold_split", None))
            t_end = float(getattr(self, "_dual_hold_t_end", None))
        except (TypeError, ValueError, OverflowError):
            return None, None
        if not (np.isfinite(split) and np.isfinite(t_end)) or split < 0.0:
            return None, None
        return split, t_end

    def _dual_pane_pause_follow(self, tab):
        """右区手动操作（滚轮/左键拖动/右键框选释放）→ 暂停跟随（批 5，D4/D9）。

        记录当前 split/t_end 为冻结点，左右两区整体定格（新采样不再推进
        画面，左区浏览窗仍可在冻结 split 内浏览），启动 2 分钟自动恢复
        定时器并重渲染；已暂停或非 dual 激活态时为 no-op。
        """
        if not self._dual_pane_active() or getattr(self, "_dual_paused", False):
            return
        t_end, split, _, _ = self._dual_pane_series()
        if t_end is None or split <= 0.0:
            return
        self._dual_paused = True
        self._dual_hold_split = float(split)
        self._dual_hold_t_end = float(t_end)
        self._arm_auto_return()
        self._plot_dual_pane(tab)

    def _dual_pane_split(self):
        """双区切分点（分钟）= 最新点 − 实时窗宽，与 _dual_pane_series 一致。

        暂停跟随期间返回冻结切分点，保证左区浏览钳制与冻结画面一致。
        """
        hold_split, _hold_t_end = self._dual_hold_values()
        if hold_split is not None:
            return hold_split
        session = self.mw.dataset
        if session is None:
            return 0.0
        latest = self._latest_live_minute(session)
        if latest is None:
            return 0.0
        # 配置以秒存储，latest 为分钟：换算后求切分点
        return max(0.0, latest - getattr(self, "live_window_sec", 60) / 60.0)

    def _dual_pane_left_window(self, split):
        """左区取数/横轴窗口：浏览窗 override 或全左窗 (None, split)。

        浏览窗 (start, end) 由左区滚轮/拖动/框选写入 `_dual_left_xlim`，
        始终钳在 [0, split] 内；损坏值（非数值/倒置）防御性清空回落全左窗。
        """
        override = getattr(self, "_dual_left_xlim", None)
        if override is None:
            return None, split
        try:
            lo, hi = float(override[0]), float(override[1])
        except (TypeError, ValueError, IndexError, OverflowError):
            self._dual_left_xlim = None
            return None, split
        if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
            self._dual_left_xlim = None
            return None, split
        return max(lo, 0.0), min(hi, float(split))

    @staticmethod
    def _clamp_dual_left_xlim(left, right, split, keep_width=True):
        """双区左区浏览窗钳制：左界≥0（_clamp_xlim 语义）且右界≤split。"""
        left, right = ChartRenderer._clamp_xlim(left, right, keep_width)
        try:
            split = float(split)
        except (TypeError, ValueError, OverflowError):
            return left, right
        if np.isfinite(split) and split > 0.0 and right > split:
            right = split
        if left > right:
            left = right
        return left, right

    def _dual_pane_apply_left_xlim(self, xlim, tab):
        """记录左区浏览窗并重渲染双区；窗口未变化时跳过重复渲染。

        交互（滚轮/拖动/框选）统一入口：写入 `_dual_left_xlim`、复用既有
        `_arm_auto_return` 启动 2 分钟自动回归，再走 `_plot_dual_pane`
        （增量路径 set_data + 应用轴范围，右区逻辑不受影响）。
        结果窗口与跟随窗 [0, split] 重合时视为放弃浏览：跟随态下无事可做，
        浏览态下清除 override 恢复跟随——避免"缩小到满窗"反而冻结左区。
        """
        try:
            left, right = float(xlim[0]), float(xlim[1])
        except (TypeError, ValueError, IndexError, OverflowError):
            return
        if not (np.isfinite(left) and np.isfinite(right)) \
                or right - left <= 1e-9:
            return
        split = self._dual_pane_split()
        if split > 0.0 and left <= 1e-9 and right >= split - 1e-9:
            if self._dual_left_xlim is None:
                # 跟随态下缩放到满窗即跟随窗本身：无需进入浏览态
                return
            self._dual_left_xlim = None
            self._render_visible_tab()
            return
        new_xlim = (left, right)
        if self._dual_left_xlim == new_xlim:
            # 同一交互的重复触发（拖拽顶到边界等）：画面已是目标窗口
            return
        self._dual_left_xlim = new_xlim
        self._arm_auto_return()
        self._plot_dual_pane(tab)

    def _dual_pane_pan_left(self, delta, base, tab):
        """双区左区拖拽平移：按 press 基窗加位移平移浏览窗，钳在 [0, split]。"""
        split = self._dual_pane_split()
        left, right = float(base[0]), float(base[1])
        width = max(right - left, 0.0)
        if split > 0.0:
            width = min(width, split)
            new_left = min(max(left + delta, 0.0), split - width)
        else:
            new_left = max(left + delta, 0.0)
        self._dual_pane_apply_left_xlim((new_left, new_left + width), tab)

    @staticmethod
    def _dual_pane_style_boundaries(tab):
        """dual 双轴相邻侧 spine 画淡虚线，形成左右分界。"""
        for ax, side in ((tab.ax, "right"), (tab.ax_right, "left")):
            spine = ax.spines.get(side)
            if spine is None:
                continue
            spine.set_visible(True)
            spine.set_linestyle((0, (4, 4)))
            spine.set_linewidth(0.8)
            spine.set_color(Theme.PLOT_AXIS)

    @staticmethod
    def _dual_pane_norm(ys_all):
        """thermal 双区共享 norm：左右 LineCollection 与 colorbar 同一引用。"""
        yf = (ys_all[np.isfinite(ys_all)]
              if ys_all is not None else np.array([]))
        if not yf.size:
            return None
        vmin, vmax = float(yf.min()), float(yf.max())
        if vmax - vmin < 1e-9:
            vmax = vmin + 1.0
        return Normalize(vmin, vmax)

    # --------------------------------------------------------------- 实时增量绘制

    def _store_inc(self, tab, series, collected, norm=None, pane="single"):
        """记录某标签页某 pane 当前绘制的通道与 artist，供增量更新复用。"""
        self._live_inc[(id(tab), pane)] = {
            "key": self._live_key(series, pane),
            "artists": collected,
            "norm": norm,
            "thermal": self.mw.color_mode == "thermal",
        }

    def _live_key(self, series, pane="single"):
        """增量缓存键：配色模式 + 通道名集合 + 配色指纹 + 布局/窗口指纹。

        配色指纹（color_mode + palette）保证切换调色板方案时缓存失效，
        强制全量重绘，曲线颜色随新方案更新；实时追加数据时指纹不变，
        照常走 set_data 增量。pane 角色与 live_window_sec 加入指纹：
        双区布局切换或右窗宽度调整后旧 artist 必须全量重建。
        """
        cfg = store.config
        pal = tuple(cfg.palette or []) if cfg is not None else ()
        colors = tuple(
            str(self.mw.channel_panel.color_of(name)).lower()
            for name, _, _ in series
        )
        overview_tab = getattr(self.mw, "tab_all", None)
        hovered = (self._effective_hover_channel(overview_tab)
                   if overview_tab is not None
                   else getattr(self.mw.channel_panel,
                                "_hover_channel_name", None))
        return (self.mw.color_mode, tuple(name for name, _, _ in series),
                (cfg.color_mode if cfg is not None else "", pal), colors,
                hovered,
                pane, self.live_window_sec)

    def _try_incremental(self, tab, series, pane="single", apply_axis=True):
        """实时采集模式下尝试增量更新已存在曲线。

        满足条件（活跃会话为 live、通道集合未变）时，复用已创建的
        Line2D / LineCollection 只更新数据、颜色映射与轴范围，返回 True；
        否则返回 False 走全量重建分支。
        pane="single"（默认）为单图路径；"dual_left"/"dual_right" 为双区
        左右轴的独立增量缓存。apply_axis=False 时不套用通用轴逻辑，
        由 dual 调用方统一处理横轴范围与共享温度轴（含共享 norm 更新）。
        """
        s = self.mw.dataset
        if s is None or not getattr(s, "is_live", False):
            return False
        key = self._live_key(series, pane)
        cached = self._live_inc.get((id(tab), pane))
        if (cached is None or cached["key"] != key
                or len(cached["artists"]) != len(series)):
            return False
        ax = self._pane_axis(tab, pane)
        xs_all = []
        ys_all = []
        for (name, x, v), (_cname, artist) in zip(series, cached["artists"]):
            if artist.axes is not ax:
                return False
            # thermal 模式：数据从单点(Line2D)升级到多点时需要 LineCollection
            # 分段着色，类型不匹配 → 回退全量重建（一次性，之后保持增量）
            if cached.get("thermal") and isinstance(artist, Line2D) and len(x) >= 2:
                return False
            self._update_artist(artist, x, v)
            # 报警高亮：categorical(Line2D) 模式下按报警状态着色；thermal 首版不高亮
            if not cached.get("thermal") and isinstance(artist, Line2D):
                artist.set_color(
                    Theme.RED if name in self._alarm_channels
                    else self.mw.channel_panel.color_of(name))
            xs_all.append(x)
            ys_all.append(v)
        if xs_all:
            xs = np.concatenate(xs_all)
            ys = np.concatenate(ys_all)
            if apply_axis:
                if cached.get("thermal"):
                    # 热力图：颜色映射范围随数据扩展，colorbar 同步
                    self._update_thermal_norm(cached.get("norm"), ys)
                self._apply_axis(ax, xs, ys)
        tab.canvas.draw_idle()
        return True

    def _update_thermal_norm(self, norm, ys):
        """热力图模式增量：把 norm 的 vmin/vmax 更新到当前数据范围。

        LineCollection / colorbar 与 norm 是同一对象引用，改 norm 即可让
        颜色映射与色条随数据增长而扩展，无需重建。
        """
        if norm is None:
            return
        yf = ys[np.isfinite(ys)]
        if not yf.size:
            return
        vmin, vmax = float(yf.min()), float(yf.max())
        if vmax - vmin < 1e-9:
            vmax = vmin + 1.0
        norm.vmin, norm.vmax = vmin, vmax

    @staticmethod
    def _update_artist(artist, x, v):
        """增量更新单个 artist 的曲线数据（Line2D.set_data / LineCollection 兜底）。"""
        if isinstance(artist, Line2D):
            artist.set_data(x, v)
        elif len(x) >= 2:
            pts = np.column_stack([x, v])
            segs = np.concatenate([pts[:-1, None, :], pts[1:, None, :]], axis=1)
            artist.set_segments(segs)
            artist.set_array(v)

    def _plot_single(self, *_):
        """通道对比标签：所有勾选的通道画在同一张图。原 MainWindow._plot_single"""
        tab = self.mw.tab_single
        names = self.mw._checked_compare_names()
        if not names:
            tab.clear()
            ax = tab.ax
            self._style_axes_canvas(ax)
            ax.text(0.5, 0.5, "请在上方勾选要对比的通道", transform=ax.transAxes,
                    ha="center", va="center", fontsize=12, color=Theme.PLOT_TEXT)
            self._enforce_fixed_margins(tab.fig)
            tab.canvas.draw_idle()
            return
        series = []
        for nm in names:
            series += self.mw.channel_panel.visible_series(None, only=nm)
        # 实时增量路径：勾选通道集合未变时只更新数据 + 轴范围
        if self._try_incremental(tab, series):
            return
        tab.clear()
        ax = tab.ax
        if series:
            norm, collected = self._draw_lines(
                ax, series, _collect=True,
                hovered_name=self._effective_hover_channel(tab))
            self._store_inc(tab, series, collected, norm)
        self._enforce_fixed_margins(tab.fig)
        tab.canvas.draw_idle()

    # --------------------------------------------------------------- 组合图

    def _plot_combo(self):
        """实时绘制 A4 三图组合（1+2 布局）。原 MainWindow._plot_combo"""
        tab = self.mw.tab_combo
        fig = tab.fig
        fig.clear()
        s1a, s1b = self.mw.a4_custom1
        s2a, s2b = self.mw.a4_custom2
        t1 = f"二、{_fmt_range(s1a, s1b)}"
        t2 = f"三、{_fmt_range(s2a, s2b)}"
        panels = [
            ("一、整体温度爬升趋势", None, None),
            (t1, s1a, s1b),
            (t2, s2a, s2b),
        ]
        # 布局代次递增：完整重绘后强制边距缓存重测（数据/字体/DPI 变化时）
        self._combo_layout_gen += 1
        gs = fig.add_gridspec(2, 2, hspace=0.25, wspace=0.15)
        axes = [
            fig.add_subplot(gs[0, :]),
            fig.add_subplot(gs[1, 0]),
            fig.add_subplot(gs[1, 1]),
        ]
        for i, (title, smin, emax) in enumerate(panels):
            ax = axes[i]
            if i == 0:
                self._draw_segment(ax, smin, emax, title, mark=False,
                                   fonts=self.COMBO_FONTS, with_legend=True,
                                   with_cbar=True, cbar_pad=0.03)
                if self.mw.a4_mark:
                    self._draw_combo_boundaries(
                        ax, self.mw.a4_custom1, self.mw.a4_custom2)
                self.mw._combo_cbar_ax = fig.axes[-1] if len(fig.axes) > 3 else None
            else:
                self._draw_segment(ax, smin, emax, title, mark=False,
                                   fonts=self.COMBO_FONTS, with_legend=False,
                                   title_loc="center",
                                   with_cbar=False)
        self._relayout_combo(fig)
        tab.canvas.draw_idle()

    def _draw_combo_frame(self, fig):
        """在 3 个子图外缘画细线虚线外框。原 MainWindow._draw_combo_frame"""
        from matplotlib.patches import Rectangle
        from matplotlib.transforms import Bbox
        if len(fig.axes) < 3:
            return
        # Exclude the colorbar axis: the frame belongs to the three plots,
        # not to the auxiliary scale on the right.
        total = Bbox.union([a.get_position() for a in fig.axes[:3]])
        # Keep the stroke visible when the union touches the canvas edge.
        inset = 1.0 / (fig.dpi * fig.get_size_inches()[0])
        r = Rectangle((total.x0 + inset, total.y0 + inset),
                      max(0, total.width - 2 * inset),
                      max(0, total.height - 2 * inset),
                      transform=fig.transFigure, fill=False,
                      # 外框落在深画布底面上，走画布轴键 PLOT_AXIS
                      edgecolor=Theme.PLOT_AXIS, linewidth=0.6, linestyle=(0, (4, 2)))
        r._combo_frame = True
        r.set_clip_on(False)
        fig.add_artist(r)

    def _on_combo_resize(self, event):
        """组合图标签页窗口缩放时重排布局。原 MainWindow._on_combo_resize"""
        if self.mw.tabs.currentIndex() != self.mw.idx_combo:
            return
        fig = self.mw.tab_combo.fig
        try:
            self._combo_relayouting = True
            changed = self._relayout_combo(fig)
        finally:
            self._combo_relayouting = False
        if changed:
            self.mw.tab_combo.canvas.draw_idle()

    def _on_combo_draw(self, event):
        """首帧/任意绘制后用真实渲染尺寸重排组合图布局。原 MainWindow._on_combo_draw

        文字实测仅在完整重绘（_plot_combo）后的首帧绘制执行一次并缓存，
        后续绘制 / resize 复用缓存边距（边距不随窗口大小改变，也无测量反馈振荡）。
        """
        if self.mw.tabs.currentIndex() != self.mw.idx_combo:
            return
        fig = self.mw.tab_combo.fig
        renderer = getattr(event, "renderer", None)
        if self._relayout_combo(fig, renderer=renderer):
            self.mw.tab_combo.canvas.draw_idle()

    @classmethod
    def _combo_text_margins(cls, fig, renderer, width_px, height_px,
                            ovr=None):
        """返回组合图边距 (left, right, top, bottom, gap_h, gap_v)。

        边距 = max(配置值 × DPI 缩放, 文字实际延伸 + PAD)。
        - DPI 缩放：文字 px 随 DPI 增长，边距按 dpi/100 同步缩放 → 任何
          DPI 下文字不溢出边距、导出（300dpi）与前端（100dpi）比例一致；
        - 有 renderer 时叠加文字实测（测量值为文字相对轴边缘的延伸量，
          与轴绝对位置无关），保证极端文字也不重叠；调用方（_relayout_combo）
          负责缓存实测结果，避免测量-布局反馈振荡。
        """
        axes = fig.axes[:3]
        ovr = ovr or {}
        dpi_scale = max(0.25, float(fig.dpi) / 100.0)
        # 文字安全余量随 DPI 缩放：与边距基线同一比例，保证任何 DPI 下
        # 前端（100dpi）与导出（300dpi）的"基线 vs 实测"判定一致
        PAD = 14.0 * dpi_scale

        def _cfg(key, default):
            return (ovr.get(key, default) if ovr else default) * dpi_scale

        left = _cfg("margin_left", cls.COMBO_MARGIN_LEFT)
        right = _cfg("margin_right", cls.COMBO_MARGIN_RIGHT)
        top = _cfg("margin_top", cls.COMBO_MARGIN_TOP)
        bottom = _cfg("margin_bottom", cls.COMBO_MARGIN_BOTTOM)
        gap_h = _cfg("gap_h", cls.COMBO_GAP_H)
        gap_v = _cfg("gap_v", cls.COMBO_GAP_V)
        if renderer is None:
            return left, right, top, bottom, gap_h, gap_v

        def _ext(item):
            try:
                bb = item.get_window_extent(renderer)
            except Exception:
                return None
            return bb if (bb.width or bb.height) else None

        def _left_ext(which):
            m = 0.0
            for ax in which:
                pos = ax.get_position()
                for item in [ax.yaxis.label, *ax.get_yticklabels()]:
                    bb = _ext(item)
                    if bb is not None:
                        m = max(m, pos.x0 * width_px - bb.x0)
            return m

        left_needed = _left_ext(axes)
        # 中央列间隔：仅右侧下方子图（axes[2]）的 Y 轴文字向左延伸进间隔
        gap_h_needed = _left_ext(axes[2:])

        bottom_needed = 0.0
        top_needed = 0.0
        gap_v_needed = 0.0
        for ax in axes:
            pos = ax.get_position()
            for item in [ax.xaxis.label, *ax.get_xticklabels()]:
                bb = _ext(item)
                if bb is not None:
                    downward = pos.y0 * height_px - bb.y1
                    bottom_needed = max(bottom_needed, downward)
                    # 顶部子图(ax0)的 X 轴文字向下延伸进上下间隔
                    if ax is axes[0]:
                        gap_v_needed = max(gap_v_needed, downward)
            if ax.get_title():
                bb = _ext(ax.title)
                if bb is not None:
                    upward = bb.y1 - pos.y1 * height_px
                    if ax is axes[0]:
                        # 顶部标题 → 上边距
                        top_needed = max(top_needed, upward)
                    else:
                        # 下方两图的标题向上延伸进上下间隔（防与上排重叠）
                        gap_v_needed = max(gap_v_needed, upward)

        left = max(left, left_needed + PAD)
        top = max(top, top_needed + PAD)
        bottom = max(bottom, bottom_needed + PAD)
        gap_h = max(gap_h, gap_h_needed + PAD)
        gap_v = max(gap_v, gap_v_needed + PAD)
        return left, right, top, bottom, gap_h, gap_v

    def _relayout_combo(self, fig, renderer=None):
        """组合图网格布局（1+2）：像素精确计算 → 直接设置各轴位置。原 MainWindow._relayout_combo

        - 边距：配置 × DPI 缩放 + 文字实测（完整重绘后首帧测量并缓存，
          resize / 后续 draw 复用 → 边距不随窗口大小改变、无测量反馈振荡）；
        - 幂等：figure 尺寸与画布一致时不再 set_size_inches（该调用会触发
          canvas resize → resize_event → _on_combo_resize 的反馈路径）；
        - 位置未变返回 False（不触发 draw_idle），配合重入保护杜绝
          resize/draw 事件链式循环（此前 WIP 在离屏切换标签页时崩溃）。
        """
        if getattr(self, "_combo_relayouting", False):
            return False
        canvas = fig.canvas
        if canvas is None:
            return False
        cw = canvas.width()
        ch = canvas.height()
        if cw <= 0 or ch <= 0:
            return False
        w_px, h_px = float(cw), float(ch)
        size_in = fig.get_size_inches()
        if (abs(size_in[0] - w_px / fig.dpi) > 1e-9
                or abs(size_in[1] - h_px / fig.dpi) > 1e-9):
            fig.set_size_inches(w_px / fig.dpi, h_px / fig.dpi)

        ovr = getattr(self.mw, '_layout_ovr', {}) or {}
        key = (fig.dpi, self._combo_layout_gen)
        if self._combo_margin_key != key:
            # 完整重绘（_plot_combo 递增布局代次）后重新计算；
            # renderer=None 时为纯配置基线，首帧绘制事件再升级为文字实测。
            self._combo_margin_cache = self._combo_text_margins(
                fig, renderer, w_px, h_px, ovr=ovr)
            self._combo_margin_key = key
            self._combo_margin_measured = renderer is not None
        elif renderer is not None and not self._combo_margin_measured:
            # 首帧绘制事件叠加文字实测（一次性，之后固定不再随 draw 变化）
            self._combo_margin_cache = self._combo_text_margins(
                fig, renderer, w_px, h_px, ovr=ovr)
            self._combo_margin_measured = True
        LEFT, RIGHT, TOP, BOTTOM, GAP_H, GAP_V = self._combo_margin_cache

        MIN_W  = self.COMBO_CELL_MIN_W
        MIN_H  = self.COMBO_CELL_MIN_H

        avail_w = max(2 * MIN_W, w_px - LEFT - RIGHT - GAP_H)
        avail_h = max(2 * MIN_H, h_px - TOP - BOTTOM - GAP_V)
        cell_w = avail_w / 2.0
        cell_h = avail_h / 2.0

        left_f   = LEFT / w_px
        right_f  = 1.0 - RIGHT / w_px
        top_f    = 1.0 - TOP / h_px
        bottom_f = BOTTOM / h_px
        cw_f     = cell_w / w_px
        ch_f     = cell_h / h_px
        gap_h_f  = GAP_H / w_px
        gap_v_f  = GAP_V / h_px

        axes = fig.axes
        if len(axes) < 3:
            return False

        CBAR_RESERVE_PX = ovr.get("cbar_reserve", 80)
        if CBAR_RESERVE_PX > w_px * 0.25:
            CBAR_RESERVE_PX = w_px * 0.15

        cbar_f = CBAR_RESERVE_PX / w_px

        # Keep every axes inside the same safe rectangle. The bottom-right
        # plot therefore cannot run past the frame or canvas edge.
        safe_right = right_f
        combo_positions = self.combo_layout_positions(
            w_px, h_px, left=LEFT, right=RIGHT, top=TOP, bottom=BOTTOM,
            gap_h=GAP_H, gap_v=GAP_V)
        new_pos = [
            [left_f, bottom_f + ch_f + gap_v_f,
             right_f - left_f - cbar_f, ch_f],
            combo_positions[1],
            combo_positions[2],
        ]

        eps = 1e-6
        same = True
        for i, pos in enumerate(new_pos):
            cur = axes[i].get_position().bounds
            if any(abs(a - b) > eps for a, b in zip(pos, cur)):
                same = False
                break
        if same:
            return False

        for i, pos in enumerate(new_pos):
            axes[i].set_position(pos)

        cbar_ax = getattr(self.mw, '_combo_cbar_ax', None)
        if cbar_ax is not None and cbar_ax in fig.axes:
            ax0 = axes[0]
            ax0_pos = ax0.get_position()
            cbar_w = 12.0 / w_px
            cbar_gap = 10.0 / w_px
            cbar_x = ax0_pos.x1 + cbar_gap
            cbar_y = ax0_pos.y0
            cbar_h = ax0_pos.height
            if cbar_x + cbar_w > right_f:
                cbar_x = max(1e-3, right_f - cbar_w - 2e-3)
            cbar_ax.set_position([cbar_x, cbar_y, cbar_w, cbar_h])
        return True

    @classmethod
    def combo_min_canvas_size(cls):
        """返回组合图画布最小尺寸（像素）。原 MainWindow.combo_min_canvas_size"""
        mw = (cls.COMBO_MARGIN_LEFT + cls.COMBO_MARGIN_RIGHT
              + cls.COMBO_GAP_H + 2 * cls.COMBO_CELL_MIN_W)
        mh = (cls.COMBO_MARGIN_TOP + cls.COMBO_MARGIN_BOTTOM
              + cls.COMBO_GAP_V + 2 * cls.COMBO_CELL_MIN_H)
        return int(mw), int(mh)

    @classmethod
    def combo_layout_positions(cls, width_px, height_px, *, left=None,
                               right=None, top=None, bottom=None,
                               gap_h=None, gap_v=None):
        """Return the three subplot rectangles within the combo safe frame."""
        left = float(cls.COMBO_MARGIN_LEFT if left is None else left)
        right = float(cls.COMBO_MARGIN_RIGHT if right is None else right)
        top = float(cls.COMBO_MARGIN_TOP if top is None else top)
        bottom = float(cls.COMBO_MARGIN_BOTTOM if bottom is None else bottom)
        gap_h = float(cls.COMBO_GAP_H if gap_h is None else gap_h)
        gap_v = float(cls.COMBO_GAP_V if gap_v is None else gap_v)

        # 最小画布下不能继续强行使用固定最小子图尺寸，否则两个下方子图
        # 会互相覆盖。优先保留边界和间距；空间不足时允许子图缩小，
        # 但始终保证两个子图之间不重叠、且不越过安全边界。
        width_px = max(float(width_px), 1.0)
        height_px = max(float(height_px), 1.0)
        left = max(0.0, min(left, width_px - 1.0))
        right = max(0.0, min(right, width_px - left - 1.0))
        top = max(0.0, min(top, height_px - 1.0))
        bottom = max(0.0, min(bottom, height_px - top - 1.0))

        gap_h = min(max(0.0, gap_h), max(0.0, width_px - left - right))
        gap_v = min(max(0.0, gap_v), max(0.0, height_px - top - bottom))
        available_w = max(1.0, width_px - left - right - gap_h)
        available_h = max(1.0, height_px - top - bottom - gap_v)
        if available_w < 2.0 * cls.COMBO_CELL_MIN_W:
            gap_h = min(gap_h, max(0.0, width_px - left - right
                                   - 2.0 * cls.COMBO_CELL_MIN_W))
            available_w = max(1.0, width_px - left - right - gap_h)
        if available_h < 2.0 * cls.COMBO_CELL_MIN_H:
            gap_v = min(gap_v, max(0.0, height_px - top - bottom
                                   - 2.0 * cls.COMBO_CELL_MIN_H))
            available_h = max(1.0, height_px - top - bottom - gap_v)

        cell_w = available_w / 2.0
        cell_h = available_h / 2.0
        left_f = left / width_px
        right_f = 1.0 - right / width_px
        bottom_f = bottom / height_px
        cell_w_f = cell_w / width_px
        cell_h_f = cell_h / height_px
        gap_h_f = gap_h / width_px
        gap_v_f = gap_v / height_px
        right_x = left_f + cell_w_f + gap_h_f
        right_width_f = max(0.0, right_f - right_x)
        return [
            [left_f, bottom_f + cell_h_f + gap_v_f,
             right_f - left_f, cell_h_f],
            [left_f, bottom_f, cell_w_f, cell_h_f],
            [right_x, bottom_f, right_width_f, cell_h_f],
        ]

    def _enforce_fixed_margins(self, fig):
        """对所有使用 tight_layout() 的视图强制执行固定像素边距。原 MainWindow._enforce_fixed_margins"""
        w_in, h_in = fig.get_size_inches()
        w_px, h_px = w_in * fig.dpi, h_in * fig.dpi
        if w_px <= 0 or h_px <= 0:
            return
        left, bottom, right, top = self.fixed_margin_fractions(w_px, h_px)
        fig.subplots_adjust(left=left, bottom=bottom, right=right, top=top)

    @classmethod
    def fixed_margin_fractions(cls, width_px, height_px):
        """Convert the fixed chart margins from pixels to figure fractions."""
        # Preserve the designed pixel margins on normal/large canvases, but
        # reduce them proportionally when the right canvas is near its minimum
        # width so axis labels remain inside the available drawing area.
        # Keep the same pixel baseline at every window size. Only the axes'
        # content rectangle grows or shrinks with the canvas.
        left_px = cls.FIXED_MARGIN_LEFT
        bottom_px = cls.FIXED_MARGIN_BOTTOM
        right_px = cls.FIXED_MARGIN_RIGHT
        top_px = cls.FIXED_MARGIN_TOP
        return (
            left_px / width_px,
            bottom_px / height_px,
            1.0 - right_px / width_px,
            1.0 - top_px / height_px,
        )

    @staticmethod
    def title_style(width_px):
        """Use one title baseline; the top margin reserves its safe area."""
        return 11, 8

    # --------------------------------------------------------------- 通道对比勾选框刷新

    def _refresh_single_combo(self):
        """刷新通道对比标签的勾选框列表。原 MainWindow._refresh_single_combo"""
        from ui.widgets.toggle_switch import ToggleSwitch
        from ui.widgets.compare_panel import style_compare_toggle
        if not hasattr(self.mw, "chk_compare_lay") or self.mw.dataset is None:
            return
        while self.mw.chk_compare_lay.count() > 1:
            it = self.mw.chk_compare_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        # 找出温度最高和最低的通道索引
        channels = self.mw.dataset.channels
        max_idx, min_idx = self._find_max_min_temp_channels(channels)
        for i, c in enumerate(channels):
            chk = ToggleSwitch(c.display_name)
            # 默认勾选最高和最低温度的通道
            chk.setChecked(i == max_idx or i == min_idx)
            # 勾选文字是界面骨架直排文字：按当前主题补偿，浅色主题配浅底胶囊
            style_compare_toggle(chk, c.color)
            chk.toggled.connect(self._plot_single)
            self.mw.chk_compare_lay.insertWidget(self.mw.chk_compare_lay.count() - 1, chk)

    def _find_max_min_temp_channels(self, channels):
        """找出温度最高和最低的通道索引。

        用通道的最高温度作为比较基准，找出整体温度最高的通道；
        用通道的最低温度作为比较基准，找出整体温度最低的通道。
        这样用户切换到单通道对比时，能一眼看到最高温与最低温曲线的对比。
        """
        max_temp = -float('inf')
        min_temp = float('inf')
        max_idx = 0
        min_idx = 0

        for i, ch in enumerate(channels):
            result = self.mw.pipeline.get(self.mw.dataset, ch)
            if result is None:
                continue
            t_pv, v_pv, _ = result
            if v_pv.size == 0:
                continue
            valid = v_pv[np.isfinite(v_pv)]
            if valid.size == 0:
                continue
            ch_max = valid.max()
            ch_min = valid.min()
            # 用通道的最高温度作为比较基准
            if ch_max > max_temp:
                max_temp = ch_max
                max_idx = i
            # 用通道的最低温度作为比较基准
            if ch_min < min_temp:
                min_temp = ch_min
                min_idx = i

        return max_idx, min_idx

    # --------------------------------------------------------------- 绘图段（供组合图 / 导出使用）

    def _draw_segment(self, ax, start_min, end_min, title, mark=True,
                      fonts=None, with_legend=True, with_cbar=True,
                      cbar_pad=0.03, title_loc="left"):
        """在给定 ax 上绘制指定时间区间的所有可见通道曲线。原 MainWindow._draw_segment"""
        series = self.mw.channel_panel.visible_series_window(start_min, end_min)
        norm = self._draw_lines(ax, series, title=title, with_legend=with_legend,
                                with_cbar=with_cbar, fonts=fonts, cbar_pad=cbar_pad,
                                title_loc=title_loc)
        self._bound_combo_temperature_ticks(ax)
        if start_min is not None or end_min is not None:
            self._apply_combo_segment_axis(ax, start_min, end_min, series)
        if mark:
            session = self.mw.dataset
            for name, x, v in series:
                ch = session.channel_by_label(name) if session else None
                if ch is None:
                    continue
                t_pv, v_pv, _ = self.mw.pipeline.get(session, ch)
                t0 = t_pv[0] if t_pv.size else 0.0
                result = self.mw.stat_panel.result_for_channel(ch)
                for point in (result.fast_to_slow_sec, result.slow_to_steady_sec):
                    if point is None or not np.isfinite(point):
                        continue
                    px = (point - t0) / 60.0
                    lo = start_min if start_min is not None else x.min()
                    hi = end_min if end_min is not None else x.max()
                    if lo <= px <= hi:
                        col = (THERMAL_CMAP(norm(v[np.argmax(v)]))
                               if norm is not None else self.mw.channel_panel.color_of(name))
                        ax.axvline(px, color=col, linestyle=":", alpha=0.5, linewidth=1.0)

    @staticmethod
    def _apply_combo_segment_axis(ax, start_min, end_min, series=None):
        """把组合图分段横轴固定到配置的绝对分钟范围。

        ``_draw_lines`` 会套用普通趋势图的实时轴逻辑（从 0 起），因此
        组合图下排必须在曲线绘制后重新应用自己的窗口。结束值为空时，
        使用该分段实际数据的最大分钟数作为右界。
        """
        def _finite_value(value):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return None
            return value if np.isfinite(value) else None

        xs = []
        for item in series or ():
            if len(item) < 2:
                continue
            values = np.asarray(item[1], dtype=float)
            finite = values[np.isfinite(values)]
            if finite.size:
                xs.append(finite)
        if not xs:
            for line in ax.lines:
                values = np.asarray(line.get_xdata(), dtype=float)
                finite = values[np.isfinite(values)]
                if finite.size:
                    xs.append(finite)
        data_min = float(np.min(np.concatenate(xs))) if xs else None
        data_max = float(np.max(np.concatenate(xs))) if xs else None
        lo = _finite_value(start_min)
        hi = _finite_value(end_min)
        if lo is None:
            lo = data_min
        if hi is None:
            hi = data_max
        if lo is None or hi is None:
            return
        if hi < lo:
            lo, hi = hi, lo
        if hi <= lo:
            hi = lo + 1.0
        span = hi - lo
        step = _nice_step(span, target_ticks=6)
        ticks = np.arange(lo, hi + step * 0.5, step, dtype=float)
        if ticks.size == 0 or ticks[-1] < hi - 1e-9:
            ticks = np.append(ticks, hi)
        else:
            ticks[-1] = min(ticks[-1], hi)
        ax.set_xlim(lo, hi)
        ax.xaxis.set_major_locator(FixedLocator(ticks))

    @staticmethod
    def _bound_combo_temperature_ticks(ax):
        """限制组合图温度主刻度在当前 y 轴范围内。"""
        if not (ax.lines or ax.collections):
            return
        lo, hi = (float(value) for value in ax.get_ylim())
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            return
        locator = ax.yaxis.get_major_locator()
        step = getattr(locator, "_base", None)
        try:
            step = float(step)
        except (TypeError, ValueError):
            step = _nice_step(hi - lo, target_ticks=7)
        if not np.isfinite(step) or step <= 0:
            step = _nice_step(hi - lo, target_ticks=7)
        bounded = np.arange(lo, hi + step * 0.5, step, dtype=float)
        bounded = bounded[bounded <= hi + 1e-9]
        if bounded.size == 0 or bounded[-1] < hi - 1e-9:
            bounded = np.append(bounded, hi)
        ax.yaxis.set_major_locator(FixedLocator(bounded))

    @staticmethod
    def _draw_combo_boundaries(ax, custom1, custom2):
        """在整体趋势图上标出两个自定义时间段的结束位置。"""
        for custom in (custom1, custom2):
            end_min = custom[1]
            if end_min is not None and np.isfinite(end_min):
                # 分界线画在深画布底面上，走画布轴键 PLOT_AXIS
                ax.axvline(float(end_min), color=Theme.PLOT_AXIS,
                           linestyle=":", alpha=0.7, linewidth=1.0)

    def draw_combo_panels(self, fig, fonts=None):
        """在已创建好 1+2 GridSpec 三个 axes 的 fig 上绘制组合图三面板。
        原 MainWindow._draw_combo_panels"""
        s1a, s1b = self.mw.a4_custom1
        s2a, s2b = self.mw.a4_custom2
        t1 = f"二、{_fmt_range(s1a, s1b)}"
        t2 = f"三、{_fmt_range(s2a, s2b)}"
        panels = [
            ("一、整体温度爬升趋势", None, None),
            (t1, s1a, s1b),
            (t2, s2a, s2b),
        ]
        for i, (title, smin, emax) in enumerate(panels):
            ax = fig.axes[i]
            if i == 0:
                self._draw_segment(ax, smin, emax, title, mark=False,
                                   fonts=fonts, with_cbar=True, cbar_pad=0.03)
                if self.mw.a4_mark:
                    self._draw_combo_boundaries(
                        ax, self.mw.a4_custom1, self.mw.a4_custom2)
            else:
                self._draw_segment(ax, smin, emax, title, mark=False,
                                   fonts=fonts, with_cbar=False)


# 辅助函数 —— 对齐 app.py 顶层 _fmt_win_int
def _fmt_win_int(w):
    """把窗口分钟数格式化为标签页标签。"""
    try:
        v = float(w)
        return str(int(v)) if v == int(v) else f"{v:g}"
    except (TypeError, ValueError):
        return str(w)
