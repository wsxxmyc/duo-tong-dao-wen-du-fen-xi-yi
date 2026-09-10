# -*- coding: utf-8 -*-
"""
ChannelGroup — 一组通道（最多 8 个）。

组头：折叠箭头 + 组号 + 状态 + 启用开关。
组内：固定高度的通道卡片，可整体折叠 / 展开，可整体启用 / 停用。
卡片始终展示（无有效数据也显示 --），折叠仅由用户手动触发。
"""
import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QToolButton, QSizePolicy,
)

from device.datastore import store
from ui.widgets.channel_card import ChannelCard, ClassicChannelCard

from ui.theme import Theme
from ui.widgets.toggle_switch import ToggleSwitch

# 组内卡片布局常量：行卡间距（卡表融合行卡间不再插 1px 分隔线）。
# 首次打开默认窗口尺寸按 8 卡无滚动计算（ChannelPanel.preferred_viewport_height），
# 改动该数值必须同步该计算。
CARD_SPACING = 4


def _header_qss(section_size=Theme.TYPE_SCALE["section"]) -> str:
    """组头样式：调用时按当前主题生成（主题切换后重建组头即可跟随）。"""
    return f"""
QToolButton {{
    background: transparent;
    border: none;
    color: {Theme.TEXT};
    font-size: {section_size}pt;
    padding: 0 2px;
}}
QToolButton:hover {{ color: {Theme.readable_text(Theme.ACCENT)}; }}
QLabel {{
    background: transparent;
    border: none;
}}
"""


