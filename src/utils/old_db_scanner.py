# -*- coding: utf-8 -*-
"""旧版本包数据库扫描器（跨版本升级一键迁移）。

绿色版用户数据跟随软件包目录走：<软件根>/用户数据/data/temperature_history.db。
解压新版本包后新库为空，旧库仍留在旧版本包内——本模块按固定特征点定位
本机上所有旧版本包的历史数据库，供启动后台扫描与历史对话框手动扫描复用。

特征点（只比对目录名，不做文件内容遍历，整盘可秒级到十几秒完成）：
1. 目录名以「温度分析工具」开头（发布包固定命名，如
   温度分析工具_v4.2.2_20260924_第1版_x64）；
2. 目录名恰为「用户数据」（任何运行过一次的绿色版包内必有，
   包目录被用户改名后仍可命中），其下 data/temperature_history.db
   存在即候选。

扫描范围：
- 锚点：软件根的父目录、桌面、下载（浅层快速通道）；
- 整盘：所有固定磁盘（软件所在盘优先），带剪枝与深度上限。

迁移执行不在本模块：复用 HistoryDatabase.import_external_sessions
（只读打开源库、按会话 ID 去重合并、不覆盖现有数据、原库不动）。
用户的迁移/跳过决定记入「用户数据/.old_db_migrations.json」，
避免每次启动对同一个库重复弹窗。
"""
import ctypes
import json
import os
import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional

from .helpers import USER_DATA_DIR, _resolve_app_root

# 发布包目录命名特征（目录名前缀）
PACKAGE_DIR_PREFIX = "温度分析工具"
# 旧包内数据库相对路径（相对包根）
REL_DB_PATH = os.path.join(USER_DATA_DIR, "data", "temperature_history.db")

# 整盘扫描最大目录深度（发布包常见嵌套 3~4 层，留足余量）
MAX_SCAN_DEPTH = 10
# 锚点目录扫描深度（父目录/桌面/下载里的浅层位置）
ANCHOR_SCAN_DEPTH = 3

# 剪枝目录名（小写比对）：系统/缓存等不可能存放用户发布包的位置
_PRUNE_DIR_NAMES = {
    "windows", "program files", "program files (x86)", "programdata",
    "appdata", "$recycle.bin", "system volume information", "recovery",
    "perflogs", "msocache", "intel", "amd", "nvidia", "drivers",
    "node_modules", "__pycache__", ".git", ".svn", ".vs", ".vscode",
    "application data", "documents and settings",
}


def _norm(path: str) -> str:
    """规整路径用于比较（绝对路径 + 小写，Windows 大小写不敏感）。"""
    return os.path.normpath(os.path.abspath(path)).lower()


def _is_under(path: str, root: str) -> bool:
    """path 是否等于 root 或位于 root 之下。"""
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r + os.sep)


def _match_candidate_db(name: str, dir_path: str) -> Optional[str]:
    """目录名特征点命中则返回候选数据库路径，否则返回 None。

    1. 目录名恰为「用户数据」→ 数据库在 dir_path/data/ 下
       （包目录被用户改名仍可命中）；
    2. 目录名以「温度分析工具」开头 → 包根就是 dir_path。
    """
    if name == USER_DATA_DIR:
        db = os.path.join(dir_path, "data", "temperature_history.db")
    elif name.startswith(PACKAGE_DIR_PREFIX):
        db = os.path.join(dir_path, REL_DB_PATH)
    else:
        return None
    return db if os.path.isfile(db) else None


def _scan_dir(root: str, depth: int, max_depth: int, app_root_norm: str,
              seen: set, out: List[Dict[str, str]],
              should_abort: Optional[Callable[[], bool]],
              progress: Callable[[str], None]) -> bool:
    """深度优先扫描目录特征点；返回 False 表示外部要求中止。

    命中的候选（包根去重后）追加进 out；剪枝目录与以 . 开头的
    隐藏目录不下钻；符号链接/重解析点不跟随。
    """
    if depth > max_depth:
        return True
    if should_abort is not None and should_abort():
        return False
    try:
        entries = list(os.scandir(root))
    except OSError:
        return True  # 无权限/盘符消失等：跳过该目录，不中断整体扫描
    for entry in entries:
        if should_abort is not None and should_abort():
            return False
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        name = entry.name
        path = entry.path
        if app_root_norm and _is_under(path, app_root_norm):
            continue  # 本软件自身包子树（含当前正在用的库）不作候选
        if name.lower() in _PRUNE_DIR_NAMES or name.startswith("."):
            continue
        db = _match_candidate_db(name, path)
        if db is not None:
            pkg_dir = os.path.dirname(path) if name == USER_DATA_DIR else path
            key = _norm(pkg_dir)
            if key not in seen:
                seen.add(key)
                out.append({"package_dir": pkg_dir, "db_path": db})
        progress(path)
        # 「用户数据」目录内部不会再有旧包，无需下钻；其余继续深入
        if name != USER_DATA_DIR:
            if not _scan_dir(path, depth + 1, max_depth, app_root_norm,
                             seen, out, should_abort, progress):
                return False
    return True


