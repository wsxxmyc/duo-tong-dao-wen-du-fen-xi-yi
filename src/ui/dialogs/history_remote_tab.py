# -*- coding: utf-8 -*-
"""
RemoteDataTab — 「查看历史 → 远程数据」标签页。

把远程服务端的历史数据库会话列表 1:1 只读映射到本地表格：
- 搜索局域网设备（与网络对话框原有流程一致）或手动输入 IP 连接；
- 连接保持（50 秒心跳保活），列出该设备最近的会话；
- 会话状态区分「● 采集中」（远程仪器正在采集，可实时监控）与
  「已完成」（历史会话，一次性获取）；
- 只读：不提供任何修改远程数据的操作。

本页只负责 UI 与意图信号；连接成功后 client 引用回传 MainWindow
统一管理（监控引擎/一次性获取都在主窗口执行）。
"""

from __future__ import annotations

import time
from typing import Optional, List, Dict, Any

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QSpinBox, QGroupBox, QMessageBox, QProgressBar, QDialog,
    QAbstractItemView, QSizePolicy,
)
from PyQt5.QtGui import QColor, QBrush

from ui.theme import Theme
from device.network import DeviceScanner
from device.network.client import DataClient, merge_same_device
from device.network.protocol import DEFAULT_TCP_PORT, DEFAULT_SCAN_TIMEOUT
from ui.dialogs import firewall_helper


# ─────────────────────────────────────────────────────────────
# 后台线程（自 remote_connect_dialog 迁移，语义不变）
# ─────────────────────────────────────────────────────────────

class ScanThread(QThread):
    """设备扫描线程"""

    scan_finished = pyqtSignal(list)
    scan_error = pyqtSignal(str)

    def __init__(self, timeout: float = DEFAULT_SCAN_TIMEOUT):
        super().__init__()
        self.timeout = timeout
        self.scanner = DeviceScanner()

    def run(self) -> None:
        try:
            devices = self.scanner.scan(timeout=self.timeout)
            self.scan_finished.emit(devices)
        except Exception as e:
            self.scan_error.emit(str(e))


class ConnectThread(QThread):
    """目标连接线程（序号防护：丢弃旧线程迟到的回调）"""

    connect_success = pyqtSignal(int, dict)
    connect_failed = pyqtSignal(int, str)

    _seq = 0

    def __init__(self, ip: str, port: int):
        super().__init__()
        ConnectThread._seq += 1
        self.seq = ConnectThread._seq
        self.ip = ip
        self.port = port
        self.client = DataClient()

    def run(self) -> None:
        try:
            if self.client.connect(self.ip, self.port):
                self.client.get_session_list(limit=1)  # 连通性探测
                self.connect_success.emit(self.seq, {
                    'ip': self.ip, 'port': self.port,
                    'client': self.client, 'connected': True,
                    'device_name': f'{self.ip}:{self.port}',
                })
            else:
                self.connect_failed.emit(
                    self.seq, "连接失败，请检查设备地址和端口")
        except Exception as e:
            self.connect_failed.emit(self.seq, f"连接异常：{str(e)}")


class SessionListThread(QThread):
    """拉取当前设备会话列表线程（连接断开时 None 作为错误上报）。"""

    sessions_loaded = pyqtSignal(object, list)
    session_error = pyqtSignal(object, str)

    def __init__(self, client, limit: int = 200):
        super().__init__()
        self.client = client
        self.limit = limit

    def run(self) -> None:
        try:
            sessions = self.client.get_session_list(limit=self.limit)
            if sessions is None:
                self.session_error.emit(
                    self.client, "连接已断开或超时，请重新连接")
                return
            self.sessions_loaded.emit(self.client, sessions)
        except Exception as e:
            self.session_error.emit(self.client, str(e))


# ─────────────────────────────────────────────────────────────
# 搜索完成结果弹窗
# ─────────────────────────────────────────────────────────────

def _ip_highlight_color() -> str:
    """设备列表/弹窗里 IP 高亮绿：深色部件（浅灰工业 表）上自动提亮保证可读。"""
    face = Theme.table_face()
    if face:
        return Theme.lighten(Theme.GREEN, 0.55)
    return Theme.GREEN


