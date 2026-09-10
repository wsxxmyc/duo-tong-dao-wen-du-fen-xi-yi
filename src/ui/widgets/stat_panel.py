# -*- coding: utf-8 -*-
"""
StatPanel — 温升统计面板组件。
从 app.py 提取 MainWindow 统计相关方法，通过 self.mw 引用 MainWindow。
"""
import numpy as np
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QWidget, QTableWidgetItem, QVBoxLayout, QLabel,
    QAbstractItemView,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

from utils import core
from ui.theme import Theme


class StatPanel(QWidget):
    """温升统计标签页：三阶段温升参数表格与通道统计。"""
    STAT_MAX_ROWS = 8   # 统计表格最多显示 8 行（通道）

    def __init__(self, mw, view_host=None):
        super().__init__()
        self.mw = mw  # MainWindow 引用
        self._result_cache = {}
        self._selected_channel_index = 0  # 记录刷新后需要保持的表格行
        self._phase_legend = None
        self._phase_legend_entries = []
        self._phase_layout_in_progress = False
        self._view_host = view_host or self
        self._build_rise_view()

    # ---- 统计核心 ----

    def _build_rise_view(self):
        """创建统计页的汇总表和单通道阶段状态图。"""
        root = QVBoxLayout(self._view_host)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        self.table = self.mw.tbl_stat
        self.table.setColumnCount(13)
        self.table.setHorizontalHeaderLabels([
            "通道", "状态", "起始温度", "快速转慢", "慢转稳态",
            "快段时长", "慢段时长", "稳态时长", "快段速率",
            "慢段速率", "稳态均值", "稳态波动", "剔除点数",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)
        root.addWidget(self.table)

        self.phase_status_label = QLabel("点击上方通道行查看阶段状态")
        self.phase_status_label.setStyleSheet(f"color:{Theme.TEXT_MUTED};")
        root.addWidget(self.phase_status_label)

        # 画布组两分区：相位图整幅归 PLOT_FACE，不借用骨架色 BG_CARD
        self.phase_figure = Figure(figsize=(8, 3.4), dpi=Theme.screen_dpi(),
                                   facecolor=Theme.PLOT_FACE)
        self.phase_canvas = FigureCanvas(self.phase_figure)
        # 图例必须属于曲线坐标轴，不能再占用曲线下方的独立区域。
        # 这样窗口缩小时，Matplotlib 可以在坐标轴内部重新寻找空白位置，
        # 图例也不会被 Qt 画布底部裁掉。
        self.phase_ax = self.phase_figure.add_axes([0.10, 0.15, 0.86, 0.73])
        self.phase_canvas.mpl_connect("resize_event", self._on_phase_resize)
        root.addWidget(self.phase_canvas, 1)

    def clear_result_cache(self) -> None:
        """清除当前会话的分析结果缓存。"""
        self._result_cache.clear()

    def _cache_key(self, channel):
        config = self.mw.rise_config.normalized()
        return (
            getattr(self.mw.dataset, "id", id(self.mw.dataset)),
            int(channel.index),
            int(getattr(self.mw.dataset, "n", 0)),
            config.filter_window_sec,
            config.steady_duration_sec,
            config.slope_decay_sensitivity,
        )

    def result_for_channel(self, channel) -> "core.RiseAnalysisResult":
        """读取当前 Session 的处理数据并缓存单通道分析结果。"""
        if self.mw.dataset is None or channel is None:
            return core.analyze_rise([], [], self.mw.rise_config)
        key = self._cache_key(channel)
        if key not in self._result_cache:
            processed = self.mw.pipeline.get(self.mw.dataset, channel)
            if not processed:
                result = core.analyze_rise([], [], self.mw.rise_config)
            else:
                result = core.analyze_rise(
                    processed[0], processed[1], self.mw.rise_config)
            self._result_cache[key] = result
        return self._result_cache[key]

    @staticmethod
    def _time_text(seconds):
        return core.sec_to_hms(seconds) if seconds is not None else "—"

    @staticmethod
    def _metric_text(metrics, key, digits=1):
        value = (metrics or {}).get(key)
        if value is None or not np.isfinite(value):
            return "—"
        return f"{value:.{digits}f}"

    def _row_values(self, channel, result, removed_count):
        status = {
            "steady_confirmed": "稳态已确认",
            "steady_unconfirmed": "稳态未确认",
            "heating": "升温分析中",
            "insufficient_data": "数据不足",
        }.get(result.status, result.status)
        fast, slow, steady = result.fast_metrics, result.slow_metrics, result.steady_metrics
        return [
            channel.display_name, status,
            self._metric_text({"v": result.start_temperature}, "v"),
            self._time_text(result.fast_to_slow_sec),
            self._time_text(result.slow_to_steady_sec),
            self._metric_text(fast, "duration_sec"),
            self._metric_text(slow, "duration_sec"),
            self._metric_text(steady, "duration_sec"),
            self._metric_text(fast, "average_rate_per_min", 3),
            self._metric_text(slow, "average_rate_per_min", 3),
            self._metric_text(steady, "mean"),
            self._metric_text(steady, "fluctuation"),
            str(removed_count),
        ]

    def _is_rise_tab_visible(self):
        """判断温升页是否可见；测试夹具未提供标签页时按可见处理。"""
        tabs = getattr(self.mw, "tabs", None)
        stat_index = getattr(self.mw, "idx_stat", None)
        if tabs is None or stat_index is None:
            return True
        try:
            return int(tabs.currentIndex()) == int(stat_index)
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return False

    def update_stats(self) -> None:
        """更新温升三阶段汇总表和下方状态图。"""
        rise_tab_visible = self._is_rise_tab_visible()
        self.mw.tbl_stat.setRowCount(0)
        if self.mw.dataset is None:
            if rise_tab_visible:
                self._clear_phase_plot("暂无数据")
            return
        channels_to_show = self.mw.dataset.channels
        for c in channels_to_show:
            res = self.mw.pipeline.get(self.mw.dataset, c)
            t, v, mask = res if res else (None, None, None)
            row = self.mw.tbl_stat.rowCount()
            self.mw.tbl_stat.insertRow(row)
            if t is None or self.mw.dataset.is_open_circuit(c):
                result = core.analyze_rise([], [], self.mw.rise_config)
                vals = self._row_values(c, result, 0)
            else:
                result = self.result_for_channel(c)
                vals = self._row_values(c, result, int(mask.sum()))
            for i, val in enumerate(vals):
                it = QTableWidgetItem(str(val))
                if i == 0:
                    it.setForeground(QColor(c.color))
                self.mw.tbl_stat.setItem(row, i, it)
        self.fit_table()
        if channels_to_show:
            if not rise_tab_visible:
                return
            selected_index = min(
                self._selected_channel_index, len(channels_to_show) - 1)
            self.mw.tbl_stat.selectRow(selected_index)
        else:
            if rise_tab_visible:
                self._clear_phase_plot("暂无通道数据")

    def _on_table_selection_changed(self):
        if not self._is_rise_tab_visible():
            return
        rows = self.mw.tbl_stat.selectionModel().selectedRows()
        if rows:
            self.update_selected_channel(rows[0].row())

    def update_selected_channel(self, index) -> None:
        """切换下方阶段状态图的通道。"""
        if self.mw.dataset is None or not self.mw.dataset.channels:
            self._clear_phase_plot("暂无数据")
            return
        index = max(0, min(int(index), len(self.mw.dataset.channels) - 1))
        self._selected_channel_index = index
        channel = self.mw.dataset.channels[index]
        result = self.result_for_channel(channel)
        self._draw_phase_plot(channel, result)

    def _clear_phase_plot(self, message):
        self.phase_ax.clear()
        self.phase_figure._suptitle = None
        self._phase_legend = None
        self._phase_legend_entries = []
        self.phase_ax.text(0.5, 0.5, message, transform=self.phase_ax.transAxes,
                           ha="center", va="center", color=Theme.PLOT_TEXT,
                           fontfamily=Theme.font("plot"))
        self._layout_phase_figure()
        self._draw_phase_canvas()

    def _draw_phase_canvas(self):
        """只在统计图可见且尺寸有效时同步绘制，避免隐藏画布生命周期崩溃。"""
        if not self.phase_canvas.isVisible():
            return
        if self.phase_canvas.width() <= 0 or self.phase_canvas.height() <= 0:
            return
        try:
            self.phase_canvas.draw()
        except RuntimeError:
            # Qt 控件正在销毁时不再触发二次绘制，保留当前图元即可。
            return

    def _phase_legend_columns(self, handles, labels):
        """按当前画布宽度选择能容纳图例的列数。"""
        if not labels:
            return 1
        canvas_width = max(int(self.phase_canvas.width()), 1)
        axes_width = canvas_width * self.phase_ax.get_position().width
        available_width = max(axes_width - 24, 120)
        # 只用字体大小估算文本宽度，不在布局计算中调用 draw()。
        # 统计图可能在实时会话切换信号中刷新，此时再次同步绘制会造成
        # Qt/Matplotlib 重入，Windows 下可能直接触发原生崩溃。
        char_width = max(float(self.phase_figure.dpi) * 8.0 / 72.0 * 0.85, 7.0)
        entry_widths = [max(42.0, len(label) * char_width + 24.0)
                        for label in labels]
        for columns in range(len(labels), 0, -1):
            rows = int(np.ceil(len(labels) / columns))
            column_widths = []
            for column in range(columns):
                column_entries = entry_widths[
                    column * rows:min((column + 1) * rows, len(entry_widths))]
                if column_entries:
                    column_widths.append(max(column_entries))
            total_width = sum(column_widths) + max(columns - 1, 0) * 16.0
            if total_width <= available_width:
                return columns
        return 1

    def _layout_phase_figure(self):
        """重新计算曲线区，并让图例在曲线坐标轴内部智能避让。"""
        if self._phase_layout_in_progress:
            return
        self._phase_layout_in_progress = True
        try:
            handles, labels = self._phase_legend_entries
            self.phase_ax.set_position([0.10, 0.15, 0.86, 0.73])
            if not labels:
                return

            columns = self._phase_legend_columns(handles, labels)
            # 图例始终挂在曲线坐标轴上。列数只根据当前宽度决定，
            # loc="best" 再根据曲线实际占用区域选择相对空白的位置。
            self._phase_legend = self.phase_ax.legend(
                handles, labels, loc="best",
                ncol=columns, fontsize=7.0, frameon=True,
                framealpha=0.82, facecolor=Theme.PLOT_FACE,
                edgecolor=Theme.BORDER, borderaxespad=0.0,
                borderpad=0.35, labelspacing=0.3,
                handlelength=1.2, handletextpad=0.4, columnspacing=0.9)
            self._phase_legend.set_in_layout(False)
            for legend_text in self._phase_legend.get_texts():
                legend_text.set_color(Theme.PLOT_TEXT)
                legend_text.set_fontfamily(Theme.font("plot"))
        finally:
            self._phase_layout_in_progress = False

    def _on_phase_resize(self, _event):
        """窗口变化后重新换列并计算图例、曲线和标题边界。"""
        if self._phase_legend_entries:
            self._layout_phase_figure()

    def _draw_phase_plot(self, channel, result):
        self.phase_ax.clear()
        if result.time_sec.size == 0:
            self._clear_phase_plot(result.diagnostic or "暂无有效数据")
            return
        x = (result.time_sec - result.time_sec[0]) / 60.0
        self.phase_ax.plot(x, result.raw_values, color=Theme.TEXT_MUTED,
                           alpha=0.35, linewidth=0.8, label="原始曲线")
        self.phase_ax.plot(x, result.smoothed_values, color=channel.color,
                           linewidth=1.8, label="平滑曲线")
        colors = {
            core.PHASE_FAST: "#e76f51",
            core.PHASE_SLOW: "#e9c46a",
            core.PHASE_STEADY: "#2a9d8f",
        }
        labels = {
            core.PHASE_FAST: "快速升温",
            core.PHASE_SLOW: "缓慢升温",
            core.PHASE_STEADY: "热稳态",
        }
        for phase, color in colors.items():
            mask = result.phase_labels == phase
            if not mask.any():
                continue
            start = np.where(mask)[0][0]
            end = np.where(mask)[0][-1]
            self.phase_ax.axvspan(x[start], x[end], color=color, alpha=0.12,
                                  label=labels[phase])
        for point, text, color in (
            (result.fast_to_slow_sec, "快→慢", "#f4a261"),
            (result.slow_to_steady_sec, "慢→稳", "#2a9d8f"),
        ):
            if point is None:
                continue
            px = (point - result.time_sec[0]) / 60.0
            py = np.interp(point, result.time_sec, result.smoothed_values)
            # 相位标记色随画布明暗补偿：深画布原色即达标，浅画布（净白精工）
            # 自动加深保持色相——线/点/文字三处同源
            shown = Theme.readable_text(color, Theme.PLOT_FACE)
            self.phase_ax.axvline(px, color=shown, linestyle="--", linewidth=1.0)
            self.phase_ax.scatter([px], [py], color=shown, s=28, zorder=5)
            self.phase_ax.annotate(text, (px, py), xytext=(5, 8),
                                   textcoords="offset points", color=shown,
                                   fontfamily=Theme.font("plot"))
        status = {
            "steady_confirmed": "稳态已确认",
            "steady_unconfirmed": "稳态未确认",
            "heating": "升温分析中",
            "insufficient_data": "数据不足",
        }.get(result.status, result.status)
        self.phase_status_label.setText(
            f"{channel.display_name}：{status}"
            + (f"，{result.diagnostic}" if result.diagnostic else ""))
        self.phase_ax.set_title("")
        self.phase_figure.suptitle(
            f"{channel.display_name} 温升阶段状态",
            y=0.97, fontsize=11, color=Theme.PLOT_TEXT,
            fontfamily=Theme.font("plot"))
        self.phase_ax.set_xlabel("时间（分钟）", fontfamily=Theme.font("plot"))
        self.phase_ax.set_ylabel("温度（℃）", fontfamily=Theme.font("plot"))
        self.phase_ax.grid(True, linestyle="--", alpha=0.3)
        self._phase_legend_entries = self.phase_ax.get_legend_handles_labels()
        self._layout_phase_figure()
        self._draw_phase_canvas()

    def apply_plot_theme(self) -> None:
        """按当前主题刷新温升统计图的已有文字和坐标轴颜色。"""
        # 画布组两分区：figure 与绘图区底面统一归 PLOT_FACE
        self.phase_figure.set_facecolor(Theme.PLOT_FACE)
        self.phase_ax.set_facecolor(Theme.PLOT_FACE)
        self.phase_ax.tick_params(colors=Theme.PLOT_TEXT)
        self.phase_ax.xaxis.label.set_color(Theme.PLOT_TEXT)
        self.phase_ax.yaxis.label.set_color(Theme.PLOT_TEXT)
        self.phase_ax.title.set_color(Theme.PLOT_TEXT)
        for spine in self.phase_ax.spines.values():
            spine.set_color(Theme.BORDER)
        for grid_line in (self.phase_ax.get_xgridlines()
                          + self.phase_ax.get_ygridlines()):
            grid_line.set_color(Theme.GRID)
        if self.phase_figure._suptitle is not None:
            self.phase_figure._suptitle.set_color(Theme.PLOT_TEXT)
        if self._phase_legend is not None:
            for legend_text in self._phase_legend.get_texts():
                legend_text.set_color(Theme.PLOT_TEXT)
        # 阶段状态提示为构造时固化的局部样式，随主题一并重放
        # （部分单测用 SimpleNamespace 桩构造面板，属性可能缺失）
        status_label = getattr(self, "phase_status_label", None)
        if status_label is not None:
            status_label.setStyleSheet(f"color:{Theme.TEXT_MUTED};")
        self.phase_canvas.draw_idle()

    def fit_table(self) -> None:
        """统计表格按实际内容高度定高。原 MainWindow._fit_stat_table"""
        vh = self.mw.tbl_stat.verticalHeader()
        hh = self.mw.tbl_stat.horizontalHeader()
        rows_h = vh.length()
        total = hh.height() + rows_h + 2 * self.mw.tbl_stat.frameWidth()
        self.mw.tbl_stat.setFixedHeight(int(total))
