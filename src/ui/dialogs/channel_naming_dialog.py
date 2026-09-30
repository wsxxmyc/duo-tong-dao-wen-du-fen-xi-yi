# -*- coding: utf-8 -*-
"""
ChannelNamingDialog —— 通道命名弹窗（面板顶部「自定义各通道名称」入口打开）。

一次性编辑所有通道的名称映射与名称池，改动实时生效。左右双栏布局：

- 左栏「各通道名称」表格：每行 = 通道物理号 / 自定义名 / 名称下拉。
  「自定义名」列只显示纯自定义名（CH 前缀与首列重复，予以隐藏；
  前端页面 / 卡片仍显示完整名 CHn·自定义名）。下拉候选复用
  ChannelPanel.name_candidates（首位「CHn（默认）」，其余为名称池中
  未被其它通道占用的名），宽度按内容自适应以完整显示名称。选中即走
  ChannelPanel._on_name_changed 统一改名链路（store.config.set_name →
  channels_changed → 面板重建），左侧卡片纯文本立即更新；随后重算各行
  候选（沿用唯一占用剔除）。
- 右栏「可用名称列表」：与设置弹窗「通道管理 → 名称列表」同源
  （mw.name_list），可编辑、可添加、可删除；名称最多 6 个字
  （NameListDelegate 编辑器限制，设置弹窗共用）；改动经 MainWindow
  既有三个方法落盘并经 _notify_name_list_changed 广播（两处表格与
  各行候选即时同步）。

非模态；主窗口持有复用实例（mw._naming_dialog），关闭仅隐藏，
重新打开时按当前活跃会话重建通道行。
"""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QComboBox, QWidget, QHBoxLayout,
    QPushButton, QSizePolicy, QFrame, QStyledItemDelegate, QLineEdit,
    QApplication,
)
from PyQt5.QtCore import pyqtSignal

from device.datastore import store
from device.datastore.channel import strip_seq_label
from ui.theme import Theme
from utils.config_io import ConfigIO


#: 自定义通道名称的最大长度（字）；名称池两处编辑入口共用
NAME_MAX_LEN = 6


class NameListDelegate(QStyledItemDelegate):
    """名称池表格第 0 列编辑代理：限制编辑器最多输入 NAME_MAX_LEN 个字。

    命名弹窗与设置弹窗的「可用名称列表」共用，保证两个入口的新增 /
    编辑名称长度一致受限（QLineEdit.maxLength 对键入与粘贴同样生效）。
    """

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            editor.setObjectName("nameListCellEditor")
            editor.setFrame(False)
            editor.setMaxLength(NAME_MAX_LEN)
        return editor

    def updateEditorGeometry(self, editor, option, index):
        """让编辑器覆盖完整单元格，避免表格内再出现一层输入框边界。"""
        editor.setGeometry(option.rect)


_DLG_QSS = """
QLabel#sectionLabel {{
    color: {muted};
    font-size: {caption}pt;
    font-weight: 600;
    background: transparent;
    border: none;
}}
QTableWidget {{
    background: transparent;
    border: 1px solid {bd};
    border-radius: 3px;
    gridline-color: {bg_input};
    color: {text};
    font-size: {base}pt;
}}
QTableWidget::item {{ padding: 0 8px; border: none; }}
QTableWidget#nameListTable::item:selected {{
    background: {active_bg};
    color: {active_fg};
}}
QTableWidget#nameListTable:focus {{ border-color: {bd}; }}
QHeaderView::section {{
    background: transparent;
    color: {muted};
    padding: 3px 6px;
    border: none;
    border-bottom: 1px solid {bd};
    font-weight: 600;
}}
QComboBox {{
    background: {bg_input};
    color: {text};
    border: 1px solid {bd};
    border-radius: 3px;
    padding: 2px 8px;
    font-size: {base}pt;
}}
QComboBox:hover {{ border-color: {accent}; }}
QComboBox QAbstractItemView {{
    background: {bg_input};
    color: {text};
    border: 1px solid {bd};
    selection-background-color: transparent;
    selection-color: {sel_fg};
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    min-height: 30px;
    padding: 6px 9px;
    border: 1px solid transparent;
    border-radius: 3px;
}}
QComboBox QAbstractItemView::item:selected {{
    background: transparent;
    border-color: {accent};
    color: {sel_fg};
}}
QLineEdit#nameListCellEditor {{
    background: {bg_input};
    color: {text};
    border: none;
    padding: 0 8px;
    font-size: {base}pt;
}}
QPushButton#poolBtn {{
    background: {bg_input};
    color: {text};
    border: 1px solid {bd};
    border-radius: 3px;
    padding: 4px 10px;
    font-size: {base}pt;
}}
QPushButton#poolBtn:hover {{
    border-color: {accent};
    color: {accent_fg};
}}
"""


