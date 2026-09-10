# -*- coding: utf-8 -*-
"""
acquisition.py —— 在线数据采集后端模块
职责：设备协议编解码、串口生命周期管理、后台轮询采集线程。
不依赖任何 UI，所有数据通过 Qt 信号传递。

依赖: pyserial (运行时安装，通过 try/except ImportError 处理)
"""

from __future__ import annotations

import os
import struct
import time
from typing import Optional

from PyQt5 import QtCore

# 串口诊断日志大小上限：超过则覆盖重写，避免长期采集无限增长
_SERIAL_LOG_MAX_BYTES = 2 * 1024 * 1024  # 2 MB

# 已知 USB 转串口芯片关键字（端口描述字段匹配，命中说明是 USB 适配的串口设备）
KNOWN_USB_SERIAL_KEYWORDS = ("CH340", "CH341", "CP210", "PL2303",
                             "FT232", "USB-SERIAL")


def port_score(description, vid, pid) -> int:
    """端口是 USB 转串口适配候选的打分（纯函数，供排序与自动选中复用）。

    - 有 vid/pid（USB 枚举）+2：物理 USB 设备
    - 描述命中已知芯片关键字（CH340/CP210x/PL2303/FT232/USB-SERIAL）+3
    """
    score = 0
    if vid and pid:
        score += 2
    desc = (description or "").upper()
    if any(k in desc for k in KNOWN_USB_SERIAL_KEYWORDS):
        score += 3
    return score


def sort_ports_detailed(ports) -> list:
    """按 USB 转串口适配度降序排序端口明细（纯函数）。

    入参/出参均为 (device, description, vid, pid) 列表；
    同分保持原有设备名顺序（sorted 稳定排序）。
    """
    return sorted(ports, key=lambda t: -port_score(t[1], t[2], t[3]))


# ============================================================================
# 协议常量（按协议行为实现，实测帧校验结果一致）
# ============================================================================

# --- 新协议（新版固件）---
TPID = 0x85          # 仪器代码
CONNECT = 0x11       # 连接检测
READ_SETDATA = 0x12  # 读设定值
READ_ALL_CH = 0x13   # 读取测量数据
READ_FMDATA = 0x14   # 读铁电
RECEIVE_HIT = 0x15   # 上位机接收成功
STOPREAD_FMDATA = 0x16  # 停止读铁电
SET_PARA = 0x17      # 设置参数
SET_CHANONOFF = 0x18 # 设置通道开关
READ_CHANONOFF = 0x19  # 读通道开关
SET_TEMPLIMIT = 0x1a # 设置温限
SET_TCTYPE = 0x1b    # 设置热电偶型号

# --- 旧协议（TP-1 / TP-2 老款仪器）---
CONNECT_OLD = 0x01       # 联机
SET_SAMP_CH_OLD = 0x02   # 设定巡检通道
READ_ALL_CH_OLD = 0x06   # 读所有通道温度（旧协议用和校验，无 CRC）

# 温度数据中的特殊值（16位有符号整数）
VAL_CHANNEL_OFF = 0x7777   # 通道关
VAL_OVERFLOW = 0x7fff      # 溢出
VAL_TC_OPEN = 0x7f7f       # 热电偶开路

# 指令/返回值的最小长度（TPID + CMD + CRC16）
MIN_FRAME_LEN = 4
# CRC16 字节数
CRC_LEN = 2


