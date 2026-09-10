"""
HTTP REST API 服务器

提供标准的 REST 接口供办公室电脑访问数据。
使用 Python 标准库实现，兼容 Python 3.8.10。
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Dict, Any, List
from urllib.parse import urlparse, parse_qs

if TYPE_CHECKING:
    from device.database.history_db import HistoryDatabase


class HTTPDataHandler(BaseHTTPRequestHandler):
    """
    HTTP 数据请求处理器

    处理所有 HTTP GET 请求，返回 JSON 格式数据。
    history_db 作为类变量，确保多线程安全。
    """

    history_db = None  # 类变量，线程安全

    def log_message(self, format: str, *args) -> None:
        """
        重写日志方法，输出到标准输出

        参数:
            format: 日志格式字符串
            *args: 格式化参数
        """
        print(f"[HTTP] {self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        """
        处理 GET 请求

        根据路径路由到不同的处理方法。
        """
        try:
            # 解析 URL
            parsed_path = urlparse(self.path)
            path = parsed_path.path
            query_params = parse_qs(parsed_path.query)

            # 路由到不同的处理方法
            if path == '/api/sessions':
                self._handle_sessions(query_params)
            elif path.startswith('/api/session/'):
                session_id = path.split('/')[-1]
                self._handle_session_detail(session_id)
            elif path.startswith('/api/data/'):
                session_id = path.split('/')[-1]
                self._handle_temperature_data(session_id, query_params)
            elif path.startswith('/api/channels/'):
                session_id = path.split('/')[-1]
                self._handle_channel_config(session_id)
            elif path == '/download/database':
                self._handle_download_database()
            elif path == '/health':
                self._handle_health_check()
            else:
                self._send_error(404, 'Not Found')

        except Exception as e:
            print(f"[HTTP] 请求处理异常: {e}")
            self._send_error(500, f'Internal Server Error: {str(e)}')

    def _handle_sessions(self, query_params: Dict[str, List[str]]) -> None:
        """
        处理 /api/sessions 请求

        获取会话列表。

        参数:
            query_params: 查询参数字典
        """
        try:
            # 解析查询参数
            limit = int(query_params.get('limit', ['100'])[0])
            start_time = float(query_params.get('start_time', [None])[0]) if 'start_time' in query_params else None
            end_time = float(query_params.get('end_time', [None])[0]) if 'end_time' in query_params else None

            # 查询会话列表
            sessions = self.history_db.get_sessions(limit, start_time, end_time)

            # 转换为字典列表
            sessions_data = [session.to_dict() for session in sessions]

            self._send_json({
                'success': True,
                'data': sessions_data,
                'count': len(sessions_data)
            })

        except ValueError as e:
            self._send_error(400, f'Invalid parameter: {str(e)}')
        except Exception as e:
            print(f"[HTTP] 获取会话列表失败: {e}")
            self._send_error(500, f'Failed to get sessions: {str(e)}')

    def _handle_session_detail(self, session_id: str) -> None:
        """
        处理 /api/session/:id 请求

        获取会话详情。

        参数:
            session_id: 会话 ID
        """
        try:
            if not session_id:
                self._send_error(400, 'Session ID is required')
                return

            # 查询会话详情
            session = self.history_db.get_session_by_id(session_id)

            if session is None:
                self._send_error(404, f'Session not found: {session_id}')
                return

            # 获取统计信息
            stats = self.history_db.get_session_stats(session_id)

            # 转换统计信息为字典
            stats_data = {
                ch_key: stat.to_dict()
                for ch_key, stat in stats.items()
            }

            self._send_json({
                'success': True,
                'data': {
                    'session': session.to_dict(),
                    'stats': stats_data
                }
            })

        except Exception as e:
            print(f"[HTTP] 获取会话详情失败: {e}")
            self._send_error(500, f'Failed to get session detail: {str(e)}')

    def _handle_temperature_data(self, session_id: str, query_params: Dict[str, List[str]]) -> None:
        """
        处理 /api/data/:id 请求

        获取温度数据。

        参数:
            session_id: 会话 ID
            query_params: 查询参数字典
        """
        try:
            if not session_id:
                self._send_error(400, 'Session ID is required')
                return

            # 解析查询参数
            channels = query_params.get('channels')  # 列表形式
            start_time = float(query_params.get('start_time', [None])[0]) if 'start_time' in query_params else None
            end_time = float(query_params.get('end_time', [None])[0]) if 'end_time' in query_params else None
            limit = int(query_params.get('limit', ['10000'])[0])
            offset = int(query_params.get('offset', ['0'])[0])

            # 查询温度数据
            result = self.history_db.get_temperature_data(
                session_id, channels, start_time, end_time, limit, offset
            )

            # 转换数据
            if isinstance(result['data'], dict):
                # 多通道数据，每个通道是 TemperatureReading 列表
                data_dict = {}
                for ch_key, readings in result['data'].items():
                    data_dict[ch_key] = [r.to_dict() for r in readings]

                self._send_json({
                    'success': True,
                    'data': data_dict,
                    'total': result['total']
                })
            else:
                # 单列表数据
                readings_data = [r.to_dict() for r in result['data']]

                self._send_json({
                    'success': True,
                    'data': readings_data,
                    'total': result['total']
                })

        except ValueError as e:
            self._send_error(400, f'Invalid parameter: {str(e)}')
        except Exception as e:
            print(f"[HTTP] 获取温度数据失败: {e}")
            self._send_error(500, f'Failed to get temperature data: {str(e)}')

    def _handle_channel_config(self, session_id: str) -> None:
        """
        处理 /api/channels/:id 请求

        获取通道配置。

        参数:
            session_id: 会话 ID
        """
        try:
            if not session_id:
                self._send_error(400, 'Session ID is required')
                return

            # 查询通道配置
            channels = self.history_db.get_channel_config(session_id)

            # 转换为字典列表
            channels_data = [channel.to_dict() for channel in channels]

            self._send_json({
                'success': True,
                'data': channels_data,
                'count': len(channels_data)
            })

        except Exception as e:
            print(f"[HTTP] 获取通道配置失败: {e}")
            self._send_error(500, f'Failed to get channel config: {str(e)}')

    def _handle_download_database(self) -> None:
        """
        处理 /download/database 请求

        下载数据库文件。
        """
        try:
            if self.history_db is None:
                self._send_error(500, 'Database not initialized')
                return

            db_path = self.history_db.db_path

            if not os.path.exists(db_path):
                self._send_error(404, 'Database file not found')
                return

            # 读取文件
            with open(db_path, 'rb') as f:
                file_data = f.read()

            # 发送响应
            self.send_response(200)
            self.send_header('Content-type', 'application/octet-stream')
            self.send_header('Content-Disposition', f'attachment; filename="{os.path.basename(db_path)}"')
            self.send_header('Content-Length', str(len(file_data)))
            self.end_headers()
            self.wfile.write(file_data)

            print(f"[HTTP] 数据库文件下载成功: {db_path} ({len(file_data)} bytes)")

        except Exception as e:
            print(f"[HTTP] 下载数据库失败: {e}")
            self._send_error(500, f'Failed to download database: {str(e)}')

    def _handle_health_check(self) -> None:
        """
        处理 /health 请求

        健康检查。
        """
        try:
            # 检查数据库连接
            db_status = 'ok' if self.history_db is not None else 'not_initialized'
            db_path = self.history_db.db_path if self.history_db else None
            db_size = self.history_db.get_db_size() if self.history_db else 0

            self._send_json({
                'success': True,
                'status': 'healthy',
                'database': {
                    'status': db_status,
                    'path': db_path,
                    'size_bytes': db_size
                }
            })

        except Exception as e:
            print(f"[HTTP] 健康检查失败: {e}")
            self._send_error(500, f'Health check failed: {str(e)}')

    def _send_json(self, data: Dict[str, Any]) -> None:
        """
        发送 JSON 响应

        参数:
            data: 要发送的数据字典
        """
        try:
            json_data = json.dumps(data, ensure_ascii=False, indent=2)
            json_bytes = json_data.encode('utf-8')

            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(json_bytes)))
            self.send_header('Access-Control-Allow-Origin', '*')  # 允许跨域
            self.end_headers()
            self.wfile.write(json_bytes)

        except Exception as e:
            print(f"[HTTP] 发送 JSON 失败: {e}")

    def _send_error(self, code: int, message: str) -> None:
        """
        发送错误响应

        参数:
            code: HTTP 状态码
            message: 错误消息
        """
        try:
            error_data = {
                'success': False,
                'error': message,
                'code': code
            }
            json_data = json.dumps(error_data, ensure_ascii=False)
            json_bytes = json_data.encode('utf-8')

            self.send_response(code)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(json_bytes)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json_bytes)

        except Exception as e:
            print(f"[HTTP] 发送错误响应失败: {e}")


class HTTPDataServer:
    """
    HTTP 数据服务器

    管理 HTTP 服务器生命周期，以线程方式运行。
    """

    def __init__(self, history_db, port: int = 8080):
        """
        初始化 HTTP 服务器

        参数:
            history_db: HistoryDatabase 实例
            port: 监听端口，默认 8080
        """
        HTTPDataHandler.history_db = history_db
        self.port = port
        self.server = None
        self.thread = None
        self.running = False

    def start(self) -> bool:
        """
        启动 HTTP 服务器

        返回:
            bool: 是否成功启动
        """
        try:
            if self.running:
                print("[HTTP] 服务器已在运行")
                return True

            # 创建 HTTP 服务器
            self.server = HTTPServer(('0.0.0.0', self.port), HTTPDataHandler)

            # 创建线程
            self.thread = threading.Thread(
                target=self._run_server,
                daemon=True,
                name='HTTPDataServer'
            )

            # 先尝试启动线程
            self.thread.start()

            # 等待一小段时间确认启动成功
            time.sleep(0.1)
            if not self.thread.is_alive():
                raise RuntimeError("线程启动失败")

            # 只有确认启动成功后才设置标志
            self.running = True

            print(f"[HTTP] 服务器启动成功，监听端口 {self.port}")
            print(f"[HTTP] 访问地址: http://localhost:{self.port}")
            print(f"[HTTP] API 接口:")
            print(f"  - GET /api/sessions          获取会话列表")
            print(f"  - GET /api/session/:id       获取会话详情")
            print(f"  - GET /api/data/:id          获取温度数据")
            print(f"  - GET /api/channels/:id      获取通道配置")
            print(f"  - GET /download/database     下载数据库文件")
            print(f"  - GET /health                健康检查")

            return True

        except OSError as e:
            print(f"[HTTP] 启动失败，端口 {self.port} 可能已被占用: {e}")
            self.running = False
            return False
        except Exception as e:
            print(f"[HTTP] 启动失败: {e}")
            self.running = False
            return False

    def _run_server(self) -> None:
        """
        运行服务器（线程函数）
        """
        try:
            self.server.serve_forever()
        except Exception as e:
            print(f"[HTTP] 服务器运行异常: {e}")
            self.running = False

    def stop(self) -> bool:
        """
        停止 HTTP 服务器

        返回:
            bool: 是否成功停止
        """
        try:
            if not self.running:
                print("[HTTP] 服务器未在运行")
                return True

            # 关闭服务器
            if self.server:
                self.server.shutdown()
                self.server.server_close()

            # 等待线程结束
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=5.0)

            self.running = False
            print("[HTTP] 服务器已停止")
            return True

        except Exception as e:
            print(f"[HTTP] 停止服务器失败: {e}")
            return False

    def is_running(self) -> bool:
        """
        检查服务器是否在运行

        返回:
            bool: 是否在运行
        """
        return self.running and self.thread is not None and self.thread.is_alive()

    def get_port(self) -> int:
        """
        获取服务器监听端口

        返回:
            int: 端口号
        """
        return self.port


def create_http_server(history_db: "HistoryDatabase",
                       port: int = 8080) -> HTTPDataServer:
    """
    创建 HTTP 数据服务器实例

    参数:
        history_db: HistoryDatabase 实例
        port: 监听端口，默认 8080

    返回:
        HTTPDataServer: 服务器实例
    """
    return HTTPDataServer(history_db, port)