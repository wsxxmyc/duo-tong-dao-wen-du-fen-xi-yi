# -*- coding: utf-8 -*-
"""
Pipeline —— 数据处理管线（异常剔除 → 填充 → 重采样 → 平滑）+ 增量缓存。

为什么需要它
------------
旧 `MainWindow._compute_processed()` 是**全量重算**：每次刷新遍历所有通道
跑一遍全部算法。文件模式下无所谓，但实时采集每秒来一帧，
全量重算会随数据增长越来越卡（O(n) → 每秒 O(n)，累计 O(n²)）。

本管线提供两条路径：
  - `recompute()`      全量：参数变化 / 载入新会话时走
  - `on_appended()`    增量：只重算末尾一个"暖机窗口"，复杂度 O(window)

修正的旧 bug
------------
旧代码用 `channel.display_name` 作为 processed 字典的键。
用户一旦把两个通道改成同名，缓存就会互相覆盖、数据串台；
改名后旧缓存也不会失效。本管线一律用 **通道列下标 index** 作键。

无 Qt 依赖，可单独测试。
"""
from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from .session import Session

try:
    from utils import core
except ImportError:                                    # pragma: no cover
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils import core


DEFAULT_PARAMS = {
    "interval": 10.0,
    "start_sec": 0.0,       # 时间轴重标定起点（秒），替代旧的 QTime 对象
    "relabel": False,
    "resample": False,
    "resample_int": 0.0,
    "resample_method": 0,   # 0=mean 1=nearest
    "diff_en": False, "diff_thr": 5.0,
    "z_en": False, "z_win": 11, "z_sig": 3.0,
    "slope_en": False, "slope_thr": 1.0,
    "fill_k": 3,
    "smooth_en": False, "smooth_win": 5,
}

# 增量重算时向前多取的暖机点数下限
MIN_WARMUP = 16


