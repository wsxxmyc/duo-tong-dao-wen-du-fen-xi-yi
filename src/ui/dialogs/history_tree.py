# -*- coding: utf-8 -*-
"""历史会话按月分组的纯逻辑（无 Qt 依赖，便于单测）。

每个月份节点带汇总（session_count / record_count / duration_sum）与
覆盖时间范围（start_ts / end_ts，供按范围删除使用）。
"""
from __future__ import annotations

import datetime
from typing import Dict, List


def group_by_month(sessions, record_counts=None) -> List[Dict]:
    """把会话列表按 月 分组，时间倒序（月倒序，月内会话时间倒序）。

    参数:
        sessions: 会话对象列表（须含 session_id / started_at / duration_seconds）
        record_counts: {session_id: 记录数}，缺省按 0 计

    返回:
        月份节点列表，结构：
        [{ 'key': '2026-08', 'label': '2026年8月',
           'session_count': N, 'record_count': N, 'duration_sum': sec,
           'start_ts': float, 'end_ts': float,
           'sessions': [session, ...] }]
    """
    counts = record_counts or {}
    months: Dict[str, Dict] = {}

    for s in sessions:
        if s.started_at is None:
            continue
        dt = datetime.datetime.fromtimestamp(s.started_at)
        mkey = f"{dt.year:04d}-{dt.month:02d}"
        mnode = months.get(mkey)
        if mnode is None:
            mnode = {
                'key': mkey, 'label': f"{dt.year}年{dt.month}月",
                'session_count': 0, 'record_count': 0, 'duration_sum': 0.0,
                'start_ts': s.started_at, 'end_ts': s.started_at,
                'sessions': [],
            }
            months[mkey] = mnode

        rec = counts.get(s.session_id, 0)
        dur = s.duration_seconds or 0.0
        mnode['sessions'].append(s)
        mnode['session_count'] += 1
        mnode['record_count'] += rec
        mnode['duration_sum'] += dur
        mnode['start_ts'] = min(mnode['start_ts'], s.started_at)
        mnode['end_ts'] = max(mnode['end_ts'], s.started_at)

    # 时间倒序：月 > 会话
    result = []
    for mkey in sorted(months, reverse=True):
        m = months[mkey]
        m['sessions'].sort(key=lambda x: x.started_at, reverse=True)
        result.append(m)
    return result