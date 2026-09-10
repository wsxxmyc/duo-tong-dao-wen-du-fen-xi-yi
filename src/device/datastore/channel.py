# -*- coding: utf-8 -*-
"""
Channel —— 唯一的通道模型。

与旧 core.ChannelData 的关键区别
--------------------------------
1. **通道不持有数据数组**。数据统一放在 Session.buffer 的列里，
   通道只保存"元信息 + 列下标"。这样实时追加一帧只写一行，
   不需要同时改 N 个 array。
2. **新增稳定键 `key`**（物理通道号 CH1..CH64）。
   它是跨文件、跨会话、跨"文件导入 / 在线采集"的唯一标识，
   通道名称与颜色都按 key 持久化，从而做到全局共享。
3. `name` / `color` / `visible` 不在这里做默认值决策，
   一律由 ChannelConfig 统一解析下发。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# 与旧 core.ChannelData.PALETTE 保持一致，避免历史文件换色
# 2026-08-30 隐患色替换（与 core.ChannelData.PALETTE 同步；ChannelConfig
# 的 self.palette 亦源自本常量）：#9B59B6→#9D6BCA、#34495E→#8FA3C7，
# 旧色对 7 套深画布仅 1.49~3.61:1。新色验算（实现同 test_theme_contrast）：
# #9D6BCA 对 7 画布 3.56~4.33:1、#8FA3C7 5.44~6.62:1，替换后前 8 通道
# 两两 ΔE2000 最小 10.8、全表相邻最小 34.7，均满足 ≥8.0。
DEFAULT_PALETTE = [
    "#E74C3C", "#3498DB", "#2ECC71", "#F39C12", "#9D6BCA",
    "#1ABC9C", "#E67E22", "#8FA3C7", "#FF6B9D", "#16A085",
]


def palette_color(index: int, palette: list[str] | None = None) -> str:
    """按序号取调色板颜色（循环）。"""
    pal = palette or DEFAULT_PALETTE
    return pal[index % len(pal)]


def normalize_key(raw: str, index: int) -> str:
    """把各种来源的通道标识规范成稳定键。

    - .tpx 通道表里的物理名 "CH64"      → "CH64"
    - .xls 表头列名 "CH7" / " ch7 "     → "CH7"
    - 无法识别的列名（如 "温度1"）       → "CH{index+1}" 兜底
    """
    s = (raw or "").strip()
    if not s:
        return f"CH{index + 1}"
    up = s.upper().replace(" ", "")
    if up.startswith("CH") and up[2:].isdigit():
        return f"CH{int(up[2:])}"
    return f"CH{index + 1}"


# ======================================================================
#  固定通道序号（CH 前缀）
# ======================================================================
def channel_seq_number(key: str) -> int:
    """从稳定键提取物理通道号：CH6 → 6，CH64 → 64。"""
    m = re.match(r"CH(\d+)", (key or "").strip().upper())
    return int(m.group(1)) if m else 0


def seq_prefix(key: str) -> str:
    """固定通道序号前缀：CH6 → CH6（保持原始 CH 通道号，不带中文数字）。"""
    n = channel_seq_number(key)
    return f"CH{n}" if n else (key or "")


def seq_label(key: str, base: str) -> str:
    """由物理键 + 基础名拼完整名：CH6 + 准直保护 → CH6·准直保护。

    base 为空或等于 key（物理号本身）时只返回前缀，不拼接。
    """
    pre = seq_prefix(key)
    if not base or base == key:
        return pre
    if base == pre or base.startswith(pre + "·") or base.startswith(pre + " "):
        return base  # 已带前缀，避免重复
    return f"{pre}·{base}"


def strip_seq_label(label: str) -> str:
    """把完整名还原为自定义名：CH6·准直保护 → 准直保护；CH6 → ""。

    同时兼容旧版本的中文数字前缀：通道六·准直保护 → 准直保护。
    """
    if not label:
        return ""
    s = label.strip()
    # 带自定义名的完整名：CH6·准直保护 / 通道六·准直保护 → 准直保护
    m = re.match(r"(?:CH\d+|通道[一二三四五六七八九十百]+)\s*[·\s]\s*", s)
    if m:
        return s[m.end():].strip()
    # 纯前缀（CH6 / 通道六）→ 空，表示使用物理号本身
    if re.match(r"(?:CH\d+|通道[一二三四五六七八九十百]+)$", s, re.IGNORECASE):
        return ""
    return ""


@dataclass
class Channel:
    """单通道元信息。数据在 Session.buffer.column(index) 里。"""

    key: str                       # 稳定键，如 "CH1"（持久化配置按此索引）
    index: int                     # 在本会话 buffer 中的列下标（0-based）
    name: str = ""                 # 显示名（ChannelConfig 下发）
    color: str = ""                # 曲线颜色（ChannelConfig 下发）
    visible: bool = True           # 是否绘制
    enabled: bool = True           # 硬件使能 / 文件中存在
    unit: str = "\u2103"
    source_label: str = ""         # 原始列名/物理名，仅供溯源显示
    # 运行时统计（采集页实时刷新用，非持久化）
    peak: float = float("-inf")
    last_value: float | None = None
    meta: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def display_name(self) -> str:
        """完整显示名：固定物理序号前缀 + 自定义名称（如 CH6·准直保护）。"""
        return self._full_label()

    @property
    def orig_name(self) -> str:
        """兼容旧代码：顺序列名 CH1/CH2...（按 index 生成）。"""
        return f"CH{self.index + 1}"

    @property
    def idx(self) -> int:
        """兼容旧代码 ChannelData.idx。"""
        return self.index

    def label(self) -> str:
        """图例文字（与 display_name 一致，均带固定序号）。"""
        return self._full_label()

    def _full_label(self) -> str:
        """统一生成带固定通道序号的完整名。"""
        return seq_label(self.key, self.name)

    def to_dict(self) -> dict:
        """导出为可序列化的配置字典"""
        return {
            "key": self.key,
            "index": self.index,
            "name": self.name,
            "color": self.color,
            "visible": self.visible,
            "enabled": self.enabled,
            "source_label": self.source_label,
        }