class ScanResultDialog(QDialog):
    """搜索完成结果弹窗：无论有无结果都弹出展示，供用户选择设备。

    - 有结果：设备列表（设备名称 / IP 地址(绿) / 端口），「连接所选」或双击行选中；
    - 无结果：给出排查提示并可「重新搜索」。
    结果经 ``chosen()``（选中的设备 dict 或 None）与 ``was_retry()`` 读取。
    """

    def __init__(self, devices: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("搜索结果")
        self.resize(440, 280)
        self._chosen = None
        self._retry = False
        self._devices = list(devices or [])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        if self._devices:
            lay.addWidget(QLabel(
                f"发现 {len(self._devices)} 台设备，选择要连接的设备："))
            table = QTableWidget(len(self._devices), 3, self)
            table.setHorizontalHeaderLabels(["设备名称", "IP 地址", "端口"])
            table.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.Stretch)
            table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeToContents)
            table.horizontalHeader().setSectionResizeMode(
                2, QHeaderView.ResizeToContents)
            table.verticalHeader().setVisible(False)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setSelectionMode(QAbstractItemView.SingleSelection)
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            ip_color = _ip_highlight_color()
            for row, dev in enumerate(self._devices):
                name = QTableWidgetItem(
                    str(dev.get("device_name") or "未知设备"))
                ip = QTableWidgetItem(str(dev.get("ip_address") or ""))
                ip.setForeground(QBrush(QColor(ip_color)))
                port = QTableWidgetItem(str(dev.get("tcp_port") or ""))
                table.setItem(row, 0, name)
                table.setItem(row, 1, ip)
                table.setItem(row, 2, port)
            table.selectRow(0)
            self._table = table
            lay.addWidget(table, 1)

            btns = QHBoxLayout()
            btns.addStretch(1)
            cancel_btn = QPushButton("取消")
            cancel_btn.clicked.connect(self.reject)
            ok_btn = QPushButton("连接所选")
            ok_btn.setStyleSheet(Theme.styled_button("primary"))
            ok_btn.clicked.connect(self._accept_selected)
            ok_btn.setDefault(True)
            btns.addWidget(cancel_btn)
            btns.addWidget(ok_btn)
            lay.addLayout(btns)
            table.doubleClicked.connect(lambda _i: self._accept_selected())
        else:
            tip = QLabel(
                "未发现设备。\n\n请确认：\n"
                "· 服务端已启动 TCP / 配对码服务；\n"
                "· 本机与设备在同一局域网；\n"
                "· 防火墙已放行（UDP 9526）。\n\n"
                "也可关闭本窗口后，在下方手动输入 IP 连接。")
            tip.setWordWrap(True)
            tip.setStyleSheet(
                f"color: {Theme.readable_text(Theme.TEXT_MUTED)};")
            lay.addWidget(tip)
            btns = QHBoxLayout()
            btns.addStretch(1)
            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(self.reject)
            retry_btn = QPushButton("重新搜索")
            retry_btn.clicked.connect(self._on_retry)
            btns.addWidget(close_btn)
            btns.addWidget(retry_btn)
            lay.addLayout(btns)

    def _accept_selected(self):
        row = self._table.currentRow()
        if 0 <= row < len(self._devices):
            self._chosen = self._devices[row]
            self.accept()

    def _on_retry(self):
        self._retry = True
        self.accept()

    def chosen(self):
        """用户选中的设备 dict；未选择/无结果时返回 None。"""
        return self._chosen

    def was_retry(self) -> bool:
        """是否点了「重新搜索」（仅在无结果分支出现）。"""
        return self._retry


# ─────────────────────────────────────────────────────────────
# 远程数据标签页
# ─────────────────────────────────────────────────────────────