def _anchor_dirs(app_root: str) -> List[str]:
    """锚点目录：软件根父目录、桌面、下载（存在才返回，去重）。"""
    home = os.path.expanduser("~")
    candidates = [
        os.path.dirname(os.path.normpath(os.path.abspath(app_root))),
        os.path.join(home, "Desktop"),
        os.path.join(home, "Downloads"),
    ]
    out: List[str] = []
    for d in candidates:
        if d and os.path.isdir(d):
            if not out or _norm(d) != _norm(out[-1]):
                out.append(d)
    return out


def fixed_drives(app_root: Optional[str] = None) -> List[str]:
    """枚举固定磁盘盘根（DRIVE_FIXED）；软件所在盘排最前。

    可插拔/网络/光驱盘不扫，避免拖慢与误报。非 Windows 返回空列表。
    """
    if os.name != "nt":
        return []
    drives: List[str] = []
    try:
        kernel32 = ctypes.windll.kernel32
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            # GetDriveTypeW 返回 3 = DRIVE_FIXED
            if kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) == 3:
                drives.append(root)
    except Exception:
        return []
    if app_root:
        app_drive = os.path.abspath(app_root)[:3].upper()

        def _rank(d: str) -> int:
            return 0 if os.path.abspath(d)[:3].upper() == app_drive else 1

        drives.sort(key=lambda d: (_rank(d), d))
    return drives


def scan(app_root: Optional[str] = None,
         drive_roots: Optional[List[str]] = None,
         include_anchors: bool = True,
         max_depth: int = MAX_SCAN_DEPTH,
         should_abort: Optional[Callable[[], bool]] = None,
         progress_cb: Optional[Callable[[str], None]] = None,
         ) -> List[Dict[str, str]]:
    """扫描本机旧版本包数据库，返回候选列表（去重、不含本软件包子树）。

    每项 {"package_dir": 包根目录, "db_path": 历史数据库文件路径}。
    drive_roots 供测试/手动限定范围，缺省为全部固定磁盘（软件所在盘
    优先）；should_abort 返回 True 时尽快中止并返回已收集结果；
    progress_cb 传入当前扫描目录（内部按 0.25 秒节流）。
    """
    root = app_root or _resolve_app_root()
    app_root_norm = _norm(root)
    seen: set = set()
    out: List[Dict[str, str]] = []
    last = {"t": 0.0}

    def progress(path: str) -> None:
        if progress_cb is None:
            return
        now = time.time()
        if now - last["t"] >= 0.25:
            last["t"] = now
            try:
                progress_cb(path)
            except Exception:
                pass

    if include_anchors:
        for anchor in _anchor_dirs(root):
            if not _scan_dir(anchor, 1, ANCHOR_SCAN_DEPTH, app_root_norm,
                             seen, out, should_abort, progress):
                return out
    if drive_roots is None:
        drive_roots = fixed_drives(root)
    for drive in drive_roots or []:
        if should_abort is not None and should_abort():
            break
        if not os.path.isdir(drive):
            continue
        if not _scan_dir(drive, 1, max_depth, app_root_norm,
                         seen, out, should_abort, progress):
            break
    return out


