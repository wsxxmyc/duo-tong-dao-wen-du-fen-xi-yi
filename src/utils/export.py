# -*- coding: utf-8 -*-
"""
导出管理器 —— 从 app.py 提取的图表/数据/报告导出方法。
所有方法均为实例方法，接收 main_window 参数以访问主窗口状态。
"""

import os
os.environ["QT_API"] = "pyqt5"
import datetime as dt

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import LinearSegmentedColormap

from PyQt5.QtWidgets import QMessageBox, QFileDialog

from .helpers import _fmt_win_int, _fmt_range, _sanitize_filename
from .config_io import ConfigIO

from utils import core
from ui.theme import Theme
from chart.chart_renderer import ChartRenderer


_EXPORT_THEME_NOT_SET = object()

# 温度色谱常量（与 app.py 定义一致）
THERMAL_CMAP = LinearSegmentedColormap.from_list(
    "温度色谱",
    ["#eaf6fb", "#bcd9ec", "#7fb3d5", "#f4d35e", "#ee9b3f",
     "#d9534f", "#7b1e1e"],
    N=256)


class ExportManager:
    """导出功能管理器：封装画布截图、批量PNG、数据表、HTML报告、A4组合图等导出操作。"""

    # PNG 导出使用印刷级分辨率，避免文字、曲线和坐标轴放大后发虚。
    PNG_EXPORT_DPI = 300
    REPORT_PNG_DPI = 300
    _REPORT_PALETTES = {
        "light": {
            "figure": "#ffffff", "axes": "#ffffff", "text": "#111827",
            "border": "#94a3b8", "grid": "#dbe3ee",
        },
        "dark": {
            "figure": "#12161c", "axes": "#12161c", "text": "#f2f5f8",
            "border": "#64748b", "grid": "#263142",
        },
    }

    EXPORT_TASKS = (
        {"id": "current_canvas", "name": "当前画布图", "extension": ".png"},
        {"id": "overview_all", "name": "整体趋势图", "extension": ".png"},
        {"id": "overview_10", "name": "前10分钟图", "extension": ".png"},
        {"id": "overview_20", "name": "前20分钟图", "extension": ".png"},
        {"id": "overview_30", "name": "前30分钟图", "extension": ".png"},
        {"id": "a4_combo", "name": "A4组合图", "extension": ".png"},
        {"id": "excel_data", "name": "Excel数据表", "extension": ".xlsx"},
    )

    @classmethod
    def available_tasks(cls):
        """返回导出弹窗使用的固定任务定义。"""
        return [dict(task) for task in cls.EXPORT_TASKS]

    @staticmethod
    def export_theme_options():
        """返回导出主题下拉选项，首项表示跟随当前界面主题。"""
        return [("跟随当前界面主题", None)] + [
            (theme_name, theme_key)
            for theme_key, theme_name in Theme.THEME_NAMES.items()
        ]

    def __init__(self, main_window):
        self._mw = main_window
        self.image_background = "light"
        self.image_format = "png"
        self._export_task_handlers = {
            "current_canvas": self._export_current_canvas_file,
            "overview_all": self._export_overview_file,
            "overview_10": self._export_overview_file,
            "overview_20": self._export_overview_file,
            "overview_30": self._export_overview_file,
            "a4_combo": self._export_a4_file,
            "excel_data": self._export_excel_file,
        }

    def export_selected(self, task_ids, directory, remember_path=False,
                        image_background="light", image_format="png",
                        export_theme=_EXPORT_THEME_NOT_SET, session_name=None):
        """按固定顺序导出选中任务；单项失败时继续执行后续任务。

        所有任务统一写进 ``directory`` 下自动新建的
        「会话名_YYYYMMDD_HHMMSS」子文件夹（results["folder"]）；
        remember_path 只记住用户所选父目录，避免下次导出文件夹套娃。
        """
        selected = set(task_ids or [])
        self.image_background = self._normalize_background(image_background)
        self.image_format = self._normalize_image_format(image_format)
        results = {"successes": [], "failures": [], "folder": None}
        if not directory or not os.path.isdir(directory):
            return {"successes": [], "failures": [{
                "task_id": "path", "name": "导出路径", "error": "导出目录不存在"
            }], "folder": None}
        if remember_path:
            ConfigIO.save_last_export_dir(directory)
        try:
            folder = self._prepare_export_folder(directory, session_name)
        except OSError as exc:
            return {"successes": [], "failures": [{
                "task_id": "path", "name": "导出路径",
                "error": f"创建导出子文件夹失败：{exc}",
            }], "folder": None}
        results["folder"] = folder
        for task in self.EXPORT_TASKS:
            if task["id"] not in selected:
                continue
            try:
                if export_theme is _EXPORT_THEME_NOT_SET:
                    path = self._export_task_file(folder, task)
                else:
                    path = self._export_task_file(folder, task, export_theme)
                results["successes"].append({
                    "task_id": task["id"], "name": task["name"], "path": path,
                })
            except Exception as exc:
                results["failures"].append({
                    "task_id": task["id"], "name": task["name"], "error": str(exc),
                })
        return results

    def _export_task_file(self, directory, task, export_theme=_EXPORT_THEME_NOT_SET):
        """执行一个导出任务并返回生成文件路径。"""
        handler = self._export_task_handlers.get(task["id"])
        if handler is not None:
            if export_theme is _EXPORT_THEME_NOT_SET:
                return handler(directory, task)
            return handler(directory, task, export_theme)
        raise NotImplementedError(f"导出任务尚未接入：{task['id']}")

    @staticmethod
    def resolve_export_theme(export_theme=None):
        """解析导出主题，不改变当前界面主题。"""
        if export_theme is None:
            return Theme.active()
        resolver = getattr(Theme, "_resolve_theme_name", None)
        if callable(resolver):
            return resolver(export_theme)
        if export_theme in Theme._PALETTES:
            return export_theme
        return Theme.active()

    def export_palette(self, export_theme=None):
        """返回本次导出的调色板副本，避免修改主题注册表。"""
        theme_name = self.resolve_export_theme(export_theme)
        return dict(Theme._PALETTES[theme_name])

    def _new_export_figure(self, export_theme=None, **kwargs):
        """创建使用导出主题背景的新图，不激活全局界面主题。

        口径：屏上画布与独立趋势 PNG = 整幅画布、底色随主题（figure 归 PLOT_FACE；
        净白精工为浅底深字例外），
        A4 报告 = 浅纸面 + 深色嵌图（走 _apply_report_style，勿在此混用）。
        """
        palette = self.export_palette(export_theme)
        kwargs.setdefault("facecolor", palette["PLOT_FACE"])
        return Figure(**kwargs)

    def _apply_export_palette(self, figure, export_theme=None):
        """把导出调色板应用到图、坐标轴和已有文字图元。"""
        if figure is None:
            raise ValueError("缺少待应用主题的图表")
        palette = self.export_palette(export_theme)
        # 独立趋势 PNG 与屏上画布同口径：figure 整幅画布（PLOT_FACE，随主题明暗）
        figure.set_facecolor(palette["PLOT_FACE"])
        figure.set_edgecolor(palette["PLOT_FACE"])
        for axes in figure.axes:
            axes.set_facecolor(palette["PLOT_FACE"])
            axes.tick_params(
                colors=palette["PLOT_TEXT"], labelsize=Theme.PLOT_TICK_SIZE)
            axes.xaxis.label.set_color(palette["PLOT_TEXT"])
            axes.yaxis.label.set_color(palette["PLOT_TEXT"])
            axes.xaxis.label.set_fontfamily(Theme.font("plot"))
            axes.yaxis.label.set_fontfamily(Theme.font("plot"))
            axes.xaxis.label.set_fontsize(Theme.PLOT_LABEL_SIZE)
            axes.yaxis.label.set_fontsize(Theme.PLOT_LABEL_SIZE)
            for tick in axes.get_xticklabels() + axes.get_yticklabels():
                tick.set_color(palette["PLOT_TEXT"])
                tick.set_fontfamily(Theme.font("mono"))
            for line in axes.xaxis.get_ticklines() + axes.yaxis.get_ticklines():
                line.set_color(palette["PLOT_TEXT"])
            for title in (axes.title, axes._left_title, axes._right_title):
                title.set_color(palette["PLOT_TEXT"])
                title.set_fontfamily(Theme.font("plot"))
                title.set_fontweight(Theme.PLOT_TITLE_WEIGHT)
            for text in axes.texts:
                text.set_color(palette["PLOT_TEXT"])
                text.set_fontfamily(Theme.font("text"))
            for spine in axes.spines.values():
                spine.set_color(palette["PLOT_AXIS"])
            for grid_line in axes.get_xgridlines() + axes.get_ygridlines():
                grid_line.set_color(palette["PLOT_GRID"])
            legend = axes.get_legend()
            if legend is not None:
                for text in legend.get_texts():
                    text.set_color(palette["PLOT_TEXT"])
                    text.set_fontfamily(Theme.font("plot"))
                legend.get_frame().set_facecolor(palette["PLOT_FACE"])
                legend.get_frame().set_edgecolor(palette["PLOT_AXIS"])
        return palette

    @classmethod
    def _normalize_background(cls, background):
        return background if background in cls._REPORT_PALETTES else "light"

    @staticmethod
    def _normalize_image_format(image_format):
        """图片文件导出仅支持 PNG（矢量图选项已按需求移除）。"""
        return "png"

    @staticmethod
    def _image_extension(image_format):
        return ".png"

    @classmethod
    def _report_colors(cls, background):
        return dict(cls._REPORT_PALETTES[cls._normalize_background(background)])

    def _apply_report_style(self, figure, background):
        """将独立报告样式应用到新建图或临时导出的当前画布。"""
        colors = self._report_colors(background)
        figure.set_facecolor(colors["figure"])
        for axes in figure.axes:
            axes.set_facecolor(colors["axes"])
            axes.tick_params(colors=colors["text"], labelsize=Theme.PLOT_TICK_SIZE)
            axes.xaxis.label.set_color(colors["text"])
            axes.yaxis.label.set_color(colors["text"])
            axes.xaxis.label.set_fontfamily(Theme.font("plot"))
            axes.yaxis.label.set_fontfamily(Theme.font("plot"))
            axes.xaxis.label.set_fontsize(Theme.PLOT_LABEL_SIZE)
            axes.yaxis.label.set_fontsize(Theme.PLOT_LABEL_SIZE)
            for tick in axes.get_xticklabels() + axes.get_yticklabels():
                tick.set_fontfamily(Theme.font("mono"))
                tick.set_color(colors["text"])
            for spine in axes.spines.values():
                spine.set_color(colors["border"])
            for grid_line in axes.get_xgridlines() + axes.get_ygridlines():
                grid_line.set_color(colors["grid"])
            for title in (axes.title, axes._left_title, axes._right_title):
                title.set_color(colors["text"])
                title.set_fontfamily(Theme.font("plot"))
                title.set_fontweight(Theme.PLOT_TITLE_WEIGHT)
            legend = axes.get_legend()
            if legend is not None:
                for text in legend.get_texts():
                    text.set_color(colors["text"])
                    text.set_fontfamily(Theme.font("plot"))
        return colors

    @staticmethod
    def _capture_figure_style(figure):
        style = {
            "figure": figure.get_facecolor(),
            "figure_edge": figure.get_edgecolor(),
            "axes": [],
        }
        for axes in figure.axes:
            style["axes"].append({
                "axes": axes,
                "facecolor": axes.get_facecolor(),
                "x_label": (axes.xaxis.label.get_color(),
                            axes.xaxis.label.get_fontfamily(),
                            axes.xaxis.label.get_fontsize()),
                "y_label": (axes.yaxis.label.get_color(),
                            axes.yaxis.label.get_fontfamily(),
                            axes.yaxis.label.get_fontsize()),
                "titles": [(title, title.get_color(), title.get_fontfamily(),
                            title.get_fontweight())
                           for title in (axes.title, axes._left_title, axes._right_title)],
                "ticks": [(tick, tick.get_color(), tick.get_fontfamily(),
                           tick.get_fontsize())
                          for tick in axes.get_xticklabels() + axes.get_yticklabels()],
                "tick_lines": [(line, line.get_color())
                               for line in axes.xaxis.get_ticklines()
                               + axes.yaxis.get_ticklines()],
                "texts": [(text, text.get_color(), text.get_fontfamily())
                          for text in axes.texts],
                "spines": [(spine, spine.get_edgecolor())
                           for spine in axes.spines.values()],
                "grid": [(line, line.get_color())
                         for line in axes.get_xgridlines() + axes.get_ygridlines()],
                "legend": [(text, text.get_color(), text.get_fontfamily())
                           for text in (axes.get_legend().get_texts()
                                        if axes.get_legend() is not None else [])],
                "legend_frame": (
                    axes.get_legend().get_frame().get_facecolor(),
                    axes.get_legend().get_frame().get_edgecolor(),
                ) if axes.get_legend() is not None else None,
            })
        return style

    @staticmethod
    def _restore_figure_style(style):
        style["figure_ref"].set_facecolor(style["figure"])
        style["figure_ref"].set_edgecolor(style["figure_edge"])
        for item in style["axes"]:
            axes = item["axes"]
            axes.set_facecolor(item["facecolor"])
            axes.xaxis.label.set_color(item["x_label"][0])
            axes.xaxis.label.set_fontfamily(item["x_label"][1])
            axes.xaxis.label.set_fontsize(item["x_label"][2])
            axes.yaxis.label.set_color(item["y_label"][0])
            axes.yaxis.label.set_fontfamily(item["y_label"][1])
            axes.yaxis.label.set_fontsize(item["y_label"][2])
            for title, color, family, weight in item["titles"]:
                title.set_color(color)
                title.set_fontfamily(family)
                title.set_fontweight(weight)
            for tick, color, family, size in item["ticks"]:
                tick.set_color(color)
                tick.set_fontfamily(family)
                tick.set_fontsize(size)
            for line, color in item["tick_lines"]:
                line.set_color(color)
            for text, color, family in item["texts"]:
                text.set_color(color)
                text.set_fontfamily(family)
            for spine, color in item["spines"]:
                spine.set_color(color)
            for line, color in item["grid"]:
                line.set_color(color)
            for text, color, family in item["legend"]:
                text.set_color(color)
                text.set_fontfamily(family)
            legend = axes.get_legend()
            if legend is not None and item["legend_frame"] is not None:
                legend.get_frame().set_facecolor(item["legend_frame"][0])
                legend.get_frame().set_edgecolor(item["legend_frame"][1])

    def save_report_figure(self, figure, path, background="light",
                           image_format="png",
                           export_theme=_EXPORT_THEME_NOT_SET):
        """以报告规格输出 PNG 图片（固定 300 DPI；矢量图选项已移除）。"""
        if figure is None or not path:
            raise ValueError("缺少待导出的图表或保存路径")
        style = self._capture_figure_style(figure)
        style["figure_ref"] = figure
        try:
            if export_theme is _EXPORT_THEME_NOT_SET:
                colors = self._apply_report_style(figure, background)
                figure_color = colors["figure"]
            else:
                palette = self._apply_export_palette(figure, export_theme)
                # 独立趋势 PNG=整幅画布（底色随主题，与屏上口径一致）；A4 报告走上方纸面分支
                figure_color = palette["PLOT_FACE"]
            figure.savefig(path, dpi=self.REPORT_PNG_DPI, format="png",
                           facecolor=figure_color, edgecolor=figure_color)
        finally:
            self._restore_figure_style(style)

    def _save_figure_with_palette(self, figure, path, export_theme=None,
                                  image_format="png"):
        """使用指定导出主题保存图片，并恢复原画布的颜色与字体。"""
        self.save_report_figure(
            figure, path, image_format=image_format,
            export_theme=export_theme)

    def _prepare_export_folder(self, directory, session_name=None):
        """在实际写盘目录下创建「会话名_YYYYMMDD_HHMMSS」子文件夹并返回路径。

        session_name 为 None 时自动取当前会话名（``store.active.title``，
        导入文件=文件名、采集=自定义或自动名）；无会话或清洗后为空则回落
        「未命名」。同一秒内重复导出时 exist_ok 合并复用同一文件夹，
        文件级防覆盖由 _unique_path 的序号后缀保证。
        """
        if session_name is None:
            session = getattr(self._mw, "dataset", None)
            session_name = getattr(session, "title", "") or ""
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = os.path.join(
            directory, f"{_sanitize_filename(session_name)}_{stamp}")
        os.makedirs(folder, exist_ok=True)
        return folder

    @staticmethod
    def _unique_path(directory, name, extension, timestamp):
        """生成不覆盖已有文件的导出路径。"""
        base = os.path.join(directory, f"{name}_{timestamp}{extension}")
        if not os.path.exists(base):
            return base
        index = 2
        while True:
            path = os.path.join(directory, f"{name}_{timestamp}_{index}{extension}")
            if not os.path.exists(path):
                return path
            index += 1

    def _export_current_canvas_file(self, directory, task,
                                    export_theme=_EXPORT_THEME_NOT_SET):
        mw = self._mw
        fig, label = self._current_canvas_figure()
        if fig is None:
            raise RuntimeError("当前没有可导出的画布")
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        extension = self._image_extension(self.image_format)
        path = self._unique_path(directory, "当前画布图", extension, timestamp)
        if export_theme is _EXPORT_THEME_NOT_SET:
            self.save_report_figure(
                fig, path, self.image_background, self.image_format)
        else:
            self.save_report_figure(
                fig, path, image_format=self.image_format,
                export_theme=export_theme)
        return path

    def _current_canvas_figure(self):
        mw = self._mw
        idx = mw.tabs.currentIndex()
        if idx == mw.idx_stat:
            idx = 0
        tab_widget = mw.tabs.widget(idx)
        canvas = tab_widget.findChild(FigureCanvas) if hasattr(tab_widget, "findChild") else None
        fig = canvas.figure if canvas is not None else None
        if fig is None and idx == mw.idx_single:
            fig = mw.tab_single.fig
        return fig, mw.tabs.tabText(idx) if fig is not None else ""

    def _export_overview_file(self, directory, task,
                              export_theme=_EXPORT_THEME_NOT_SET):
        mw = self._mw
        windows = {
            "overview_all": ("整体趋势图", None),
            "overview_10": ("前10分钟图", mw.win_front[0]),
            "overview_20": ("前20分钟图", mw.win_front[1]),
            "overview_30": ("前30分钟图", mw.win_front[2]),
        }
        name, max_minutes = windows[task["id"]]
        figure_kwargs = {
            "show_stats": (max_minutes is None or
                            bool(getattr(mw, "show_window_stats", True)))
        }
        if export_theme is not _EXPORT_THEME_NOT_SET:
            figure_kwargs["export_theme"] = export_theme
        fig = self._build_overview_figure(max_minutes, name, **figure_kwargs)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        extension = self._image_extension(self.image_format)
        path = self._unique_path(directory, name, extension, timestamp)
        if export_theme is _EXPORT_THEME_NOT_SET:
            self.save_report_figure(
                fig, path, self.image_background, self.image_format)
        else:
            self.save_report_figure(
                fig, path, image_format=self.image_format,
                export_theme=export_theme)
        return path

    def _build_overview_figure(self, max_minutes=None, title="整体趋势图",
                               show_stats=True, export_theme=None):
        """生成趋势导出图；整体趋势固定带统计栏，窗口图由设置控制。"""
        mw = self._mw
        channels = list(mw.dataset.visible_channels()) if mw.dataset else []
        show_stats = bool(show_stats) or max_minutes is None
        palette = self.export_palette(export_theme)
        if not show_stats:
            fig = self._new_export_figure(
                export_theme, figsize=(9, 5), dpi=self.PNG_EXPORT_DPI)
            trend_ax = fig.add_subplot(111)
            mw._draw_lines(trend_ax, mw._visible_series(max_minutes),
                           title=title, title_loc="left")
            fig.tight_layout()
            self.apply_export_margins(fig)
            self._apply_export_palette(fig, export_theme)
            return fig

        row_count = max(len(channels), 1)
        fig = self._new_export_figure(
            export_theme, figsize=(11.69, 8.27),
            dpi=self.PNG_EXPORT_DPI)
        # 用户确认的方案 B：A4 横向整图，趋势图占满上部全宽，
        # 底部为通道统计表格（色点+名称+最高/最低/平均，>8 通道拆双栏），
        # 插入 Word 后可在 A4 横向页面内完整显示。
        table_h = self._overview_table_height(row_count)
        trend_ax = fig.add_axes(
            [0.075, 0.055 + table_h + 0.060, 0.885,
             max(0.30, 0.955 - 0.055 - table_h - 0.060)])
        table_ax = fig.add_axes([0.075, 0.055, 0.885, table_h])
        trend_ax.set_facecolor(palette["BG_CARD"])
        mw._draw_lines(trend_ax, mw._visible_series(max_minutes), title=title,
                       title_loc="left", with_legend=False)
        self._draw_overview_table(table_ax, channels, max_minutes,
                                  palette=palette)
        self._apply_export_palette(fig, export_theme)
        return fig

    @staticmethod
    def _overview_table_height(channel_count):
        """底部统计表格高度（figure 坐标）：表头 + 数据行（>8 通道双栏减半）。"""
        rows = channel_count if channel_count <= 8 else (channel_count + 1) // 2
        return min(0.30, 0.030 * (max(rows, 1) + 1) + 0.020)

    @staticmethod
    def _artist_bbox(artist, renderer):
        """安全获取 matplotlib 图元的像素边界。"""
        if artist is None:
            return None
        try:
            if hasattr(artist, "get_visible") and not artist.get_visible():
                return None
            return artist.get_window_extent(renderer)
        except (AttributeError, RuntimeError, ValueError):
            return None

    def _apply_combo_export_layout(self, fig):
        """按像素安全区重排 A4 三个子图及色条轴。"""
        if len(fig.axes) < 3:
            return
        width_px, height_px = (np.asarray(fig.get_size_inches()) * fig.dpi)
        width_px, height_px = float(width_px), float(height_px)
        renderer = getattr(self._mw, "chart_renderer", None)
        layout_owner = renderer if renderer is not None else None
        if layout_owner is None:
            from chart.chart_renderer import ChartRenderer
            layout_owner = ChartRenderer

        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        draw_renderer = canvas.get_renderer()
        axes = fig.axes[:3]
        # 与前端共用文字感知边距（_combo_text_margins）→ 导出图 == 前端所见，
        # 高 DPI / 大字体下文字不溢出边距、下排标题不叠上排。
        ovr = getattr(self._mw, '_layout_ovr', {}) or {}
        left, right, top, bottom, gap_h, gap_v = layout_owner._combo_text_margins(
            fig, draw_renderer, width_px, height_px, ovr=ovr)
        cbar_axes = fig.axes[3:]
        cbar_reserve = ovr.get("cbar_reserve", 80.0) if cbar_axes else 0.0
        for _ in range(6):
            positions = layout_owner.combo_layout_positions(
                width_px, height_px,
                left=left, right=right, top=top, bottom=bottom,
                gap_h=gap_h, gap_v=gap_v)
            if cbar_axes:
                positions[0][2] = max(
                    1.0 / width_px,
                    positions[0][2] - cbar_reserve / width_px,
                )
            for axis, position in zip(axes, positions):
                axis.set_position(position)
            if cbar_axes:
                top_axis = axes[0].get_position()
                cbar_axes[0].set_position([
                    top_axis.x1 + 12.0 / width_px,
                    top_axis.y0,
                    24.0 / width_px,
                    top_axis.height,
                ])

            canvas.draw()
            draw_renderer = canvas.get_renderer()
            upper_box = self._artist_bbox(axes[0], draw_renderer)
            lower_boxes = [
                self._artist_bbox(axis, draw_renderer) for axis in axes[1:]
            ]
            lower_boxes = [box for box in lower_boxes if box is not None]
            if upper_box is None or not lower_boxes:
                break

            upper_decorations = [
                axes[0].xaxis.label,
                *axes[0].get_xticklabels(),
            ]
            lower_decorations = [
                axes[1].title, axes[2].title,
                axes[1].get_legend(), axes[2].get_legend(),
            ]
            upper_decorations = [
                self._artist_bbox(artist, draw_renderer)
                for artist in upper_decorations
            ]
            lower_decorations = [
                self._artist_bbox(artist, draw_renderer)
                for artist in lower_decorations
            ]
            upper_decorations = [
                box for box in upper_decorations if box is not None
            ]
            lower_decorations = [
                box for box in lower_decorations if box is not None
            ]
            if not upper_decorations or not lower_decorations:
                break

            upper_extension = max(
                0.0,
                upper_box.y0 - min(box.y0 for box in upper_decorations),
            )
            lower_top = max(box.y1 for box in lower_boxes)
            lower_extension = max(
                0.0,
                max(box.y1 for box in lower_decorations) - lower_top,
            )
            current_gap = upper_box.y0 - lower_top
            required_gap = upper_extension + lower_extension + 12.0
            if current_gap + 0.5 >= required_gap:
                break
            gap_v += required_gap - current_gap

        canvas.draw()

    def _draw_overview_table(self, ax, channels, max_minutes=None,
                             palette=None):
        """绘制导出图底部的通道统计表格（方案 B：色点+名称+最高/最低/平均）。"""
        palette = palette or self.export_palette()
        ax.set_xlim(0.0, 1.0)
        ax.set_ylim(0.0, 1.0)
        ax.axis("off")
        # 表格外框（折线绘制，Line2D 不受 _apply_export_palette 文字覆盖影响）
        ax.plot([0.0, 1.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 1.0, 0.0],
                color=palette["BORDER"], linewidth=0.8, clip_on=False)
        if not channels:
            ax.text(0.5, 0.5, "暂无可见通道", color=palette["TEXT_MUTED"],
                    fontsize=9.5, fontfamily=Theme.font("text"),
                    ha="center", va="center")
            return

        font = Theme.font("text")
        size = 9.5 if len(channels) <= 8 else 9.0
        two_col = len(channels) > 8
        half = (len(channels) + 1) // 2
        groups = ([channels[:half], channels[half:]] if two_col
                  else [channels])
        spans = ([(0.0, 0.475), (0.525, 1.0)] if two_col else [(0.0, 1.0)])
        cols = (("通道", 0.018, "left"),
                ("最高 (℃)", 0.66, "right"),
                ("最低 (℃)", 0.83, "right"),
                ("平均 (℃)", 0.985, "right"))
        for group, (x0, x1) in zip(groups, spans):
            width = x1 - x0
            top_pad, bottom_pad = 0.05, 0.06
            row_step = (1.0 - top_pad - bottom_pad) / (len(group) + 0.6)
            header_y = 1.0 - top_pad - 0.3 * row_step
            for label, fx, align in cols:
                ax.text(x0 + width * fx, header_y, label,
                        color=palette["TEXT"], fontsize=size,
                        fontweight="bold", fontfamily=font, ha=align,
                        va="center")
            ax.plot([x0 + 0.004, x1 - 0.004],
                    [header_y - 0.55 * row_step] * 2,
                    color=palette["BORDER"], linewidth=0.9)
            for index, channel in enumerate(group):
                y = header_y - (0.6 + index + 0.5) * row_step
                vmax, vmin, vavg = self._channel_stats(channel, max_minutes)
                # 色点用方块 marker：通道色由 Line2D 承载，不被调色板文字
                # 循环覆盖，色块跟随通道颜色变化
                ax.plot([x0 + width * 0.030], [y], marker="s",
                        markersize=4.6, linestyle="none",
                        color=channel.color or Theme.TEXT_MUTED)
                ax.text(x0 + width * 0.052, y, channel.display_name,
                        color=palette["TEXT"], fontsize=size,
                        fontfamily=font, ha="left", va="center")
                for fx, value in ((0.66, vmax), (0.83, vmin), (0.985, vavg)):
                    ax.text(x0 + width * fx, y,
                            "--" if value is None else f"{value:.1f}",
                            color=(palette["TEXT"] if value is not None
                                   else palette["TEXT_MUTED"]),
                            fontsize=size, fontfamily=font, ha="right",
                            va="center")
                if index < len(group) - 1:
                    ax.plot([x0 + 0.004, x1 - 0.004],
                            [y - 0.5 * row_step] * 2,
                            color=palette["BORDER"], linewidth=0.5,
                            alpha=0.7)

    def _channel_stats(self, channel, max_minutes=None):
        """返回单个通道的 (最高, 最低, 平均)；无有效数据时各值为 None。"""
        mw = self._mw
        result = mw.pipeline.get(mw.dataset, channel)
        if not result:
            return (None, None, None)
        values = np.asarray(result[1], dtype=float)
        if max_minutes is not None and len(result[0]) == len(values):
            time_sec = np.asarray(result[0], dtype=float)
            if time_sec.size:
                time_min = (time_sec - time_sec[0]) / 60.0
                values = values[time_min <= float(max_minutes)]
        values = values[np.isfinite(values)]
        if values.size == 0:
            return (None, None, None)
        return (float(values.max()), float(values.min()),
                float(values.mean()))

    def _export_a4_file(self, directory, task,
                        export_theme=_EXPORT_THEME_NOT_SET):
        mw = self._mw
        if not mw.dataset.visible_channels():
            raise RuntimeError("请至少勾选一个通道")
        # 与前端组合图共用同一字体档（COMBO_FONTS）→ 导出图 == 前端所见
        from chart.chart_renderer import ChartRenderer
        fonts = dict(ChartRenderer.COMBO_FONTS)
        selected_theme = (None if export_theme is _EXPORT_THEME_NOT_SET
                          else export_theme)
        fig = self._new_export_figure(
            selected_theme, figsize=(11.69, 8.27),
            dpi=self.REPORT_PNG_DPI)
        gs = fig.add_gridspec(2, 2, hspace=0.30, wspace=0.16,
                              left=0.075, right=0.975, top=0.90, bottom=0.075)
        fig.add_subplot(gs[0, :])
        fig.add_subplot(gs[1, 0])
        fig.add_subplot(gs[1, 1])
        self._draw_combo_panels(fig, fonts=fonts)
        self._apply_combo_export_layout(fig)
        self._apply_export_palette(fig, selected_theme)
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        extension = self._image_extension(self.image_format)
        path = self._unique_path(directory, "A4组合图", extension, timestamp)
        if export_theme is _EXPORT_THEME_NOT_SET:
            self.save_report_figure(
                fig, path, self.image_background, self.image_format)
        else:
            self.save_report_figure(
                fig, path, image_format=self.image_format,
                export_theme=export_theme)
        return path

    def _build_excel_dataframe(self):
        mw = self._mw
        vis = mw.dataset.visible_channels()
        if not vis:
            raise RuntimeError("没有可见通道")
        # 通道对象可能不可哈希（定义了 __eq__ 未定义 __hash__），用列表保序
        fetched = []
        for channel in vis:
            res = mw.pipeline.get(mw.dataset, channel)
            if res:
                fetched.append((channel, np.asarray(res[0], dtype=float),
                                np.asarray(res[1], dtype=float)))
        # 参考时间轴取第一个有数据的通道，避免空通道作参考丢掉整表数据
        ref_t = next((t for _channel, t, _v in fetched if t.size), None)
        if ref_t is None:
            raise RuntimeError("可见通道均无数据，无法导出数据表")
        df = pd.DataFrame({"时间": [core.sec_to_hms(x) for x in ref_t]})
        for channel, t, values in fetched:
            name = channel.display_name
            if not t.size or not values.size:
                # 无数据通道写空列，不能让 interp 抛异常拖垮整表导出
                df[name] = np.full(len(ref_t), np.nan)
            elif len(values) == len(ref_t):
                df[name] = np.round(values, 3)
            else:
                # 时间乱序先排序；参考范围之外置 NaN 而非端点外推，避免伪造数据
                order = np.argsort(t, kind="stable")
                df[name] = np.round(
                    np.interp(ref_t, t[order], values[order],
                              left=np.nan, right=np.nan), 3)
        return df

    def _export_excel_file(self, directory, task,
                           export_theme=_EXPORT_THEME_NOT_SET):
        # export_theme 仅为统一分发签名兼容：Excel 数据表与主题无关，忽略之。
        # 缺这个参数会让抽屉导出必现 TypeError（跟随主题时下发的是 None）。
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self._unique_path(directory, "处理后数据", ".xlsx", timestamp)
        self._build_excel_dataframe().to_excel(path, index=False)
        return path

    @staticmethod
    def apply_export_margins(fig):
        """为独立导出图片预留坐标轴标题和刻度的安全边距。"""
        fig.subplots_adjust(left=0.067, bottom=0.096, right=0.982, top=0.93)

    # ---- 当前画布导出 ----
    def export_current_canvas(self):
        """导出当前画布标签正在显示的图（单张PNG）。左侧"导出整体图"入口。"""
        mw = self._mw
        if not mw._guard():
            return
        idx = mw.tabs.currentIndex()
        # 统计标签无数值图，退回整体趋势
        if idx == mw.idx_stat:
            idx = 0
        # 找到当前标签的 Figure
        tab_widget = mw.tabs.widget(idx)
        fig = None
        canvas = tab_widget.findChild(FigureCanvas) if hasattr(tab_widget, "findChild") else None
        if canvas is not None:
            fig = canvas.figure
        if fig is None and idx == mw.idx_single:
            fig = mw.tab_single.fig
        if fig is None:
            QMessageBox.information(mw, "提示", "当前标签无可导出的图表。")
            return
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        label = mw.tabs.tabText(idx)
        path, _ = QFileDialog.getSaveFileName(
            mw, "保存当前画布图",
            os.path.join(os.getcwd(), f"{ts}_{label}.png"),
            "PNG 图片 (*.png)")
        if not path:
            return
        try:
            fig.savefig(path, dpi=self.PNG_EXPORT_DPI)
        except Exception as e:
            QMessageBox.critical(mw, "导出失败", str(e))
            return
        QMessageBox.information(mw, "导出完成", f"已保存：\n{path}")
        mw.statusBar().showMessage(f"已导出当前画布图：{label}")

    # ---- 批量PNG ----
    def export_png(self):
        mw = self._mw
        if not mw._guard():
            return
        d = QFileDialog.getExistingDirectory(mw, "选择导出目录", os.getcwd())
        if not d:
            return
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        specs = [("整体趋势", None),
                 (f"前{_fmt_win_int(mw.win_front[0])}分钟", mw.win_front[0]),
                 (f"前{_fmt_win_int(mw.win_front[1])}分钟", mw.win_front[1]),
                 (f"前{_fmt_win_int(mw.win_front[2])}分钟", mw.win_front[2])]
        saved = []
        for name, mx in specs:
            fig = Figure(figsize=(9, 5), dpi=self.PNG_EXPORT_DPI)
            ax = fig.add_subplot(111)
            mw._draw_lines(ax, mw._visible_series(mx), title=name)
            fig.tight_layout()
            self.apply_export_margins(fig)
            fp = os.path.join(d, f"{ts}_{name}.png")
            fig.savefig(fp, dpi=self.PNG_EXPORT_DPI)
            saved.append(fp)
        QMessageBox.information(
            mw, "导出完成",
            f"已导出 {len(saved)} 张总览曲线图（整体 / 前{_fmt_win_int(mw.win_front[0])} / "
            f"前{_fmt_win_int(mw.win_front[1])} / 前{_fmt_win_int(mw.win_front[2])} 分钟）到：\n{d}\n\n"
            f"提示：单通道曲线请在右侧画布\u201c单通道\u201d标签页查看，用该标签页工具栏的\u201c保存图片\u201d可单独导出。")
        mw.statusBar().showMessage(f"已导出 {len(saved)} 张总览PNG")

    # ---- 数据导出 ----
    def export_data(self):
        mw = self._mw
        if not mw._guard():
            return
        path, _ = QFileDialog.getSaveFileName(
            mw, "保存处理后数据", os.path.join(os.getcwd(), "处理后数据.csv"),
            "CSV (*.csv);;Excel (*.xlsx)")
        if not path:
            return
        vis = mw.dataset.visible_channels()
        if not vis:
            QMessageBox.information(mw, "提示", "没有可见通道")
            return
        ref_res = mw.pipeline.get(mw.dataset, vis[0])
        ref_t = ref_res[0] if ref_res else np.array([])
        df = pd.DataFrame({"时间": [core.sec_to_hms(x) for x in ref_t]})
        for c in vis:
            res = mw.pipeline.get(mw.dataset, c)
            if not res:
                continue
            t, v = res[0], res[1]
            if len(v) == len(ref_t):
                df[c.display_name] = np.round(v, 3)
            else:
                df[c.display_name] = np.round(np.interp(ref_t, t, v), 3)
        try:
            if path.lower().endswith(".xlsx"):
                df.to_excel(path, index=False)
            else:
                df.to_csv(path, index=False, encoding="utf-8-sig")
        except Exception as e:
            QMessageBox.critical(mw, "导出失败", str(e))
            return
        QMessageBox.information(mw, "导出完成", f"已保存：\n{path}")

    # ---- A4 组合图 ----
    def export_a4(self):
        mw = self._mw
        if not mw._guard():
            return
        if not mw.dataset.visible_channels():
            QMessageBox.information(mw, "提示", "请至少勾选一个通道")
            return
        path, sel = QFileDialog.getSaveFileName(
            mw, "保存 A4 横向组合图",
            os.path.join(os.getcwd(), "温度爬升_A4组合图.png"),
            "PNG 图片 (*.png);;PDF 文档 (*.pdf)")
        if not path:
            return

        紧凑 = {"title": 12, "label": 9, "tick": 8, "legend": 7}
        fig = Figure(figsize=(11.69, 8.27), dpi=self.PNG_EXPORT_DPI)
        gs = fig.add_gridspec(2, 2, hspace=0.30, wspace=0.16,
                              left=0.075, right=0.975, top=0.90, bottom=0.075)
        fig.add_subplot(gs[0, :])
        fig.add_subplot(gs[1, 0])
        fig.add_subplot(gs[1, 1])
        self._draw_combo_panels(fig, fonts=紧凑)
        self._apply_combo_export_layout(fig)

        try:
            fig.savefig(path, dpi=self.PNG_EXPORT_DPI)
        except Exception as e:
            QMessageBox.critical(mw, "导出失败", str(e))
            return
        QMessageBox.information(
            mw, "导出完成",
            f"A4 横向组合图已保存（3 张子图，1+2 排版）：\n{path}")
        mw.statusBar().showMessage("已导出 A4 组合图：3 子图")

    # ---- A4 辅助 ----
    def _draw_combo_panels(self, fig, fonts=None):
        """在已创建好 1+2 GridSpec 三个 axes 的 fig 上绘制组合图三面板。"""
        mw = self._mw
        s1a, s1b = mw.a4_custom1
        s2a, s2b = mw.a4_custom2
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
                                   fonts=fonts, with_legend=True,
                                   with_cbar=True, cbar_pad=0.03,
                                   title_loc="left")
                if mw.a4_mark:
                    mw.chart_renderer._draw_combo_boundaries(
                        ax, mw.a4_custom1, mw.a4_custom2)
            else:
                self._draw_segment(ax, smin, emax, title, mark=False,
                                   fonts=fonts, with_legend=False,
                                   with_cbar=False, title_loc="center")
                ChartRenderer._apply_combo_segment_axis(ax, smin, emax)

    def _draw_segment(self, ax, start_min, end_min, title, mark=True,
                      fonts=None, with_legend=True, with_cbar=True,
                      cbar_pad=0.03, title_loc="left"):
        """在给定 ax 上绘制指定时间区间(分钟)的所有可见通道曲线。"""
        mw = self._mw
        series = mw._visible_series_window(start_min, end_min)
        norm = mw._draw_lines(
            ax, series, title=title, with_legend=with_legend,
            with_cbar=with_cbar, fonts=fonts, cbar_pad=cbar_pad,
            title_loc=title_loc)
        ChartRenderer._bound_combo_temperature_ticks(ax)
        if mark:
            session = mw.dataset
            for name, x, v in series:
                ch = session.channel_by_label(name) if session else None
                if ch is None:
                    continue
                res = mw.pipeline.get(session, ch)
                if not res:
                    continue
                t_pv, v_pv = res[0], res[1]
                t0 = t_pv[0] if t_pv.size else 0.0
                result = mw.stat_panel.result_for_channel(ch)
                for point in (result.fast_to_slow_sec, result.slow_to_steady_sec):
                    if point is None or not np.isfinite(point):
                        continue
                    px = (point - t0) / 60.0
                    lo = start_min if start_min is not None else x.min()
                    hi = end_min if end_min is not None else x.max()
                    if lo <= px <= hi:
                        col = (THERMAL_CMAP(norm(v[np.argmax(v)]))
                               if norm is not None else mw._color_of(name))
                        ax.axvline(px, color=col, linestyle=":", alpha=0.5, linewidth=1.0)
