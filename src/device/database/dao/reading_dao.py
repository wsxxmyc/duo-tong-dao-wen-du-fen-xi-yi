"""
温度数据访问对象（Reading DAO）

提供温度数据表的 CRUD 操作，特别优化批量插入性能。
"""

import sqlite3
from typing import List, Optional, Dict
from ..models import TemperatureReading


class ReadingDAO:
    """温度数据访问对象"""

    def __init__(self, conn: sqlite3.Connection):
        """
        初始化温度数据 DAO

        参数:
            conn: SQLite 数据库连接
        """
        self.conn = conn

    def create(self, reading: TemperatureReading) -> bool:
        """
        创建单条温度数据

        参数:
            reading: 温度数据模型

        返回:
            bool: 是否成功
        """
        try:
            data = reading.to_dict()
            self.conn.execute('''
                INSERT INTO temperature_readings (
                    session_id, channel_key, timestamp, temperature, is_valid
                ) VALUES (?, ?, ?, ?, ?)
            ''', (
                data['session_id'], data['channel_key'], data['timestamp'],
                data['temperature'], data['is_valid']
            ))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            print(f"创建温度数据失败: {e}")
            return False

    def create_batch(self, readings: List[TemperatureReading]) -> bool:
        """
        批量插入温度数据（使用事务，性能优化）

        参数:
            readings: 温度数据模型列表

        返回:
            bool: 是否成功
        """
        if not readings:
            return True

        try:
            self.conn.execute('BEGIN TRANSACTION')

            # 使用 executemany 批量插入，性能更高
            data_list = []
            for reading in readings:
                data = reading.to_dict()
                data_list.append((
                    data['session_id'], data['channel_key'], data['timestamp'],
                    data['temperature'], data['is_valid']
                ))

            self.conn.executemany('''
                INSERT INTO temperature_readings (
                    session_id, channel_key, timestamp, temperature, is_valid
                ) VALUES (?, ?, ?, ?, ?)
            ''', data_list)

            self.conn.commit()
            return True
        except sqlite3.Error as e:
            self.conn.rollback()
            print(f"批量插入温度数据失败: {e}")
            return False

    def get_by_session(self, session_id: str, limit: int = 10000,
                       offset: int = 0) -> List[TemperatureReading]:
        """
        查询会话的所有温度数据（分页）

        参数:
            session_id: 会话 ID
            limit: 返回数量限制
            offset: 偏移量

        返回:
            List[TemperatureReading]: 温度数据列表
        """
        cursor = self.conn.execute('''
            SELECT * FROM temperature_readings
            WHERE session_id = ?
            ORDER BY timestamp ASC
            LIMIT ? OFFSET ?
        ''', (session_id, limit, offset))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        readings = []
        for row in rows:
            data = dict(zip(columns, row))
            readings.append(TemperatureReading.from_dict(data))

        return readings

    def get_by_channel(self, session_id: str, channel_key: str,
                       limit: int = 10000) -> List[TemperatureReading]:
        """
        查询特定通道的温度数据

        参数:
            session_id: 会话 ID
            channel_key: 通道键（如 CH1）
            limit: 返回数量限制

        返回:
            List[TemperatureReading]: 温度数据列表
        """
        cursor = self.conn.execute('''
            SELECT * FROM temperature_readings
            WHERE session_id = ? AND channel_key = ?
            ORDER BY timestamp ASC
            LIMIT ?
        ''', (session_id, channel_key, limit))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        readings = []
        for row in rows:
            data = dict(zip(columns, row))
            readings.append(TemperatureReading.from_dict(data))

        return readings

    def get_by_time_range(self, session_id: str, start_time: float,
                          end_time: float, limit: int = 10000) -> List[TemperatureReading]:
        """
        查询时间范围内的温度数据

        参数:
            session_id: 会话 ID
            start_time: 开始时间戳
            end_time: 结束时间戳
            limit: 返回数量限制

        返回:
            List[TemperatureReading]: 温度数据列表
        """
        cursor = self.conn.execute('''
            SELECT * FROM temperature_readings
            WHERE session_id = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            LIMIT ?
        ''', (session_id, start_time, end_time, limit))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]

        readings = []
        for row in rows:
            data = dict(zip(columns, row))
            readings.append(TemperatureReading.from_dict(data))

        return readings

    def get_channels_data(self, session_id: str, channel_keys: List[str],
                          start_time: Optional[float] = None,
                          end_time: Optional[float] = None,
                          limit: int = 10000) -> Dict[str, List[TemperatureReading]]:
        """查询多个通道的温度数据（按通道分组）。

        固定 SQL 模板 + 全参数绑定：可选条件用 ``(? IS NULL OR col >= ?)``
        模式表达，无任何 SQL 拼接；``limit<=0`` 传 -1（sqlite 表示不限制）。
        """
        if not channel_keys:
            return {}
        lim = -1 if (not limit or limit <= 0) else int(limit)
        channels_data: Dict[str, List[TemperatureReading]] = {}
        for ck in channel_keys:
            cursor = self.conn.execute(
                "SELECT * FROM temperature_readings "
                "WHERE session_id = ? AND channel_key = ? "
                "AND (? IS NULL OR timestamp >= ?) "
                "AND (? IS NULL OR timestamp <= ?) "
                "ORDER BY timestamp ASC LIMIT ?",
                (session_id, ck, start_time, start_time,
                 end_time, end_time, lim))
            columns = [desc[0] for desc in cursor.description]
            channels_data[ck] = [
                TemperatureReading.from_dict(dict(zip(columns, row)))
                for row in cursor.fetchall()
            ]
        return channels_data

    def get_channels_arrays(self, session_id: str, channel_keys: List[str],
                            start_time: Optional[float] = None,
                            end_time: Optional[float] = None,
                            limit: int = 0) -> Dict[str, tuple]:
        """直接返回每通道的 ``(timestamp, temperature)`` numpy 数组。

        跳过 ORM 物化，供历史会话大数据加载（store.load_session_from_db）。
        ``limit<=0`` 不限制；温度 None 转 NaN。固定 SQL + 全参数绑定。
        """
        import numpy as np
        if not channel_keys:
            return {}
        lim = -1 if (not limit or limit <= 0) else int(limit)
        result: Dict[str, tuple] = {}
        for ck in channel_keys:
            cursor = self.conn.execute(
                "SELECT timestamp, temperature FROM temperature_readings "
                "WHERE session_id = ? AND channel_key = ? "
                "AND (? IS NULL OR timestamp >= ?) "
                "AND (? IS NULL OR timestamp <= ?) "
                "ORDER BY timestamp ASC LIMIT ?",
                (session_id, ck, start_time, start_time,
                 end_time, end_time, lim))
            rows = cursor.fetchall()
            if rows:
                # C6：单次 numpy C 级转换（None→NaN 自动），消除逐行 Python 追加 + 二次 None→NaN 遍历
                arr = np.array(rows, dtype=float)
                result[ck] = (arr[:, 0].copy(), arr[:, 1].copy())
            else:
                result[ck] = (np.empty(0, dtype=float), np.empty(0, dtype=float))
        return result

    def count_by_session(self, session_id: str) -> int:
        """
        查询会话的温度数据总数

        参数:
            session_id: 会话 ID

        返回:
            int: 数据总数
        """
        cursor = self.conn.execute('''
            SELECT COUNT(*) FROM temperature_readings WHERE session_id = ?
        ''', (session_id,))
        return cursor.fetchone()[0]

    def count_by_channel(self, session_id: str, channel_key: str) -> int:
        """
        查询特定通道的温度数据总数

        参数:
            session_id: 会话 ID
            channel_key: 通道键

        返回:
            int: 数据总数
        """
        cursor = self.conn.execute('''
            SELECT COUNT(*) FROM temperature_readings
            WHERE session_id = ? AND channel_key = ?
        ''', (session_id, channel_key))
        return cursor.fetchone()[0]

    def delete_by_session(self, session_id: str) -> bool:
        """
        删除会话的所有温度数据

        参数:
            session_id: 会话 ID

        返回:
            bool: 是否成功
        """
        try:
            cursor = self.conn.execute('''
                DELETE FROM temperature_readings WHERE session_id = ?
            ''', (session_id,))
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"删除温度数据失败: {e}")
            return False

    def get_time_range(self, session_id: str) -> tuple:
        """
        查询会话的时间范围

        参数:
            session_id: 会话 ID

        返回:
            tuple: (最小时间戳, 最大时间戳)
        """
        cursor = self.conn.execute('''
            SELECT MIN(timestamp), MAX(timestamp)
            FROM temperature_readings
            WHERE session_id = ?
        ''', (session_id,))
        row = cursor.fetchone()
        return (row[0] or 0, row[1] or 0)