"""
Windows 防火墙检测与放行工具

用户不懂防火墙：程序在「公用 / 专用」网络被 Windows 防火墙拦截入站时，
服务端能启动、广播也在发，但客户端永远连不进来，且没有任何提示。
本模块用 netsh 查询防火墙规则（verbose 模式输出含程序路径字段），
按【当前 exe 路径 + 目标端口】判定是否存在启用的阻止规则，
并提供一键放行（删除阻止规则 + 添加允许规则，失败时自动管理员提权重试）。

依赖：仅标准库（subprocess / ctypes / dataclasses），兼容 Python 3.8 与 Windows 7。
注意：netsh 输出编码随系统语言变化（中文 GBK / 英文 UTF-8），解析时自动探测。
"""

import ctypes
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# 中英文合一的段标题与字段名（netsh 输出按系统语言二选一）
_SECTION_HEADERS = ("规则名称", "Rule Name")
_FIELD_ALIASES = {
    "规则名称": "name", "Rule Name": "name",
    "已启用": "enabled", "Enabled": "enabled",
    "方向": "direction", "Direction": "direction",
    "配置文件": "profiles", "Profiles": "profiles",
    "协议": "protocol", "Protocol": "protocol",
    "本地端口": "local_port", "Local Port": "local_port",
    "程序": "program", "Program": "program",
    "操作": "action", "Action": "action",
}
_ANY_VALUES = ("任何", "any", "全部", "all", "*")

# 入站规则查询参数（verbose 才会输出「程序」字段）
_SHOW_ARGS = ["advfirewall", "firewall", "show", "rule", "name=all", "dir=in", "verbose"]
_ADD_RULE_ARGS = ["advfirewall", "firewall", "add", "rule",
                  'name=多通道温度分析仪', "dir=in", "action=allow",
                  "enable=yes", "profile=any"]
_DELETE_RULE_ARGS = ["advfirewall", "firewall", "delete", "rule"]

# netsh 命令超时（秒）：本机规则多时全量查询可达数秒
_NETSH_TIMEOUT = 20


class FirewallCommandError(RuntimeError):
    """netsh 命令本身的错误（权限不足、语法错误等）。"""


def _run_netsh(args: List[str], timeout: int = _NETSH_TIMEOUT) -> str:
    """执行 netsh 命令并返回按系统语言正确解码的文本输出。

    返回:
        str: netsh 标准输出文本

    抛出:
        FirewallCommandError: netsh 退出码非 0（含权限不足）时抛出，附带 stderr。
    """
    try:
        proc = subprocess.run(
            ["netsh"] + args,
            capture_output=True,
            timeout=timeout,
            creationflags=0x08000000,  # CREATE_NO_WINDOW：不弹出黑色命令行窗口
        )
    except FileNotFoundError:
        raise FirewallCommandError("未找到 netsh 命令，无法检测防火墙")
    except subprocess.TimeoutExpired:
        raise FirewallCommandError(f"netsh 命令超时（>{timeout} 秒）")

    raw = proc.stdout
    # 按系统语言探测编码：中文系统 GBK、英文系统 UTF-8
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            candidate = raw.decode(enc)
            if "规则名称" in candidate or "Rule Name" in candidate:
                text = candidate
                break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode("gbk", errors="replace")

    if proc.returncode != 0:
        stderr = proc.stderr.decode("gbk", errors="replace").strip()
        raise FirewallCommandError(stderr or f"netsh 命令失败（退出码 {proc.returncode}）")
    return text


def _parse_bool(value: str) -> Optional[bool]:
    """解析中英文的 是/否、Yes/No 布尔值。"""
    v = value.strip().lower()
    if v in ("是", "yes", "true", "1"):
        return True
    if v in ("否", "no", "false", "0"):
        return False
    return None


def _is_any(value: str) -> bool:
    """协议 / 端口字段的「任何/全部」值判断。"""
    return value.strip().lower() in _ANY_VALUES


def parse_firewall_rules(output: str) -> List[Dict[str, str]]:
    """解析 netsh `show rule ... verbose` 输出为规则字典列表。

    netsh 规则段以「规则名称:」行开头（中英文系统标题不同），段内为
    「字段名: 值」行。字段名规范化为英文键，值保持原文本（含全角空格）。

    参数:
        output: netsh 命令的标准输出

    返回:
        List[Dict]: 每条规则一个字典，键见 _FIELD_ALIASES；无法识别的
                    段标题行（如分隔线）自动跳过。
    """
    rules: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 段分隔线（----...）与无字段行跳过
        if set(line) <= set("- \t") or line.startswith("----"):
            continue

        # 段标题：新规则开始
        if any(line.startswith(h) for h in _SECTION_HEADERS):
            name_value = line.split(":", 1)[1].strip() if ":" in line else ""
            if current is not None:
                rules.append(current)
            current = {"name": name_value}
            continue

        # 字段行：取第一个冒号（值里可能再含冒号，如程序路径）
        if current is None:
            continue
        m = re.match(r"^([^:：]+)[:：]\s*(.*)$", line)
        if not m:
            continue
        raw_key, value = m.group(1).strip(), m.group(2).strip()
        key = _FIELD_ALIASES.get(raw_key, raw_key)
        current[key] = value

    if current is not None:
        rules.append(current)
    return rules


