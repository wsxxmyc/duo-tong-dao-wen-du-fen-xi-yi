"""实时曲线显示抽样工具，不修改原始数据。"""
from __future__ import annotations

from typing import Optional

import numpy as np


class SeriesSampler:
    """按显示容量抽样，保留每个区间的局部最小值和最大值。

    长时间实时采集下采用「梯度密度」分桶：桶宽沿时间从右（最新）到
    左（最老）递增，即最新数据采样更密、历史数据采样更疏。这样右端
    正在增长的实时段能看清曲线细节，而整体趋势仍受 max_points 预算
    约束。梯度指数 GRAD_EXP<1 越大则疏密反差越弱。
    """

    GRAD_EXP = 0.6   # 分桶边界幂指数（<1 → 右密左疏）

    @staticmethod
    def sample(x: np.ndarray, y: np.ndarray,
               max_points: int = 2000) -> tuple[np.ndarray, np.ndarray]:
        """对 (x, y) 抽样到不超过 max_points 个点，保留局部极值与无效值边界。

        向量化实现：连续有效段用分桶 + argmin/argmax 一次性取局部极值，
        Python 循环次数只随「有效段数」变化（正常数据仅 1~2 段），不随
        数据点数增长。旧实现按点逐一遍历 _finite_runs，数据超过
        max_points 后实时采集每帧刷新耗时随数据量线性增长（64 通道 1 小时
        ≈360ms/帧，8 小时 ≈630ms/帧），拖垮 100ms 的合并刷新周期，UI
        线程被占满导致趋势图/点位/时间全部冻结。
        """
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        count = min(len(x), len(y))
        x, y = x[:count], y[:count]
        if count <= max_points or max_points < 3:
            return x.copy(), y.copy()

        finite = np.isfinite(x) & np.isfinite(y)
        runs = SeriesSampler._finite_runs(finite)
        if not runs:
            return x[:1].copy(), y[:1].copy()

        # 连续有效段：每段内按「梯度密度」分桶取局部最小/最大——桶宽从
        # 最新（右）端到最老（左）端递增，实时增长的右端采样更密、历史
        # 段更疏（旧实现等宽 chunk + 向下取整，把 run_len % bins 个最新
        # 点整段压成 min/max 两点，曲线右端出现随点数锯齿涨落的长直线
        # 空洞，且尾部稳态段包络被削平成假平线）。
        # bins 预算按段长比例分配给所有需要降采样的大段，并预先扣减
        # 「短段全量点 + 无效边界」的额度，保证汇总后不超 max_points——
        # 否则末尾的 linspace 均匀抽稀会把部分 bin 的 min/max 极值成对
        # 丢弃，全局视图下细节丢失。
        big_runs = [(a, b) for a, b in runs if b - a > max_points]
        budget = (max_points
                  - sum(b - a for a, b in runs if b - a <= max_points)
                  - 3 * len(big_runs) - 8)
        big_total = sum(b - a for a, b in big_runs)
        if budget >= 2 * len(big_runs):
            bins_total = max(len(big_runs), budget // 2)
        else:
            bins_total = max(1, len(big_runs))

        selected = []
        for a, b in runs:
            run_len = b - a
            if run_len <= max_points:
                selected.extend(range(a, b))
                continue
            bins = max(2, int(round(bins_total * run_len / big_total)))
            edges = SeriesSampler._gradient_edges(a, b, bins)
            yseg = y[a:b]
            starts = edges[:-1] - a
            ids = np.repeat(np.arange(starts.size), np.diff(edges))
            imin, imax = SeriesSampler._bin_extremes(yseg, starts, ids)
            selected.extend((a + imin).tolist())
            selected.extend((a + imax).tolist())

        # 保留无效值边界，防止抽样后把断开的曲线重新连起来。
        invalid = np.flatnonzero(~finite)
        if invalid.size:
            imid = invalid[(invalid > 0) & (invalid < count - 1)]
            if imid.size:
                keep = finite[imid - 1] | finite[imid + 1]
                selected.extend(imid[keep].tolist())
            if invalid[0] == 0:
                selected.append(0)
            if invalid[-1] == count - 1:
                selected.append(count - 1)
        else:
            # 曲线首尾必须始终显示（与 bin 划分位置无关）
            selected.extend((0, count - 1))

        if not selected:
            return x[:1].copy(), y[:1].copy()
        selected = np.unique(selected)
        if selected.size > max_points:
            # 防御兜底：上方预算分配已保证常规数据（少数有效段）不会走到
            # 这里；仅当无效边界/有效段异常多而超出估算时按序均匀保底。
            keep = np.linspace(0, selected.size - 1, max_points, dtype=int)
            selected = selected[keep]
        return x[selected], y[selected]

    @staticmethod
    def window(x: np.ndarray, y: np.ndarray, start: Optional[float] = None,
               end: Optional[float] = None,
               max_points: int = 2000) -> tuple[np.ndarray, np.ndarray]:
        """截取 [start, end] 分钟窗口内的 (x, y) 并抽样。"""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        count = min(len(x), len(y))
        x, y = x[:count], y[:count]
        mask = np.isfinite(x)
        if start is not None:
            mask &= x >= float(start)
        if end is not None:
            mask &= x <= float(end)
        indexes = np.flatnonzero(mask)
        if indexes.size == 0:
            return np.array([], dtype=float), np.array([], dtype=float)
        return SeriesSampler.sample(x[indexes], y[indexes], max_points)

    @staticmethod
    def _gradient_edges(a: int, b: int, bins: int) -> np.ndarray:
        """为 [a, b) 生成梯度分桶边界（右密左疏、铺满整段、无残差）。

        边界取 (i/bins)**GRAD_EXP（GRAD_EXP<1）：边界向新数据一端聚集
        → 右端桶窄（采样密）、左端桶宽（采样疏）。取整后 np.unique
        保证边界严格递增（每桶至少 1 点），末桶右界钉在 b，整段
        [a, b) 被精确覆盖——不存在旧实现那样"没分到桶里的尾巴"。
        """
        run_len = b - a
        t = (np.arange(bins + 1, dtype=float) / bins) ** SeriesSampler.GRAD_EXP
        edges = a + np.round(t * (run_len - 1)).astype(int)
        edges = np.unique(edges)
        edges[-1] = b
        return edges

    @staticmethod
    def _bin_extremes(yseg: np.ndarray, starts: np.ndarray,
                      ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """向量化取每桶最小/最大点的下标（桶内并列值只取第一个）。

        reduceat 求各桶极值，等值掩码找位置，np.unique 的首现索引做
        "每桶第一个"归约——全程无逐桶 Python 循环，总开销 O(段长)。
        前提：yseg 全为有限值（调用方按有效段切片保证）。
        """
        vmin = np.minimum.reduceat(yseg, starts)
        vmax = np.maximum.reduceat(yseg, starts)
        imin = np.flatnonzero(yseg <= vmin[ids])
        imax = np.flatnonzero(yseg >= vmax[ids])
        _, first_min = np.unique(ids[imin], return_index=True)
        _, first_max = np.unique(ids[imax], return_index=True)
        return imin[first_min], imax[first_max]

    @staticmethod
    def _finite_runs(mask):
        """返回连续有效段的 (start, end) 索引区间列表。

        numpy 找断点（diff != 1），Python 循环仅随段数变化；旧实现
        逐点遍历全部索引，是实时刷新链路 O(n) 卡顿的根因之一。
        """
        indexes = np.flatnonzero(mask)
        if indexes.size == 0:
            return []
        d = np.diff(indexes)
        breaks = np.flatnonzero(d != 1)
        starts = np.concatenate(([0], breaks + 1))
        ends = np.concatenate((breaks + 1, [indexes.size]))
        return [(int(indexes[s]), int(indexes[e - 1]) + 1)
                for s, e in zip(starts, ends)]