def probe_db(db_path: str) -> Dict[str, Any]:
    """只读探测候选库：校验表结构并统计会话数与起止时间。

    返回 {"db_path", "sessions", "first_ts", "last_ts", "size_bytes"}；
    文件不存在 / 打不开 / 不是本程序数据库时抛 ValueError。

    只读 URI 打开，外部文件零污染；若库残留 WAL 而缺 -shm（如手工
    复制过文件）导致只读打不开，退回 immutable 方式再试——忽略 WAL
    可能少读最近未落盘会话，仅影响候选统计，不影响迁移合并本身。
    """
    if not os.path.isfile(db_path):
        raise ValueError(f"文件不存在：{db_path}")
    # 路径中的 ?/# 需转义，避免被当作 URI 参数（与导入链路口径一致）
    esc = db_path.replace("?", "%3F").replace("#", "%23")
    src = None
    try:
        try:
            src = sqlite3.connect(f"file:{esc}?mode=ro", uri=True)
            return _probe_with(src, db_path)
        except sqlite3.Error:
            if src is not None:
                try:
                    src.close()
                except Exception:
                    pass
            try:
                src = sqlite3.connect(f"file:{esc}?mode=ro&immutable=1",
                                      uri=True)
                return _probe_with(src, db_path)
            except sqlite3.Error as e:
                raise ValueError(f"无法读取数据库：{e}")
    finally:
        if src is not None:
            try:
                src.close()
            except Exception:
                pass


def _probe_with(src, db_path: str) -> Dict[str, Any]:
    """在已打开的只读连接上校验表结构并读取统计。"""
    try:
        cur = src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND "
            "name IN ('sessions','channels','temperature_readings')")
        tables = {row[0] for row in cur.fetchall()}
    except sqlite3.Error as e:
        raise ValueError(f"无法读取数据库：{e}")
    if not {"sessions", "channels", "temperature_readings"} <= tables:
        raise ValueError("不是本程序的历史数据库文件（缺少数据表）")
    row = src.execute(
        "SELECT COUNT(*), MIN(started_at), MAX(started_at) FROM sessions"
    ).fetchone()
    return {
        "db_path": db_path,
        "sessions": int(row[0] or 0),
        "first_ts": float(row[1]) if row[1] is not None else None,
        "last_ts": float(row[2]) if row[2] is not None else None,
        "size_bytes": os.path.getsize(db_path),
    }


def scan_with_probe(should_abort: Optional[Callable[[], bool]] = None,
                    progress_cb: Optional[Callable[[str], None]] = None,
                    **scan_kwargs) -> List[Dict[str, Any]]:
    """扫描 + 探测一步到位：只返回通过表结构校验的有效候选。

    按最早会话时间升序（老库排前）；无效文件（垃圾/非本程序库）静默
    剔除——扫描面上可能存在同名目录，探测是最终裁决。
    """
    found = scan(should_abort=should_abort, progress_cb=progress_cb,
                 **scan_kwargs)
    valid: List[Dict[str, Any]] = []
    for item in found:
        try:
            valid.append(probe_db(item["db_path"]))
        except (ValueError, sqlite3.Error):
            continue
    valid.sort(key=lambda c: (
        c["first_ts"] if c["first_ts"] is not None else float("inf"),
        c["db_path"]))
    return valid


# ======================================================================
#  迁移决定记录（.old_db_migrations.json，避免每次启动重复弹窗）
# ======================================================================

def decisions_path(app_root: Optional[str] = None) -> str:
    """迁移决定记录文件路径：<软件根>/用户数据/.old_db_migrations.json。"""
    root = app_root or _resolve_app_root()
    return os.path.join(root, USER_DATA_DIR, ".old_db_migrations.json")


def load_decisions(app_root: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """读取迁移决定记录；文件缺失/损坏返回空 dict（可诊断降级）。"""
    path = decisions_path(app_root)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, ValueError):
        pass
    return {}


def record_decision(db_path: str, choice: str, imported: int = 0,
                    app_root: Optional[str] = None) -> None:
    """记录对一个旧库的处理决定：migrated / skip / never。

    skip（本次跳过）下次启动会再询问；never（不再提示）与 migrated
    永久静默。写入失败仅影响免打扰，不影响迁移结果。
    """
    data = load_decisions(app_root)
    data[_norm(db_path)] = {
        "choice": choice, "at": time.time(), "imported": int(imported)}
    path = decisions_path(app_root)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def filter_by_decisions(candidates: List[Dict[str, Any]],
                        decisions: Dict[str, Dict[str, Any]],
                        ) -> List[Dict[str, Any]]:
    """过滤掉已迁移（migrated）与不再提示（never）的候选；skip 保留再问。"""
    drop = {"migrated", "never"}
    return [c for c in candidates
            if decisions.get(_norm(c["db_path"]), {}).get("choice")
            not in drop]
