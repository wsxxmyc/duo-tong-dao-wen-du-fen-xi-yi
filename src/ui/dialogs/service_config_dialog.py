# -*- coding: utf-8 -*-
"""
ServiceConfigDialog — 服务配置对话框

提供多模式网络服务的配置、状态显示和诊断功能。

「服务 → 对外网卡」业务规则（实现依据，改动前务必同步）：
- 只有依赖 UDP 广播做局域网发现的服务才消费对外网卡：TCP Socket 服务
  （DataServer 附带 UDPBroadcaster）与配对码服务（自身即 UDP 广播）。
- HTTP REST API 绑定 0.0.0.0 且没有发现机制，对外网卡对其不生效，
  任意本机地址的该端口都可访问。
- advertise_ip 是 ServiceManager 的单一全局值：TCP 与配对码共用同一张
  网卡，不允许两个广播服务通告不同地址（否则客户端会扫到同一台设备的
  两个地址）。
- 合法取值 = 「自动（默认出口 IP）」或当前本机可通告 IPv4；回环、
  链路本地（169.254.x.x）、未指定地址一律不可选。
"""

from __future__ import annotations

import socket
import time

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
    QLabel, QGroupBox, QCheckBox, QSpinBox, QMessageBox, QComboBox,
    QWidget, QSizePolicy,
)
from PyQt5 import sip

from ui.theme import Theme
from utils.config_io import ConfigIO
from device.network.netutil import get_local_ips, subnet_broadcast_addresses
from ui.dialogs import firewall_helper


# 消费「对外网卡」的服务：勾选其中任意一个，网卡选择才生效
NIC_CONSUMER_SERVICES = (("tcp", "TCP"), ("pairing_code", "配对码"))

# 固定端口说明（配对码服务端口不可配，与其端口输入框位置对齐展示）
PAIRING_PORT_TEXT = "UDP 9526（固定）"


def _is_advertisable_ip(ip: str) -> bool:
    """是否为可作为对外通告地址的本机 IPv4。

    回环、未指定地址与链路本地地址（169.254.x.x，多为无 DHCP 时的自动
    私有地址）客户端不可达，不能作为通告地址。
    """
    if not ip or ip in ("127.0.0.1", "0.0.0.0"):
        return False
    return not ip.startswith("169.254.")


def advertisable_ips() -> list:
    """当前可作为对外通告地址的本机 IPv4 列表（默认路由出口优先）。"""
    try:
        ips = get_local_ips()
    except Exception:
        return []
    return [ip for ip in ips if _is_advertisable_ip(ip)]