class ChannelGroup(QWidget):
    # 组启用开关变化信号（group_idx, enabled）——供采集组数即时联动
    enabled_changed = pyqtSignal(int, bool)
    """一组通道：组头 + 卡片列表（可折叠 / 可整体启用）。"""

    @staticmethod
    def cards_layout_alignment():
        """返回通道卡片固定从顶部排列的布局方式。"""
        return Qt.AlignTop

    def __init__(self, group_idx, channels, mw, panel):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.group_idx = group_idx        # 0-based
        self.channels = channels          # list[Channel]
        self.mw = mw                      # MainWindow
        self.panel = panel                # ChannelPanel
        self.cards: dict[int, ChannelCard] = {}
        self.collapsed = False
        self.enabled = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(1)

        # ── 组头 ──
        self.head = QWidget()
        hh = QHBoxLayout(self.head)
        hh.setContentsMargins(2, 1, 2, 1)
        hh.setSpacing(2)
        self.head.setStyleSheet(_header_qss())

        self.btn_toggle = QToolButton()
        self.btn_toggle.setToolTip("折叠 / 展开本组")
        self.btn_toggle.clicked.connect(self.toggle_collapse)
        hh.addWidget(self.btn_toggle)

        self.lbl_name = QLabel(f"第 {group_idx + 1} 组")
        self.lbl_name.setStyleSheet(
            f"font-weight:600;font-size:{Theme.TYPE_SCALE['section']}pt;"
            f"color:{Theme.TEXT};")
        hh.addWidget(self.lbl_name, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};font-size:{Theme.TYPE_SCALE['caption']}pt;")
        hh.addWidget(self.lbl_status)

        self.chk_enable = ToggleSwitch("启用")
        self.chk_enable.setToolTip("开启 = 本组所有通道参与显示；关闭 = 整体停用")
        self.chk_enable.setChecked(True)
        # 用 stateChanged 而非 toggled：从“半选”点成全选时 Qt 只发
        # stateChanged（isChecked 布尔值未变），toggled 收不到会导致开关无响应
        self.chk_enable.stateChanged.connect(self._on_enable_state_changed)
        hh.addWidget(self.chk_enable)
        outer.addWidget(self.head)

        # ── 卡片容器（可折叠）──
        self.cards_widget = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_widget)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(CARD_SPACING)
        self.cards_layout.setAlignment(self.cards_layout_alignment())
        outer.addWidget(self.cards_widget)
        self.cards_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # ── 建卡片（视图模式决定卡类：卡表融合行卡 / 经典 70px 大卡）──
        card_cls = (ChannelCard if panel.view_mode == "table"
                    else ClassicChannelCard)
        for c in channels:
            card = card_cls(c)
            card.visibility_toggled.connect(panel._on_visibility_changed)
            card.hover_changed.connect(panel._on_card_hover_changed)
            self.cards_layout.addWidget(card)
            self.cards[c.index] = card

        self.set_collapsed(False)
        self._set_arrow()

    # ------------------------------------------------------------------
    #  折叠 / 展开
    # ------------------------------------------------------------------
    def toggle_collapse(self) -> None:
        """切换折叠 / 展开状态并通知面板。"""
        self.set_collapsed(not self.collapsed)
        self.panel._on_group_collapse_changed(self.group_idx, self.collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        """设置折叠状态（True=折叠隐藏卡片）。"""
        self.collapsed = bool(collapsed)
        self.cards_widget.setVisible(not self.collapsed)
        self._set_arrow()

    def set_header_visible(self, visible: bool) -> None:
        """仅一组通道时隐藏组头标题，直接展示通道数据，节省垂直空间。"""
        self.head.setVisible(bool(visible))

    def _set_arrow(self):
        self.btn_toggle.setText("▸" if self.collapsed else "▾")

    # ------------------------------------------------------------------
    #  启用 / 停用（整体）
    # ------------------------------------------------------------------
    def _on_enable_state_changed(self, state):
        """组启用开关：选中(Checked)=全开、未选中(Unchecked)=全关。

        监听 stateChanged：半选态点成全选时 Qt 不发出 toggled，
        用 stateChanged 才能保证每次点击都响应。半选状态仅由
        sync_card_checks() 程序设置（blockSignals），点击时不会停在半选。
        """
        enabled = (state == Qt.Checked)
        self.enabled = enabled
        s = store.active
        if s is None:
            return
        for c in self.channels:
            if c.index < len(s.channels):
                c.visible = enabled
        if store.config is not None:
            # 静默批量持久化（notify=False）：组启停只局部刷新卡片开关，
            # 不触发 channels_changed → 整面板重建
            with store.config.batch(notify=False):
                for c in self.channels:
                    store.config.set_visible(c.key, enabled, save=False)
            store.config.save()
        # 卡片开关跟随新状态（替代全量重建）
        self.sync_card_checks()
        self.mw.refresh_plots()
        # 通知面板/主窗口：组启停 → 采集组数联动（用户取消组 = 停止采集该组）
        self.enabled_changed.emit(self.group_idx, enabled)

    def set_enabled(self, enabled: bool) -> None:
        """程序化设置启用状态（不触发信号）。"""
        self.enabled = bool(enabled)
        self.chk_enable.blockSignals(True)
        self.chk_enable.setChecked(self.enabled)
        self.chk_enable.blockSignals(False)

    # ------------------------------------------------------------------
    #  数据刷新 / 同步
    # ------------------------------------------------------------------
    def refresh_values(self, s) -> None:
        """刷新组内所有卡片（当前值 + 统计）。"""
        for idx, card in self.cards.items():
            ch = s.channel_by_index(idx)
            if ch is None:
                card.show_none()
            else:
                self.panel._refresh_card(card, ch)

    def sync_card_checks(self) -> None:
        """把组内卡片勾选与通道 visible 同步，并更新组启用状态。"""
        s = store.active
        if s is None:
            return
        vis_list = []
        for idx, card in self.cards.items():
            ch = s.channel_by_index(idx)
            if ch is not None:
                card.set_visible_state(ch.visible)
                vis_list.append(ch.visible)
        # 组启用状态：全部可见→勾选，全部不可见→不勾选，混合→半选
        if vis_list:
            all_on = all(vis_list)
            any_on = any(vis_list)
            self.chk_enable.blockSignals(True)
            if all_on:
                self.chk_enable.setCheckState(Qt.Checked)
            elif not any_on:
                self.chk_enable.setCheckState(Qt.Unchecked)
            else:
                self.chk_enable.setCheckState(Qt.PartiallyChecked)
            self.chk_enable.blockSignals(False)

    def has_data(self, s) -> bool:
        """本组是否有有效数据（任一通道非全 NaN）。"""
        for c in self.channels:
            col = s.buffer.column(c.index)
            if col.size and np.isfinite(col).any():
                return True
        return False

    def set_status(self, text: str) -> None:
        """设置组状态文本。"""
        self.lbl_status.setText(text)
