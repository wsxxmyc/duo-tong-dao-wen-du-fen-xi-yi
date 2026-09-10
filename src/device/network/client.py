"""
网络客户端模块

提供局域网设备扫描和远程数据查询功能。
"""

import socket
import json
import errno
import time
from typing import Optional, List, Dict, Any
import logging

from .protocol import (
    create_request,
    send_message,
    receive_message,
    validate_broadcast_message,
    REQUEST_GET_SESSION_LIST,
    REQUEST_GET_CHANNEL_CONFIG,
    REQUEST_GET_TEMPERATURE_DATA,
    REQUEST_GET_SESSION_STATS,
    RESPONSE_OK,
    DEFAULT_UDP_PORT,
    DEFAULT_TCP_PORT,
    DEFAULT_TIMEOUT,
    DEFAULT_SCAN_TIMEOUT
)
from .netutil import get_local_ips

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _same_subnet(ip_a: str, ip_b: str) -> bool:
    """两个 IPv4 是否同 /24 网段（前 3 段相同）。"""
    if not ip_a or not ip_b:
        return False
    return ip_a.split('.')[:3] == ip_b.split('.')[:3]


def merge_same_device(devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一台设备（device_name 相同）多 IP 通告合并为一行。

    服务端多网卡时会对每个网卡 IP 各广播一条消息（ip_address 不同），
    客户端若按地址直接展示会把一台设备显示成多行。这里按 device_name
    合并：优先保留与客户端同 /24 网段的 IP 作为连接首选，其余地址放入
    alt_addresses（供界面提示），避免误连不可达地址。

    参数:
        devices: 扫描到的设备消息列表（已按 (ip, port) 去重）

    返回:
        合并后的设备列表；单地址设备原样保留，且不附加 alt_addresses。
    """
    local_ips = get_local_ips()
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for dev in devices:
        name = dev.get('device_name') or ''
        key = name or f"{dev.get('ip_address')}:{dev.get('tcp_port')}"
        groups.setdefault(key, []).append(dev)

    merged = []
    for items in groups.values():
        if len(items) == 1:
            merged.append(items[0])
            continue
        # 同网段优先（rank 0），同 rank 保持原出现顺序（稳定排序）
        items.sort(key=lambda d: 0 if any(
            _same_subnet(d.get('ip_address', ''), ip) for ip in local_ips) else 1)
        primary = items[0]
        primary['alt_addresses'] = [
            f"{d.get('ip_address')}:{d.get('tcp_port')}"
            for d in items[1:]
        ]
        merged.append(primary)

    return merged


# ─────────────────────────────────────────────────────
# 设备扫描器
# ─────────────────────────────────────────────────────

class DeviceScanner:
    """
    局域网设备扫描器

    监听 UDP 广播消息，发现局域网中的 多通道温度分析仪设备。
    """

    def __init__(self, udp_port: int = DEFAULT_UDP_PORT):
        """
        初始化设备扫描器

        参数:
            udp_port: UDP 广播端口
        """
        self.udp_port = udp_port

    def scan(self, timeout: float = DEFAULT_SCAN_TIMEOUT) -> List[Dict[str, Any]]:
        """
        扫描局域网中的 多通道温度分析仪设备

        参数:
            timeout: 扫描超时时间（秒）

        返回:
            List[Dict]: 发现的设备列表，每个设备包含:
                - device_name: 设备名称
                - ip_address: IP 地址
                - tcp_port: TCP 端口
                - version: 应用版本
                - timestamp: 广播时间戳

        抛出:
            OSError: 本机 UDP 监听端口被占用等致命错误，由上层提示用户。
        """
        devices = []
        seen_addresses = set()
        sock = None

        try:
            # 创建 UDP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(('0.0.0.0', self.udp_port))
            except OSError as e:
                # 本机端口被其他程序占用时明确报错，而不是静默返回空列表，
                # 避免把「端口被占用」误判成「局域网内没有设备」。
                # Windows 下对被 SO_EXCLUSIVEADDRUSE 占用的端口再绑定，
                # 报的是 WSAEACCES(10013) 而非 WSAEADDRINUSE(10048)，两者都算占用。
                if e.errno in (errno.EADDRINUSE, errno.EACCES, 10048, 10013):
                    raise OSError(
                        f"本机 UDP 端口 {self.udp_port} 已被其他程序占用，"
                        f"无法搜索局域网设备；请关闭占用该端口的程序后重试") from e
                raise
            sock.settimeout(timeout)

            logger.info(f"开始扫描局域网设备，端口: {self.udp_port}，超时: {timeout}秒")

            # 监听广播消息
            # 注意：timeout 是「总扫描时长」而非单次 recvfrom 超时——服务端
            # 每 2 秒广播一轮，若只等单次超时，持续收到包时扫描永不结束。
            start_time = time.time()
            while True:
                remaining = timeout - (time.time() - start_time)
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    data, addr = sock.recvfrom(1024)

                    # 解析消息
                    message = json.loads(data.decode('utf-8'))

                    # 验证消息
                    if validate_broadcast_message(message):
                        # 通告 IP 不可达时（服务端无外网路由/多网卡选错，
                        # 会通告 127.0.0.1 或链路本地地址），用 UDP 报文真实
                        # 来源地址兜底 —— 能收到包就说明该地址必然可达。
                        ip_address = message.get('ip_address')
                        if not ip_address or ip_address in ('127.0.0.1', '0.0.0.0') \
                                or ip_address.startswith('169.254.'):
                            ip_address = addr[0]
                            message['ip_address'] = ip_address

                        # 避免重复
                        device_key = (ip_address, message.get('tcp_port'))
                        if device_key not in seen_addresses:
                            seen_addresses.add(device_key)
                            devices.append(message)
                            logger.info(f"发现设备: {message.get('device_name')} ({ip_address}:{message.get('tcp_port')})")

                except socket.timeout:
                    # 超时，停止扫描
                    break

                except Exception as e:
                    logger.error(f"接收广播消息失败: {e}")

        except Exception as e:
            # 端口被占用等致命错误向上抛出，由上层（ScanThread）转发为界面提示
            logger.error(f"扫描设备失败: {e}")
            raise

        finally:
            # 关闭 socket
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

        logger.info(f"扫描完成，发现 {len(devices)} 个设备")
        # 同一设备多 IP 通告合并为一行（同网段优先，其余进 alt_addresses）
        return merge_same_device(devices)


# ─────────────────────────────────────────────────────
# 数据客户端
# ─────────────────────────────────────────────────────

class DataClient:
    """
    网络数据客户端

    连接远程服务端，查询历史数据。
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        """
        初始化数据客户端

        参数:
            timeout: 网络超时时间（秒）
        """
        self.timeout = timeout
        self.socket = None
        self.connected = False
        self.server_address = None

    def connect(self, ip: str, port: int = DEFAULT_TCP_PORT) -> bool:
        """
        连接到远程服务端

        参数:
            ip: 服务端 IP 地址
            port: 服务端端口

        返回:
            bool: 是否成功
        """
        try:
            # 关闭已有连接
            self.disconnect()

            # 创建 TCP socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(self.timeout)

            # 连接服务端
            self.socket.connect((ip, port))
            self.server_address = (ip, port)
            self.connected = True

            logger.info(f"已连接到服务端: {ip}:{port}")
            return True

        except Exception as e:
            logger.error(f"连接服务端失败: {e}")
            self.connected = False
            return False

    def disconnect(self) -> None:
        """断开连接"""
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            finally:
                self.socket = None

        self.connected = False
        self.server_address = None
        logger.info("已断开连接")

    def is_connected(self) -> bool:
        """
        检查是否已连接

        返回:
            bool: 是否已连接
        """
        return self.connected

    def get_session_list(self, limit: int = 20, start_time: Optional[float] = None,
                        end_time: Optional[float] = None) -> Optional[List[Dict]]:
        """
        查询会话列表

        参数:
            limit: 返回数量限制
            start_time: 开始时间过滤（可选）
            end_time: 结束时间过滤（可选）

        返回:
            List[Dict]: 会话列表，失败返回 None
        """
        if not self.connected:
            logger.error("未连接到服务端")
            return None

        # 创建请求
        request = create_request(
            REQUEST_GET_SESSION_LIST,
            limit=limit,
            start_time=start_time,
            end_time=end_time
        )

        # 发送请求并接收响应
        response = self._send_request(request)

        if response and response.get('status') == RESPONSE_OK:
            return response.get('data', {}).get('sessions', [])
        else:
            logger.error(f"查询会话列表失败: {response}")
            return None

    def get_channel_config(self, session_id: str) -> Optional[List[Dict]]:
        """
        查询通道配置

        参数:
            session_id: 会话 ID

        返回:
            List[Dict]: 通道配置列表，失败返回 None
        """
        if not self.connected:
            logger.error("未连接到服务端")
            return None

        # 创建请求
        request = create_request(
            REQUEST_GET_CHANNEL_CONFIG,
            session_id=session_id
        )

        # 发送请求并接收响应
        response = self._send_request(request)

        if response and response.get('status') == RESPONSE_OK:
            return response.get('data', {}).get('channels', [])
        else:
            logger.error(f"查询通道配置失败: {response}")
            return None

    def get_temperature_data(self, session_id: str, channels: Optional[List[str]] = None,
                            start_time: Optional[float] = None, end_time: Optional[float] = None,
                            limit: int = 10000, offset: int = 0) -> Optional[Dict]:
        """
        查询温度数据

        参数:
            session_id: 会话 ID
            channels: 通道键列表（可选）
            start_time: 开始时间戳（可选）
            end_time: 结束时间戳（可选）
            limit: 返回数量限制
            offset: 偏移量

        返回:
            Dict: 温度数据，包含 'data', 'total', 'format' 字段，失败返回 None
        """
        if not self.connected:
            logger.error("未连接到服务端")
            return None

        # 创建请求
        request = create_request(
            REQUEST_GET_TEMPERATURE_DATA,
            session_id=session_id,
            channels=channels,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            offset=offset
        )

        # 发送请求并接收响应
        response = self._send_request(request)

        if response and response.get('status') == RESPONSE_OK:
            return response.get('data')
        else:
            logger.error(f"查询温度数据失败: {response}")
            return None

    def get_all_temperature_data(self, session_id: str,
                                 batch_size: int = 20000,
                                 progress_cb=None) -> Optional[Dict]:
        """循环分页拉取远端会话的全部温度数据。

        服务端 get_temperature_data 支持 limit/offset 分页；逐批拉取并合并，
        直到拉完 total 条。用于长时间老化（数天）产生的超大会话，避免单次
        拉取被 limit 截断导致数据丢失。

        参数:
            session_id: 会话 ID
            batch_size: 每批拉取条数（默认 2 万）
            progress_cb: 进度回调（已拉取条数, 总条数），供界面显示进度

        返回:
            Dict: 合并后的数据 {'data': [...], 'total': N, 'format': 'list'}；
                  未连接或拉取不到数据时返回 None
        """
        if not self.connected:
            logger.error("未连接到服务端")
            return None

        merged = []
        offset = 0
        total = None

        while True:
            batch = self.get_temperature_data(
                session_id, limit=batch_size, offset=offset)
            if not batch or not batch.get('data'):
                break
            rows = batch['data']
            merged.extend(rows)
            offset += len(rows)
            total = batch.get('total') or total

            if progress_cb is not None:
                try:
                    progress_cb(offset, total)
                except Exception:
                    pass

            # 已拉完或剩余为空，停止
            if total and offset >= total:
                break
            if not rows:
                break

        if not merged:
            return None
        return {'data': merged, 'total': len(merged), 'format': 'list'}

    def get_session_stats(self, session_id: str) -> Optional[Dict]:
        """
        查询会话统计信息

        参数:
            session_id: 会话 ID

        返回:
            Dict: 统计信息字典，失败返回 None
        """
        if not self.connected:
            logger.error("未连接到服务端")
            return None

        # 创建请求
        request = create_request(
            REQUEST_GET_SESSION_STATS,
            session_id=session_id
        )

        # 发送请求并接收响应
        response = self._send_request(request)

        if response and response.get('status') == RESPONSE_OK:
            return response.get('data', {}).get('stats', {})
        else:
            logger.error(f"查询统计信息失败: {response}")
            return None

    def _send_request(self, request: Dict) -> Optional[Dict]:
        """
        发送请求并接收响应

        参数:
            request: 请求字典

        返回:
            Dict: 响应字典，失败返回 None
        """
        try:
            # 发送请求
            if not send_message(self.socket, request, compress=True):
                logger.error("发送请求失败")
                return None

            # 接收响应
            response = receive_message(self.socket, timeout=self.timeout)

            if not response:
                logger.error("未收到响应")
                return None

            return response

        except Exception as e:
            logger.error(f"请求失败: {e}")
            return None


# ─────────────────────────────────────────────────────
# 远程会话
# ─────────────────────────────────────────────────────

class RemoteSession:
    """
    远程会话数据模型

    封装从远程服务端获取的会话数据，提供与本地会话一致的接口。
    """

    def __init__(self, session_id: str, session_data: Dict,
                 channel_config: List[Dict], temperature_data: Dict):
        """
        初始化远程会话

        参数:
            session_id: 会话 ID
            session_data: 会话元数据（字典）
            channel_config: 通道配置（列表）
            temperature_data: 温度数据（字典或列表）
        """
        self.session_id = session_id
        self.session_data = session_data
        self.channel_config = channel_config
        self.temperature_data = temperature_data

    @property
    def session_name(self) -> Optional[str]:
        """获取会话名称"""
        return self.session_data.get('session_name')

    @property
    def started_at(self) -> float:
        """获取开始时间"""
        return self.session_data.get('started_at', 0.0)

    @property
    def stopped_at(self) -> Optional[float]:
        """获取停止时间"""
        return self.session_data.get('stopped_at')

    @property
    def channel_count(self) -> int:
        """获取通道数量"""
        return self.session_data.get('channel_count', 0)

    @property
    def interval_seconds(self) -> float:
        """获取采集间隔"""
        return self.session_data.get('interval_seconds', 1.0)

    def get_channels(self) -> List[str]:
        """
        获取通道键列表

        返回:
            List[str]: 通道键列表
        """
        return [ch.get('channel_key') for ch in self.channel_config]

    def get_channel_name(self, channel_key: str) -> Optional[str]:
        """
        获取通道显示名称

        参数:
            channel_key: 通道键

        返回:
            str: 通道名称
        """
        for ch in self.channel_config:
            if ch.get('channel_key') == channel_key:
                return ch.get('channel_name') or ch.get('channel_key')
        return None

    def get_channel_color(self, channel_key: str) -> Optional[str]:
        """
        获取通道颜色

        参数:
            channel_key: 通道键

        返回:
            str: 颜色代码（#RRGGBB）
        """
        for ch in self.channel_config:
            if ch.get('channel_key') == channel_key:
                return ch.get('color')
        return None

    def get_temperature_readings(self, channel_key: str) -> List[Dict]:
        """
        获取指定通道的温度读数

        参数:
            channel_key: 通道键

        返回:
            List[Dict]: 温度读数列表，每个包含:
                - timestamp: 时间戳
                - temperature: 温度值
                - is_valid: 是否有效
        """
        # 根据数据格式处理
        if isinstance(self.temperature_data, dict):
            # 按通道分组的数据
            return self.temperature_data.get(channel_key, [])
        else:
            # 列表数据，过滤指定通道
            return [
                rd for rd in self.temperature_data
                if rd.get('channel_key') == channel_key
            ]

    def to_dict(self) -> Dict:
        """
        转换为字典

        返回:
            Dict: 会话数据字典
        """
        return {
            'session_id': self.session_id,
            'session_data': self.session_data,
            'channel_config': self.channel_config,
            'temperature_data': self.temperature_data
        }