"""
会话数据访问对象（Session DAO）

提供会话表的 CRUD 操作。
"""

import sqlite3
from typing import Optional, List
from ..models import Session
import time


class SessionDAO:
    """会话数据访问对象"""

    def __init__(self, conn: sqlite3.Connection):
        """
        初始化会话 DAO

        参数:
            conn: SQLite 数据库连接
        """
        self.conn = conn

    def create(self, session: Session) -> bool:
        """
        创建新的测量会话记录

        参数:
            session: 会话数据模型

        返回:
            bool: 是否成功
        """
        try:
            data = session.to_dict()
            self.conn.execute('''
                INSERT INTO sessions (
                    session_id, session_name, started_at, stopped_at,
                    duration_seconds, channel_count, interval_seconds,
                    source_type, source_path, notes, tags,
                    created_at, updated_at, locked
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data['session_id'], data['session_name'], data['started_at'],
                data['stopped_at'], data['duration_seconds'], data['channel_count'],
                data['interval_seconds'], data['source_type'], data['source_path'],
                data['notes'], data['tags'], data['created_at'], data['updated_at'],
                data['locked']
            ))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"创建会话失败: {e}")
            return False

    def get_by_id(self, session_id: str) -> Optional[Session]:
        """
        根据 ID 查询会话

        参数:
            session_id: 会话 ID

        返回:
            Session: 会话数据模型，如果不存在返回 None
        """
        cursor = self.conn.execute('''
            SELECT * FROM sessions WHERE session_id = ?
        ''', (session_id,))
        row = cursor.fetchone()
        if row:
            columns = [desc[0] for desc in cursor.description]
            data = dict(zip(columns, row))
            return Session.from_dict(data)
        return None

    def get_list(self, limit: int = 100, start_time: Optional[float] = None,
                 end_time: Optional[float] = None) -> List[Session]:
        """
        查询历史会话列表

        参数:
            limit: 返回数量限制
            start_time: 开始时间过滤
            end_time: 结束时间过滤

        返回:
            List[Session]: 会话列表
        """
        query = 'SELECT * FROM sessions WHERE 1=1'
        params = []

        if start_time is not None:
            query += ' AND started_at >= ?'
            params.append(start_time)
        if end_time is not None:
            query += ' AND started_at <= ?'
            params.append(end_time)

        query += ' ORDER BY started_at DESC LIMIT ?'
        params.append(limit)

        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        sessions = []
        for row in rows:
            data = dict(zip(columns, row))
            sessions.append(Session.from_dict(data))

        return sessions

    def update_stop_time(self, session_id: str, stopped_at: float) -> bool:
        """
        更新会话停止时间

        参数:
            session_id: 会话 ID
            stopped_at: 停止时间戳

        返回:
            bool: 是否成功
        """
        try:
            # 同时计算持续时间
            cursor = self.conn.execute('''
                UPDATE sessions
                SET stopped_at = ?,
                    duration_seconds = ? - started_at,
                    updated_at = ?
                WHERE session_id = ?
            ''', (stopped_at, stopped_at, time.time(), session_id))
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"更新会话停止时间失败: {e}")
            return False

    def update_name(self, session_id: str, session_name: str) -> bool:
        """
        更新会话名称（自定义名称，时间戳不可修改）

        参数:
            session_id: 会话 ID
            session_name: 新的会话名称

        返回:
            bool: 是否成功
        """
        try:
            cursor = self.conn.execute('''
                UPDATE sessions
                SET session_name = ?, updated_at = ?
                WHERE session_id = ?
            ''', (session_name, time.time(), session_id))
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"更新会话名称失败: {e}")
            return False

    def update_notes(self, session_id: str, notes: str) -> bool:
        """
        更新会话备注

        参数:
            session_id: 会话 ID
            notes: 备注内容

        返回:
            bool: 是否成功
        """
        try:
            cursor = self.conn.execute('''
                UPDATE sessions
                SET notes = ?, updated_at = ?
                WHERE session_id = ?
            ''', (notes, time.time(), session_id))
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"更新会话备注失败: {e}")
            return False

    def set_locked(self, session_id: str, locked: bool) -> bool:
        """
        设置会话锁定状态（锁定后进入白名单，30 天保留清理跳过）

        参数:
            session_id: 会话 ID
            locked: True 锁定 / False 解锁

        返回:
            bool: 是否成功
        """
        try:
            cursor = self.conn.execute('''
                UPDATE sessions
                SET locked = ?, updated_at = ?
                WHERE session_id = ?
            ''', (1 if locked else 0, time.time(), session_id))
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"更新会话锁定状态失败: {e}")
            return False

    def delete(self, session_id: str) -> bool:
        """
        删除会话（级联删除相关数据）

        参数:
            session_id: 会话 ID

        返回:
            bool: 是否成功
        """
        try:
            cursor = self.conn.execute('''
                DELETE FROM sessions WHERE session_id = ?
            ''', (session_id,))
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"删除会话失败: {e}")
            return False

    def count(self) -> int:
        """
        查询会话总数

        返回:
            int: 会话总数
        """
        cursor = self.conn.execute('SELECT COUNT(*) FROM sessions')
        return cursor.fetchone()[0]