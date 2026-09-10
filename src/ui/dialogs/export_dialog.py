# -*- coding: utf-8 -*-
"""右侧非模态导出抽屉：导出类型列表和底部导出操作区。"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from ui.theme import Theme
from utils.config_io import ConfigIO


class ExportDialog(QFrame):
    """可嵌入主窗口的非模态导出抽屉。"""

    export_requested = pyqtSignal(list, str, bool, str, str, object)
    close_requested = pyqtSignal()

    # 首次使用（无记忆配置）时的默认勾选：整体趋势图 + A4组合图
    DEFAULT_SELECTED_TASKS = ("overview_all", "a4_combo")

    def __init__(self, parent, tasks, default_directory=None,
                 export_theme_options=None):
        super().__init__(parent)
        self.tasks = [dict(task) for task in tasks or []]
        self.task_checks = {}
        self.task_format_labels = {}
        self._default_directory = default_directory or ConfigIO.load_last_export_dir()
        self._export_theme_options = list(export_theme_options or [
            ("跟随当前界面主题", None),
            *[(theme_name, theme_key)
              for theme_key, theme_name in Theme.THEME_NAMES.items()],
        ])
        self.setObjectName("exportDrawer")
        self.setFixedWidth(380)
        self._build_ui()
        self._apply_initial_selection()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("导出")
        title.setObjectName("exportDrawerTitle")
        header.addWidget(title)
        header.addStretch(1)
        close_button = QPushButton("×")
        close_button.setObjectName("exportDrawerClose")
        close_button.setFixedSize(28, 28)
        close_button.setToolTip("收起导出面板")
        close_button.clicked.connect(self.close_requested.emit)
        header.addWidget(close_button)
        root.addLayout(header)

        hint = QLabel("选择需要导出的内容")
        hint.setObjectName("exportDrawerHint")
        root.addWidget(hint)

        self.list_area = QWidget()
        list_layout = QVBoxLayout(self.list_area)
        list_layout.setContentsMargins(0, 2, 0, 0)
        list_layout.setSpacing(2)
        for task in self.tasks:
            task_id = task.get("id", "")
            row = QFrame()
            row.setObjectName("exportTaskRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 7, 10, 7)
            row_layout.setSpacing(8)
            check = QCheckBox(task.get("name", task_id))
            check.setObjectName("exportTaskCheck")
            check.setProperty("taskId", task_id)
            check.toggled.connect(self._refresh_state)
            check.toggled.connect(self._remember_selection)
            row_layout.addWidget(check, 1)
            format_label = QLabel(task.get("extension", "").upper().lstrip("."))
            format_label.setObjectName("exportTaskFormat")
            format_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row_layout.addWidget(format_label)
            list_layout.addWidget(row)
            self.task_checks[task_id] = check
            self.task_format_labels[task_id] = format_label
        list_layout.addStretch(1)
        root.addWidget(self.list_area, 1)

        operation = QFrame()
        operation.setObjectName("exportDrawerOperation")
        operation_layout = QVBoxLayout(operation)
        # 操作区是独立边框容器，按钮与容器边缘保持明确留白，避免
        # 按钮描边和外框重叠；按钮之间也使用稳定的 10px 间距。
        operation_layout.setContentsMargins(14, 12, 14, 14)
        operation_layout.setSpacing(8)

        self.selected_count_label = QLabel("已选择 0 项")
        self.selected_count_label.setObjectName("exportSelectedCount")
        operation_layout.addWidget(self.selected_count_label)

        path_row = QHBoxLayout()
        self.path_label = QLabel(self._default_directory)
        self.path_label.setObjectName("exportPathLabel")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setToolTip(self._default_directory)
        path_row.addWidget(self.path_label, 1)
        choose_path = QPushButton("选择路径")
        choose_path.clicked.connect(self._choose_directory)
        path_row.addWidget(choose_path)
        operation_layout.addLayout(path_row)

        self.image_background = QComboBox()
        self.image_background.addItem("白色背景（报告推荐）", "light")
        self.image_background.addItem("黑色背景", "dark")
        operation_layout.addLayout(self._option_row("图片背景", self.image_background))

        self.image_format = QComboBox()
        # 矢量图（SVG）导出已按需求移除，仅保留 PNG 高清图（报告内嵌图仍为 SVG）
        self.image_format.addItem("PNG 高清图（300 DPI）", "png")
        self.image_format.currentIndexChanged.connect(self._refresh_image_task_formats)
        operation_layout.addLayout(self._option_row("图片格式", self.image_format))

        self.export_theme_combo = QComboBox()
        for theme_name, theme_key in self._export_theme_options:
            self.export_theme_combo.addItem(theme_name, theme_key)
        operation_layout.addLayout(self._option_row("导出主题", self.export_theme_combo))

        self.remember_path = QCheckBox("记住上次路径")
        self.remember_path.setChecked(True)
        operation_layout.addWidget(self.remember_path)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(2, 2, 2, 0)
        button_row.setSpacing(10)
        button_row.addStretch(1)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.close_requested.emit)
        button_row.addWidget(cancel_button)
        self.confirm_button = QPushButton("开始导出")
        self.confirm_button.setProperty("buttonRole", "primary")
        self.confirm_button.clicked.connect(self._emit_export)
        button_row.addWidget(self.confirm_button)
        operation_layout.addLayout(button_row)
        root.addWidget(operation)

    @staticmethod
    def _option_row(label_text, widget):
        row = QHBoxLayout()
        row.setSpacing(8)
        label = QLabel(label_text)
        label.setObjectName("exportOptionLabel")
        row.addWidget(label)
        row.addWidget(widget, 1)
        return row

    def visible_task_ids(self):
        return [task["id"] for task in self.tasks
                if self.task_checks.get(task["id"]) is not None
                and self.task_checks[task["id"]].isChecked()]

    def _apply_initial_selection(self):
        """恢复上次勾选；从未保存过（首次使用）则用默认两项。

        初始化阶段屏蔽 toggled 信号，只读不写，避免打开抽屉即写配置。
        """
        saved = ConfigIO.load_export_selected_tasks()
        if saved is None:
            saved = list(self.DEFAULT_SELECTED_TASKS)
        for task_id in dict.fromkeys(saved):
            check = self.task_checks.get(task_id)
            if check is None:
                continue  # 已删除的任务 id（如 HTML 报告）直接忽略
            check.blockSignals(True)
            check.setChecked(True)
            check.blockSignals(False)
        self._refresh_state()

    def _remember_selection(self):
        """勾选变化即持久化，跨重启记住用户勾选的任务。"""
        ConfigIO.save_export_selected_tasks(self.visible_task_ids())

    def _refresh_state(self):
        count = len(self.visible_task_ids())
        self.selected_count_label.setText(f"已选择 {count} 项")
        self.confirm_button.setEnabled(count > 0)

    def _refresh_image_task_formats(self):
        extension = str(self.image_format.currentData() or "png").upper()
        for task in self.tasks:
            task_id = task.get("id", "")
            if task_id in ExportDialog._image_task_ids():
                label = self.task_format_labels.get(task_id)
                if label is not None:
                    label.setText(extension)

    @staticmethod
    def _image_task_ids():
        return {"current_canvas", "overview_all", "overview_10", "overview_20",
                "overview_30", "a4_combo"}

    def _choose_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self, "选择导出目录",
            self.path_label.text() or self._default_directory)
        if directory:
            self.path_label.setText(directory)
            self.path_label.setToolTip(directory)

    def _emit_export(self):
        task_ids = self.visible_task_ids()
        if not task_ids:
            return
        self.export_requested.emit(
            task_ids, self.path_label.text(), self.remember_path.isChecked(),
            str(self.image_background.currentData() or "light"),
            str(self.image_format.currentData() or "png"),
            self.export_theme_combo.currentData())
