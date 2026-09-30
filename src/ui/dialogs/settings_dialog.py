# -*- coding: utf-8 -*-
"""
设置弹窗 —— 从 app.py 主窗口提取的 SettingsDialog 类。

包含 7 个分页的详细参数设置弹窗，以及所有分页构建方法、导航切换、
字体调节、参数预设保存/加载等辅助方法。
"""

from __future__ import annotations

import re

from PyQt5.QtCore import Qt, QTime, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QGroupBox,
    QPushButton, QLabel, QTableWidget, QHeaderView,
    QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit,
    QMessageBox, QScrollArea, QAbstractItemView,
    QButtonGroup, QRadioButton,
    QCheckBox, QGridLayout, QSizePolicy, QLineEdit,
)
from PyQt5.QtWidgets import QAbstractSpinBox

from utils.config_io import (
    ConfigIO, LIVE_MONITOR_DEFAULTS, LIVE_WINDOW_SEC_CHOICES,
    LIVE_WINDOW_SEC_DEFAULT, parse_percent)
from utils.helpers import px_to_pt
from ui.theme import Theme
from ui.dialogs.channel_naming_dialog import NameListDelegate
from ui.widgets.toggle_switch import ToggleSwitch


class PercentLineEdit(QLineEdit):
    """百分比输入框：接受 ``100`` / ``"100%"`` / ``60`` 等写法并做范围校验与提示。

    为什么不用 QSpinBox：旧版卡片透明度用的是量程写死 ``setRange(30, 95)`` 的
    QSpinBox，键入 ``100`` 会被静默截断成 ``95``、``0`` 被抬成 ``30``，用户直观
    感受就是「透明度设置不生效」。本控件改用文本解析，允许用户直接键入 ``100%``，
    并对非法输入与越界值给出可见提示，而不是无声截断。

    对外保持 QSpinBox 的 ``value()`` / ``setValue()`` / ``valueChanged`` 接口，
    以便 ``main_window._apply_live_monitor_page`` 与 ``sync_live_monitor_widgets``
    等既有调用方无需改动。
    """

    valueChanged = pyqtSignal(float)

    def __init__(self, minimum=0.0, maximum=100.0, parent=None):
        super().__init__(parent)
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self._value = self._minimum
        self._hint = ""
        self.setPlaceholderText(f"{self._minimum:g} ~ {self._maximum:g}（可带 %）")
        self.setToolTip(f"{self._minimum:g} ~ {self._maximum:g}（可带 %）")
        self.textEdited.connect(self._on_text_edited)
        self.editingFinished.connect(self._on_editing_finished)

    # ---- 公开接口（与 QSpinBox 用法保持一致）----
    def value(self):
        """当前有效值（百分比，已钳制到量程内）。"""
        return self._value

    def setValue(self, value):
        """程序赋值；不发射 valueChanged，避免与外部回灌形成回环。"""
        ok, number, message = parse_percent(value, self._minimum, self._maximum)
        if not ok:
            return
        self._value = number
        self._set_text_for(number, "")

    def minimum(self):
        return self._minimum

    def maximum(self):
        return self._maximum

    def hint(self):
        """最近一次校验的提示文案（空串表示输入合法且在范围内）。"""
        return self._hint

    # ---- 校验与提示 ----
    def _on_text_edited(self, text):
        ok, number, message = parse_percent(text, self._minimum, self._maximum)
        if ok:
            self._value = number
            self._hint = message          # 越界时 message 为修正提示，合法时为空
            self._mark_state(bool(message), message)
            self.valueChanged.emit(number)
        else:
            self._hint = message
            self._mark_state(True, message)

    def _on_editing_finished(self):
        """失焦时收敛：非法输入还原为上一次有效值，越界值修正到端点。"""
        ok, number, message = parse_percent(self.text(), self._minimum, self._maximum)
        if ok:
            self._value = number
            self._hint = message
            self._set_text_for(number, message)
            self.valueChanged.emit(number)
        else:
            self._hint = "输入无效，已还原为上一次有效值"
            self._set_text_for(self._value, self._hint)

    def _set_text_for(self, number, message):
        self.blockSignals(True)
        self.setText(f"{number:g}")
        self.blockSignals(False)
        self._mark_state(bool(message), message)

    def _mark_state(self, warn, message):
        """越界/非法时描红并挂提示，合法时恢复正常边框。"""
        border = Theme.RED if warn else Theme.BORDER
        self.setStyleSheet(
            f"QLineEdit {{ border: 1px solid {border}; border-radius: 3px;"
            f" padding: 2px 4px; }}")
        self.setToolTip(
            message or f"{self._minimum:g} ~ {self._maximum:g}（可带 %）")


