# -*- coding: utf-8 -*-
"""
ChannelConfig —— 全局唯一的通道配置中心。

它是"文件导入"与"在线采集"共享同一套通道名称 / 颜色 / 显隐的唯一来源。
不管数据是从菜单栏打开的 .tpx，还是串口正在采的实时流，
只要物理通道号相同（CH1、CH7…），拿到的名字和颜色就完全一致。

持久化文件：config/channels.json
------------------------------
{
  "version": 2,
  "color_mode": "categorical" | "thermal",
  "palette": ["#E74C3C", ...],
  "name_pool": ["准直保护", "准直镜片", ...],
  "active_preset": "1",
  "presets": { "1": {"CH1": "QBH", ...}, "2": {...} },
  "channels": {
      "CH1": {"name": "QBH", "color": "#E74C3C", "visible": true}
  }
}

- `channels` 是权威的逐通道配置。
- `presets` 是可一键套用的整机方案（承接旧 channel_presets.json）。
- `name_pool` 是下拉框候选名（承接旧 name_list.json）。

无 Qt 依赖：变更通知用轻量回调，由 DataStore 转成 Qt 信号。
"""
from __future__ import annotations

import os
import json
from typing import Callable

from utils.config_io import SETTINGS_FILE

from .channel import Channel, DEFAULT_PALETTE, palette_color

CONFIG_VERSION = 2

# 热力模式渐变锚点：低温蓝 → 高温红
_THERMAL_STOPS = ["#3498DB", "#1ABC9C", "#2ECC71", "#F1C40F", "#E67E22", "#E74C3C"]


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{int(r):02X}{int(g):02X}{int(b):02X}"


def thermal_color(index: int, total: int) -> str:
    """在热力渐变上按通道序号取色（不依赖 matplotlib）。"""
    if total <= 1:
        return _THERMAL_STOPS[0]
    pos = (index / (total - 1)) * (len(_THERMAL_STOPS) - 1)
    i = int(pos)
    if i >= len(_THERMAL_STOPS) - 1:
        return _THERMAL_STOPS[-1]
    frac = pos - i
    c0 = _hex_to_rgb(_THERMAL_STOPS[i])
    c1 = _hex_to_rgb(_THERMAL_STOPS[i + 1])
    return _rgb_to_hex(*[c0[k] + (c1[k] - c0[k]) * frac for k in range(3)])


