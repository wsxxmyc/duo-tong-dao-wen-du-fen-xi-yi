# -*- coding: utf-8 -*-
"""
ComboPanel — 组合图布局调优面板组件。
从 app.py 提取 MainWindow 组合图 UI 构建方法，通过 self.mw 引用 MainWindow。
"""
from ui.theme import Theme
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QSpinBox, QPushButton, QDialog,
)


class ComboPanel(QWidget):
    """组合图标签页：多通道温度曲线子图布局与导出。"""
    def __init__(self, mw):
        super().__init__()
        self.mw = mw  # MainWindow 引用
        self._tuner_sb = None  # 调谐器 spinbox 映射
        self._tuner_dlg = None  # 调谐器对话框引用

    def open_tuner(self) -> None:
        """弹出组合图布局调试面板。原 MainWindow._open_combo_layout_tuner"""
        dlg = QDialog(self.mw)
        dlg.setWindowTitle("组合图布局调试 — 实时调参")
        dlg.setMinimumWidth(420)

        layout = QVBoxLayout(dlg)
        form = QFormLayout()

        ovr = getattr(self.mw, '_layout_ovr', {})
        defaults = self.mw.chart_renderer._combo_layout_defaults
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
            sb.valueChanged.connect(self.apply_refresh)

        self._tuner_sb = sb_map
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_save = QPushButton("✅ 保存为全局默认")
        btn_save.setStyleSheet(Theme.styled_button("success"))
        btn_save.clicked.connect(self.save_default)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dlg.close)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self.apply_refresh()

        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
        self._tuner_dlg = dlg
        dlg.show()

    def apply_refresh(self) -> None:
        """读取调谐器当前值，更新 _layout_ovr 并立即重绘组合图。原 MainWindow._apply_tuner_refresh"""
        sb_map = getattr(self, '_tuner_sb', None)
        if sb_map is None:
            return
        ovr = {}
        for key in self.mw.chart_renderer.LAYOUT_KEYS:
            ovr[key] = sb_map[key].value()
        self.mw._layout_ovr = ovr
        # 实时调节同时持久化，重启后恢复最后一次有效布局。
        self.mw._save_combo_layout_config(ovr)
        if self.mw.dataset is not None and self.mw.processed:
            self.mw.chart_renderer._plot_combo()
            self.mw.statusBar().showMessage(
                f"布局已刷新：L{ovr['margin_left']} R{ovr['margin_right']} "
                f"T{ovr['margin_top']} B{ovr['margin_bottom']} "
                f"H{ovr['gap_h']} V{ovr['gap_v']}", 2000)

    def save_default(self) -> None:
        """当前调谐器值保存为全局默认配置。原 MainWindow._save_tuner_default"""
        sb_map = getattr(self, '_tuner_sb', None)
        if sb_map is None:
            return
        ovr = {}
        for key in self.mw.chart_renderer.LAYOUT_KEYS:
            ovr[key] = sb_map[key].value()
        self.mw._layout_ovr = ovr
        self.mw._save_combo_layout_config(ovr)
        self.mw.statusBar().showMessage("组合图布局参数已保存为全局默认", 3000)