class ServiceConfigPage(QWidget):
    """服务配置页

    功能：
    - 服务模式选择（TCP、HTTP、配对码）
    - 对外网卡选择（仅 TCP / 配对码消费，HTTP 不参与局域网发现）
    - 服务状态显示（运行状态、端口、错误信息）
    - 服务启动/停止控制
    - 服务诊断功能
    - 连接信息查看（可鼠标选中复制）

    可嵌入「数据」弹窗服务页（ui/dialogs/history_dialog.py），也可通过
    ServiceConfigDialog 独立使用。
    """

    # 信号：服务状态变更
    service_changed = pyqtSignal()

    def __init__(self, parent=None, show_footer: bool = True):
        super().__init__(parent)
        self._service_manager = None
        # 最近一次防火墙检测的状态色（切主题重设样式时不丢失检测结果色）
        self._fw_last_color = None
        # 程序化同步勾选框/端口期间屏蔽「勾选即启停」逻辑，避免加载配置时误启动服务
        self._updating_ui = False
        # 客户端模式标志：已连接/加载远程设备数据时禁止启用网络服务（硬互斥）
        self._client_active = False
        # 嵌入统一网络服务窗口时隐藏本页底部队列（由外层窗口统一提供）
        self._show_footer = show_footer

        # 对外网卡：可选地址缓存（检测运行中网卡变化）与被自动回退的旧地址
        self._local_ips_cache: list = []
        self._nic_correction: str = None
        # 分组标题与字段标签（切主题时统一重设颜色）
        self._section_labels: list = []
        self._field_labels: list = []
        self._value_labels: list = []

        # 服务状态检查定时器
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)  # 1 秒刷新一次
        self._status_timer.timeout.connect(self._update_service_status)

        # 网卡列表变化检测定时器（15 秒）：插拔网线 / 连 VPN / 网卡禁用后
        # 自动过滤失效地址并把已失效的通告地址回退「自动」，避免脏数据
        self._nic_timer = QTimer(self)
        self._nic_timer.setInterval(15000)
        self._nic_timer.timeout.connect(self._refresh_nic_list_if_changed)

        self._init_ui()
        self._status_timer.start()
        self._nic_timer.start()
        # 首帧渲染完成后自动检测一次防火墙（netsh 查询约 1~3 秒，不阻塞首屏）；
        # 定时器挂在 self 下，页销毁时随之销毁，避免回调访问已删除控件
        self._fw_auto_timer = QTimer(self)
        self._fw_auto_timer.setSingleShot(True)
        self._fw_auto_timer.setInterval(500)
        self._fw_auto_timer.timeout.connect(self._refresh_firewall_status_only)
        self._fw_auto_timer.start()

    def _init_ui(self):
        """初始化界面"""
        # 页对象标识（样式表定位背景）
        self.setObjectName("serviceConfigPage")

        # 主布局（精简：收紧外边距与间距，页签标题已表明用途，不再重复页面大标题）
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 服务配置区域（服务开关 + 对外网卡）
        config_group = self._create_config_group()
        layout.addWidget(config_group)

        # 载入上次持久化的服务配置（勾选 + 端口 + 对外网卡）
        self._load_persisted_config()

        # 连接信息区域（本机地址 / 服务地址 / 广播状态 / 防火墙）
        info_group = self._create_info_group()
        layout.addWidget(info_group)

        # 按钮区域（嵌入统一窗口时隐藏，由外层统一提供）
        if self._show_footer:
            button_layout = self._create_button_layout()
            layout.addLayout(button_layout)

        # 应用主题（局部样式；主题切换后由 refresh_theme 重新应用）
        self.refresh_theme()

    # ── 样式 ───────────────────────────────────────────────────

    def _style_client_mode_banner(self):
        """互斥提示条样式（随主题切换重设）。文字经 readable_text 对卡底
        补偿（工业中灰上提亮），边框保持状态色 1px。"""
        self._client_mode_banner.setStyleSheet(f"""
            color: {Theme.readable_text(Theme.ORANGE)};
            background: {Theme.BG_CARD};
            border: 1px solid {Theme.ORANGE};
            border-radius: 3px;
            padding: 6px;
        """)

    @staticmethod
    def _style_alert_label(label: QLabel):
        """校验提示条样式（随主题切换重设）：橙色警示文字 + 卡底衬底。"""
        label.setStyleSheet(f"""
            color: {Theme.readable_text(Theme.ORANGE)};
            background: {Theme.BG_CARD};
            border: 1px solid {Theme.ORANGE};
            border-radius: 3px;
            padding: 5px;
        """)

    def refresh_theme(self) -> None:
        """主题切换后按新主题重新应用本页局部样式。

        通用控件（按钮/勾选框/端口输入/下拉框/标签文字）的样式全部由
        全局 QSS 提供，随 app.setStyleSheet 重设自动跟随主题；本页只
        保留两类局部样式，且都必须随切主题重设：
        1. QGroupBox 分区容器——全局 QSS 未覆盖，group_box_qss() 按
           当前主题生成，不重设会停留旧主题色；
        2. 语义色标签——互斥横幅/校验提示/状态文字用颜色表达业务语义
           （警告橙/成功绿/弱化灰），QSS 无法按业务状态着色，集中在此
           按当前主题重设，构造与切主题共用一套逻辑。
        信息层级固定为：分组标题（TEXT 加粗）> 值（TEXT）> 字段名与提示
        （TEXT_MUTED）> 校验提示（橙）。
        """
        self.setStyleSheet(Theme.group_box_qss())
        self._style_client_mode_banner()
        self._style_alert_label(self._nic_alert)
        self._style_alert_label(self._conn_alert)
        for lbl in self._section_labels:
            lbl.setStyleSheet(f"font-weight:bold; color: {Theme.TEXT};")
        for lbl in self._field_labels:
            lbl.setStyleSheet(f"color: {Theme.TEXT_MUTED};")
        for lbl in self._value_labels:
            lbl.setStyleSheet(f"color: {Theme.TEXT};")
        for lbl in (self._nic_hint, self._nic_scope_hint,
                    self._broadcast_status_label, self._http_note_label):
            lbl.setStyleSheet(f"color: {Theme.TEXT_MUTED};")
        # 防火墙状态色：已检测过按检测结果（绿/红），否则弱化灰；
        # 文字色统一经 readable_text 对卡底补偿
        fw_color = self._fw_last_color or Theme.TEXT_MUTED
        self._firewall_status_label.setStyleSheet(
            f"color: {Theme.readable_text(fw_color)};")
        # 服务状态标签按当前运行状态重设语义色（1 秒定时器平时也会刷；
        # 定时器随窗口关闭停止，此处兜底保证重开/切主题后颜色正确；
        # 管理器尚未注入时统一弱化灰）
        if self._service_manager is not None:
            self._update_service_status()
        else:
            for lbl in (self._tcp_status_label, self._http_status_label,
                        self._pairing_status_label):
                lbl.setStyleSheet(f"color: {Theme.TEXT_MUTED};")

    # ── 界面构建 ───────────────────────────────────────────────

    def _section_label(self, text: str) -> QLabel:
        """分组标题（加粗正文色，弱化灰之上一个层级）。"""
        lbl = QLabel(text)
        lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._section_labels.append(lbl)
        return lbl

    def _field_label(self, text: str, align_right: bool = True) -> QLabel:
        """字段名标签（弱化灰，与值形成主次层级）。"""
        lbl = QLabel(text)
        if align_right:
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._field_labels.append(lbl)
        return lbl

    def _create_config_group(self) -> QGroupBox:
        """创建「服务模式」区域。

        单个网格统一四列对齐（名称 / 端口标签 / 端口或网卡控件 / 状态），
        分组标题以跨列行插入，保证服务行与网卡行的控件左边缘严格对齐。
        """
        group = QGroupBox("服务模式")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # 客户端模式互斥提示条（默认隐藏）
        self._client_mode_banner = QLabel(
            "⚠ 本机处于「客户端模式」：已从远程设备加载数据。\n"
            "请先打开本地文件或开始采集返回本地模式，再启用网络服务。")
        self._client_mode_banner.setWordWrap(True)
        self._style_client_mode_banner()
        self._client_mode_banner.setVisible(False)
        layout.addWidget(self._client_mode_banner)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        grid.setContentsMargins(2, 0, 2, 0)

        # 列宽：0 名称 / 1 端口标签 / 2 端口或网卡控件 / 3 状态（右侧吸收余量）
        fm = self.fontMetrics()
        grid.setColumnMinimumWidth(0, max(
            fm.horizontalAdvance("HTTP REST API 服务") + 26,
            fm.horizontalAdvance("对外网卡") + 6))
        grid.setColumnMinimumWidth(1, fm.horizontalAdvance("端口：") + 4)
        grid.setColumnMinimumWidth(2, 196)
        grid.setColumnStretch(3, 1)

        row = 0
        # ── 分组：服务开关 ──
        grid.addWidget(self._section_label("服务开关"), row, 0, 1, 4)
        row += 1

        # TCP 服务：勾选 + 端口 + 同行内联状态
        self._tcp_check = QCheckBox("TCP Socket 服务")
        self._tcp_check.setToolTip("启用 TCP Socket 服务，支持设备连接和数据传输（勾选即启动）")
        self._tcp_check.toggled.connect(self._on_tcp_toggled)
        self._tcp_check.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        grid.addWidget(self._tcp_check, row, 0, Qt.AlignLeft)

        grid.addWidget(self._field_label("端口："), row, 1)
        self._tcp_port = QSpinBox()
        self._tcp_port.setRange(1024, 65535)
        self._tcp_port.setValue(9527)
        self._tcp_port.setFixedWidth(72)
        self._tcp_port.valueChanged.connect(lambda _v: self._save_persisted_config())
        grid.addWidget(self._tcp_port, row, 2, Qt.AlignLeft)

        self._tcp_status_label = QLabel("已停止")
        # 预留运行中状态的最长文本宽度，保证行宽不随状态变化而抖动
        self._reserve_status_min_width(
            self._tcp_status_label, "● 运行中 端口 65535")
        grid.addWidget(self._tcp_status_label, row, 3, Qt.AlignLeft)
        row += 1

        # HTTP 服务：勾选 + 端口 + 同行内联状态
        self._http_check = QCheckBox("HTTP REST API 服务")
        self._http_check.setToolTip("启用 HTTP REST API 服务，提供 Web 接口（勾选即启动）")
        self._http_check.toggled.connect(self._on_http_toggled)
        self._http_check.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        grid.addWidget(self._http_check, row, 0, Qt.AlignLeft)

        grid.addWidget(self._field_label("端口："), row, 1)
        self._http_port = QSpinBox()
        self._http_port.setRange(1024, 65535)
        self._http_port.setValue(8080)
        self._http_port.setFixedWidth(72)
        self._http_port.valueChanged.connect(lambda _v: self._save_persisted_config())
        grid.addWidget(self._http_port, row, 2, Qt.AlignLeft)

        self._http_status_label = QLabel("已停止")
        self._reserve_status_min_width(
            self._http_status_label, "● 运行中 端口 65535")
        grid.addWidget(self._http_status_label, row, 3, Qt.AlignLeft)
        row += 1

        # 配对码服务：勾选 + 固定端口说明（与端口输入框同列对齐）+ 状态
        self._pairing_check = QCheckBox("配对码服务")
        self._pairing_check.setToolTip("启用配对码服务，通过配对码快速连接（勾选即启动）")
        self._pairing_check.toggled.connect(self._on_pairing_toggled)
        self._pairing_check.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        grid.addWidget(self._pairing_check, row, 0, Qt.AlignLeft)

        grid.addWidget(self._field_label("端口："), row, 1)
        pairing_port_label = QLabel(PAIRING_PORT_TEXT)
        pairing_port_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._field_labels.append(pairing_port_label)
        grid.addWidget(pairing_port_label, row, 2, Qt.AlignLeft)

        self._pairing_status_label = QLabel("已停止")
        self._reserve_status_min_width(
            self._pairing_status_label, "配对码 9999 · 运行中")
        grid.addWidget(self._pairing_status_label, row, 3, Qt.AlignLeft)
        row += 1

        # ── 分组：对外网卡 ──
        nic_header = self._section_label("对外网卡")
        grid.addWidget(nic_header, row, 0, 1, 2)
        self._nic_scope_hint = QLabel("仅 TCP／配对码的局域网发现需要")
        grid.addWidget(self._nic_scope_hint, row, 2, 1, 2, Qt.AlignLeft)
        row += 1

        grid.addWidget(self._field_label("网卡地址"), row, 0)
        self._nic_combo = QComboBox()
        self._nic_combo.setMinimumWidth(190)
        self._nic_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._nic_combo.setToolTip(
            "选择对外通告的网卡 IP：「自动」按默认路由出口通告，多网卡时客户端"
            "扫描可能出现多个地址；指定网卡后只通告该地址")
        self._nic_combo.currentIndexChanged.connect(
            self._on_advertise_option_changed)
        grid.addWidget(self._nic_combo, row, 2, Qt.AlignLeft)

        self._nic_effect_label = QLabel("")
        self._nic_effect_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._field_labels.append(self._nic_effect_label)
        grid.addWidget(self._nic_effect_label, row, 3, Qt.AlignLeft)
        row += 1

        # 网卡校正提示条（默认隐藏；地址失效自动回退时出现）
        self._nic_alert = QLabel("")
        self._nic_alert.setWordWrap(True)
        self._style_alert_label(self._nic_alert)
        self._nic_alert.setVisible(False)
        grid.addWidget(self._nic_alert, row, 0, 1, 4)
        row += 1

        # 网卡作用说明（随生效服务与所选地址变化，始终弱化灰）
        self._nic_hint = QLabel("")
        self._nic_hint.setWordWrap(True)
        self._nic_hint.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grid.addWidget(self._nic_hint, row, 0, 1, 4)

        layout.addLayout(grid)
        return group

    def _reserve_status_min_width(self, label: QLabel, longest: str) -> None:
        """按最长状态文本预留标签最小宽度，使状态变化时行宽保持稳定。

        同时约束最大宽度（取预留宽度），防止异常长文本（如错误详情）
        撑开布局导致弹窗内容变形。
        """
        fm = label.fontMetrics()
        reserved = fm.horizontalAdvance(longest) + 12
        label.setMinimumWidth(reserved)
        label.setMaximumWidth(reserved)

    def _create_info_group(self) -> QGroupBox:
        """创建「连接信息」区域。

        按业务语义分四块：本机地址（通告地址 / 其他地址）、服务地址
        （运行时逐行显示实际端口）、局域网广播、防火墙。名称列统一右对齐
        等宽，值可选中复制。
        """
        group = QGroupBox("连接信息")
        layout = QVBoxLayout(group)
        layout.setSpacing(5)

        # 校验提示条（网卡回退 / HTTP 多网卡说明；默认隐藏）
        self._conn_alert = QLabel("")
        self._conn_alert.setWordWrap(True)
        self._style_alert_label(self._conn_alert)
        self._conn_alert.setVisible(False)
        layout.addWidget(self._conn_alert)

        # 两个信息网格共用同一名称列宽度，保证上下对齐
        fm = self.fontMetrics()
        name_width = max(fm.horizontalAdvance(t) for t in
                         ("通告地址", "其他地址", "TCP", "HTTP", "配对码")) + 8

        # ── 分组：本机地址 ──
        layout.addWidget(self._section_label("本机地址"))
        host_grid = QGridLayout()
        host_grid.setHorizontalSpacing(8)
        host_grid.setVerticalSpacing(4)
        host_grid.setContentsMargins(2, 0, 2, 0)
        host_grid.setColumnMinimumWidth(0, name_width)
        host_grid.setColumnStretch(1, 1)
        _host_name, self._advertise_value = self._add_info_row(
            host_grid, 0, "通告地址")
        _other_name, self._other_ips_value = self._add_info_row(
            host_grid, 1, "其他地址")
        # 构造时缓存已就绪（_load_persisted_config 先于本方法执行）
        self._other_ips_value.setText(
            "、".join(self._local_ips_cache) or "无其他可通告地址")
        layout.addLayout(host_grid)

        # ── 分组：服务地址 ──
        layout.addWidget(self._section_label("服务地址"))
        addr_grid = QGridLayout()
        addr_grid.setHorizontalSpacing(8)
        addr_grid.setVerticalSpacing(4)
        addr_grid.setContentsMargins(2, 0, 2, 0)
        addr_grid.setColumnMinimumWidth(0, name_width)
        addr_grid.setColumnStretch(1, 1)
        self._tcp_addr_row = self._add_info_row(addr_grid, 0, "TCP")
        self._http_addr_row = self._add_info_row(addr_grid, 1, "HTTP")
        self._pairing_addr_row = self._add_info_row(addr_grid, 2, "配对码")
        self._idle_addr_label = QLabel("未启用任何服务")
        self._idle_addr_label.setWordWrap(True)
        self._field_labels.append(self._idle_addr_label)
        addr_grid.addWidget(self._idle_addr_label, 3, 0, 1, 2)
        layout.addLayout(addr_grid)
        # 每行成对放入（名称, 值），供显隐控制；初始无服务运行 → 隐藏
        self._addr_rows = (self._tcp_addr_row, self._http_addr_row,
                           self._pairing_addr_row)
        for row in self._addr_rows:
            row[0].setVisible(False)
            row[1].setVisible(False)

        self._http_note_label = QLabel(
            "HTTP 监听全部网卡，其余本机地址同样可访问")
        self._http_note_label.setWordWrap(True)
        self._http_note_label.setVisible(False)
        layout.addWidget(self._http_note_label)

        # ── 分组：局域网广播 ──
        layout.addWidget(self._section_label("局域网广播"))
        self._broadcast_status_label = QLabel("无广播服务运行")
        self._broadcast_status_label.setWordWrap(True)
        layout.addWidget(self._broadcast_status_label)

        # ── 分组：防火墙（状态与操作同行，压低块高）──
        layout.addWidget(self._section_label("防火墙"))
        fw_row = QHBoxLayout()
        fw_row.setSpacing(8)
        self._firewall_status_label = QLabel("防火墙：未检测")
        self._firewall_status_label.setWordWrap(True)
        fw_row.addWidget(self._firewall_status_label, 1)
        self._fw_check_btn = QPushButton("检查防火墙")
        self._fw_check_btn.setToolTip("检测 Windows 防火墙是否阻止本程序的局域网入站连接")
        self._fw_check_btn.clicked.connect(self._on_firewall_check_clicked)
        fw_row.addWidget(self._fw_check_btn)
        self._fw_fix_btn = QPushButton("一键放行")
        self._fw_fix_btn.setToolTip("删除本程序的防火墙阻止规则并添加允许规则（需要管理员授权）")
        self._fw_fix_btn.clicked.connect(self._on_firewall_fix_clicked)
        fw_row.addWidget(self._fw_fix_btn)
        layout.addLayout(fw_row)

        return group

    def _add_info_row(self, grid: QGridLayout, row: int, name: str):
        """信息行：名称右对齐固定宽 + 值左对齐可选中复制。

        返回:
            tuple: (名称标签, 值标签)；调用方按业务状态控制整行显隐。
        """
        name_lbl = self._field_label(name)
        value_lbl = QLabel("—")
        value_lbl.setWordWrap(True)
        value_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._value_labels.append(value_lbl)
        grid.addWidget(name_lbl, row, 0)
        grid.addWidget(value_lbl, row, 1)
        return name_lbl, value_lbl

    def _create_button_layout(self) -> QHBoxLayout:
        """创建按钮布局"""
        layout = QHBoxLayout()
        layout.addStretch()

        # 诊断按钮
        diagnose_btn = QPushButton("诊断")
        diagnose_btn.clicked.connect(self._diagnose_services)
        layout.addWidget(diagnose_btn)

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        return layout

    # ── 服务控制 ───────────────────────────────────────────────

    def set_service_manager(self, manager) -> None:
        """设置服务管理器（仅同步界面，不自动启动服务）"""
        self._service_manager = manager
        self._load_persisted_config()
        self._update_service_status()

    def set_client_active(self, active: bool) -> None:
        """客户端模式互斥：已连接/加载远程数据时禁止启用网络服务。

        服务端（运行服务）与客户端（连接远程）硬互斥，
        见「数据」弹窗服务页（dialogs/history_dialog.py）与
        main_window._open_history / _sync_client_mode_banner。
        """
        self._client_active = bool(active)
        if self._client_mode_banner is not None:
            self._client_mode_banner.setVisible(self._client_active)
        # 客户端模式下禁止启停服务（正常流程下此时不应有服务在运行）
        self._tcp_check.setEnabled(not self._client_active)
        self._http_check.setEnabled(not self._client_active)
        self._pairing_check.setEnabled(not self._client_active)
        self._refresh_advertise_scope()

    def refresh_after_external_change(self) -> None:
        """服务被外部停止（如「连接远程」页一键停止全部服务）后同步界面。"""
        self._save_persisted_config()
        self._update_service_status()
        self.service_changed.emit()

    def _on_tcp_toggled(self, enabled: bool):
        """TCP 服务切换：勾选即启动，取消勾选即停止。"""
        self._tcp_port.setEnabled(not enabled)
        self._refresh_advertise_scope()
        if self._updating_ui or self._service_manager is None:
            return
        self._apply_service_switch('tcp', enabled, self._tcp_port.value())

    def _on_http_toggled(self, enabled: bool):
        """HTTP 服务切换：勾选即启动，取消勾选即停止。"""
        self._http_port.setEnabled(not enabled)
        self._refresh_advertise_scope()
        if self._updating_ui or self._service_manager is None:
            return
        self._apply_service_switch('http', enabled, self._http_port.value())

    def _on_pairing_toggled(self, enabled: bool):
        """配对码服务切换：勾选即启动，取消勾选即停止。"""
        self._refresh_advertise_scope()
        if self._updating_ui or self._service_manager is None:
            return
        self._apply_service_switch('pairing_code', enabled)

    def _apply_service_switch(self, service_type: str, enabled: bool,
                              port: int = None) -> None:
        """立即启停指定服务并同步界面、持久化配置。

        启停为 UI 线程同步调用（绑定端口偶发卡顿）：等待光标给出即时
        反馈，成功后 _update_service_status 立即同步勾选态。
        """
        name_map = {'tcp': 'TCP', 'http': 'HTTP', 'pairing_code': '配对码'}
        display = name_map.get(service_type, service_type)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if enabled:
                ok = self._service_manager.start_service(service_type, port)
                if not ok:
                    QApplication.restoreOverrideCursor()
                    status = self._service_manager.get_service_status(service_type)
                    QMessageBox.warning(
                        self, "服务启动失败",
                        f"{display} 服务启动失败：\n{status.error or '未知错误'}\n"
                        f"请检查端口占用或防火墙设置。")
                    self._update_service_status()  # 勾选框回退为未勾选
                    return
            else:
                self._service_manager.stop_service(service_type)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "服务操作失败", f"{display} 服务操作异常：\n{str(e)}")
            self._update_service_status()
            return
        QApplication.restoreOverrideCursor()

        # 同步界面、持久化、通知主窗口
        self._update_service_status()
        self._save_persisted_config()
        self.service_changed.emit()

    def _load_persisted_config(self):
        """读取上次持久化的服务配置并同步到勾选、端口与对外网卡控件。

        仅在首次初始化/注入管理器时加载一次，不触发启停。
        """
        self._updating_ui = True
        try:
            cfg = ConfigIO.load_network_service_config()
            self._tcp_check.setChecked(bool(cfg.get("tcp_enabled", True)))
            self._tcp_port.setValue(int(cfg.get("tcp_port", 9527)))
            self._http_check.setChecked(bool(cfg.get("http_enabled", False)))
            self._http_port.setValue(int(cfg.get("http_port", 8080)))
            self._pairing_check.setChecked(bool(cfg.get("pairing_enabled", False)))
            self._rebuild_advertise_options(cfg.get("advertise_ip", "") or "")
        except Exception:
            pass
        finally:
            self._updating_ui = False
        # 同步端口可编辑状态（勾选时端口锁定，需先取消勾选再改端口）
        self._tcp_port.setEnabled(not self._tcp_check.isChecked())
        self._http_port.setEnabled(not self._http_check.isChecked())
        self._refresh_advertise_scope()

    # ── 对外网卡：可选项、联动与自动校正 ───────────────────────

    def _active_nic_consumers(self) -> list:
        """当前已勾选、且消费对外网卡的服务显示名列表。

        HTTP 绑定 0.0.0.0、无发现机制，不消费对外网卡；因此只有 TCP 与
        配对码勾选时网卡选择才有意义。
        """
        checked = {
            'tcp': self._tcp_check.isChecked(),
            'pairing_code': self._pairing_check.isChecked(),
        }
        return [display for key, display in NIC_CONSUMER_SERVICES
                if checked.get(key)]

    def _rebuild_advertise_options(self, advertise_ip: str = "") -> None:
        """重建「对外网卡」下拉选项：自动（默认出口）+ 当前可通告的本机 IP。

        不在本机网卡列表的地址（网卡禁用 / 换网 / IP 变化）直接过滤掉，
        已持久化的地址失效时自动回退「自动」，并通过提示条告知，避免
        界面显示一个实际不会生效的地址。
        """
        ips = advertisable_ips()
        self._local_ips_cache = ips

        correction = None
        if advertise_ip and advertise_ip not in ips:
            correction = advertise_ip
            advertise_ip = ""

        self._updating_ui = True
        try:
            self._nic_combo.clear()
            self._nic_combo.addItem("自动（默认出口 IP）", "")
            self._nic_combo.setItemData(
                0, "按默认路由出口网卡通告；多网卡时客户端扫描可能出现多个地址",
                Qt.ToolTipRole)
            for ip in ips:
                idx = self._nic_combo.count()
                self._nic_combo.addItem(ip, ip)
                self._nic_combo.setItemData(
                    idx, f"仅通告该网卡 IP：{ip}，客户端扫描列表只显示此地址",
                    Qt.ToolTipRole)
            target = self._nic_combo.findData(advertise_ip)
            self._nic_combo.setCurrentIndex(target if target >= 0 else 0)
        finally:
            self._updating_ui = False

        self._nic_correction = correction
        self._refresh_advertise_scope()

    def _refresh_advertise_scope(self) -> None:
        """按当前勾选的服务刷新网卡控件可用性与说明文字。

        无消费方（只勾 HTTP 或全不勾）时禁用下拉但保留已选值——用户重新
        勾回 TCP／配对码后立即恢复生效，不产生配置丢失。
        """
        consumers = self._active_nic_consumers()
        enabled = bool(consumers) and not self._client_active
        self._nic_combo.setEnabled(enabled)

        ip = self.advertise_ip()
        if self._client_active:
            self._nic_effect_label.setText("客户端模式下已停用")
        elif not consumers:
            self._nic_effect_label.setText("当前不生效")
        else:
            self._nic_effect_label.setText(
                f"{'、'.join(consumers)} 生效")

        if not consumers:
            self._nic_hint.setText(
                "当前未启用 TCP／配对码服务：HTTP 监听全部网卡、不参与局域网"
                "发现，网卡选择暂不生效（已选值保留）。")
        elif ip:
            self._nic_hint.setText(
                f"仅向 {ip} 所在网段广播并通告该地址；其余网段的客户端需在"
                f"「远程数据」页手动输入 IP 连接。")
        else:
            self._nic_hint.setText(
                "自动模式：通告本机全部可通告 IP，客户端扫描可能出现多个地址；"
                "多网卡机器建议指定一张网卡。")

        if self._nic_correction:
            self._nic_alert.setText(
                f"⚠ 原通告地址 {self._nic_correction} 已不在本机网卡列表中"
                f"（网卡变更 / 禁用 / 换网），已自动回退「自动」。")
            self._nic_alert.setVisible(True)
        else:
            self._nic_alert.setVisible(False)

    def _refresh_nic_list_if_changed(self) -> None:
        """检测本机网卡变化并重建选项（15 秒一次，仅页面可见时执行）。

        插拔网线、连接 VPN、网卡禁用等场景下，已选通告地址可能失效；
        失效时自动回退「自动」并把新地址应用到运行中的服务，避免广播
        静默失效产生的脏数据。
        """
        if not self.isVisible():
            return
        ips = advertisable_ips()
        if ips == self._local_ips_cache:
            return
        self._rebuild_advertise_options(self.advertise_ip())
        if self._service_manager is not None:
            self._apply_advertise_ip_to_services(silent=True)

    def showEvent(self, event) -> None:
        """页面再次显示时立即复核网卡列表（定时器仅在可见时刷新）。"""
        super().showEvent(event)
        try:
            self._refresh_nic_list_if_changed()
        except Exception:
            pass

    def advertise_ip(self) -> str:
        """当前选中的对外通告 IP（空串 = 自动 / 默认出口）。"""
        idx = self._nic_combo.currentIndex()
        if idx < 0:
            return ""
        return self._nic_combo.itemData(idx) or ""

    def select_advertise_ip(self, ip: str) -> bool:
        """程序化选择指定通告 IP（空串 = 自动）。返回是否命中可选项。"""
        idx = self._nic_combo.findData(ip or "")
        if idx < 0:
            return False
        self._nic_combo.setCurrentIndex(idx)
        return True

    def _on_advertise_option_changed(self, _index: int) -> None:
        """对外网卡选择变更：持久化并应用到运行中的服务。"""
        if self._updating_ui:
            return
        self._nic_correction = None   # 用户已重新选择，清除失效提示
        ip = self.advertise_ip()
        self._refresh_advertise_scope()
        self._save_persisted_config()
        self._apply_advertise_ip_to_services(silent=False)

    def _apply_advertise_ip_to_services(self, silent: bool = False) -> None:
        """把当前通告地址应用到服务管理器，并按需要重启 TCP 广播。

        参数:
            silent: True 表示由网卡自动检测触发（不提示重启失败以外的问题）。
        """
        if self._service_manager is None:
            return
        ip = self.advertise_ip()
        self._service_manager.advertise_ip = ip
        # 广播类服务运行中：重启使其按新网卡通告（其他服务下次启动生效）
        restarted = []
        try:
            for key, display in NIC_CONSUMER_SERVICES:
                status = self._service_manager.get_service_status(key)
                if not status.enabled:
                    continue
                self._service_manager.stop_service(key)
                ok = self._service_manager.start_service(key, status.port)
                if not ok:
                    new_status = self._service_manager.get_service_status(key)
                    QMessageBox.warning(
                        self, "服务重启失败",
                        f"对外网卡变更后重启 {display} 服务失败：\n"
                        f"{new_status.error or '未知错误'}")
                else:
                    restarted.append(display)
        except Exception:
            pass
        self._update_service_status()
        if not silent or restarted:
            self.service_changed.emit()

    def _save_persisted_config(self):
        """按勾选状态、端口与所选对外网卡保存网络服务配置。"""
        try:
            ConfigIO.save_network_service_config({
                "tcp_enabled": bool(self._tcp_check.isChecked()),
                "tcp_port": int(self._tcp_port.value()),
                "http_enabled": bool(self._http_check.isChecked()),
                "http_port": int(self._http_port.value()),
                "pairing_enabled": bool(self._pairing_check.isChecked()),
                "advertise_ip": self.advertise_ip(),
            })
        except Exception as e:
            print(f"[SERVER] 保存服务配置失败: {e}", flush=True)

    # ── 状态刷新 ───────────────────────────────────────────────

    def _update_service_status(self):
        """更新服务状态显示（内联到各服务行的状态标签）"""
        if not self._service_manager:
            return

        try:
            # TCP 状态
            tcp_status = self._service_manager.get_service_status('tcp')
            self._update_inline_status_label(self._tcp_status_label, tcp_status)

            # HTTP 状态
            http_status = self._service_manager.get_service_status('http')
            self._update_inline_status_label(self._http_status_label, http_status)

            # 配对码状态（内联显示当前配对码 + 运行状态）
            pairing_status = self._service_manager.get_service_status('pairing_code')
            if pairing_status.enabled:
                code = self._service_manager.get_pairing_code()
                if code:
                    self._pairing_status_label.setText(f"配对码 {code} · 运行中")
                    self._pairing_status_label.setStyleSheet(
                        f"color: {Theme.readable_text(Theme.ACCENT)}; font-weight: bold;")
                else:
                    self._pairing_status_label.setText("获取配对码失败")
                    self._pairing_status_label.setStyleSheet(
                        f"color: {Theme.readable_text(Theme.RED)};")
            else:
                error_text = f" - {pairing_status.error}" if pairing_status.error else ""
                self._pairing_status_label.setText(f"已停止{error_text}")
                self._pairing_status_label.setStyleSheet(
                    f"color: {Theme.readable_text(Theme.TEXT_MUTED)};")

            # 更新连接信息
            self._update_connection_info()

            # 更新广播健康状态（最近发送时间 / 失败原因）
            self._update_broadcast_status()

            # 更新复选框状态（同步）
            self._tcp_check.blockSignals(True)
            self._tcp_check.setChecked(tcp_status.enabled)
            self._tcp_check.blockSignals(False)

            self._http_check.blockSignals(True)
            self._http_check.setChecked(http_status.enabled)
            self._http_check.blockSignals(False)

            self._pairing_check.blockSignals(True)
            self._pairing_check.setChecked(pairing_status.enabled)
            self._pairing_check.blockSignals(False)

        except Exception:
            # 静默失败，避免定时器刷屏
            pass

    def _update_inline_status_label(self, label: QLabel, status):
        """更新单行内联状态标签（运行中显示端口，停止/错误给出提示）；
        状态色作文字统一经 readable_text 对卡底补偿。"""
        if status.enabled:
            if status.error:
                label.setText(f"错误: {status.error}")
                label.setStyleSheet(
                    f"color: {Theme.readable_text(Theme.ORANGE)};")
            else:
                port_text = f"端口 {status.port}" if status.port else ""
                label.setText(f"● 运行中 {port_text}" if port_text else "● 运行中")
                label.setStyleSheet(
                    f"color: {Theme.readable_text(Theme.GREEN)};"
                    f" font-weight: bold;")
        else:
            error_text = f" - {status.error}" if status.error else ""
            label.setText(f"已停止{error_text}")
            label.setStyleSheet(
                f"color: {Theme.readable_text(Theme.TEXT_MUTED)};")

    def _effective_advertise_ip(self) -> tuple:
        """返回 (生效的通告 IP, 是否生效, 是否发生失效回退)。

        生效 = 至少勾选了一个消费网卡的服务且所选地址仍在本机；
        失效地址一律按「自动」处理，并返回回退标记供界面提示。

        网卡列表取定时刷新的缓存（1 秒状态定时器不走网卡枚举，避免
        UDP 路由探测与主机名解析被高频调用）。
        """
        ip = (getattr(self._service_manager, 'advertise_ip', '') or '').strip()
        consumers = self._active_nic_consumers()
        ips = self._local_ips_cache
        stale = bool(ip) and ip not in ips
        if stale:
            ip = ""
        return ip, bool(consumers), stale

    def _update_connection_info(self):
        """更新「连接信息」各分块：本机地址 / 服务地址 / 广播 / 提示条。"""
        if not self._service_manager:
            return

        advertise_ip, nic_active, stale = self._effective_advertise_ip()
        try:
            tcp_status = self._service_manager.get_service_status('tcp')
            http_status = self._service_manager.get_service_status('http')
            pairing_status = self._service_manager.get_service_status('pairing_code')
            running = (tcp_status.enabled or http_status.enabled
                       or pairing_status.enabled)
        except Exception:
            return

        # 无任何服务运行时不解析本机 IP（_get_local_ip 会发一次路由探测）
        default_ip = self._get_local_ip() if running else ""
        ips = self._local_ips_cache
        display_ip = advertise_ip or default_ip
        other_ips = [ip for ip in ips if ip != display_ip] if display_ip else list(ips)

        # ── 本机地址 ──
        if nic_active:
            self._advertise_value.setText(
                f"{display_ip}（自动 · 默认出口）" if not advertise_ip
                else f"{display_ip}（指定网卡）")
        else:
            self._advertise_value.setText(
                "不生效（当前无 TCP／配对码服务）" if running else "—")
        self._other_ips_value.setText(
            "、".join(other_ips) if other_ips else "无其他可通告地址")

        # ── 服务地址（端口取实际运行端口：TCP 被占用时会自动顺延）──
        self._set_addr_row(self._tcp_addr_row, tcp_status.enabled,
                           f"{display_ip}:{tcp_status.port}"
                           if tcp_status.port else "")
        self._set_addr_row(self._http_addr_row, http_status.enabled,
                           f"http://{display_ip}:{http_status.port}"
                           if http_status.port else "")
        pairing_code = ""
        if pairing_status.enabled:
            pairing_code = self._service_manager.get_pairing_code() or "获取失败"
        self._set_addr_row(self._pairing_addr_row, pairing_status.enabled,
                           pairing_code)
        self._idle_addr_label.setVisible(not running)
        # 多网卡且 HTTP 运行时提示：HTTP 不受网卡选择限制
        self._http_note_label.setVisible(
            http_status.enabled and len(other_ips) > 0)

        # ── 提示条：网卡失效回退 ──
        alerts = []
        if stale:
            alerts.append(
                "⚠ 配置的对外网卡已不在本机网卡列表，本页已按「自动」显示；"
                "请重新选择网卡。")
        if not alerts:
            self._conn_alert.setVisible(False)
        else:
            self._conn_alert.setText("\n".join(alerts))
            self._conn_alert.setVisible(True)

    @staticmethod
    def _set_addr_row(row, visible: bool, text: str) -> None:
        """显隐一条服务地址行并写入值。"""
        name_lbl, value_lbl = row
        name_lbl.setVisible(visible)
        value_lbl.setVisible(visible)
        if visible:
            value_lbl.setText(text or "—")

    def _update_broadcast_status(self):
        """更新广播健康状态：显示最近广播时间、失败次数与原因。

        广播是客户端发现的唯一途径；发送失败（如防火墙、路由问题）时
        界面必须可见，否则服务端「运行中」但客户端永远搜不到。
        """
        if not self._service_manager:
            return

        try:
            discovery = self._service_manager.get_discovery_status()
        except Exception:
            # 获取失败不干扰主状态刷新（定时器 1 秒一次，静默跳过）
            return

        lines = []
        for service_type, display in (('tcp', 'TCP'), ('pairing_code', '配对码')):
            info = discovery.get(service_type)
            if not info:
                continue
            error = info.get('broadcast_error')
            last_at = info.get('last_broadcast_at')
            if error:
                lines.append(f"⚠ {display} 广播失败：{error}")
            elif last_at:
                last_text = time.strftime(
                    "%H:%M:%S", time.localtime(last_at))
                lines.append(f"{display} 广播正常（最近 {last_text}）")
            else:
                lines.append(f"{display} 广播尚未发送")
        self._broadcast_status_label.setText(
            "\n".join(lines) if lines else "无广播服务运行")

    # ── 防火墙检测与放行 ─────────────────────────────────────

    def _set_firewall_style(self, color: str, text: str) -> None:
        """更新防火墙状态行并记住状态色（refresh_theme 按它还原）；
        文字色经 readable_text 对卡底补偿（工业中灰上提亮）。"""
        self._fw_last_color = color
        self._firewall_status_label.setStyleSheet(
            f"color: {Theme.readable_text(color)};")
        self._firewall_status_label.setText(text)

    def _refresh_firewall_status_only(self):
        """静默检测防火墙并更新状态行（不弹窗；页面打开时自动执行一次）。

        netsh 查询约 1~3 秒且在 UI 线程同步执行：先给出「检测中」状态行
        与等待光标（含 processEvents 让其先画出），避免页面像无响应。
        """
        self._firewall_status_label.setText("防火墙：检测中……")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        # 让「检测中」状态行与等待光标先画出，再进入 1~3 秒同步查询
        QApplication.processEvents()
        # processEvents 可能兑现本页排队的 deleteLater（测试快速开关页时必现）；
        # 页已销毁则终止刷新，等待光标是全局状态，恢复后再返回
        if sip.isdeleted(self):
            QApplication.restoreOverrideCursor()
            return
        try:
            checks = firewall_helper.check_firewall_status()
        except Exception:
            QApplication.restoreOverrideCursor()
            self._firewall_status_label.setText("防火墙：检测失败（可点『检查防火墙』重试）")
            return
        QApplication.restoreOverrideCursor()
        color, text = firewall_helper.status_style_and_text(checks)
        self._set_firewall_style(color, text)

    def _on_firewall_check_clicked(self):
        """手动检查：更新状态行并弹窗展示结论。"""
        self._firewall_status_label.setText("防火墙：检测中……")
        try:
            checks = firewall_helper.check_firewall_status()
        except Exception as e:
            self._firewall_status_label.setText("防火墙：检测失败")
            QMessageBox.warning(self, "防火墙检测失败", str(e))
            return
        color, text = firewall_helper.status_style_and_text(checks)
        self._set_firewall_style(color, text)
        firewall_helper.show_check_result(self)

    def _on_firewall_fix_clicked(self):
        """一键放行：删除阻止规则 + 添加允许规则（可能弹 UAC），完成后复查。"""
        firewall_helper.fix_and_show(self)
        self._refresh_firewall_status_only()

    def _diagnose_services(self):
        """诊断服务"""
        if not self._service_manager:
            QMessageBox.warning(self, "诊断失败", "服务管理器未初始化")
            return

        try:
            results = []

            # 检查 TCP 服务
            tcp_status = self._service_manager.get_service_status('tcp')
            if tcp_status.enabled:
                results.append(f"✓ TCP 服务运行中 (端口: {tcp_status.port})")
            else:
                results.append("✗ TCP 服务未启动")

            # 检查 HTTP 服务
            http_status = self._service_manager.get_service_status('http')
            if http_status.enabled:
                results.append(f"✓ HTTP 服务运行中 (端口: {http_status.port})")
            else:
                results.append("✗ HTTP 服务未启动")

            # 检查配对码服务
            pairing_status = self._service_manager.get_service_status('pairing_code')
            if pairing_status.enabled:
                code = self._service_manager.get_pairing_code()
                if code:
                    results.append(f"✓ 配对码服务运行中 (当前配对码: {code})")
                else:
                    results.append("✗ 配对码服务运行中但无法获取配对码")
            else:
                results.append("✗ 配对码服务未启动")

            # 检查本机 IP
            local_ip = self._get_local_ip()
            results.append(f"\n本机 IP: {local_ip}")

            # 生效的对外网卡（HTTP 不消费网卡，诊断中一并说明）
            advertise_ip, nic_active, stale = self._effective_advertise_ip()
            results.append(
                f"对外网卡: {advertise_ip or '自动（默认出口）'}"
                f"{'（不生效：当前无 TCP／配对码服务）' if not nic_active else ''}")
            if stale:
                results.append("⚠ 配置的对外网卡已不在本机网卡列表，广播已回退默认出口")

            # 本机全部网卡 IP 与广播目标（现场排查网段/多网卡问题用）
            try:
                all_ips = get_local_ips()
                results.append(f"本机全部 IP: {'、'.join(all_ips)}")
                targets = subnet_broadcast_addresses(all_ips)
                results.append(
                    f"广播目标: 255.255.255.255"
                    + (f"、{'、'.join(targets)}" if targets else ""))
            except Exception:
                pass

            # 检查端口占用（真实检测）
            results.append("\n端口占用状态：")
            tcp_port = tcp_status.port or self._service_manager.DEFAULT_TCP_PORT
            if tcp_status.enabled:
                results.append(f"  TCP {tcp_port}: 本服务运行中（端口由本服务占用）")
            else:
                used = self._check_tcp_port_in_use(tcp_port)
                results.append(f"  TCP {tcp_port}: {'已被其他程序占用' if used else '端口空闲'}")

            http_port = http_status.port or self._service_manager.DEFAULT_HTTP_PORT
            if http_status.enabled:
                results.append(f"  HTTP {http_port}: 本服务运行中（端口由本服务占用）")
            else:
                used = self._check_tcp_port_in_use(http_port)
                results.append(f"  HTTP {http_port}: {'已被其他程序占用' if used else '端口空闲'}")

            pairing_port = (pairing_status.port
                            or self._service_manager.DEFAULT_PAIRING_CODE_PORT)
            if pairing_status.enabled:
                results.append(f"  UDP {pairing_port}: 配对码服务运行中（端口由本服务占用）")
            else:
                used = self._check_udp_port_in_use(pairing_port)
                results.append(f"  UDP {pairing_port}: {'已被其他程序占用' if used else '端口空闲'}")

            QMessageBox.information(self, "服务诊断结果", "\n".join(results))

        except Exception as e:
            QMessageBox.warning(self, "诊断失败", f"服务诊断时发生错误：\n{str(e)}")

    @staticmethod
    def _check_tcp_port_in_use(port: int) -> bool:
        """检测 TCP 端口是否已被其他进程占用。

        Windows 下 SO_REUSEADDR 允许重复绑定（bind 不报错但连接会被先占用者
        劫持），因此用 SO_EXCLUSIVEADDRUSE 探测：绑定成功说明空闲，失败说明占用。

        返回:
            bool: True 表示端口已被占用，False 表示空闲。
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass  # 非 Windows 平台无此选项，退回普通 bind 探测
            s.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True
        finally:
            s.close()

    @staticmethod
    def _check_udp_port_in_use(port: int) -> bool:
        """检测 UDP 端口是否已被其他进程绑定。

        返回:
            bool: True 表示端口已被占用，False 表示空闲。
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass  # 非 Windows 平台无此选项，退回普通 bind 探测
            s.bind(("0.0.0.0", port))
            return False
        except OSError:
            return True
        finally:
            s.close()

    def _get_local_ip(self) -> str:
        """获取本机 IP 地址"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            return local_ip
        except Exception:
            return "127.0.0.1"

    # ── 清理 ───────────────────────────────────────────────────

    def shutdown(self) -> None:
        """释放资源：停止状态刷新定时器"""
        self._status_timer.stop()
        self._nic_timer.stop()

    def resume(self) -> None:
        """重新显示后恢复状态刷新（shutdown 的逆操作；弹窗实例复用场景）"""
        self._status_timer.start()
        self._nic_timer.start()

    def run_diagnostics(self) -> None:
        """服务诊断（公开入口：容器诊断按钮 / 外部调用方使用）"""
        self._diagnose_services()


class ServiceConfigDialog(QDialog):
    """服务配置对话框（独立窗口模式）

    内部复用 ServiceConfigPage，仅在独立窗口场景下补充窗口级设置，
    保持与旧调用方 / 验证脚本兼容。
    """

    # 信号：服务状态变更（转发自页面）
    service_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("服务配置")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._page = ServiceConfigPage(self)
        self._page.service_changed.connect(self.service_changed)
        layout.addWidget(self._page)
        # 固定尺寸：按内容实际需要的最小尺寸 + 少量边距，适配不同字体/DPI
        hint = self.sizeHint()
        self.setFixedSize(hint.width() + 8, hint.height() + 8)

    def set_service_manager(self, manager) -> None:
        """注入服务管理器（转发给页面）"""
        self._page.set_service_manager(manager)

    def closeEvent(self, event) -> None:
        """关闭事件：释放页面资源"""
        self._page.shutdown()
        super().closeEvent(event)
