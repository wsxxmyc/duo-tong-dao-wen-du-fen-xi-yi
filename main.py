#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""多通道温度分析仪 — 入口点"""
from __future__ import annotations

import sys
import os
os.environ["QT_API"] = "pyqt5"

# 源码包位于 src/ 下：统一注入 sys.path（顶层包：ui / chart / device / utils）
_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont, QIcon


def get_icon_path(icon_name: str = "app", size: int = 256) -> str:
    """获取图标文件路径
    
    Args:
        icon_name: 图标名称 (app, tray, file)
        size: 图标尺寸
    
    Returns:
        图标文件的完整路径
    """
    # 资源目录
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的路径
        base_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        # 开发时的路径
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    icons_dir = os.path.join(base_dir, "assets", "icons")
    png_dir = os.path.join(icons_dir, "png")
    
    # 图标名称映射
    name_map = {
        "app": "app_icon",
        "tray": "tray_icon",
        "file": "file_icon",
    }
    
    prefix = name_map.get(icon_name, icon_name)
    
    # 尝试加载指定尺寸
    png_path = os.path.join(png_dir, f"{prefix}_{size}x{size}.png")
    if os.path.exists(png_path):
        return png_path
    
    # 尝试加载 256 尺寸作为默认
    default_path = os.path.join(png_dir, f"{prefix}_256x256.png")
    if os.path.exists(default_path):
        return default_path
    
    # 尝试加载 128 尺寸
    fallback_path = os.path.join(png_dir, f"{prefix}_128x128.png")
    if os.path.exists(fallback_path):
        return fallback_path
    
    # 尝试加载 64 尺寸
    min_path = os.path.join(png_dir, f"{prefix}_64x64.png")
    if os.path.exists(min_path):
        return min_path
    
    # 回退到 ICO 文件
    ico_path = os.path.join(icons_dir, f"{prefix}.ico")
    if os.path.exists(ico_path):
        return ico_path
    
    return ""

# 向后兼容：测试代码直接 import app 引用以下模块（有意重导出，F401 勿删）
from ui.main_window import MainWindow  # noqa: F401
from utils import core  # noqa: F401
from utils.config_io import AXIS_CONFIG_FILE, A4_CONFIG_FILE, OVERVIEW_CONFIG_FILE, CONFIG_DIR  # noqa: F401
from utils.config_io import ConfigIO  # noqa: F401
from utils.export import ExportManager  # noqa: F401
from utils.helpers import (  # noqa: F401
    FONT_RATIOS, FONT_PRESETS, FONT_LEVEL_NAMES,
    _fmt_win_int, _fmt_range, _wrap_cjk, _nice_step, _build_font_dict, _resolve_config_dir
)
from chart.chart_tab import PlotTab  # noqa: F401
from ui.widgets.channel_panel import ChannelPanel  # noqa: F401
from chart.chart_renderer import ChartRenderer  # noqa: F401
from ui.widgets.stat_panel import StatPanel  # noqa: F401
from ui.widgets.combo_panel import ComboPanel  # noqa: F401
from ui.widgets.compare_panel import ComparePanel  # noqa: F401
from ui.dialogs.settings_dialog import SettingsDialog  # noqa: F401
from ui.theme import Theme  # noqa: F401
from utils.font_manager import build_font_config, register_bundled_qt_fonts  # noqa: F401

import matplotlib
matplotlib.use("Qt5Agg")


def register_tpx_association() -> None:
    """注册 .tpx 文件关联（HKCU，免管理员）。

    - 文件图标：DefaultIcon → 项目 file_icon.ico（1:1 使用项目图标集，禁用默认图标）。
    - 双击打开：shell\\open\\command → 本程序 "%1"（配合 main() 的 argv[1] 直接打开）。
    - 幂等：每次启动以当前 exe 路径刷新（绿色版移动目录后关联自动更新路径）。
    仅打包版注册（开发模式不污染开发机）；注册失败静默，不影响主程序。
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import winreg
        base_dir = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
        icon_path = os.path.join(base_dir, "assets", "icons", "file_icon.ico")
        if not os.path.exists(icon_path):
            return
        exe_path = os.path.abspath(sys.executable)
        open_cmd = f'"{exe_path}" "%1"'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.tpx") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, "MTA.TPXData")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Classes\MTA.TPXData") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, "多通道温度分析仪数据文件")
            with winreg.CreateKey(k, "DefaultIcon") as ik:
                winreg.SetValue(ik, "", winreg.REG_SZ, icon_path)
            with winreg.CreateKey(k, r"shell\open\command") as ck:
                winreg.SetValue(ck, "", winreg.REG_SZ, open_cmd)
    except Exception:
        pass  # 注册失败不影响主程序


def main() -> None:
    """应用入口：初始化 Qt 应用并运行主窗口事件循环"""
    # ── HiDPI：必须在 QApplication 构造前启用（px 与 pt 同因子缩放、相对
    #    布局恒定不截断、位图按设备像素比渲染防糊；出问题可独立回退本开关）──
    from PyQt5.QtCore import Qt
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("多通道温度分析仪")

    # ── 全局禁用数值控件鼠标滚轮改值（只允许键入 / 点击上下微调）──
    from ui.widgets.no_wheel import install_no_wheel_input
    install_no_wheel_input(app)

    # ── 设置应用图标 ──
    icon_path = get_icon_path("app", 256)
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    # ── 全局异常捕获（防止闪退，traceback 写入日志文件）──
    import faulthandler, traceback
    from utils.helpers import _resolve_log_dir
    _err_log = os.path.join(_resolve_log_dir(), "acq_crash.log")
    try:
        faulthandler.enable(file=open(_err_log, "w"), all_threads=True)
    except Exception:
        pass

    def _excepthook(exc_type, exc_val, exc_tb):
        try:
            with open(_err_log, "a", encoding="utf-8") as f:
                f.write("\n".join(traceback.format_exception(exc_type, exc_val, exc_tb)))
        except Exception:
            pass
        traceback.print_exception(exc_type, exc_val, exc_tb)

    sys.excepthook = _excepthook

    # 主题（全局深色；切换浅色：Theme.apply_to(app, "light")）
    register_bundled_qt_fonts()
    Theme.configure_fonts(build_font_config())
    Theme.apply_to(app)
    import matplotlib.pyplot as plt
    plt.rcParams.update(Theme.mpl_params())

    # 字体
    f = QFont()
    try:
        f.setFamilies(Theme.FONT_FAMILY)
    except AttributeError:
        f.setFamily(Theme.font("ui"))
    f.setPointSizeF(float(Theme.FONT_SIZE))
    app.setFont(f)

    # 启动主窗口
    from ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    # 文件关联：注册 .tpx（图标=项目 file_icon，双击用本程序打开）
    register_tpx_association()
    # 文件关联双击传参：直接打开传入的数据文件（如 %1 的 .tpx）
    if len(sys.argv) > 1:
        p = sys.argv[1]
        if p and os.path.isfile(p):
            try:
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(0, lambda: w._start_import(p))
            except Exception:
                pass
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