# ============================================================================
# AcquisitionProtocol — 协议编解码（纯函数，无状态）
# ============================================================================
class AcquisitionProtocol:
    """多通道测温设备串口协议编解码器。

    纯静态方法集合，不持有任何状态。
    """

    # --- 指令常量 ---
    TPID = TPID
    CONNECT = CONNECT
    READ_ALL_CH = READ_ALL_CH

    @staticmethod
    def calc_crc16(data: bytes) -> int:
        """计算 CRC16（Modbus 多项式 0xA001），按协议行为实现，实测帧校验结果一致。

        返回标准 16 位 CRC 值（未交换字节序）。发送帧与接收响应一律按
        [高字节, 低字节] 顺序传输（实测真机确认，与本模块 _frame 构建序一致）。
        """
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                tt = crc & 1
                crc >>= 1
                crc &= 0x7FFF
                if tt == 1:
                    crc ^= 0xA001
                crc &= 0xFFFF
        return crc

    @staticmethod
    def check_crc16(data_with_crc: bytes) -> bool:
        """校验 CRC（最后 2 字节为 CRC，高字节在前）。

        字节序与 _frame 发送序、设备实测响应一致：[高字节, 低字节]。
        （此前误改为低字节在前，导致握手/读设定值/读通道开关全部误判失败，
         见 _proto_audit.py 真机核对报告。）

        Args:
            data_with_crc: 包含 CRC 的完整数据帧

        Returns:
            True 校验通过，False 校验失败
        """
        if len(data_with_crc) < MIN_FRAME_LEN:
            return False
        payload = data_with_crc[:-CRC_LEN]
        expected_high = data_with_crc[-2]  # CRC 高字节（传输在前）
        expected_low = data_with_crc[-1]   # CRC 低字节（传输在后）
        computed = AcquisitionProtocol.calc_crc16(payload)
        return expected_high == (computed >> 8) & 0xFF and expected_low == computed & 0xFF

    @staticmethod
    def build_connect_command() -> bytes:
        """构建连接检测指令: TPID + CONNECT + CRC16（高字节在前）。"""
        return AcquisitionProtocol._frame(CONNECT)

    @staticmethod
    def build_read_command() -> bytes:
        """构建读取全部通道指令: TPID + READ_ALL_CH + CRC16（高字节在前）。"""
        return AcquisitionProtocol._frame(READ_ALL_CH)

    # ==================================================================
    # 新协议完整指令集（0x11~0x1B，CRC16）
    # ==================================================================

    @staticmethod
    def _frame(cmd: int, payload: bytes = b"") -> bytes:
        """构建新协议帧: TPID + CMD + [payload] + CRC16（高字节在前）。"""
        data = bytes([TPID, cmd]) + payload
        crc = AcquisitionProtocol.calc_crc16(data)
        return data + bytes([(crc >> 8) & 0xFF, crc & 0xFF])

    @staticmethod
    def build_read_setdata_command() -> bytes:
        """读设定值 (0x12): TPID + READ_SETDATA + CRC16。"""
        return AcquisitionProtocol._frame(READ_SETDATA)

    @staticmethod
    def parse_setdata_response(data: bytes) -> Optional[dict]:
        """解析读设定值返回帧。

        协议文档定义: TPID(1) + READ_SETDATA(1) + X1~X8(8) + Y(n) + Z(n) + CRC(2)，
        其中 X8=配置组数，Y/Z 各 n 字节。
        但真机实测（新固件）只返回: TPID(1) + READ_SETDATA(1) + X1~X8(8) + CRC(2)
        = 12 字节，**不含 Y(通道开关) / Z(热电偶型号)**。
        本解析两种结构都兼容：有 Y/Z 时按文档解析，没有时置空列表。
        （通道开关由独立的 READ_CHANONOFF(0x19) 命令读取，不依赖此帧的 Y。）
        """
        # 最小帧: 2(头) + 8(X) + 2(CRC) = 12 字节
        if (len(data) < 12 or data[0] != TPID or data[1] != READ_SETDATA
                or not AcquisitionProtocol.check_crc16(data)):
            return None
        x_offset = 2  # X1 从 offset 2 开始
        group_count = data[x_offset + 7]  # X8 = 配置组数（offset 9）
        y_start = x_offset + 8  # offset 10
        y_len = group_count
        z_len = group_count
        expected_len = 2 + 8 + y_len + z_len + 2

        channel_switches: list[int] = []
        tc_types: list[int] = []
        if len(data) >= expected_len:
            # 文档结构：含 Y/Z
            z_start = y_start + y_len
            channel_switches = list(data[y_start:y_start + y_len])
            tc_types = list(data[z_start:z_start + z_len])
        # 否则真机结构：仅 X8 + CRC（12 字节），Y/Z 置空
        return {
            "group_switch": data[x_offset],       # X1: 组开关
            "storage_flag": data[x_offset + 1],   # X2: 存储标志
            "storage_used": data[x_offset + 2],   # X3: 已用存储空间
            "store_hour": data[x_offset + 3],     # X4: 存储间隔-时
            "store_min": data[x_offset + 4],      # X5: 存储间隔-分
            "store_sec": data[x_offset + 5],      # X6: 存储间隔-秒
            "temp_scale": data[x_offset + 6],     # X7: 温标
            "group_count": group_count,           # X8: 配置组数
            "channel_switches": channel_switches,  # Y（真机不返则为空）
            "tc_types": tc_types,                  # Z（真机不返则为空）
        }

    @staticmethod
    def build_set_chanonoff_command(channel_masks: list[int]) -> bytes:
        """设置通道开关 (0x18)：发送 9 字节通道掩码。"""
        masks = list(channel_masks[:9]) + [0x00] * (9 - len(channel_masks))
        return AcquisitionProtocol._frame(SET_CHANONOFF, bytes(masks[:9]))

    @staticmethod
    def build_read_chanonoff_command() -> bytes:
        """读通道开关 (0x19): TPID + READ_CHANONOFF + CRC16。"""
        return AcquisitionProtocol._frame(READ_CHANONOFF)

    @staticmethod
    def parse_chanonoff_response(data: bytes) -> Optional[list[int]]:
        """解析读通道开关返回帧（9 字节掩码）。

        响应帧: TPID(1) + READ_CHANONOFF(1) + 9字节掩码 + CRC(2) = 13 字节
        """
        if (len(data) < 2 + 9 + 2 or data[0] != TPID
                or data[1] != READ_CHANONOFF
                or not AcquisitionProtocol.check_crc16(data)):
            return None
        return list(data[2:11])

    @staticmethod
    def build_set_para_command(group_switch: int, storage_flag: int,
                               clear_flag: int, store_h: int, store_m: int,
                               store_s: int) -> bytes:
        """设置参数 (0x17): TPID + SET_PARA + X1~X7 + CRC16。

        X7(温标)由设备管理，上位机不可更改，此处填 0 由设备保留原值。
        """
        payload = bytes([group_switch, storage_flag, clear_flag,
                         store_h, store_m, store_s, 0x00])
        return AcquisitionProtocol._frame(SET_PARA, payload)

    @staticmethod
    def build_set_tctype_command(tc_types: list[int]) -> bytes:
        """设置热电偶型号 (0x1B): TPID + SET_TCTYPE + 8字节型号 + CRC16。

        型号值: 0=K, 1=J, 2=T, 3=X（其他自定义）
        """
        types = list(tc_types[:8]) + [0x00] * (8 - len(tc_types))
        return AcquisitionProtocol._frame(SET_TCTYPE, bytes(types[:8]))

    @staticmethod
    def build_set_templimit_command(limit_id: int, switch_byte: int,
                                    upper: int, lower: int) -> bytes:
        """设置温限 (0x1A): TPID + SET_TEMPLIMIT + X1 + X2 + X3(4B) + CRC16。

        Args:
            limit_id: 温限号
            switch_byte: bit7=开关, bit6~4=组号, bit3~0=通道范围
            upper: 温度上限（实际温度*10）
            lower: 温度下限（实际温度*10，负数用补码）
        """
        payload = bytes([limit_id, switch_byte]) + struct.pack(">hh", upper, lower)
        return AcquisitionProtocol._frame(SET_TEMPLIMIT, payload)

    @staticmethod
    def build_read_fmdata_command() -> bytes:
        """读铁电存储数据 (0x14): TPID + READ_FMDATA + CRC16。"""
        return AcquisitionProtocol._frame(READ_FMDATA)

    @staticmethod
    def build_receive_hit_command() -> bytes:
        """上位机接收成功应答 (0x15): TPID + RECEIVE_HIT + CRC16。"""
        return AcquisitionProtocol._frame(RECEIVE_HIT)

    @staticmethod
    def build_stopread_fmdata_command() -> bytes:
        """停止读铁电 (0x16): TPID + STOPREAD_FMDATA + CRC16。"""
        return AcquisitionProtocol._frame(STOPREAD_FMDATA)

    # ==================================================================
    # 旧协议（TP-1 / TP-2）—— 和校验，无 CRC
    # ==================================================================

    @staticmethod
    def calc_checksum(data: bytes) -> int:
        """旧协议和校验：所有字节的累加和的低字节。

        旧协议 READ_ALL_CH_OLD 返回帧的校验方式（协议文档 X3）：
            "X1，X2 两部分所有字节之和的低字节"
        """
        return sum(data) & 0xFF

    @staticmethod
    def build_connect_old_command() -> bytes:
        """旧协议联机指令: 0x85, 0x01（无校验）。"""
        return bytes([TPID, CONNECT_OLD])

    @staticmethod
    def parse_connect_old_response(data: bytes) -> Optional[int]:
        """解析旧协议联机返回值: 0x85, 0x01, X。

        Returns:
            X=0 → TP-1（8通道），X=1 → TP-2（16通道）；失败返回 None
        """
        if len(data) < 3 or data[0] != TPID or data[1] != CONNECT_OLD:
            return None
        return data[2]

    @staticmethod
    def build_set_samp_ch_old_command(channels: int, channel_bytes: int = 1) -> bytes:
        """旧协议设定巡检通道: 0x85, 0x02, X。

        Args:
            channels: 通道开关位掩码。TP-1 为 1 字节，TP-2 为 2 字节。
            channel_bytes: 发送的掩码字节数，旧协议 TP-2 应传 2。
                      bit0~bit7 对应通道 0~7 的开关（1=开, 0=关）。
        """
        n = 2 if channel_bytes >= 2 else 1
        return bytes([TPID, SET_SAMP_CH_OLD]) + int(channels).to_bytes(n, "little")

    @staticmethod
    def build_read_old_command() -> bytes:
        """旧协议读所有通道温度: 0x85, 0x06（无校验）。"""
        return bytes([TPID, READ_ALL_CH_OLD])

    @staticmethod
    def parse_temperature_old(data: bytes, instrument_type: int) -> tuple:
        """解析旧协议温度返回值。

        返回帧格式: 0x85, 0x06, X1(冷端2B), X2(通道温度), X3(和校验1B)
          - TP-1: X2 = 16 字节 (8 通道 × 2B)
          - TP-2: X2 = 32 字节 (16 通道 × 2B)
        温度值 = 整型，溢出 0xFFFF，热电偶断开 0xFFFE

        Args:
            data: 完整返回帧
            instrument_type: 0=TP-1(8ch), 1=TP-2(16ch)

        Returns:
            (cold_junction_temp, [channel_temps...]) 冷端温度和通道温度列表
        """
        n_ch = 8 if instrument_type == 0 else 16
        temp_bytes_len = n_ch * 2
        # 帧头2B + 冷端2B + 温度 + 校验1B
        min_len = 2 + 2 + temp_bytes_len + 1
        if len(data) < min_len or data[0] != TPID or data[1] != READ_ALL_CH_OLD:
            return (None, [None] * n_ch)

        offset = 2
        # 冷端温度（2 字节，整型）
        cold_raw = struct.unpack_from("<H", data, offset)[0]
        cold_temp = None if cold_raw in (0xFFFF, 0xFFFE) else cold_raw / 10.0
        offset += 2

        # 通道温度
        temps = []
        for i in range(n_ch):
            raw = struct.unpack_from("<H", data, offset + i * 2)[0]
            if raw == 0xFFFF:
                temps.append(None)      # 溢出
            elif raw == 0xFFFE:
                temps.append(None)      # 热电偶断开
            else:
                temps.append(raw / 10.0)

        return (cold_temp, temps)

    @staticmethod
    def verify_checksum_old(data: bytes) -> bool:
        """校验旧协议返回帧的和校验。

        协议文档 X3: "X1(冷端)，X2(温度值) 两部分所有字节之和的低字节"
        即校验范围 = 冷端温度(2B) + 通道温度(n_ch*2B)，不含帧头和校验字节本身。
        """
        if len(data) < 5:
            return False
        # 跳过帧头(2B)，取冷端+温度数据到最后字节前（最后1字节是校验）
        payload_start = 2
        payload_end = len(data) - 1
        if payload_end <= payload_start:
            return False
        computed = sum(data[payload_start:payload_end]) & 0xFF
        return computed == data[payload_end]

    @staticmethod
    def parse_temperature(data: bytes, group_count: int) -> list[Optional[float]]:
        """解析温度数据。

        响应帧格式（读全通道温度响应）:
          [0..3]:   TPID + CMD + 2B headerCRC
          [4..]:    温度数据 (group_count * 16 字节) + tailCRC (2 字节)

        每个通道 2 字节，**大端序** 16 位有符号整数（实测真机确认）。
        实际温度 = 原始值 / 10

        特殊值（大端读取后的值）:
          0x7777 → 通道关       (返回 None)
          0x7FFF → 溢出         (返回 None)
          0x7F7F → 热电偶开路   (返回 None)

        Args:
            data: 完整的返回数据帧
            group_count: 配置组数

        Returns:
            list[Optional[float]]: 温度值列表，长度 = group_count * 8，
                                   无效通道返回 None
        """
        n_channels = group_count * 8
        payload_offset = 4
        expected_payload = group_count * 16

        if len(data) < payload_offset + expected_payload + CRC_LEN:
            return [None] * n_channels

        temp_bytes = data[payload_offset:payload_offset + expected_payload]

        result: list[Optional[float]] = []
        for i in range(n_channels):
            offset = i * 2
            if offset + 2 > len(temp_bytes):
                result.append(None)
                continue
            # 大端序 16 位有符号整数（实测真机确认）
            raw = struct.unpack_from(">h", temp_bytes, offset)[0]

            # 检查特殊值（大端读取后的值）
            if raw == VAL_CHANNEL_OFF:   # 0x7777
                result.append(None)
            elif raw == VAL_OVERFLOW:    # 0x7FFF
                result.append(None)
            elif raw == VAL_TC_OPEN:     # 0x7F7F
                result.append(None)
            else:
                # 实际温度 = 原始值 / 10
                temp_c = raw / 10.0
                result.append(temp_c)

        return result

    # ==================================================================
    # 协议自动检测（供 _do_handshake 与 main_window._start_acquisition 复用）
    # ==================================================================
    @staticmethod
    def detect_protocol(serial_mgr: "SerialPortManager"
                        ) -> Optional[tuple[str, dict]]:
        """自动检测设备使用新协议还是旧协议（无副作用，不改 serial_mgr 状态）。

        检测顺序：先试新协议 CONNECT(0x11)，失败再试旧协议 CONNECT_OLD(0x01)。
        三类结局：
          - 识别成功 → ("new"|"old", {"group_count", "instrument_type?"})
          - 协议不匹配 → ("mismatch", {})   设备在线但两种协议握手均被拒/无回显
          - 物理无响应 → None               一个字节都没回（线/供电/串口/波特率）

        新协议响应含 CRC（5B），旧协议无 CRC（3B）。NAK(cmd|0x80) 由
        send_and_receive 自动识别并返回，本函数据此区分『拒收』与『无响应』。

        Returns:
            Optional[tuple]: 见上；None 表示物理层完全无响应。
        """
        proto = AcquisitionProtocol()
        any_response = False  # 设备是否回过任何字节（区分物理无响应）

        # ① 新协议 CONNECT(0x11)：响应 = TPID + CONNECT + 组数 + CRC(2)
        cmd = proto.build_connect_command()
        resp = serial_mgr.send_and_receive(cmd, 5)
        if resp and len(resp) >= 2 and resp[0] == TPID:
            any_response = True
            if len(resp) >= 5 and resp[1] == CONNECT and proto.check_crc16(resp):
                gc = max(1, min(8, resp[2]))
                return ("new", {"group_count": gc})
            # resp[1]==0x91 (NAK) 或 CRC 错 → 设备在线但不认新协议，转旧协议

        # ② 旧协议 CONNECT_OLD(0x01)：响应 = TPID + CONNECT_OLD + X（无 CRC）
        cmd_old = proto.build_connect_old_command()
        resp_old = serial_mgr.send_and_receive(cmd_old, 3)
        if resp_old and len(resp_old) >= 2 and resp_old[0] == TPID:
            any_response = True
            if len(resp_old) >= 3 and resp_old[1] == CONNECT_OLD:
                inst = resp_old[2]
                # X=0 → TP-1（8 通道，1 组），X=1 → TP-2（16 通道，2 组）
                gc = 1 if inst == 0 else 2
                return ("old", {"group_count": gc, "instrument_type": inst})

        # 设备回过字节但两种协议都没拿到有效握手 → 协议不匹配
        return ("mismatch", {}) if any_response else None


