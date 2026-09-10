# -*- coding: utf-8 -*-
"""
组合图布局调参弹窗 —— 从 app.py 的 _open_combo_layout_tuner 提取。

创建一个非模态 QDialog，浮在组合图上方，实时调参即时生效。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QSpinBox, QPushButton,
)
from ui.theme import Theme


class ComboTunerDialog(QDialog):
    """组合图布局调参弹窗，通过 self.parent 引用 MainWindow。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent

        self.setWindowTitle("组合图布局调试 — 实时调参")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # 用当前实际值（覆盖或默认）初始化 spinbox
        ovr = getattr(self.parent, '_layout_ovr', {})
        defaults = self.parent._combo_layout_defaults
        sb_map = {}
        meta = [
            ("margin_left",   "左边距（px）",   0, 200, "Y 轴标题 + tick 标签的左侧留白"),
            ("margin_right",  "右边距（px）",   0, 200, "右侧留白（无坐标轴，可很小）"),
            ("margin_top",    "上边距（px）",   0, 200, "顶部留白（已无总标题，可调小）"),
            ("margin_bottom", "下边距（px）",   0, 200, "底部 X 轴标题 + tick 标签留白"),
            ("gap_h",         "水平间隙（px）", 0, 200, "底部两列子图之间的间距"),
            ("gap_v",         "垂直间隙（px）", 0, 200, "上下两行子图之间的间距"),
            ("cbar_reserve",  "颜色条预留（px）", 0, 200, "右侧颜色条占用的宽度"),
        ]

        for key, label, lo, hi, tip in meta:
            sb = QSpinBox()
            sb.setRange(lo, hi)
            sb.setValue(int(ovr.get(key, defaults[key])))
            sb.setToolTip(tip)
            sb_map[key] = sb
            form.addRow(f"{label}：", sb)
            # 改值即刷新组合图
            sb.valueChanged.connect(self._apply_tuner_refresh)

        self._sb_map = sb_map
        # 存储到 parent 方便其他回调读取
        parent._tuner_sb = sb_map
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("✅ 保存为全局默认")
        btn_save.setStyleSheet(Theme.styled_button("success"))
        btn_save.clicked.connect(self._save_tuner_default)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        # 立即应用一次当前值
        self._apply_tuner_refresh()

        # 非模态弹窗：浮在组合图上方，调参时组合图实时更新
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.parent._tuner_dlg = self

    def _apply_tuner_refresh(self):
        """读取调谐器当前值，更新 _layout_ovr 并立即重绘组合图。"""
        self.parent._apply_tuner_refresh()
        sb_map = getattr(self, "_sb_map", None)
        if sb_map is not None:
            ovr = {key: sb_map[key].value()
                   for key in self.parent.chart_renderer.LAYOUT_KEYS}
            self.parent._save_combo_layout_config(ovr)

    def _save_tuner_default(self):
        """当前调谐器值保存为全局默认配置。"""
        self.parent._save_tuner_default()
