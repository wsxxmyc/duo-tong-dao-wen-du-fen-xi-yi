# -*- coding: utf-8 -*-
"""采集落库兜底文件（spill）——写库失败时数据旁路落盘、恢复后回灌。

背景（整改计划 P1-3）：UI 冻结或磁盘/数据库故障时，采集数据不允许静默丢失。
Recorder 连续写库失败或缓冲超限时，把批次逐行追加为 JSONL 兜底文件
（``用户数据/wal_spill_<session_id>.jsonl``，逐批 flush+fsync）；
DB 恢复后由 flusher 自动回灌，程序重启后由启动扫描提示回灌残留文件。

行格式（每行一条）：
    {"session_id": "...", "row": {"channel_key", "timestamp", "temperature", "is_valid"}}

约定：
- 回灌成功（事务提交）后文件改名 ``.done``，不重复导入；
- 失败批次只在 spill 文件中驻留，绝不与内存缓冲双份共存导致重复入库；
- 本模块为纯 Python，无 Qt 依赖，可在任意线程调用。
"""
from __future__ import annotations

import glob
import json
import os
import time
from typing import Dict, List, Optional, Tuple

SPILL_PREFIX = "wal_spill_"
SPILL_SUFFIX = ".jsonl"


def default_spill_dir() -> str:
    """兜底文件目录：与历史数据库同目录（用户数据/data）。"""
    from device.database.history_db import default_db_path
    d = os.path.dirname(default_db_path())
    return d or "."


def spill_path(session_id: str, directory: Optional[str] = None) -> str:
    """指定会话的兜底文件路径（不创建目录；目录在首次写入时建）。"""
    directory = directory or default_spill_dir()
    return os.path.join(directory, f"{SPILL_PREFIX}{session_id}{SPILL_SUFFIX}")


def append_rows(path: str, session_id: str, rows: List[Dict]) -> int:
    """把一批行追加写入兜底文件并强制落盘。返回写入行数。

    写失败抛异常（调用方负责把行放回内存缓冲，做最后一级兜底）。
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fp:
        for r in rows:
            fp.write(json.dumps(
                {"session_id": session_id, "row": r},
                ensure_ascii=False, default=float) + "\n")
        fp.flush()
        os.fsync(fp.fileno())
    return len(rows)


def read_rows(path: str) -> Tuple[List[Tuple[str, Dict]], int]:
    """读取兜底文件。返回 ([(session_id, row), ...], 损坏行数)。"""
    out: List[Tuple[str, Dict]] = []
    corrupt = 0
    try:
        with open(path, "r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    out.append((str(obj.get("session_id") or ""),
                                dict(obj.get("row") or {})))
                except (ValueError, TypeError, AttributeError):
                    corrupt += 1
    except OSError:
        return [], corrupt
    return out, corrupt


def find_residual_files(directory: Optional[str] = None) -> List[str]:
    """扫描未回灌的兜底文件（.done 已排除）。"""
    directory = directory or default_spill_dir()
    return sorted(glob.glob(os.path.join(directory,
                                          SPILL_PREFIX + "*" + SPILL_SUFFIX)))


def count_rows(path: str) -> int:
    """统计兜底文件行数（启动提示用；损坏行不计入）。"""
    rows, _ = read_rows(path)
    return len(rows)


def replay_file(history_db, path: str) -> Tuple[int, str]:
    """把一个兜底文件按会话分组回灌进数据库。

    返回 (回灌成功行数, 错误信息)。全部会话导入成功才改名 .done；
    会话在库中不存在时中止并保留文件（不静默丢数据），由调用方报告。
    """
    rows, corrupt = read_rows(path)
    if corrupt:
        return 0, f"兜底文件存在 {corrupt} 行损坏数据，已中止回灌：{path}"
    if not rows:
        try:
            os.remove(path)
        except OSError as e:
            return 0, f"清理空兜底文件失败: {e}"
        return 0, ""
    # 按会话分组，逐会话事务写入（insert_readings_batch 内部单事务）
    by_session: Dict[str, List[Dict]] = {}
    for sid, row in rows:
        if not sid:
            return 0, f"兜底文件存在缺少会话 ID 的行，已中止回灌：{path}"
        by_session.setdefault(sid, []).append(row)
    for sid, batch in by_session.items():
        if history_db.get_session_by_id(sid) is None:
            # 会话已被 30 天保留策略 CASCADE 清理：不自动重建会话，
            # 文件改名 .orphan 保留现场并报告，绝不静默丢弃（P1-3 审计项）
            orphan = path + ".orphan"
            try:
                if os.path.exists(orphan):
                    os.remove(orphan)
                os.rename(path, orphan)
            except OSError as e:
                return 0, (f"会话 {sid} 不在历史库中，且兜底文件改名失败: {e}"
                           f"（文件保留：{path}）")
            return 0, (f"会话 {sid} 不在历史库中（超出 30 天保留期），"
                       f"数据已转入孤儿文件待人工处置：{orphan}")
        if not history_db.insert_readings_batch(sid, batch):
            return 0, f"回灌写入失败（数据库异常），兜底文件已保留：{path}"
    done = path + ".done"
    try:
        if os.path.exists(done):
            os.remove(done)
        os.rename(path, done)
    except OSError as e:
        return len(rows), f"数据已入库但兜底文件改名失败: {e}"
    return len(rows), ""


def replay_all(history_db, directory: Optional[str] = None) -> Dict:
    """回灌所有残留兜底文件。返回 {"files": int, "rows": int, "errors": [str]}。"""
    files = find_residual_files(directory)
    total_rows = 0
    errors: List[str] = []
    for p in files:
        n, err = replay_file(history_db, p)
        total_rows += n
        if err:
            errors.append(err)
    return {"files": len(files), "rows": total_rows, "errors": errors}
