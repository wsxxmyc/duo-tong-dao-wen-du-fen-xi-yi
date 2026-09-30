# -*- coding: utf-8 -*-
"""
Session —— 唯一的数据容器。

这是本次重构的核心概念：**取消"文件数据"和"采集数据"的区分**。
两者都是 Session，只是 `source` 不同：

    SOURCE_FILE  静态，从 .tpx/.xls/.csv 载入，行数不再变
    SOURCE_LIVE  动态，串口持续追加，行数在长

所有页面（整体趋势 / 前10·20·30 / 通道对比 / 组合图 / 温升统计 / 实时监控）
一律只面向 Session 编程，**不需要知道数据从哪来**。
采集时页面照常渲染，只是数据在长；打开历史文件时切换活动 Session，
页面拿到的接口完全一样。

兼容性：保留 `time_sec` / `n` / `visible_channels()` 等旧 Dataset 的属性名，
让上层代码可以分阶段迁移，不必一次改完。
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Optional, Union

import numpy as np

from .buffer import TimeSeriesBuffer, buffer_from_arrays
from .channel import Channel, normalize_key

SOURCE_FILE = "file"
SOURCE_LIVE = "live"


class Session:
    """一次数据会话：时间轴 + 通道元信息 + 数据缓冲。"""

    def __init__(self,
                 channels: list[Channel],
                 buffer: TimeSeriesBuffer | None = None,
                 source: str = SOURCE_FILE,
                 title: str = "",
                 path: str = "",
                 interval: float = 1.0):
        self.id = uuid.uuid4().hex[:12]
        self.channels = channels
        self.buffer = buffer or TimeSeriesBuffer(max(len(channels), 1))
        self.source = source
        self.path = path
        self.interval = float(interval) if interval else 1.0
        self.created_at = time.time()
        self.title = title or self._default_title()
        # 采集态
        self.is_recording = False
        self.record_path: str = ""
        self.started_at: float | None = None
        self.stopped_at: float | None = None
        # 处理管线缓存（由 Pipeline 写入，key = 通道 index）
        self.processed: dict = {}
        self.meta: dict = {}

    # ------------------------------------------------------------------
    def _default_title(self) -> str:
        if self.path:
            return os.path.basename(self.path)
        if self.source == SOURCE_LIVE:
            return time.strftime("实时采集 %H:%M:%S", time.localtime(self.created_at))
        return "未命名"

    # ==================================================================
    #  基本查询
    # ==================================================================
    @property
    def n(self) -> int:
        """数据点数。"""
        return self.buffer.n

    @property
    def time_sec(self) -> np.ndarray:
        """时间轴（秒，从 0 起），零拷贝视图。兼容旧 Dataset。"""
        return self.buffer.time

    @property
    def is_live(self) -> bool:
        """是否实时采集会话"""
        return self.source == SOURCE_LIVE

    @property
    def source_path(self) -> str:
        """兼容旧 Dataset.source_path。"""
        return self.path

    @property
    def orig_interval(self) -> float:
        """兼容旧 Dataset.orig_interval。"""
        return self.interval

    def values(self, ch: Union[Channel, int]) -> np.ndarray:
        """取某通道的原始数值视图。ch 可以是 Channel 或列下标。"""
        idx = ch.index if isinstance(ch, Channel) else int(ch)
        return self.buffer.column(idx)

    def visible_channels(self) -> list[Channel]:
        """返回可见（未隐藏）的通道列表"""
        return [c for c in self.channels if c.visible]

    def channel_by_label(self, label: str) -> Channel | None:
        """按完整显示名（含固定通道序号）查找：CH6·准直保护。"""
        return next((c for c in self.channels if c.display_name == label), None)

    def channel_by_index(self, idx: int) -> Optional[Channel]:
        """按列下标查找通道（不存在返回 None）"""
        return next((c for c in self.channels if c.index == idx), None)

    def is_open_circuit(self, ch: Union[Channel, int]) -> bool:
        """通道是否开路 / 无数据（全 NaN）。"""
        if self.is_live:
            # live 会话：append_frame/append_bulk 已增量维护 last_value，
            # 从未出现有限值 ⇔ 整列全 NaN，O(1) 判定替代整列 isnan 扫描
            c = (ch if isinstance(ch, Channel)
                 else self.channel_by_index(int(ch)))
            return getattr(c, "last_value", None) is None
        col = self.values(ch)
        return col.size == 0 or bool(np.all(np.isnan(col)))

    # ==================================================================
    #  写入（仅 LIVE 会话使用）
    # ==================================================================
    def append_frame(self, t: float, values) -> int:
        """追加一帧数据，返回行号。同时更新通道峰值 / 最新值。"""
        row = self.buffer.append(t, values)
        for c in self.channels:
            i = c.index
            v = values[i] if i < len(values) else None
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv != fv:      # NaN
                continue
            c.last_value = fv
            if fv > c.peak:
                c.peak = fv
        return row

    def ensure_channels(self, n_channels: int, config=None) -> None:
        """采集中发现通道数变多时扩容。"""
        if n_channels <= len(self.channels):
            return
        self.buffer.grow_channels(n_channels)
        for i in range(len(self.channels), n_channels):
            key = f"CH{i + 1}"
            ch = Channel(key=key, index=i, source_label=key)
            self.channels.append(ch)
        if config is not None:
            config.apply_all(self.channels)

    # ==================================================================
    #  构造工厂
    # ==================================================================
    @classmethod
    def create_live(cls, n_channels: int, interval: float = 1.0,
                    config=None, title: str = "") -> Session:
        """创建一个空的实时采集会话。"""
        channels = [
            Channel(key=f"CH{i + 1}", index=i, source_label=f"CH{i + 1}")
            for i in range(n_channels)
        ]
        s = cls(channels, TimeSeriesBuffer(n_channels),
                source=SOURCE_LIVE, interval=interval, title=title)
        if config is not None:
            config.apply_all(s.channels)
        return s

    @classmethod
    def from_columns(cls, time_sec: np.ndarray, columns: list,
                     keys: Optional[list[str]] = None, source=SOURCE_FILE,
                     path: str = "", interval: float = 1.0,
                     config=None, title: str = "") -> Session:
        """由时间轴 + 通道列构造（文件载入路径用）。

        Args:
            columns: list[np.ndarray]，每个元素是一个通道的数值序列
            keys:    list[str]，每列的原始标识（物理通道名 / 表头列名）
        """
        n_ch = max(len(columns), 1)
        keys = list(keys or [])
        channels = []
        for i in range(len(columns)):
            raw = keys[i] if i < len(keys) else ""
            channels.append(Channel(
                key=normalize_key(raw, i),
                index=i,
                source_label=(raw or f"CH{i + 1}"),
            ))
        buf = buffer_from_arrays(np.asarray(time_sec, dtype=float), columns)
        s = cls(channels, buf, source=source, path=path,
                interval=interval, title=title)
        if config is not None:
            config.apply_all(s.channels)
        # 开路通道默认不勾选（保持旧行为）
        for c in s.channels:
            if s.is_open_circuit(c):
                c.visible = False
                c.enabled = False
        return s

    # ==================================================================
    def duration_sec(self) -> float:
        """会话总时长（秒），不足两个数据点时为 0"""
        t = self.buffer.time
        return float(t[-1] - t[0]) if t.size >= 2 else 0.0

    def __repr__(self) -> str:
        return (f"<Session {self.id} {self.source} '{self.title}' "
                f"n={self.n} ch={len(self.channels)}"
                f"{' REC' if self.is_recording else ''}>")
