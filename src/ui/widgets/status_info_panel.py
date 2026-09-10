# -*- coding: utf-8 -*-
"""底部实时信息面板：集中展示采集、点数、时长与网络状态。"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ui.theme import Theme
from ui.widgets.heartbeat_indicator import HeartbeatIndicator


class StatusInfoPanel(QFrame):
    """两行高对比状态面板，一级指标使用亮色胶囊。"""

    PRIMARY_HEIGHT = 28

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusInfoPanel")
        self.setMinimumHeight(68)
        self.setMaximumHeight(74)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(4)

        primary = QFrame(self)
        primary.setObjectName("statusPrimaryRow")
        primary_layout = QHBoxLayout(primary)
        primary_layout.setContentsMargins(0, 0, 0, 0)
        primary_layout.setSpacing(8)
        self.lbl_acq_status = self._label("未连接", "info", True, primary)
        self.lbl_point_count = self._label("0 点", "info", True, primary)
        self.lbl_elapsed = self._label("⏱ 采集时长：--", "current", True, primary)
        self.lbl_net_status = self._label("网络：未启用", "info", True, primary)
        # IP 是一级信息；正常窗口下完整显示，窄窗口仍由 MainWindow 的
        # elide 逻辑与 tooltip 兜底。
        # 状态栏在窄窗口仍需允许省略地址，完整值由 tooltip 保留。
        self.lbl_net_status.setMinimumWidth(180)
        self.lbl_net_status.setMaximumWidth(440)
        self.heartbeat = HeartbeatIndicator(18, primary)
        primary_layout.addWidget(self.lbl_acq_status)
        primary_layout.addWidget(self.lbl_point_count)
        primary_layout.addWidget(self.lbl_elapsed)
        primary_layout.addWidget(self.heartbeat)
        primary_layout.addWidget(self.lbl_net_status, 1)
        layout.addWidget(primary)

        secondary = QFrame(self)
        secondary.setObjectName("statusInfoZone")
        secondary_layout = QHBoxLayout(secondary)
        secondary_layout.setContentsMargins(0, 0, 0, 0)
        secondary_layout.setSpacing(12)
        self.lbl_live_status = self._label("历史文件 · 不在实时采集", "info", False, secondary)
        self.lbl_live_status.setMinimumWidth(300)
        self.lbl_dual_view_info = self._label("单视图", "current", False, secondary)
        self.lbl_dual_view_info.setObjectName("dualViewStatusLabel")
        self.lbl_mem_usage = self._label("内存：--", "info", False, secondary)
        self._alarm_label = self._label("", "warning", False, secondary)
        self.lbl_source = self._label("", "info", False, secondary)
        self.lbl_source.setObjectName("statusSourceLabel")
        secondary_layout.addWidget(self.lbl_live_status, 1)
        secondary_layout.addWidget(self.lbl_dual_view_info)
        secondary_layout.addWidget(self.lbl_mem_usage)
        secondary_layout.addWidget(self._alarm_label)
        secondary_layout.addWidget(self.lbl_source)
        layout.addWidget(secondary)
        self.refresh_theme()

    def _label(self, text, role, emphasis, parent):
        label = QLabel(text, parent)
        label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.set_semantic(label, role, emphasis)
        return label

    def set_semantic(self, label, role, emphasis=None):
        """按语义重放标签样式，主题切换时也由此统一刷新。"""
        if emphasis is None:
            emphasis = bool(label.property("semanticEmphasis"))
        label.setProperty("semanticRole", role)
        label.setProperty("semanticEmphasis", bool(emphasis))
        if emphasis:
            label.setStyleSheet(
                Theme.semantic_emphasis_qss(role, weight=600, radius=6, pad_x=9)
                + "font-size:12pt; min-height:26px;")
        else:
            label.setStyleSheet(
                f"color:{Theme.semantic_text(role, Theme.statusbar_surface())};"
                "font-size:10pt;font-weight:600;letter-spacing:0.25px;")

    def refresh_theme(self):
        """重放专属底色和所有标签语义色。"""
        surface = Theme.statusbar_surface()
        border = Theme.semantic_text("current", surface)
        self.setStyleSheet(
            f"QFrame#statusInfoPanel{{background:{surface};"
            f"border-top:2px solid {border};}}"
            "QFrame#statusPrimaryRow,QFrame#statusInfoZone{"
            "background:transparent;border:none;}")
        for label in self.findChildren(QLabel):
            self.set_semantic(label, label.property("semanticRole") or "info")