class Pipeline:
    """带增量缓存的处理管线。一个 Pipeline 服务一个 Session。"""

    def __init__(self, params: dict | None = None):
        self.params = dict(DEFAULT_PARAMS)
        if params:
            self.params.update(params)
        self._dirty = True
        # 参数代数：set_params 实质变化时递增，供上层取数缓存
        # （ChannelPanel.visible_series 记忆化）识别"行数未变但结果
        # 已变"的失效场景（改平滑/重采样参数后不得返回旧数组）
        self.version = 0

    # ==================================================================
    def set_params(self, params: dict) -> bool:
        """更新参数。发生实质变化时置脏并返回 True。"""
        changed = False
        for k, v in params.items():
            if self.params.get(k) != v:
                self.params[k] = v
                changed = True
        if changed:
            self._dirty = True
            self.version += 1
        return changed

    @property
    def is_dirty(self) -> bool:
        """处理缓存是否已置脏（参数变化或数据追加后需要重算）"""
        return self._dirty

    # ------------------------------------------------------------------
    @property
    def _reshapes_axis(self) -> bool:
        """当前参数是否会改变时间轴形状（重采样/重标定）。
        这类参数下无法做增量，只能全量重算。"""
        p = self.params
        return bool(p.get("resample") and p.get("resample_int", 0) > 0) or bool(p.get("relabel"))

    @property
    def warmup(self) -> int:
        """左侧暖机长度。

        对一个**切片**跑 rolling 算法时，切片开头的若干点因为缺少左侧邻居，
        会用 min_periods 兜底算出与全量不同的值。这段污染区必须丢弃，
        所以增量重算要向前多取 warmup 个点，且**只采用 warmup 之后的结果**。
        """
        p = self.params
        w = MIN_WARMUP
        if p.get("z_en"):
            w = max(w, int(p.get("z_win", 11)) * 2)
        if p.get("smooth_en"):
            w = max(w, int(p.get("smooth_win", 5)) * 2)
        w = max(w, int(p.get("fill_k", 3)) * 4)
        return w

    @property
    def lookback(self) -> int:
        """右侧未确定长度。

        rolling(center=True) 的第 i 个点依赖 [i-r, i+r]，其中 r = win//2。
        因此数据到达 n 时，末尾 r 个点算出的只是**临时值**，
        必须在后续帧到来时重算修正，否则增量结果永远收敛不到全量结果。
        取整个窗口长度（> r）留足余量。
        """
        p = self.params
        lb = 1
        if p.get("z_en"):
            lb = max(lb, int(p.get("z_win", 11)))
        if p.get("smooth_en"):
            lb = max(lb, int(p.get("smooth_win", 5)))
        return lb

    # ==================================================================
    #  核心算法（单通道）
    # ==================================================================
    def _process_column(self, t: np.ndarray, v: np.ndarray):
        """对一段 (t, v) 执行完整算法链，返回 (t, v, anomaly)。"""
        p = self.params
        t = np.asarray(t, dtype=float).copy()
        v = np.asarray(v, dtype=float).copy()

        if p.get("relabel"):
            t = core.relabel_time(len(v), float(p.get("start_sec", 0.0)),
                                  float(p.get("interval", 10.0)))

        anomaly = np.zeros(len(v), dtype=bool)
        if p.get("diff_en"):
            anomaly |= core.detect_diff_threshold(v, float(p["diff_thr"]))
        if p.get("z_en"):
            anomaly |= core.detect_zscore(v, int(p["z_win"]), float(p["z_sig"]))
        if p.get("slope_en"):
            anomaly |= core.detect_slope(v, t, float(p["slope_thr"]))

        if anomaly.any():
            v = core.fill_neighbor_mean(v, anomaly, k=int(p.get("fill_k", 3)))

        if p.get("resample") and float(p.get("resample_int", 0)) > 0:
            method = "mean" if int(p.get("resample_method", 0)) == 0 else "nearest"
            t, v = core.resample(t, v, float(p["resample_int"]), method=method)
            anomaly = np.zeros(len(v), dtype=bool)

        if p.get("smooth_en") and int(p.get("smooth_win", 1)) > 1:
            v = core.moving_average(v, int(p["smooth_win"]))

        return t, v, anomaly

    # ==================================================================
    #  全量 / 增量
    # ==================================================================
    def recompute(self, session: "Session") -> dict:
        """全量重算整个会话。结果写入 session.processed（键=通道 index）。"""
        out = {}
        t_all = session.buffer.time
        if t_all.size == 0:
            session.processed = out
            self._dirty = False
            return out
        for c in session.channels:
            v = session.buffer.column(c.index)
            out[c.index] = self._process_column(t_all, v)
        session.processed = out
        self._dirty = False
        return out

    def on_appended(self, session: "Session", start_row: int, count: int) -> bool:
        """增量更新。返回 True 表示走了增量路径，False 表示已回退到全量。

        重算区间的三段划分（n_old = 新数据到达前的行数）::

            0 ──────────────── seg_start ──── adopt ──── n_old ── n
            │      保留旧结果        │  暖机丢弃  │  重算采用   │ 新增 │
                                    └──── 实际送进算法的切片 ─────┘

        - `seg_start = n_old - lookback - warmup`：切片起点
        - 切片内前 `warmup` 个点受左边界效应污染 → 丢弃，用旧值
        - `[adopt, n)` 全部采用新算的值，其中 `[adopt, n_old)` 是对
          上一轮"临时值"（rolling 未来数据不足）的修正

        这样增量结果最终与全量重算逐点一致（已由 T7 测试锁定）。
        """
        if self._dirty or self._reshapes_axis or not session.processed:
            self.recompute(session)
            return False

        n = session.n
        n_old = max(0, start_row)
        w = self.warmup
        seg_start = max(0, n_old - self.lookback - w)
        # 切片从头开始时无左边界效应，无需丢弃
        adopt = seg_start + w if seg_start > 0 else 0
        drop = adopt - seg_start

        t_seg = session.buffer.time[seg_start:n]
        if t_seg.size == 0:
            return True

        for c in session.channels:
            cached = session.processed.get(c.index)
            v_seg = session.buffer.column(c.index)[seg_start:n]
            t_new, v_new, a_new = self._process_column(t_seg, v_seg)
            if cached is None or len(cached[0]) < adopt:
                # 缓存不可用 → 该通道整段重算
                t_full = session.buffer.time[:n]
                session.processed[c.index] = self._process_column(
                    t_full, session.buffer.column(c.index)[:n])
                continue
            ct, cv, ca = cached[0], cached[1], cached[2]
            session.processed[c.index] = (
                np.concatenate([ct[:adopt], t_new[drop:]]),
                np.concatenate([cv[:adopt], v_new[drop:]]),
                np.concatenate([ca[:adopt], a_new[drop:]]),
            )
        return True

    # ==================================================================
    #  读取
    # ==================================================================
    def get(self, session: "Session", ch) -> Optional[tuple]:
        """取某通道的处理结果 (t, v, anomaly)。缓存缺失时自动补算。"""
        idx = ch.index if hasattr(ch, "index") else int(ch)
        res = session.processed.get(idx)
        if res is None:
            if self._dirty or not session.processed:
                self.recompute(session)
            else:
                t = session.buffer.time
                res = self._process_column(t, session.buffer.column(idx))
                session.processed[idx] = res
            res = session.processed.get(idx)
        return res

    def series_minutes(self, session: "Session", ch,
                       max_minutes: float | None = None) -> tuple[np.ndarray, np.ndarray]:
        """取 (分钟x, 温度v)，供绘图直接使用。"""
        res = self.get(session, ch)
        if res is None:
            return np.array([]), np.array([])
        t, v = res[0], res[1]
        if t.size == 0:
            return t, v
        x = (t - t[0]) / 60.0
        if max_minutes is not None:
            m = x <= max_minutes
            return x[m], v[m]
        return x, v

    def series_window(self, session: "Session", ch,
                      start_min: Optional[float] = None,
                      end_min: Optional[float] = None) -> tuple[np.ndarray, np.ndarray]:
        """取指定分钟窗口内的 (x, v)。"""
        x, v = self.series_minutes(session, ch)
        if x.size == 0:
            return x, v
        m = np.ones(len(x), dtype=bool)
        if start_min is not None:
            m &= x >= start_min
        if end_min is not None:
            m &= x <= end_min
        return x[m], v[m]
