"""
通道数据访问对象（Channel DAO）

提供通道配置表的 CRUD 操作。
"""

import sqlite3
from typing import List, Optional
from ..models import Channel


class ChannelDAO:
    """通道数据访问对象"""

    def __init__(self, conn: sqlite3.Connection):
        """
        初始化通道 DAO

        参数:
            conn: SQLite 数据库连接
        """
        self.conn = conn

    def create(self, channel: Channel) -> bool:
        """
        创建通道配置

        参数:
            channel: 通道数据模型

        返回:
            bool: 是否成功
        """
        try:
            data = channel.to_dict()
            self.conn.execute('''
                INSERT INTO channels (
                    session_id, channel_key, channel_name, color,
                    physical_name, unit, visible
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                data['session_id'], data['channel_key'], data['channel_name'],
                data['color'], data['physical_name'], data['unit'], data['visible']
            ))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"创建通道配置失败: {e}")
            return False

    def create_batch(self, channels: List[Channel]) -> bool:
        """
        批量创建通道配置（使用事务）

        参数:
            channels: 通道数据模型列表

        返回:
            bool: 是否成功
        """
        try:
            self.conn.execute('BEGIN TRANSACTION')

            for channel in channels:
                data = channel.to_dict()
                self.conn.execute('''
                    INSERT INTO channels (
                        session_id, channel_key, channel_name, color,
                        physical_name, unit, visible
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data['session_id'], data['channel_key'], data['channel_name'],
                    data['color'], data['physical_name'], data['unit'], data['visible']
                ))

            self.conn.commit()
            return True
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"批量创建通道配置失败: {e}")
            return False

    def get_by_session(self, session_id: str) -> List[Channel]:
        """
        查询会话的所有通道配置

        参数:
            session_id: 会话 ID

        返回:
            List[Channel]: 通道配置列表
        """
        cursor = self.conn.execute('''
            SELECT * FROM channels WHERE session_id = ?
            ORDER BY CAST(substr(channel_key, 3) AS INTEGER), channel_key
        ''', (session_id,))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        channels = []
        for row in rows:
            data = dict(zip(columns, row))
            channels.append(Channel.from_dict(data))

        return channels

    def get_by_key(self, session_id: str, channel_key: str) -> Optional[Channel]:
        """
        查询特定通道配置

        参数:
            session_id: 会话 ID
            channel_key: 通道键（如 CH1）

        返回:
            Channel: 通道配置，如果不存在返回 None
        """
        cursor = self.conn.execute('''
            SELECT * FROM channels WHERE session_id = ? AND channel_key = ?
        ''', (session_id, channel_key))
        row = cursor.fetchone()
        if row:
            columns = [desc[0] for desc in cursor.description]
            data = dict(zip(columns, row))
            return Channel.from_dict(data)
        return None

    def update_visibility(self, session_id: str, channel_key: str,
                          visible: bool) -> bool:
        """
        更新通道可见性

        参数:
            session_id: 会话 ID
            channel_key: 通道键
            visible: 是否可见

        返回:
            bool: 是否成功
        """
        try:
            cursor = self.conn.execute('''
                UPDATE channels
                SET visible = ?
                WHERE session_id = ? AND channel_key = ?
            ''', (1 if visible else 0, session_id, channel_key))
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"更新通道可见性失败: {e}")
            return False

    def update_name(self, session_id: str, channel_key: str,
                    channel_name: str) -> bool:
        """
        更新通道名称

        参数:
            session_id: 会话 ID
            channel_key: 通道键
            channel_name: 通道名称

        返回:
            bool: 是否成功
        """
        try:
            cursor = self.conn.execute('''
                UPDATE channels
                SET channel_name = ?
                WHERE session_id = ? AND channel_key = ?
            ''', (channel_name, session_id, channel_key))
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"更新通道名称失败: {e}")
            return False

    def update_color(self, session_id: str, channel_key: str,
                     color: str) -> bool:
        """
        更新通道颜色

        参数:
            session_id: 会话 ID
            channel_key: 通道键
            color: 颜色（#RRGGBB）

        返回:
            bool: 是否成功
        """
        try:
            cursor = self.conn.execute('''
                UPDATE channels
                SET color = ?
                WHERE session_id = ? AND channel_key = ?
            ''', (color, session_id, channel_key))
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"更新通道颜色失败: {e}")
            return False

    def delete_by_session(self, session_id: str) -> bool:
        """
        删除会话的所有通道配置

        参数:
            session_id: 会话 ID

        返回:
            bool: 是否成功
        """
        try:
            cursor = self.conn.execute('''
                DELETE FROM channels WHERE session_id = ?
            ''', (session_id,))
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"删除通道配置失败: {e}")
            return False

    def count_by_session(self, session_id: str) -> int:
        """
        查询会话的通道数量

        参数:
            session_id: 会话 ID

        返回:
            int: 通道数量
        """
        cursor = self.conn.execute('''
            SELECT COUNT(*) FROM channels WHERE session_id = ?
        ''', (session_id,))
        return cursor.fetchone()[0]