class ChannelConfig:
    """通道名称 / 颜色 / 显隐的全局配置。"""

    def __init__(self, path: str):
        self.path = path
        self.color_mode: str = "categorical"
        self.palette: list[str] = list(DEFAULT_PALETTE)
        self.name_pool: list[str] = []
        self.presets: dict[str, dict[str, str]] = {}
        self.active_preset: str = ""
        # key -> {"name","color","visible"}
        self.channels: dict[str, dict] = {}
        self._listeners: list = []
        self._muted = False

    # ==================================================================
    #  变更通知
    # ==================================================================
    def subscribe(self, callback: Callable) -> None:
        """注册变更回调 callback(keys: list[str] | None)。None 表示整体变化。"""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def _notify(self, keys=None):
        if self._muted:
            return
        for cb in list(self._listeners):
            try:
                cb(keys)
            except Exception:
                pass

    class _Batch:
        """with cfg.batch(): 期间合并通知，退出时统一发一次。
        notify=False 时退出也不通知（用于显隐切换等只做局部刷新的场景，
        避免触发 channels_changed → 整面板重建）。"""

        def __init__(self, cfg, notify: bool = True):
            self.cfg = cfg
            self.notify = notify

        def __enter__(self):
            self.cfg._muted = True
            return self.cfg

        def __exit__(self, *exc):
            self.cfg._muted = False
            if self.notify:
                self.cfg._notify(None)
            return False

    def batch(self, notify: bool = True) -> ChannelConfig._Batch:
        """进入批量变更上下文：期间合并通知，退出时统一广播一次"""
        return ChannelConfig._Batch(self, notify=notify)

    # ==================================================================
    #  查询 / 解析
    # ==================================================================
    def resolve(self, key: str, index: int, total: int = 8) -> dict:
        """取得某通道的最终配置。没有存过就按规则生成默认值。

        命名规则（与「通道命名」流程一致）：
          - 用户未自定义 → 默认显示原始通道名（CH1 / CH2 …）；
          - 用户从下拉选用 / 套用预设后 → 显示「CHn·自定义名」。
        预设名只在主动 apply_preset() 时写入 channels，这里不再自动套用。

        Args:
            key:   稳定键，如 "CH1"
            index: 在当前会话中的列序号，用于分配默认颜色
            total: 会话总通道数，热力模式按此归一化
        """
        rec = self.channels.get(key, {})
        name = (rec.get("name") or "").strip()
        if not name:
            name = key   # 未自定义 → 原始通道名
        color = rec.get("color") or ""
        if not color:
            color = (thermal_color(index, total) if self.color_mode == "thermal"
                     else palette_color(index, self.palette))
        visible = rec.get("visible")
        return {
            "name": name,
            "color": color,
            "visible": True if visible is None else bool(visible),
        }

    def apply_to(self, ch: Channel, total: int = 8) -> None:
        """把配置下发到通道对象上。"""
        r = self.resolve(ch.key, ch.index, total)
        ch.name = r["name"]
        ch.color = r["color"]
        ch.visible = r["visible"]

    def apply_all(self, channels: list[Channel]) -> None:
        """批量下发。这是保证"任何来源的数据都长一样"的关键调用。"""
        total = max(len(channels), 1)
        for ch in channels:
            self.apply_to(ch, total)

    # ==================================================================
    #  修改
    # ==================================================================
    def set_name(self, key: str, name: str, save: bool = True) -> None:
        """设置通道显示名并持久化（新名字会加入候选名池）"""
        self.channels.setdefault(key, {})["name"] = name
        if name and name not in self.name_pool:
            self.name_pool.append(name)
        if save:
            self.save()
        self._notify([key])

    def set_color(self, key: str, color: str, save: bool = True):
        self.channels.setdefault(key, {})["color"] = color
        if save:
            self.save()
        self._notify([key])

    def set_visible(self, key: str, visible: bool, save: bool = True,
                    notify: bool = True):
        """设置通道显隐。notify=False 时只持久化、不广播
        channels_changed（用于开关切换的局部刷新，避免全量重建面板）。"""
        self.channels.setdefault(key, {})["visible"] = bool(visible)
        if save:
            self.save()
        if notify:
            self._notify([key])

    def set_color_mode(self, mode: str, reset_colors: bool = True) -> None:
        """切换配色模式。reset_colors=True 会清掉逐通道的颜色覆盖，
        让所有通道重新按新模式取色。"""
        self.color_mode = mode
        if reset_colors:
            for rec in self.channels.values():
                rec.pop("color", None)
        self.save()
        self._notify(None)

    def apply_preset(self, preset_key: str) -> bool:
        """套用整机预设方案（会写入逐通道名称）。"""
        preset = self.presets.get(preset_key)
        if not preset:
            return False
        self.active_preset = preset_key
        with self.batch():
            for k, nm in preset.items():
                self.channels.setdefault(k, {})["name"] = nm
                if nm and nm not in self.name_pool:
                    self.name_pool.append(nm)
        self.save()
        return True

    # ==================================================================
    #  持久化
    # ==================================================================
    def load(self) -> bool:
        """从磁盘加载通道配置；成功返回 True，文件缺失或损坏返回 False"""
        if not os.path.exists(self.path):
            return False
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if os.path.basename(self.path) == "settings.json":
                d = d.get("channels", {}) if isinstance(d, dict) else {}
        except Exception:
            return False
        self.color_mode = d.get("color_mode", "categorical")
        pal = d.get("palette")
        if isinstance(pal, list) and pal:
            self.palette = pal
        self.name_pool = list(d.get("name_pool") or [])
        self.presets = dict(d.get("presets") or {})
        self.active_preset = d.get("active_preset", "")
        chans = d.get("channels") or {}
        self.channels = {k: dict(v) for k, v in chans.items() if isinstance(v, dict)}
        return True

    def save(self) -> bool:
        """把当前配置原子写入磁盘；成功返回 True"""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            payload = {
                "version": CONFIG_VERSION,
                "color_mode": self.color_mode,
                "palette": self.palette,
                "name_pool": self.name_pool,
                "active_preset": self.active_preset,
                "presets": self.presets,
                "channels": self.channels,
            }
            tmp = self.path + ".tmp"
            if os.path.basename(self.path) == "settings.json":
                root = {}
                if os.path.exists(self.path):
                    try:
                        with open(self.path, "r", encoding="utf-8") as f:
                            loaded = json.load(f)
                        if isinstance(loaded, dict):
                            root = loaded
                    except Exception:
                        pass
                root["channels"] = payload
                payload = root
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            return True
        except Exception:
            return False

    # ==================================================================
    #  旧配置迁移
    # ==================================================================
    def migrate_legacy(self, config_dir: str) -> list[str]:
        """把 name_list.json / color_config.json / channel_presets.json
        合并进新的 channels.json。只在新文件不存在时执行。

        返回迁移了哪些来源（供日志展示）。旧文件保留不删。
        """
        migrated = []

        legacy_channels = os.path.join(config_dir, "channels.json")
        if os.path.exists(legacy_channels):
            try:
                with open(legacy_channels, "r", encoding="utf-8") as f:
                    old = json.load(f)
                if isinstance(old, dict):
                    self.color_mode = old.get("color_mode", self.color_mode)
                    self.palette = old.get("palette", self.palette) or self.palette
                    self.name_pool = list(old.get("name_pool") or self.name_pool)
                    self.active_preset = old.get("active_preset", self.active_preset)
                    self.presets = dict(old.get("presets") or self.presets)
                    self.channels = {k: dict(v) for k, v in (old.get("channels") or {}).items()
                                     if isinstance(v, dict)}
                    migrated.append("channels.json")
            except Exception:
                pass

        def _read(fn):
            p = os.path.join(config_dir, fn)
            if not os.path.exists(p):
                return None
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None

        lst = _read("name_list.json")
        if isinstance(lst, list) and lst:
            for nm in lst:
                if nm not in self.name_pool:
                    self.name_pool.append(nm)
            migrated.append("name_list.json")

        cc = _read("color_config.json")
        if isinstance(cc, dict) and cc.get("mode"):
            self.color_mode = cc["mode"]
            migrated.append("color_config.json")

        cp = _read("channel_presets.json")
        if isinstance(cp, dict) and cp:
            for pk, mapping in cp.items():
                if isinstance(mapping, dict):
                    self.presets[str(pk)] = {str(k): str(v) for k, v in mapping.items()}
            migrated.append("channel_presets.json")
            # 首个预设作为默认命名来源，但不写死到 channels（保持可切换）
            if not self.active_preset:
                self.active_preset = sorted(self.presets.keys())[0]

        if migrated:
            self.save()
        return migrated


def load_or_migrate(config_dir: str, unified: bool = False) -> ChannelConfig:
    """标准入口；应用运行时将通道配置写入统一 settings.json。"""
    cfg = ChannelConfig(SETTINGS_FILE if unified else os.path.join(config_dir, "channels.json"))
    legacy_exists = os.path.exists(os.path.join(config_dir, "channels.json"))
    if not cfg.load() or (unified and legacy_exists and not cfg.channels):
        cfg.migrate_legacy(config_dir)
    return cfg
