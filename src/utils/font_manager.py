"""字体选择与可选内置字体注册。

界面优先使用 Segoe UI，中文和图表优先使用 Noto Sans SC / 思源黑体，
数值优先使用等宽字体。发布包如果放入已确认授权的字体文件，会自动注册；
没有字体文件时安全回退到目标电脑已有字体。

内置字体必须是静态实例（Regular/Bold），禁止使用可变字体（VF）：
Win7 的 DirectWrite 不支持 VF，会按默认实例渲染（本字体默认实例为
Thin/100），10pt 下细笔画整段消失（2026-09-10 Win7 真机回归）。
"""
import os
import sys

from matplotlib import font_manager as mpl_font_manager


UI_FONT_CANDIDATES = (
    "Segoe UI", "Noto Sans SC", "Source Han Sans SC",
    "Microsoft YaHei UI", "Microsoft YaHei", "Arial",
)
TEXT_FONT_CANDIDATES = (
    "Noto Sans SC", "Source Han Sans SC", "Segoe UI",
    "Microsoft YaHei UI", "Microsoft YaHei", "Arial",
)
PLOT_FONT_CANDIDATES = (
    "Microsoft YaHei", "Microsoft YaHei UI", "Noto Sans SC",
    "Source Han Sans SC", "Segoe UI", "Arial",
)
MONO_FONT_CANDIDATES = (
    "Consolas", "Noto Sans Mono", "Noto Mono", "Cascadia Mono", "Courier New",
)


def bundled_font_dir(base_dir=None):
    """返回可选字体资源目录；目录不存在时也只返回路径。"""
    if base_dir:
        return os.path.join(base_dir, "assets", "fonts")
    roots = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))]
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        roots.insert(0, frozen_root)
    for root in roots:
        directory = os.path.join(root, "assets", "fonts")
        if os.path.isdir(directory):
            return directory
    return os.path.join(roots[0], "assets", "fonts")


def register_bundled_fonts(base_dir=None):
    """注册资源目录内的字体，返回实际注册的字体名称列表。"""
    directory = bundled_font_dir(base_dir)
    if not os.path.isdir(directory):
        return []
    registered = []
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith((".ttf", ".otf")):
            continue
        path = os.path.join(directory, name)
        try:
            mpl_font_manager.fontManager.addfont(path)
            registered.append(mpl_font_manager.FontProperties(fname=path).get_name())
        except (OSError, RuntimeError, ValueError):
            continue
    return registered


def register_bundled_qt_fonts(base_dir=None):
    """把内置字体注册到 Qt，返回注册成功的字体族名称。"""
    try:
        from PyQt5.QtGui import QFontDatabase
    except ImportError:
        return []
    directory = bundled_font_dir(base_dir)
    if not os.path.isdir(directory):
        return []
    registered = []
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith((".ttf", ".otf")):
            continue
        path = os.path.join(directory, name)
        try:
            font_id = QFontDatabase.addApplicationFont(path)
            if font_id >= 0:
                registered.extend(QFontDatabase.applicationFontFamilies(font_id))
        except (OSError, RuntimeError, ValueError):
            continue
    return registered


def available_font_names():
    """返回 Matplotlib 当前可用字体名称。"""
    return {entry.name for entry in mpl_font_manager.fontManager.ttflist}


def choose_font(candidates, available=None, default="DejaVu Sans"):
    """从候选列表中选择当前环境真正存在的字体。"""
    available = available if available is not None else available_font_names()
    for name in candidates:
        if name in available:
            return name
    return default


def build_font_config(base_dir=None):
    """注册内置字体并返回界面、正文、图表、等宽字体配置。"""
    register_bundled_fonts(base_dir)
    available = available_font_names()
    return {
        "ui": choose_font(UI_FONT_CANDIDATES, available),
        "text": choose_font(TEXT_FONT_CANDIDATES, available),
        "plot": choose_font(PLOT_FONT_CANDIDATES, available),
        "mono": choose_font(MONO_FONT_CANDIDATES, available),
    }
