"""
网络协议定义

定义客户端-服务端通信的协议常量和辅助函数。
"""

import json
import gzip
import struct
from typing import Dict, Any, Optional

# ─────────────────────────────────────────────────────
# 协议常量
# ─────────────────────────────────────────────────────

# 请求类型
REQUEST_GET_SESSION_LIST = "get_session_list"
REQUEST_GET_CHANNEL_CONFIG = "get_channel_config"
REQUEST_GET_TEMPERATURE_DATA = "get_temperature_data"
REQUEST_GET_SESSION_STATS = "get_session_stats"

# 响应状态
RESPONSE_OK = "ok"
RESPONSE_ERROR = "error"

# UDP 广播标识
APP_IDENTIFIER = "MTA-TEMP-ANALYZER"

# 默认端口
DEFAULT_UDP_PORT = 9526
DEFAULT_TCP_PORT = 9527

# 默认广播间隔（秒）
# 必须小于 DEFAULT_SCAN_TIMEOUT（3.0 秒），否则扫描窗口可能错过整轮广播，
# 导致「服务已启动但局域网扫描找不到设备」。
DEFAULT_BROADCAST_INTERVAL = 2.0

# 默认超时（秒）
DEFAULT_TIMEOUT = 10.0
DEFAULT_SCAN_TIMEOUT = 3.0

# 数据压缩阈值（字节）
COMPRESSION_THRESHOLD = 1024  # 1KB

# ─────────────────────────────────────────────────────
# 协议辅助函数
# ─────────────────────────────────────────────────────

def create_broadcast_message(device_name: str, tcp_port: int,
                            version: str = "1.2.0",
                            ip_address: Optional[str] = None) -> Dict[str, Any]:
    """
    创建 UDP 广播消息

    参数:
        device_name: 设备名称
        tcp_port: TCP 端口
        version: 应用版本
        ip_address: IP 地址（可选）

    返回:
        Dict: 广播消息字典
    """
    import time

    message = {
        "app": APP_IDENTIFIER,
        "version": version,
        "device_name": device_name,
        "tcp_port": tcp_port,
        "timestamp": int(time.time())
    }

    if ip_address:
        message["ip_address"] = ip_address

    return message


def create_request(request_type: str, **kwargs) -> Dict[str, Any]:
    """
    创建请求消息

    参数:
        request_type: 请求类型
        **kwargs: 请求参数

    返回:
        Dict: 请求消息字典
    """
    request = {
        "type": request_type
    }
    request.update(kwargs)
    return request


def create_response(status: str, data: Any = None,
                   message: str = None) -> Dict[str, Any]:
    """
    创建响应消息

    参数:
        status: 响应状态（ok 或 error）
        data: 响应数据
        message: 错误消息（仅当 status=error）

    返回:
        Dict: 响应消息字典
    """
    response = {
        "status": status
    }

    if data is not None:
        response["data"] = data

    if message:
        response["message"] = message

    return response


def serialize_message(message: Dict[str, Any], compress: bool = False) -> tuple:
    """
    序列化消息（可选 gzip 压缩）

    参数:
        message: 消息字典
        compress: 是否压缩

    返回:
        tuple: (序列化后的字节数据, 是否压缩标志)
    """
    # 转换为 JSON
    json_data = json.dumps(message, ensure_ascii=False)

    # 编码为 UTF-8
    data = json_data.encode('utf-8')

    # 可选压缩
    compressed = False
    if compress and len(data) > COMPRESSION_THRESHOLD:
        data = gzip.compress(data)
        compressed = True

    return data, compressed


def deserialize_message(data: bytes, compressed: bool = False) -> Dict[str, Any]:
    """
    反序列化消息（支持 gzip 解压）

    参数:
        data: 字节数据
        compressed: 数据是否已压缩

    返回:
        Dict: 消息字典
    """
    # 解压（如果需要）
    if compressed:
        data = gzip.decompress(data)

    # 解码为 UTF-8
    json_data = data.decode('utf-8')

    # 解析 JSON
    return json.loads(json_data)


def send_message(sock: Any, message: Dict[str, Any], compress: bool = False) -> bool:
    """
    发送消息（带长度前缀）

    协议格式：
    - 前 4 字节：数据长度（网络字节序，大端）
    - 第 5 字节：压缩标志（0=未压缩，1=已压缩）
    - 后续字节：消息数据

    参数:
        sock: socket 对象
        message: 消息字典
        compress: 是否压缩

    返回:
        bool: 是否成功
    """
    try:
        # 序列化消息
        data, compressed = serialize_message(message, compress)

        # 构造数据包：长度(4B) + 压缩标志(1B) + 数据
        length = len(data)
        flags = 1 if compressed else 0
        header = struct.pack('!IB', length, flags)

        # 发送数据
        sock.sendall(header + data)
        return True

    except Exception as e:
        print(f"发送消息失败: {e}")
        return False


def receive_message(sock: Any, timeout: float = DEFAULT_TIMEOUT) -> Optional[Dict[str, Any]]:
    """
    接收消息（带长度前缀）

    参数:
        sock: socket 对象
        timeout: 超时时间（秒）

    返回:
        Dict: 消息字典，失败返回 None
    """
    try:
        # 设置超时
        sock.settimeout(timeout)

        # 接收头部（5 字节）
        header = _recv_exact(sock, 5)
        if not header:
            return None

        # 解析头部
        length, flags = struct.unpack('!IB', header)
        compressed = (flags == 1)

        # 接收数据
        data = _recv_exact(sock, length)
        if not data:
            return None

        # 反序列化
        return deserialize_message(data, compressed)

    except Exception as e:
        print(f"接收消息失败: {e}")
        return None


def _recv_exact(sock: Any, length: int) -> Optional[bytes]:
    """
    接收精确长度的数据

    参数:
        sock: socket 对象
        length: 期望长度

    返回:
        bytes: 接收的数据，失败返回 None
    """
    data = b''
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            return None
        data += chunk

    return data


def validate_broadcast_message(message: Dict[str, Any]) -> bool:
    """
    验证广播消息是否来自 多通道温度分析仪设备

    参数:
        message: 消息字典

    返回:
        bool: 是否有效
    """
    return (
        isinstance(message, dict) and
        message.get('app') == APP_IDENTIFIER and
        'device_name' in message and
        'tcp_port' in message
    )