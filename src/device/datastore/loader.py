# -*- coding: utf-8 -*-
"""
loader —— 文件 → Session。

策略说明
--------
本模块**不重写解析逻辑**：.tpx 二进制解析与 .xls/.csv 文本解析
已在 core.py 中经多组样本与 .xls↔.tpx 交叉验证（8374 格 100% 一致），
重写风险远大于收益。这里只做 `core.Dataset → Session` 的适配。

待 P8 阶段把纯算法搬进 algo/ 时，再把解析函数原样迁移过来，
届时本文件的对外签名不变，上层无感知。

关键点：文本文件用表头中的物理通道号生成 Session 稳定键；TPX
二进制的数据区按启用顺序存储，样本中可能把第 8 个组内通道写成
`CH64`，因此 TPX 必须使用解析器生成的组内顺序名 `CH1..CH8`。
"""
from __future__ import annotations

import os

import numpy as np

from .channel import Channel, normalize_key
from .buffer import buffer_from_arrays
from .session import Session, SOURCE_FILE
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注用，避免运行时循环导入
    from .channel_config import ChannelConfig

from utils import core


FILE_FILTER = ("数据文件 (*.xlsx *.xls *.csv *.txt *.tpx);;"
               "工程数据文件 (*.tpx);;"
               "表格/文本 (*.xlsx *.xls *.csv *.txt);;"
               "所有文件 (*.*)")


def dataset_to_session(ds: "core.Dataset", path: str = "",
                       config: Optional["ChannelConfig"] = None,
                       source: str = SOURCE_FILE) -> Session:
    """把旧的 core.Dataset 适配成 Session。"""
    columns, keys = [], []
    is_tpx = os.path.splitext(path or getattr(ds, "source_path", ""))[1].lower() == ".tpx"
    for i, c in enumerate(ds.channels):
        columns.append(np.asarray(c.raw, dtype=float))
        if is_tpx:
            # TPX 数据区是启用通道的组内顺序；CH64 可能只是第 8 列的
            # 原厂槽位名，不能直接变成界面上的第 64 通道。
            keys.append(getattr(c, "orig_name", "") or f"CH{i+1}")
        else:
            # 文本表头通常保存物理通道号，优先用于跨文件匹配配置。
            keys.append(getattr(c, "physical_name", "") or getattr(c, "orig_name", "") or f"CH{i+1}")

    chans: list[Channel] = []
    for i, raw_key in enumerate(keys):
        chans.append(Channel(
            key=normalize_key(raw_key, i),
            index=i,
            source_label=str(raw_key),
        ))

    buf = buffer_from_arrays(np.asarray(ds.time_sec, dtype=float), columns)
    s = Session(chans, buf, source=source,
                path=path or getattr(ds, "source_path", ""),
                interval=getattr(ds, "orig_interval", 1.0))
    if config is not None:
        config.apply_all(s.channels)
    # 开路通道默认不勾选（沿用旧行为）
    for c in s.channels:
        if s.is_open_circuit(c):
            c.visible = False
            c.enabled = False
    return s


def load_session(path: str, config: Optional["ChannelConfig"] = None) -> Session:
    """主入口：加载任意支持的文件为 Session。

    Raises:
        ValueError / OSError: 解析失败时向上抛，由 UI 层提示
    """
    ds = core.load_file(path)
    return dataset_to_session(ds, path=path, config=config)