# ============================================================================
# SerialPortManager — 串口生命周期管理
# ============================================================================
class SerialPortManager:
    """串口生命周期管理器。

    封装 pyserial 的打开/关闭/读写操作，缺省时优雅降级。
    """

    # 默认串口参数（与设备协议一致）
    DEFAULT_BAUDRATE = 2400
    DEFAULT_BYTESIZE = 8
    DEFAULT_PARITY = "N"
    DEFAULT_STOPBITS = 1
    # 单次读超时（秒）。2400 波特下 22 字节响应约需 92ms，但设备内部
    # 存储/处理可能偶发延迟，0.5s 过短会误报"无响应"，取 1.0s 更稳。
    DEFAULT_TIMEOUT = 1.0

    def __init__(self):
        self._port = None        # serial.Serial 实例
        self._serial = None      # serial 模块引用
        self._available = True   # pyserial 是否可用

        try:
            import serial as _serial_mod
            self._serial = _serial_mod
        except ImportError:
            self._available = False

    # --- 属性 ---

    @property
    def port(self) -> Optional[object]:
        """当前串口对象 (serial.Serial | None)。"""
        return self._port

    @property
    def is_open(self) -> bool:
        """串口是否已打开。"""
        return self._port is not None and self._port.is_open

    @property
    def is_available(self) -> bool:
        """pyserial 库是否可用。"""
        return self._available

    # --- 端口枚举 ---

    def list_ports(self, retries: int = 3) -> list[str]:
        """枚举可用 COM 口（自动重试，避免偶发空结果）。

        - include_links=True：兼容虚拟串口（VSPD / com0com 等链路）。
        - 空结果自动重试：部分 Windows 环境 comports() 偶发返回空列表
          （权限/时序问题，见 PITFALLS 1.2），重试可稳定枚举。
        - 去重：include_links 下可能出现重复设备名。

        Returns:
            list[str]: 端口名列表，如 ['COM1', 'COM3', ...]；不可用时返回空列表。
        """
        if not self._available:
            return []
        try:
            # 必须显式导入 tools.list_ports，否则 self._serial.tools 可能未绑定
            from serial.tools import list_ports as _lp
        except Exception as e:
            print(f"[ACQ] 枚举串口失败: {e}", flush=True)
            return []
        for attempt in range(max(1, retries)):
            try:
                ports = _lp.comports(include_links=True)
                seen, out = set(), []
                for p in sorted(ports):
                    dev = p.device
                    if dev and dev not in seen:
                        seen.add(dev)
                        out.append(dev)
                if out:
                    return out
            except Exception as e:
                print(f"[ACQ] 串口枚举第 {attempt + 1} 次尝试异常: {e}", flush=True)
            if attempt < retries - 1:
                time.sleep(0.4)
        return []

    def list_ports_detailed(self, retries: int = 3) -> list:
        """枚举 COM 口明细（设备名 + 描述 + VID/PID），自动重试与去重。

        与 list_ports 的区别：保留 comports() 的 description/vid/pid 字段，
        供前端显示「COM3 · USB-SERIAL CH340」并按 USB 转串口适配度排序。

        Returns:
            list[tuple]: (device, description, vid, pid) 列表（已去重，
            不排序——排序用纯函数 sort_ports_detailed）；不可用时返回空列表。
        """
        if not self._available:
            return []
        try:
            from serial.tools import list_ports as _lp
        except Exception as e:
            print(f"[ACQ] 枚举串口失败: {e}", flush=True)
            return []
        for attempt in range(max(1, retries)):
            try:
                ports = _lp.comports(include_links=True)
                seen, out = set(), []
                for p in sorted(ports, key=lambda x: x.device or ""):
                    dev = p.device
                    if dev and dev not in seen:
                        seen.add(dev)
                        out.append((dev, p.description or "",
                                    getattr(p, "vid", None),
                                    getattr(p, "pid", None)))
                if out:
                    return out
            except Exception as e:
                print(f"[ACQ] 串口明细枚举第 {attempt + 1} 次尝试异常: {e}",
                      flush=True)
            if attempt < retries - 1:
                time.sleep(0.4)
        return []

    # --- 打开/关闭 ---

    def open(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> bool:
        """打开串口，配置为 8N1。

        Args:
            port_name: COM 口名称，如 "COM3"
            baudrate:  波特率，默认 2400

        Returns:
            bool: 打开成功返回 True
        """
        if not self._available:
            return False

        self.close()

        try:
            self._port = self._serial.Serial(
                port=port_name,
                baudrate=baudrate,
                bytesize=self.DEFAULT_BYTESIZE,
                parity=self.DEFAULT_PARITY,
                stopbits=self.DEFAULT_STOPBITS,
                timeout=self.DEFAULT_TIMEOUT,
            )
            return True
        except Exception:
            self._port = None
            return False

    def close(self):
        """关闭串口。"""
        if self._port is not None:
            try:
                if self._port.is_open:
                    self._port.close()
            except Exception:
                pass
            self._port = None

    def set_baudrate(self, baudrate: int) -> bool:
        """运行时切换波特率（串口保持打开，pyserial 支持热切换）。

        用于握手阶段的波特率自动遍历：当前波特率握手失败时依次尝试
        2400/4800/9600/19200，直到找到可用波特率。
        """
        if self._port is None or not self._port.is_open:
            return False
        try:
            self._port.baudrate = int(baudrate)
            return True
        except Exception:
            return False

    def __del__(self):
        self.close()

    # --- 读写 ---

    def write(self, data: bytes) -> bool:
        """发送数据。

        Args:
            data: 要发送的字节数据

        Returns:
            bool: 发送成功返回 True
        """
        if not self.is_open:
            return False
        try:
            self._port.write(data)
            return True
        except Exception:
            return False

    def read(self, n: int, timeout: float | None = None) -> Optional[bytes]:
        """循环读取 n 个字节（处理分片到达），超时返回 None。

        在低波特率（2400）下，设备响应可能分片到达，单次 read(n) 可能读不满。
        本方法在总超时内循环读取，直至凑满 n 字节。

        Args:
            n:       期望读取的字节数
            timeout: 总超时秒数，None 用默认超时

        Returns:
            Optional[bytes]: 成功返回 bytes，超时或失败返回 None
        """
        if not self.is_open:
            return None
        try:
            import time as _t
            total_timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
            old_to = self._port.timeout
            self._port.timeout = 0.05  # 每次短超时，循环累积
            deadline = _t.monotonic() + total_timeout
            buf = b""
            try:
                while len(buf) < n and _t.monotonic() < deadline:
                    chunk = self._port.read(n - len(buf))
                    if chunk:
                        buf += chunk
            finally:
                self._port.timeout = old_to
            return buf if len(buf) == n else None
        except Exception:
            return None

    def send_and_receive(self, data: bytes, resp_len: int, retries: int = 3,
                         resp_cmd: int | None = None) -> Optional[bytes]:
        """发送指令并接收响应，内置 3 次自动重试。

        成功条件：收到 resp_len 字节，且响应帧头(TPID)与指令帧头匹配。
        设备返回错误帧（指令号 | 0x80，即 NAK）时立即返回该帧、不重试——
        即：设备在线但拒收该命令，
        重试无意义，应由上层决定是否换协议。

        Args:
            data:     要发送的指令数据
            resp_len: 期望接收的响应字节数
            retries:  重试次数（默认 3）
            resp_cmd: 期望响应回显的指令号。默认与发送指令相同；个别指令
                      （如 SET_PARA 0x17 成功回显 0x12 READ_SETDATA）需显式指定。

        Returns:
            Optional[bytes]: 成功返回响应数据；设备 NAK 返回错误帧；失败返回 None
        """
        if len(data) < 2:
            return None
        expected_cmd = resp_cmd if resp_cmd is not None else data[1]
        if not self.is_open:
            return None
        result = None
        for _ in range(retries):
            try:
                self._port.reset_input_buffer()
            except Exception:
                pass
            if not self.write(data):
                continue
            resp = self.read(resp_len)
            if resp and len(resp) >= resp_len:
                # 帧头匹配校验（TPID + CMD 回显）
                if resp[0] == data[0] and resp[1] == expected_cmd:
                    result = resp
                    break
                # NAK：设备在线但拒收（指令号 | 0x80）。立即返回，不重试。
                if resp[0] == data[0] and resp[1] == (expected_cmd | 0x80):
                    result = resp
                    break
        # 记录完整命令↔响应到日志文件（诊断设备联机/通道开关用）
        self._log_tx_rx(data, result)
        return result

    def _log_tx_rx(self, tx: bytes, rx: Optional[bytes]):
        """把一次串口事务（发送命令 + 设备响应）追加到诊断日志。

        日志存于 <软件根>/用户数据/logs/acq_serial.log；超过 2MB 自动覆盖重写，
        避免长期采集导致日志无限增长。
        """
        try:
            from utils.helpers import _resolve_log_dir
            path = os.path.join(_resolve_log_dir(), "acq_serial.log")
            # 大小滚动：超限则覆盖重写，只保留近期诊断信息
            mode = "w" if (os.path.exists(path)
                           and os.path.getsize(path) > _SERIAL_LOG_MAX_BYTES) else "a"
            line = f"TX {tx.hex(' ')}\nRX {rx.hex(' ') if rx else '无响应'}\n"
            with open(path, mode, encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass


# ============================================================================
# AcquisitionWorker — 定时轮询线程
# ============================================================================
class AcquisitionWorker(QtCore.QThread):
    """后台采集线程：定时轮询设备，通过信号发射温度数据。

    数据流:
      串口 → SerialPortManager → AcquisitionWorker.run() → signals
    """

    # 温度数据信号: timestamp(float), channel_values(list[float|None])
    data_received = QtCore.pyqtSignal(float, list)
    # 连接状态变化信号
    connection_changed = QtCore.pyqtSignal(bool)
    # 错误信号
    error_occurred = QtCore.pyqtSignal(str)

    # 默认采集间隔（毫秒）
    DEFAULT_INTERVAL_MS = 1000
    # 默认重试间隔（秒）
    RETRY_INTERVAL_SEC = 2.0
    # 最大连续错误次数后主动断开
    MAX_CONSECUTIVE_ERRORS = 5

    def __init__(self, serial_mgr: SerialPortManager,
                 protocol: AcquisitionProtocol | None = None,
                 interval_ms: int = DEFAULT_INTERVAL_MS,
                 group_count: int = 8,
                 active_groups: int | None = None,
                 proto_mode: str = "auto",
                 instrument_type: int = 0,
                 parent: QtCore.QObject | None = None):
        """
        Args:
            serial_mgr:  串口管理器实例
            protocol:    协议编解码器实例，None 时使用 AcquisitionProtocol 默认实例
            interval_ms: 轮询间隔（毫秒）
            group_count: 配置组数（默认为 8）——决定读取响应帧长度，须为设备实际组数
            active_groups: 实际启用的组数（≤ group_count）。None 时与 group_count 相同。
                         读取完整帧但只上报前 active_groups*8 个通道（后台"采集组数"设置）。
            proto_mode:  协议模式 "auto"(自动握手) / "new"(直接新协议) / "old"(直接旧协议)
            instrument_type: 旧协议设备类型（0=TP-1, 1=TP-2），仅 proto_mode="old" 时用
            parent:      QObject 父对象
        """
        super().__init__(parent)
        self._serial_mgr = serial_mgr
        self._protocol = protocol or AcquisitionProtocol()
        self._interval_ms = max(100, interval_ms)  # 最小 100ms
        self._group_count = group_count
        # 实际启用的通道组数（后台"采集组数"设置），默认与设备配置组数一致
        if active_groups is not None and 0 < active_groups < group_count:
            self._active_groups = int(active_groups)
        else:
            self._active_groups = group_count
        self._active_channels = self._active_groups * 8

        # 运行控制
        self._running = False       # 是否运行中
        self._paused = False        # 是否暂停

        # 错误计数
        self._error_count = 0

        # 协议模式（构造时传入，避免重复握手）
        self._proto_mode = proto_mode  # "auto" / "new" / "old"
        self._instrument_type = instrument_type  # 旧协议：0=TP-1, 1=TP-2

    # --- 属性 ---

    def set_interval(self, ms: int) -> None:
        """运行时修改采集间隔。

        Args:
            ms: 新的间隔毫秒数（最小 100ms）
        """
        self._interval_ms = max(100, ms)

    def set_group_count(self, count: int):
        """设置配置组数。

        Args:
            count: 组数 (1~8)
        """
        self._group_count = max(1, min(8, count))

    def set_active_groups(self, count: int) -> None:
        """运行时调整实际启用的组数（左面板组联动：取消组 → 停止上报该组）。

        只限制上报的通道数（values[:active_channels]），不改设备读取帧长。
        """
        if 0 < count <= self._group_count:
            self._active_groups = int(count)
            self._active_channels = self._active_groups * 8

    @property
    def is_running(self) -> bool:
        """线程是否运行中。"""
        return self._running

    # --- 生命期控制 ---

    def run(self) -> None:
        """轮询主循环入口（在子线程中执行）。

        顶层捕获所有未预期异常：任何错误都经 error_occurred / connection_changed
        通知 UI，避免线程静默死亡、界面卡在 "采集ing" 假状态（C5 不变量）。
        实际握手与轮询逻辑见 _run_impl。
        """
        try:
            self._run_impl()
        except Exception:
            import traceback
            tb = traceback.format_exc()
            print(f"[ACQ] 采集线程异常退出:\n{tb}", flush=True)
            try:
                last = tb.strip().splitlines()[-1] if tb.strip() else "未知错误"
                self.error_occurred.emit(f"采集线程异常退出：{last}")
                self.connection_changed.emit(False)
            except Exception:
                pass
            self._running = False

    def _run_impl(self) -> None:
        """轮询主循环实际实现。

        流程:
          1. 连接握手（自动检测新/旧协议，获取配置组数）
          2. 每 _interval_ms 执行一次采集
          3. 按检测到的协议读温度
          4. emit data_received(timestamp, values)
          5. 连续错误超限自动断开
        """
        self._running = True
        self._error_count = 0

        # 若构造时未指定协议模式（"auto"），则自动握手检测；
        # 已指定模式则跳过握手（避免重复握手干扰设备）
        if self._serial_mgr.is_open:
            if self._proto_mode == "auto":
                result = AcquisitionProtocol.detect_protocol(self._serial_mgr)
                if result is None:
                    self.error_occurred.emit("连接失败：设备无响应，请检查线缆、供电、串口和波特率")
                    self.connection_changed.emit(False)
                    self._running = False
                    return
                proto_mode, info = result
                if proto_mode == "mismatch":
                    self.error_occurred.emit("连接失败：设备在线但新/旧协议握手均被拒收，请手动选择协议版本")
                    self.connection_changed.emit(False)
                    self._running = False
                    return
                self._proto_mode = proto_mode
                self._group_count = info["group_count"]
                if "instrument_type" in info:
                    self._instrument_type = info["instrument_type"]
                self.connection_changed.emit(True)
                print(f"[ACQ] 自动握手: 协议={self._proto_mode}, 组数={self._group_count}", flush=True)
            else:
                self.connection_changed.emit(True)
                print(f"[ACQ] 使用预设协议: {self._proto_mode}, 组数={self._group_count}", flush=True)
        else:
            self.error_occurred.emit("串口未打开")
            self._running = False
            return

        while self._running:
            if self._paused:
                time.sleep(0.2)
                continue

            if not self._serial_mgr.is_open:
                time.sleep(self.RETRY_INTERVAL_SEC)
                continue

            # 按协议模式读取温度
            if self._proto_mode == "old":
                values = self._read_old()
            else:
                values = self._read_new()

            if values is None:
                self._error_count += 1
                self.error_occurred.emit(
                    f"读取数据失败 ({self._error_count}/{self.MAX_CONSECUTIVE_ERRORS})"
                )
                if self._error_count >= self.MAX_CONSECUTIVE_ERRORS:
                    self.connection_changed.emit(False)
                time.sleep(self.RETRY_INTERVAL_SEC)
                continue

            # 成功：清除错误计数
            if self._error_count > 0:
                self._error_count = 0
                self.connection_changed.emit(True)

            # 后台"采集组数"设置：只上报前 active_groups*8 个通道，丢弃其余
            if values is not None and self._active_channels < len(values):
                values = values[:self._active_channels]

            timestamp = time.time()
            # 发射数据：timestamp, values（用于数据库写入）
            self.data_received.emit(timestamp, values)
            time.sleep(self._interval_ms / 1000.0)

        self._running = False

    def _read_new(self):
        """新协议读温度（READ_ALL_CH 0x13 + CRC16）。

        响应帧结构（实测真机确认）:
          [TPID(1) + CMD(1) + headerCRC(2)] + [temp_data(group*16)] + [tailCRC(2)]
        headerCRC 覆盖 TPID+CMD；tailCRC 只覆盖 temp_data。
        """
        cmd = self._protocol.build_read_command()
        resp_len = 4 + self._group_count * 16 + CRC_LEN
        resp = self._serial_mgr.send_and_receive(cmd, resp_len)
        if not resp or len(resp) < resp_len:
            print(f"[ACQ] 读温失败: 响应不足 ({len(resp) if resp else 0}/{resp_len})", flush=True)
            return None
        if resp[0] != TPID or resp[1] != READ_ALL_CH:
            print(f"[ACQ] 读温失败: 帧头不匹配 {resp[0]:02x}/{resp[1]:02x} (期望 85/13)", flush=True)
            return None
        # 校验尾部 CRC（只覆盖温度数据部分）。校验失败仅记录警告、不中断
        # 采集：防御性处理——CRC 失配时温度字段本身仍可用。
        temp_data = resp[4:4 + self._group_count * 16 + CRC_LEN]
        if not self._protocol.check_crc16(temp_data):
            tail = temp_data[-2:].hex() if len(temp_data) >= 2 else ""
            print(f"[ACQ] 读温警告: 尾部 CRC 未通过 (尾部={tail})，继续解析",
                  flush=True)
        values = self._protocol.parse_temperature(resp, self._group_count)
        if values and not any(v is not None for v in values):
            # 全部无效 → 打印特殊值分布，便于诊断设备通道使能 / 热电偶状态
            try:
                n = self._group_count * 8
                raw = struct.unpack_from(">%dh" % n, resp, 4)
                off = sum(1 for r in raw if r == VAL_CHANNEL_OFF)
                opn = sum(1 for r in raw if r == VAL_TC_OPEN)
                ovf = sum(1 for r in raw if r == VAL_OVERFLOW)
                oth = sum(1 for r in raw
                          if r not in (VAL_CHANNEL_OFF, VAL_TC_OPEN, VAL_OVERFLOW))
                print(f"[ACQ] 全通道无效: 通道关={off} 开路={opn} 溢出={ovf} 其它={oth} "
                      f"(帧头={resp[:4].hex()})", flush=True)
            except Exception as e:
                print(f"[ACQ] 全通道无效诊断异常: {e}", flush=True)
        return values

    def _read_old(self):
        """旧协议读温度（READ_ALL_CH_OLD 0x06 + 和校验）。"""
        cmd = self._protocol.build_read_old_command()
        n_ch = 8 if self._instrument_type == 0 else 16
        # 返回帧: TPID(1) + CMD(1) + 冷端(2) + 温度(n_ch*2) + 校验(1)
        resp_len = 2 + 2 + n_ch * 2 + 1
        resp = self._serial_mgr.send_and_receive(cmd, resp_len)
        if not resp or len(resp) < resp_len:
            return None
        if resp[0] != TPID or resp[1] != READ_ALL_CH_OLD:
            return None
        cold, temps = self._protocol.parse_temperature_old(resp, self._instrument_type)
        return temps

    def stop(self, timeout_ms: int = 5000) -> bool:
        """停止轮询（设置标志位 + 等待线程结束）。可安全重复调用。

        返回 True 表示线程在 timeout_ms 内干净退出；False 表示仍运行（通常卡在
        阻塞式串口读写或双协议握手中）。返回 False 时调用方**不可**立即释放
        worker 引用，否则触发 "QThread: Destroyed while thread is still running"
        闪退（C4 不变量）；须保留引用直到 finished 信号。
        """
        self._running = False
        if self.isRunning():
            self.wait(timeout_ms)
            if self.isRunning():
                print(
                    f"[ACQ] stop() 等待 {timeout_ms}ms 后线程仍未退出（可能卡在阻塞"
                    f"串口读写/握手中）；调用方须保留 worker 引用直到 finished",
                    flush=True,
                )
                return False
        return True

    def pause(self) -> None:
        """暂停采集（不关闭串口）。"""
        self._paused = True

    def resume(self) -> None:
        """恢复采集。"""
        self._paused = False
