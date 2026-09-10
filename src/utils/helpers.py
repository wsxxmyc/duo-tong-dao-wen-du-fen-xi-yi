# -*- coding: utf-8 -*-
"""
通用工具函数与常量 —— 从 app.py 提取的辅助方法。
"""

import os
import sys

import numpy as np

from utils import core


# ===== 字体比例联动定义 =====
# 以导航栏字号为基准（1.0），其他维度按固定系数自动计算
FONT_RATIOS = {
    "nav": 1.0,
    "title": 1.08,
    "header": 1.0,
    "base": 0.92,
    "desc": 0.85,
    "intro": 0.85,
}

FONT_PRESETS = {
    "较小": 12,
    "默认": 14,
    "较大": 16,
    "特大": 18,
}
FONT_LEVEL_NAMES = list(FONT_PRESETS.keys())


def px_to_pt(px: float) -> float:
    """px→pt（96dpi 基准，×0.75）。

    HiDPI 批次起 QSS 字号统一以 pt 发射：pt 随系统 DPI 缩放而 px 不随，
    100% 缩放下两者物理等值（12px=9pt），观感零变化。
    """
    return round(px * 0.75, 1)


def _build_font_dict(nav_size):
    """根据导航栏字号按比例计算所有维度的字号（四舍五入取整）。"""
    return {k: max(9, round(nav_size * r)) for k, r in FONT_RATIOS.items()}


def _nice_step(span, target_ticks=6):
    """为给定数据跨度选一个"漂亮"刻度步长（1/2/5×10^n），
    使大致产生 target_ticks 个刻度。用于智能轴范围取整。"""
    import math
    if span <= 0:
        return 1.0
    raw = span / max(target_ticks, 1)
    mag = 10 ** math.floor(math.log10(raw))
    norm = raw / mag
    if norm < 1.5:
        step = 1.0
    elif norm < 3:
        step = 2.0
    elif norm < 7:
        step = 5.0
    else:
        step = 10.0
    return step * mag


# ── 绿色版统一数据目录 ──
# 所有用户数据（配置/数据库/日志）都放在软件根目录的「用户数据/」下，
# 实现免安装绿色版：复制整个发布包文件夹即带走全部数据。
USER_DATA_DIR = "用户数据"
# 打包后主程序所在的子目录名（见 build/launcher.py 的 RUNTIME_DIR）。
# 主程序在「程序文件/」内，数据要放其上一级（发布包根）。
_RUNTIME_SUBDIR = "程序文件"


def _resolve_app_root():
    """返回应用数据根目录（绿色版 = 软件根目录）。

    所有用户数据（配置/数据库/日志）都派生自本目录。解析优先级：
      1. 环境变量 ``MTA_HOME``（launcher 启动时注入，最可靠，见 build/launcher.py）
      2. 打包模式（``sys.frozen``）：主程序 exe 所在目录；若其在
         ``程序文件/`` 子目录内，回溯到上一级（发布包根目录）
      3. 开发模式：项目根（``src`` 的上两级（项目根））
    """
    env_root = os.environ.get("MTA_HOME")
    if env_root and os.path.isdir(env_root):
        return os.path.abspath(env_root)
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        # 发布包结构：发布包根/程序文件/主程序.exe → 数据放发布包根
        if os.path.basename(exe_dir) == _RUNTIME_SUBDIR:
            return os.path.dirname(exe_dir)
        return exe_dir
    # 开发模式：__file__ = <项目根>/src/utils/helpers.py
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _resolve_config_dir():
    """配置文件目录：<软件根>/用户数据/config。"""
    cfg = os.path.join(_resolve_app_root(), USER_DATA_DIR, "config")
    os.makedirs(cfg, exist_ok=True)
    return cfg


def _resolve_data_dir():
    """数据文件目录（历史数据库等）：<软件根>/用户数据/data。"""
    d = os.path.join(_resolve_app_root(), USER_DATA_DIR, "data")
    os.makedirs(d, exist_ok=True)
    return d