def _rule_matches_program(rule: Dict[str, str], exe_path: str) -> bool:
    """规则程序字段是否指向当前 exe。

    Windows 防火墙按 exe 路径精确匹配程序（换名/换目录后的新 exe 不受
    旧规则约束），因此这里仅做路径规范化后的大小写不敏感比较。
    """
    program = rule.get("program", "").strip()
    if not program:
        return False
    try:
        target = os.path.normcase(os.path.abspath(exe_path))
        rule_program = os.path.normcase(os.path.abspath(program))
    except (ValueError, OSError, TypeError):
        return False
    return rule_program == target


def _rule_matches_port(rule: Dict[str, str], protocol: str, port: int) -> bool:
    """规则是否按协议 + 端口匹配（本地端口为「任何」时视为全端口规则）。"""
    rule_protocol = rule.get("protocol", "").strip().lower()
    if rule_protocol in ("tcp", "udp"):
        if rule_protocol != protocol.lower():
            return False
    elif not _is_any(rule_protocol):
        return False  # ICMP / 其它协议不参与端口判断

    local_port = rule.get("local_port", "").strip()
    if _is_any(local_port):
        return True
    # 端口可能以逗号分隔多个值，逐一比较
    return any(p.strip() == str(port) for p in local_port.split(","))


@dataclass
class PortCheck:
    """单个端口检查项及其防火墙结论。"""
    protocol: str
    port: int
    # 结论：ok=已放行（无阻止规则）；blocked=存在阻止规则；no_rule=无任何相关规则（默认允许时才通）
    status: str = "no_rule"
    # 命中的阻止规则名称（status=blocked 时非空）
    blocking_rules: List[str] = field(default_factory=list)


def check_firewall(ports: List[Tuple[str, int]],
                   exe_path: Optional[str] = None,
                   rules: Optional[List[Dict[str, str]]] = None) -> List[PortCheck]:
    """检测指定端口是否存在启用的入站阻止规则。

    匹配规则：
    - 按程序规则（netsh verbose 输出含「程序」字段）：仅当程序路径匹配
      exe_path 时生效；
    - 按端口规则（无程序字段，作用于所有程序）：协议 + 本地端口匹配时生效。
    两类规则任一命中阻止即判定 blocked。不区分网络配置文件
    （公用/专用任一生效即提示，对小白用户宁多提示不少提示）。

    参数:
        ports: [(协议, 端口)] 检查清单，如 [("TCP", 9527), ("UDP", 9526)]
        exe_path: 本程序 exe 路径；提供时程序规则按此匹配
        rules: 预先解析的规则列表（可传入以便复用一次 netsh 查询）

    返回:
        List[PortCheck]: 与 ports 一一对应的检查结果。
    """
    if rules is None:
        rules = parse_firewall_rules(_run_netsh(_SHOW_ARGS))

    results: List[PortCheck] = []
    for protocol, port in ports:
        check = PortCheck(protocol=protocol, port=port)
        matched_blocking = []
        matched_allow = False
        for rule in rules:
            if rule.get("direction", "入").strip().lower() not in ("入", "in"):
                continue  # 只看入站规则
            if _parse_bool(rule.get("enabled", "")) is False:
                continue  # 已禁用的规则不生效
            # 按程序规则只作用于该程序；按端口规则作用于所有程序
            has_program = bool(rule.get("program", "").strip())
            if has_program:
                if exe_path is None or not _rule_matches_program(rule, exe_path):
                    continue
            elif not _rule_matches_port(rule, protocol, port):
                continue

            action = rule.get("action", "").strip().lower()
            if action in ("阻止", "block", "deny") or "阻止" in action or "deny" in action:
                matched_blocking.append(rule.get("name", "未命名规则"))
            elif action in ("允许", "allow", "permit"):
                matched_allow = True

        if matched_blocking:
            check.status = "blocked"
            check.blocking_rules = matched_blocking
        elif matched_allow:
            check.status = "ok"
        else:
            check.status = "no_rule"
        results.append(check)
    return results


def delete_rules(rule_names: List[str]) -> None:
    """删除指定名称的全部规则（含禁止放行的阻止规则）。

    同名可能有多条（不同配置文件各一条），netsh delete 按名称删除全部。
    需要管理员权限；失败抛出 FirewallCommandError。

    参数:
        rule_names: 要删除的规则名称列表
    """
    for name in rule_names:
        if not name:
            continue
        _run_netsh(_DELETE_RULE_ARGS + ["name=" + name])


