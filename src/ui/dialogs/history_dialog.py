# -*- coding: utf-8 -*-
"""
HistoryDialog — 数据对话框（本机数据 / 远程数据 / 服务）

工具栏唯一数据入口「🗂 数据」打开本弹窗，聚合全部数据来源：
- 标签「本机数据」：从本地 SQLite 历史数据库读取采集会话记录
  - 左侧「按月统计」列表（每月会话次数 / 记录数 / 总时长一目了然），
    点击月份即筛选该月会话；右侧平铺表格一行一个会话，日期完整显示
  - 会话名称自定义（重命名按钮 + 输入框，日期列不可编辑）
  - 会话结束判断：仅本机正在实时采集的会话显示「采集中」；
    服务断开 / 采集异常后即使未写停止时间也按已结束处理
  - 手动「保存并结束」：把仍处于未完成状态的会话写入停止时间
  - 加载会话到主界面分析（通过 load_requested 信号交由主窗口执行，
    成功后弹窗提示「已加载」并自动关闭本窗口）
  - 导出会话为 CSV；删除会话（单个）与按时间范围批量删除
  - 起止日期筛选；打开其他数据库文件 / 返回默认数据库
  - 来源列区分：本机采集（绿）/ 远程（橙，并显示设备 IP:端口）/ 文件（灰）
- 标签「远程数据」：远程设备连接与会话获取（RemoteDataTab）
- 标签「服务」：本机网络服务启停（ServiceConfigPage，懒创建——其
  构造后自动执行的防火墙自检需 netsh 查询 1~3 秒，不能拖慢弹窗首开）
- 标签栏右侧「📂 打开文件」角标按钮：承接原工具栏文件入口，
  点击发出 open_file_requested 由主窗口弹系统文件对话框

对话框持有独立的只读数据库连接，不干扰在线采集的写入线程
（SQLite WAL 模式支持并发读写）。
"""
from __future__ import annotations

import datetime
import os
import time
from typing import Optional, List

from PyQt5.QtCore import Qt, QDate, QThread, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTreeWidget, QTreeWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QAbstractItemView, QInputDialog, QDateEdit, QMenu,
    QListWidget, QListWidgetItem, QTabWidget, QWidget, QToolButton,
)

from ui.theme import Theme
from device.database.history_db import HistoryDatabase, default_db_path
from ui.dialogs.history_tree import group_by_month
from ui.dialogs.history_remote_tab import RemoteDataTab
from ui.dialogs.service_config_dialog import ServiceConfigPage


def _fmt_dt(ts: Optional[float]) -> str:
    """Unix 时间戳 → 本地时间字符串（'—' 表示无值）。"""
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _fmt_duration(sec: Optional[float]) -> str:
    """秒 → HH:MM:SS（'—' 表示无值 / 负数）。"""
    if sec is None or sec < 0:
        return "—"
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(round(sec % 60))
    return f"{h:02d}:{m:02d}:{s:02d}"


def _parse_remote_device(source_path: Optional[str]) -> str:
    """从远端会话 source_path（remote://<ip>:<port>/<会话ID>）解析设备地址。

    解析失败返回空字符串（来源列保持显示「远程」）。
    """
    if not source_path:
        return ""
    rest = source_path[len("remote://"):]
    slash = rest.find("/")
    if slash > 0:
        rest = rest[:slash]
    return rest if rest else ""


class _ImportDBWorker(QThread):
    """后台外部库导入：import_external_sessions + 进度信号回 UI 线程。

    C1：整库合并可能耗时（大会话逐条复制），移出 UI 线程；
    ValueError（非本程序库/文件打不开）经 failed 信号回 UI 线程弹窗。
    """
    progress = pyqtSignal(int, int)          # done, total
    done = pyqtSignal(object, object, object)  # imported, skipped, failed
    failed = pyqtSignal(str)

    def __init__(self, hdb, source_path: str, parent=None):
        super().__init__(parent)
        self._hdb = hdb
        self._source_path = source_path

    def run(self):
        try:
            result = self._hdb.import_external_sessions(
                self._source_path,
                progress_cb=lambda d, t: self.progress.emit(d, t))
            self.done.emit(*result)
        except Exception as e:
            self.failed.emit(str(e))


def count_external_sessions(source_path: str) -> int:
    """只读统计外部库的会话数（导入确认弹窗用；失败抛 ValueError）。"""
    import sqlite3
    if not os.path.exists(source_path):
        raise ValueError(f"文件不存在：{source_path}")
    esc = source_path.replace("?", "%3F").replace("#", "%23")
    try:
        src = sqlite3.connect(f"file:{esc}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise ValueError(f"无法打开外部数据库：{e}")
    try:
        try:
            cur = src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "name IN ('sessions','channels','temperature_readings')")
            tables = {row[0] for row in cur.fetchall()}
        except sqlite3.Error as e:
            raise ValueError(f"无法读取外部数据库：{e}")
        if not {"sessions", "channels",
                "temperature_readings"} <= tables:
            raise ValueError("不是本程序的历史数据库文件（缺少数据表）")
        return src.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        src.close()


