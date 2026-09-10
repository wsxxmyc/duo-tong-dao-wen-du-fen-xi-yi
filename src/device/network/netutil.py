"""
网络工具函数

提供本机 IPv4 地址枚举和子网广播地址计算，供 UDP 设备发现使用。
纯标准库实现，兼容 Python 3.8。
"""

import socket
from typing import List, Optional

# 受限广播地址：按默认路由接口所在网段发送，无法到达其他网卡/子网
LIMITED_BROADCAST = "255.255.255.255"

# 默认路由探测目标（只查路由表获取出口 IP，UDP connect 不会真正发包）
_ROUTE_PROBE_TARGET = ("8.8.8.8", 80)


def _default_route_ip() -> Optional[str]:
    """获取默认路由出口的本机 IPv4 地址（8.8.8.8 探测法，不发包）。

    返回:
        Optional[str]: 出口 IP；无默认路由或失败时返回 None。
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        s.connect(_ROUTE_PROBE_TARGET)
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _is_local_ip(ip: str) -> bool:
    """判断是否为可通告的本机 IPv4 地址（过滤回环与链路本地地址）。"""
    if not ip or ip == "0.0.0.0" or ip == "127.0.0.1":
        return False
    # 链路本地地址（无 DHCP 分配时的自动私有地址）不可达，不能通告
    return not (ip.startswith("169.254.") and ip.count(".") == 3)


def get_local_ips() -> List[str]:
    """枚举本机所有可通告的非回环 IPv4 地址，默认路由出口 IP 优先。

    多网卡 / VPN / 虚拟机桥接等环境下，本机可能有多个网段地址；
    返回全部候选，由广播方按每个地址分别通告，客户端可从中选择可达地址。

    返回:
        List[str]: 本机 IPv4 地址列表（去重）；完全失败时返回 ["127.0.0.1"]，
                  此时客户端可用 UDP 源地址兜底连接。
    """
    ips: List[str] = []

    # 1. 默认路由出口 IP（与客户端最可能同网段，优先）
    default_ip = _default_route_ip()
    if default_ip and _is_local_ip(default_ip):
        ips.append(default_ip)

    # 2. 主机名解析补充（可带出其他网卡/别名地址）
    try:
        _, _, addr_list = socket.gethostbyname_ex(socket.gethostname())
        for ip in addr_list:
            if _is_local_ip(ip) and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    # 3. 全部失败时回退回环地址，保证广播消息仍有 ip_address 字段；
    #    客户端收到后用 UDP 源地址兜底，因此回环通告不影响连接能力。
    if not ips:
        ips.append("127.0.0.1")

    return ips


def subnet_broadcast_addresses(local_ips: List[str]) -> List[str]:
    """为每个本机 IPv4 计算 /24 子网广播地址（x.x.x.255）。

    局域网基本使用 /24 掩码；受限广播 255.255.255.255 只从默认路由接口出发，
    向各网卡子网广播地址分别发送，多网卡 / 多 VLAN 环境下各网段客户端才能收到。

    参数:
        local_ips: 本机 IPv4 地址列表

    返回:
        List[str]: 去重后的子网广播地址列表（回环地址无子网广播）。
    """
    targets = []
    for ip in local_ips:
        if ip == "127.0.0.1":
            continue
        parts = ip.split(".")
        if len(parts) == 4 and all(part.isdigit() for part in parts):
            subnet_broadcast = ".".join(parts[:3]) + ".255"
            if subnet_broadcast not in targets:
                targets.append(subnet_broadcast)
    return targets