def _resolve_log_dir():
    """日志文件目录（崩溃/串口诊断）：<软件根>/用户数据/logs。"""
    d = os.path.join(_resolve_app_root(), USER_DATA_DIR, "logs")
    os.makedirs(d, exist_ok=True)
    return d


# 图片导出默认根目录名（<软件根>/导出图片）。
EXPORT_DIR_NAME = "导出图片"
# Windows 文件/文件夹名非法字符（含路径分隔符）。
_FILENAME_ILLEGAL_CHARS = '\\/:*?"<>|'
# Windows 保留设备名（不加扩展名也不能直接用作文件夹名）。
_WINDOWS_RESERVED_NAMES = (
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
)


def _resolve_export_root_dir():
    """图片导出默认根目录：<软件根>/导出图片（不存在则创建）。"""
    d = os.path.join(_resolve_app_root(), EXPORT_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _sanitize_filename(text, fallback="未命名", max_length=50):
    """把会话名等自由文本清洗为 Windows 安全的文件/文件夹名。

    非法字符与控制字符替换为 ``_``，去除首尾空白及结尾的 ``.`` 与空格，
    规避 Windows 保留名（前缀 ``_``），超长截断；候选依次为 text →
    fallback → "未命名"，清洗后仍为空（或只剩替换下划线）则用下一候选。
    """
    result = ""
    for candidate in (text, fallback, "未命名"):
        result = "".join(
            "_" if ch in _FILENAME_ILLEGAL_CHARS or ord(ch) < 32 else ch
            for ch in str(candidate or ""))
        result = result.strip().rstrip(". ")
        if len(result) > max_length:
            result = result[:max_length].rstrip(". ")
        if result.upper().startswith(_WINDOWS_RESERVED_NAMES):
            result = "_" + result
        if result.strip("_"):
            break
    return result or "未命名"


def _fmt_win_int(w):
    """把窗口分钟数格式化为标签页标签（整数更清爽，如 10.0→前10分钟）。"""
    try:
        v = float(w)
        return str(int(v)) if v == int(v) else f"{v:g}"
    except (TypeError, ValueError):
        return str(w)


def _fmt_range(a, b):
    if (a is None or a == 0) and b is None:
        return "全程"
    if (a is None or a == 0) and b is not None:
        return f"前{b:g}分钟"
    if a is not None and b is None:
        return f"{a:g}分钟后到结束"
    return f"{a:g}~{b:g}分钟"


def _wrap_cjk(text, width):
    """按字符宽度严格折行（matplotlib 对中文不自动换行，手动按字符数切分）。"""
    lines, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= width:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    return lines


def _stats_lines(main_window):
    """生成各通道三阶段温升统计行（用于分析图与 AI 提示；被无头验证脚本复用）。"""
    lines = []
    for c in main_window.dataset.visible_channels():
        result = main_window.stat_panel.result_for_channel(c)
        if result.status == "insufficient_data":
            continue
        state = {
            "steady_confirmed": "稳态已确认",
            "steady_unconfirmed": "稳态未确认",
            "heating": "升温分析中",
        }.get(result.status, result.status)
        fast_rate = (result.fast_metrics or {}).get("average_rate_per_min")
        slow_rate = (result.slow_metrics or {}).get("average_rate_per_min")
        fast_rate_text = f"{fast_rate:.3f}" if np.isfinite(fast_rate) else "—"
        slow_rate_text = f"{slow_rate:.3f}" if np.isfinite(slow_rate) else "—"
        fast_time = (core.sec_to_hms(result.fast_to_slow_sec)
                     if result.fast_to_slow_sec is not None else "—")
        steady_time = (core.sec_to_hms(result.slow_to_steady_sec)
                       if result.slow_to_steady_sec is not None else "—")
        lines.append(
            f"{c.display_name}：{state} / 起始 {result.start_temperature:.1f}℃"
            f" / 快转慢 {fast_time} / 慢转稳 {steady_time}"
            f" / 快段速率 {fast_rate_text}℃/分 / 慢段速率 {slow_rate_text}℃/分")
    return lines