class HistoryDialog(QDialog):
    """数据对话框（三标签：本机数据 / 远程数据 / 服务 + 打开文件角标按钮）"""

    # 请求把某个会话加载到主界面分析
    # dict: {'session_id': str, 'session_name': str, 'db_path': str}
    load_requested = pyqtSignal(dict)

    # 请求主窗口打开温度数据文件（角标「📂 打开文件」按钮）
    open_file_requested = pyqtSignal()

    # 服务状态变更（转发自服务页；主窗口据此同步状态栏/标题/远程页互斥）
    service_changed = pyqtSignal()

    # ── 远程数据标签页信号（转发自 RemoteDataTab）──
    # 连接成功（device_info 含 client）
    remote_connected = pyqtSignal(dict)
    # 连接断开
    remote_disconnected = pyqtSignal()
    # 获取会话（session_info, monitor：True=采集中实时监控）
    remote_fetch_requested = pyqtSignal(dict, bool)
    # 停止实时监控
    remote_stop_monitor = pyqtSignal()

    COL_DATE = 0      # 日期（完整，含时分秒）
    COL_NAME = 1      # 会话名称
    COL_CHANNELS = 2  # 通道数
    COL_RECORDS = 3   # 记录数
    COL_DURATION = 4  # 时长
    COL_STATUS = 5    # 状态（采集中 / 已完成）
    COL_SOURCE = 6    # 来源（本机采集 / 文件 / 远程）

    def __init__(self, db_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("数据")
        self.setMinimumSize(880, 560)
        self.resize(980, 660)

        self._db_path = db_path or default_db_path()
        self._history_db: Optional[HistoryDatabase] = None
        self._sessions: List = []
        self._session_map = {}
        # 上次前景重刷时的选中项（选择未变时跳过全表重刷）
        self._last_restyled_selected = None
        self._months: List = []
        self._counts: dict = {}
        # 主窗口加载后回写的结果（True 成功 / False 失败 / None 未加载）
        self._last_load_ok = None
        # 服务页懒创建状态：页面与管理器在首次切到「服务」标签时才落到
        # 内嵌页上（外部注入只记录，见 set_service_manager）
        self._service_page: Optional[ServiceConfigPage] = None
        self._service_manager = None
        self._client_mode_active = False

        self._init_ui()
        self._open_db(self._db_path)

    # ==================================================================
    #  UI
    # ==================================================================
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # 三标签：本机数据（本地库）/ 远程数据（远程库 1:1 只读映射）/
        # 服务（网络服务启停，原「🌐 服务」窗口迁入；懒创建）。弹窗
        # 已有「数据」标题，页内不再放重复的大标题。
        self._tabs = QTabWidget()
        local_page = self._create_local_page()
        self._tabs.addTab(local_page, "本机数据")
        self._remote_tab = RemoteDataTab(self)
        self._tabs.addTab(self._remote_tab, "远程数据")
        self._service_container = self._create_service_container()
        self._tabs.addTab(self._service_container, "服务")
        # 首次切到「服务」标签时构建内嵌服务配置页（懒创建）
        self._tabs.currentChanged.connect(self._on_tab_changed)
        # 转发远程页信号（主窗口只需连接本对话框）
        self._remote_tab.connected.connect(self.remote_connected)
        self._remote_tab.disconnected.connect(self.remote_disconnected)
        self._remote_tab.fetch_requested.connect(self.remote_fetch_requested)
        self._remote_tab.stop_monitor_requested.connect(
            self.remote_stop_monitor)

        # 角标按钮「📂 打开文件」：承接原工具栏文件入口，点击请求
        # 主窗口弹系统文件对话框（Ctrl+O 等效）
        self._btn_open_file = QToolButton()
        self._btn_open_file.setObjectName("historyOpenFileButton")
        self._btn_open_file.setText("📂 打开文件")
        self._btn_open_file.setToolTip("打开 / 导入温度数据文件 (Ctrl+O)")
        self._btn_open_file.clicked.connect(self.open_file_requested)
        # 角标按钮不直接贴在 QTabWidget 边框上；使用独立宿主保留
        # 四周留白，避免按钮左/右描边与标签页外框重合。
        corner_host = QWidget()
        corner_layout = QHBoxLayout(corner_host)
        corner_layout.setContentsMargins(6, 0, 8, 0)
        corner_layout.setSpacing(0)
        corner_layout.addWidget(self._btn_open_file)
        self._corner_host = corner_host
        self._tabs.setCornerWidget(corner_host, Qt.TopRightCorner)

        layout.addWidget(self._tabs, 1)

        self._update_buttons()

        # 局部样式统一由 refresh_theme 生成（构造与切主题共用；内联
        # setStyleSheet 优先于全局 QSS，构造时固化旧主题色会导致切主题不跟随）
        self.refresh_theme()

    def refresh_theme(self) -> None:
        """主题切换后按新主题重设本弹窗全部局部样式。

        本弹窗多处局部样式（会话树 / 月份列表 / 状态标签 / 主按钮 /
        远程数据页）优先于全局 QSS，内容在生成时按当时主题固化；
        不重设的话这些控件会停留在旧主题色。主窗口 _apply_theme 在
        切换主题时对存活的弹窗实例调用本方法（关闭仅隐藏，重开也正确）。
        """
        self._lbl_db.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};font-size:9pt;")
        self._month_title.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};font-size:10pt;font-weight:bold;")
        selection_bg = Theme.EMPHASIS_FILL
        self._month_list.setStyleSheet(f"""
            QListWidget {{
                background: {Theme.BG_CARD};
                color: {Theme.TEXT};
                border: 1px solid {Theme.BORDER};
                border-radius: {Theme.RADIUS}px;
                selection-background-color: {selection_bg};
                selection-color: {Theme.on_color_fg(selection_bg)};
                outline: none;
            }}
            QListWidget::item {{
                padding: 5px 6px;
                border: none;
            }}
            QListWidget::item:hover {{
                background: {Theme.BG_HOVER};
            }}
            QListWidget::item:selected {{
                background: {selection_bg};
                color: {Theme.on_color_fg(selection_bg)};
            }}
        """)
        # 树开启了 setAlternatingRowColors，交替行色必须显式给出：QSS
        # 只写 background 时交替行取系统 palette，深色主题下会渲染成
        # 默认的浅灰白条纹（未选中行成「纯白色」、文字看不清）。
        # QDateEdit 不被 QSpinBox 选择器匹配（各继承自 QAbstractSpinBox），
        # 起止时间控件需单独给样式，否则保持系统原生白底。
        selection_bg = Theme.EMPHASIS_FILL
        face = Theme.table_face()
        if face:
            # 浅灰工业深色部件：本机会话树整体深底白字，选中行实色深蓝白字
            self._tree.setStyleSheet(f"""
                QTreeWidget {{
                    background: {face['base']};
                    alternate-background-color: {face['alt']};
                    color: {face['text']};
                    border: 1px solid {Theme.BORDER};
                    border-radius: {Theme.RADIUS}px;
                    selection-background-color: {face['sel_bg']};
                    selection-color: {face['sel_text']};
                    outline: none;
                }}
                QTreeWidget::item {{
                    padding: 4px;
                    border: none;
                }}
                QTreeWidget::item:hover {{ background: {face['alt']}; }}
                QTreeWidget::item:selected {{
                    background: {face['sel_bg']};
                    color: {face['sel_text']};
                }}
                QHeaderView::section {{
                    background: {face['header_bg']};
                    color: {face['header_text']};
                    border: none;
                    border-bottom: 1px solid {Theme.BORDER};
                    padding: 6px;
                    font-weight: bold;
                }}
                QDateEdit {{
                    background: {Theme.BG_INPUT};
                    color: {Theme.TEXT};
                    border: 1px solid {Theme.BORDER};
                    border-radius: 3px;
                    padding: 3px 6px;
                }}
                QDateEdit:focus {{ border-color: {Theme.ACCENT}; }}
            """)
        else:
            self._tree.setStyleSheet(f"""
                QTreeWidget {{
                    background: {Theme.BG_CARD};
                    alternate-background-color:
                        {Theme.lighten(Theme.BG_CARD, 0.04)};
                    color: {Theme.TEXT};
                    border: 1px solid {Theme.BORDER};
                    border-radius: {Theme.RADIUS}px;
                    selection-background-color: {selection_bg};
                    selection-color: {Theme.on_color_fg(selection_bg)};
                    outline: none;
                }}
                QTreeWidget::item {{
                    padding: 4px;
                    border: none;
                }}
                QTreeWidget::item:hover {{ background: {Theme.BG_HOVER}; }}
                QTreeWidget::item:selected {{
                    background: {selection_bg};
                    color: {Theme.on_color_fg(selection_bg)};
                }}
                QHeaderView::section {{
                    background: {Theme.BG_INPUT};
                    color: {Theme.TEXT};
                    border: none;
                    border-bottom: 1px solid {Theme.BORDER};
                    padding: 6px;
                    font-weight: bold;
                }}
                QDateEdit {{
                    background: {Theme.BG_INPUT};
                    color: {Theme.TEXT};
                    border: 1px solid {Theme.BORDER};
                    border-radius: 3px;
                    padding: 3px 6px;
                }}
                QDateEdit:focus {{ border-color: {Theme.ACCENT}; }}
            """)
        self._lbl_status.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};font-size:9pt;")
        self._btn_load.setStyleSheet(Theme.styled_button("primary"))
        # 会话行前景色按新主题与选中状态重刷（构建时固化的颜色不会
        # 跟随主题；force 跳过「选中未变」守卫，无选中项时也必须刷）
        self._restyle_session_items(force=True)
        # 远程数据页（表格/按钮/整页样式）随主题一并刷新
        self._remote_tab.refresh_theme()
        # 服务页（已创建时）：QGroupBox/语义色标签局部样式随主题重设
        if self._service_page is not None:
            self._service_page.refresh_theme()
        # 角标「打开文件」按钮与主工具栏按钮同款描边样式（局部固化，
        # 必须随切主题重设，否则停留旧主题色）
        self._btn_open_file.setStyleSheet(f"""
            QToolButton#historyOpenFileButton {{
                background: {Theme.BG_INPUT};
                border: 1px solid {Theme.BORDER};
                border-radius: 3px;
                min-height: 0;
                padding: 3px 12px;
            }}
            QToolButton#historyOpenFileButton:hover {{
                background: {Theme.BG_HOVER};
                border-color: {Theme.ACCENT};
                color: {Theme.readable_text(Theme.ACCENT)};
            }}
        """)

    def _create_local_page(self) -> QWidget:
        """本机数据页：数据库工具行 + 筛选 + 按月统计/平铺表格 + 操作按钮。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        # 子控件与标签页外框之间保留水平安全边距，避免顶部按钮、
        # 月份列表和右侧会话表共用同一条边框线。
        layout.setContentsMargins(10, 8, 10, 0)
        layout.setSpacing(10)

        # 数据库文件工具行
        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        self._lbl_db = QLabel("")
        tool_row.addWidget(self._lbl_db, 1)

        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.clicked.connect(self._load_sessions)
        tool_row.addWidget(self._btn_refresh)

        self._btn_open_db = QPushButton("打开数据库文件")
        self._btn_open_db.clicked.connect(self._open_file)
        tool_row.addWidget(self._btn_open_db)

        self._btn_default_db = QPushButton("返回默认数据库")
        self._btn_default_db.setToolTip(
            f"切换回默认数据库 {default_db_path()}")
        self._btn_default_db.clicked.connect(self._back_to_default)
        tool_row.addWidget(self._btn_default_db)

        self._btn_import_db = QPushButton("导入外部数据库…")
        self._btn_import_db.setToolTip(
            "把另一台设备的历史数据库合并进当前数据库（按会话 ID 去重，已存在的自动跳过）")
        self._btn_import_db.clicked.connect(self._import_external_db)
        tool_row.addWidget(self._btn_import_db)
        layout.addLayout(tool_row)

        # 时间筛选行
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        filter_row.addWidget(QLabel("起止时间："))
        self._date_start = QDateEdit(QDate(2020, 1, 1))
        self._date_start.setCalendarPopup(True)
        self._date_end = QDateEdit(QDate.currentDate())
        self._date_end.setCalendarPopup(True)
        for de in (self._date_start, self._date_end):
            de.setDisplayFormat("yyyy-MM-dd")
        filter_row.addWidget(self._date_start)
        filter_row.addWidget(QLabel("至"))
        filter_row.addWidget(self._date_end)

        self._btn_filter = QPushButton("筛选")
        self._btn_filter.clicked.connect(self._load_sessions)
        filter_row.addWidget(self._btn_filter)

        self._btn_filter_all = QPushButton("全部")
        self._btn_filter_all.setToolTip("恢复显示全部历史会话")
        self._btn_filter_all.clicked.connect(self._reset_filter)
        filter_row.addWidget(self._btn_filter_all)

        self._btn_delete_range = QPushButton("按范围删除")
        self._btn_delete_range.setToolTip("删除起止日期范围内的全部会话")
        self._btn_delete_range.clicked.connect(self._delete_by_date_range)
        filter_row.addWidget(self._btn_delete_range)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        # 会话列表：左侧按月统计 + 右侧平铺会话表格
        body = QHBoxLayout()
        body.setSpacing(10)

        # 左侧：按月统计列表（每月会话次数一目了然）
        left = QVBoxLayout()
        left.setSpacing(6)
        self._month_title = QLabel("按月统计")
        left.addWidget(self._month_title)
        self._month_list = QListWidget()
        self._month_list.setFixedWidth(172)
        self._month_list.currentRowChanged.connect(self._on_month_selected)
        left.addWidget(self._month_list, 1)
        body.addLayout(left)

        # 右侧：平铺会话表格（一行一个会话，日期完整显示）
        self._tree = self._create_tree()
        body.addWidget(self._tree, 1)
        layout.addLayout(body, 1)

        # 状态提示
        self._lbl_status = QLabel("")
        layout.addWidget(self._lbl_status)

        # 操作按钮
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 6, 8, 4)
        btn_row.setSpacing(10)

        self._btn_load = QPushButton("加载分析")
        self._btn_load.setProperty("buttonRole", "primary")
        self._btn_load.setToolTip(
            "把选中的会话加载到主界面分析，成功后自动关闭本窗口")
        self._btn_load.clicked.connect(self._load_selected)
        btn_row.addWidget(self._btn_load)

        self._btn_rename = QPushButton("重命名")
        self._btn_rename.setToolTip("自定义选中会话的名称（时间戳不可修改）")
        self._btn_rename.clicked.connect(self._rename_selected)
        btn_row.addWidget(self._btn_rename)

        self._btn_export = QPushButton("导出 CSV")
        self._btn_export.clicked.connect(self._export_selected)
        btn_row.addWidget(self._btn_export)

        self._btn_end = QPushButton("保存并结束")
        self._btn_end.setToolTip(
            "把选中的「已断开」会话标记为已结束（写入数据库停止时间）")
        self._btn_end.clicked.connect(self._end_selected)
        btn_row.addWidget(self._btn_end)

        self._btn_delete = QPushButton("删除会话")
        self._btn_delete.clicked.connect(self._delete_selected)
        btn_row.addWidget(self._btn_delete)

        btn_row.addStretch(1)

        self._btn_close = QPushButton("关闭")
        self._btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self._btn_close)
        layout.addLayout(btn_row)

        return page

    def _create_tree(self) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setColumnCount(7)
        tree.setHeaderLabels(
            ["日期", "会话名称", "通道数", "记录数",
             "时长", "状态", "来源"])
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        tree.setSelectionMode(QAbstractItemView.SingleSelection)
        tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tree.setIndentation(0)

        header = tree.header()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.Stretch)
        for col in (self.COL_DATE, self.COL_CHANNELS, self.COL_RECORDS,
                    self.COL_DURATION, self.COL_STATUS, self.COL_SOURCE):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)

        tree.itemSelectionChanged.connect(self._update_buttons)
        tree.itemDoubleClicked.connect(self._on_double_clicked)
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.customContextMenuRequested.connect(self._show_context_menu)

        return tree

    # ==================================================================
    #  服务标签页（ServiceConfigPage 懒创建）
    # ==================================================================
    def _create_service_container(self) -> QWidget:
        """服务标签页容器：内嵌 ServiceConfigPage（懒创建）+ 诊断按钮行。

        ServiceConfigPage 构造后 500ms 会自动做一次防火墙自检（netsh
        查询约 1~3 秒、同步执行），不能在弹窗构造时创建——否则首次
        打开「数据」弹窗就卡顿。页面推迟到首次切到本标签页时构建
        （_ensure_service_page）；外部注入接口只记录状态、不触发创建。
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        self._service_host = QVBoxLayout()
        self._service_host.setSpacing(0)
        layout.addLayout(self._service_host, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_diagnose = QPushButton("🔧 服务诊断")
        self._btn_diagnose.setToolTip(
            "检查各网络服务运行状态、端口占用与本机网卡信息")
        self._btn_diagnose.clicked.connect(self._on_diagnose_clicked)
        btn_row.addWidget(self._btn_diagnose)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        return page

    def _ensure_service_page(self) -> ServiceConfigPage:
        """按需创建内嵌服务配置页（幂等），并应用已记录的外部状态。"""
        if self._service_page is not None:
            return self._service_page
        page = ServiceConfigPage(self, show_footer=False)
        page.service_changed.connect(self.service_changed)
        self._service_page = page
        self._service_host.addWidget(page)
        # 应用注入期间记录的外部状态（管理器 / 客户端模式互斥横幅）
        if self._service_manager is not None:
            page.set_service_manager(self._service_manager)
        page.set_client_active(self._client_mode_active)
        page.refresh_theme()
        return page

    def _on_tab_changed(self, index: int) -> None:
        """首次切到「服务」标签时构建内嵌服务配置页（懒创建）。"""
        if self._tabs.widget(index) is self._service_container:
            self._ensure_service_page()

    def _on_diagnose_clicked(self) -> None:
        """服务诊断：确保页面已创建后转调其诊断入口。"""
        self._ensure_service_page().run_diagnostics()

    # ==================================================================
    #  数据库连接
    # ==================================================================
    def _open_db(self, path: str):
        """切换数据库文件并刷新会话树。"""
        if self._history_db is not None:
            try:
                self._history_db.close()
            except Exception:
                pass
            self._history_db = None

        self._db_path = path
        self._lbl_db.setText(f"数据库：{path}")
        # 打开时可能触发建表 / 列迁移（大库秒级，UI 线程同步）：
        # 先让「正在打开数据库」状态行画出来，避免像无响应
        self._lbl_status.setText("正在打开数据库…")
        QApplication.processEvents()
        try:
            self._history_db = HistoryDatabase(path)
        except Exception as e:
            self._history_db = None
            self._lbl_status.setText(f"无法打开数据库：{e}")
            self._tree.clear()
            self._update_buttons()
            return
        self._load_sessions()

    def _open_file(self):
        """打开其他数据库文件：文件对话框默认跳到当前数据库所在目录。"""
        start_dir = os.path.dirname(self._db_path) or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "打开数据库文件", start_dir,
            "SQLite 数据库 (*.db *.sqlite *.db-wal);;所有文件 (*.*)")
        if path:
            self._open_db(path)

    def _back_to_default(self):
        self._open_db(default_db_path())

    # ==================================================================
    #  导入外部数据库（A 库并入 B 库）
    # ==================================================================
    def _import_external_db(self):
        """选择外部 .db → 确认 → 后台合并进当前数据库 → 刷新会话树。"""
        if self._history_db is None:
            QMessageBox.warning(self, "无法导入", "当前数据库不可用")
            return
        start_dir = os.path.dirname(self._db_path) or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要导入的外部数据库", start_dir,
            "SQLite 数据库 (*.db *.sqlite);;所有文件 (*.*)")
        if not path:
            return
        if os.path.abspath(path) == os.path.abspath(self._db_path):
            QMessageBox.information(self, "无需导入", "选择的文件就是当前数据库。")
            return
        try:
            n_sessions = count_external_sessions(path)
        except ValueError as e:
            QMessageBox.warning(self, "无法导入", str(e))
            return

        ret = QMessageBox.question(
            self, "确认导入外部数据库",
            f"将把外部库中的 {n_sessions} 个会话合并进当前数据库，"
            "已存在的自动跳过。\n\n"
            "注意：若外部库文件复制时未带 -wal 同名文件，最近未落盘的"
            "数据可能不在文件内。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret != QMessageBox.Yes:
            return

        # 后台导入（大会话逐条复制可能耗时），进度经信号回 UI 线程
        self._btn_import_db.setEnabled(False)
        self._lbl_status.setText("正在导入外部数据库…")
        self._import_worker = _ImportDBWorker(self._history_db, path, self)
        self._import_worker.progress.connect(self._on_import_progress)
        self._import_worker.done.connect(self._on_import_done)
        self._import_worker.failed.connect(self._on_import_failed)
        self._import_worker.finished.connect(self._import_worker.deleteLater)
        self._import_worker.start()

    def _on_import_progress(self, done: int, total: int):
        self._lbl_status.setText(
            f"正在导入外部数据库… {done}/{total} 个会话")

    def _on_import_done(self, imported, skipped, failed):
        self._btn_import_db.setEnabled(True)
        self._lbl_status.setText("导入完成")
        QMessageBox.information(
            self, "导入完成",
            f"新导入 {imported} 个，跳过 {skipped} 个（已存在）"
            + (f"，失败 {failed} 个。" if failed else "。"))
        self._load_sessions()

    def _on_import_failed(self, msg: str):
        self._btn_import_db.setEnabled(True)
        self._lbl_status.setText("导入失败")
        QMessageBox.warning(self, "导入失败", msg)

    # ==================================================================
    #  会话树
    # ==================================================================
    def _current_range(self):
        """起止日期 → (起始 00:00 时间戳, 结束 23:59:59.999 时间戳)。"""
        d0, d1 = self._date_start.date(), self._date_end.date()
        if d0 > d1:
            d0, d1 = d1, d0
        start_ts = datetime.datetime(
            d0.year(), d0.month(), d0.day()).timestamp()
        end_ts = datetime.datetime(
            d1.year(), d1.month(), d1.day()).timestamp() + 86400 - 0.001
        return start_ts, end_ts

    def _reset_filter(self):
        self._date_start.setDate(QDate(2020, 1, 1))
        self._date_end.setDate(QDate.currentDate())
        self._load_sessions()

    def _session_item(self, s, record_count: int) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        recording = (s.stopped_at is None
                     and self._is_live_recording(s.session_id))
        # 日期列：完整显示开始时间；结束时间放入提示，不占表格列宽
        item.setText(self.COL_DATE, _fmt_dt(s.started_at))
        item.setToolTip(self.COL_DATE,
                        f"开始：{_fmt_dt(s.started_at)}\n结束：{_fmt_dt(s.stopped_at)}")
        item.setText(self.COL_NAME, s.session_name or s.session_id)
        # 锁定白名单：名称前缀 🔒 标识，提示不参与 30 天自动清理
        if s.locked:
            item.setText(self.COL_NAME, f"🔒 {s.session_name or s.session_id}")
        item.setText(self.COL_CHANNELS, str(s.channel_count))
        item.setText(self.COL_RECORDS, str(record_count))
        item.setText(self.COL_DURATION, _fmt_duration(s.duration_seconds))
        if recording:
            status = "● 采集中"
        elif s.stopped_at is None:
            status = "已结束（异常中断）"
        else:
            status = "已完成"
        item.setText(self.COL_STATUS, status)

        # 来源列：本机采集 / 文件 / 远程（远程橙色标识并显示设备地址）
        source_type = (s.source_type or 'file').lower()
        if source_type == 'remote':
            source_text = "远程"
            device = _parse_remote_device(s.source_path)
            if device:
                source_text = f"远程 · {device}"
        elif source_type == 'live':
            source_text = "本机采集"
        else:
            source_text = "文件"
        item.setText(self.COL_SOURCE, source_text)
        if source_type == 'remote':
            tip = "该会话来自远程设备，已保存在本地数据库"
            if s.source_path:
                tip = f"来源：{s.source_path}\n{tip}"
            item.setToolTip(self.COL_NAME, tip)
        if s.locked:
            base_tip = item.toolTip(self.COL_NAME)
            item.setToolTip(self.COL_NAME,
                            (base_tip + "\n" if base_tip else "")
                            + "已锁定：白名单会话，不参与 30 天自动清理")
        # 各列前景色统一由 _session_item_colors 决定（构建着色与切主题/
        # 选中态重刷共用一套语义色，保证同一行内颜色状态一致）
        for col, color in self._session_item_colors(s, False).items():
            item.setForeground(col, QColor(color))

        item.setData(0, Qt.UserRole, ('session', s.session_id))
        return item

    def _session_item_colors(self, s, selected: bool) -> dict:
        """会话行各列前景色：选中行所有列统一强调色（配边框式选中态，
        文字经 readable_text 对卡底补偿），未选中行按状态/来源语义色
        （语义色同样经 readable_text 补偿，工业中灰上自动提亮）。

        item 的 ForegroundRole 优先于 QSS 的 color 与选中态颜色——不在
        选中/切主题时重设的话，选中行会各列颜色混杂、切主题后停留旧
        主题色。本方法以会话数据为唯一事实源，构建与重刷两处共用。

        注意：返回的是**全部 7 列**的颜色——未选中行非语义列必须显式
        回到 Theme.TEXT。只返回语义色列的话，曾被选中（全列强调色）的
        行取消选中后，非语义列前景停留在强调色，状态语义错乱。
        """
        # 浅灰工业深色部件时补偿底色换会话树深行底（白字系），其余主题对卡底
        face = Theme.table_face()
        surface = face["base"] if face else None
        if selected:
            return {col: Theme.on_color_fg(Theme.EMPHASIS_FILL)
                    for col in range(7)}
        colors = {col: Theme.readable_text(Theme.TEXT, surface)
                  for col in range(7)}
        recording = (s.stopped_at is None
                     and self._is_live_recording(s.session_id))
        if recording or s.stopped_at is None:
            colors[self.COL_STATUS] = Theme.readable_text(Theme.ORANGE,
                                                          surface)
        else:
            colors[self.COL_STATUS] = Theme.readable_text(Theme.TEXT_MUTED,
                                                          surface)
        source_type = (s.source_type or 'file').lower()
        if source_type == 'remote':
            # 远程会话的名称/来源同色标识，形成明显的来源区分
            colors[self.COL_SOURCE] = Theme.readable_text(Theme.ORANGE,
                                                          surface)
            colors[self.COL_NAME] = Theme.readable_text(Theme.ORANGE, surface)
        elif source_type == 'live':
            colors[self.COL_SOURCE] = Theme.readable_text(Theme.GREEN, surface)
        else:
            colors[self.COL_SOURCE] = Theme.readable_text(Theme.TEXT_MUTED,
                                                          surface)
        if s.locked:
            # 锁定白名单：名称列绿色标识受保护状态（优先于来源语义色）
            colors[self.COL_NAME] = Theme.readable_text(Theme.GREEN, surface)
        return colors

    def _restyle_session_items(self, force: bool = False) -> None:
        """按当前主题与选中状态重刷会话行前景色。

        触发点：切主题（refresh_theme，force=True）与选择变化
        （_update_buttons，连接自 itemSelectionChanged）。选中项未变时
        跳过，避免高频点击下全表重刷。数据未加载时静默跳过。
        """
        current = self._tree.currentItem()
        if not force and current is self._last_restyled_selected:
            return
        self._last_restyled_selected = current
        if not self._session_map:
            return
        for item in self._iter_session_items():
            data = item.data(0, Qt.UserRole)
            if data is None or data[0] != 'session':
                continue
            s = self._session_map.get(data[1])
            if s is None:
                continue
            for col, color in self._session_item_colors(
                    s, item is current).items():
                item.setForeground(col, QColor(color))

    def _load_sessions(self):
        """按筛选范围查询会话，重建按月统计列表与平铺会话表格。"""
        self._tree.clear()
        self._sessions = []
        self._session_map = {}
        self._months = []
        if self._history_db is None:
            self._lbl_status.setText("无法打开数据库")
            self._update_buttons()
            return

        # 大库查询 + 逐会话 COUNT 可达秒级且在 UI 线程同步执行：
        # 先让「加载中」状态行画出来，避免页面像无响应
        self._lbl_status.setText("正在加载会话列表…")
        QApplication.processEvents()
        start_ts, end_ts = self._current_range()
        try:
            sessions = self._history_db.get_sessions(
                limit=5000, start_time=start_ts, end_time=end_ts)
            counts = self._history_db.get_record_counts(
                [s.session_id for s in sessions])
        except Exception as e:
            self._lbl_status.setText(f"查询会话失败：{e}")
            self._update_buttons()
            return

        self._sessions = sessions
        self._counts = counts
        for s in sessions:
            self._session_map[s.session_id] = s

        self._months = group_by_month(sessions, counts)
        self._rebuild_month_list()
        # 默认选中「全部」显示所有会话
        self._month_list.blockSignals(True)
        self._month_list.setCurrentRow(0)
        self._month_list.blockSignals(False)
        self._fill_sessions(sessions)

        self._lbl_status.setText(
            f"共 {len(sessions)} 个会话 · 自动保留最近 30 天"
            f"（🔒 锁定会话不清理；仅实时采集中会话不可加载/删除）")
        self._update_buttons()

    def _rebuild_month_list(self):
        """重建左侧「按月统计」列表：首项「全部」+ 每月一行。"""
        self._month_list.clear()
        all_item = QListWidgetItem(f"全部（{len(self._sessions)} 个会话）")
        self._month_list.addItem(all_item)
        for m in self._months:
            item = QListWidgetItem(f"{m['label']} · {m['session_count']}次会话")
            item.setToolTip(
                f"{m['label']}：{m['session_count']} 个会话 · "
                f"{m['record_count']:,} 条记录 · "
                f"{_fmt_duration(m['duration_sum'])}")
            item.setData(Qt.UserRole, ('month', m['key']))
            self._month_list.addItem(item)

    def _on_month_selected(self, row: int):
        """左侧月份选择 → 平铺表格只显示该月会话（「全部」显示全部）。"""
        item = self._month_list.item(row)
        if item is None:
            return
        data = item.data(Qt.UserRole)
        if data is None or data[0] != 'month':
            self._fill_sessions(self._sessions)
            return
        mkey = data[1]
        for m in self._months:
            if m['key'] == mkey:
                self._fill_sessions(m['sessions'])
                return

    def _fill_sessions(self, sessions):
        """按给定会话列表填充平铺表格（一行一个会话）。"""
        self._tree.clear()
        for s in sessions:
            self._tree.addTopLevelItem(self._session_item(
                s, self._counts.get(s.session_id, 0)))

    def _selected_session(self):
        """当前选中会话行的会话对象；未选中返回 None。"""
        item = self._tree.currentItem()
        if item is None:
            return None
        data = item.data(0, Qt.UserRole)
        if data is None or data[0] != 'session':
            return None
        return self._session_map.get(data[1])

    def _update_buttons(self):
        # 选中行变化时重刷各列前景色（选中行统一反白，其余行回语义色）
        self._restyle_session_items()
        s = self._selected_session()
        if s is None:
            self._btn_load.setEnabled(False)
            self._btn_rename.setEnabled(False)
            self._btn_export.setEnabled(False)
            self._btn_end.setEnabled(False)
            self._btn_delete.setEnabled(False)
            return
        recording = (s.stopped_at is None
                     and self._is_live_recording(s.session_id))
        # 会话在数据库里仍是「采集中」状态（stopped_at 为空）
        db_open = s.stopped_at is None
        self._btn_load.setEnabled(not recording)
        # 名称是会话元数据，任何状态都可自定义
        self._btn_rename.setEnabled(True)
        self._btn_export.setEnabled(True)
        # 仅允许手动结束已断开但仍显示为未完成的会话
        self._btn_end.setEnabled(db_open and not recording)
        self._btn_delete.setEnabled(not recording)

    def _is_live_recording(self, session_id: str) -> bool:
        """判断会话是否为本机当前正在进行的实时采集会话。"""
        try:
            from device.datastore import store
            live = store.live
            return bool(live is not None and live.is_recording
                        and live.id == session_id)
        except Exception:
            return False

    def _live_recording_ids(self) -> set:
        """本机正在实时采集的会话 ID 集合（批量删除时需跳过）。"""
        try:
            from device.datastore import store
            live = store.live
            if live is not None and live.is_recording:
                return {live.id}
        except Exception:
            pass
        return set()

    def _on_double_clicked(self, item, column):
        """双击已完成会话 = 加载分析。"""
        if item is None:
            return
        data = item.data(0, Qt.UserRole)
        if data is None or data[0] != 'session':
            return
        self._tree.setCurrentItem(item)
        self._load_selected()

    def _show_context_menu(self, pos):
        """右键菜单：会话行（加载/重命名/导出/结束/删除）。"""
        item = self._tree.itemAt(pos)
        if item is None:
            return
        self._tree.setCurrentItem(item)
        data = item.data(0, Qt.UserRole)
        menu = QMenu(self)
        if data is not None and data[0] == 'session':
            s = self._selected_session()
            recording = (s is not None and s.stopped_at is None
                         and self._is_live_recording(s.session_id))
            act_load = menu.addAction("加载分析")
            act_load.setEnabled(not recording)
            act_rename = menu.addAction("重命名")
            act_export = menu.addAction("导出 CSV")
            act_end = menu.addAction("保存并结束")
            act_end.setEnabled(s is not None and s.stopped_at is None
                               and not recording)
            act_del = menu.addAction("删除会话")
            act_del.setEnabled(not recording)
            act_lock = menu.addAction(
                "解锁（恢复自动清理）" if s is not None and s.locked
                else "锁定（30 天内不自动清理）")
            chosen = menu.exec_(self._tree.viewport().mapToGlobal(pos))
            if chosen is act_load:
                self._load_selected()
            elif chosen is act_rename:
                self._rename_selected()
            elif chosen is act_export:
                self._export_selected()
            elif chosen is act_end:
                self._end_selected()
            elif chosen is act_del:
                self._delete_selected()
            elif chosen is act_lock:
                self._toggle_lock_selected()

    # ==================================================================
    #  操作
    # ==================================================================
    def _load_selected(self):
        """发出加载请求，由主窗口把数据加载进本地 DataStore。"""
        s = self._selected_session()
        if s is None:
            return
        name = s.session_name or s.session_id
        self._last_load_ok = None
        self.load_requested.emit({
            'session_id': s.session_id,
            'session_name': name,
            'db_path': self._db_path,
        })
        # 主窗口加载完成后会关闭对话框（复用隐藏机制）

    def _export_selected(self):
        """导出选中会话为 CSV 文件。"""
        s = self._selected_session()
        if s is None or self._history_db is None:
            return
        default_name = f"{s.session_name or s.session_id}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出会话为 CSV", default_name,
            "CSV 文件 (*.csv);;所有文件 (*.*)")
        if not path:
            return
        ok = self._history_db.export_to_csv(
            s.session_id, path, use_display_name=False)
        if ok:
            self._lbl_status.setText(f"已导出：{os.path.basename(path)}")
            QMessageBox.information(self, "导出成功", f"已导出到：\n{path}")
        else:
            QMessageBox.warning(self, "导出失败", "导出会话数据失败")

    def _end_selected(self):
        """手动结束选中的会话：写入数据库停止时间，标记为已完成。"""
        s = self._selected_session()
        if s is None or self._history_db is None:
            return
        if s.stopped_at is not None:
            return
        name = s.session_name or s.session_id
        ret = QMessageBox.question(
            self, "确认结束",
            f"会话「{name}」尚未写入结束时间。\n"
            f"确定将其标记为已结束并保存吗？\n"
            f"（结束时间取最后一条温度数据的时刻）")
        if ret != QMessageBox.Yes:
            return
        if self._history_db.end_session(s.session_id):
            self._lbl_status.setText(f"会话「{name}」已标记为结束")
            self._load_sessions()
        else:
            QMessageBox.warning(self, "保存失败", "结束会话失败")

    def _rename_selected(self):
        """重命名选中会话（仅修改名称，时间戳不可修改）。"""
        s = self._selected_session()
        if s is None or self._history_db is None:
            return
        old_name = s.session_name or s.session_id
        new_name, ok = QInputDialog.getText(
            self, "重命名会话", "新的会话名称：", text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "重命名失败", "会话名称不能为空")
            return
        if new_name == old_name:
            return
        if self._history_db.rename_session(s.session_id, new_name):
            self._load_sessions()
            for it in self._iter_session_items():
                if it.data(0, Qt.UserRole)[1] == s.session_id:
                    self._tree.setCurrentItem(it)
                    break
            self._lbl_status.setText(f"会话已重命名为：{new_name}")
        else:
            QMessageBox.warning(self, "重命名失败", "更新会话名称失败")

    def _iter_session_items(self):
        """遍历平铺表格中的全部会话行 item。"""
        for i in range(self._tree.topLevelItemCount()):
            yield self._tree.topLevelItem(i)

    def _delete_selected(self):
        """删除选中会话（级联删除其温度数据 / 通道配置 / 统计信息）。"""
        s = self._selected_session()
        if s is None or self._history_db is None:
            return
        name = s.session_name or s.session_id
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除会话「{name}」？\n删除后该会话数据不可恢复。")
        if ret != QMessageBox.Yes:
            return
        if self._history_db.delete_session(s.session_id):
            self._lbl_status.setText(f"已删除会话：{name}")
            self._load_sessions()
        else:
            QMessageBox.warning(self, "删除失败", "删除会话失败")

    def _delete_by_date_range(self):
        """删除起止日期范围内的全部会话（跳过采集中会话）。"""
        start_ts, end_ts = self._current_range()
        text = (f"确定删除 {self._date_start.date().toString('yyyy-MM-dd')} 至 "
                f"{self._date_end.date().toString('yyyy-MM-dd')} "
                f"范围内的全部会话吗？\n删除后数据不可恢复。")
        ret = QMessageBox.question(self, "确认删除", text)
        if ret != QMessageBox.Yes:
            return
        deleted, skipped = self._history_db.delete_sessions_by_range(
            start_ts, end_ts, exclude_ids=self._live_recording_ids())
        self._set_delete_result(deleted, skipped)

    def _set_delete_result(self, deleted: int, skipped: int):
        msg = f"已删除 {deleted} 个会话"
        if skipped:
            msg += f"，跳过 {skipped} 个采集中/锁定会话"
        self._load_sessions()
        self._lbl_status.setText(msg)

    def _toggle_lock_selected(self):
        """切换选中会话锁定状态（锁定 = 白名单，30 天自动清理跳过）。

        成功后原地更新当前行文本/提示/颜色，不重建列表（保留选中与
        月份过滤状态）。
        """
        s = self._selected_session()
        if s is None or self._history_db is None:
            return
        new_locked = not bool(s.locked)
        if not self._history_db.set_session_locked(s.session_id, new_locked):
            self._lbl_status.setText("更新锁定状态失败")
            return
        s.locked = new_locked
        item = self._tree.currentItem()
        if item is not None:
            fresh = self._session_item(s, self._counts.get(s.session_id, 0))
            for col in range(7):
                item.setText(col, fresh.text(col))
                item.setToolTip(col, fresh.toolTip(col))
                item.setForeground(col, fresh.foreground(col))
        self._lbl_status.setText(
            f"已{'锁定' if new_locked else '解锁'}会话："
            f"{s.session_name or s.session_id}"
            + ("（不参与 30 天自动清理）" if new_locked else ""))

    def set_remote_server_active(self, active: bool) -> None:
        """同步服务端互斥状态到远程数据页（本机服务运行时禁用连接）。"""
        self._remote_tab.set_server_active(active)

    def set_service_manager(self, manager) -> None:
        """记录服务管理器；服务页已创建时同步注入（不自动启停）。

        不触发懒创建：服务页首次切到「服务」标签时构建并应用本状态。
        """
        self._service_manager = manager
        if self._service_page is not None:
            self._service_page.set_service_manager(manager)

    def set_client_mode_active(self, active: bool) -> None:
        """记录客户端模式互斥状态；服务页已创建时同步其横幅。

        不触发懒创建（横幅只是页面内状态）；服务端（运行服务）与
        客户端（连接远程）硬互斥，主窗口在打开弹窗与模式翻转时推送。
        """
        self._client_mode_active = bool(active)
        if self._service_page is not None:
            self._service_page.set_client_active(self._client_mode_active)

    def maybe_reconnect_remote(self) -> None:
        """重新打开对话框时自动重连上次远程设备（静默失败）。"""
        self._remote_tab.maybe_reconnect()

    def showEvent(self, event) -> None:
        """重新显示时恢复服务页状态定时器（closeEvent 已停止；实例复用）。"""
        super().showEvent(event)
        if self._service_page is not None:
            self._service_page.resume()

    def closeEvent(self, event) -> None:
        """关闭对话框时释放数据库连接、远程页线程与服务页定时器
        （远程连接保留不中断）。"""
        if self._history_db is not None:
            try:
                self._history_db.close()
            except Exception:
                pass
            self._history_db = None
        try:
            self._remote_tab.shutdown()
        except Exception:
            pass
        if self._service_page is not None:
            try:
                self._service_page.shutdown()
            except Exception:
                pass
        super().closeEvent(event)
