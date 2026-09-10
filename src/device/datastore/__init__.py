# -*- coding: utf-8 -*-
"""
datastore —— 统一数据层。

对外只暴露这几样东西，上层不要越过它直接碰内部模块：

    from device.datastore import store, Session, Channel, Pipeline

    store.init_config(CONFIG_DIR)          # 启动时一次
    store.open_file(path)                  # 菜单栏导入
    store.create_live_session(64, 1.0)     # 在线采集
    store.append_live(t, values)           # 采集线程回调（Qt 队列连接）
    store.active                           # 当前展示的会话
    store.config.set_name("CH1", "准直保护")  # 全局共享的通道命名

分层约束（严禁反向依赖）
------------------------
    UI (main_window / widgets / dialogs)
             ↓  只读 store + 订阅信号
    datastore.store          ← 唯一依赖 Qt 的模块
             ↓
    session / buffer / channel / channel_config / recorder / pipeline
             ↓                    （以上均无 Qt 依赖，可单测）
    core (算法与解析)
"""

from .channel import Channel, DEFAULT_PALETTE, palette_color, normalize_key
from .buffer import TimeSeriesBuffer, buffer_from_arrays
from .session import Session, SOURCE_FILE, SOURCE_LIVE
from .channel_config import ChannelConfig, load_or_migrate, thermal_color
from .recorder import Recorder
from .tpx_writer import write_tpx
from .pipeline import Pipeline, DEFAULT_PARAMS
from . import loader
from .store import DataStore, store

__all__ = [
    "store", "DataStore",
    "Session", "SOURCE_FILE", "SOURCE_LIVE",
    "Channel", "DEFAULT_PALETTE", "palette_color", "normalize_key",
    "TimeSeriesBuffer", "buffer_from_arrays",
    "ChannelConfig", "load_or_migrate", "thermal_color",
    "Recorder", "write_tpx",
    "Pipeline", "DEFAULT_PARAMS",
    "loader",
]
