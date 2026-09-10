# -*- coding: utf-8 -*-
"""非模态报警对话框：列出当前 active 报警，提供「复位」（确认静音）按钮。

交互语义（与 main_window 配合）：
- 超限时弹出本窗，列出未确认的 active 报警（通道 / 类型）。
- 点「复位」发出 ``acked`` 信号 → 主窗口确认静音（停声音、关弹窗），
  但曲线高亮 / 状态栏指示保持，直到温度真正恢复正常后自动清除。
- 全部报警恢复后由主窗口隐藏本窗。
- 非模态（``setModal(False)``），不阻塞采集与界面操作。
"""
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel, QListWidget,
                             QPushButton, QHBoxLayout)

from ui.theme import Theme


class AlarmDialog(QDialog):
    """非模态持续报警窗。"""

    acked = pyqtSignal()   # 「复位」按钮点击

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("温度报警")
        self.setModal(False)
        self.setObjectName("alarmDialog")
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)

        self._title = QLabel("⚠  检测到温度超限")
        self._title.setStyleSheet(
            f"color:{Theme.readable_text(Theme.RED)}; font-weight:bold; font-size:14pt;")
        lay.addWidget(self._title)

        self._list = QListWidget()
        self._list.setMinimumHeight(120)
        lay.addWidget(self._list)

        self._hint = QLabel(
            "复位将停止声音并关闭此窗口；曲线高亮与状态栏指示保持，"
            "直到温度恢复正常后自动清除。")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color:{Theme.TEXT_MUTED};")
        lay.addWidget(self._hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_ack = QPushButton("复位（确认静音）")
        self._btn_ack.setProperty("buttonRole", "primary")
        self._btn_ack.setStyleSheet(Theme.styled_button("warning"))
        self._btn_ack.clicked.connect(self._on_ack)
        btn_row.addWidget(self._btn_ack)
        lay.addLayout(btn_row)

    def refresh_theme(self):
        """主题切换后重放构造期固化的局部样式。

        本弹窗为持久实例（MainWindow 缓存复用，关闭仅隐藏），隐藏时切
        主题若不重放，重开会停留旧主题配色；列表样式走全局 QList 规则
        无需处理。
        """
        self._title.setStyleSheet(
            f"color:{Theme.readable_text(Theme.RED)}; font-weight:bold; font-size:14pt;")
        self._hint.setStyleSheet(f"color:{Theme.readable_text(Theme.TEXT_MUTED)};")
        self._btn_ack.setStyleSheet(Theme.styled_button("warning"))

    def set_alarms(self, items):
        """更新报警列表；空列表则隐藏窗口。"""
        self._list.clear()
        if not items:
            self.hide()
            return
        for it in items:
            self._list.addItem(it)
        if not self.isVisible():
            self.show()

    def _on_ack(self):
        self.acked.emit()
