"""
网络模块

提供远程数据访问功能，包括服务端和客户端。
"""

from .server import DataServer, UDPBroadcaster, TCPHandler
from .client import DataClient, DeviceScanner, RemoteSession
from .http_server import HTTPDataServer, create_http_server
from .pairing_code_server import PairingCodeServer
from .service_manager import ServiceManager, ServiceStatus
from .protocol import (
    REQUEST_GET_SESSION_LIST,
    REQUEST_GET_CHANNEL_CONFIG,
    REQUEST_GET_TEMPERATURE_DATA,
    REQUEST_GET_SESSION_STATS,
    RESPONSE_OK,
    RESPONSE_ERROR
)

__all__ = [
    # 服务端
    'DataServer',
    'UDPBroadcaster',
    'TCPHandler',

    # HTTP 服务端
    'HTTPDataServer',
    'create_http_server',

    # 配对码服务端
    'PairingCodeServer',

    # 服务管理器
    'ServiceManager',
    'ServiceStatus',

    # 客户端
    'DataClient',
    'DeviceScanner',
    'RemoteSession',

    # 协议常量
    'REQUEST_GET_SESSION_LIST',
    'REQUEST_GET_CHANNEL_CONFIG',
    'REQUEST_GET_TEMPERATURE_DATA',
    'REQUEST_GET_SESSION_STATS',
    'RESPONSE_OK',
    'RESPONSE_ERROR'
]