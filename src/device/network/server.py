"""
网络服务端模块

提供 UDP 广播和 TCP 数据查询服务。
"""

import socket
import threading
import time
import json
from typing import Callable, Dict, Any, Optional
import logging

from .protocol import (
    create_broadcast_message,
    create_response,
    send_message,
    receive_message,
    REQUEST_GET_SESSION_LIST,
    REQUEST_GET_CHANNEL_CONFIG,
    REQUEST_GET_TEMPERATURE_DATA,
    REQUEST_GET_SESSION_STATS,
    RESPONSE_OK,
    RESPONSE_ERROR,
    DEFAULT_UDP_PORT,
    DEFAULT_TCP_PORT,
    DEFAULT_BROADCAST_INTERVAL,
)
from .netutil import get_local_ips, subnet_broadcast_addresses, LIMITED_BROADCAST

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 单连接空闲超时（秒）：同一连接连续处理多个请求时，客户端在两次请求之间
# 可能有用户交互（如选择会话），空闲超过该时长才断开连接。
CONNECTION_IDLE_TIMEOUT = 60.0


# ─────────────────────────────────────────────────────
# UDP 广播器
# ─────────────────────────────────────────────────────

class UDPBroadcaster:
    """
    UDP 设备广播器

    在局域网中定期广播设备信息，让客户端可以发现服务端。
    """

    def __init__(self, device_name: str, tcp_port: int = DEFAULT_TCP_PORT,
                 udp_port: int = DEFAULT_UDP_PORT,
                 interval: float = DEFAULT_BROADCAST_INTERVAL,
                 version: str = "1.2.0",
                 advertise_ip: str = ""):
        """
        初始化 UDP 广播器

        参数:
            device_name: 设备名称
            tcp_port: TCP 服务端口
            udp_port: UDP 广播端口
            interval: 广播间隔（秒）
            version: 应用版本
            advertise_ip: 对外通告的网卡 IP（空 = 自动选择默认路由出口 IP）；
                多网卡机器手动指定后只通告该地址，客户端扫描列表不出现重复行
        """
        self.device_name = device_name
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.interval = interval
        self.version = version
        self.advertise_ip = advertise_ip or ""

        self.running = False
        self.thread = None
        self.socket = None

        # 广播健康状态（供 UI 显示：最近发送时间 / 最近失败原因）
        self.last_broadcast_at: Optional[float] = None
        self.broadcast_error: Optional[str] = None

    @staticmethod
    def _resolve_advertise_ips(advertise_ip: str, local_ips) -> list:
        """按配置的对外网卡 IP 过滤本机地址列表。

        配置地址已不在本机网卡（网卡变更 / 禁用）时回退默认路由出口 IP，
        避免广播静默失效。
        """
        if not advertise_ip:
            return list(local_ips)
        if advertise_ip in local_ips:
            return [advertise_ip]
        logger.warning(
            f"配置的对外网卡 {advertise_ip} 不在本机网卡列表中，"
            f"回退默认出口 IP {local_ips[:1]}")
        return list(local_ips[:1])

    def start(self):
        """启动广播"""
        if self.running:
            logger.warning("UDP 广播器已在运行")
            return

        self.running = True
        self.thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self.thread.start()
        logger.info(f"UDP 广播器已启动，端口: {self.udp_port}")

    def stop(self):
        """停止广播"""
        self.running = False

        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            finally:
                self.socket = None

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

        logger.info("UDP 广播器已停止")

    def _broadcast_loop(self):
        """广播循环

        每个周期向受限广播地址（255.255.255.255）和各网卡 /24 子网广播地址
        分别发送广播；每个本机 IP 通告一条对应消息，供客户端选择可达地址。
        """
        try:
            # 创建 UDP socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            while self.running:
                try:
                    # 枚举本机可通告 IP（配置了对外网卡则只通告该 IP；
                    # 否则默认路由出口 IP 优先的全部地址）
                    local_ips = self._resolve_advertise_ips(
                        self.advertise_ip, get_local_ips())
                    subnet_targets = subnet_broadcast_addresses(local_ips)

                    for ip_address in local_ips:
                        # 创建广播消息，通告当前 IP
                        message = create_broadcast_message(
                            device_name=self.device_name,
                            tcp_port=self.tcp_port,
                            version=self.version,
                            ip_address=ip_address
                        )
                        data = json.dumps(message).encode('utf-8')

                        # 目标：受限广播 + 本 IP 对应子网广播
                        targets = [LIMITED_BROADCAST]
                        targets.extend(subnet_targets)

                        for target in targets:
                            self.socket.sendto(data, (target, self.udp_port))
                            # 每次发成功立即记录，避免停止竞态下整轮被跳过
                            self.last_broadcast_at = time.time()
                            self.broadcast_error = None
                            logger.debug(
                                f"广播消息已发送 → {target}:{self.udp_port} "
                                f"ip={ip_address}")

                except Exception as e:
                    # 正常停止（stop 关闭 socket 引发）不记录为广播失败
                    if self.running:
                        logger.error(f"发送广播失败: {e}")
                        self.broadcast_error = str(e)

                # 等待下一次广播
                time.sleep(self.interval)

        except Exception as e:
            logger.error(f"广播循环异常: {e}")
            self.running = False


