# -*- coding: utf-8 -*-
"""
ConfigWatcher —— settings.json 运行时热重载。

基于 QFileSystemWatcher 监听统一配置文件的改动（含应用自身的原子写入），
通过内容哈希去重：若新内容与「本进程最后一次写入快照」一致，则视为自身写入，
不触发热更新，避免应用循环刷新。

对外信号：
- section_changed(set[str])  —— 发生变化的一级配置段名。
- names_file_changed()       —— 通道名称独立文件被外部修改（去抖 + 哈希判重）。

无 Qt 依赖的用法：仅需 PyQt5.QtCore，可安全被 GUI 主线程持有。
"""
import hashlib
import json
import os

from PyQt5.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from . import channel_names_file
from .config_io import ConfigIO, SETTINGS_FILE


class ConfigWatcher(QObject):
    """监听配置文件的运行时变化，去抖后发出 section_changed 信号。"""

    section_changed = pyqtSignal(set)
    names_file_changed = pyqtSignal()

    DEBOUNCE_MS = 400   # 文件系统事件聚合窗口

    def __init__(self, parent=None):
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._flush)
        self._pending = set()
        self._loaded = {}
        self._paths = []

    # ------------------------------------------------------------------
    #  对外接口
    # ------------------------------------------------------------------
    def start(self, paths=None):
        """建立监听基线：只记录当前内容，不触发信号。

        应在应用完成启动配置加载后再调用，避免重复应用一遍已有配置。
        """
        if paths is None:
            paths = (SETTINGS_FILE, channel_names_file.NAMES_FILE)
        self._paths = [p for p in paths if os.path.exists(p)]
        if self._paths:
            self._watcher.addPaths(self._paths)
        self._loaded = self._read_settings() or {}

    def stop(self):
        """停止监听并清理定时器（应用退出时调用）。"""
        self._watcher.removePaths(self._paths)
        self._debounce.stop()
        self._paths = []

    # ------------------------------------------------------------------
    #  内部
    # ------------------------------------------------------------------
    def _on_file_changed(self, path):
        # Windows 上 os.replace 原子替换会替换文件句柄，QFileSystemWatcher
        # 可能因此丢失监听；事件触发后重新添加路径以恢复跟踪。
        if path not in self._watcher.files():
            self._watcher.addPath(path)
        self._pending.add(path)
        self._debounce.start(self.DEBOUNCE_MS)

    def _flush(self):
        if not self._pending:
            return
        paths = self._pending
        self._pending = set()

        changed = set()
        if SETTINGS_FILE in paths:
            new = self._read_settings()
            if new is None:
                return
            # 内容哈希与本进程最近一次写入一致 → 判定为自身写入，不触发热更新
            if not self._is_self_write():
                for k, v in new.items():
                    if self._loaded.get(k) != v:
                        changed.add(k)
                for k in set(self._loaded) - set(new):
                    changed.add(k)
            self._loaded = new
        # 通道名称独立文件：内容哈希与最近一次自身写入不一致 → 外部修改
        names_path = channel_names_file.NAMES_FILE
        if names_path in paths:
            if channel_names_file.file_hash(names_path) != \
                    channel_names_file.written_hash():
                self.names_file_changed.emit()
        if changed:
            self.section_changed.emit(changed)

    @staticmethod
    def _is_self_write():
        digest = ConfigWatcher._file_hash()
        return digest is not None and digest == ConfigIO.written_hash()

    @staticmethod
    def _file_hash():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return hashlib.sha1(
                    f.read().encode("utf-8", "replace")).hexdigest()
        except Exception:
            return None

    def _read_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception:
            return None
