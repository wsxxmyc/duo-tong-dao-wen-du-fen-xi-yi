# -*- coding: utf-8 -*-
"""
core.py —— 温度爬升分析工具 数据核心层
职责：文件加载（编码/分隔符自适应、开路识别）、异常检测（3 算法）、
      邻域均值填充、平滑、重采样、时间轴重标定、统计分析。
不依赖任何 UI，可单独测试。
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd


# ----------------------------------------------------------------------------
# 数据模型
# ----------------------------------------------------------------------------
class ChannelData:
    """单个通道的数据与配置"""

    # 预置调色板（红涨绿跌之外的通用配色，保证多通道可区分）
    # 2026-08-30 隐患色替换（与 device/datastore/channel.DEFAULT_PALETTE
    # 同步）：#9B59B6→#9D6BCA、#34495E→#8FA3C7。
    # 旧色对 7 套主题深画布（PLOT_FACE）对比度不足：#34495E 仅
    # 1.49~1.82:1（曲线上不可见）、#9B59B6 2.97~3.61:1 且 4 套 <3.2。
    # 新色验算（WCAG 对比度 + CIEDE2000，实现同 test_theme_contrast）：
    #   #9D6BCA 对 7 画布 3.56~4.33:1；#8FA3C7 对 7 画布 5.44~6.62:1；
    #   替换后前 8 通道两两 ΔE2000 最小 10.8（第 4/7 位，原有色对），
    #   新引入色对 第2/8=13.9、第5/8=19.3，全表相邻最小 34.7，均 ≥8.0。
    PALETTE = [
        "#E74C3C", "#3498DB", "#2ECC71", "#F39C12", "#9D6BCA",
        "#1ABC9C", "#E67E22", "#8FA3C7", "#FF6B9D", "#16A085",
    ]

    def __init__(self, name: str, values: np.ndarray, color: str, idx: int = 0,
                 physical_name: str = ""):
        self.orig_name = name          # 顺序列名，如 CH1（统一命名，保证 .xls/.tpx 一致）
        self.display_name = name       # 用户可改的显示名
        self.physical_name = physical_name or name  # 物理通道名（如 CH64/CH7），可溯源
        self.raw = values.astype(float)  # 原始数值（开路/非数值为 nan）
        self.color = color
        # 有效数据点数量为 0 判定为"开路/无数据"
        self.is_open = np.all(np.isnan(self.raw))
        # 默认：有数据的通道显示，开路通道不显示（用户可手动改）
        self.visible = not self.is_open
        self.idx = idx


class Dataset:
    """整份数据集：时间轴（秒） + 若干通道"""

    def __init__(self, time_sec: np.ndarray, channels: list[ChannelData],
                 orig_interval: float, source_path: str = ""):
        self.time_sec = time_sec.astype(float)   # 原始时间（秒）
        self.channels = channels
        self.orig_interval = orig_interval        # 原始采集间隔（秒）
        self.source_path = source_path

    @property
    def n(self) -> int:
        """数据点数量（时间轴长度）"""
        return len(self.time_sec)

    def visible_channels(self) -> list[ChannelData]:
        """返回所有可见（未隐藏）的通道列表"""
        return [c for c in self.channels if c.visible]


# ----------------------------------------------------------------------------
# 文件加载
# ----------------------------------------------------------------------------
def _try_read_text(path: str) -> pd.DataFrame:
    """读取制表符/逗号分隔的文本（自动尝试多编码/分隔符）"""
    encodings = ["utf-8-sig", "gbk", "gb18030", "utf-8", "latin1"]
    seps = ["\t", ",", ";"]
    last_err = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                sample = f.read(4096)
        except Exception as e:      # 编码不匹配
            last_err = e
            continue
        # 猜分隔符
        sep = "\t"
        best = -1
        for s in seps:
            c = sample.split("\n")[0].count(s)
            if c > best:
                best, sep = c, s
        try:
            df = pd.read_csv(path, sep=sep, encoding=enc, engine="python")
            if df.shape[1] >= 2:
                return df
        except Exception as e:
            last_err = e
            continue
    raise ValueError(f"无法解析文本文件：{last_err}")


def parse_time_column(col: pd.Series) -> tuple[np.ndarray, float]:
    """把时间列解析成“秒”数组，并推断采集间隔。
    支持 HH:MM:SS / MM:SS / 纯数字(秒) 。"""
    def to_sec(v):
        if pd.isna(v):
            return np.nan
        s = str(v).strip()
        if ":" in s:
            parts = s.split(":")
            try:
                parts = [float(p) for p in parts]
            except ValueError:
                return np.nan
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            return parts[0]
        try:
            return float(s)
        except ValueError:
            return np.nan

    sec = col.map(to_sec).to_numpy(dtype=float)
    # 推断间隔：相邻差的中位数
    diffs = np.diff(sec)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    interval = float(np.median(diffs)) if diffs.size else 10.0
    return sec, interval


# ----------------------------------------------------------------------------
# TPX 二进制格式 (.tpx) 解析
# ----------------------------------------------------------------------------
# 格式布局（经 8 份 .tpx 样本 + 4 对 .xls↔.tpx 交叉验证，8374 格 100% 一致）：
#   头部 0x00: [1B len]"TP-X" + u32(版本=2000) + 6 个长度前缀字符串 + 4 字节 flag
#   通道表: 64 项，每项 [1B len]['CHx'][4B 使能标志 u32]，CH1..CH64
#     ⚠ 使能标志 u32 非零即启用，某些固件使能位在 byte 1（如 0x00010000=256）
#   表尾: [u32] 块计数（不可靠，部分文件为 0，不依赖）
#   数据区: 重复的数据块，每块 = (启用通道数+1) 个 float32 + 1 个 u32 计数器
#     块内布局: [v0 镜像][v1..vM: M 个启用通道值][计数器]
#     v0 是 v1(CH1) 的完全相同副本 → 丢弃
#     计数器递增 → 作为时间轴（秒），首值 = 间隔，需减去使从 0 起
#     缺测值 65535/65534 → NaN（开路/无数据）
# 通道命名按"启用顺序重编号"：第 N 个启用通道 → CH(N)，与上位机 xls 导出列名一致。
_TPX_MISSING = (65535.0, 65534.0)  # 开路 / 无数据 的缺测标记值


def _parse_tpx(path: str) -> Dataset:
    """解析 TPX 二进制工程文件为 Dataset。"""
    with open(path, "rb") as f:
        data = f.read()
    n = len(data)

    def _lp(off):
        """读 1 字节长度前缀字符串，返回 (字符串, 新偏移)。"""
        ln = data[off]
        off += 1
        s = data[off:off + ln].decode("utf-8", errors="replace")
        return s, off + ln

    # ---- 头部 ----
    off = 0
    magic, off = _lp(off)                      # "TP-X"
    if magic != "TP-X":
        raise ValueError(f"文件魔数不匹配（魔数={magic!r}）")
    off += 4                                   # u32 版本
    for _ in range(6):                         # 6 个长度前缀字段
        _, off = _lp(off)
    off += 4                                   # 4 字节 flag

    # ---- 通道表 0x31 起：64 项，收集启用通道（保持顺序）----
    # 使能标志为 4 字节 u32，非零即启用。
    # 注意：某些固件版本将使能位写在 byte 1 而非 byte 0
    #   （如 0x00010000 = 256），因此必须检查整个 u32。
    enabled_names = []
    for _ in range(64):
        name, off = _lp(off)                   # 通道物理名，如 "CH6" / "CH64"
        en_u32 = struct.unpack_from("<I", data, off)[0]
        if en_u32:
            enabled_names.append(name.strip())
        off += 4
    n_enabled = len(enabled_names)
    off += 4                                   # 表尾记录块数计数（不可靠，跳过）

    data_start = off
    if n_enabled == 0:
        raise ValueError("数据文件无启用通道")

    # ---- 数据区：每块 = (启用通道数+1) float32 + 1 u32 计数器 ----
    # 块内 [v0镜像][v1..vM][计数器]，M = n_enabled
    block_floats = n_enabled + 1               # 含 v0 镜像
    block_size = block_floats * 4 + 4          # + 计数器 u32
    total = n - data_start
    if total <= 0:
        raise ValueError("数据文件无数据区")
    # 允许末尾残缺（不足一块的字节，常见于末条计数器缺失），仅解析完整块
    n_blocks = total // block_size
    remainder = total % block_size
    if n_blocks == 0:
        raise ValueError(
            f"数据区({total}B)不足一块({block_size}B)，无法解析"
            f"（启用通道数={n_enabled}），文件可能损坏或通道数推断有误")

    # 向量化读取：每块解 block_floats 个 float + 1 个 u32
    rec_fmt = f"<{block_floats}fI"
    vals = np.empty((n_blocks, n_enabled), dtype=float)   # 通道值（已丢 v0）
    time_cnt = np.empty(n_blocks, dtype=float)
    for k in range(n_blocks):
        row = struct.unpack_from(rec_fmt, data, data_start + k * block_size)
        # row[0] 是 v0 镜像，丢弃；row[1..n_enabled] 是通道值；末位是计数器
        vals[k, :] = row[1:1 + n_enabled]
        time_cnt[k] = row[-1]

    # 缺测值 → NaN
    miss_mask = np.isin(vals, _TPX_MISSING)
    vals[miss_mask] = np.nan

    # ---- 时间轴：计数器作为秒数，减去首值使从 0 起 ----
    # TPX 计数器 = (k+1) × 间隔，即第一条数据的计数器 = 间隔值（如 60 或 10）。
    # 上位机 XLS 导出的时间从 00:00:00 开始，为保持一致需减去首值。
    if n_blocks > 0 and time_cnt[0] != 0:
        time_cnt = time_cnt - time_cnt[0]
    time_sec, interval = parse_time_column(pd.Series(time_cnt))

    # ---- 通道构建：按启用顺序重编号 CH1, CH2, ...
    # 上位机 XLS 导出始终使用顺序列名（CH1~CHn），
    # 无论物理通道号是多少（如末通道物理为 CH64，XLS 仍标 CHn）。
    # 为保证 .tpx 与 .xls 解析结果完全一致，统一使用顺序命名。
    # 同时保留物理通道名到 physical_name，便于溯源。
    channels: list[ChannelData] = []
    for i in range(n_enabled):
        color = ChannelData.PALETTE[i % len(ChannelData.PALETTE)]
        name = f"CH{i + 1}"
        phys = enabled_names[i] if i < len(enabled_names) else name
        channels.append(ChannelData(name, vals[:, i], color, i, phys))

    return Dataset(time_sec, channels, interval, path)


def load_file(path: str) -> Dataset:
    """主入口：加载任意支持的文件为 Dataset"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".tpx":
        return _parse_tpx(path)
    if ext in (".xlsx", ".xlsm"):
        df = pd.read_excel(path, engine="openpyxl")
    elif ext == ".xls":
        # 该项目的 .xls 实为文本，优先按文本解析；失败再按真正 xls
        try:
            df = _try_read_text(path)
        except Exception:
            df = pd.read_excel(path)
    else:  # .csv / .txt / 其它文本
        df = _try_read_text(path)

    if df.shape[1] < 2:
        raise ValueError("文件至少需要 时间列 + 1 个通道列")

    # 第一列作为时间
    time_col = df.iloc[:, 0]
    time_sec, interval = parse_time_column(time_col)

    # 其余列作为通道
    # 统一使用顺序命名 CH1, CH2, ...（与 TPX 解析保持一致）
    # 原始列名保留到 physical_name（可能是物理通道名如 CH7/CH8）
    channels: list[ChannelData] = []
    ci = 0
    for col_name in df.columns[1:]:
        raw = pd.to_numeric(df[col_name], errors="coerce").to_numpy(dtype=float)
        # 65535/65534 → NaN（与 TPX 统一：开路/无数据标记值）
        miss_mask = np.isin(raw, _TPX_MISSING)
        if miss_mask.any():
            raw[miss_mask] = np.nan
        color = ChannelData.PALETTE[ci % len(ChannelData.PALETTE)]
        seq_name = f"CH{ci + 1}"
        phys_name = str(col_name).strip()
        channels.append(ChannelData(seq_name, raw, color, ci, phys_name))
        ci += 1

    # 清理时间为 nan 的行（极少见）
    mask = np.isfinite(time_sec)
    if not np.all(mask):
        time_sec = time_sec[mask]
        for c in channels:
            c.raw = c.raw[mask]
            c.is_open = bool(np.all(np.isnan(c.raw)))
            c.visible = not c.is_open

    return Dataset(time_sec, channels, interval, path)