class ChannelNamingDialog(QDialog):
    """居中非模态的通道命名弹窗：左通道映射表 + 右名称池管理，实时生效。"""

    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw                       # MainWindow 引用（设置弹窗同款模式）
        self.setWindowTitle("通道命名")
        self.setModal(False)
        # 最小宽度保证左栏下拉列可完整显示最长候选名（约 176px）
        self.setMinimumSize(570, 360)
        self._row_channels = []            # 行号 -> Channel（行序 = 会话通道序）
        self._row_combos = []              # 行号 -> QComboBox
        self._build_ui()
        self.setStyleSheet(self._build_qss())
        # 会话切换 / 关闭 → 打开状态下同步重建通道行
        # （绑定方法连接：窗口销毁时 Qt 自动断开，store 单例无残留）
        store.active_changed.connect(self._on_active_changed)

        # ── 几何记忆 ──
        self._geometry_save_timer = QTimer(self, singleShot=True, timeout=self._save_geometry)
        self._restoring_geometry = False  # 防止恢复期间触发保存

    @staticmethod
    def _build_qss() -> str:
        """按当前主题生成整窗 QSS（构造与 refresh_theme 共用）。
        强调色作文字（hover、下拉选中）经 readable_text 对卡底补偿。"""
        return _DLG_QSS.format(
            muted=Theme.TEXT_MUTED, text=Theme.TEXT, bd=Theme.BORDER,
            bg_input=Theme.BG_INPUT, accent=Theme.ACCENT,
            active_bg=Theme.lighten(Theme.ACCENT, 0.82),
            active_fg=Theme.on_color_fg(Theme.lighten(Theme.ACCENT, 0.82)),
            sel_fg=Theme.readable_text(Theme.ACCENT, Theme.BG_INPUT),
            accent_fg=Theme.readable_text(Theme.ACCENT),
            caption=f"{max(8, Theme.FONT_SIZE - 1)}",
            base=f"{Theme.FONT_SIZE}")

    def refresh_theme(self) -> None:
        """主题切换后按新主题重设整窗 QSS。

        本弹窗被主窗口缓存复用（关闭仅隐藏），QSS 在构造时 format 固化
        且优先于全局 QSS，不重设会停留在旧主题配色。
        """
        self.setStyleSheet(self._build_qss())

    # ------------------------------------------------------------------
    #  几何记忆（保存/恢复位置与大小）
    # ------------------------------------------------------------------
    _MIN_SIZE = (570, 360)  # 必须与 setMinimumSize 同步

    def _restore_geometry(self) -> None:
        """从配置恢复弹窗位置与大小；非法值/出屏坐标降级为父窗口居中。"""
        try:
            d = ConfigIO.load_section("naming_dialog_geometry", {}, {})
            x = int(d.get("x", -1))
            y = int(d.get("y", -1))
            w = max(self._MIN_SIZE[0], int(d.get("w", self._MIN_SIZE[0])))
            h = max(self._MIN_SIZE[1], int(d.get("h", self._MIN_SIZE[1])))

            # 坐标夹取在可用屏幕区域内（防止多屏/分辨率变化后窗口不可见）
            if x >= 0 and y >= 0:
                screen = QApplication.desktop().availableGeometry(self)
                x = min(max(x, screen.left()), screen.right() - w)
                y = min(max(y, screen.top()), screen.bottom() - h)
                self.setGeometry(x, y, w, h)
            # 否则保持 Qt 默认行为（居中于父窗口）
        except Exception:
            pass  # 异常时保持 Qt 默认行为（居中）

    def _save_geometry(self) -> None:
        """持久化当前几何（x, y, w, h）到 settings.json 的 naming_dialog_geometry 分区。"""
        try:
            g = self.geometry()
            ConfigIO.save_section("naming_dialog_geometry", {
                "x": g.x(), "y": g.y(),
                "w": max(g.width(), self._MIN_SIZE[0]),
                "h": max(g.height(), self._MIN_SIZE[1]),
            })
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        """窗口尺寸变化：延迟持久化（防抖，避免拖动时频繁写盘）。"""
        super().resizeEvent(event)
        if hasattr(self, "_geometry_save_timer") and not self._restoring_geometry:
            self._geometry_save_timer.start(250)

    def moveEvent(self, event) -> None:
        """窗口移动：延迟持久化（防抖）。"""
        super().moveEvent(event)
        if hasattr(self, "_geometry_save_timer") and not self._restoring_geometry:
            self._geometry_save_timer.start(250)

    def hideEvent(self, event) -> None:
        """关闭/隐藏：立即持久化（弹窗为复用实例，hide = 隐藏到托盘/任务栏）。"""
        self._geometry_save_timer.stop()
        self._save_geometry()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    #  UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self):
        """左右双栏：左「各通道名称」映射表（吃掉多余宽度），右「可用名称列表」
        池管理（名称限 6 字，固定窄宽不留大片留白）。"""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        columns = QHBoxLayout()
        columns.setSpacing(8)

        # ── 左栏：各通道名称（映射表） ──
        left = QVBoxLayout()
        left.setSpacing(4)
        lbl_channels = QLabel("各通道名称（选择后立即生效）")
        lbl_channels.setObjectName("sectionLabel")
        left.addWidget(lbl_channels)

        self.tbl_channels = QTableWidget(0, 3)
        self.tbl_channels.setHorizontalHeaderLabels(["通道", "名称", "选择名称"])
        ch = self.tbl_channels.horizontalHeader()
        ch.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        ch.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        # 下拉列吃掉剩余宽度：既能完整显示名称，又避免列间大片留白
        ch.setSectionResizeMode(2, QHeaderView.Stretch)
        self.tbl_channels.verticalHeader().setVisible(False)
        # 每个通道下拉框保留上下留白，避免相邻控件视觉上首尾相接。
        self.tbl_channels.verticalHeader().setDefaultSectionSize(36)
        self.tbl_channels.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl_channels.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl_channels.setShowGrid(False)
        self.tbl_channels.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left.addWidget(self.tbl_channels, 1)
        columns.addLayout(left, 1)

        # ── 右栏：可用名称列表（名称池管理） ──
        # 名称限 6 字 → 固定窄栏即可容纳，多余宽度全部留给左栏
        pane_pool = QWidget()
        pane_pool.setFixedWidth(190)
        right = QVBoxLayout(pane_pool)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)
        lbl_pool = QLabel(f"可用名称列表（最多 {NAME_MAX_LEN} 字）")
        lbl_pool.setObjectName("sectionLabel")
        right.addWidget(lbl_pool)

        # 池表列名与设置弹窗一致；属性名 tbl_name_list 与
        # MainWindow._populate_name_list_table / _remove_name_from_list 兼容
        self.tbl_name_list = QTableWidget(0, 1)
        self.tbl_name_list.setObjectName("nameListTable")
        self.tbl_name_list.setHorizontalHeaderLabels(["可用名称列表"])
        ph = self.tbl_name_list.horizontalHeader()
        ph.setSectionResizeMode(0, QHeaderView.Stretch)
        self.tbl_name_list.verticalHeader().setVisible(False)
        self.tbl_name_list.verticalHeader().setDefaultSectionSize(26)
        self.tbl_name_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tbl_name_list.setEditTriggers(QAbstractItemView.DoubleClicked |
                                           QAbstractItemView.SelectedClicked |
                                           QAbstractItemView.EditKeyPressed)
        # 编辑器限制最多 NAME_MAX_LEN 个字（设置弹窗同一代理）
        self.tbl_name_list.setItemDelegateForColumn(0, NameListDelegate(self.tbl_name_list))
        self.tbl_name_list.itemChanged.connect(self.mw._on_name_list_changed)
        self.tbl_name_list.itemSelectionChanged.connect(self._update_name_list_actions)
        right.addWidget(self.tbl_name_list, 1)

        btns = QWidget()
        bh = QHBoxLayout(btns)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(6)
        self.btn_add_name = QPushButton("➕ 添加")
        self.btn_add_name.setObjectName("poolBtn")
        self.btn_add_name.clicked.connect(lambda: self.mw._add_name_to_list(self))
        self.btn_delete_name = QPushButton("✕ 删除")
        self.btn_delete_name.setObjectName("poolBtn")
        self.btn_delete_name.clicked.connect(lambda: self.mw._remove_name_from_list(self))
        bh.addWidget(self.btn_add_name)
        bh.addWidget(self.btn_delete_name)
        bh.addStretch(1)
        right.addWidget(btns)

        # 名称独立文件手动重载入口：文件改动通常由 ConfigWatcher 自动生效，
        # 此按钮用于自动监听失效（如个别系统事件丢失）时的兜底
        self.btn_reload_names = QPushButton("📄 从文件重载")
        self.btn_reload_names.setObjectName("poolBtn")
        self.btn_reload_names.setToolTip(
            "重新读取 用户数据/config/通道名称-channel-names.json 并应用。\n"
            "该文件用文本编辑器修改保存后会自动生效，此按钮为手动兜底。")
        self.btn_reload_names.clicked.connect(
            lambda: self.mw._apply_channel_names_from_file())
        right.addWidget(self.btn_reload_names)
        columns.addWidget(pane_pool)

        lay.addLayout(columns)
        self._update_name_list_actions()

    def _update_name_list_actions(self) -> None:
        """删除按钮只在名称表存在当前选中行时可用。"""
        self.btn_delete_name.setEnabled(self.tbl_name_list.currentRow() >= 0)

    def select_name_list_row(self, row: int) -> None:
        """选中指定名称行；非法行清空选择并同步删除按钮状态。"""
        if 0 <= row < self.tbl_name_list.rowCount():
            self.tbl_name_list.setCurrentCell(row, 0)
        else:
            self.tbl_name_list.clearSelection()
            self.tbl_name_list.setCurrentCell(-1, -1)
        self._update_name_list_actions()

    def edit_name_list_row(self, row: int) -> None:
        """选中并编辑指定名称行，自动全选默认名称以便直接覆盖输入。"""
        self.select_name_list_row(row)
        item = self.tbl_name_list.item(row, 0)
        if item is None:
            return
        self.tbl_name_list.setFocus(Qt.OtherFocusReason)
        self.tbl_name_list.editItem(item)
        QTimer.singleShot(0, self._select_active_name_editor)

    def _select_active_name_editor(self) -> None:
        for editor in self.tbl_name_list.findChildren(QLineEdit):
            if editor.isVisible():
                editor.selectAll()
                break

    # ------------------------------------------------------------------
    #  打开 / 会话同步
    # ------------------------------------------------------------------
    def open_dialog(self) -> None:
        """打开（复用实例）弹窗：恢复几何 → 按当前会话重建通道行并填充名称池。"""
        self._restoring_geometry = True
        try:
            self._restore_geometry()
        finally:
            self._restoring_geometry = False

        self.rebuild_channels()
        self.refresh_pool()
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_active_changed(self, session) -> None:
        """会话切换 / 关闭 → 打开状态下同步重建通道行。"""
        if self.isVisible():
            self.rebuild_channels()

    # ------------------------------------------------------------------
    #  通道行（映射表）
    # ------------------------------------------------------------------
    def rebuild_channels(self) -> None:
        """按当前活跃会话重建通道映射表（行 = 通道，列 = 物理号/显示名/下拉）。"""
        s = store.active
        self.tbl_channels.setRowCount(0)
        self._row_channels = []
        self._row_combos = []
        panel = getattr(self.mw, "channel_panel", None)
        if s is None or not s.channels:
            return
        for ch in s.channels:
            row = self.tbl_channels.rowCount()
            self.tbl_channels.insertRow(row)
            item_key = QTableWidgetItem(ch.key)
            item_key.setFlags(Qt.ItemIsEnabled)
            self.tbl_channels.setItem(row, 0, item_key)
            # 「自定义名」列只显示纯自定义名：CH 前缀与首列重复，予以隐藏
            # （前端页面 / 卡片仍显示完整名 CHn·自定义名）
            item_name = QTableWidgetItem(strip_seq_label(ch.display_name) or "—")
            item_name.setFlags(Qt.ItemIsEnabled)
            self.tbl_channels.setItem(row, 1, item_name)

            combo = QComboBox()
            # 填满 Stretch 列宽度：下拉列已占剩余空间，combo 横向填满避免右侧留空
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            # 候选与左侧面板一致：首位默认项 + 名称池中未被占用的名
            cands = panel.name_candidates(ch) if panel is not None else [
                f"{ch.key}（默认）"]
            combo.addItems(cands)
            # 未自定义时 resolve 下发的 name 即物理号（CH1），视为默认项
            default_label = f"{ch.key}（默认）"
            named = ch.name not in ("", ch.key)
            target = ch.display_name if named else default_label
            idx = combo.findText(target)
            if idx < 0:
                combo.addItem(target)
                idx = combo.count() - 1
            combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(
                lambda _i, c=ch: self._on_combo_changed(c))
            combo_cell = QWidget()
            combo_layout = QVBoxLayout(combo_cell)
            combo_layout.setContentsMargins(0, 4, 0, 4)
            combo_layout.setSpacing(0)
            combo_layout.addWidget(combo)
            self.tbl_channels.setCellWidget(row, 2, combo_cell)
            self._row_channels.append(ch)
            self._row_combos.append(combo)

    def _on_combo_changed(self, ch) -> None:
        """行下拉选中 → 统一改名链路（面板重建 + 卡片纯文本即时更新）。"""
        row = self._row_channels.index(ch) if ch in self._row_channels else -1
        if row < 0:
            return
        label = self._row_combos[row].currentText()
        panel = getattr(self.mw, "channel_panel", None)
        if panel is None:
            return
        panel._on_name_changed(ch.index, label)

    def refresh_channel_candidates(self) -> None:
        """名称池 / 占用变化 → 重算各行候选并更新「当前显示」列。

        blockSignals 保留每行当前选择，不触发改名回调（防止循环）。
        """
        s = store.active
        panel = getattr(self.mw, "channel_panel", None)
        if s is None or panel is None:
            return
        for row, ch in enumerate(self._row_channels):
            if row >= self.tbl_channels.rowCount():
                break
            item = self.tbl_channels.item(row, 1)
            if item is not None:
                item.setText(strip_seq_label(ch.display_name) or "—")
            combo = self._row_combos[row]
            cands = panel.name_candidates(ch)
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(cands)
            idx = combo.findText(current)
            if idx < 0:
                combo.addItem(current)
                idx = combo.count() - 1
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    # ------------------------------------------------------------------
    #  名称池
    # ------------------------------------------------------------------
    def refresh_pool(self) -> None:
        """名称池变化 → 重填池表格并重算各行下拉候选。"""
        selected_row = self.tbl_name_list.currentRow()
        self.mw._populate_name_list_table(self)
        if selected_row >= 0 and self.tbl_name_list.rowCount() > 0:
            selected_row = min(selected_row, self.tbl_name_list.rowCount() - 1)
        self.select_name_list_row(selected_row)
        self.refresh_channel_candidates()
