# -*- coding: utf-8 -*-
"""
channel_names_file —— 通道自定义名称的专用独立配置文件。

文件：用户数据/config/通道名称-channel-names.json（与 settings.json 同目录）。

设计目标（2026-09-24 通道名称独立文件批次）：
  - 用户可用任意文本编辑器增删改名称，保存后软件即时生效（ConfigWatcher 监听）；
  - 可从其他电脑整份复制替换该文件，立即生效；
  - 升级换新版本文件夹后，把这份文件复制进新包 用户数据/config/ 即恢复名称；
  - 首次运行由当前配置自动生成默认文件。

文件格式（UTF-8 JSON，键中文为主、兼容英文别名，"_"开头键忽略）：
  {
    "_说明": "...",
    "候选名称": ["准直保护", ...],
    "通道名称": { "CH1": "准直保护", "CH6": "腔体气温" }
  }

权威性约定：本文件是通道「名称」的唯一权威来源；settings.json 的
channels.name / name_list 段退为兼容镜像（应用内改名仍同步写回，
保证回退旧版本时名称不丢）。颜色 / 显隐 / 预设仍只在 settings.json。

防回环：本进程写入后登记内容哈希（与 ConfigIO._WRITTEN_HASH 同款机制，
见 PITFALLS §9.3），ConfigWatcher 用它区分「自身写入」与「外部修改」。

无 Qt 依赖，可被 GUI 主线程与测试直接调用。
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from .helpers import _resolve_config_dir

# 文件名（中文在前便于用户识别；目录归属 用户数据/config）
NAMES_FILENAME = "通道名称-channel-names.json"
# 模块级固化路径（与 config_io.SETTINGS_FILE 同款模式；conftest 会重定向）
NAMES_FILE = os.path.join(_resolve_config_dir(), NAMES_FILENAME)

# 键别名：中文为主，英文兼容（方便从 settings.json 段直接拷贝内容）
_POOL_KEYS = ("候选名称", "name_pool")
_MAPPING_KEYS = ("通道名称", "channels")

# 通道键合法形式：CH1 ~ CH64（大小写与内部空白宽容，如 "ch 3" → CH3）
_CH_KEY_RE = re.compile(r"^CH\s*(\d{1,3})$", re.IGNORECASE)
_MAX_CHANNEL = 64

_DOC_TEXT = (
    "通道自定义名称文件：外部编辑保存后软件即时生效；可整份复制到其他电脑替换使用。\n"
    "候选名称：命名弹窗下拉候选池，字符串数组。\n"
    "通道名称：逐通道指定（键为物理通道号 CH1~CH64）；删除某键或值留空 = 该通道恢复默认显示 CHn。\n"
    "以 \"_\" 开头的键会被忽略，可自行保留备注；请以 UTF-8 编码保存。"
)

# 本进程最近一次写入本文件的内容哈希（防自身写回触发热重载循环）
_WRITTEN_HASH = None


class NamesFileError(Exception):
    """名称文件读取/解析失败（message 面向用户，可直接进状态栏）。"""


def _ch_order(key: str):
    """按物理通道号排序（非法键排最后，保持稳定）。"""
    m = _CH_KEY_RE.match(key or "")
    return (int(m.group(1)) if m else 9999, key or "")


def _serialize(pool, mapping) -> str:
    payload = {
        "_说明": _DOC_TEXT,
        "候选名称": list(pool),
        "通道名称": {k: mapping[k] for k in sorted(mapping, key=_ch_order)},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def read_names_file(path: str = None):
    """读取并校验名称文件，返回 (候选名池, 通道名称映射)。

    - 池：非空字符串 strip 后去重（保序）；非字符串项跳过；
    - 映射：键归一为 CHn（1~64，非法键忽略），值 strip；空串 = 显式恢复默认；
      值兼容 {"name": "..."} 结构（从 settings.json channels 段拷来的形态）；
    - 文件缺失 / JSON 损坏 / 结构类型不对 → 抛 NamesFileError（不静默清空）。
    """
    p = path or NAMES_FILE
    if not os.path.exists(p):
        raise NamesFileError(f"文件不存在：{p}")
    try:
        # utf-8-sig：容忍记事本另存 UTF-8 时写入的 BOM
        with open(p, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise NamesFileError(
            f"JSON 解析失败（第 {exc.lineno} 行 第 {exc.colno} 列）：{exc.msg}")
    except OSError as exc:
        raise NamesFileError(f"无法读取文件：{exc}")
    if not isinstance(data, dict):
        raise NamesFileError("文件顶层必须是 { } 对象（当前不是）")

    pool_raw = None
    for k in _POOL_KEYS:
        if k in data:
            pool_raw = data[k]
            break
    pool = []
    if pool_raw is not None:
        if not isinstance(pool_raw, list):
            raise NamesFileError("「候选名称」必须是字符串数组 [ \"...\", ... ]")
        seen = set()
        for item in pool_raw:
            if not isinstance(item, str):
                continue
            nm = item.strip()
            if nm and nm not in seen:
                seen.add(nm)
                pool.append(nm)

    map_raw = None
    for k in _MAPPING_KEYS:
        if k in data:
            map_raw = data[k]
            break
    mapping = {}
    if map_raw is not None:
        if not isinstance(map_raw, dict):
            raise NamesFileError("「通道名称」必须是对象 { \"CH1\": \"名称\", ... }")
        for key, val in map_raw.items():
            m = _CH_KEY_RE.match(str(key).strip())
            if not m:
                continue  # 非法键忽略（含 "_说明" 等下划线键）
            n = int(m.group(1))
            if not 1 <= n <= _MAX_CHANNEL:
                continue
            if isinstance(val, dict):
                val = val.get("name", "")
            if not isinstance(val, str):
                continue
            mapping[f"CH{n}"] = val.strip()
    return pool, mapping


def write_names_file(pool, mapping, path: str = None) -> bool:
    """把名称状态原子写入文件；内容与现文件一致时跳过写盘（返回 False）。"""
    p = path or NAMES_FILE
    text = _serialize(pool, mapping)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                if f.read() == text:
                    return False
        except OSError:
            pass
    tmp = p + ".tmp"
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p)
    global _WRITTEN_HASH
    _WRITTEN_HASH = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return True


def update_mapping_in_file(mapping, path: str = None) -> bool:
    """只替换文件中的通道名称映射，候选池保持文件现状。

    语义门控：文件现有映射与目标一致时直接跳过（不重排用户排版）。
    供 channels_changed 通知链使用——映射来源于全局共享的 store.config，
    任何持有者写入的值都一致，不会互相踩踏。
    """
    p = path or NAMES_FILE
    try:
        pool, f_map = read_names_file(p)
        if f_map == mapping:
            return False
    except NamesFileError:
        pool = []
    return write_names_file(pool, mapping, p)


def update_pool_in_file(pool, path: str = None) -> bool:
    """只替换文件中的候选名池，通道名称映射保持文件现状。

    语义门控同上。仅供名称池的显式编辑入口（_save_name_list）调用，
    不挂在信号通知链上，避免后台刷新用过期池快照覆盖池内容。
    """
    p = path or NAMES_FILE
    try:
        f_pool, mapping = read_names_file(p)
        if f_pool == pool:
            return False
    except NamesFileError:
        mapping = {}
    return write_names_file(pool, mapping, p)


def file_hash(path: str = None):
    """当前文件内容哈希（读不到返回 None）。"""
    try:
        with open(path or NAMES_FILE, "r", encoding="utf-8-sig") as f:
            return hashlib.sha1(f.read().encode("utf-8", "replace")).hexdigest()
    except Exception:
        return None


def written_hash():
    """本进程最近一次写入的内容哈希（未写过返回 None）。"""
    return _WRITTEN_HASH