# ----------------------------------------------------------------------------
# 异常检测（3 种算法，返回布尔掩码 True=异常）
# ----------------------------------------------------------------------------
def detect_diff_threshold(values: np.ndarray, thr: float) -> np.ndarray:
    """相邻点温差阈值：|x[i]-x[i-1]| > thr 判为异常"""
    v = values
    mask = np.zeros(len(v), dtype=bool)
    d = np.abs(np.diff(v))
    idx = np.where(d > thr)[0] + 1     # 突变发生在后一个点
    mask[idx] = True
    return mask


def detect_zscore(values: np.ndarray, window: int, n_sigma: float) -> np.ndarray:
    """滑动窗口 Z-score：偏离窗口均值 n_sigma 倍标准差判为异常"""
    v = pd.Series(values)
    mp = max(3, window // 2)
    med = v.rolling(window, center=True, min_periods=mp).mean()
    std = v.rolling(window, center=True, min_periods=mp).std()
    std = std.replace(0, np.nan)
    z = ((v - med).abs() / std).to_numpy()
    mask = np.zeros(len(values), dtype=bool)
    valid = np.isfinite(z)
    mask[valid] = z[valid] > n_sigma
    return mask


def detect_slope(values: np.ndarray, time_sec: np.ndarray, rate_thr: float) -> np.ndarray:
    """变化率（斜率）：|Δ温度/Δ时间| > rate_thr(℃/秒) 判为异常"""
    mask = np.zeros(len(values), dtype=bool)
    dt = np.diff(time_sec)
    dt[dt == 0] = np.nan
    rate = np.abs(np.diff(values) / dt)
    idx = np.where(rate > rate_thr)[0] + 1
    mask[idx] = True
    return mask


def fill_neighbor_mean(values: np.ndarray, anomaly_mask: np.ndarray,
                       k: int = 3) -> np.ndarray:
    """把异常点置为邻域有效点的均值（前后各取 k 个有效点）。
    若邻域不足则退化为线性插值。"""
    v = values.copy()
    v[anomaly_mask] = np.nan
    idx_all = np.arange(len(v))
    valid = np.isfinite(v)
    if valid.sum() == 0:
        return values.copy()

    out = v.copy()
    for i in np.where(anomaly_mask)[0]:
        left = [j for j in range(i - 1, -1, -1) if valid[j]][:k]
        right = [j for j in range(i + 1, len(v)) if valid[j]][:k]
        nb = left + right
        if nb:
            out[i] = np.mean(v[nb])
    # 仍有 nan（连续异常导致邻域取不到）→ 线性插值兜底
    still = ~np.isfinite(out)
    if still.any():
        out[still] = np.interp(idx_all[still], idx_all[~still], out[~still])
    return out


def moving_average(values: np.ndarray, window: int) -> np.ndarray:
    """整体移动平均平滑"""
    if window <= 1:
        return values.copy()
    return pd.Series(values).rolling(window, center=True, min_periods=1).mean().to_numpy()


# ----------------------------------------------------------------------------
# 重采样 & 时间轴重标定
# ----------------------------------------------------------------------------
def resample(time_sec: np.ndarray, values: np.ndarray,
             new_interval: float, method: str = "mean") -> tuple[np.ndarray, np.ndarray]:
    """按新间隔重采样。method: mean(区间平均) / nearest(最近抽取)"""
    if new_interval <= 0:
        return time_sec.copy(), values.copy()
    t0, t1 = time_sec[0], time_sec[-1]
    new_t = np.arange(t0, t1 + new_interval / 2, new_interval)
    if method == "nearest":
        idx = np.searchsorted(time_sec, new_t)
        idx = np.clip(idx, 0, len(time_sec) - 1)
        return new_t, values[idx]
    # mean：把原始点归入每个新区间求均值
    new_v = np.full(len(new_t), np.nan)
    bins = np.digitize(time_sec, new_t - new_interval / 2)
    s = pd.Series(values).groupby(bins).mean()
    for b, val in s.items():
        bi = int(b) - 1
        if 0 <= bi < len(new_v):
            new_v[bi] = val
    # 空区间线性插值兜底
    nanm = ~np.isfinite(new_v)
    if nanm.any() and (~nanm).sum() >= 2:
        new_v[nanm] = np.interp(new_t[nanm], new_t[~nanm], new_v[~nanm])
    return new_t, new_v


def relabel_time(n_points: int, start_sec: float, interval: float) -> np.ndarray:
    """按新的起始时刻 + 间隔重新标定时间轴（不改数据点数量）"""
    return start_sec + np.arange(n_points) * interval


def sec_to_hms(sec: float) -> str:
    """将秒数格式化为 HH:MM:SS 字符串"""
    sec = int(round(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ----------------------------------------------------------------------------
# 温升三阶段自适应分析
# ----------------------------------------------------------------------------
PHASE_FAST = "fast"
PHASE_SLOW = "slow"
PHASE_STEADY = "steady"


@dataclass(frozen=True)
class RiseAnalysisConfig:
    """温升分析参数，只调节相对趋势分析灵敏度。"""

    filter_window_sec: float = 60.0
    steady_duration_sec: float = 300.0
    slope_decay_sensitivity: float = 0.50

    def normalized(self) -> RiseAnalysisConfig:
        """校正非法参数，避免设置值导致分析崩溃。"""
        try:
            filter_window = max(float(self.filter_window_sec), 1.0)
        except (TypeError, ValueError):
            filter_window = 60.0
        try:
            steady_duration = max(float(self.steady_duration_sec), 1.0)
        except (TypeError, ValueError):
            steady_duration = 300.0
        try:
            sensitivity = float(self.slope_decay_sensitivity)
        except (TypeError, ValueError):
            sensitivity = 0.50
        return RiseAnalysisConfig(
            filter_window_sec=filter_window,
            steady_duration_sec=steady_duration,
            slope_decay_sensitivity=min(max(sensitivity, 0.10), 0.90),
        )


@dataclass(frozen=True)
class AlarmConfig:
    """温度报警参数（全局统一阈值 + 各动作开关 + Modbus 输出参数）。

    仅在在线实时采集时生效（main_window._on_acq_data 判定），离线导入不判定。
    报警信号通过独立串口以 Modbus RTU 输出，与采集协议相互隔离。
    详见 文档-docs/温度报警功能-alarm-feature-design-2026-08-12.md。
    """

    enabled: bool = True
    temp_high: float = 100.0       # 上限（℃）；下限已于 2026-08-22 移除（业务只保留上限）
    follow_axis: bool = True       # 报警上限智能跟随温度轴基础窗口上限
    follow_offset: float = 2.0     # 跟随偏移：报警上限 = 轴基础上限 - 该值
    rate_threshold: float = 10.0   # 变化率阈值（℃/s）
    diff_threshold: float = 20.0   # 通道间温差阈值（℃）
    # 恢复滞回（2026-09-23 报警风暴整改 P0-1，见整改计划 §4 P0-1）：
    # 上限恢复需 v ≤ temp_high - clear_margin，且恢复态保持达 clear_hold_sec
    # 才发 cleared；clear_margin=0 且 clear_hold_sec=0 时上限恢复语义与旧版一致
    # （变化率/温差恢复带宽为固定工业惯例，见 ALARM_RATE_CLEAR_FACTOR 等常量）
    clear_margin: float = 1.0      # 上限恢复滞回带（℃）
    clear_hold_sec: float = 3.0    # 恢复保持时长（秒），0=立即恢复
    # 各动作开关
    act_highlight: bool = True
    act_sound: bool = True
    act_popup: bool = True
    act_log: bool = True
    act_serial: bool = False
    # Modbus RTU 输出参数（独立串口，零侵入采集线程）
    serial_port: str = ""          # 空=不输出
    serial_baud: int = 9600
    modbus_slave: int = 1          # 从站地址（1-247）
    modbus_coil: int = 0           # 线圈地址（0-65535）
    sound_file: str = ""           # 空=蜂鸣，否则 .wav 路径

    def normalized(self) -> "AlarmConfig":
        """校正非法参数：上限阈值、Modbus 地址范围、类型修正。

        返回新的 frozen 实例；任何字段类型异常时回落到对应默认值。
        """
        def _float(value, default):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        def _int(value, default, lo, hi):
            try:
                v = int(value)
            except (TypeError, ValueError):
                return default
            return max(lo, min(hi, v))

        return AlarmConfig(
            enabled=bool(self.enabled),
            temp_high=_float(self.temp_high, 100.0),
            follow_axis=bool(self.follow_axis),
            follow_offset=min(max(_float(self.follow_offset, 2.0), 0.0), 50.0),
            rate_threshold=max(_float(self.rate_threshold, 10.0), 0.0),
            diff_threshold=max(_float(self.diff_threshold, 20.0), 0.0),
            act_highlight=bool(self.act_highlight),
            act_sound=bool(self.act_sound),
            act_popup=bool(self.act_popup),
            act_log=bool(self.act_log),
            act_serial=bool(self.act_serial),
            serial_port=str(self.serial_port or ""),
            serial_baud=_int(self.serial_baud, 9600, 1, 115200),
            modbus_slave=_int(self.modbus_slave, 1, 1, 247),
            modbus_coil=_int(self.modbus_coil, 0, 0, 65535),
            sound_file=str(self.sound_file or ""),
            clear_margin=min(max(_float(self.clear_margin, 1.0), 0.0), 50.0),
            clear_hold_sec=min(max(_float(self.clear_hold_sec, 3.0), 0.0), 60.0),
        )


# ---- 报警判定（纯函数，无 UI 依赖）----
# 报警类型常量；channel_idx 为 None 表示帧级报警（通道间温差）
# （下限报警 ALARM_LOW 已于 2026-08-22 随下限业务整体移除）
ALARM_HIGH = "high"
ALARM_RATE = "rate"
ALARM_DIFF = "diff"

# 恢复滞回固定带宽（变化率/温差，与 cfg.clear_margin 独立）
ALARM_RATE_CLEAR_FACTOR = 0.7    # 变化率恢复需 rate ≤ threshold × 0.7
ALARM_DIFF_CLEAR_MARGIN_C = 2.0  # 温差恢复需 diff ≤ threshold - 2.0℃

# 报警状态机相位
ALARM_PHASE_NORMAL = "normal"
ALARM_PHASE_ACTIVE = "active"
ALARM_PHASE_CLEARING = "clearing"   # 已越出滞回带、保持时长未满（对外仍视为报警中）


def alarm_phase(state):
    """states 值 → 相位字符串。值形态："normal"|"active"|("clearing", since_ts)。"""
    if isinstance(state, tuple):
        return state[0]
    return state or ALARM_PHASE_NORMAL


def alarm_clear_since(state):
    """clearing 相位的恢复起点时间戳；非 clearing 返回 None。"""
    return state[1] if isinstance(state, tuple) else None


def alarm_active(state):
    """该状态是否仍属"报警中"（active 或 clearing，对外语义一致）。"""
    return alarm_phase(state) != ALARM_PHASE_NORMAL


def _recovery_satisfied(key, values, prev_map, timestamp, cfg, valid_set):
    """active/clearing 态的恢复条件（含滞回带）是否满足（纯判定）。

    无法计算的指标（无上一有效点、有效通道不足）沿用旧语义视为"不违规=恢复"。
    """
    ch_idx, atype = key
    if atype == ALARM_HIGH:
        v = values[ch_idx]
        return v is not None and v <= cfg.temp_high - cfg.clear_margin
    if atype == ALARM_RATE:
        v = values[ch_idx]
        prev = prev_map.get(ch_idx)
        if v is None or prev is None:
            return True
        dt = timestamp - prev[0]
        if dt <= 0:
            return True
        rate = abs(v - prev[1]) / dt
        return rate <= cfg.rate_threshold * ALARM_RATE_CLEAR_FACTOR
    # ALARM_DIFF（帧级）
    if len(valid_set) < 2:
        return True
    valid_vals = [values[i] for i in valid_set if values[i] is not None]
    if len(valid_vals) < 2:
        return True
    return (max(valid_vals) - min(valid_vals)
            <= cfg.diff_threshold - ALARM_DIFF_CLEAR_MARGIN_C)


def evaluate_channel_alarms(values, prev_map, timestamp, cfg):
    """计算单帧各通道的报警违规（纯函数，无状态、无 UI）。

    Args:
        values: 逐通道温度 list，None 表示无效通道（跳过，不参与判定）。
        prev_map: ``{channel_idx: (prev_timestamp, prev_value)}`` 各通道上一有效点。
        timestamp: 当前帧绝对时间戳（秒）。
        cfg: ``AlarmConfig``（建议已 normalized）。

    Returns:
        ``(violations, valid_set, new_prev_map)``：
          - violations: ``{(channel_idx|None, alarm_type): actual_value}``
          - valid_set: 本帧有效通道索引集合
          - new_prev_map: 更新后的 prev_map 副本
    """
    violations = {}
    new_prev = dict(prev_map)
    valid_indices = []
    for i, v in enumerate(values):
        if v is None:
            continue
        valid_indices.append(i)
        # 上限
        if v > cfg.temp_high:
            violations[(i, ALARM_HIGH)] = v
        # 变化率（需上一有效点）
        prev = prev_map.get(i)
        if prev is not None:
            pt, pv = prev
            dt = timestamp - pt
            if dt > 0:
                rate = abs(v - pv) / dt
                if rate > cfg.rate_threshold:
                    violations[(i, ALARM_RATE)] = rate
        new_prev[i] = (timestamp, v)
    # 通道间温差（帧级，channel_idx=None）
    if len(valid_indices) >= 2:
        valid_vals = [values[i] for i in valid_indices]
        diff = max(valid_vals) - min(valid_vals)
        if diff > cfg.diff_threshold:
            violations[(None, ALARM_DIFF)] = diff
    return violations, set(valid_indices), new_prev


def step_alarm(values, prev_map, states, timestamp, cfg):
    """单帧报警判定 + 三态锁定状态机推进（纯函数，无 UI）。

    状态机：每 ``(channel_idx|None, alarm_type)`` 独立维护
    ``normal`` / ``active`` / ``("clearing", 恢复起点)``。

    - normal 且本帧违规 → active（产生 active 事件）
    - active 且本帧违规 → 保持 active（不重复发事件）
    - clearing 且本帧违规 → 回到 active（恢复计时清零，产生 retrigger 事件：
      确认已解除后重新提醒；频率上界=恢复尝试次数，被滞回+hold 钳制）
    - active 且本帧不违规但仍处滞回带内 → 保持 active（消灭临界抖动）
    - 越出滞回带 → clearing；恢复条件连续保持达 ``clear_hold_sec`` →
      normal（产生 cleared 事件）。clearing 对外语义仍算报警中
      （消费方用 :func:`alarm_phase` 判 ``in ("active","clearing")``）。

    无效通道（None）的通道级报警保持原状态、不产生事件；温差（帧级）在
    有效通道不足 2 个时视为恢复候选（同样受滞回保持时长约束，
    详见 design §4.4、§10 与整改计划 §4 P0-1）。

    Args:
        values, prev_map, timestamp, cfg: 同 :func:`evaluate_channel_alarms`。
        states: ``{key: "normal"|"active"|("clearing", ts)}`` 当前状态。

    Returns:
        ``(events, new_prev_map, new_states)``：
          - events: ``[(channel_idx|None, alarm_type, "active"|"retrigger"|"cleared", actual_value)]``
          - new_prev_map / new_states：更新后的副本（不原地修改入参）
    """
    if not cfg.enabled:
        return [], dict(prev_map), dict(states)
    violations, valid_set, new_prev = evaluate_channel_alarms(
        values, prev_map, timestamp, cfg)
    new_states = dict(states)
    events = []
    for key in set(new_states.keys()) | set(violations.keys()):
        ch_idx, atype = key
        # 通道级报警：本帧该通道无效则保持原状态，不产生事件
        if ch_idx is not None and ch_idx not in valid_set:
            continue
        state = new_states.get(key, ALARM_PHASE_NORMAL)
        phase = alarm_phase(state)
        if key in violations:
            if phase == ALARM_PHASE_NORMAL:
                new_states[key] = ALARM_PHASE_ACTIVE
                events.append((ch_idx, atype, "active", violations[key]))
            else:
                # 违规复现：恢复计时作废，锁回 active。clearing 期复现是一次
                # 真实「恢复失败」→ 发 retrigger（GUI 撤销确认后重新提醒，
                # 整改 Q2-2）；active 期复现不发事件，维持风暴抑制。
                new_states[key] = ALARM_PHASE_ACTIVE
                if phase == ALARM_PHASE_CLEARING:
                    events.append((ch_idx, atype, "retrigger",
                                   violations[key]))
            continue
        if phase == ALARM_PHASE_NORMAL:
            continue
        if not _recovery_satisfied(
                key, values, prev_map, timestamp, cfg, valid_set):
            # 滞回带内：恢复未确认；clearing 被打断则退回 active
            new_states[key] = ALARM_PHASE_ACTIVE
            continue
        hold = getattr(cfg, "clear_hold_sec", 0.0)
        since = alarm_clear_since(state)
        if hold <= 0.0:
            new_states[key] = ALARM_PHASE_NORMAL
            events.append((ch_idx, atype, "cleared", 0.0))
        elif since is None:
            new_states[key] = (ALARM_PHASE_CLEARING, timestamp)
        elif timestamp - since >= hold:
            new_states[key] = ALARM_PHASE_NORMAL
            events.append((ch_idx, atype, "cleared", 0.0))
    return events, new_prev, new_states


@dataclass
class RiseAnalysisResult:
    """单条通道的温升三阶段分析结果。"""

    status: str
    time_sec: np.ndarray
    raw_values: np.ndarray
    cleaned_values: np.ndarray
    smoothed_values: np.ndarray
    slope_per_min: np.ndarray
    phase_labels: np.ndarray
    start_temperature: float | None = None
    fast_to_slow_sec: float | None = None
    slow_to_steady_sec: float | None = None
    fast_metrics: dict | None = None
    slow_metrics: dict | None = None
    steady_metrics: dict | None = None
    removed_outlier_count: int = 0
    diagnostic: str = ""

    @property
    def steady_mean(self) -> Optional[float]:
        """稳态阶段平均温度（无稳态数据时为 None）"""
        return (self.steady_metrics or {}).get("mean")

    @property
    def steady_fluctuation(self) -> Optional[float]:
        """稳态阶段温度波动（95 分位 - 5 分位，无稳态数据时为 None）"""
        return (self.steady_metrics or {}).get("fluctuation")

    def get(self, key: str, default: object = None) -> object:
        """提供字典式查询，便于界面和导出层读取结果。"""
        mapping = {
            "status": self.status,
            "start_temperature": self.start_temperature,
            "fast_to_slow_sec": self.fast_to_slow_sec,
            "slow_to_steady_sec": self.slow_to_steady_sec,
            "fast_metrics": self.fast_metrics,
            "slow_metrics": self.slow_metrics,
            "steady_metrics": self.steady_metrics,
            "steady_mean": self.steady_mean,
            "steady_fluctuation": self.steady_fluctuation,
            "removed_outlier_count": self.removed_outlier_count,
            "diagnostic": self.diagnostic,
        }
        return mapping.get(key, default)


def _window_points(time_sec: np.ndarray, window_sec: float) -> int:
    """按当前数据的中位采样间隔换算一个奇数平滑窗口。"""
    if len(time_sec) < 2:
        return 1
    diffs = np.diff(time_sec)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if diffs.size == 0:
        return 1
    points = max(3, int(round(float(window_sec) / float(np.median(diffs)))))
    if points % 2 == 0:
        points += 1
    max_points = len(time_sec) if len(time_sec) % 2 else len(time_sec) - 1
    return min(points, max(1, max_points))


def _robust_outlier_mask(values: np.ndarray, window_points: int) -> np.ndarray:
    """按通道自身局部中位数和 MAD 识别尖峰。"""
    if len(values) < 3:
        return np.zeros(len(values), dtype=bool)
    series = pd.Series(values, dtype=float)
    half = max(1, window_points // 2)
    local_median = series.rolling(
        window_points, center=True, min_periods=max(3, half)).median()
    residual = (series - local_median).abs()
    local_mad = residual.rolling(
        window_points, center=True, min_periods=max(3, half)).median()
    fallback = float(np.nanmedian(residual.to_numpy(dtype=float)))
    if not np.isfinite(fallback) or fallback <= 0:
        fallback = max(float(np.nanstd(values)) * 0.1, np.finfo(float).eps)
    scale = local_mad.to_numpy(dtype=float).copy()
    scale[~np.isfinite(scale) | (scale <= 0)] = fallback
    med = local_median.to_numpy(dtype=float)
    return np.isfinite(med) & np.isfinite(values) & (residual.to_numpy() > 6.0 * scale)


def _prepare_rise_series(time_sec, values, config):
    """清洗、插值和平滑单通道序列。"""
    time = np.asarray(time_sec, dtype=float).reshape(-1)
    raw = np.asarray(values, dtype=float).reshape(-1)
    size = min(time.size, raw.size)
    time, raw = time[:size], raw[:size]
    valid_time = np.isfinite(time)
    if not valid_time.any():
        empty = np.array([], dtype=float)
        return empty, empty, empty, empty, np.array([], dtype=bool)
    time, raw = time[valid_time], raw[valid_time]
    order = np.argsort(time, kind="stable")
    time, raw = time[order], raw[order]
    unique = np.concatenate(([True], np.diff(time) > 0))
    time, raw = time[unique], raw[unique]
    finite = np.isfinite(raw)
    if not finite.any():
        empty = np.full(time.shape, np.nan, dtype=float)
        return time, raw, empty, empty, np.zeros(time.size, dtype=bool)
    fill = raw.copy()
    valid_index = np.arange(fill.size)
    if (~finite).any():
        fill[~finite] = np.interp(
            valid_index[~finite], valid_index[finite], fill[finite])
    window_points = _window_points(time, config.filter_window_sec)
    outlier = _robust_outlier_mask(fill, window_points)
    cleaned = fill.copy()
    if outlier.any():
        keep = ~outlier
        if keep.sum() >= 2:
            cleaned[outlier] = np.interp(
                valid_index[outlier], valid_index[keep], cleaned[keep])
    series = pd.Series(cleaned, dtype=float)
    median_values = series.rolling(
        window_points, center=True, min_periods=1).median()
    smoothed = median_values.rolling(
        window_points, center=True, min_periods=1).mean().to_numpy(dtype=float)
    return time, raw, cleaned, smoothed, outlier


def _local_slope_per_minute(time_sec: np.ndarray, values: np.ndarray,
                            window_points: int) -> np.ndarray:
    """用真实时间求平滑曲线的局部斜率，单位为 ℃/分钟。"""
    if len(values) < 2:
        return np.zeros(len(values), dtype=float)
    slope = np.gradient(values, time_sec) * 60.0
    return pd.Series(slope).rolling(
        window_points, center=True, min_periods=1).mean().to_numpy()


def _find_sustained_run(time_sec: np.ndarray, mask: np.ndarray,
                        min_duration_sec: float, start_index: int = 0):
    """寻找第一个连续覆盖指定时间的真值窗口。"""
    start = None
    for index in range(max(0, int(start_index)), len(time_sec)):
        if bool(mask[index]):
            if start is None:
                start = index
            if time_sec[index] - time_sec[start] >= min_duration_sec:
                return start, index
        else:
            start = None
    return None


def _phase_metrics(time_sec, smoothed_values, slope_per_min, start, end):
    """生成阶段的起止、时长和平均升温速率。"""
    if start is None or end is None or end < start:
        return None
    values = np.asarray(smoothed_values[start:end + 1], dtype=float)
    slopes = np.asarray(slope_per_min[start:end + 1], dtype=float)
    finite_values = values[np.isfinite(values)]
    finite_slopes = slopes[np.isfinite(slopes)]
    if finite_values.size == 0:
        return None
    return {
        "start_sec": float(time_sec[start]),
        "end_sec": float(time_sec[end]),
        "duration_sec": max(0.0, float(time_sec[end] - time_sec[start])),
        "average_rate_per_min": (
            float(np.nanmean(finite_slopes)) if finite_slopes.size else np.nan),
        "temperature_change": float(finite_values[-1] - finite_values[0]),
    }


def _empty_rise_result(time_sec, values, diagnostic):
    """创建可安全展示的空/无效分析结果。"""
    time = np.asarray(time_sec, dtype=float).reshape(-1)
    raw = np.asarray(values, dtype=float).reshape(-1)
    size = min(time.size, raw.size)
    time, raw = time[:size], raw[:size]
    nan_values = np.full(size, np.nan, dtype=float)
    return RiseAnalysisResult(
        status="insufficient_data", time_sec=time, raw_values=raw,
        cleaned_values=nan_values.copy(), smoothed_values=nan_values.copy(),
        slope_per_min=nan_values.copy(),
        phase_labels=np.full(size, "unknown", dtype="U7"),
        diagnostic=diagnostic,
    )


def analyze_rise(time_sec: np.ndarray, values: np.ndarray,
                 config: RiseAnalysisConfig | None = None) -> RiseAnalysisResult:
    """独立分析一条通道的快速、缓慢、稳态三阶段。"""
    config = (config or RiseAnalysisConfig()).normalized()
    time, raw, cleaned, smoothed, outlier = _prepare_rise_series(
        time_sec, values, config)
    if time.size < 3 or not np.isfinite(smoothed).any():
        return _empty_rise_result(time_sec, values, "有效数据不足，无法识别温升阶段")

    window_points = _window_points(time, config.filter_window_sec)
    slope = _local_slope_per_minute(time, smoothed, window_points)
    finite_slope = slope[np.isfinite(slope)]
    positive_slope = finite_slope[finite_slope > 0]
    if positive_slope.size < 3:
        return RiseAnalysisResult(
            status="heating", time_sec=time, raw_values=raw,
            cleaned_values=cleaned, smoothed_values=smoothed,
            slope_per_min=slope,
            phase_labels=np.full(time.size, PHASE_FAST, dtype="U7"),
            start_temperature=float(smoothed[0]),
            removed_outlier_count=int(outlier.sum()),
            diagnostic="有效上升斜率不足，暂无法识别完整三阶段",
        )

    low_slope = float(np.nanpercentile(positive_slope, 20))
    high_slope = float(np.nanpercentile(positive_slope, 80))
    if high_slope <= low_slope:
        high_slope = low_slope + max(abs(low_slope), np.finfo(float).eps)
    sensitivity = config.slope_decay_sensitivity
    transition_level = low_slope + (high_slope - low_slope) * sensitivity
    # 稳态不是“接近低分位”就算成立：缓慢升温段本身也可能长期保持
    # 一个稳定的正斜率。这里要求斜率继续衰减到高位参考的相对低区间，
    # 这样仍然只依赖当前通道的斜率比例，不引入绝对温度/斜率门槛。
    steady_level = high_slope * (0.10 + 0.10 * (1.0 - sensitivity))
    steady_level = max(steady_level, np.finfo(float).eps)

    transition_mask = np.isfinite(slope) & (slope <= transition_level)
    confirm_sec = max(config.filter_window_sec * 2.0, 1.0)
    fast_run = _find_sustained_run(time, transition_mask, confirm_sec, 1)
    fast_to_slow_index = fast_run[0] if fast_run else None
    if fast_to_slow_index is None:
        return RiseAnalysisResult(
            status="heating", time_sec=time, raw_values=raw,
            cleaned_values=cleaned, smoothed_values=smoothed,
            slope_per_min=slope,
            phase_labels=np.full(time.size, PHASE_FAST, dtype="U7"),
            start_temperature=float(smoothed[0]),
            removed_outlier_count=int(outlier.sum()),
            diagnostic="尚未确认快速升温向缓慢升温转折",
        )

    steady_mask = np.isfinite(slope) & (np.abs(slope) <= steady_level)
    steady_run = _find_sustained_run(
        time, steady_mask, config.steady_duration_sec,
        fast_to_slow_index + 1)
    slow_to_steady_index = steady_run[0] if steady_run else None

    labels = np.full(time.size, PHASE_FAST, dtype="U7")
    labels[fast_to_slow_index:] = PHASE_SLOW
    if slow_to_steady_index is not None:
        labels[slow_to_steady_index:] = PHASE_STEADY

    fast_end = max(fast_to_slow_index, 0)
    slow_end = (slow_to_steady_index - 1
                if slow_to_steady_index is not None else time.size - 1)
    fast_metrics = _phase_metrics(time, smoothed, slope, 0, fast_end)
    slow_metrics = _phase_metrics(
        time, smoothed, slope, fast_to_slow_index, max(fast_to_slow_index, slow_end))
    steady_metrics = None
    if slow_to_steady_index is not None:
        steady_values = smoothed[slow_to_steady_index:]
        steady_values = steady_values[np.isfinite(steady_values)]
        steady_metrics = _phase_metrics(
            time, smoothed, slope, slow_to_steady_index, time.size - 1)
        if steady_metrics is not None and steady_values.size:
            steady_metrics["mean"] = float(np.nanmean(steady_values))
            steady_metrics["fluctuation"] = float(
                np.nanpercentile(steady_values, 95)
                - np.nanpercentile(steady_values, 5))

    return RiseAnalysisResult(
        status=("steady_confirmed" if slow_to_steady_index is not None
                else "steady_unconfirmed"),
        time_sec=time, raw_values=raw, cleaned_values=cleaned,
        smoothed_values=smoothed, slope_per_min=slope,
        phase_labels=labels, start_temperature=float(smoothed[0]),
        fast_to_slow_sec=float(time[fast_to_slow_index]),
        slow_to_steady_sec=(
            float(time[slow_to_steady_index])
            if slow_to_steady_index is not None else None),
        fast_metrics=fast_metrics, slow_metrics=slow_metrics,
        steady_metrics=steady_metrics,
        removed_outlier_count=int(outlier.sum()),
        diagnostic=("" if slow_to_steady_index is not None
                    else "稳态尚未满足持续时间要求"),
    )
