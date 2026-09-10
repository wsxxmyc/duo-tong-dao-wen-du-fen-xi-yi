"""
数据库数据模型定义

定义数据库表对应的 Python 数据类，用于类型标注和数据传递。
兼容 Python 3.8.10，避免使用 3.9+ 新特性。
"""

from dataclasses import dataclass
from typing import Optional
import time


@dataclass
class Session:
    """测量会话数据模型"""
    session_id: str
    session_name: Optional[str] = None
    started_at: float = 0.0
    stopped_at: Optional[float] = None
    duration_seconds: Optional[float] = None
    channel_count: int = 0
    interval_seconds: float = 1.0
    source_type: str = 'live'  # 'live' 或 'file'
    source_path: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[str] = None  # JSON 字符串
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    locked: bool = False  # 锁定白名单：30 天保留策略自动清理时跳过

    def to_dict(self) -> dict:
        """转换为字典（用于数据库插入）"""
        return {
            'session_id': self.session_id,
            'session_name': self.session_name,
            'started_at': self.started_at,
            'stopped_at': self.stopped_at,
            'duration_seconds': self.duration_seconds,
            'channel_count': self.channel_count,
            'interval_seconds': self.interval_seconds,
            'source_type': self.source_type,
            'source_path': self.source_path,
            'notes': self.notes,
            'tags': self.tags,
            'created_at': self.created_at or time.time(),
            'updated_at': self.updated_at or time.time(),
            'locked': 1 if self.locked else 0
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Session':
        """从字典创建实例"""
        return cls(
            session_id=data.get('session_id', ''),
            session_name=data.get('session_name'),
            started_at=data.get('started_at', 0.0),
            stopped_at=data.get('stopped_at'),
            duration_seconds=data.get('duration_seconds'),
            channel_count=data.get('channel_count', 0),
            interval_seconds=data.get('interval_seconds', 1.0),
            source_type=data.get('source_type', 'live'),
            source_path=data.get('source_path'),
            notes=data.get('notes'),
            tags=data.get('tags'),
            created_at=data.get('created_at'),
            updated_at=data.get('updated_at'),
            locked=bool(data.get('locked', 0))
        )


@dataclass
class Channel:
    """通道配置数据模型"""
    channel_id: Optional[int] = None
    session_id: str = ''
    channel_key: str = ''  # CH1, CH2, ..., CH64
    channel_name: Optional[str] = None
    color: Optional[str] = None
    physical_name: Optional[str] = None
    unit: str = '°C'
    visible: bool = True

    def to_dict(self) -> dict:
        """转换为字典（用于数据库插入）"""
        return {
            'channel_id': self.channel_id,
            'session_id': self.session_id,
            'channel_key': self.channel_key,
            'channel_name': self.channel_name,
            'color': self.color,
            'physical_name': self.physical_name,
            'unit': self.unit,
            'visible': 1 if self.visible else 0
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Channel':
        """从字典创建实例"""
        return cls(
            channel_id=data.get('channel_id'),
            session_id=data.get('session_id', ''),
            channel_key=data.get('channel_key', ''),
            channel_name=data.get('channel_name'),
            color=data.get('color'),
            physical_name=data.get('physical_name'),
            unit=data.get('unit', '°C'),
            visible=bool(data.get('visible', 1))
        )


@dataclass
class TemperatureReading:
    """温度数据模型"""
    reading_id: Optional[int] = None
    session_id: str = ''
    channel_key: str = ''
    timestamp: float = 0.0
    temperature: Optional[float] = None  # NULL 表示无效温度
    is_valid: bool = True

    def to_dict(self) -> dict:
        """转换为字典（用于数据库插入）"""
        return {
            'reading_id': self.reading_id,
            'session_id': self.session_id,
            'channel_key': self.channel_key,
            'timestamp': self.timestamp,
            'temperature': self.temperature,
            'is_valid': 1 if self.is_valid else 0
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'TemperatureReading':
        """从字典创建实例"""
        return cls(
            reading_id=data.get('reading_id'),
            session_id=data.get('session_id', ''),
            channel_key=data.get('channel_key', ''),
            timestamp=data.get('timestamp', 0.0),
            temperature=data.get('temperature'),
            is_valid=bool(data.get('is_valid', 1))
        )


@dataclass
class AlarmEvent:
    """报警事件记录（进入 active / 恢复 cleared）。

    channel_key 为 ``'__diff__'`` 表示通道间温差（帧级报警）。
    """
    event_id: Optional[int] = None
    session_id: str = ''
    channel_key: str = ''
    timestamp: float = 0.0
    alarm_type: str = ''           # 'high' / 'low' / 'rate' / 'diff'
    threshold: Optional[float] = None
    actual_value: Optional[float] = None
    status: str = 'active'         # 'active' / 'cleared'

    def to_dict(self) -> dict:
        """转换为字典（用于数据库插入）。"""
        return {
            'event_id': self.event_id,
            'session_id': self.session_id,
            'channel_key': self.channel_key,
            'timestamp': self.timestamp,
            'alarm_type': self.alarm_type,
            'threshold': self.threshold,
            'actual_value': self.actual_value,
            'status': self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AlarmEvent':
        """从字典创建实例。"""
        return cls(
            event_id=data.get('event_id'),
            session_id=data.get('session_id', ''),
            channel_key=data.get('channel_key', ''),
            timestamp=data.get('timestamp', 0.0),
            alarm_type=data.get('alarm_type', ''),
            threshold=data.get('threshold'),
            actual_value=data.get('actual_value'),
            status=data.get('status', 'active'),
        )


@dataclass
class DeviceConnection:
    """设备连接历史数据模型"""
    connection_id: Optional[int] = None
    session_id: Optional[str] = None
    com_port: Optional[str] = None
    device_type: Optional[str] = None
    baud_rate: Optional[int] = None
    connected_at: Optional[float] = None
    disconnected_at: Optional[float] = None
    connection_status: Optional[str] = None  # 'success' | 'timeout' | 'error'
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        """转换为字典（用于数据库插入）"""
        return {
            'connection_id': self.connection_id,
            'session_id': self.session_id,
            'com_port': self.com_port,
            'device_type': self.device_type,
            'baud_rate': self.baud_rate,
            'connected_at': self.connected_at,
            'disconnected_at': self.disconnected_at,
            'connection_status': self.connection_status,
            'error_message': self.error_message
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'DeviceConnection':
        """从字典创建实例"""
        return cls(
            connection_id=data.get('connection_id'),
            session_id=data.get('session_id'),
            com_port=data.get('com_port'),
            device_type=data.get('device_type'),
            baud_rate=data.get('baud_rate'),
            connected_at=data.get('connected_at'),
            disconnected_at=data.get('disconnected_at'),
            connection_status=data.get('connection_status'),
            error_message=data.get('error_message')
        )


@dataclass
class ExportHistory:
    """导出记录数据模型"""
    export_id: Optional[int] = None
    session_id: Optional[str] = None
    export_type: Optional[str] = None  # 'excel' | 'html' | 'png'
    export_path: Optional[str] = None
    exported_at: Optional[float] = None
    file_size_bytes: Optional[int] = None

    def to_dict(self) -> dict:
        """转换为字典（用于数据库插入）"""
        return {
            'export_id': self.export_id,
            'session_id': self.session_id,
            'export_type': self.export_type,
            'export_path': self.export_path,
            'exported_at': self.exported_at,
            'file_size_bytes': self.file_size_bytes
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ExportHistory':
        """从字典创建实例"""
        return cls(
            export_id=data.get('export_id'),
            session_id=data.get('session_id'),
            export_type=data.get('export_type'),
            export_path=data.get('export_path'),
            exported_at=data.get('exported_at'),
            file_size_bytes=data.get('file_size_bytes')
        )


@dataclass
class ChannelStats:
    """通道统计信息数据模型"""
    stat_id: Optional[int] = None
    session_id: str = ''
    channel_key: str = ''
    min_temp: Optional[float] = None
    max_temp: Optional[float] = None
    avg_temp: Optional[float] = None
    std_dev: Optional[float] = None
    valid_count: int = 0
    invalid_count: int = 0
    rise_rate: Optional[float] = None
    rise_time_seconds: Optional[float] = None

    def to_dict(self) -> dict:
        """转换为字典（用于数据库插入）"""
        return {
            'stat_id': self.stat_id,
            'session_id': self.session_id,
            'channel_key': self.channel_key,
            'min_temp': self.min_temp,
            'max_temp': self.max_temp,
            'avg_temp': self.avg_temp,
            'std_dev': self.std_dev,
            'valid_count': self.valid_count,
            'invalid_count': self.invalid_count,
            'rise_rate': self.rise_rate,
            'rise_time_seconds': self.rise_time_seconds
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'ChannelStats':
        """从字典创建实例"""
        return cls(
            stat_id=data.get('stat_id'),
            session_id=data.get('session_id', ''),
            channel_key=data.get('channel_key', ''),
            min_temp=data.get('min_temp'),
            max_temp=data.get('max_temp'),
            avg_temp=data.get('avg_temp'),
            std_dev=data.get('std_dev'),
            valid_count=data.get('valid_count', 0),
            invalid_count=data.get('invalid_count', 0),
            rise_rate=data.get('rise_rate'),
            rise_time_seconds=data.get('rise_time_seconds')
        )