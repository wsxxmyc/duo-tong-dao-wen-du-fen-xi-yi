# -*- coding: utf-8 -*-
"""Modbus RTU 帧组解（报警独立串口输出，零新依赖）。

复用 ``acquisition.AcquisitionProtocol.calc_crc16``（即 Modbus CRC16，多项式 0xA001、
初值 0xFFFF）。注意：Modbus RTU 标准要求 CRC 低字节在前，与本项目采集协议
「高字节在前」相反——组帧时按 Modbus 标准，单元测试用独立 CRC 实现锁死。

详见 ``文档-docs/温度报警功能-alarm-feature-design-2026-08-12.md`` §8。
"""

from device.acquisition import AcquisitionProtocol


def _crc16(data: bytes) -> int:
    """Modbus CRC16（复用采集协议的同款 CRC 计算）。"""
    return AcquisitionProtocol.calc_crc16(data)


def build_write_single_coil(slave: int, coil: int, on: bool) -> bytes:
    """构建 Modbus「写单个线圈」(0x05) 请求帧。

    Args:
        slave: 从站地址（自动钳制到 1-247）。
        coil:  线圈地址（自动钳制到 0-65535）。
        on:    True=ON(0xFF00) 触发报警，False=OFF(0x0000) 解除。

    Returns:
        8 字节请求帧：
        ``[slave][0x05][coil Hi][coil Lo][value Hi][value Lo][CRC Lo][CRC Hi]``。
        CRC 按 Modbus 标准低字节在前。
    """
    slave = max(1, min(247, int(slave)))
    coil = max(0, min(65535, int(coil)))
    value = 0xFF00 if on else 0x0000
    frame = bytes([slave, 0x05,
                   (coil >> 8) & 0xFF, coil & 0xFF,
                   (value >> 8) & 0xFF, value & 0xFF])
    crc = _crc16(frame)
    return frame + bytes([crc & 0xFF, (crc >> 8) & 0xFF])  # 低字节在前
