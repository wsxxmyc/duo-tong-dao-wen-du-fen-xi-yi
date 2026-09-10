"""
配对码服务器

通过 UDP 广播配对码，方便客户端自动发现设备。
"""

from __future__ import annotations

import secrets
import threading
import time
import json
import socket
import logging
from typing import Optional, Dict, Any

from .protocol import (
    APP_IDENTIFIER,
    DEFAULT_UDP_PORT,
    DEFAULT_TCP_PORT,
    DEFAULT_BROADCAST_INTERVAL,
)
from .netutil import get_local_ips, subnet_broadcast_addresses, LIMITED_BROADCAST

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PairingCodeServer:
    """
    配对码服务器

    生成配对码并通过 UDP 广播，方便客户端自动发现设备。
    支持配对码有效期管理和自动刷新。
    """

    def __init__(
        self,
        history_db: Any,
        device_name: str = "多通道温度分析仪",
        tcp_port: int = DEFAULT_TCP_PORT,
        http_port: int = 8080,
        udp_port: int = DEFAULT_UDP_PORT,
        broadcast_interval: float = DEFAULT_BROADCAST_INTERVAL,
        pairing_code_ttl: int = 1800,
        advertise_ip: str = "",
    ):
        """
        初始化配对码服务器

        参数:
            history_db: 数据库管理器（HistoryDatabase 实例）
            device_name: 设备名称
            tcp_port: TCP 服务端口
            http_port: HTTP 服务端口
            udp_port: UDP 广播端口
            broadcast_interval: 广播间隔（秒）
            pairing_code_ttl: 配对码有效期（秒），默认 30 分钟
            advertise_ip: 对外通告的网卡 IP（空 = 自动选择）
        """
        self.history_db = history_db
        self.device_name = device_name
        self.tcp_port = tcp_port
        self.http_port = http_port
        self.udp_port = udp_port
        self.broadcast_interval = broadcast_interval
        self.pairing_code_ttl = pairing_code_ttl
        self.advertise_ip = advertise_ip or ""

        # 配对码相关
        self.pairing_code: Optional[str] = None
        self.pairing_code_timestamp: float = 0

        # 线程管理
        self.running: bool = False
        self.thread: Optional[threading.Thread] = None
        self.udp_socket: Optional[socket.socket] = None

        # 广播健康状态（供 UI 显示：最近发送时间 / 最近失败原因）
        self.last_broadcast_at: Optional[float] = None
        self.broadcast_error: Optional[str] = None

        # 线程锁（用于配对码更新）
        self._lock = threading.Lock()

    def start(self) -> bool:
        """
        启动配对码服务器

        返回:
            bool: 是否成功启动
        """
        if self.running:
            logger.warning("配对码服务器已在运行")
            return True

        try:
            # 生成初始配对码
            self._generate_pairing_code()

            # 启动广播线程
            self.running = True
            self.thread = threading.Thread(
                target=self._broadcast_loop,
                daemon=True,
                name="PairingCodeServer"
            )
            self.thread.start()

            logger.info(
                f"配对码服务器已启动 - 设备: {self.device_name}, "
                f"配对码: {self.pairing_code}, "
                f"UDP端口: {self.udp_port}"
            )

            return True

        except Exception as e:
            logger.error(f"启动配对码服务器失败: {e}")
            self.running = False
            return False

    def stop(self) -> None:
        """停止配对码服务器"""
        if not self.running:
            return

        logger.info("正在停止配对码服务器...")

        # 标记停止
        self.running = False

        # 关闭 socket
        if self.udp_socket:
            try:
                self.udp_socket.close()
            except Exception as e:
                logger.warning(f"关闭 UDP socket 时出错: {e}")
            finally:
                self.udp_socket = None

        # 等待线程结束
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

        logger.info("配对码服务器已停止")

    def get_pairing_code(self) -> Optional[str]:
        """
        获取当前配对码

        返回:
            Optional[str]: 配对码（4 位数字），如果服务器未启动则返回 None
        """
        if not self.running:
            return None

        with self._lock:
            # 检查配对码是否过期
            if self._is_pairing_code_expired():
                self._generate_pairing_code()

            return self.pairing_code

    def refresh_pairing_code(self) -> str:
        """
        刷新配对码（立即生成新配对码）

        返回:
            str: 新的配对码
        """
        with self._lock:
            self._generate_pairing_code()
            logger.info(f"配对码已刷新: {self.pairing_code}")
            return self.pairing_code

    def is_running(self) -> bool:
        """
        检查服务器是否运行中

        返回:
            bool: 是否运行中
        """
        return self.running

    def _generate_pairing_code(self) -> None:
        """
        生成配对码（内部方法，不加锁）

        注意: 调用此方法前必须已获取 _lock
        """
        self.pairing_code = str(secrets.choice(range(1000, 10000)))
        self.pairing_code_timestamp = time.time()

    def _is_pairing_code_expired(self) -> bool:
        """
        检查配对码是否过期

        返回:
            bool: 是否过期
        """
        if not self.pairing_code or self.pairing_code_timestamp == 0:
            return True

        elapsed = time.time() - self.pairing_code_timestamp
        return elapsed > self.pairing_code_ttl

    def _broadcast_loop(self) -> None:
        """广播循环（在线程中运行）

        每个周期向受限广播地址（255.255.255.255）和各网卡 /24 子网广播地址
        分别发送广播；每个本机 IP 通告一条对应消息，供客户端选择可达地址。
        """
        try:
            # 创建 UDP socket
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # 设置超时，避免在停止时阻塞
            self.udp_socket.settimeout(1.0)

            logger.info(f"UDP 广播线程已启动，端口: {self.udp_port}")

            while self.running:
                try:
                    # 获取配对码（会自动检查过期）
                    code = self.get_pairing_code()
                    if not code:
                        logger.warning("无法获取配对码，跳过本次广播")
                        time.sleep(self.broadcast_interval)
                        continue

                    # 枚举本机可通告 IP（配置了对外网卡则只通告该 IP；
                    # 否则默认路由出口 IP 优先的全部地址）
                    local_ips = self._resolve_advertise_ips(
                        self.advertise_ip, get_local_ips())
                    subnet_targets = subnet_broadcast_addresses(local_ips)

                    for local_ip in local_ips:
                        # 创建广播消息，通告当前 IP
                        message = self._create_broadcast_message(code, local_ip)
                        data = json.dumps(message, ensure_ascii=False).encode('utf-8')

                        # 目标：受限广播 + 本 IP 对应子网广播
                        targets = [LIMITED_BROADCAST]
                        targets.extend(subnet_targets)

                        for target in targets:
                            self.udp_socket.sendto(data, (target, self.udp_port))
                            # 每次发成功立即记录，避免停止竞态下整轮被跳过
                            self.last_broadcast_at = time.time()
                            self.broadcast_error = None
                            logger.debug(
                                f"广播消息已发送 → {target}:{self.udp_port} "
                                f"ip={local_ip}")

                except socket.timeout:
                    # socket 超时是正常的（用于检查 running 标志）
                    continue

                except Exception as e:
                    # 正常停止（stop 关闭 socket 引发）不记录为广播失败
                    if self.running:
                        logger.error(f"发送广播失败: {e}")
                        self.broadcast_error = str(e)

                # 等待下一次广播
                time.sleep(self.broadcast_interval)

        except Exception as e:
            logger.error(f"广播循环异常退出: {e}")

        finally:
            # 确保 socket 被关闭
            if self.udp_socket:
                try:
                    self.udp_socket.close()
                except Exception:
                    pass
                finally:
                    self.udp_socket = None

            logger.info("UDP 广播线程已退出")

    def _resolve_advertise_ips(self, advertise_ip: str, local_ips) -> list:
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

    def _create_broadcast_message(
        self,
        pairing_code: str,
        ip_address: str
    ) -> Dict[str, Any]:
        """
        创建广播消息

        参数:
            pairing_code: 配对码
            ip_address: IP 地址

        返回:
            Dict: 广播消息字典
        """
        return {
            "app": APP_IDENTIFIER,
            "version": "1.3.0",  # 配对码功能版本
            "device_name": self.device_name,
            "pairing_code": pairing_code,
            "tcp_port": self.tcp_port,
            "http_port": self.http_port,
            "ip_address": ip_address,
            "timestamp": int(time.time())
        }


# ─────────────────────────────────────────────────────
# 测试代码
# ─────────────────────────────────────────────────────

if __name__ == "__main__":
    """简单测试脚本"""
    import sys

    print("=" * 60)
    print("配对码服务器测试")
    print("=" * 60)

    # 创建模拟的 history_db（仅用于测试）
    class MockHistoryDB:
        pass

    mock_db = MockHistoryDB()

    # 创建配对码服务器
    server = PairingCodeServer(
        history_db=mock_db,
        device_name="MTA-TEST",
        tcp_port=9527,
        http_port=8080,
        udp_port=9526,
        broadcast_interval=5.0,
        pairing_code_ttl=1800  # 30 分钟
    )

    try:
        # 启动服务器
        print("\n正在启动配对码服务器...")
        server.start()

        print(f"配对码: {server.get_pairing_code()}")
        print("按 Ctrl+C 停止服务器...\n")

        # 持续运行
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n收到停止信号...")

    finally:
        # 停止服务器
        server.stop()
        print("测试结束")
        sys.exit(0)