def add_allow_rule(protocol: str, port: int, exe_path: str,
                   rule_name: str = "多通道温度分析仪") -> None:
    """添加一条按程序放行的入站规则（覆盖全部配置文件）。

    需要管理员权限；失败抛出 FirewallCommandError。

    参数:
        protocol: "TCP" 或 "UDP"
        port: 端口号
        exe_path: 本程序 exe 路径
        rule_name: 规则名称
    """
    args = _ADD_RULE_ARGS[:]
    # _ADD_RULE_ARGS[4] 是默认 name 参数，替换为用户指定的规则名
    args[4] = "name=" + rule_name
    args.extend([f"protocol={protocol}", f"localport={port}",
                 f"program={exe_path}"])
    _run_netsh(args)


def _run_elevated(netsh_args: List[str]) -> bool:
    """通过 UAC 提权执行 netsh 命令（ShellExecuteW runas，隐藏窗口）。

    返回:
        bool: 提权进程是否已启动（不代表命令一定成功）；
              用户取消 UAC 或环境不支持时返回 False。
    """
    # 含空格或 = 的参数加引号包裹，避免路径/规则名被拆散
    args_str = " ".join(
        '"{}"'.format(a) if (" " in a or "=" in a) else a for a in netsh_args)
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "netsh.exe", args_str, None, 0)  # SW_HIDE
    return result > 32


def _wait_until(predicate, timeout: float = 10.0, interval: float = 0.3) -> bool:
    """轮询等待条件成立（提权命令异步执行后确认生效用）。"""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except FirewallCommandError:
            pass
        time.sleep(interval)
    return False


def fix_firewall_ports(ports: List[Tuple[str, int]],
                       exe_path: str,
                       rule_name: str = "多通道温度分析仪") -> Dict[Tuple[str, int], Tuple[bool, str]]:
    """一键放行：删除命中端口的阻止规则，并添加按程序的允许规则。

    先以当前权限直接执行；权限不足时自动 UAC 提权重试，并轮询确认生效。
    每一步都返回 (成功与否, 说明)，供界面展示给不懂防火墙的用户。

    参数:
        ports: [(协议, 端口)] 要放行的端口清单
        exe_path: 本程序 exe 路径
        rule_name: 使用的允许规则名称

    返回:
        Dict[(协议, 端口)] -> (是否成功, 结果说明)
    """
    results: Dict[Tuple[str, int], Tuple[bool, str]] = {}

    # 查询现有规则，收集命中端口的阻止规则（跨配置文件全部收集）
    try:
        rules_cache = parse_firewall_rules(_run_netsh(_SHOW_ARGS))
    except FirewallCommandError as e:
        for item in ports:
            results[item] = (False, f"无法查询防火墙规则：{e}")
        return results

    blocking_names: List[str] = []
    for protocol, port in ports:
        check = check_firewall([(protocol, port)], exe_path=exe_path,
                               rules=rules_cache)[0]
        if check.status == "blocked":
            for name in check.blocking_rules:
                if name not in blocking_names:
                    blocking_names.append(name)

    # 1) 删除阻止规则（提权删除后轮询确认消失）
    blocking_names_to_delete = list(blocking_names)
    if blocking_names_to_delete:
        try:
            delete_rules(blocking_names_to_delete)
        except FirewallCommandError:
            if not _run_elevated(_DELETE_RULE_ARGS
                                 + ["name=" + n for n in blocking_names_to_delete]):
                msg = "需要管理员权限才能删除防火墙阻止规则（UAC 未授权）"
                for item in ports:
                    results[item] = (False, msg)
                return results
            _wait_until(lambda: not any(
                n in [r.get("name", "") for r in
                      parse_firewall_rules(_run_netsh(_SHOW_ARGS))]
                for n in blocking_names_to_delete))

    # 2) 添加允许规则（每端口一条按程序放行；权限不足时提权重试）
    for protocol, port in ports:
        try:
            add_allow_rule(protocol, port, exe_path, rule_name)
            results[(protocol, port)] = (True, "已添加允许规则")
        except FirewallCommandError as e:
            denied = ("权限" in str(e) or "访问被拒绝" in str(e)
                      or "denied" in str(e).lower())
            if denied and _run_elevated(_ADD_RULE_ARGS[2:] + [
                    f"protocol={protocol}", f"localport={port}",
                    "program=" + exe_path]):
                results[(protocol, port)] = (True, "已通过管理员权限添加允许规则")
            elif denied:
                results[(protocol, port)] = (False, f"需要管理员权限：{e}")
            else:
                results[(protocol, port)] = (False, f"添加允许规则失败：{e}")
    return results


def main() -> None:
    """命令行自测：列出本程序入站阻止检测结果。"""
    import sys

    exe = sys.executable
    print(f"当前 exe: {exe}")
    results = check_firewall([("TCP", 9527), ("UDP", 9526), ("TCP", 8080)],
                             exe_path=exe)
    for r in results:
        print(f"  {r.protocol} {r.port}: {r.status}"
              + (f" <- {r.blocking_rules}" if r.blocking_rules else ""))


if __name__ == "__main__":
    main()