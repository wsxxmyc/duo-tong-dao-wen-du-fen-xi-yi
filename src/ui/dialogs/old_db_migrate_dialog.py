# -*- coding: utf-8 -*-
"""旧版本数据库迁移选择对话框。

列出扫描到的旧版本包数据库候选（位置 / 会话数 / 时间范围 / 大小），
用户勾选后一键合并进当前数据库。合并执行由调用方负责（后台线程复用
HistoryDatabase.import_external_sessions：按会话 ID 去重、不覆盖现有
数据、旧库原样保留）。
"""
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QHeaderView,
                             QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout)


def _fmt_ts(ts) -> str:
    """时间戳 → 「YYYY-MM-DD HH:MM」；空值显示占位符。"""
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _fmt_size(size_bytes) -> str:
    """文件大小 → KB/MB/GB 自适应文本。"""
    if not size_bytes:
        return "—"
    mb = size_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{max(size_bytes / 1024, 0.1):.0f} KB"


class OldDbMigrateDialog(QDialog):
    """旧版本数据库候选勾选框：默认全选，确认后返回选中的候选列表。"""

    def __init__(self, candidates, parent=None, title="发现旧版本数据库"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(720, 420)
        self.resize(820, 480)
        self._candidates = list(candidates)

        layout = QVBoxLayout(self)
        tip = QLabel(
            f"在本机找到 {len(self._candidates)} 个旧版本软件包的历史数据库：\n"
            "勾选需要迁移的库，将按会话合并进当前数据库"
            "（已存在的会话自动跳过，旧库原样保留、不会被删除）。")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["数据库位置", "会话数", "时间范围", "大小"])
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        for c in self._candidates:
            item = QTreeWidgetItem([
                c.get("db_path", ""),
                str(c.get("sessions", 0)),
                f"{_fmt_ts(c.get('first_ts'))} ~ {_fmt_ts(c.get('last_ts'))}",
                _fmt_size(c.get("size_bytes")),
            ])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked)
            item.setToolTip(0, c.get("db_path", ""))
            self._tree.addTopLevelItem(item)
        self._tree.itemChanged.connect(self._refresh_migrate_text)
        layout.addWidget(self._tree, 1)

        self._chk_never = QCheckBox("未勾选的库以后不再提示")
        layout.addWidget(self._chk_never)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_migrate = QPushButton(self._migrate_text())
        self._btn_migrate.clicked.connect(self.accept)
        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_migrate)
        btn_row.addWidget(self._btn_cancel)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    def _checked_count(self) -> int:
        count = 0
        for i in range(self._tree.topLevelItemCount()):
            if self._tree.topLevelItem(i).checkState(0) == Qt.Checked:
                count += 1
        return count

    def _migrate_text(self) -> str:
        return f"迁移选中（{self._checked_count()} 个）"

    def _refresh_migrate_text(self, *_args):
        self._btn_migrate.setText(self._migrate_text())

    def selected_candidates(self):
        """返回勾选中的候选列表（保持传入顺序）。"""
        out = []
        for i, c in enumerate(self._candidates):
            item = self._tree.topLevelItem(i)
            if item is not None and item.checkState(0) == Qt.Checked:
                out.append(c)
        return out

    @property
    def never_ask_unselected(self) -> bool:
        """未勾选的库是否记为「不再提示」（否则记为本次跳过、下次再问）。"""
        return self._chk_never.isChecked()
