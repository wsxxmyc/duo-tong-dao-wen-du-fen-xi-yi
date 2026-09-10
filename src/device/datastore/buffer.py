# -*- coding: utf-8 -*-
"""
TimeSeriesBuffer —— 列式可增长的二维时序缓冲。

设计要点
--------
1. **列式存储**：时间轴 1 维 + 数值 2 维 (n_rows, n_channels)。
   通道数据不再散落在 N 个 Python list 里，追加一帧只写一行。
2. **摊销 O(1) 追加**：预分配容量，写满后容量翻倍（numpy resize 拷贝）。
3. **零拷贝读取**：`time` / `column()` 返回底层数组的视图（view），
   绘图直接拿视图用，不产生副本。
4. **无 UI 依赖**：可单独测试。

⚠ 视图有效期：任何 append/extend 触发扩容后，之前取得的视图会失效
   （底层数组被替换）。调用方应在每次绘图时重新取视图，不要长期缓存。
"""
from __future__ import annotations

import numpy as np

# 初始容量：按 1 秒采样跑 8 分钟不扩容
DEFAULT_CAPACITY = 512
# 单次扩容上限，避免超长采集一次申请过大内存
MAX_GROWTH_STEP = 65536


class TimeSeriesBuffer:
    """时间轴 + 多通道数值的可增长缓冲区。"""

    __slots__ = ("_n_ch", "_time", "_values", "_n", "_cap")

    def __init__(self, n_channels: int, capacity: int = DEFAULT_CAPACITY):
        if n_channels <= 0:
            raise ValueError(f"通道数必须为正，收到 {n_channels}")
        self._n_ch = int(n_channels)
        self._cap = max(int(capacity), 16)
        self._time = np.empty(self._cap, dtype=np.float64)
        self._values = np.empty((self._cap, self._n_ch), dtype=np.float64)
        self._n = 0

    # ------------------------------------------------------------------
    #  容量管理
    # ------------------------------------------------------------------
    def _ensure(self, extra: int):
        """确保还能再放下 extra 行，不够就扩容。"""
        need = self._n + extra
        if need <= self._cap:
            return
        new_cap = self._cap
        while new_cap < need:
            step = min(new_cap, MAX_GROWTH_STEP)
            new_cap += step
        new_time = np.empty(new_cap, dtype=np.float64)
        new_vals = np.empty((new_cap, self._n_ch), dtype=np.float64)
        new_time[:self._n] = self._time[:self._n]
        new_vals[:self._n] = self._values[:self._n]
        self._time = new_time
        self._values = new_vals
        self._cap = new_cap

    # ------------------------------------------------------------------
    #  写入
    # ------------------------------------------------------------------
    def append(self, t: float, row) -> int:
        """追加一帧。row 中 None / 缺失 → NaN。返回写入的行号。"""
        self._ensure(1)
        i = self._n
        self._time[i] = t
        buf = self._values[i]
        buf[:] = np.nan
        m = min(len(row), self._n_ch)
        for k in range(m):
            v = row[k]
            buf[k] = np.nan if v is None else v
        self._n += 1
        return i

    def extend(self, times: np.ndarray, rows: np.ndarray) -> tuple[int, int]:
        """批量追加。rows 形状 (m, n_channels)。返回 (起始行号, 行数)。"""
        times = np.asarray(times, dtype=np.float64)
        rows = np.asarray(rows, dtype=np.float64)
        if rows.ndim == 1:
            rows = rows.reshape(1, -1)
        m = len(times)
        if m == 0:
            return self._n, 0
        if rows.shape[0] != m:
            raise ValueError(f"时间点数 {m} 与数据行数 {rows.shape[0]} 不匹配")
        if rows.shape[1] != self._n_ch:
            raise ValueError(f"列数 {rows.shape[1]} 与通道数 {self._n_ch} 不匹配")
        self._ensure(m)
        start = self._n
        self._time[start:start + m] = times
        self._values[start:start + m] = rows
        self._n += m
        return start, m

    # ------------------------------------------------------------------
    #  读取（零拷贝视图）
    # ------------------------------------------------------------------
    @property
    def n(self) -> int:
        """已写入的行数。"""
        return self._n

    @property
    def n_channels(self) -> int:
        """通道数量"""
        return self._n_ch

    @property
    def capacity(self) -> int:
        """当前预分配容量（行数）"""
        return self._cap

    @property
    def time(self) -> np.ndarray:
        """时间轴视图，长度 n。"""
        return self._time[:self._n]

    @property
    def matrix(self) -> np.ndarray:
        """全量数值视图，形状 (n, n_channels)。"""
        return self._values[:self._n]

    def column(self, idx: int) -> np.ndarray:
        """第 idx 个通道的数值视图（非连续内存，绘图可用）。"""
        if not 0 <= idx < self._n_ch:
            raise IndexError(f"通道索引 {idx} 越界（共 {self._n_ch} 个）")
        return self._values[:self._n, idx]

    def row(self, idx: int) -> np.ndarray:
        """第 idx 帧的所有通道值。"""
        if not 0 <= idx < self._n:
            raise IndexError(f"行索引 {idx} 越界（共 {self._n} 行）")
        return self._values[idx]

    # ------------------------------------------------------------------
    #  结构调整
    # ------------------------------------------------------------------
    def grow_channels(self, n_channels: int) -> None:
        """扩充通道数（采集中发现更多通道时用）。已有数据右侧补 NaN。"""
        if n_channels <= self._n_ch:
            return
        new_vals = np.full((self._cap, n_channels), np.nan, dtype=np.float64)
        new_vals[:self._n, :self._n_ch] = self._values[:self._n]
        self._values = new_vals
        self._n_ch = int(n_channels)

    def clear(self) -> None:
        """清空数据（行数归零，容量保留）"""
        self._n = 0

    def __len__(self) -> int:
        return self._n

    def __repr__(self) -> str:
        return (f"<TimeSeriesBuffer n={self._n} ch={self._n_ch} "
                f"cap={self._cap}>")


def buffer_from_arrays(time_sec: np.ndarray, columns: list) -> TimeSeriesBuffer:
    """由已有的时间轴 + 通道列构造 buffer（文件载入路径用）。"""
    n = len(time_sec)
    n_ch = max(len(columns), 1)
    buf = TimeSeriesBuffer(n_ch, capacity=max(n, 16))
    if n == 0:
        return buf
    mat = np.full((n, n_ch), np.nan, dtype=np.float64)
    for i, col in enumerate(columns):
        col = np.asarray(col, dtype=np.float64)
        mat[:min(n, len(col)), i] = col[:n]
    buf.extend(np.asarray(time_sec, dtype=np.float64), mat)
    return buf
