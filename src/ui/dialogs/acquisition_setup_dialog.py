# -*- coding: utf-8 -*-
"""
AcquisitionSetupDialog — 开始采集设置对话框

点击「开始采集」后弹出，包含两区块：
- 会话命名：按时间戳自动命名 / 自定义会话名称（自由文本，二选一）
- 通道命名：使用默认名称（CH1/CH2…）/ 使用上一次的名称（二选一）

两个区块经 QButtonGroup 显式分组、互不干扰（不分组时 Qt
autoExclusive 会让同父的全部 QRadioButton 全局互斥，出现跨区块
只能单选一个的交互问题）。

确定后通过 session_name() / reset_channel_names() 读取结果；
自定义名称为空时禁止确定。
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QLineEdit,
    QPushButton, QFrame, QButtonGroup,
)

from ui.theme import Theme


class AcquisitionSetupDialog(QDialog):
    """开始采集设置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("开始采集设置")
        # 最小宽度按最长选项文字（「按时间戳自动命名（如 采集_20260811_143000）」）
        # 的完整渲染需求取值：440 时 QRadioButton 文字右端被布局裁剪，
        # 用户看不到完整选项文案。
        self.setMinimumWidth(560)
        self._init_ui()
        self._update_ok_state()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # ── 区块一：会话命名 ──
        layout.addWidget(self._section_label("会话命名"))
        self.rb_auto = QRadioButton("按时间戳自动命名（如 采集_20260811_143000）")
        self.rb_custom = QRadioButton("自定义会话名称")
        self.rb_custom.toggled.connect(self._on_naming_mode_changed)
        # 会话命名组：自动 / 自定义二选一。QButtonGroup 显式分组后与
        # 通道命名组彻底独立——不分组时 Qt autoExclusive 默认让同父的
        # 4 个 QRadioButton 全局互斥，点击任一会取消另一区块的选中。
        self._group_session = QButtonGroup(self)
        self._group_session.addButton(self.rb_auto)
        self._group_session.addButton(self.rb_custom)
        self.rb_auto.setChecked(True)
        self.edt_name = QLineEdit()
        self.edt_name.setPlaceholderText("输入会话名称，如：温控箱-01")
        self.edt_name.setEnabled(False)
        self.edt_name.textChanged.connect(self._update_ok_state)
        layout.addWidget(self.rb_auto)
        layout.addWidget(self.rb_custom)
        layout.addWidget(self.edt_name)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"color:{Theme.BORDER};")
        layout.addWidget(line)

        # ── 区块二：通道命名 ──
        layout.addWidget(self._section_label("通道命名"))
        self.rb_reset_names = QRadioButton(
            "使用默认名称（CH1/CH2…）")
        self.rb_prev_names = QRadioButton(
            "使用上一次的名称（沿用上次各通道自定义名）")
        # 通道命名组：默认 / 上一次二选一，与会话命名组独立（同上）。
        self._group_channel = QButtonGroup(self)
        self._group_channel.addButton(self.rb_reset_names)
        self._group_channel.addButton(self.rb_prev_names)
        self.rb_prev_names.setChecked(True)
        layout.addWidget(self.rb_reset_names)
        layout.addWidget(self.rb_prev_names)

        # ── 按钮 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_cancel)
        self._btn_ok = QPushButton("确定")
        self._btn_ok.setProperty("buttonRole", "primary")
        self._btn_ok.setStyleSheet(Theme.styled_button("primary"))
        self._btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(self._btn_ok)
        layout.addLayout(btn_row)

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-weight:bold;color:{Theme.TEXT};")
        return lbl

    def _on_naming_mode_changed(self, custom_on: bool):
        self.edt_name.setEnabled(custom_on)
        if custom_on:
            self.edt_name.setFocus()
        self._update_ok_state()

    def _update_ok_state(self):
        custom = self.rb_custom.isChecked()
        self._btn_ok.setEnabled(not custom or bool(self.edt_name.text().strip()))

    def session_name(self) -> Optional[str]:
        """自定义会话名；选「按时间自动命名」返回 None。"""
        if self.rb_custom.isChecked():
            return self.edt_name.text().strip() or None
        return None

    def reset_channel_names(self) -> bool:
        """True = 重置为原始通道名（CH1/CH2…）；False = 沿用上次命名。"""
        return self.rb_reset_names.isChecked()