# ─────────────────────────────────────────────────────
# TCP 请求处理器
# ─────────────────────────────────────────────────────

def _port_in_use(host: str, port: int) -> bool:
    """探测指定端口是否已被占用。

    Windows 下 SO_REUSEADDR 允许重复绑定同一端口（bind 不报错但连接会被
    先占用者劫持），因此不能依赖 bind 异常判断占用；这里用 Windows 专属的
    SO_EXCLUSIVEADDRUSE 重新探测：绑定成功说明空闲，失败说明已被占用。

    返回:
        bool: True 表示端口已被占用，False 表示空闲。
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        except OSError:
            pass  # 非 Windows 平台无此选项，退回普通 bind 探测
        s.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        s.close()


class TCPHandler:
    """
    TCP 请求处理器

    监听 TCP 连接，处理客户端查询请求。
    每个连接使用独立线程处理，支持并发访问。
    """

    def __init__(self, port: int = DEFAULT_TCP_PORT,
                 request_handler: Callable[[Dict], Dict] = None,
                 max_connections: int = 10):
        """
        初始化 TCP 处理器

        参数:
            port: 监听端口
            request_handler: 请求处理函数
            max_connections: 最大并发连接数
        """
        self.port = port
        self.request_handler = request_handler
        self.max_connections = max_connections

        self.running = False
        self.thread = None
        self.socket = None
        self.client_threads = []

    MAX_PORT_FALLBACK = 20  # 端口被占用时最多顺延尝试的端口数

    def start(self) -> None:
        """启动 TCP 监听

        从配置端口开始，若该端口已被外部进程占用（Windows 下 SO_REUSEADDR
        重复绑定会导致「假启动成功」且连接被劫持），则自动顺延到下一个
        空闲端口，并将 self.port 更新为实际监听端口。全部尝试失败才抛出。
        """
        if self.running:
            logger.warning("TCP 处理器已在运行")
            return

        last_err = None  # Python 3.8 兼容：不在此处使用 `X | None` 注解
        for offset in range(self.MAX_PORT_FALLBACK):
            port = self.port + offset
            if _port_in_use("0.0.0.0", port):
                logger.warning(f"TCP 端口 {port} 已被占用，尝试下一个端口")
                continue
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.socket.bind(("0.0.0.0", port))
                self.socket.listen(self.max_connections)
                # 设置超时：Windows 上从其他线程 close() 不会解除 accept() 阻塞，
                # 必须让 accept 定期超时返回，以便检查 running 标志退出循环。
                self.socket.settimeout(0.5)

                if port != self.port:
                    logger.warning(f"TCP 端口 {self.port} 被占用，已自动切换到端口 {port}")
                self.port = port
                self.running = True
                self.thread = threading.Thread(target=self._accept_loop, daemon=True)
                self.thread.start()
                logger.info(f"TCP 处理器已启动，端口: {self.port}")
                return
            except OSError as e:
                # 探测后到绑定之间出现竞态（端口又被占用），顺延重试下一个端口
                last_err = e
                logger.warning(f"TCP 端口 {port} 绑定失败: {e}，尝试下一个端口")
                if self.socket:
                    try:
                        self.socket.close()
                    except Exception:
                        pass
                    self.socket = None
                continue

        raise OSError(
            f"TCP 端口 {self.port} ~ {self.port + self.MAX_PORT_FALLBACK - 1} "
            f"均被占用，无法启动: {last_err}")

    def stop(self):
        """停止 TCP 监听"""
        self.running = False

        # 关闭主 socket
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            finally:
                self.socket = None

        # 等待主线程结束
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

        # 等待所有客户端线程结束
        for thread in self.client_threads:
            if thread.is_alive():
                thread.join(timeout=1.0)

        self.client_threads.clear()
        logger.info("TCP 处理器已停止")

    def _accept_loop(self):
        """接受连接循环（监听 socket 已在 start() 中创建并绑定）"""
        try:
            while self.running:
                try:
                    # 接受新连接（阻塞，超时 0.5s）
                    client_socket, client_address = self.socket.accept()

                    # 创建独立线程处理连接
                    thread = threading.Thread(
                        target=self._handle_client,
                        args=(client_socket, client_address),
                        daemon=True
                    )
                    thread.start()
                    self.client_threads.append(thread)

                    # 清理已结束的线程
                    self.client_threads = [t for t in self.client_threads if t.is_alive()]

                except socket.timeout:
                    # 超时返回，重新检查 running 标志
                    continue
                except Exception as e:
                    if self.running:
                        logger.error(f"接受连接失败: {e}")

        except Exception as e:
            logger.error(f"TCP 监听循环异常: {e}")
            self.running = False

        finally:
            # 循环退出后确保监听 socket 真正关闭，释放端口
            try:
                if self.socket:
                    self.socket.close()
            except Exception:
                pass
            finally:
                self.socket = None

    def _handle_client(self, client_socket: socket.socket, client_address: tuple):
        """
        处理客户端连接

        同一连接可连续处理多个请求：客户端查询流程（会话列表 → 通道配置 →
        温度数据）复用同一连接，避免每次查询都新建连接。空闲超过
        CONNECTION_IDLE_TIMEOUT（客户端长时间无请求）才断开。

        参数:
            client_socket: 客户端 socket
            client_address: 客户端地址 (ip, port)
        """
        logger.info(f"客户端连接: {client_address}")

        try:
            while self.running:
                # 接收请求（阻塞，空闲超时返回 None 后退出循环）
                request = receive_message(
                    client_socket, timeout=CONNECTION_IDLE_TIMEOUT)

                if not request:
                    logger.info(f"客户端 {client_address} 断开或空闲超时")
                    return

                logger.debug(f"收到请求: {request}")

                # 处理请求
                if self.request_handler:
                    response = self.request_handler(request)
                else:
                    response = create_response(RESPONSE_ERROR, message="未配置请求处理器")

                # 发送响应（发送失败说明客户端已断开）
                if not send_message(client_socket, response, compress=True):
                    return

                logger.debug(f"响应已发送: {response.get('status')}")

        except Exception as e:
            logger.error(f"处理客户端 {client_address} 时出错: {e}")

        finally:
            # 关闭连接
            try:
                client_socket.close()
            except Exception:
                pass

            logger.info(f"客户端断开: {client_address}")


# ─────────────────────────────────────────────────────
# 数据服务器
# ─────────────────────────────────────────────────────

class DataServer:
    """
    网络数据服务器

    整合 UDP 广播和 TCP 查询服务，提供完整的远程数据访问功能。
    """

    def __init__(self, history_db, device_name: str = "多通道温度分析仪",
                 tcp_port: int = DEFAULT_TCP_PORT,
                 udp_port: int = DEFAULT_UDP_PORT,
                 version: str = "1.2.0",
                 advertise_ip: str = ""):
        """
        初始化数据服务器

        参数:
            history_db: 数据库管理器（HistoryDatabase 实例）
            device_name: 设备名称
            tcp_port: TCP 端口
            udp_port: UDP 端口
            version: 应用版本
            advertise_ip: 对外通告的网卡 IP（空 = 自动选择）
        """
        self.history_db = history_db
        self.device_name = device_name
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.version = version
        self.advertise_ip = advertise_ip or ""

        self.udp_broadcaster = None
        self.tcp_handler = None
        self.running = False

    def start(self):
        """启动服务器

        先启动 TCP 处理器（端口被占用时可能自动顺延），再按实际监听端口
        启动 UDP 广播器，确保广播消息中的 tcp_port 与真实端口一致。
        """
        if self.running:
            logger.warning("数据服务器已在运行")
            return

        try:
            # 先启动 TCP 处理器（内部会自动顺延被占用端口）
            self.tcp_handler = TCPHandler(
                port=self.tcp_port,
                request_handler=self._handle_request
            )
            self.tcp_handler.start()
            # 同步实际监听端口（可能已被自动顺延）
            self.tcp_port = self.tcp_handler.port

            # 再按实际端口启动 UDP 广播器
            self.udp_broadcaster = UDPBroadcaster(
                device_name=self.device_name,
                tcp_port=self.tcp_port,
                udp_port=self.udp_port,
                version=self.version,
                advertise_ip=self.advertise_ip
            )
            self.udp_broadcaster.start()

            self.running = True
            logger.info(f"数据服务器已启动 - 设备: {self.device_name}, TCP: {self.tcp_port}, UDP: {self.udp_port}")

        except Exception as e:
            logger.error(f"启动数据服务器失败: {e}")
            self.stop()
            raise

    def stop(self) -> None:
        """停止服务器"""
        self.running = False

        if self.udp_broadcaster:
            self.udp_broadcaster.stop()
            self.udp_broadcaster = None

        if self.tcp_handler:
            self.tcp_handler.stop()
            self.tcp_handler = None

        logger.info("数据服务器已停止")

    def is_running(self) -> bool:
        """
        检查服务器是否运行中

        返回:
            bool: 是否运行中
        """
        return self.running

    def _handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        处理客户端请求

        参数:
            request: 请求字典

        返回:
            Dict: 响应字典
        """
        try:
            request_type = request.get('type')

            if request_type == REQUEST_GET_SESSION_LIST:
                return self._handle_get_session_list(request)

            elif request_type == REQUEST_GET_CHANNEL_CONFIG:
                return self._handle_get_channel_config(request)

            elif request_type == REQUEST_GET_TEMPERATURE_DATA:
                return self._handle_get_temperature_data(request)

            elif request_type == REQUEST_GET_SESSION_STATS:
                return self._handle_get_session_stats(request)

            else:
                return create_response(RESPONSE_ERROR, message=f"未知请求类型: {request_type}")

        except Exception as e:
            logger.error(f"处理请求失败: {e}")
            return create_response(RESPONSE_ERROR, message=str(e))

    def _handle_get_session_list(self, request: Dict) -> Dict:
        """
        处理查询会话列表请求

        参数:
            request: 请求字典

        返回:
            Dict: 响应字典
        """
        limit = request.get('limit', 100)
        start_time = request.get('start_time')
        end_time = request.get('end_time')

        # 查询会话列表
        sessions = self.history_db.get_sessions(
            limit=limit,
            start_time=start_time,
            end_time=end_time
        )

        # 转换为字典列表
        sessions_data = []
        for session in sessions:
            session_dict = session.to_dict()

            # 计算持续时间（分钟）
            if session_dict.get('duration_seconds'):
                session_dict['duration_minutes'] = int(session_dict['duration_seconds'] / 60)
            else:
                session_dict['duration_minutes'] = None

            sessions_data.append(session_dict)

        return create_response(RESPONSE_OK, data={'sessions': sessions_data})

    def _handle_get_channel_config(self, request: Dict) -> Dict:
        """
        处理查询通道配置请求

        参数:
            request: 请求字典

        返回:
            Dict: 响应字典
        """
        session_id = request.get('session_id')

        if not session_id:
            return create_response(RESPONSE_ERROR, message="缺少 session_id 参数")

        # 查询通道配置
        channels = self.history_db.get_channel_config(session_id)

        # 转换为字典列表
        channels_data = [ch.to_dict() for ch in channels]

        return create_response(RESPONSE_OK, data={'channels': channels_data})

    def _handle_get_temperature_data(self, request: Dict) -> Dict:
        """
        处理查询温度数据请求

        参数:
            request: 请求字典

        返回:
            Dict: 响应字典
        """
        session_id = request.get('session_id')

        if not session_id:
            return create_response(RESPONSE_ERROR, message="缺少 session_id 参数")

        channels = request.get('channels')
        start_time = request.get('start_time')
        end_time = request.get('end_time')
        limit = request.get('limit', 10000)
        offset = request.get('offset', 0)

        # 查询温度数据
        result = self.history_db.get_temperature_data(
            session_id=session_id,
            channels=channels,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            offset=offset
        )

        # 转换数据格式
        if isinstance(result.get('data'), dict):
            # 按通道分组的数据
            data = {}
            for channel_key, readings in result['data'].items():
                data[channel_key] = [rd.to_dict() for rd in readings]

            return create_response(RESPONSE_OK, data={
                'data': data,
                'total': result['total'],
                'format': 'by_channel'
            })
        else:
            # 列表数据
            readings_data = [rd.to_dict() for rd in result['data']]

            return create_response(RESPONSE_OK, data={
                'data': readings_data,
                'total': result['total'],
                'format': 'list'
            })

    def _handle_get_session_stats(self, request: Dict) -> Dict:
        """
        处理查询会话统计信息请求

        参数:
            request: 请求字典

        返回:
            Dict: 响应字典
        """
        session_id = request.get('session_id')

        if not session_id:
            return create_response(RESPONSE_ERROR, message="缺少 session_id 参数")

        # 查询统计信息
        stats_dict = self.history_db.get_session_stats(session_id)

        # 转换为字典
        stats_data = {}
        for channel_key, stats in stats_dict.items():
            stats_data[channel_key] = stats.to_dict()

        return create_response(RESPONSE_OK, data={'stats': stats_data})