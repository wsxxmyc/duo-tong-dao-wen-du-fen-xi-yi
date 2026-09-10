# -*- coding: utf-8 -*-
"""
防火墙检测与放行的弹窗辅助

服务端页与客户端页共用：检测本程序入站端口放行状态、一键放行，
用 QMessageBox 向完全不懂防火墙的用户展示结论与操作入口。
"""

from __future__ import annotations

import sys
from typing import List, Tuple

from PyQt5.QtWidgets import QMessageBox, QWidget

from device.network import firewall
from ui.theme import Theme

# 网络远程功能使用的端口（设备发现 / 数据连接 / HTTP 网页）
CHECK_PORTS: List[Tuple[str, int]] = [
    ("TCP", 9527),
    ("UDP", 9526),
    ("TCP", 8080),
]

_RULE_NAME = "多通道温度分析仪 (远程访问)"


def get_exe_path() -> str:
    """返回当前程序的可执行文件路径（源码运行时为 python.exe，发布包为主程序）。"""
    return sys.executable


def check_firewall_status() -> List[firewall.PortCheck]:
    """检测本程序入站端口放行状态（可能耗时 1~3 秒，勿在主线程高频调用）。"""
    return firewall.check_firewall(CHECK_PORTS, exe_path=get_exe_path())


def _summary_lines(checks: List[firewall.PortCheck]) -> List[str]:
    """把检测结论转换为用户可读的中文行文本。"""
    lines = []
    for check in checks:
        if check.status == "blocked":
            lines.append(f"⚠ {check.protocol} {check.port}：被阻止"
                         f"（{len(check.blocking_rules)} 条阻止规则）")
        elif check.status == "ok":
            lines.append(f"✓ {check.protocol} {check.port}：已放行")
        else:
            lines.append(f"· {check.protocol} {check.port}：未找到规则"
                         f"（多数网络默认拦截，建议放行）")
    return lines


def _any_blocked(checks: List[firewall.PortCheck]) -> bool:
    return any(c.status == "blocked" for c in checks)


def show_check_result(parent: QWidget) -> None:
    """检测并在弹窗中展示结论，点击「一键放行」直接修复。"""
    try:
        checks = check_firewall_status()
    except Exception as e:
        QMessageBox.warning(
            parent, "防火墙检测失败",
            f"无法读取 Windows 防火墙规则：\n{e}\n\n"
            f"可尝试：控制面板 → Windows Defender 防火墙 → 允许应用通过防火墙")
        return

    lines = _summary_lines(checks)
    title = "防火墙检测结果"
    if _any_blocked(checks):
        msg = ("检测到 Windows 防火墙阻止了本程序的入站连接，"
               "局域网内其他电脑将无法发现和连接本机。\n\n")
        msg += "\n".join(lines)
        box = QMessageBox(parent)
        box.setWindowTitle(title)
        box.setText(msg)
        box.setIcon(QMessageBox.Warning)
        fix_btn = box.addButton("一键放行", QMessageBox.AcceptRole)
        box.addButton("稍后处理", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is fix_btn:
            fix_and_show(parent)
    else:
        msg = "本程序入站端口未被防火墙阻止，局域网发现与连接不会因此中断。\n\n"
        msg += "\n".join(lines)
        QMessageBox.information(parent, title, msg)


def fix_and_show(parent: QWidget) -> None:
    """一键放行：删除阻止规则并添加允许规则（可能弹出 UAC 管理员授权）。"""
    # 放行可能触发 UAC 弹窗，先向用户说明
    confirm = QMessageBox.question(
        parent, "一键放行",
        "将删除本程序的防火墙阻止规则，并添加允许规则"
        "（UDP 9526 设备发现、TCP 9527 数据连接、TCP 8080 网页服务）。\n\n"
        "如果弹出用户账户控制（UAC）窗口，请点击「是」。\n\n"
        "是否继续？",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
    if confirm != QMessageBox.Yes:
        return

    results = firewall.fix_firewall_ports(CHECK_PORTS, exe_path=get_exe_path(),
                                          rule_name=_RULE_NAME)
    ok_lines = []
    fail_lines = []
    for (protocol, port), (ok, msg) in results.items():
        if ok:
            ok_lines.append(f"✓ {protocol} {port}：{msg}")
        else:
            fail_lines.append(f"✗ {protocol} {port}：{msg}")

    if not fail_lines:
        QMessageBox.information(
            parent, "放行完成",
            "防火墙已放行以下端口，局域网内其他电脑现在可以连接本机：\n\n"
            + "\n".join(ok_lines))
    else:
        QMessageBox.warning(
            parent, "部分失败",
            "以下端口未能放行：\n\n" + "\n".join(fail_lines)
            + "\n\n请以管理员身份重新运行本程序后再试，"
              "或手动在「Windows Defender 防火墙」中允许本程序。")


def status_style_and_text(checks: List[firewall.PortCheck]) -> Tuple[str, str]:
    """服务端页内嵌状态行的样式与文本（不弹窗）。"""
    if not checks:
        return Theme.TEXT_MUTED, "防火墙：未检测"
    if _any_blocked(checks):
        return (Theme.ORANGE,
                "防火墙：⚠ 检测到阻止规则，局域网将无法连接本机（可点击『一键放行』）")
    if all(c.status == "ok" for c in checks):
        return Theme.GREEN, "防火墙：✓ 已放行，不会阻止局域网连接"
    return (Theme.TEXT_MUTED,
            "防火墙：未找到放行规则，多数网络默认拦截入站（建议放行）")