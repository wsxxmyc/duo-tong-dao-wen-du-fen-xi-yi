# -*- coding: utf-8 -*-
"""
TPX 二进制写出器 —— 生成可供原厂上位机打开的 .tpx 工程文件。

格式来源：对设备导出的多份 .tpx 样本逆向 + 与 .xls 交叉验证得出，
与 core._parse_tpx() 的解析逻辑严格互逆。

文件布局
--------
    0x00  [1B len]"TP-X"
          u32 版本 = 2000
          6 × [1B len]字符串   = ['65','admin','hp41','admin','hp41','YYYY-MM-DD']
          u32 flag = 1
    0x31  通道表：64 项 × { [1B len]"CHn" + u32 使能标志 }
          （名字固定 CH1..CH64；使能非零即启用）
    0x268 u32 块计数（样本恒为 0，解析端不依赖，写 0）
    0x26C 数据区：每帧 = (启用通道数+1) × float32 + u32 计数器
          帧内 [v0 镜像 = v1][v1..vM][计数器]
          计数器 = (k+1) × 间隔秒
          缺测（开路/无数据）写 65535.0

⚠ 末尾残缺块：实测样本的数据区余数 **恒等于 blocksize − 4**，
   即文件结尾多出一组 floats 而没有配套计数器。为最大化兼容性，
   本写出器完全复刻该特征（末尾补写一次最后一帧的 floats）。
"""
from __future__ import annotations

import struct
import time

import numpy as np
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注用（write_tpx 的 session 标注）
    from .session import Session

TPX_MAGIC = "TP-X"
TPX_VERSION = 2000
TPX_MISSING = 65535.0
TOTAL_SLOTS = 64          # 通道表固定 64 项

# 头部 6 个字符串字段的默认值（取自设备导出样本的通用取值）
DEFAULT_HEADER_FIELDS = ["65", "admin", "hp41", "admin", "hp41"]


def _lp(s: str) -> bytes:
    """长度前缀字符串编码。"""
    b = s.encode("utf-8")
    if len(b) > 255:
        b = b[:255]
    return bytes([len(b)]) + b


def _slot_of(key: str) -> int:
    """"CH7" → 6（0-based 槽位）。无法识别返回 -1。"""
    up = (key or "").strip().upper().replace(" ", "")
    if up.startswith("CH") and up[2:].isdigit():
        n = int(up[2:])
        if 1 <= n <= TOTAL_SLOTS:
            return n - 1
    return -1


def build_header(fields: list[str] | None = None, date_str: str = "") -> bytes:
    """构造 0x00~0x30 的头部。"""
    f = list(fields or DEFAULT_HEADER_FIELDS)
    while len(f) < 5:
        f.append("")
    date_str = date_str or time.strftime("%Y-%m-%d")
    out = bytearray()
    out += _lp(TPX_MAGIC)
    out += struct.pack("<I", TPX_VERSION)
    for s in f[:5]:
        out += _lp(s)
    out += _lp(date_str)
    out += struct.pack("<I", 1)          # flag
    return bytes(out)


def build_channel_table(enabled_slots: set[int]) -> bytes:
    """构造 64 项通道表。enabled_slots 为 0-based 槽位集合。"""
    out = bytearray()
    for i in range(TOTAL_SLOTS):
        out += _lp(f"CH{i + 1}")
        out += struct.pack("<I", 1 if i in enabled_slots else 0)
    return bytes(out)


def write_tpx(path: str, session: "Session", interval: float | None = None,
              header_fields: list[str] | None = None) -> tuple[bool, str]:
    """把 Session 写成 .tpx 文件。

    Args:
        path:     输出路径
        session:  datastore.Session
        interval: 采样间隔秒（缺省取 session.interval），会取整为 ≥1 的整数
        header_fields: 覆盖头部 5 个字符串字段

    Returns:
        (成功?, 错误信息)
    """
    try:
        chans = list(session.channels)
        if not chans:
            return False, "会话中没有通道"

        n_rows = session.n
        if n_rows == 0:
            return False, "会话中没有数据"

        # ---- 通道 → 64 槽位映射 ----
        # 未能识别物理号的通道，按出现顺序占用前面的空槽
        slots: list[tuple[int, object]] = []
        used: set[int] = set()
        pending = []
        for c in chans:
            s = _slot_of(c.key)
            if s >= 0 and s not in used:
                used.add(s)
                slots.append((s, c))
            else:
                pending.append(c)
        for c in pending:
            for s in range(TOTAL_SLOTS):
                if s not in used:
                    used.add(s)
                    slots.append((s, c))
                    break
        # 数据区按槽位升序排列（与原厂"启用顺序"一致）
        slots.sort(key=lambda x: x[0])
        ordered = [c for _, c in slots]
        n_en = len(ordered)
        if n_en == 0:
            return False, "没有可写入的通道"

        # ---- 采样间隔（计数器步长必须是整数秒）----
        iv = interval if interval is not None else session.interval
        step = max(1, int(round(iv or 1)))

        # ---- 组装 ----
        buf = bytearray()
        buf += build_header(header_fields)
        buf += build_channel_table(used)
        buf += struct.pack("<I", 0)      # 表尾块计数：原厂恒为 0

        mat = session.buffer.matrix      # (n_rows, n_ch) 视图
        col_idx = [c.index for c in ordered]
        rec_fmt = f"<{n_en + 1}fI"

        last_vals = None
        for k in range(n_rows):
            row = mat[k]
            vals = []
            for ci in col_idx:
                v = row[ci] if ci < row.size else np.nan
                vals.append(TPX_MISSING if (v != v) else float(v))
            last_vals = vals
            # [v0 镜像][v1..vM][计数器]
            buf += struct.pack(rec_fmt, vals[0], *vals, (k + 1) * step)

        # ---- 复刻原厂末尾残缺块（仅 floats，无计数器）----
        if last_vals is not None:
            buf += struct.pack(f"<{n_en + 1}f", last_vals[0], *last_vals)

        with open(path, "wb") as f:
            f.write(buf)
        return True, ""
    except Exception as e:
        return False, str(e)
