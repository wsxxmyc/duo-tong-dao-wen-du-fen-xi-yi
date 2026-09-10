"""
服务管理器模块

统一管理 TCP Socket、HTTP REST API 和配对码三种网络服务。
"""

from __future__ import annotations

from typing import Dict, Any, Optional
from dataclasses import dataclass
import logging
import threading

# 配置日志
logger = logging.getLogger(__name__)


@dataclass
class ServiceStatus:
    """
    服务状态信息
    
    Attributes:
        enabled: 服务是否启用（运行中）
        port: 服务监听的端口号
        error: 错误信息（如果服务启动失败）
    """
    enabled: bool = False
    port: Optional[int] = None
    error: Optional[str] = None


class ServiceManager:
    """
    多模式服务管理器
    
    统一管理 TCP Socket、HTTP REST API 和配对码三种服务，
    提供服务启动、停止和状态查询功能。
    
    Attributes:
        history_db: 历史数据库管理器实例
        device_name: 设备名称（用于广播）
        services: 服务状态字典，包含 tcp、http、pairing_code 三种服务
    """
    
    # 默认端口配置
    DEFAULT_TCP_PORT = 9527
    DEFAULT_HTTP_PORT = 8080
    DEFAULT_PAIRING_CODE_PORT = 9526  # UDP 端口
    
    def __init__(self, history_db: Any, device_name: str = "多通道温度分析仪",
                 advertise_ip: str = ""):
        """
        初始化服务管理器

        Args:
            history_db: 历史数据库管理器实例
            device_name: 设备名称，用于网络广播和识别
            advertise_ip: 对外通告的网卡 IP（空 = 自动选择默认路由出口 IP）；
                配置后 TCP 与配对码广播只通告该地址
        """
        self.history_db = history_db
        self.device_name = device_name
        self.advertise_ip = advertise_ip or ""

        # 线程锁，保护服务状态更新
        self._lock = threading.Lock()

        # 服务实例
        self.tcp_server: Optional[Any] = None
        self.http_server: Optional[Any] = None
        self.pairing_code_server: Optional[Any] = None

        # 服务状态字典
        self.services: Dict[str, ServiceStatus] = {
            'tcp': ServiceStatus(),
            'http': ServiceStatus(),
            'pairing_code': ServiceStatus()
        }

        logger.info(f"服务管理器已初始化 - 设备名称: {device_name}")
    
    def start_service(self, service_type: str, port: Optional[int] = None) -> bool:
        """
        启动指定类型的服务

        Args:
            service_type: 服务类型，可选值：'tcp', 'http', 'pairing_code'
            port: 监听端口（可选，不指定则使用默认端口）

        Returns:
            bool: 启动是否成功

        Raises:
            ValueError: 服务类型无效时抛出
        """
        if service_type not in self.services:
            raise ValueError(f"未知的服务类型: {service_type}，可选值: tcp, http, pairing_code")

        # 加锁保护服务状态检查和更新
        with self._lock:
            # 检查服务是否已在运行
            if self.services[service_type].enabled:
                logger.warning(f"{service_type} 服务已在运行，端口: {self.services[service_type].port}")
                return True

            try:
                # 端口冲突检测：TCP 与 HTTP 不得使用相同端口
                if service_type == 'tcp':
                    effective_port = port or self.DEFAULT_TCP_PORT
                    http_status = self.services['http']
                    if http_status.enabled and http_status.port == effective_port:
                        raise RuntimeError(
                            f"端口 {effective_port} 已被 HTTP 服务占用，请更换 TCP 服务端口")
                elif service_type == 'http':
                    effective_port = port or self.DEFAULT_HTTP_PORT
                    tcp_status = self.services['tcp']
                    if tcp_status.enabled and tcp_status.port == effective_port:
                        raise RuntimeError(
                            f"端口 {effective_port} 已被 TCP 服务占用，请更换 HTTP 服务端口")

                # 根据服务类型启动对应服务
                if service_type == 'tcp':
                    self._start_tcp_server(port or self.DEFAULT_TCP_PORT)
                elif service_type == 'http':
                    self._start_http_server(port or self.DEFAULT_HTTP_PORT)
                elif service_type == 'pairing_code':
                    self._start_pairing_code_server()

                # 标记服务已启用
                self.services[service_type].enabled = True
                self.services[service_type].error = None

                # TCP / HTTP 端口变动后同步给运行中的配对码广播：配对码报文
                # 里带 tcp_port / http_port，客户端据此连接，不同步会让客户端
                # 拿到构造默认值（旧端口）而连不上。
                if service_type in ('tcp', 'http'):
                    self._sync_pairing_advertised_ports()

                logger.info(f"{service_type} 服务已成功启动，端口: {self.services[service_type].port}")
                return True

            except Exception as e:
                # 记录错误信息
                error_msg = f"启动 {service_type} 服务失败: {str(e)}"
                self.services[service_type].error = error_msg
                # 清空端口信息
                self.services[service_type].port = None
                logger.error(error_msg, exc_info=True)
                return False
    
    def stop_service(self, service_type: str) -> bool:
        """
        停止指定类型的服务

        Args:
            service_type: 服务类型，可选值：'tcp', 'http', 'pairing_code'

        Returns:
            bool: 停止是否成功

        Raises:
            ValueError: 服务类型无效时抛出
        """
        if service_type not in self.services:
            raise ValueError(f"未知的服务类型: {service_type}，可选值: tcp, http, pairing_code")

        # 加锁保护服务状态检查和更新
        with self._lock:
            # 检查服务是否已停止
            if not self.services[service_type].enabled:
                logger.warning(f"{service_type} 服务未在运行")
                return True

            try:
                # 根据服务类型停止对应服务
                if service_type == 'tcp':
                    self._stop_tcp_server()
                elif service_type == 'http':
                    self._stop_http_server()
                elif service_type == 'pairing_code':
                    self._stop_pairing_code_server()

                # 原子性地重置服务状态（创建新的 ServiceStatus 对象）
                self.services[service_type] = ServiceStatus()

                # 端口释放后同步配对码广播，避免继续通告已失效的端口
                if service_type in ('tcp', 'http'):
                    self._sync_pairing_advertised_ports()

                logger.info(f"{service_type} 服务已成功停止")
                return True

            except Exception as e:
                # 记录错误信息
                error_msg = f"停止 {service_type} 服务失败: {str(e)}"
                self.services[service_type].error = error_msg
                logger.error(error_msg, exc_info=True)
                return False
    
    def stop_all_services(self) -> None:
        """
        停止所有服务

        按顺序停止所有已启用的服务，即使某个服务停止失败也会继续停止其他服务。
        """
        logger.info("开始停止所有服务")

        # 先获取需要停止的服务列表（避免在迭代时修改）
        with self._lock:
            services_to_stop = [
                st for st, status in self.services.items()
                if status.enabled
            ]

        # 逐个停止服务
        for service_type in services_to_stop:
            try:
                self.stop_service(service_type)
            except Exception as e:
                logger.error(f"停止 {service_type} 服务时发生异常: {e}", exc_info=True)

        logger.info("所有服务停止完成")
    
    def get_service_status(self, service_type: str) -> ServiceStatus:
        """
        获取指定服务的状态
        
        Args:
            service_type: 服务类型，可选值：'tcp', 'http', 'pairing_code'
        
        Returns:
            ServiceStatus: 服务状态对象
        
        Raises:
            ValueError: 服务类型无效时抛出
        """
        if service_type not in self.services:
            raise ValueError(f"未知的服务类型: {service_type}，可选值: tcp, http, pairing_code")
        
        return self.services[service_type]
    
    def get_all_services_status(self) -> Dict[str, ServiceStatus]:
        """
        获取所有服务的状态
        
        Returns:
            Dict[str, ServiceStatus]: 服务类型到状态的映射字典
        """
        return self.services.copy()
    
    def get_pairing_code(self) -> Optional[str]:
        """
        获取当前配对码（仅当配对码服务运行时可用）
        
        Returns:
            Optional[str]: 配对码字符串，如果服务未运行则返回 None
        """
        if not self.services['pairing_code'].enabled or not self.pairing_code_server:
            logger.warning("配对码服务未运行，无法获取配对码")
            return None
        
        try:
            return self.pairing_code_server.get_pairing_code()
        except Exception as e:
            logger.error(f"获取配对码失败: {e}", exc_info=True)
            return None
    
    def get_discovery_status(self) -> Dict[str, Dict[str, Any]]:
        """
        获取各广播服务的健康状态（供界面显示，排查「客户端搜不到」）。

        返回:
            Dict[str, Dict[str, Any]]: 服务类型（tcp / pairing_code）到
                {'last_broadcast_at': float|None, 'broadcast_error': str|None} 的映射；
                服务未启动或广播组件缺失时不包含对应键。
        """
        result: Dict[str, Dict[str, Any]] = {}

        if self.tcp_server is not None:
            broadcaster = getattr(self.tcp_server, 'udp_broadcaster', None)
            if broadcaster is not None:
                result['tcp'] = {
                    'last_broadcast_at': getattr(
                        broadcaster, 'last_broadcast_at', None),
                    'broadcast_error': getattr(
                        broadcaster, 'broadcast_error', None),
                }

        if self.pairing_code_server is not None:
            result['pairing_code'] = {
                'last_broadcast_at': getattr(
                    self.pairing_code_server, 'last_broadcast_at', None),
                'broadcast_error': getattr(
                    self.pairing_code_server, 'broadcast_error', None),
            }

        return result
    
    def _start_tcp_server(self, port: int) -> None:
        """
        启动 TCP Socket 服务器
        
        Args:
            port: 监听端口号
        
        Raises:
            Exception: 启动失败时抛出
        """
        try:
            from .server import DataServer
            
            # 创建并启动 TCP 服务器
            self.tcp_server = DataServer(
                history_db=self.history_db,
                device_name=self.device_name,
                tcp_port=port,
                advertise_ip=self.advertise_ip
            )
            self.tcp_server.start()

            # 记录实际监听端口（端口被占用时 DataServer 会自动顺延）
            actual_port = self.tcp_server.tcp_port
            self.services['tcp'].port = actual_port
            if actual_port != port:
                logger.warning(f"TCP 端口 {port} 被占用，服务实际运行在端口 {actual_port}")
            
        except ImportError as e:
            raise RuntimeError(f"无法导入 DataServer: {e}")
        except Exception:
            # 确保清理资源
            if self.tcp_server:
                try:
                    self.tcp_server.stop()
                except Exception:
                    pass
                finally:
                    self.tcp_server = None
            raise
    
    def _stop_tcp_server(self) -> None:
        """
        停止 TCP Socket 服务器
        """
        if self.tcp_server:
            try:
                self.tcp_server.stop()
            finally:
                self.tcp_server = None
    
    def _start_http_server(self, port: int) -> None:
        """
        启动 HTTP REST API 服务器
        
        Args:
            port: 监听端口号
        
        Raises:
            Exception: 启动失败时抛出
        """
        try:
            from .http_server import HTTPDataServer
            
            # 创建并启动 HTTP 服务器
            self.http_server = HTTPDataServer(
                history_db=self.history_db,
                port=port
            )
            self.http_server.start()
            
            # 记录端口信息
            self.services['http'].port = port
            
        except ImportError as e:
            raise RuntimeError(f"无法导入 HTTPDataServer: {e}")
        except Exception:
            # 确保清理资源
            if self.http_server:
                try:
                    self.http_server.stop()
                except Exception:
                    pass
                finally:
                    self.http_server = None
            raise
    
    def _stop_http_server(self) -> None:
        """
        停止 HTTP REST API 服务器
        """
        if self.http_server:
            try:
                self.http_server.stop()
            finally:
                self.http_server = None
    
    def _advertised_port(self, service_type: str) -> int:
        """返回配对码广播应通告的端口：运行中取实际端口，否则取配置端口。

        TCP 端口被占用时会自动顺延，因此运行中必须以实际端口为准；
        服务未启动时取持久化的配置端口（下次启动会用它）。
        """
        status = self.services.get(service_type)
        if status is not None and status.enabled and status.port:
            return int(status.port)
        fallback = (self.DEFAULT_TCP_PORT if service_type == 'tcp'
                    else self.DEFAULT_HTTP_PORT)
        try:
            from utils.config_io import ConfigIO
            cfg = ConfigIO.load_network_service_config()
            key = 'tcp_port' if service_type == 'tcp' else 'http_port'
            return int(cfg.get(key) or fallback)
        except Exception:
            return fallback

    def _sync_pairing_advertised_ports(self) -> None:
        """把当前 TCP / HTTP 端口同步给运行中的配对码广播。

        配对码报文携带 tcp_port / http_port 供客户端连接；服务启停或端口
        变更后不同步会让广播继续通告旧端口（脏数据）。
        """
        server = self.pairing_code_server
        if server is None or not self.services['pairing_code'].enabled:
            return
        try:
            server.tcp_port = self._advertised_port('tcp')
            server.http_port = self._advertised_port('http')
        except Exception as e:
            logger.warning(f"同步配对码广播端口失败: {e}")

    def _start_pairing_code_server(self) -> None:
        """
        启动配对码服务器

        配对码服务使用固定的 UDP 端口 9526；广播报文里携带的 TCP / HTTP
        端口取当前实际运行端口（未运行则取持久化配置的端口），避免通告
        构造默认值导致客户端连到错误端口。

        Raises:
            Exception: 启动失败时抛出
        """
        try:
            from .pairing_code_server import PairingCodeServer

            # 创建并启动配对码服务器
            self.pairing_code_server = PairingCodeServer(
                history_db=self.history_db,
                device_name=self.device_name,
                udp_port=self.DEFAULT_PAIRING_CODE_PORT,
                advertise_ip=self.advertise_ip,
                tcp_port=self._advertised_port('tcp'),
                http_port=self._advertised_port('http'),
            )
            self.pairing_code_server.start()
            
            # 记录端口信息
            self.services['pairing_code'].port = self.DEFAULT_PAIRING_CODE_PORT
            
        except ImportError as e:
            raise RuntimeError(f"无法导入 PairingCodeServer: {e}")
        except Exception:
            # 确保清理资源
            if self.pairing_code_server:
                try:
                    self.pairing_code_server.stop()
                except Exception:
                    pass
                finally:
                    self.pairing_code_server = None
            raise
    
    def _stop_pairing_code_server(self) -> None:
        """
        停止配对码服务器
        """
        if self.pairing_code_server:
            try:
                self.pairing_code_server.stop()
            finally:
                self.pairing_code_server = None
    
    def __repr__(self) -> str:
        """
        返回服务管理器的字符串表示
        
        Returns:
            str: 包含设备名称和各服务状态的字符串
        """
        status_parts = []
        for service_type, status in self.services.items():
            state = "运行中" if status.enabled else "已停止"
            port_info = f"端口:{status.port}" if status.port else ""
            error_info = f"错误:{status.error}" if status.error else ""
            status_parts.append(f"{service_type}={state} {port_info} {error_info}".strip())
        
        return f"<ServiceManager device={self.device_name} {', '.join(status_parts)}>"