class RemoteDataTab(QWidget):
    """「查看历史 → 远程数据」页（只读映射远程库会话）。"""

    # 连接成功（device_info 含 client；MainWindow 记录统一管理）
    connected = pyqtSignal(dict)
    # 连接断开（MainWindow 清引用）
    disconnected = pyqtSignal()
    # 获取会话（session_info, monitor：True=采集中实时监控 False=已完结一次性获取）
    fetch_requested = pyqtSignal(dict, bool)
    # 停止实时监控
    stop_monitor_requested = pyqtSignal()

    # 会话表列
    COL_DATE, COL_NAME, COL_CHANNELS, COL_DURATION = 0, 1, 2, 3
    COL_STATUS, COL_FETCH = 4, 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scan_thread: Optional[ScanThread] = None
        self._connect_thread: Optional[ConnectThread] = None
        self._session_thread: Optional[SessionListThread] = None
        self._probe_thread: Optional[SessionListThread] = None
        self._retired_threads: list = []
        self._devices: List[Dict[str, Any]] = []
        self._sessions: List[Dict[str, Any]] = []
        self._client: Optional[DataClient] = None
        self._current: Optional[Dict[str, Any]] = None      # 当前连接信息
        self._last: Optional[Dict[str, Any]] = None         # 上次成功连接（重开重连）
        self._server_active = False
        self._monitor_session_id = ''
        # 上次前景重刷时的选中行（行未变时跳过全表重刷）
        self._last_restyled_row = -2

        self._init_ui()

    # ── UI ─────────────────────────────────────────────────────

    def _init_ui(self):
        self.setObjectName("remoteDataTab")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 远程设备区：搜索 + 手动输入 + 连接
        device_group = self._create_device_group()
        layout.addWidget(device_group)

        # 会话表格（1:1 映射远程库）
        self._session_table = self._create_session_table()
        layout.addWidget(self._session_table, 1)

        # 按钮行：刷新 / 获取数据 / 停止监控
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._refresh_btn = QPushButton("刷新列表")
        self._refresh_btn.setToolTip("重新拉取当前设备的会话列表")
        self._refresh_btn.clicked.connect(self._refresh_sessions)
        self._refresh_btn.setEnabled(False)
        btn_row.addWidget(self._refresh_btn)

        self._fetch_btn = QPushButton("获取数据")
        self._fetch_btn.setProperty("buttonRole", "primary")
        self._fetch_btn.setStyleSheet(Theme.styled_button("primary"))
        self._fetch_btn.setToolTip(
            "选中「已完成」会话：一次性获取完整数据；\n"
            "选中「● 采集中」会话：实时监控（每分钟自动更新，结束后保存）")
        self._fetch_btn.clicked.connect(self._fetch_selected)
        self._fetch_btn.setEnabled(False)
        btn_row.addWidget(self._fetch_btn)

        self._stop_monitor_btn = QPushButton("停止监控")
        self._stop_monitor_btn.setToolTip("停止当前实时监控并保存到本地历史")
        self._stop_monitor_btn.clicked.connect(
            self.stop_monitor_requested.emit)
        self._stop_monitor_btn.setEnabled(False)
        btn_row.addWidget(self._stop_monitor_btn)
        btn_row.addStretch(1)

        self._status_label = QLabel("未连接：请搜索设备或输入服务端 IP")
        self._status_label.setStyleSheet(
            f"color: {Theme.readable_text(Theme.TEXT_MUTED)};")
        btn_row.addWidget(self._status_label)
        layout.addLayout(btn_row)

        # 局部样式统一由 refresh_theme 生成（构造与切主题共用；本页
        # setStyleSheet 优先于全局 QSS，构造时固化旧主题色会导致切主题不跟随）
        self.refresh_theme()

    def refresh_theme(self) -> None:
        """主题切换后按新主题重设本页全部局部样式。

        通用控件（按钮/输入框/下拉框/标签/表头）样式全部由全局 QSS
        提供，随 app.setStyleSheet 重设自动跟随主题，本页不再复刻一份
        局部规则（复刻版固化旧主题色且缺少完整状态，反而屏蔽全局）。
        仅保留两类局部样式：
        1. QGroupBox 分区容器——全局 QSS 未覆盖，按当前主题生成；
        2. 会话表格交替行色——表格开启 setAlternatingRowColors，QSS
           不显式给出时交替行取系统 palette，深色主题下渲染成浅灰白
           条纹（未选中行成「纯白色」、文字看不清）。
        会话行各列前景色在 item 构建时固化，须一并按新主题重刷。
        由 HistoryDialog.refresh_theme 转发调用。
        """
        self._status_label.setStyleSheet(
            f"color: {Theme.readable_text(Theme.TEXT_MUTED)};")
        self._fetch_btn.setStyleSheet(Theme.styled_button("primary"))
        # 连接标签颜色按当前连接状态取色（断开按钮仅在已连接时可用）；
        # 状态色作文字统一经 readable_text 对卡底补偿
        connected = self._disconnect_btn.isEnabled()
        conn_color = (Theme.GREEN if connected else Theme.TEXT_MUTED)
        self._conn_label.setStyleSheet(
            f"color: {Theme.readable_text(conn_color)};"
            f" font-weight: bold;")
        selection_bg = Theme.EMPHASIS_FILL
        face = Theme.table_face()
        if face:
            # 浅灰工业 深色部件：会话表整体深底白字，选中行实色深蓝白字；
            # 交替行用深灰两档（同样必须显式给出，理由见上）
            self.setStyleSheet(f"""
                {Theme.group_box_qss()}
                QTableWidget {{
                    background: {face['base']};
                    color: {face['text']};
                    alternate-background-color: {face['alt']};
                    selection-background-color: {face['sel_bg']};
                    selection-color: {face['sel_text']};
                    outline: none;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                {Theme.group_box_qss()}
                QTableWidget {{
                    alternate-background-color:
                        {Theme.lighten(Theme.BG_CARD, 0.04)};
                    selection-background-color: {selection_bg};
                    selection-color: {Theme.on_color_fg(selection_bg)};
                    outline: none;
                }}
            """)
        self._restyle_session_rows(force=True)

    def _create_device_group(self) -> QGroupBox:
        group = QGroupBox("远程设备")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self._scan_btn = QPushButton("搜索设备")
        self._scan_btn.setToolTip("扫描局域网中的服务端设备（约 3 秒）")
        self._scan_btn.clicked.connect(self._start_scan)
        row1.addWidget(self._scan_btn)

        self._fw_btn = QPushButton("防火墙检测")
        self._fw_btn.setToolTip("检测 Windows 防火墙是否阻止发现/连接（可一键放行）")
        self._fw_btn.clicked.connect(
            lambda: firewall_helper.show_check_result(self))
        row1.addWidget(self._fw_btn)

        self._scan_progress = QProgressBar()
        self._scan_progress.setRange(0, 0)
        self._scan_progress.setFixedHeight(18)
        self._scan_progress.setVisible(False)
        row1.addWidget(self._scan_progress, 1)
        layout.addLayout(row1)

        # 设备列表：搜索结果全部展开（本场景 ≤5 台，列表比下拉直观，
        # 关键信息 IP 用绿色突出，方便逐台对比）
        self._device_table = self._create_device_table()
        layout.addWidget(self._device_table)

        # 目标地址/端口：列表选中自动回填，也支持手动输入连接
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(QLabel("IP："))
        self._ip_edit = QLineEdit()
        self._ip_edit.setPlaceholderText("例如：192.168.1.100")
        self._ip_edit.setFixedWidth(110)
        row2.addWidget(self._ip_edit)
        row2.addWidget(QLabel("端口："))
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(DEFAULT_TCP_PORT)
        self._port_spin.setFixedWidth(72)
        row2.addWidget(self._port_spin)

        self._connect_btn = QPushButton("连接")
        self._connect_btn.clicked.connect(self._connect_target)
        row2.addWidget(self._connect_btn)
        self._disconnect_btn = QPushButton("断开")
        self._disconnect_btn.setToolTip("断开当前连接（监控中的实时监控不受影响）")
        self._disconnect_btn.clicked.connect(self._disconnect)
        self._disconnect_btn.setEnabled(False)
        row2.addWidget(self._disconnect_btn)
        row2.addStretch(1)
        layout.addLayout(row2)

        self._conn_label = QLabel("当前设备：未连接")
        self._conn_label.setStyleSheet(
            f"color: {Theme.readable_text(Theme.TEXT_MUTED)};"
            f" font-weight: bold;")
        layout.addWidget(self._conn_label)
        return group

    def _create_device_table(self) -> QTableWidget:
        """设备列表表：IP 绿色高亮；行选中自动回填 IP/端口。"""
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["设备名称", "IP 地址", "端口"])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.itemSelectionChanged.connect(self._restyle_device_rows)
        table.itemSelectionChanged.connect(self._on_device_selected)
        return table

    def _create_session_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels(
            ["日期", "会话名称", "通道数", "时长", "状态", "获取状态"])
        header = table.horizontalHeader()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.Stretch)
        for col in (self.COL_DATE, self.COL_CHANNELS, self.COL_DURATION,
                    self.COL_STATUS, self.COL_FETCH):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)   # 只读
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(180)
        table.itemSelectionChanged.connect(self._on_selection_changed)
        table.doubleClicked.connect(lambda _i: self._fetch_selected())
        return table

    # ── 服务端互斥 ─────────────────────────────────────────────

    def set_server_active(self, active: bool) -> None:
        """本机服务端服务运行中时禁用连接操作（角色硬互斥）。"""
        self._server_active = bool(active)
        for w in (self._scan_btn, self._connect_btn, self._ip_edit,
                  self._port_spin, self._device_table, self._fw_btn):
            w.setEnabled(not self._server_active)
        if self._server_active:
            self._status_label.setText(
                "本机处于服务端模式，请先停止服务再连接远程设备")

    # ── 扫描 ───────────────────────────────────────────────────

    def _start_scan(self):
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("搜索中...")
        self._scan_progress.setVisible(True)
        self._status_label.setText("正在搜索局域网设备…")

        self._retire(self._scan_thread)
        self._scan_thread = ScanThread(timeout=DEFAULT_SCAN_TIMEOUT)
        self._scan_thread.scan_finished.connect(self._on_scan_finished)
        self._scan_thread.scan_error.connect(self._on_scan_error)
        self._scan_thread.start()

    def _on_scan_finished(self, devices: List[Dict[str, Any]]):
        self._scan_btn.setEnabled(not self._server_active)
        self._scan_btn.setText("搜索设备")
        self._scan_progress.setVisible(False)
        self._apply_scan_results(devices)
        # 搜索完成交互：无论有无结果都弹窗展示列表供用户选择
        self._prompt_scan_results()

    def _apply_scan_results(self, devices: List[Dict[str, Any]]) -> None:
        """把搜索结果渲染到页面设备列表（不弹窗），并自动回填第一台。"""
        self._devices = list(devices or [])
        table = self._device_table
        table.setRowCount(len(self._devices))
        ip_color = _ip_highlight_color()
        for row, dev in enumerate(self._devices):
            name = QTableWidgetItem(str(dev.get("device_name") or "未知设备"))
            ip = QTableWidgetItem(str(dev.get("ip_address") or ""))
            ip.setForeground(QBrush(QColor(ip_color)))
            port = QTableWidgetItem(str(dev.get("tcp_port") or ""))
            table.setItem(row, 0, name)
            table.setItem(row, 1, ip)
            table.setItem(row, 2, port)
        self._set_device_table_height(len(self._devices))
        self._restyle_device_rows()
        if self._devices:
            table.selectRow(0)
            self._fill_from_row(0)
            self._status_label.setText(
                f"发现 {len(self._devices)} 台设备，已自动选择第一台，点「连接」")
        else:
            self._status_label.setText(
                "未发现设备：检查服务端服务已启动、防火墙已放行（UDP 9526），"
                "或手动输入 IP 连接")

    def _prompt_scan_results(self) -> None:
        """搜索完成后弹窗展示结果（有无结果都弹）；选中即回填并高亮列表行。"""
        if self._server_active:
            return
        dlg = ScanResultDialog(self._devices, self)
        dlg.exec_()
        if dlg.was_retry():
            self._start_scan()
            return
        chosen = dlg.chosen()
        if chosen is None or not self._devices:
            return  # 取消：保留 apply 阶段自动选中的第一台
        for i, dev in enumerate(self._devices):
            if (dev.get("device_name") == chosen.get("device_name")
                    and dev.get("ip_address") == chosen.get("ip_address")
                    and dev.get("tcp_port") == chosen.get("tcp_port")):
                self._device_table.selectRow(i)
                self._fill_from_row(i)
                break

    def _set_device_table_height(self, count: int) -> None:
        """设备表高度随行数自适应（1~5 台全展开，超出滚动）。"""
        row_h = self._device_table.verticalHeader().defaultSectionSize() or 26
        head_h = self._device_table.horizontalHeader().height() or 24
        h = head_h + max(count, 0) * row_h + 4
        h = max(head_h + 2 * row_h + 4, min(h, head_h + 5 * row_h + 4))
        self._device_table.setFixedHeight(h)

    def _on_device_selected(self):
        """设备列表行选中 → 回填 IP/端口（供连接或手动微调）。"""
        self._fill_from_row(self._device_table.currentRow())

    def _fill_from_row(self, row: int) -> None:
        if 0 <= row < len(self._devices):
            dev = self._devices[row]
            self._ip_edit.setText(str(dev.get("ip_address", "")))
            try:
                self._port_spin.setValue(
                    int(dev.get("tcp_port", DEFAULT_TCP_PORT)))
            except (TypeError, ValueError):
                self._port_spin.setValue(DEFAULT_TCP_PORT)

    def _restyle_device_rows(self, *_args) -> None:
        """IP 绿色高亮；选中行 IP 改用强调色前景保证选中态可读。"""
        ip_color = _ip_highlight_color()
        sel_fg = Theme.on_color_fg(Theme.EMPHASIS_FILL)
        table = self._device_table
        sel_model = table.selectionModel()
        selected = ({i.row() for i in sel_model.selectedRows()}
                    if sel_model is not None else set())
        for r in range(table.rowCount()):
            ip_item = table.item(r, 1)
            if ip_item is not None:
                ip_item.setForeground(
                    QBrush(QColor(sel_fg if r in selected
                                  else ip_color)))

    def _on_scan_error(self, error: str):
        self._scan_btn.setEnabled(not self._server_active)
        self._scan_btn.setText("搜索设备")
        self._scan_progress.setVisible(False)
        self._status_label.setText(f"搜索失败：{error}")
        QMessageBox.warning(self, "搜索失败", f"扫描局域网设备失败：\n{error}")

    # ── 连接 ───────────────────────────────────────────────────

    def _connect_target(self):
        ip = self._ip_edit.text().strip()
        if not ip:
            QMessageBox.warning(self, "输入错误", "请输入 IP 地址")
            return
        if self._server_active:
            QMessageBox.warning(
                self, "操作被阻止",
                "本机处于「服务端模式」（网络服务运行中），"
                "请先停止服务再连接远程设备。")
            return
        self._do_connect(ip, self._port_spin.value(), quiet=False)

    def _do_connect(self, ip: str, port: int, quiet: bool):
        self._connect_btn.setEnabled(False)
        self._scan_btn.setEnabled(False)
        self._status_label.setText(f"正在连接 {ip}:{port}…")

        self._retire(self._connect_thread)
        self._connect_thread = ConnectThread(ip, port)
        seq = self._connect_thread.seq
        self._connect_thread.connect_success.connect(
            lambda s, info, _q=quiet: self._on_connect_success(s, info, _q))
        self._connect_thread.connect_failed.connect(
            lambda s, err, _q=quiet: self._on_connect_failed(s, err, _q))
        self._connect_thread.start()

    def _on_connect_success(self, seq: int, device_info: Dict[str, Any],
                            quiet: bool):
        if (self._connect_thread is None
                or seq != self._connect_thread.seq):
            device_info.get('client') and device_info['client'].disconnect()
            return
        self._connect_btn.setEnabled(not self._server_active)
        self._scan_btn.setEnabled(not self._server_active)

        # 切换设备：断开旧连接
        self._disconnect_client()

        self._client = device_info.get('client')
        self._current = device_info
        self._last = {
            'ip': device_info.get('ip', ''),
            'port': device_info.get('port', ''),
            'device_name': device_info.get('device_name', ''),
        }
        ip, port = device_info.get('ip', ''), device_info.get('port', '')
        label = f"{ip}:{port}" if ip else '未知设备'
        self._conn_label.setText(f"当前设备：{label}（已连接）")
        self._conn_label.setStyleSheet(
            f"color: {Theme.readable_text(Theme.GREEN)}; font-weight: bold;")
        self._disconnect_btn.setEnabled(True)
        self._ip_edit.setText(ip)
        self._status_label.setText(f"已连接 {label}，正在获取会话列表…")

        self.connected.emit(device_info)
        self._refresh_sessions()

    def _on_connect_failed(self, seq: int, error: str, quiet: bool):
        if (self._connect_thread is None
                or seq != self._connect_thread.seq):
            return
        self._connect_btn.setEnabled(not self._server_active)
        self._scan_btn.setEnabled(not self._server_active)
        self._status_label.setText(f"连接失败：{error}")
        if not quiet:
            QMessageBox.warning(self, "连接失败",
                                f"无法连接到远程设备：\n{error}")

    def _disconnect(self):
        """手动断开当前连接（监控由主窗口管理，不受影响）。"""
        self._disconnect_client()
        self._conn_label.setText("当前设备：未连接")
        self._conn_label.setStyleSheet(
            f"color: {Theme.readable_text(Theme.TEXT_MUTED)};"
            f" font-weight: bold;")
        self._disconnect_btn.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._fetch_btn.setEnabled(False)
        self._status_label.setText("已断开连接")
        self.disconnected.emit()

    def _disconnect_client(self):
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._current = None

    # ── 会话列表 ───────────────────────────────────────────────

    def _refresh_sessions(self):
        if self._client is None:
            return
        self._refresh_btn.setEnabled(False)
        self._fetch_btn.setEnabled(False)
        self._status_label.setText("正在获取会话列表…")
        client = self._client
        self._retire(self._session_thread)
        self._session_thread = SessionListThread(client)
        self._session_thread.sessions_loaded.connect(
            lambda c, ss: self._on_sessions_loaded(c, ss))
        self._session_thread.session_error.connect(
            lambda c, err: self._on_session_error(c, err))
        self._session_thread.start()

    def _on_sessions_loaded(self, client, sessions: List[Dict[str, Any]]):
        if client is not self._client:
            return  # 切换设备后旧线程的迟到结果
        self._sessions = sessions
        self._session_table.setRowCount(len(sessions))
        for i, s in enumerate(sessions):
            started = s.get('started_at')
            date_str = (time.strftime('%Y-%m-%d %H:%M:%S',
                                     time.localtime(started))
                        if started else '未知时间')
            self._session_table.setItem(
                i, self.COL_DATE, QTableWidgetItem(date_str))
            self._session_table.setItem(
                i, self.COL_NAME,
                QTableWidgetItem(s.get('session_name')
                                 or s.get('session_id') or '未命名'))
            self._session_table.setItem(
                i, self.COL_CHANNELS,
                QTableWidgetItem(str(s.get('channel_count', ''))))
            dur = s.get('duration_seconds')
            dur_str = self._fmt_dur(dur) if dur else '—'
            self._session_table.setItem(
                i, self.COL_DURATION, QTableWidgetItem(dur_str))

            # 状态：远端正在采集（未写停止时间） vs 已完成
            # （前景色统一由 _restyle_session_rows 按当前主题/选中态着色）
            if s.get('stopped_at') is None:
                status = "● 采集中"
                tip = "远程仪器正在采集该会话：可实时监控（每分钟自动更新）"
            else:
                status = "已完成"
                tip = "远程库中已完结的历史会话：一次性获取完整数据"
            item = QTableWidgetItem(status)
            item.setToolTip(tip)
            self._session_table.setItem(i, self.COL_STATUS, item)

            fetch_item = QTableWidgetItem("未获取")
            fetch_item.setData(Qt.UserRole, "none")
            self._session_table.setItem(i, self.COL_FETCH, fetch_item)

        self._refresh_btn.setEnabled(True)
        if sessions:
            self._status_label.setText(
                f"共 {len(sessions)} 个会话：点选后点「获取数据」"
                f"（● 采集中=实时监控 / 已完成=一次性获取）")
        else:
            self._status_label.setText("当前设备没有历史会话")
        # 新表 item 构建时未着色，统一按当前主题重刷
        self._restyle_session_rows(force=True)

    def _restyle_session_rows(self, force: bool = False) -> None:
        """按当前主题与选中状态重刷会话表各列前景色。

        item 的 ForegroundRole 优先于 QSS 的 color 与选中态颜色，构建
        时固化的颜色在切主题后会停留旧值、选中行各列也会颜色混杂；
        这里按 _sessions 数据与「获取状态」列的 UserRole 状态重设：
        选中行所有列统一为补偿强调文字（配 1px ACCENT 选中边框，与本机会话
        树一致），未选中行按状态/获取结果语义色。
        与 HistoryDialog._session_item_colors 同一套交互契约：未选中
        行**全部列**都显式着色（非语义列回 Theme.TEXT），避免曾被选中
        着强调色的行取消选中后残留旧前景（取消后回语义色）。
        触发点：切主题（refresh_theme）、列表重建（_on_sessions_loaded）、
        选择变化（_on_selection_changed）与获取结果回写。
        """
        current_row = self._session_table.currentRow()
        if not force and current_row == self._last_restyled_row:
            return
        self._last_restyled_row = current_row
        if not self._sessions:
            return
        # 浅灰工业 深色部件时补偿底色换数据表深行底（白字系），其余主题对卡底
        face = Theme.table_face()
        surface = face["base"] if face else None
        fetch_colors = {"ok": Theme.readable_text(Theme.GREEN, surface),
                        "fail": Theme.readable_text(Theme.RED, surface),
                        "monitor": Theme.readable_text(Theme.GREEN, surface)}
        for i, s in enumerate(self._sessions):
            selected = i == current_row
            for col in range(self._session_table.columnCount()):
                item = self._session_table.item(i, col)
                if item is None:
                    continue
                if selected:
                    item.setForeground(QColor(
                        Theme.on_color_fg(Theme.EMPHASIS_FILL)))
                else:
                    color = Theme.readable_text(Theme.TEXT, surface)
                    if col == self.COL_NAME:
                        # 会话名称橙色：与 HistoryDialog 本机页「远程
                        # 会话名称橙色」标识统一（本页全部会话均来自
                        # 远程设备，整列统一为远程语义色）
                        color = Theme.readable_text(Theme.ORANGE, surface)
                    elif col == self.COL_STATUS:
                        color = (Theme.readable_text(Theme.ORANGE, surface)
                                 if s.get('stopped_at') is None
                                 else Theme.readable_text(Theme.TEXT_MUTED,
                                                          surface))
                    elif col == self.COL_FETCH:
                        state = item.data(Qt.UserRole)
                        color = fetch_colors.get(
                            state,
                            Theme.readable_text(Theme.TEXT_MUTED, surface))
                    item.setForeground(QColor(color))

    def _on_session_error(self, client, error: str):
        if client is not self._client:
            return
        self._refresh_btn.setEnabled(False)
        self._status_label.setText(f"获取会话列表失败：{error}")

    @staticmethod
    def _fmt_dur(sec) -> str:
        if sec is None or sec < 0:
            return "—"
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(round(sec % 60))
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _on_selection_changed(self):
        row = self._session_table.currentRow()
        # 选中行变化时重刷各列前景色（选中行统一反白，其余行回语义色）
        self._restyle_session_rows()
        self._fetch_btn.setEnabled(
            0 <= row < len(self._sessions) and self._client is not None)

    # ── 获取 / 监控 ────────────────────────────────────────────

    def _fetch_selected(self):
        row = self._session_table.currentRow()
        if row < 0 or row >= len(self._sessions):
            return
        info = self._sessions[row]
        monitor = info.get('stopped_at') is None
        name = info.get('session_name') or info.get('session_id')
        self._status_label.setText(
            f"正在{'监控' if monitor else '获取'}会话「{name}」…")
        self.fetch_requested.emit(info, monitor)

    # ── 主窗口回写 ─────────────────────────────────────────────

    def set_fetch_result(self, session_id: str, ok: bool,
                         hint: str = "") -> None:
        """获取/监控启动结果回写：更新会话行「获取状态」列。"""
        for i, s in enumerate(self._sessions):
            if s.get('session_id') == session_id:
                if ok:
                    text, state = "已获取", "ok"
                else:
                    text, state = "获取失败", "fail"
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, state)
                self._session_table.setItem(i, self.COL_FETCH, item)
                break
        if hint:
            self._status_label.setText(hint)
        # 新 item 未着色，按当前主题/选中态重刷
        self._restyle_session_rows(force=True)

    def set_monitor_active(self, active: bool,
                           session_id: str = "") -> None:
        """监控状态回写：会话行标记「监控中」，启停「停止监控」按钮。"""
        self._monitor_session_id = session_id if active else ''
        self._stop_monitor_btn.setEnabled(active)
        for i, s in enumerate(self._sessions):
            if s.get('session_id') == session_id:
                if active:
                    item = QTableWidgetItem("● 监控中")
                    item.setData(Qt.UserRole, "monitor")
                    item.setToolTip("每分钟自动更新；结束后保存到本地历史")
                    self._session_table.setItem(i, self.COL_FETCH, item)
                break
        # 新 item 未着色，按当前主题/选中态重刷
        self._restyle_session_rows(force=True)

    # ── 重开自动重连 ───────────────────────────────────────────

    def maybe_reconnect(self) -> None:
        """对话框重新打开时自动重连上次设备（静默失败）。"""
        if self._server_active or self._client is not None:
            return
        conn = self._last
        if not conn or not conn.get('ip'):
            return
        self._status_label.setText(
            f"正在自动连接上次设备 {conn.get('ip')}:{conn.get('port')}…")
        self._do_connect(conn['ip'], int(conn['port']), quiet=True)

    # ── 线程管理 ───────────────────────────────────────────────

    def _retire(self, thread) -> None:
        """退役线程保活（防 QThread 运行中被 GC 销毁导致 Qt abort）。"""
        if thread is None:
            return
        self._retired_threads.append(thread)
        thread.finished.connect(
            lambda t=thread: self._retired_threads.remove(t)
            if t in self._retired_threads else None)

    def shutdown(self) -> None:
        """释放线程；连接保留（由主窗口/用户管理）。"""
        for t in [self._scan_thread, self._connect_thread,
                  self._session_thread, self._probe_thread,
                  *self._retired_threads]:
            if t is not None and t.isRunning():
                t.quit()
                t.wait(1000)