class SettingsDialog(QDialog):
    """详细参数设置弹窗（两栏布局）：左侧固定导航栏 + 右侧参数配置内容区。
    全部设置项都通过本弹窗调整，从而把主页面尽量让给画布。

    通过 self.parent 引用 MainWindow。
    """

    # 弹窗内输入控件统一固定宽度（像素），保证长中文文字完全显示
    _INW = 220
    # 悬浮监控页取色按钮/色值输入/恢复按钮统一尺寸（替代散落魔法数，视觉值不变）
    _SWATCH = (48, 22)
    _HEX_W = 120
    _RESET_W = 96
    _DEFAULT_SIZE = (1000, 680)
    _MIN_SIZE = (920, 600)
    # 与 MainWindow.open_settings 的 page_names 一致（依赖其导航索引）
    _PAGE_NAMES = ["通道管理", "采集设置", "采集时间", "数据处理",
                   "轴设置", "组合图", "温升统计", "报警设置", "悬浮监控"]

    def __init__(self, parent, nav, stack):
        super().__init__(parent)
        self.parent = parent
        self.nav = nav
        self.stack = stack
        self.setObjectName("settingsDialog")

        self.setWindowTitle("参数设置")
        # 字号继承自主窗口当前字体等级，用户可在「显示设置」中调节
        fs = self.parent._font["base"]
        _f = self.font()
        _f.setPointSize(fs)
        self.setFont(_f)
        self.setFixedSize(*self._DEFAULT_SIZE)
        # 根布局：顶部（左侧导航 + 右侧内容区） + 底部全局操作栏
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(0)
        top.addWidget(nav, 0)        # 左侧固定导航栏（nav 自身已 setFixedWidth）
        content = QWidget()
        content.setObjectName("settingsContent")
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(0)
        content_lay.addWidget(stack, 1)
        top.addWidget(content, 1)    # 右侧参数配置内容区（随窗口拉伸）
        root.addLayout(top, 1)
        root.addWidget(self._build_bottom_bar())

        # 构建所有分页（添加到 self.stack）
        self._build_all_pages()

        # 未保存守卫：记录各"需应用"页面的最近已应用快照
        self._page_snapshots = {}
        self._current_nav_item = None
        self._showing_dirty_hint = False
        self._refresh_all_snapshots()

        # 脏状态反馈：各"需应用"页注册控件的变更信号 → 更新底部操作栏提示
        self._wire_dirty_feedback()

    def _build_all_pages(self):
        """构建所有设置分页面并添加到 stack。"""
        self._build_channel_page()
        self._build_acq_page()
        self._build_time_page()
        self._build_anomaly_page()
        self._build_axis_page()
        self._build_overview_page()
        self._build_stat_page()
        self._build_alarm_page()
        self._build_live_monitor_page()

    def closeEvent(self, event) -> None:
        """关闭弹窗前：未保存修改守卫 + 保存当前尺寸到配置文件。"""
        if not self._confirm_leave_page():
            event.ignore()
            return
        try:
            sz = self.size()
            ConfigIO.save_section("settings_dialog_geometry", {"w": sz.width(), "h": sz.height()})
        except Exception:
            pass
        super().closeEvent(event)

    # ----------------------------------------------------- 底部全局操作栏
    def _build_bottom_bar(self):
        """底部全局操作栏：左侧状态反馈行 + 右侧「应用保存 / 返回 / 关闭」。

        所有分页共用这一组按钮，各页不再各自放置「应用」按钮；
        页面专属动作（读取/写入采样通道、添加/删除名称等）保留在页内。
        """
        bar = QWidget()
        bar.setObjectName("settingsBottomBar")
        bar.setFixedHeight(44)
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 4, 12, 4)
        h.setSpacing(8)

        # 左侧状态反馈行：脏状态提示常驻，成功/警告类消息 3 秒淡出
        self._status_label = QLabel("")
        self._status_label.setObjectName("settingsPageStatus")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"color:{Theme.TEXT_MUTED};")
        h.addWidget(self._status_label, 1)
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._clear_transient_status)

        self.btn_apply_all = QPushButton("应用保存")
        self.btn_apply_all.setObjectName("applyAllButton")
        self.btn_apply_all.setProperty("buttonRole", "primary")
        self.btn_apply_all.setToolTip("应用所有页面未保存的修改，不关闭弹窗")
        self.btn_apply_all.clicked.connect(self._on_apply_all)
        h.addWidget(self.btn_apply_all)

        self.btn_return = QPushButton("返回")
        self.btn_return.setObjectName("returnButton")
        self.btn_return.setProperty("buttonRole", "secondary")
        self.btn_return.setToolTip("应用所有未保存的修改并关闭弹窗，回到主窗口")
        self.btn_return.clicked.connect(self._on_return)
        h.addWidget(self.btn_return)

        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("closeSettingsButton")
        self.btn_close.setProperty("buttonRole", "secondary")
        self.btn_close.clicked.connect(self.close)
        h.addWidget(self.btn_close)
        return bar

    def _apply_all_pages(self) -> bool:
        """应用所有存在未保存修改的分页；同一回调去重（页2/3 均映射刷新）。"""
        actions = {}
        for idx in sorted(self._page_field_specs()):
            if self._page_dirty(idx):
                action = self._apply_action_for_page(idx)
                if action is not None:
                    actions[action] = True
        for action in actions:
            try:
                action()
            except Exception:
                return False
        self._refresh_all_snapshots()
        self._update_dirty_feedback()
        return True

    def _on_apply_all(self):
        """「应用保存」：应用全部未保存修改，保持弹窗打开。"""
        if self._apply_all_pages():
            self.set_page_status("已保存全部设置 ✓", "success")
        else:
            self.set_page_status("应用失败，请检查参数", "warning")

    def _on_return(self):
        """「返回」：应用全部未保存修改后关闭，回到主窗口。"""
        if not self._apply_all_pages():
            self.set_page_status("应用失败，请检查参数", "warning")
            return
        self.close()

    def _clear_transient_status(self):
        """3 秒定时到点，清空临时状态消息。"""
        self._showing_dirty_hint = False
        self._status_label.clear()

    # ----------------------------------------------------- 脏状态反馈
    def _wire_dirty_feedback(self):
        """把各"需应用"页注册控件的变更信号统一接到脏状态反馈。"""
        for specs in self._page_field_specs().values():
            for widget, _g, _s in specs:
                for signal in self._field_change_signals(widget):
                    signal.connect(self._on_field_changed)

    @staticmethod
    def _field_change_signals(widget):
        """返回控件的变更信号列表（组合框可编辑时补接文本编辑信号）。"""
        if isinstance(widget, QComboBox):
            sigs = [widget.currentIndexChanged]
            if widget.isEditable():
                sigs.append(widget.editTextChanged)
            return sigs
        if isinstance(widget, QAbstractSpinBox):
            # QTimeEdit 无 valueChanged，需接 timeChanged
            return ([widget.timeChanged] if isinstance(widget, QTimeEdit)
                    else [widget.valueChanged])
        if isinstance(widget, QCheckBox):
            return [widget.stateChanged]
        if isinstance(widget, QRadioButton):
            return [widget.toggled]
        return []

    def _on_field_changed(self, *_args):
        """任意注册控件变更 → 刷新底部操作栏脏提示与「应用保存」高亮。"""
        self._update_dirty_feedback()

    def _update_dirty_feedback(self):
        """按当前脏页面更新「应用保存」高亮与状态行常驻提示。"""
        dirty = [self._PAGE_NAMES[i] for i in sorted(self._page_field_specs())
                 if self._page_dirty(i)]
        btn = getattr(self, "btn_apply_all", None)
        if dirty:
            self._showing_dirty_hint = True
            self._status_timer.stop()
            if btn is not None and not btn.property("needsApply"):
                btn.setProperty("needsApply", True)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            self._status_label.setStyleSheet(
                f"color:{Theme.readable_text(Theme.ORANGE, Theme.BG_PRIMARY)};"
                f"font-weight:600;padding:2px 10px;")
            self._status_label.setText("有未应用的修改：" + "、".join(dirty))
        else:
            if btn is not None and btn.property("needsApply"):
                btn.setProperty("needsApply", False)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
            if self._showing_dirty_hint:
                self._showing_dirty_hint = False
                self._status_label.clear()

    # ----------------------------------------------------- 输入控件统一宽度
    def _fix_input(self, w):
        """设置输入控件的宽度策略，下拉框自适应内容不被截断。"""
        w.setMinimumWidth(120)
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if isinstance(w, QComboBox):
            # 让下拉框根据最长项自动调整宽度，不截断文字
            w.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        else:
            w.setMaximumWidth(self._INW)
        if isinstance(w, QAbstractSpinBox):
            # 键盘键入时回车/失焦才提交，避免输入过程触发即时应用
            w.setKeyboardTracking(False)
        return w

    # ----------------------------------------------------- 卡片 / 页面辅助
    def _new_page_scroll(self):
        """返回一个可滚动的参数页：内部垂直布局 lay（卡片等距排列，底部留白）。"""
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("pageScroll")
        content = QWidget()
        content.setObjectName("pageContent")
        content.setMinimumWidth(0)
        # 忽略子控件的理论最小宽度，强制内容跟随固定视口收缩。
        content.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(7)
        scroll.setWidget(content)
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(scroll)
        return page, lay

    def _new_page_fill(self):
        """返回一个不可滚动、填满高度的参数页（适用于含大表格的页面）。"""
        page = QWidget()
        content = QWidget()
        content.setObjectName("pageContent")
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(content)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(content)
        return page, lay

    def _page_intro(self, lay, text):
        """分类说明（T1 教程/提示卡，样式统一由 _apply_style 的
        settingsPageIntro 规则提供：卡片底 + 左侧强调条）。

        控件级只烘焙字号——非文字垂直占位由 QSS 的 padding+border 决定
        （整改计划 §4 T1：冻结页高度预算，禁止在此加 padding/border）。
        """
        t = QLabel(text)
        t.setObjectName("settingsPageIntro")
        t.setWordWrap(True)
        fs = self.parent._current_font["intro"]
        # 文字色不烘焙：由 _apply_style 的 settingsPageIntro 规则按当前主题给出
        t.setStyleSheet(f"font-size:{px_to_pt(fs)}pt;line-height:1.4;")
        lay.addWidget(t)

    def _compact_grid(self, parent_layout, cards, object_name, dense=False):
        """将参数卡片按两列排列，减少页面纵向高度。

        dense=True 保持紧凑间距（高度冻结页专用，见整改计划 §6 杠杆 1）。
        """
        grid_widget = QWidget()
        grid_widget.setObjectName(object_name)
        grid_widget.setMinimumWidth(0)
        grid_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(3 if dense else 8)
        for index, card in enumerate(cards):
            grid.addWidget(card, index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        parent_layout.addWidget(grid_widget)
        return grid_widget

    def _param_card(self, title, control, desc, dense=False):
        """参数行（无边框留白块）：顶部左右「参数名称 + 输入控件」，下方说明文字。

        dense=True 保持紧凑留白（高度冻结页专用）。
        """
        card = QWidget()
        card.setObjectName("paramCard")
        card.setMinimumWidth(0)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v = QVBoxLayout(card)
        if dense:
            v.setContentsMargins(2, 2, 2, 2)
        else:
            v.setContentsMargins(6, 3, 6, 3)
        v.setSpacing(3 if dense else 4)
        top = QHBoxLayout()
        top.setSpacing(8)
        nm = QLabel(title)
        nm.setObjectName("settingsParamLabel")
        nm.setMinimumWidth(0)
        nm.setWordWrap(True)
        nm.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        fs_t = self.parent._current_font["title"]
        # 文字色不烘焙：由 _apply_style 的 settingsParamLabel 规则按当前主题给出
        # （控件级色 + 按值替换会在「同值跨键」主题对下错向，见 refresh_theme_colors）
        nm.setStyleSheet(f"font-weight:600;font-size:{px_to_pt(fs_t)}pt;")
        top.addWidget(nm)
        top.addStretch(1)
        top.addWidget(control)
        v.addLayout(top)
        if desc:
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setTextInteractionFlags(Qt.TextSelectableByMouse)
            fs_d = self.parent._current_font["desc"]
            d.setStyleSheet(
                f"color:{Theme.TEXT_MUTED};font-size:{px_to_pt(fs_d)}pt;line-height:1.4;"
                f"padding-right:8px;")
            v.addWidget(d)
        return card

    def _section_card(self, title, body, desc, dense=False):
        """分区卡片：QGroupBox 原生标题框 + 主体控件（表格 / 按钮组等）+ 说明。

        所有分区统一使用 QGroupBox，标题为原生 ::title（位于边框线上），
        说明文字保留在内容区、可选中复制。objectName 固定为 "sectionCard"，
        供测试与外部查找；调用方可在返回后再 setObjectName 覆盖。
        dense=True 保持紧凑内边距（高度冻结页专用）。
        """
        group = QGroupBox(title)
        group.setObjectName("sectionCard")
        group.setMinimumWidth(0)
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v = QVBoxLayout(group)
        if dense:
            v.setContentsMargins(6, 1, 6, 2)
        else:
            v.setContentsMargins(10, 4, 10, 6)
        v.setSpacing(4 if dense else 6)
        if body is not None:
            v.addWidget(body)
        if desc:
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setTextInteractionFlags(Qt.TextSelectableByMouse)
            fs_d = self.parent._current_font["desc"]
            d.setStyleSheet(
                f"color:{Theme.TEXT_MUTED};font-size:{px_to_pt(fs_d)}pt;line-height:1.4;")
            v.addWidget(d)
        return group

    def _feature_block(self, title, switch, desc, body):
        """可折叠功能块：QGroupBox 标题 + 启用开关 + 参数区。

        用户勾选/取消「启用」时参数区同步展开/折叠（未启用时不显示子参数），
        保证每个功能只显示当前生效的参数，避免平铺时参数与开关归属不清。
        body 需为独立 QWidget；开关折叠状态由调用方负责连接业务刷新。
        """
        group = QGroupBox(title)
        group.setObjectName("sectionCard")
        group.setMinimumWidth(0)
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v = QVBoxLayout(group)
        v.setContentsMargins(10, 4, 10, 6)
        v.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(8)
        switch.setStyleSheet("font-weight:600;")
        header.addWidget(switch)
        if desc:
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setTextInteractionFlags(Qt.TextSelectableByMouse)
            fs_d = self.parent._current_font["desc"]
            d.setStyleSheet(
                f"color:{Theme.TEXT_MUTED};font-size:{px_to_pt(fs_d)}pt;line-height:1.6;")
            header.addWidget(d, 1)
        v.addLayout(header)
        body.setVisible(switch.isChecked())
        v.addWidget(body)
        switch.toggled.connect(lambda enabled: body.setVisible(bool(enabled)))
        return group

    def _row_widget(self, *widgets):
        """把若干控件拼成一行 QWidget。"""
        w = QWidget()
        w.setMinimumWidth(0)
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for item in widgets:
            h.addWidget(item)
        return w

    def _col_widget(self, *widgets):
        """把若干控件拼成一列 QWidget（按钮纵向排列）。"""
        w = QWidget()
        v = QVBoxLayout(w)
        w.setMinimumWidth(0)
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        for item in widgets:
            v.addWidget(item)
        return w

    def _make_btn(self, text, slot):
        b = QPushButton(text)
        b.clicked.connect(slot)
        return b

    def _confirm_apply(self, title, action):
        """执行应用动作。「应用」按钮本身即显式意图，不再二次确认；
        误改/误关由切页与关闭守卫（_confirm_leave_page）兜底。"""
        action()

    # ----------------------------------------------------- 显示 / 导航样式
    def _apply_style(self):
        """设置弹窗整体样式。

        层级：页面（深色底）→ 分区卡片（浅色细边框）→ 参数行（无边框留白）。
        单个参数用「无框参数行」而非一个个加边框的小卡片，避免满屏框线。
        """
        # 工业标准交互态（与 Theme.styled_button("primary") 同款幽灵式）：
        # 无状态底色填充；选中/强调 = 1px ACCENT 边框 + readable_text 补偿文字
        _primary_fg = Theme.readable_text(Theme.ACCENT, Theme.BG_CARD)
        _active_bg = Theme.lighten(Theme.ACCENT, 0.82)
        _active_fg = Theme.on_color_fg(_active_bg)
        if Theme.is_light_theme():
            _primary_hover_bd = Theme.darken(Theme.ACCENT, 0.25)
            _primary_pressed_bd = Theme.darken(Theme.ACCENT, 0.45)
        else:
            _primary_hover_bd = Theme.lighten(Theme.ACCENT, 0.3)
            _primary_pressed_bd = Theme.lighten(Theme.ACCENT, 0.5)
        self.setStyleSheet(f"""
            {Theme.group_box_qss()}
            /* 表格：单一主背景下的数据容器——透明底 + 细边框，表头仅底部细分隔线；
               选中态使用整行浅色底，单元格不绘制逐格边框 */
            QTableWidget {{
                background:transparent;
                color:{Theme.TEXT};
                border:1px solid {Theme.BORDER};
                border-radius:{Theme.RADIUS_SM}px;
                gridline-color:transparent;
                selection-background-color:{_active_bg};
                selection-color:{_active_fg};
            }}
            QTableWidget QHeaderView::section {{
                background:transparent;
                color:{Theme.TEXT_MUTED};
                border:none;
                border-bottom:1px solid {Theme.BORDER};
                padding:5px 8px;
                font-weight:600;
            }}
            QWidget#pageContent {{ background:{Theme.BG_PRIMARY}; }}
            QScrollArea#pageScroll {{ background:{Theme.BG_PRIMARY}; border:none; }}
            /* 单个参数行：无背景留白块，靠间距与文字颜色分区 */
            QWidget#paramCard {{
                background:transparent;
                border:1px solid transparent;
                border-radius:{Theme.RADIUS_SM}px;
            }}
            QWidget#paramCard:hover {{
                border-color:{Theme.BORDER};
            }}
            /* T1 教程/提示卡：卡片底 + 左侧强调条（非文字垂直占位 ≈7px，
               冻结页高度预算见整改计划 §4；颜色均为 refresh_theme_colors 键） */
            QLabel#settingsPageIntro {{
                color:{Theme.TEXT_MUTED};
                background:{Theme.BG_CARD};
                border:1px solid {Theme.BORDER};
                border-left:3px solid {Theme.ACCENT};
                border-radius:{Theme.RADIUS}px;
                padding:1px 10px 2px 10px;
            }}
            QLabel#settingsSectionHeader {{
                color:{Theme.readable_text(Theme.ACCENT, Theme.BG_PRIMARY)};
                background:transparent;
                font-weight:600;
                padding:8px 4px 3px 4px;
                border-bottom:1px solid {Theme.BORDER};
            }}
            QLabel#settingsParamLabel {{
                color:{Theme.TEXT};
                background:transparent;
                font-weight:600;
            }}
            /* 分区容器统一由 group_box_qss() 的 QGroupBox 规则控制
               （sectionCard / samplingOperationSection 均为 QGroupBox） */
            /* 主按钮：对齐 Theme.styled_button("primary") 幽灵式——
               transparent 底 + 1px ACCENT 边框 + readable_text 补偿文字，
               hover/pressed 仅换边框色（随主题明暗取方向） */
            QPushButton[buttonRole="primary"] {{
                background:{_active_bg};
                color:{_active_fg};
                border:1px solid {Theme.ACCENT};
                border-radius:{Theme.RADIUS}px;
                padding:6px 16px;
                font-weight:600;
            }}
            QPushButton[buttonRole="primary"]:hover {{
                background:{_active_bg};
                border-color:{_primary_hover_bd};
            }}
            QPushButton[buttonRole="primary"]:pressed {{
                background:{Theme.ACCENT};
                color:{Theme.on_color_fg(Theme.ACCENT)};
                border-color:{_primary_pressed_bd};
            }}
            /* 次按钮：同样幽灵式（工业标准无状态底色填充）——transparent 底
               + 1px 中性边框；hover/pressed 换强调边框 + 补偿文字 */
            QPushButton[buttonRole="secondary"] {{
                background:transparent;
                color:{Theme.TEXT};
                border:1px solid {Theme.BORDER};
                border-radius:{Theme.RADIUS}px;
                padding:6px 16px;
                font-weight:600;
            }}
            QPushButton[buttonRole="secondary"]:hover {{
                background:transparent;
                color:{_primary_fg};
                border-color:{Theme.ACCENT};
            }}
            QPushButton[buttonRole="secondary"]:pressed {{
                background:transparent;
                color:{_primary_fg};
                border-color:{_primary_hover_bd};
            }}
            /* 底部全局操作栏：与内容区用细分隔线区分 */
            QWidget#settingsBottomBar {{
                background:{Theme.BG_CARD};
                border-top:1px solid {Theme.BORDER};
            }}
            /* 「应用保存」存在未应用修改时高亮：工业标准边框+文字式
               （无底色填充；比基态更粗一级的字重作强调） */
            QPushButton[buttonRole="primary"][needsApply="true"] {{
                background:{_active_bg};
                color:{_active_fg};
                border:1px solid {Theme.ACCENT};
                font-weight:700;
            }}
            QPushButton[buttonRole="primary"][needsApply="true"]:hover {{
                background:{_active_bg};
                border-color:{_primary_hover_bd};
            }}
            QPushButton[buttonRole="primary"]:disabled {{
                background:{Theme.BG_INPUT};
                color:{Theme.TEXT_MUTED};
            }}
        """)

    def _apply_nav_style(self):
        """导航栏样式直接设置在 nav 自身（避免通过父窗口传播被弹窗阻断）。

        遵循单一主背景与整项激活态：导航栏透明，仅靠右侧细分隔线
        与内容区区分；item 不绘制逐项边框，悬停使用浅色底，选中使用
        主题强调色浅色底和高对比文字。扁平导航下所有项均为叶子项，
        统一使用 item 基础样式。
        """
        fs_nav = self.parent._current_font["nav"]
        _active_bg = Theme.lighten(Theme.ACCENT, 0.82)
        _active_fg = Theme.on_color_fg(_active_bg)
        self.nav.setStyleSheet(f"""
            QTreeWidget#navBar {{
                background:transparent; border:none; border-right:1px solid {Theme.BORDER};
                font-size:{px_to_pt(fs_nav)}pt; outline:0; padding:8px 4px;
            }}
            QTreeWidget#navBar::item {{
                padding:8px 12px; margin:0 4px 2px 4px; border-radius:6px;
                border:none;
                background:transparent;
                color:{Theme.TEXT_MUTED};
            }}
            QTreeWidget#navBar::item:hover {{
                background:{Theme.BG_HOVER}; color:{Theme.TEXT};
            }}
            QTreeWidget#navBar::item:selected {{
                background:{_active_bg}; font-weight:600;
                color:{_active_fg};
            }}
            QTreeWidget#navBar::item:pressed {{
                background:{Theme.ACCENT}; color:{Theme.on_color_fg(Theme.ACCENT)};
            }}
            QTreeWidget#navBar::branch {{ background:transparent; }}
        """)

    def refresh_theme_colors(self, old_colors, new_colors) -> None:
        """替换设置页局部样式中的旧主题色，避免切换后残留另一套配色。

        两层防线（顺序 str.replace 的两级缺陷，均经 7×6 全主题对矩阵
        测试钉死）：
        1. 单遍令牌替换：re.sub 一次匹配全部旧色值，杜绝「前一步输出
           成为后一步输入」的级联（切石墨时 TEXT→#ffffff 先写入、
           SEL_TEXT(#ffffff→#1b1d21) 随后误改白色为近黑，深底深字）。
        2. 歧义值丢弃：同一旧色值在不同键下映射到不同新值时（如
           中灰 TEXT 与 SEL_TEXT 同为 #ffffff，目标各异），按值替换
           无法区分语义，一律弃替——此类样式交由 _apply_style/
           _apply_nav_style 重生成兜底；恒等映射（old==new）不入表。
        色值按长度降序入 alternation，防短值截断长值。
        """
        if not isinstance(old_colors, dict) or not isinstance(new_colors, dict):
            return
        keys = ("BG_PRIMARY", "BG_CARD", "BG_INPUT", "BORDER", "TEXT",
                "TEXT_MUTED", "ACCENT", "GREEN", "RED", "ORANGE",
                "BG_HOVER", "BG_PRESSED", "SCROLL_HANDLE",
                "SCROLL_HANDLE_HOVER", "SEL_TEXT", "INDICATOR_BORDER",
                "GRID")
        full = {}
        ambiguous = set()
        for key in keys:
            old = (old_colors.get(key) or "").lower()
            new = new_colors.get(key) or ""
            if not old or not new:
                continue
            if old in full and full[old].lower() != new.lower():
                ambiguous.add(old)
            else:
                full[old] = new
        mapping = {k: v for k, v in full.items()
                   if k not in ambiguous and k != v.lower()}
        if not mapping:
            self._apply_style()
            self._apply_nav_style()
            return
        pattern = re.compile(
            "|".join(re.escape(k) for k in
                     sorted(mapping, key=len, reverse=True)),
            re.IGNORECASE)

        def _sub(m):
            return mapping[m.group(0).lower()]

        for widget in [self] + self.findChildren(QWidget):
            style = widget.styleSheet()
            if not style:
                continue
            widget.setStyleSheet(pattern.sub(_sub, style))
        self._apply_style()
        self._apply_nav_style()

    def update_page_header(self, page_idx) -> None:
        """页头标题栏已移除，此方法保留为空操作以兼容导航切换调用。"""
        pass

    def set_page_status(self, text, level="info") -> None:
        """页面底部状态行反馈；应用类回调同时刷新当前页快照。"""
        try:
            self._snapshot_page(self.stack.currentIndex())
        except Exception:
            pass
        label = getattr(self, "_status_label", None)
        if label is None:
            return
        self._showing_dirty_hint = False
        # 状态色经 readable_text 对页面主背景补偿（底色行位于透明页面上）
        color = (Theme.readable_text(Theme.GREEN, Theme.BG_PRIMARY)
                 if level == "success"
                 else (Theme.readable_text(Theme.ORANGE, Theme.BG_PRIMARY)
                       if level == "warning"
                       else Theme.readable_text(Theme.TEXT_MUTED, Theme.BG_PRIMARY)))
        label.setStyleSheet(f"color:{color};font-weight:600;padding:2px 10px;")
        label.setText(text)
        self._status_timer.start(3000)

    # ----------------------------------------------------- 通道管理页
    def _build_channel_page(self):
        """通道管理：名称列表（命名来源）+ 配色方案（切换通道颜色）。"""
        page, lay = self._new_page_fill()

        # ===== 显示设置：界面主题 + 字体大小 =====
        self.cmb_theme = QComboBox()
        for key, label in Theme.THEME_NAMES.items():
            self.cmb_theme.addItem(label, key)
        idx_theme = self.cmb_theme.findData(Theme.active())
        self.cmb_theme.setCurrentIndex(max(idx_theme, 0))
        self.cmb_theme.currentIndexChanged.connect(self._on_theme_changed)

        # 两项显示设置并排，节省固定弹窗中的垂直空间。
        display_body = QWidget()
        display_layout = QHBoxLayout(display_body)
        display_layout.setContentsMargins(0, 0, 0, 0)
        display_layout.setSpacing(14)
        display_layout.addWidget(self._param_card(
            "界面主题", self._fix_input(self.cmb_theme), ""), 1)

        # 通道列表风格：卡表融合（表头+紧凑行）⇄ 经典卡片（旧 70px 大卡）。
        # 与主题同为通道管理页即时生效项（不注册 _page_field_specs）
        self.cmb_channel_view = QComboBox()
        self.cmb_channel_view.addItem("卡表融合（新）", "table")
        self.cmb_channel_view.addItem("经典卡片（旧）", "classic")
        idx_view = self.cmb_channel_view.findData(
            self.parent._channel_view_mode)
        self.cmb_channel_view.setCurrentIndex(max(idx_view, 0))
        self.cmb_channel_view.setToolTip(
            "经典卡片为旧版 70px 大卡片样式；卡表融合为表头+紧凑行样式")
        self.cmb_channel_view.currentIndexChanged.connect(
            self._on_channel_view_changed)
        display_layout.addWidget(self._param_card(
            "通道列表风格", self._fix_input(self.cmb_channel_view), ""), 1)
        display_card = self._section_card("显示设置", display_body, "")

        # 名称列表：所有可用名称的池子，左侧下拉选项来源于此
        # （通道命名统一在左侧面板卡片下拉完成，此处不提供单独编辑表，避免重叠）
        self.tbl_name_list = QTableWidget(0, 1)
        self.tbl_name_list.setHorizontalHeaderLabels(["可用名称列表"])
        nh = self.tbl_name_list.horizontalHeader()
        nh.setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_name_list.verticalHeader().setVisible(False)
        self.tbl_name_list.verticalHeader().setDefaultSectionSize(28)
        self.tbl_name_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tbl_name_list.setEditTriggers(QAbstractItemView.DoubleClicked |
                                           QAbstractItemView.SelectedClicked |
                                           QAbstractItemView.EditKeyPressed)
        # 编辑器限制名称最多 6 个字（与命名弹窗同一代理）
        self.tbl_name_list.setItemDelegateForColumn(
            0, NameListDelegate(self.tbl_name_list))
        self.tbl_name_list.itemChanged.connect(self.parent._on_name_list_changed)
        self.parent._populate_name_list_table(self)

        b_add_name = self._make_btn("➕ 添加名称", self.parent._add_name_to_list)
        b_del_name = self._make_btn("✕ 删除所选", self.parent._remove_name_from_list)
        name_list_widget = self._col_widget(
            self.tbl_name_list,
            self._row_widget(b_add_name, b_del_name))
        name_list_card = self._section_card("名称列表", name_list_widget, "")
        name_list_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 配色方案（勾选表格，打勾即应用）
        self.tbl_scheme = QTableWidget(0, 2)
        self.tbl_scheme.setHorizontalHeaderLabels(["启用", "方案名称"])
        sch = self.tbl_scheme.horizontalHeader()
        sch.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        sch.setSectionResizeMode(1, QHeaderView.Stretch)
        self.tbl_scheme.verticalHeader().setVisible(False)
        self.tbl_scheme.verticalHeader().setDefaultSectionSize(34)
        self.tbl_scheme.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_scheme.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl_scheme.setShowGrid(False)
        self.tbl_scheme.setFrameShape(QFrame.NoFrame)
        fs_b = self.parent._current_font["base"]
        fs_h = self.parent._current_font["header"]
        self.tbl_scheme.setStyleSheet(
            f"QTableWidget {{ font-size:{px_to_pt(fs_b)}pt; gridline-color:{Theme.BG_INPUT};"
            f" background:transparent; border:1px solid {Theme.BORDER};"
            f" border-radius:3px; }}"
            "QTableWidget::item { padding:0 10px; border:1px solid transparent;"
            f" border-bottom:1px solid {Theme.BG_INPUT}; background:transparent; }}"
            f"QTableWidget::item:hover {{ background:transparent;"
            f" border:1px solid {Theme.SCROLL_HANDLE_HOVER}; }}"
            f"QHeaderView::section {{ background:transparent; color:{Theme.TEXT_MUTED};"
            f" padding:6px 10px; border:none; border-bottom:1px solid {Theme.BORDER};"
            f" font-weight:600; font-size:{px_to_pt(fs_h)}pt; }}"
            f"QTableWidget::item:disabled {{ color:{Theme.TEXT_MUTED}; }}")
        self.tbl_scheme.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 列 0 使用 ToggleSwitch 方格勾选框驱动（见 _populate_scheme_table / _on_scheme_toggled）
        self.parent._populate_scheme_table(self)
        scheme_card = self._section_card("配色方案", self.tbl_scheme, "")
        scheme_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 布局：左栏（显示设置在上、配色方案在下）与右栏（名称列表占整列）并排；
        # 名称列表与显示设置水平对齐，配色方案位于显示设置下方。
        left_col = QWidget()
        left_col.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_lay = QVBoxLayout(left_col)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)
        left_lay.addWidget(display_card, 0)
        left_lay.addWidget(scheme_card, 1)

        channel_row = QWidget()
        channel_row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        row_lay = QHBoxLayout(channel_row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(14)
        row_lay.addWidget(left_col, 1)
        # 名称列表已移至左侧面板「自定义各通道名称」弹窗统一管理，此处隐藏避免重复
        # （保留控件引用供后续彻底清理，当前仅隐藏）
        row_lay.addWidget(name_list_card, 0)  # stretch=0 不占空间
        name_list_card.setVisible(False)
        lay.addWidget(channel_row, 1)
        # body（表格）在卡片内部占满垂直空间：
        # _section_card 布局 index 0=body、1=desc，因此 stretch 应给 index 0。
        name_list_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        name_list_widget.layout().setStretch(0, 1)
        name_list_card.layout().setStretch(0, 1)
        scheme_card.layout().setStretch(0, 1)

        self.stack.addWidget(page)

    # ----------------------------------------------------- 采集设置页
    def _build_acq_page(self):
        """采集设置：波特率 / 采样间隔 / 自动保存间隔 / 采集组数。"""
        page, lay = self._new_page_scroll()
        self._page_intro(lay, "在线采集的常用参数集中在此配置：波特率、采样间隔，以及采集的通道组数。"
                              "「采集组数」为 0 时自动读取仪器配置的组数（设备联机识别）；"
                              "也可以手动指定只读取前 N 组，避免显示未启用的通道。")

        # 波特率
        self.cmb_acq_baud = QComboBox()
        self.cmb_acq_baud.addItems(["2400", "4800", "9600", "19200"])
        idx_b = self.cmb_acq_baud.findText(str(self.parent._acq_baud))
        self.cmb_acq_baud.setCurrentIndex(max(idx_b, 0))
        self.cmb_acq_baud.currentIndexChanged.connect(
            lambda _i: self.parent._set_acq_baud(int(self.cmb_acq_baud.currentText())))
        baud_card = self._param_card(
            "波特率", self._fix_input(self.cmb_acq_baud),
            "串口通信波特率，必须与设备一致（默认 2400），开始采集前设置。")

        # 采样间隔
        self.cmb_acq_interval = QComboBox()
        self.cmb_acq_interval.addItems(["500", "1000", "2000", "5000"])
        idx_i = self.cmb_acq_interval.findText(str(self.parent._acq_interval_ms))
        self.cmb_acq_interval.setCurrentIndex(max(idx_i, 0))
        self.cmb_acq_interval.currentIndexChanged.connect(
            lambda _i: self.parent._set_acq_interval(int(self.cmb_acq_interval.currentText())))
        interval_card = self._param_card(
            "采样间隔(ms)", self._fix_input(self.cmb_acq_interval),
            "设备轮询采集的时间间隔，单位毫秒。数值越小刷新越密，默认 1000ms。")

        self.cmb_acq_protocol = QComboBox()
        self.cmb_acq_protocol.addItem("自动检测（推荐）", "auto")
        self.cmb_acq_protocol.addItem("新版协议", "new")
        self.cmb_acq_protocol.addItem("旧版协议", "old")
        protocol_index = self.cmb_acq_protocol.findData(self.parent._acq_protocol)
        self.cmb_acq_protocol.setCurrentIndex(max(protocol_index, 0))
        self.cmb_acq_protocol.currentIndexChanged.connect(
            lambda _i: self.parent._set_acq_protocol(
                self.cmb_acq_protocol.currentData()))
        protocol_card = self._param_card(
            "协议版本", self._fix_input(self.cmb_acq_protocol),
            "自动检测：连接时先试新协议(0x11)，失败回退旧协议(0x01)，无需知道设备型号。"
            "新/旧版为手动固定。旧版协议支持写入通道，不能读取通道配置。")

        self.cmb_acq_groups = QComboBox()
        self.cmb_acq_groups.addItem("自动（读取仪器组数）", 0)
        for g in range(1, 9):
            self.cmb_acq_groups.addItem(f"{g} 组（{g * 8} 通道）", g)
        gi = self.cmb_acq_groups.findData(self.parent._acq_max_groups)
        self.cmb_acq_groups.setCurrentIndex(max(gi, 0))
        self.cmb_acq_groups.currentIndexChanged.connect(
            lambda _i: self.parent._set_acq_max_groups(self.cmb_acq_groups.currentData()))
        groups_card = self._param_card(
            "采集组数", self._fix_input(self.cmb_acq_groups),
            "0=自动：设备联机识别仪器配置的组数（一组 8 个通道）。指定 1~8 时只启用 / 读取前 N 组，"
            "未启用组的通道不显示，通道列表不会拥挤；需要全部通道时选「自动」或最大组数，"
            "列表超高会自动出现垂直滚动条。")

        core_section = QWidget()
        core_section.setObjectName("acquisitionCoreSection")
        core_section.setMinimumWidth(0)
        core_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        core_layout = QVBoxLayout(core_section)
        core_layout.setContentsMargins(0, 0, 0, 0)
        self._compact_grid(
            core_layout, [baud_card, interval_card, protocol_card, groups_card],
            "acqCompactGrid")
        lay.addWidget(core_section)

        # 采样通道读写
        self._build_sampling_channel_card(lay)

        lay.addStretch(1)
        self.stack.addWidget(page)

    def _build_sampling_channel_card(self, lay):
        """构建组为列、通道为行的采样矩阵。"""
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        self.tbl_sampling_channels = QGridLayout()
        self.tbl_sampling_channels.setContentsMargins(0, 0, 0, 0)
        self.tbl_sampling_channels.setHorizontalSpacing(10)
        self.tbl_sampling_channels.setVerticalSpacing(3)
        body_lay.addLayout(self.tbl_sampling_channels)

        buttons = QHBoxLayout()
        self.btn_read_sampling = QPushButton("从仪器读取采样通道")
        self.btn_write_sampling = QPushButton("采样通道写入仪器")
        self.btn_read_sampling.setProperty("buttonRole", "secondary")
        self.btn_write_sampling.setProperty("buttonRole", "primary")
        self.btn_read_sampling.setEnabled(self.parent._acq_protocol == "new")
        self.btn_read_sampling.setToolTip(
            "新版协议支持读取；旧版协议没有读取通道配置指令。")
        self.btn_read_sampling.clicked.connect(self.parent._read_sampling_channels)
        self.btn_write_sampling.clicked.connect(self.parent._write_sampling_channels)
        buttons.addStretch(1)
        for btn in (self.btn_read_sampling, self.btn_write_sampling):
            buttons.addWidget(btn)

        # 状态行在上、操作按钮右下对齐：读写动作收尾于卡底（整改计划 F-10）
        self.lbl_sampling_status = QLabel("尚未读取仪器通道状态")
        self.lbl_sampling_status.setStyleSheet(f"color:{Theme.TEXT_MUTED};")
        body_lay.addWidget(self.lbl_sampling_status)
        body_lay.addLayout(buttons)
        sampling_card = self._section_card(
            "采样通道设置", body,
            "勾选状态表示仪器将参与采样的通道。写入后会自动回读确认；采集运行中不能操作。")
        sampling_card.setObjectName("samplingOperationSection")
        lay.addWidget(sampling_card)
        self._set_sampling_masks(list(self.parent._acq_channel_masks))
        for group in range(8):
            self._set_sampling_group_enabled(
                group, bool(self.parent._acq_group_switch & (1 << group)))

    def _set_sampling_masks(self, masks):
        """按组掩码重建矩阵，并保留组开关与通道开关的联动。"""
        while self.tbl_sampling_channels.count():
            item = self.tbl_sampling_channels.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._sampling_checks = []
        self._sampling_group_checks = []
        # 页面固定显示 1~8 组；设备未配置的组由组开关关闭并置灰。
        group_count = 8
        masks = list(masks[:8]) + [0] * (8 - len(masks))
        group_checks = [[] for _ in range(group_count)]
        # 第一行：组开关；第一列：通道名称；交叉位置：通道开关。
        self.tbl_sampling_channels.addWidget(QLabel(""), 0, 0)
        group_names = ["第一组", "第二组", "第三组", "第四组",
                       "第五组", "第六组", "第七组", "第八组"]
        for group in range(group_count):
            title = QCheckBox(group_names[group])
            title.setObjectName("samplingGroupSwitch")
            title.stateChanged.connect(
                lambda _state, idx=group: self._on_sampling_group_changed(idx))
            title.setStyleSheet("QCheckBox { font-weight:600; }")
            self.tbl_sampling_channels.addWidget(title, 0, group + 1,
                                                 alignment=Qt.AlignCenter)
            self._sampling_group_checks.append(title)

        for ch in range(8):
            label = QLabel(f"通道{ch + 1}")
            label.setObjectName("samplingChannelLabel")
            self.tbl_sampling_channels.addWidget(label, ch + 1, 0)
            for group in range(group_count):
                check = QCheckBox()
                check.setText("")
                check.setToolTip(f"第{group + 1}组 / 通道{ch + 1}")
                check.setObjectName("samplingChannelCheck")
                mask = int(masks[group]) if group < len(masks) else 0
                check.blockSignals(True)
                check.setChecked(bool(mask & (1 << ch)))
                check.blockSignals(False)
                check.toggled.connect(self._save_sampling_config)
                self.tbl_sampling_channels.addWidget(
                    check, ch + 1, group + 1, alignment=Qt.AlignCenter)
                group_checks[group].append(check)

        self._sampling_checks = group_checks

        self.tbl_sampling_channels.setColumnStretch(0, 0)
        for group in range(group_count):
            self.tbl_sampling_channels.setColumnStretch(group + 1, 1)

        self._sampling_group_count = group_count
        for group in range(group_count):
            self._set_sampling_group_enabled(group, True)

    def _sampling_masks(self):
        """按组返回通道掩码；未启用的组返回 0。"""
        masks = []
        for group, checks in enumerate(self._sampling_checks):
            if not self._sampling_group_checks[group].isChecked():
                masks.append(0)
                continue
            masks.append(sum((1 << ch) for ch, check in enumerate(checks)
                             if check.isChecked()))
        return masks

    def _on_sampling_group_changed(self, group):
        """组开关关闭时禁用该组 8 个通道，但不清除原勾选状态。"""
        if group >= len(self._sampling_group_checks):
            return
        self._set_sampling_group_enabled(
            group, self._sampling_group_checks[group].isChecked())
        self._save_sampling_config()

    def _set_sampling_group_enabled(self, group, enabled):
        """设置组开关，并同步该组通道的可用状态。"""
        if group >= len(self._sampling_group_checks):
            return
        group_check = self._sampling_group_checks[group]
        group_check.blockSignals(True)
        group_check.setChecked(bool(enabled))
        group_check.blockSignals(False)
        for check in self._sampling_checks[group]:
            check.setEnabled(bool(enabled))

    def _save_sampling_config(self, *_args):
        """组或通道状态变化后立即保存到主窗口的采集配置。"""
        if hasattr(self.parent, "_save_sampling_config"):
            self.parent._save_sampling_config(self)

    def update_sampling_channels(self, masks, message, group_switch=None) -> None:
        """按设备返回的组掩码刷新采样矩阵并显示提示信息。"""
        self._set_sampling_masks(masks)
        if group_switch is not None:
            for group, check in enumerate(self._sampling_group_checks):
                self._set_sampling_group_enabled(group, bool(group_switch & (1 << group)))
        self._save_sampling_config()
        self.lbl_sampling_status.setText(message)

    def sampling_masks(self) -> list:
        """按组返回通道掩码列表。"""
        return self._sampling_masks()

    def sampling_group_switch(self) -> int:
        """按组开关勾选状态汇总为位掩码整数。"""
        return sum((1 << group) for group, check in
                   enumerate(self._sampling_group_checks) if check.isChecked())

    # ----------------------------------------------------- 采集时间页
    def _build_time_page(self):
        """采集时间：按功能块组织，未启用的功能折叠隐藏，参数归属一目了然。

        功能块（2026-08-22 重采样移出设置页后仅剩一个）：
          1. 重标定时间轴 —— 以「起始时刻 + 行号 × 采集间隔」覆盖原始时间列。
        功能开关勾选/取消立即生效并刷新画布；数值参数由底部全局「应用保存」提交。
        """
        page, lay = self._new_page_scroll()
        self._page_intro(lay, "校正每条曲线的时间轴，使横轴刻度符合真实采集时刻。"
                              "下方功能独立启用，未启用时自动折叠隐藏；"
                              "起始时刻、采集间隔等数值修改后经底部「应用保存」提交。"
                              "重采样等数据编辑请使用工具栏「编辑」按钮。")

        # ── 功能块 1：重标定时间轴 ──
        self.chk_relabel = ToggleSwitch("启用")
        self.chk_relabel.setObjectName("relabelFeatureSwitch")
        self.ed_start = QTimeEdit()
        self.ed_start.setDisplayFormat("HH:mm:ss")
        self.ed_start.setTime(QTime(0, 0, 0))
        self.ed_start.setButtonSymbols(QAbstractSpinBox.NoButtons)
        start_card = self._param_card(
            "起始时刻", self._fix_input(self.ed_start),
            "重标定后时间轴的起点，默认 00:00:00。直接键入时、分、秒数值即可。")
        self.sp_interval = QDoubleSpinBox()
        self.sp_interval.setRange(0.1, 3600); self.sp_interval.setValue(10.0)
        self.sp_interval.setDecimals(1)
        interval_card = self._param_card(
            "采集间隔（秒）", self._fix_input(self.sp_interval),
            "重标定后相邻两个采样点之间的固定时间间隔（起点 + 行号 × 间隔）。")
        relabel_body = QWidget()
        relabel_body.setObjectName("relabelFeatureBody")
        relabel_body_lay = QVBoxLayout(relabel_body)
        relabel_body_lay.setContentsMargins(0, 0, 0, 0)
        relabel_body_lay.setSpacing(6)
        self._compact_grid(
            relabel_body_lay, [start_card, interval_card], "timeCompactGrid")
        lay.addWidget(self._feature_block(
            "重标定时间轴", self.chk_relabel,
            "勾选后以「起始时刻 + 行号 × 采集间隔」重新生成时间轴，覆盖原始时间列；"
            "适合原始时间列缺失或时间不对的场景。",
            relabel_body))
        self.chk_relabel.toggled.connect(self.parent.apply_and_refresh)

        # （2026-08-22）重采样已移出设置页：改为工具栏「🛠 编辑」按会话编辑

        # ── 页内操作：重置为初始（应用由底部全局「应用保存」统一提交）──
        b_rs = QPushButton("重置为初始")
        b_rs.setObjectName("resetTimePageButton")
        b_rs.setProperty("buttonRole", "secondary")
        b_rs.clicked.connect(
            lambda: self._confirm_apply("重置参数为初始值", self.parent.reset_params))
        reset_row = QWidget()
        reset_row.setObjectName("timeResetRow")
        reset_lay = QHBoxLayout(reset_row)
        reset_lay.setContentsMargins(0, 0, 0, 0)
        reset_lay.addStretch(1)
        reset_lay.addWidget(b_rs)
        lay.addWidget(reset_row)
        lay.addStretch(1)
        self.stack.addWidget(page)

    # ----------------------------------------------------- 数据处理页（异常剔除 + 整体平滑）
    def _build_anomaly_page(self):
        page, lay = self._new_page_scroll()
        self._page_intro(lay, "异常检测与填充对所有会话生效，修改后立即重新计算当前数据并刷新图表。"
                             "异常检测的三种方法通过下拉菜单选择，选中方法的参数自动展开、其余折叠；"
                             "阈值、窗口等数值修改后经底部「应用保存」提交。"
                             "平滑仅对录制中的实时曲线生效；已完成的数据请在工具栏「🛠 编辑」中按会话设置。")

        # ---- 实时采集平滑（仅录制中的实时会话生效；历史/已结束数据用「🛠 编辑」弹窗按会话设置）----
        self.chk_smooth = ToggleSwitch("启用实时采集平滑"); self.chk_smooth.setChecked(False)
        self.sp_smooth = QSpinBox(); self.sp_smooth.setRange(1, 99); self.sp_smooth.setValue(5)
        self._smooth_window_card = self._param_card(
            "平滑窗口（点）", self._fix_input(self.sp_smooth),
            "参与平均的相邻点数。窗口越大曲线越平滑，但快速变化和峰值可能被削弱。"
            "仅对录制中的实时曲线生效；已完成的数据请在工具栏「🛠 编辑」中按会话设置。")
        smooth_section = QWidget()
        smooth_section.setObjectName("smoothingSection")
        smooth_section.setMinimumWidth(0)
        smooth_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        smooth_lay = QVBoxLayout(smooth_section)
        smooth_lay.setContentsMargins(0, 0, 0, 0)
        smooth_lay.setSpacing(6)
        smooth_lay.addWidget(self._method_block(
            self.chk_smooth, self._smooth_window_card, "smoothCompactGrid"))
        lay.addWidget(smooth_section)

        # ---- 异常检测方法：下拉菜单选择，选中方法展开参数 ----
        self.cmb_anomaly_method = QComboBox()
        self.cmb_anomaly_method.addItems(["温差法", "标准分离群法", "变化率法"])
        self.cmb_anomaly_method.setCurrentIndex(1)  # 默认标准分离群法
        method_select_card = self._param_card(
            "异常检测方法", self._fix_input(self.cmb_anomaly_method),
            "三种检测方法一次只能启用一种，选择后下方自动展开对应参数、折叠其余参数。")

        self.sp_diff = QDoubleSpinBox(); self.sp_diff.setRange(0.1, 50)
        self.sp_diff.setValue(1.5); self.sp_diff.setDecimals(2)
        self.sp_zwin = QSpinBox(); self.sp_zwin.setRange(3, 999); self.sp_zwin.setValue(11)
        self.sp_zsig = QDoubleSpinBox(); self.sp_zsig.setRange(0.5, 10)
        self.sp_zsig.setValue(3.0); self.sp_zsig.setDecimals(1)
        self.sp_slope = QDoubleSpinBox(); self.sp_slope.setRange(0.001, 100)
        self.sp_slope.setValue(0.15); self.sp_slope.setDecimals(3)

        self._diff_params = self._param_card(
            "相邻温差阈值（℃）", self._fix_input(self.sp_diff),
            "相邻两点温度突变超过该值即判为异常。例如设为 1.5℃，可剔除温度骤降 / 骤升超过 1.5℃ 的尖点。"
            "值越小越敏感。")
        self._z_params = self._col_widget(
            self._param_card("窗口点数", self._fix_input(self.sp_zwin), "滑动窗口点数。"),
            self._param_card("倍数标准差", self._fix_input(self.sp_zsig),
                             "偏离均值超过该倍数时判为异常。"))
        self._slope_params = self._param_card(
            "变化率阈值（℃/秒）", self._fix_input(self.sp_slope),
            "单位时间变化率超过该值判为异常。")

        method_section = QWidget()
        method_section.setObjectName("anomalyDetectionSection")
        method_section.setMinimumWidth(0)
        method_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        method_lay = QVBoxLayout(method_section)
        method_lay.setContentsMargins(0, 0, 0, 0)
        method_lay.setSpacing(6)
        method_lay.addWidget(method_select_card)
        method_lay.addWidget(self._diff_params)
        method_lay.addWidget(self._z_params)
        method_lay.addWidget(self._slope_params)
        lay.addWidget(method_section)

        # 填充处理（对所有检测方法生效，始终可见）
        self.sp_fill = QSpinBox(); self.sp_fill.setRange(1, 20); self.sp_fill.setValue(3)
        fill_card = self._param_card(
            "邻域均值填充点数", self._fix_input(self.sp_fill),
            "异常点用邻域有效值平均估算。")
        fill_section = self._section_card(
            "填充处理", fill_card, "对检测到的异常点统一填充，作用于当前选中的检测方法。")
        fill_section.setObjectName("anomalyFillSection")
        lay.addWidget(fill_section)

        # 只有处理开关即时生效；阈值、窗口等数值统一由底部「应用保存」提交。
        self.cmb_anomaly_method.currentIndexChanged.connect(self._on_anomaly_method_changed)
        self.chk_smooth.toggled.connect(self._on_settings_smooth_toggled)
        # 初始化折叠状态（构造期 setChecked 早于信号连接，需显式执行一次）
        self._update_anomaly_visibility()
        self._smooth_window_card.setVisible(self.chk_smooth.isChecked())
        b_rs = QPushButton("重置为初始")
        b_rs.setObjectName("resetProcessingPageButton")
        b_rs.setProperty("buttonRole", "secondary")
        b_rs.clicked.connect(
            lambda: self._confirm_apply("重置参数为初始值", self.parent.reset_params))
        reset_row = QWidget()
        reset_row.setObjectName("processingResetRow")
        reset_lay = QHBoxLayout(reset_row)
        reset_lay.setContentsMargins(0, 0, 0, 0)
        reset_lay.addStretch(1)
        reset_lay.addWidget(b_rs)
        lay.addWidget(reset_row)
        lay.addStretch(1)
        self.stack.addWidget(page)

    def _method_block(self, check, params, object_name):
        """方法选择块：标题（开关）在上，参数容器在下；参数可折叠隐藏。"""
        block = QWidget()
        block.setObjectName(object_name)
        block.setMinimumWidth(0)
        block.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bl = QVBoxLayout(block)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)
        bl.addWidget(check)
        bl.addWidget(params)
        return block

    def _anomaly_method(self) -> str:
        """返回当前下拉选中的异常检测方法标识：'diff' / 'z' / 'slope'。"""
        return ("diff" if self.cmb_anomaly_method.currentIndex() == 0 else
                ("z" if self.cmb_anomaly_method.currentIndex() == 1 else "slope"))

    def _update_anomaly_visibility(self):
        """按下拉选择折叠 / 展开各方法的参数容器。"""
        idx = self.cmb_anomaly_method.currentIndex()
        self._diff_params.setVisible(idx == 0)
        self._z_params.setVisible(idx == 1)
        self._slope_params.setVisible(idx == 2)

    def _set_anomaly_method(self, method):
        """程序化选中检测方法（不触发 currentIndexChanged 信号）。"""
        idx = {"diff": 0, "z": 1, "slope": 2}.get(method, 1)
        self.cmb_anomaly_method.blockSignals(True)
        self.cmb_anomaly_method.setCurrentIndex(idx)
        self.cmb_anomaly_method.blockSignals(False)
        self._update_anomaly_visibility()

    def _on_anomaly_method_changed(self, _index=-1):
        """方法下拉切换：更新折叠状态并立即重算。"""
        self._update_anomaly_visibility()
        self.parent.apply_and_refresh()

    def _on_settings_smooth_toggled(self, enabled):
        """整体平滑开关：折叠 / 展开窗口参数，并同步父窗口（工具栏 + 重算）。"""
        self._smooth_window_card.setVisible(bool(enabled))
        self.parent._on_settings_smooth_toggled(enabled)

    # ----------------------------------------------------- 轴设置页
    def _build_axis_page(self):
        """轴设置：时间轴支持「智能判断 / 自定义」；温度轴统一智能模式，
        仅配置越界扩展系数（基础窗口在主界面左侧底部调节）。
        """
        page, lay = self._new_page_scroll()
        self._page_intro(lay, "统一规定所有曲线图（整体 / 前10·20·30分钟 / 单通道 / 组合图 / 导出图片）"
                             "的横轴（时间）范围与刻度，以及温度轴的越界扩展系数。"
                             "时间轴支持自定义范围；温度轴统一智能模式，基础窗口在主界面左侧底部调节。"
                             "设定后经底部「应用保存」生效，并自动保存、重启沿用。")

        # ── 时间轴模式 ──
        self.ax_time_auto_rb = QRadioButton("智能判断（根据数据自动）")
        self.ax_time_custom_rb = QRadioButton("自定义（手动设定）")
        self.ax_time_mode_group = QButtonGroup(self)
        self.ax_time_mode_group.addButton(self.ax_time_auto_rb, 0)
        self.ax_time_mode_group.addButton(self.ax_time_custom_rb, 1)
        if self.parent.ax_time_mode == "auto":
            self.ax_time_auto_rb.setChecked(True)
        else:
            self.ax_time_custom_rb.setChecked(True)
        mode_row_time = self._col_widget(self.ax_time_auto_rb, self.ax_time_custom_rb)
        time_mode_card = self._param_card(
            "模式", mode_row_time,
            "默认智能判断按数据自动设定；切到「自定义」后填写范围与间隔，其余参数自动折叠。")

        self.ax_time_min_sp = QDoubleSpinBox(); self.ax_time_min_sp.setRange(-1e6, 1e6)
        self.ax_time_min_sp.setDecimals(1); self.ax_time_min_sp.setValue(self.parent.ax_time_min)
        time_min_card = self._param_card(
            "最小值（分钟）", self._fix_input(self.ax_time_min_sp),
            "横轴显示范围下限。")

        self.ax_time_max_sp = QDoubleSpinBox(); self.ax_time_max_sp.setRange(-1e6, 1e6)
        self.ax_time_max_sp.setDecimals(1); self.ax_time_max_sp.setValue(self.parent.ax_time_max)
        time_max_card = self._param_card(
            "最大值（分钟）", self._fix_input(self.ax_time_max_sp),
            "横轴显示范围上限。")

        self.ax_time_step_sp = QDoubleSpinBox(); self.ax_time_step_sp.setRange(0.1, 1e6)
        self.ax_time_step_sp.setDecimals(1); self.ax_time_step_sp.setValue(self.parent.ax_time_step)
        time_step_card = self._param_card(
            "主刻度间隔（分钟）", self._fix_input(self.ax_time_step_sp),
            "每多少分钟一个主刻度。")

        time_params_body = QWidget()
        time_params_body.setObjectName("timeAxisParamsBody")
        time_params_lay = QVBoxLayout(time_params_body)
        time_params_lay.setContentsMargins(0, 0, 0, 0)
        time_params_lay.setSpacing(6)
        self._compact_grid(
            time_params_lay, [time_min_card, time_max_card, time_step_card],
            "timeAxisCompactGrid")
        time_params_body.setVisible(self.parent.ax_time_mode == "custom")
        self.ax_time_custom_rb.toggled.connect(time_params_body.setVisible)

        # ── 温度轴（统一智能模式，基础窗口在主界面左侧底部调节）──
        self.ax_temp_lo_factor_sp = QDoubleSpinBox()
        self.ax_temp_lo_factor_sp.setRange(0.1, 1.0)
        self.ax_temp_lo_factor_sp.setDecimals(2)
        self.ax_temp_lo_factor_sp.setValue(self.parent.ax_temp_lo_factor)
        temp_lo_factor_card = self._param_card(
            "下界扩展系数（×最低温度）", self._fix_input(self.ax_temp_lo_factor_sp),
            "温度低于基础窗口下限时，轴下界 = 最低温度 × 系数（须 ≤ 1，默认 0.90）。")

        self.ax_temp_hi_factor_sp = QDoubleSpinBox()
        self.ax_temp_hi_factor_sp.setRange(1.0, 10.0)
        self.ax_temp_hi_factor_sp.setDecimals(2)
        self.ax_temp_hi_factor_sp.setValue(self.parent.ax_temp_hi_factor)
        temp_hi_factor_card = self._param_card(
            "上界扩展系数（×最高温度）", self._fix_input(self.ax_temp_hi_factor_sp),
            "温度高于基础窗口上限时，轴上界 = 最高温度 × 系数（须 ≥ 1，默认 1.30）。")

        axis_grid = QWidget()
        axis_grid.setObjectName("axisCompactGrid")
        axis_layout = QHBoxLayout(axis_grid)
        axis_layout.setContentsMargins(0, 0, 0, 0)
        axis_layout.setSpacing(14)
        time_box = self._section_card(
            "时间轴",
            self._col_widget(time_mode_card, time_params_body),
            "横轴范围与刻度，单位：分钟。")
        time_box.setObjectName("axisSettingsCard")
        time_box.setProperty("axisName", "时间轴")
        time_box.setProperty("axisUnit", "分钟")
        temp_box = self._section_card(
            "温度轴（智能模式）",
            self._col_widget(temp_lo_factor_card, temp_hi_factor_card),
            "基础窗口在主界面左侧底部调节；越界时按扩展系数自动扩展，刻度自动生成。")
        temp_box.setObjectName("axisSettingsCard")
        temp_box.setProperty("axisName", "温度轴")
        temp_box.setProperty("axisUnit", "℃")
        time_section = QWidget()
        time_section.setObjectName("axisTimeSection")
        time_section_layout = QVBoxLayout(time_section)
        time_section_layout.setContentsMargins(0, 0, 0, 0)
        time_section_layout.addWidget(time_box)
        temp_section = QWidget()
        temp_section.setObjectName("axisTemperatureSection")
        temp_section_layout = QVBoxLayout(temp_section)
        temp_section_layout.setContentsMargins(0, 0, 0, 0)
        temp_section_layout.addWidget(temp_box)
        axis_layout.addWidget(time_section, 1)
        axis_layout.addWidget(temp_section, 1)
        lay.addWidget(axis_grid)

        # ── 整体趋势双区视图（实时会话历史-实时左右分区）──
        self.ax_dual_view_chk = ToggleSwitch("启用历史-实时双区视图")
        self.ax_dual_view_chk.setChecked(
            bool(getattr(self.parent, "ax_dual_view_enabled", False)))
        self._set_current_control_semantic(
            self.ax_dual_view_chk, self.ax_dual_view_chk.isChecked())
        self.ax_dual_view_chk.toggled.connect(
            lambda checked: self._set_current_control_semantic(
                self.ax_dual_view_chk, checked))
        dual_view_card = self._param_card(
            "历史-实时双区视图", self.ax_dual_view_chk,
            "实时采集中整体趋势页时长超过右窗宽度时，自动切换为"
            "「左全历史 + 右实时窗」双区视图。")

        self.ax_live_window_cmb = QComboBox()
        for seconds in LIVE_WINDOW_SEC_CHOICES:
            self.ax_live_window_cmb.addItem(f"{seconds} 秒", seconds)
        default_idx = self.ax_live_window_cmb.findData(
            LIVE_WINDOW_SEC_DEFAULT)
        current_idx = self.ax_live_window_cmb.findData(
            int(getattr(self.parent, "ax_live_window_sec",
                        LIVE_WINDOW_SEC_DEFAULT)))
        self.ax_live_window_cmb.setCurrentIndex(
            current_idx if current_idx >= 0 else default_idx)
        self._set_current_control_semantic(self.ax_live_window_cmb, True)
        live_window_card = self._param_card(
            "右区实时窗宽度", self._fix_input(self.ax_live_window_cmb),
            "双区视图右区显示最近 N 秒实时数据（窄窗下采集点逐秒跳动"
            "更清晰），左区显示全部历史；改动经「应用保存」生效。")

        dual_grid = QWidget()
        dual_grid.setObjectName("dualViewCompactGrid")
        dual_grid_lay = QVBoxLayout(dual_grid)
        dual_grid_lay.setContentsMargins(0, 0, 0, 0)
        dual_grid_lay.setSpacing(6)
        self._compact_grid(dual_grid_lay,
                           [dual_view_card, live_window_card],
                           "dualViewCompactGridInner")
        dual_box = self._section_card(
            "整体趋势双区视图", dual_grid,
            "仅作用于实时采集的整体趋势页；历史文件与手动浏览沿用单图视图。")
        lay.addWidget(dual_box)

        lay.addStretch(1)
        self.stack.addWidget(page)

    @staticmethod
    def _set_current_control_semantic(control, is_current):
        """让设置页已生效的选项使用统一的当前态视觉语义。"""
        control.setProperty(
            "semanticRole", "current" if bool(is_current) else "info")
        style = control.style()
        if style is not None:
            style.unpolish(control)
            style.polish(control)
        control.update()

    # ----------------------------------------------------- 组合图页
    def _build_overview_page(self):
        page, lay = self._new_page_scroll()
        self._page_intro(lay, "设定右侧画布「前N分钟」概览标签页的时间窗口。"
                             "前N分钟导出图的左侧通道统计栏可在本页开关；整体趋势图始终显示统计栏。"
                             "修改即时生效并自动保存，重启沿用。")

        self.ov_w1 = QDoubleSpinBox(); self.ov_w1.setRange(0.5, 1e6)
        self.ov_w1.setDecimals(1); self.ov_w1.setValue(self.parent.win_front[0])
        self.ov_w2 = QDoubleSpinBox(); self.ov_w2.setRange(0.5, 1e6)
        self.ov_w2.setDecimals(1); self.ov_w2.setValue(self.parent.win_front[1])
        self.ov_w3 = QDoubleSpinBox(); self.ov_w3.setRange(0.5, 1e6)
        self.ov_w3.setDecimals(1); self.ov_w3.setValue(self.parent.win_front[2])
        # 三张参数卡硬挤一行时长标题与控件互相挤压：改双列网格（整改计划 P-1）
        sec_front = QWidget()
        sfl = QVBoxLayout(sec_front)
        sfl.setContentsMargins(0, 0, 0, 0)
        self._compact_grid(sfl, [
            self._param_card("概览标签页 1 时长（分钟）",
                             self._fix_input(self.ov_w1), "第 1 个概览窗口。"),
            self._param_card("概览标签页 2 时长（分钟）",
                             self._fix_input(self.ov_w2), "第 2 个概览窗口。"),
            self._param_card("概览标签页 3 时长（分钟）",
                             self._fix_input(self.ov_w3), "第 3 个概览窗口。"),
        ], "overviewWindowGrid")
        overview_window_card = self._section_card(
            "概览标签页时间窗口", sec_front,
            "这三个数值决定右侧画布「前N分钟」三个概览标签页各自展示的时间长度，"
            "也用于「导出4张总览曲线图」。它们仅影响概览视图，不改变左侧常规温升统计。")
        overview_window_card.setObjectName("overviewWindowSection")
        lay.addWidget(overview_window_card)

        for w in (self.ov_w1, self.ov_w2, self.ov_w3):
            w.valueChanged.connect(self.parent._apply_overview_page)

        self.ov_show_window_stats = QCheckBox(
            "前 10 / 20 / 30 分钟图显示左侧通道统计栏")
        self.ov_show_window_stats.setObjectName("showWindowStatsCheck")
        self.ov_show_window_stats.setChecked(
            bool(getattr(self.parent, "show_window_stats", True)))
        stats_setting_card = self._section_card(
            "概览图统计栏", self._row_widget(self.ov_show_window_stats),
            "整体趋势图固定带统计栏；关闭后仅隐藏前 10、20、30 分钟导出图的统计栏。")
        stats_setting_card.setObjectName("overviewStatsSection")
        lay.addWidget(stats_setting_card)
        self.ov_show_window_stats.stateChanged.connect(
            self.parent._apply_overview_page)

        a4_section = QWidget()
        a4_section.setObjectName("a4ConfigurationSection")
        a4_section.setMinimumWidth(0)
        a4_section.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        a4_layout = QVBoxLayout(a4_section)
        a4_layout.setContentsMargins(0, 0, 0, 0)
        self._build_a4_controls(a4_layout)
        lay.addWidget(a4_section)
        lay.addStretch(1)
        self.stack.addWidget(page)

    # ----------------------------------------------------- 悬浮监控页（悬浮卡外观）
    def _build_live_monitor_page(self):
        from PyQt5.QtWidgets import QCheckBox, QComboBox, QPushButton
        page, lay = self._new_page_scroll()
        self._page_intro(lay, "调节桌面监控悬浮卡外观：整卡只有一种颜色——卡片底色"
                             "（边框、卡片体、顶部滑块条、趋势图背景融合为同一色），"
                             "带一个透明度（0 % 全透明 ~ 100 % 完全不透明，两端都能设到）；"
                             "趋势图折线/填充色默认跟随当前主题，可自定义并一键恢复。"
                             "修改即时生效并自动保存，重启沿用。")
        cfg = ConfigIO.load_live_monitor_config()
        self.lm_card_color = cfg["card_color"]
        # 趋势线色：空串=跟随主题强调色，非空=用户自定义色（优先于主题色）
        self.lm_line_color = cfg.get("line_color", "")
        # 本页为高度冻结页（整改计划 §2.5）：布局辅助一律 dense=True，
        # 外观 / 悬浮球与桌宠 / 全通道面板 / 生态舱 四分区按模块分型排布。
        self.lm_card_color_btn = QPushButton()
        self.lm_card_color_btn.setFixedSize(*self._SWATCH)
        # 透明度改用文本输入框：允许直接键入 100% / 0%，并对越界与非法输入给出
        # 提示，不再像 QSpinBox(30~95) 那样把 100 静默截断为 95、0 抬成 30。
        self.lm_card_alpha_in = PercentLineEdit(0.0, 100.0, self)
        self.lm_card_alpha_in.setValue(round(cfg["card_alpha"] * 100.0, 2))
        self.lm_card_alpha_hint = QLabel("")
        self.lm_card_alpha_hint.setObjectName("liveMonitorAlphaHint")
        self.lm_card_alpha_hint.setWordWrap(True)
        self.lm_card_alpha_hint.setMinimumWidth(0)
        alpha_wrap = QWidget()
        alpha_wrap.setMinimumWidth(0)
        alpha_lay = QVBoxLayout(alpha_wrap)
        alpha_lay.setContentsMargins(0, 0, 0, 0)
        alpha_lay.setSpacing(2)
        alpha_lay.addWidget(self._fix_input(self.lm_card_alpha_in))
        alpha_lay.addWidget(self.lm_card_alpha_hint)

        self.lm_window_sec_sp = QSpinBox()
        self.lm_window_sec_sp.setRange(5, 120)
        self.lm_window_sec_sp.setSuffix(" 秒")
        self.lm_window_sec_sp.setValue(int(cfg["window_sec"]))
        self.lm_ball_size_sp = QSpinBox()
        self.lm_ball_size_sp.setRange(40, 120)
        self.lm_ball_size_sp.setSuffix(" px")
        self.lm_ball_size_sp.setValue(int(cfg["ball_size"]))
        self.lm_ball_alpha_sp = QSpinBox()
        self.lm_ball_alpha_sp.setRange(40, 100)
        self.lm_ball_alpha_sp.setSuffix(" %")
        self.lm_ball_alpha_sp.setValue(int(round(cfg["ball_alpha"] * 100)))

        # —— 趋势线/填充色：取色按钮 + 色值输入 + 恢复默认 ——
        line_row = QWidget()
        line_row.setMinimumWidth(0)
        lr = QHBoxLayout(line_row)
        lr.setContentsMargins(0, 0, 0, 0)
        lr.setSpacing(6)
        self.lm_line_color_btn = QPushButton()
        self.lm_line_color_btn.setFixedSize(*self._SWATCH)
        self.lm_line_color_edit = QLineEdit()
        self.lm_line_color_edit.setPlaceholderText("跟随主题")
        self.lm_line_color_edit.setMaximumWidth(self._HEX_W)
        self.lm_line_color_edit.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.lm_line_reset_btn = QPushButton("恢复默认")
        self.lm_line_reset_btn.setMaximumWidth(self._RESET_W)
        self.lm_line_reset_btn.setToolTip("清除自定义趋势色，回到跟随当前主题")
        lr.addWidget(self.lm_line_color_btn)
        lr.addWidget(self.lm_line_color_edit, 1)
        lr.addWidget(self.lm_line_reset_btn)

        line_card = self._param_card("趋势线/填充色", line_row, "")
        line_card.setToolTip(
            "留空=跟随当前主题强调色（切换主题自动同步）；"
            "填写 #rrggbb 或点击取色即自定义，自定义色优先级最高。")
        style_body = QWidget()
        style_lay = QVBoxLayout(style_body)
        style_lay.setContentsMargins(0, 0, 0, 0)
        self._compact_grid(style_lay, [
            self._param_card("卡片底色", self.lm_card_color_btn,
                             "整卡唯一颜色，点击取色。"),
            self._param_card("卡片透明度", alpha_wrap,
                             "0 % 全透明，100 % 完全不透明；可输入「100%」或「60」。"
                             "越界或非法会给出提示并自动修正。"),
            line_card,
            self._param_card("时间窗口", self._fix_input(self.lm_window_sec_sp),
                             "曲线显示最近多少秒；与弹窗顶部滑块同步。"),
        ], "liveMonitorStyleGrid", dense=True)
        ball_body = QWidget()
        ball_lay = QVBoxLayout(ball_body)
        ball_lay.setContentsMargins(0, 0, 0, 0)
        self._compact_grid(ball_lay, [
            self._param_card("悬浮球大小", self._fix_input(self.lm_ball_size_sp),
                             "悬浮球直径像素。"),
            self._param_card("悬浮球透明度", self._fix_input(self.lm_ball_alpha_sp),
                             "数值越大球越不透明。"),
        ], "liveMonitorBallGrid", dense=True)

        # —— 悬浮球与桌宠：角吸附偏好 + 形象切换 + 找回入口（原位构建，
        #    不再事后 insertWidget 挤入；整改计划 F-05）——
        pos_row = QWidget()
        pr = QHBoxLayout(pos_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(6)
        self.lm_corner_cmb = QComboBox()
        self.lm_corner_cmb.addItem("右下角", "bottom_right")
        self.lm_corner_cmb.addItem("屏幕居中", "center")
        self.lm_corner_cmb.addItem("右上角", "top_right")
        self.lm_corner_cmb.addItem("左上角", "top_left")
        corner_idx = self.lm_corner_cmb.findData(
            cfg.get("ball_corner", "bottom_right"))
        if corner_idx >= 0:
            self.lm_corner_cmb.setCurrentIndex(corner_idx)
        self.lm_style_cmb = QComboBox()
        self.lm_style_cmb.addItem("🦖 测温恐龙", "dino")
        self.lm_style_cmb.addItem("🚀 玻璃生态舱", "cabin")
        style_idx = self.lm_style_cmb.findData(cfg.get("pet_style", "dino"))
        self.lm_style_cmb.setCurrentIndex(max(0, style_idx))
        self.lm_style_cmb.setToolTip(
            "悬浮球桌宠形象：生态舱自带均衡器液柱、逼近报警线呼吸、超温喷火"
            "与恢复庆祝动画；液柱建议球径 ≥ 88px 看完整效果。切换立即生效")
        self.lm_style_cmb.currentIndexChanged.connect(self._on_lm_value_changed)
        self.lm_style_cmb.currentIndexChanged.connect(
            lambda _i: self._lm_sync_cabin_visible())
        self.lm_restore_btn = QPushButton("恢复默认位置")
        self.lm_restore_btn.setToolTip(
            "悬浮球回到默认吸附位（屏幕右下角）并转为吸附状态")
        pr.addWidget(self.lm_corner_cmb)
        pr.addWidget(self.lm_style_cmb)
        pr.addWidget(self.lm_restore_btn)
        pr.addStretch(1)
        pos_card = self._param_card("悬浮球位置", pos_row, "")
        pos_card.setToolTip(
            "选择后悬浮球立即吸附到该位置并记住偏好（屏幕居中=球心对正屏幕工作区正中）；"
            "拖动球可在整个桌面自由放置（松手落在位置吸附区内自动吸附），球面始终不会"
            "越出屏幕工作区，防止被拖到屏幕外疏漏。")
        ball_lay.addWidget(pos_card)

        # —— 悬浮球球心最高温直显（实时采集时）——
        temp_row = QWidget()
        tr = QHBoxLayout(temp_row)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(6)
        self.lm_ball_show_cb = QCheckBox("球心显示最高温度")
        self.lm_ball_show_cb.setChecked(bool(cfg.get("ball_show_max_temp", True)))
        self.lm_ball_warn_pct_sp = QSpinBox()
        self.lm_ball_warn_pct_sp.setRange(0, 100)
        self.lm_ball_warn_pct_sp.setSuffix(" %")
        self.lm_ball_warn_pct_sp.setValue(int(cfg.get("ball_temp_warn_pct", 90)))
        self.lm_ball_warn_pct_sp.setToolTip("0 表示不启用「接近上限」琥珀警示档")
        tr.addWidget(self.lm_ball_show_cb)
        tr.addWidget(self.lm_ball_warn_pct_sp)
        tr.addWidget(QLabel("接近上限即警示"))
        tr.addStretch(1)
        temp_card = self._param_card("球心最高温度", temp_row, "")
        temp_card.setToolTip(
            "实时采集时在球心以数字显示当前所有可见通道的最高温度：达到上限变红、"
            "达到阈值百分比变琥珀（上限默认跟随报警设置，重启沿用）。")
        ball_lay.addWidget(temp_card)

        # —— 全通道悬浮面板（独立第三块悬浮显示，与球/趋势弹窗互不依附）——
        panel_row = QWidget()
        pnl = QHBoxLayout(panel_row)
        pnl.setContentsMargins(0, 0, 0, 0)
        pnl.setSpacing(6)
        self.lm_panel_enabled_cb = QCheckBox("实时采集时显示全通道面板")
        self.lm_panel_enabled_cb.setChecked(
            bool(cfg.get("panel_enabled", False)))
        self.lm_panel_alpha_sp = QSpinBox()
        self.lm_panel_alpha_sp.setRange(40, 100)
        self.lm_panel_alpha_sp.setSuffix(" %")
        self.lm_panel_alpha_sp.setValue(
            int(round(float(cfg.get("panel_alpha", 0.85)) * 100)))
        self.lm_panel_alpha_sp.setToolTip("数值越大面板越不透明")
        self.lm_panel_theme_cmb = QComboBox()
        self.lm_panel_theme_cmb.addItem("浅色卡", "light")
        self.lm_panel_theme_cmb.addItem("深色卡", "dark")
        theme_idx = self.lm_panel_theme_cmb.findData(
            cfg.get("panel_theme", "light"))
        if theme_idx >= 0:
            self.lm_panel_theme_cmb.setCurrentIndex(theme_idx)
        self.lm_panel_theme_cmb.setToolTip(
            "浅色卡=冷灰白底墨色字；深色卡=工业深灰底柔白字（与场景主题无关）")
        pnl.addWidget(self.lm_panel_enabled_cb)
        pnl.addWidget(self.lm_panel_theme_cmb)
        pnl.addWidget(self.lm_panel_alpha_sp)
        pnl.addWidget(QLabel("面板不透明度"))
        pnl.addStretch(1)
        panel_row.setToolTip(
            "实时采集时在桌面显示独立面板，逐通道一行「名称+温度+进度条」（进度条"
            "按报警上限表达余量，超温红/接近橙，无效通道显示 --）；卡片皮肤深浅"
            "两套手动选、不随场景主题变化；可拖拽、位置自动记忆，停止采集或查看"
            "历史文件时自动隐藏。")

        # —— 生态舱参数（形象切换下拉已原位并入悬浮球位置行；dino 态整卡
        #    隐藏不占高，保「无纵向滚动条」验收）——
        self.lm_cabin_fluct_sp = QDoubleSpinBox()
        self.lm_cabin_fluct_sp.setRange(0.0, 20.0)
        self.lm_cabin_fluct_sp.setSingleStep(0.5)
        self.lm_cabin_fluct_sp.setSuffix(" ℃/s")
        self.lm_cabin_fluct_sp.setValue(float(cfg.get("cabin_fluct_rate", 2.0)))
        self.lm_cabin_fluct_sp.setToolTip(
            "单通道温度变化速率超过该值即视为「快速波动」（生态舱惊讶摇摆、"
            "液面加速）；0 = 关闭波动档")
        self.lm_cabin_fluct_sp.valueChanged.connect(self._on_lm_value_changed)
        highs_cfg = dict(cfg.get("cabin_channel_highs", {}))
        self.lm_cabin_wrap = QWidget()
        wcl = QVBoxLayout(self.lm_cabin_wrap)
        wcl.setContentsMargins(0, 0, 0, 0)
        wcl.setSpacing(4)
        fluct_row = QHBoxLayout()
        fluct_row.setContentsMargins(0, 0, 0, 0)
        fluct_row.addWidget(QLabel("波动灵敏度"))
        fluct_row.addWidget(self._fix_input(self.lm_cabin_fluct_sp))
        fluct_row.addWidget(QLabel("逐通道显示阈值（勾选=覆盖全局上限，仅驱动宠物显示）:"))
        fluct_row.addStretch(1)
        wcl.addLayout(fluct_row)
        thr_grid = QGridLayout()
        thr_grid.setContentsMargins(0, 0, 0, 0)
        thr_grid.setHorizontalSpacing(4)
        thr_grid.setVerticalSpacing(2)
        self.lm_cabin_thr_cb = []
        self.lm_cabin_thr_sp = []
        for i in range(8):
            key = f"CH{i + 1}"
            r, c = divmod(i, 4)
            cb = QCheckBox("自定义")
            cb.setChecked(key in highs_cfg)
            sp = QDoubleSpinBox()
            sp.setRange(1.0, 9999.0)
            sp.setDecimals(1)
            sp.setSuffix(" ℃")
            sp.setValue(float(highs_cfg.get(key, 50.0)))
            sp.setEnabled(cb.isChecked())
            cb.toggled.connect(sp.setEnabled)
            cb.toggled.connect(self._on_lm_value_changed)
            sp.valueChanged.connect(self._on_lm_value_changed)
            cell = QWidget()
            cell.setMinimumWidth(0)
            cc = QHBoxLayout(cell)
            cc.setContentsMargins(0, 0, 0, 0)
            cc.setSpacing(3)
            cc.addWidget(QLabel(key))
            cc.addWidget(cb)
            cc.addWidget(sp, 1)
            thr_grid.addWidget(cell, r, c)
            self.lm_cabin_thr_cb.append(cb)
            self.lm_cabin_thr_sp.append(sp)
        wcl.addLayout(thr_grid)
        self.lm_cabin_card = self._section_card(
            "生态舱参数", self.lm_cabin_wrap, "", dense=True)
        self._lm_sync_cabin_visible()

        style_section = self._section_card(
            "监控悬浮卡外观", style_body,
            "文字/网格按卡片底色亮度自动配深浅；弹窗贴附悬浮球，"
            "拖动/缩放不影响这些设置。", dense=True)
        style_section.setObjectName("liveMonitorSection")
        ball_section = self._section_card(
            "悬浮球与桌宠", ball_body,
            "位置吸附与形象切换即时生效；详细规则见各卡悬停提示。",
            dense=True)
        panel_section = self._section_card(
            "全通道面板", panel_row,
            "逐通道「名称+温度+进度条」；深浅皮肤手动选，停止采集或查看"
            "历史时自动隐藏；详见悬停提示。", dense=True)
        lay.addWidget(style_section)
        lay.addWidget(ball_section)
        lay.addWidget(panel_section)
        lay.addWidget(self.lm_cabin_card)

        self.lm_card_color_btn.clicked.connect(self._on_lm_pick_card_color)
        self.lm_line_color_btn.clicked.connect(self._on_lm_pick_line_color)
        self.lm_line_color_edit.returnPressed.connect(
            self._on_lm_line_color_committed)
        self.lm_line_color_edit.editingFinished.connect(
            self._on_lm_line_color_committed)
        self.lm_line_reset_btn.clicked.connect(self._on_lm_reset_line_color)
        self.lm_corner_cmb.currentIndexChanged.connect(
            self._on_lm_corner_changed)
        self.lm_restore_btn.clicked.connect(self._on_lm_restore_position)
        self.lm_ball_show_cb.toggled.connect(self._on_lm_value_changed)
        self.lm_ball_warn_pct_sp.valueChanged.connect(
            self._on_lm_value_changed)
        self.lm_panel_enabled_cb.toggled.connect(self._on_lm_value_changed)
        self.lm_panel_alpha_sp.valueChanged.connect(self._on_lm_value_changed)
        self.lm_panel_theme_cmb.currentIndexChanged.connect(
            self._on_lm_value_changed)
        # 本页为即时生效页：键盘输入直接提交（不受全局 keyboardTracking(False)
        # 影响——旧行为键入值必须回车/失焦才生效，用户会误以为「不生效」）；
        # 变更经 300ms 防抖统一应用一次，避免逐参数触发多次全量重绘卡顿。
        for w in (self.lm_window_sec_sp, self.lm_ball_size_sp,
                  self.lm_ball_alpha_sp):
            w.setKeyboardTracking(True)
            w.valueChanged.connect(self._on_lm_value_changed)
        self.lm_card_alpha_in.valueChanged.connect(self._on_lm_value_changed)
        self.lm_card_alpha_in.textEdited.connect(self._lm_refresh_alpha_hint)
        self._lm_debounce = QTimer(self)
        self._lm_debounce.setSingleShot(True)
        self._lm_debounce.setInterval(300)
        self._lm_debounce.timeout.connect(self._apply_live_monitor_page)
        self._lm_refresh_color_controls()
        self._lm_refresh_alpha_hint()
        lay.addStretch(1)
        self.stack.addWidget(page)

    def _lm_refresh_alpha_hint(self):
        """把透明度输入框的校验提示显示到下方（越界/非法标红）。"""
        hint = self.lm_card_alpha_in.hint()
        self.lm_card_alpha_hint.setText(hint)
        color = Theme.RED if hint else Theme.TEXT_MUTED
        fs_d = self.parent._current_font["desc"]
        self.lm_card_alpha_hint.setStyleSheet(
            f"color:{color};font-size:{px_to_pt(fs_d)}pt;")

    def _on_lm_corner_changed(self, _index):
        """悬浮球位置选择变更 → 立即吸附到所选位置并持久化偏好。"""
        corner = self.lm_corner_cmb.currentData()
        if not corner:
            return
        ball = getattr(self.parent, "_live_monitor_ball", None)
        if ball is not None and hasattr(ball, "bring_back_to"):
            ball.bring_back_to(corner)

    def _on_lm_restore_position(self):
        """恢复默认位置：球回默认吸附角（右下角）并转吸附状态。"""
        corner = LIVE_MONITOR_DEFAULTS["ball_corner"]
        idx = self.lm_corner_cmb.findData(corner)
        if idx >= 0:
            self.lm_corner_cmb.setCurrentIndex(idx)
        ball = getattr(self.parent, "_live_monitor_ball", None)
        if ball is not None and hasattr(ball, "bring_back_to"):
            ball.bring_back_to(corner)

    def _on_lm_value_changed(self, _value):
        """输入变更 → 300ms 防抖后统一应用（连击只应用最终值）。"""
        self._lm_debounce.start()

    def _lm_sync_cabin_visible(self):
        """生态舱参数卡随桌宠形象整卡显隐（dino 隐藏不占高，保布局验收）。"""
        cmb = getattr(self, "lm_style_cmb", None)
        card = getattr(self, "lm_cabin_card", None)
        if cmb is None or card is None:
            return
        card.setVisible(cmb.currentData() == "cabin")

    def _apply_live_monitor_page(self):
        """读取全部控件值，落盘并即时应用到弹窗/球（防抖定时器也走这里）。"""
        if getattr(self, "_lm_debounce", None) is not None \
                and self._lm_debounce.isActive():
            self._lm_debounce.stop()
        self.parent._apply_live_monitor_page()

    def _lm_refresh_color_controls(self):
        """刷新取色按钮的色块预览（卡片底色 + 趋势线色）。"""
        self.lm_card_color_btn.setStyleSheet(
            f"background: {self.lm_card_color};"
            f" border: 1px solid #888; border-radius: 3px;")
        self.lm_card_color_btn.setToolTip(self.lm_card_color)

        custom = bool((getattr(self, "lm_line_color", "") or "").strip())
        preview = self.lm_line_color if custom else getattr(
            Theme, "ACCENT", "#1769aa")
        self.lm_line_color_btn.setStyleSheet(
            f"background: {preview};"
            f" border: 1px solid #888; border-radius: 3px;")
        self.lm_line_color_edit.blockSignals(True)
        self.lm_line_color_edit.setText(self.lm_line_color if custom else "")
        self.lm_line_color_edit.blockSignals(False)
        normal_border = (
            f"QLineEdit {{ border: 1px solid {Theme.BORDER};"
            f" border-radius: 3px; padding: 2px 4px; }}")
        self.lm_line_color_edit.setStyleSheet(normal_border)
        if custom:
            self.lm_line_color_btn.setToolTip(f"自定义趋势色 {self.lm_line_color}")
            self.lm_line_color_edit.setToolTip(
                "自定义趋势色（清空后回车=恢复跟随主题）")
        else:
            self.lm_line_color_btn.setToolTip(f"跟随主题强调色 {preview}")
            self.lm_line_color_edit.setToolTip(
                "留空=跟随当前主题强调色（切换主题自动同步）")
        self.lm_line_reset_btn.setEnabled(custom)

    def _on_lm_pick_card_color(self):
        from PyQt5.QtGui import QColor
        from PyQt5.QtWidgets import QColorDialog
        picked = QColorDialog.getColor(QColor(self.lm_card_color), self, "选择卡片底色")
        if picked.isValid():
            self.lm_card_color = picked.name()
            self._lm_refresh_color_controls()
            self._apply_live_monitor_page()

    def _on_lm_pick_line_color(self):
        """趋势线取色：写入自定义色（优先级高于主题色）。"""
        from PyQt5.QtGui import QColor
        from PyQt5.QtWidgets import QColorDialog
        initial = self.lm_line_color or getattr(Theme, "ACCENT", "#1769aa")
        picked = QColorDialog.getColor(QColor(initial), self, "选择趋势线/填充色")
        if picked.isValid():
            self.lm_line_color = picked.name().lower()
            self._lm_refresh_color_controls()
            self._apply_live_monitor_page()

    def _on_lm_line_color_committed(self):
        """色值输入提交：接受 #rrggbb / rrggbb，留空=跟随主题；非法时描红提示。"""
        text = self.lm_line_color_edit.text().strip()
        if text == "":
            # 留空即「恢复默认（跟随主题）」
            if self.lm_line_color != "":
                self.lm_line_color = ""
                self._lm_refresh_color_controls()
                self._apply_live_monitor_page()
            return
        candidate = text if text.startswith("#") else "#" + text
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", candidate):
            self.lm_line_color_edit.setStyleSheet(
                f"QLineEdit {{ border: 1px solid {Theme.RED};"
                f" border-radius: 3px; padding: 2px 4px; }}")
            self.lm_line_color_edit.setToolTip(
                "色值无效，请输入 #rrggbb 或 rrggbb（留空=跟随主题）")
            return
        if candidate.lower() != self.lm_line_color:
            self.lm_line_color = candidate.lower()
            self._lm_refresh_color_controls()
            self._apply_live_monitor_page()

    def _on_lm_reset_line_color(self):
        """恢复默认：清空自定义趋势色，回到跟随当前主题强调色。"""
        if self.lm_line_color == "":
            return
        self.lm_line_color = ""
        self._lm_refresh_color_controls()
        self._apply_live_monitor_page()

    def sync_live_monitor_widgets(self, cfg):
        """外部热重载 live_monitor 段后回灌控件值（阻断信号避免回环）。"""
        for w, key, scale in (
                (getattr(self, "lm_card_alpha_in", None), "card_alpha", 100),
                (getattr(self, "lm_window_sec_sp", None), "window_sec", 1),
                (getattr(self, "lm_ball_size_sp", None), "ball_size", 1),
                (getattr(self, "lm_ball_alpha_sp", None), "ball_alpha", 100)):
            if w is None or key not in cfg:
                continue
            w.blockSignals(True)
            # int 转换：QSpinBox.setValue 不接受 float（ball_alpha 等
            # 配置为浮点，round 后仍为 float，直传会 TypeError）
            w.setValue(int(round(cfg[key] * scale)))
            w.blockSignals(False)
        if "card_color" in cfg:
            self.lm_card_color = cfg["card_color"]
        if "line_color" in cfg:
            self.lm_line_color = cfg["line_color"]
        if hasattr(self, "lm_card_color_btn"):
            self._lm_refresh_color_controls()
        if hasattr(self, "lm_card_alpha_hint"):
            self._lm_refresh_alpha_hint()
        if hasattr(self, "lm_corner_cmb") and "ball_corner" in cfg:
            idx = self.lm_corner_cmb.findData(cfg["ball_corner"])
            if idx >= 0 and idx != self.lm_corner_cmb.currentIndex():
                self.lm_corner_cmb.blockSignals(True)
                self.lm_corner_cmb.setCurrentIndex(idx)
                self.lm_corner_cmb.blockSignals(False)
        if hasattr(self, "lm_ball_show_cb") \
                and "ball_show_max_temp" in cfg:
            self.lm_ball_show_cb.blockSignals(True)
            self.lm_ball_show_cb.setChecked(bool(cfg["ball_show_max_temp"]))
            self.lm_ball_show_cb.blockSignals(False)
        if hasattr(self, "lm_ball_warn_pct_sp") \
                and "ball_temp_warn_pct" in cfg:
            self.lm_ball_warn_pct_sp.blockSignals(True)
            self.lm_ball_warn_pct_sp.setValue(int(cfg["ball_temp_warn_pct"]))
            self.lm_ball_warn_pct_sp.blockSignals(False)
        if hasattr(self, "lm_panel_enabled_cb") \
                and "panel_enabled" in cfg:
            self.lm_panel_enabled_cb.blockSignals(True)
            self.lm_panel_enabled_cb.setChecked(bool(cfg["panel_enabled"]))
            self.lm_panel_enabled_cb.blockSignals(False)
        if hasattr(self, "lm_panel_alpha_sp") \
                and "panel_alpha" in cfg:
            self.lm_panel_alpha_sp.blockSignals(True)
            self.lm_panel_alpha_sp.setValue(
                int(round(float(cfg["panel_alpha"]) * 100)))
            self.lm_panel_alpha_sp.blockSignals(False)
        if hasattr(self, "lm_panel_theme_cmb") \
                and "panel_theme" in cfg:
            idx = self.lm_panel_theme_cmb.findData(cfg["panel_theme"])
            if idx >= 0:
                self.lm_panel_theme_cmb.blockSignals(True)
                self.lm_panel_theme_cmb.setCurrentIndex(idx)
                self.lm_panel_theme_cmb.blockSignals(False)
        # 生态舱：形象下拉 + 灵敏度 + 逐通道阈值勾选/数值整表回灌
        if hasattr(self, "lm_style_cmb") and "pet_style" in cfg:
            idx = self.lm_style_cmb.findData(cfg["pet_style"])
            self.lm_style_cmb.blockSignals(True)
            self.lm_style_cmb.setCurrentIndex(max(0, idx))
            self.lm_style_cmb.blockSignals(False)
            self._lm_sync_cabin_visible()
        if hasattr(self, "lm_cabin_fluct_sp") \
                and "cabin_fluct_rate" in cfg:
            self.lm_cabin_fluct_sp.blockSignals(True)
            self.lm_cabin_fluct_sp.setValue(float(cfg["cabin_fluct_rate"]))
            self.lm_cabin_fluct_sp.blockSignals(False)
        if hasattr(self, "lm_cabin_thr_cb") and "cabin_channel_highs" in cfg:
            highs = dict(cfg["cabin_channel_highs"])
            for i, (cb, sp) in enumerate(zip(self.lm_cabin_thr_cb,
                                             self.lm_cabin_thr_sp)):
                key = f"CH{i + 1}"
                on = key in highs
                cb.blockSignals(True)
                cb.setChecked(on)
                cb.blockSignals(False)
                sp.setEnabled(on)
                if on:
                    sp.blockSignals(True)
                    sp.setValue(float(highs[key]))
                    sp.blockSignals(False)

    # ----------------------------------------------------- 温升统计页（相对斜率参数）
    def _build_stat_page(self):
        page, lay = self._new_page_scroll()
        config = self.parent.rise_config
        self._page_intro(
            lay,
            "温升阶段使用每条通道自身的平滑曲线和相对斜率衰减识别，"
            "不使用固定温度门槛。修改后经底部「应用保存」重新分析统计表和阶段状态图，并自动保存。")

        self.rise_filter_window = QDoubleSpinBox()
        self.rise_filter_window.setRange(1.0, 600.0)
        self.rise_filter_window.setDecimals(0)
        self.rise_filter_window.setValue(config.filter_window_sec)
        filter_card = self._param_card(
            "滤波窗口（秒）", self._fix_input(self.rise_filter_window),
            "先对每条通道做平滑，再计算斜率。窗口越大，噪声和尖峰影响越小，但拐点会更平滑。")

        self.rise_steady_duration = QDoubleSpinBox()
        self.rise_steady_duration.setRange(30.0, 3600.0)
        self.rise_steady_duration.setDecimals(0)
        self.rise_steady_duration.setValue(config.steady_duration_sec)
        steady_card = self._param_card(
            "稳态持续时长（秒）", self._fix_input(self.rise_steady_duration),
            "只有斜率连续满足当前通道的相对稳态条件达到该时长，才确认进入热稳态。")

        self.rise_slope_decay = QDoubleSpinBox()
        self.rise_slope_decay.setRange(0.10, 0.90)
        self.rise_slope_decay.setDecimals(2)
        self.rise_slope_decay.setSingleStep(0.05)
        self.rise_slope_decay.setValue(config.slope_decay_sensitivity)
        decay_card = self._param_card(
            "斜率衰减灵敏度", self._fix_input(self.rise_slope_decay),
            "控制快速升温转为缓慢升温的相对斜率衰减程度，范围 0.10～0.90。数值越大，转折判定越严格。")

        threshold_body = QWidget()
        threshold_layout = QVBoxLayout(threshold_body)
        threshold_layout.setContentsMargins(0, 0, 0, 0)
        self._compact_grid(
            threshold_layout, [filter_card, steady_card, decay_card],
            "statCompactGrid")
        threshold_section = self._section_card("温升判定阈值", threshold_body, "")
        threshold_section.setObjectName("statisticsThresholdSection")
        lay.addWidget(threshold_section)

        lay.addStretch(1)
        self.stack.addWidget(page)

    # ----------------------------------------------------- 报警设置页
    def _build_alarm_page(self):
        page, lay = self._new_page_scroll()
        cfg = self.parent.alarm_config
        self._page_intro(
            lay,
            "实时采集时按全局阈值判定温度超限（上下限 / 变化率 / 通道间温差），"
            "触发时锁定，恢复正常前不重复。可界面高亮、声音、弹窗、写入历史，"
            "并通过独立串口 Modbus 输出报警信号给外部报警模块。"
            "修改后经底部「应用保存」生效。")

        # 总开关
        self.alm_enabled = ToggleSwitch("启用")
        self.alm_enabled.setChecked(cfg.enabled)
        enabled_card = self._param_card(
            "报警总开关", self.alm_enabled,
            "关闭后实时采集不进行任何判定与动作。", dense=True)
        lay.addWidget(self._section_card(
            "启用", enabled_card, "总开关关闭时所有动作均不执行。", dense=True))

        # 阈值（全局统一）
        def _spin(lo, hi, val, step=1.0, dec=1):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(dec)
            s.setSingleStep(step)
            s.setValue(val)
            return s

        self.alm_high = _spin(-1000.0, 3000.0, cfg.temp_high)
        self.alm_follow_axis = ToggleSwitch("跟随温度轴基础窗口")
        self.alm_follow_axis.setChecked(cfg.follow_axis)
        self.alm_follow_offset = QDoubleSpinBox()
        self.alm_follow_offset.setRange(0.0, 50.0)
        self.alm_follow_offset.setDecimals(1)
        self.alm_follow_offset.setSingleStep(0.5)
        self.alm_follow_offset.setValue(float(cfg.follow_offset))
        self.alm_rate = _spin(0.0, 10000.0, cfg.rate_threshold)
        self.alm_diff = _spin(0.0, 10000.0, cfg.diff_threshold)
        follow_card = self._param_card(
            "上限跟随温度轴", self.alm_follow_axis,
            "开启后报警上限自动 = 温度轴基础窗口上限 − 偏移（例：轴上限 40 → 报警 38）；"
            "图表轴自动扩展不改变报警上限。关闭后可在上方手动输入。", dense=True)
        offset_card = self._param_card(
            "跟随偏移（℃）", self._fix_input(self.alm_follow_offset),
            "报警上限相对温度轴基础窗口上限的下调量。", dense=True)
        high_card = self._param_card(
            "温度上限（℃）", self._fix_input(self.alm_high),
            "超过此值触发超上限报警。", dense=True)
        rate_card = self._param_card(
            "变化率阈值（℃/秒）", self._fix_input(self.alm_rate),
            "相邻采样点温度变化率超过此值触发报警。", dense=True)
        diff_card = self._param_card(
            "通道间温差阈值（℃）", self._fix_input(self.alm_diff),
            "同一帧有效通道最大与最小之差超过此值触发报警。", dense=True)

        def _sync_follow_ui():
            follow = self.alm_follow_axis.isChecked()
            self.alm_high.setEnabled(not follow)
            if follow:
                self.alm_high.setValue(round(
                    float(self.parent.ax_temp_base_hi)
                    - self.alm_follow_offset.value(), 2))
        self.alm_follow_axis.toggled.connect(lambda _on: _sync_follow_ui())
        self.alm_follow_offset.valueChanged.connect(
            lambda _v: _sync_follow_ui())
        _sync_follow_ui()

        thr_section = QWidget()
        thr_lay = QVBoxLayout(thr_section)
        thr_lay.setContentsMargins(0, 0, 0, 0)
        self._compact_grid(
            thr_lay, [follow_card, offset_card, high_card, rate_card, diff_card],
            "alarmThresholdGrid", dense=True)
        lay.addWidget(self._section_card(
            "阈值（全局统一）", thr_section,
            "所有通道共用同一组阈值；跟随温度轴时上限自动计算、无需手填。",
            dense=True))

        # 动作开关
        def _toggle(val):
            t = ToggleSwitch("启用")
            t.setChecked(val)
            return t

        self.alm_act_highlight = _toggle(cfg.act_highlight)
        self.alm_act_sound = _toggle(cfg.act_sound)
        self.alm_act_popup = _toggle(cfg.act_popup)
        self.alm_act_log = _toggle(cfg.act_log)
        self.alm_act_serial = _toggle(cfg.act_serial)
        self.alm_act_highlight.setToolTip("报警通道曲线变红、状态栏显示。")
        self.alm_act_sound.setToolTip("进入报警时蜂鸣或播放提示音。")
        self.alm_act_popup.setToolTip("进入报警时状态栏弹出强提示。")
        self.alm_act_log.setToolTip("报警进入 / 恢复写入数据库。")
        self.alm_act_serial.setToolTip("通过独立串口 Modbus 输出报警信号。")
        act_section = QWidget()
        act_lay = QVBoxLayout(act_section)
        act_lay.setContentsMargins(0, 0, 0, 0)
        # 动作开关用无说明的紧凑卡片两列排布，说明移入悬停提示，节省纵向高度
        self._compact_grid(act_lay, [
            self._param_card("界面高亮", self.alm_act_highlight, "", dense=True),
            self._param_card("声音提示", self.alm_act_sound, "", dense=True),
            self._param_card("弹窗提示", self.alm_act_popup, "", dense=True),
            self._param_card("写入历史", self.alm_act_log, "", dense=True),
            self._param_card("串口输出", self.alm_act_serial, "", dense=True),
        ], "alarmActionGrid", dense=True)
        lay.addWidget(self._section_card(
            "报警动作", act_section, "可独立开关；各项说明见悬停提示。",
            dense=True))

        # Modbus 串口参数
        self.alm_serial_port = QComboBox()
        self.alm_serial_port.setEditable(True)
        # 可编辑下拉的内嵌 QLineEdit 局部去边框：全局 QSS 的 QLineEdit
        # 规则会给内嵌行编辑再描一圈，与 QComboBox 外框叠加成双边框
        self.alm_serial_port.setStyleSheet(
            "QComboBox QLineEdit { border: none;"
            " background: transparent; padding: 0; }")
        try:
            from serial.tools import list_ports as _lp
            for p in sorted(sp.device for sp in _lp.comports()):
                self.alm_serial_port.addItem(p)
        except Exception:
            pass
        if cfg.serial_port:
            self.alm_serial_port.setCurrentText(cfg.serial_port)
        port_card = self._param_card(
            "报警输出串口", self.alm_serial_port,
            "独立于采集串口；留空则不输出。", dense=True)
        self.alm_serial_baud = QSpinBox()
        self.alm_serial_baud.setRange(1, 115200)
        self.alm_serial_baud.setValue(int(cfg.serial_baud))
        baud_card = self._param_card("波特率", self.alm_serial_baud, "", dense=True)
        self.alm_modbus_slave = QSpinBox()
        self.alm_modbus_slave.setRange(1, 247)
        self.alm_modbus_slave.setValue(int(cfg.modbus_slave))
        slave_card = self._param_card(
            "Modbus 从站地址", self.alm_modbus_slave, "范围 1-247。", dense=True)
        self.alm_modbus_coil = QSpinBox()
        self.alm_modbus_coil.setRange(0, 65535)
        self.alm_modbus_coil.setValue(int(cfg.modbus_coil))
        coil_card = self._param_card(
            "Modbus 线圈地址", self.alm_modbus_coil,
            "写单个线圈触发 / 解除报警。", dense=True)
        ser_section = QWidget()
        ser_lay = QVBoxLayout(ser_section)
        ser_lay.setContentsMargins(0, 0, 0, 0)
        self._compact_grid(
            ser_lay, [port_card, baud_card, slave_card, coil_card],
            "alarmSerialGrid", dense=True)
        lay.addWidget(self._section_card(
            "Modbus 串口输出", ser_section,
            "仅当「串口输出」开启且选定端口时生效。", dense=True))

        lay.addStretch(1)
        self.stack.addWidget(page)

    # ----------------------------------------------------- 概览页中的 A4 组合图配置
    def _build_a4_controls(self, lay):
        """在概览页下方构建 A4 组合图参数，保留原有控件和即时应用逻辑。"""

        self.a4_e1a = QDoubleSpinBox(); self.a4_e1a.setRange(0, 1e6)
        self.a4_e1a.setDecimals(1); self.a4_e1a.setValue(self.parent.a4_custom1[0] or 0.0)
        self.a4_e1b = QDoubleSpinBox(); self.a4_e1b.setRange(0, 1e6)
        self.a4_e1b.setDecimals(1); self.a4_e1b.setValue(self.parent.a4_custom1[1] if self.parent.a4_custom1[1] is not None else 0.0)
        self.a4_c1end = ToggleSwitch("结束=到数据末尾")
        self.a4_c1end.setChecked(self.parent.a4_custom1[1] is None)
        self.a4_c1end.stateChanged.connect(
            lambda s: self.a4_e1b.setDisabled(s == Qt.Checked))
        self.a4_e2a = QDoubleSpinBox(); self.a4_e2a.setRange(0, 1e6)
        self.a4_e2a.setDecimals(1); self.a4_e2a.setValue(self.parent.a4_custom2[0] or 0.0)
        self.a4_e2b = QDoubleSpinBox(); self.a4_e2b.setRange(0, 1e6)
        self.a4_e2b.setDecimals(1); self.a4_e2b.setValue(self.parent.a4_custom2[1] if self.parent.a4_custom2[1] is not None else 0.0)
        self.a4_c2end = ToggleSwitch("结束=到数据末尾")
        self.a4_c2end.setChecked(self.parent.a4_custom2[1] is None)
        self.a4_c2end.stateChanged.connect(
            lambda s: self.a4_e2b.setDisabled(s == Qt.Checked))
        def a4_card(title, start, end, end_toggle, desc):
            body = self._col_widget(
                self._param_card("起始（分钟）", self._fix_input(start), "时间段起点。"),
                self._param_card("结束（分钟）", self._row_widget(
                    self._fix_input(end), end_toggle), desc),
            )
            return self._section_card(title, body, "")

        a4_grid = QWidget()
        a4_grid.setObjectName("a4CompactGrid")
        a4_layout = QHBoxLayout(a4_grid)
        a4_layout.setContentsMargins(0, 0, 0, 0)
        a4_layout.setSpacing(14)
        a4_layout.addWidget(a4_card(
            "自定义时间段图 1", self.a4_e1a, self.a4_e1b, self.a4_c1end,
            "结束可设置为数据末尾。"), 1)
        a4_layout.addWidget(a4_card(
            "自定义时间段图 2", self.a4_e2a, self.a4_e2b, self.a4_c2end,
            "结束可设置为数据末尾。"), 1)
        lay.addWidget(a4_grid)

        self.a4_chk_mark = ToggleSwitch("启用"); self.a4_chk_mark.setChecked(self.parent.a4_mark)
        # 尾部悬空裸卡并入同级标题分区，与两张时间段卡层级对齐（整改计划 F-02）
        mark_card = self._param_card(
            "在曲线上标注各通道进入平稳的时间线", self.a4_chk_mark,
            "在曲线上用虚线标出各通道进入平稳期的时刻。")
        lay.addWidget(self._section_card("平稳期标注", mark_card, ""))

    # ----------------------------------------------------- 界面主题切换
    def _on_theme_changed(self, idx):
        """用户切换界面主题 → 委托 MainWindow 全局生效（文字 / 背景 / 控件）。"""
        key = self.cmb_theme.itemData(idx)
        if key and key != Theme.active():
            self.parent._apply_theme(key)

    # ----------------------------------------------------- 通道列表风格切换
    def _on_channel_view_changed(self, idx):
        """用户切换通道列表风格 → 委托 MainWindow 即时生效（同主题页模式）。

        与下拉当前值不同才触发，避免程序性同步（blockSignals 之外的重放）
        造成重复重建面板。
        """
        data = self.cmb_channel_view.itemData(idx)
        if data and data != self.parent._channel_view_mode:
            self.parent._apply_channel_view_mode(data)

    # ----------------------------------------------------- 导航切换
    # ----------------------------------------------------- 未保存守卫
    def _page_field_specs(self):
        """需「应用」生效的页面字段注册表：page_idx → [(widget, getter, setter)]。

        未注册的页面（通道管理 / 采集设置 / 概览即时项）均为即时生效，
        不存在"未保存"状态。getter/setter 直接服务于快照比对与还原。
        """
        def sp(w):
            return (w, lambda: w.value(),
                    lambda v: (w.blockSignals(True), w.setValue(v),
                               w.blockSignals(False)))
        def cmb(w):
            return (w, lambda: w.currentIndex(),
                    lambda v: (w.blockSignals(True), w.setCurrentIndex(v),
                               w.blockSignals(False)))
        def chk(w):
            return (w, lambda: w.isChecked(),
                    lambda v: (w.blockSignals(True), w.setChecked(v),
                               w.blockSignals(False)))
        def tm(w):
            return (w, lambda: w.time().toString("HH:mm:ss"),
                    lambda v: (w.blockSignals(True),
                               w.setTime(QTime.fromString(v, "HH:mm:ss")),
                               w.blockSignals(False)))
        def txt(w):
            return (w, lambda: w.currentText().strip(),
                    lambda v: (w.blockSignals(True), w.setCurrentText(v),
                               w.blockSignals(False)))
        def radio_auto(w, other):
            def set_val(v):
                w.blockSignals(True)
                w.setChecked(bool(v))
                other.setChecked(not bool(v))
                w.blockSignals(False)
            return (w, lambda: w.isChecked(), set_val)
        return {
            2: [tm(self.ed_start), sp(self.sp_interval),
                chk(self.chk_relabel)],
            3: [chk(self.chk_smooth), sp(self.sp_smooth),
                cmb(self.cmb_anomaly_method), sp(self.sp_diff),
                sp(self.sp_zwin), sp(self.sp_zsig), sp(self.sp_slope),
                sp(self.sp_fill)],
            4: [radio_auto(self.ax_time_auto_rb, self.ax_time_custom_rb),
                sp(self.ax_time_min_sp), sp(self.ax_time_max_sp),
                sp(self.ax_time_step_sp), sp(self.ax_temp_lo_factor_sp),
                sp(self.ax_temp_hi_factor_sp),
                chk(self.ax_dual_view_chk),
                cmb(self.ax_live_window_cmb)],
            5: [sp(self.a4_e1a), sp(self.a4_e1b), chk(self.a4_c1end),
                sp(self.a4_e2a), sp(self.a4_e2b), chk(self.a4_c2end),
                chk(self.a4_chk_mark)],
            6: [sp(self.rise_filter_window), sp(self.rise_steady_duration),
                sp(self.rise_slope_decay)],
            7: [chk(self.alm_enabled), chk(self.alm_follow_axis),
                sp(self.alm_follow_offset), sp(self.alm_high),
                sp(self.alm_rate), sp(self.alm_diff),
                chk(self.alm_act_highlight), chk(self.alm_act_sound),
                chk(self.alm_act_popup), chk(self.alm_act_log),
                chk(self.alm_act_serial), txt(self.alm_serial_port),
                sp(self.alm_serial_baud), sp(self.alm_modbus_slave),
                sp(self.alm_modbus_coil)],
        }

    def _refresh_all_snapshots(self):
        for idx in self._page_field_specs():
            self._snapshot_page(idx)

    def _snapshot_page(self, idx):
        specs = self._page_field_specs().get(idx)
        if specs:
            self._page_snapshots[idx] = [g() for _w, g, _s in specs]

    def _page_dirty(self, idx) -> bool:
        specs = self._page_field_specs().get(idx)
        snap = self._page_snapshots.get(idx)
        if not specs or snap is None:
            return False
        return any(g() != old for (_w, g, _s), old in zip(specs, snap))

    def _restore_page(self, idx, snap):
        specs = self._page_field_specs().get(idx)
        if not specs or snap is None:
            return
        for (_w, _g, setter), old in zip(specs, snap):
            setter(old)
        if idx == 5:
            # A4「结束=到数据末尾」开关还联动输入框可用态，还原后补一次
            self.a4_e1b.setDisabled(self.a4_c1end.isChecked())
            self.a4_e2b.setDisabled(self.a4_c2end.isChecked())
        if idx == 7:
            # 跟随开关联动上限框可用态
            self.alm_high.setEnabled(not self.alm_follow_axis.isChecked())

    def _apply_action_for_page(self, idx):
        return {
            2: self.parent.apply_and_refresh,
            3: self.parent.apply_and_refresh,
            4: self.parent._apply_axis_page,
            5: self.parent._apply_a4_page,
            6: self.parent._apply_stat_page,
            7: self.parent._apply_alarm_page,
        }.get(idx)

    def _ask_unsaved_decision(self, idx) -> str:
        """三选一弹窗：save / discard / cancel（测试注入点）。"""
        name = self._PAGE_NAMES[idx] if 0 <= idx < len(self._PAGE_NAMES) else ""
        box = QMessageBox(self)
        box.setWindowTitle("有未保存的修改")
        box.setText(f"「{name}」的参数已修改但尚未应用，是否保存？")
        b_save = box.addButton("保存并应用", QMessageBox.YesRole)
        b_discard = box.addButton("放弃修改", QMessageBox.NoRole)
        b_cancel = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(b_cancel)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is b_save:
            return "save"
        if clicked is b_discard:
            return "discard"
        return "cancel"

    def _confirm_leave_page(self) -> bool:
        """离开当前页（切页/关闭）前的守卫；True=可以离开。"""
        idx = self.stack.currentIndex()
        if not self._page_dirty(idx):
            return True
        decision = self._ask_unsaved_decision(idx)
        if decision == "cancel":
            return False
        if decision == "save":
            action = self._apply_action_for_page(idx)
            if action is not None:
                action()          # 应用回调会经 set_page_status 回写快照
        else:
            self._restore_page(idx, self._page_snapshots.get(idx))
        self._update_dirty_feedback()
        return True

    def _revert_nav_selection(self):
        nav = getattr(self, "nav", None)
        prev = getattr(self, "_current_nav_item", None)
        if nav is None or prev is None:
            return
        nav.blockSignals(True)
        nav.setCurrentItem(prev)
        nav.blockSignals(False)

    def _on_nav_tree(self, item, _col):
        """导航点击：离开有未保存修改的页面前先询问。"""
        page_idx = self.parent._nav_page_map.get(id(item))
        if page_idx is None or page_idx == self.stack.currentIndex():
            return
        if not self._confirm_leave_page():
            self._revert_nav_selection()
            return
        self.stack.setCurrentIndex(page_idx)
        self._current_nav_item = item

    # ----------------------------------------------------- 参数预设
    def _param_state(self):
        return {
            "interval": self.sp_interval.value(),
            "start": self.ed_start.time().toString("HH:mm:ss"),
            "relabel": self.chk_relabel.isChecked(),
            "method": self._anomaly_method(),
            "diff_v": self.sp_diff.value(),
            "z_win": self.sp_zwin.value(), "z_sig": self.sp_zsig.value(),
            "slope_v": self.sp_slope.value(),
            "fill": self.sp_fill.value(),
            "smooth": self.chk_smooth.isChecked(), "smooth_w": self.sp_smooth.value(),
        }

    def _apply_param_state(self, st):
        try:
            self.sp_interval.setValue(st["interval"])
            h, m, s = map(int, str(st["start"]).split(":"))
            self.ed_start.setTime(QTime(h, m, s))
            self.chk_relabel.setChecked(st["relabel"])
            method = st.get("method")
            if method not in ("diff", "z", "slope"):
                # 旧配置迁移：按 diff → z → slope 优先级取第一个曾开启的方法，
                # 全部未开启则使用默认方法（标准分离群法）。
                method = ("diff" if st.get("diff") else
                          ("z" if st.get("z") else
                           ("slope" if st.get("slope") else "z")))
            self._set_anomaly_method(method)
            self.sp_diff.setValue(st["diff_v"])
            self.sp_zwin.setValue(st["z_win"]); self.sp_zsig.setValue(st["z_sig"])
            self.sp_slope.setValue(st["slope_v"])
            self.sp_fill.setValue(st["fill"])
            self.chk_smooth.setChecked(st["smooth"]); self.sp_smooth.setValue(st["smooth_w"])
            self._smooth_window_card.setVisible(self.chk_smooth.isChecked())
        except Exception:
            pass
        # 程序化装载持久化参数 ≠ 用户修改：装载后重置未保存守卫快照，
        # 否则 open_settings 一打开就误报"未保存修改"
        try:
            self._refresh_all_snapshots()
        except Exception:
            pass
        self._update_dirty_feedback()
        self.parent.apply_and_refresh()
