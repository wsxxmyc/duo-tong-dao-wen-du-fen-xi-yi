# -*- coding: utf-8 -*-
"""
JSON 配置文件的读取/写入 —— 从 app.py 提取的配置存取方法。
所有操作代理到指定 main_window 的属性上读写。
"""

import hashlib
import math
import os
import json

from .helpers import _resolve_config_dir, _resolve_data_dir, _resolve_export_root_dir


CONFIG_DIR = _resolve_config_dir()
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
DATA_DIR = _resolve_data_dir()

# 统一配置文件结构版本（用于版本跟踪与后续结构升级判断）
CONFIG_VERSION = 1

# 本进程最近一次写入 settings.json 的内容哈希。
# ConfigWatcher 用它区分「自身写入」与「外部修改」，避免热重载循环刷新。
_WRITTEN_HASH = None


def _file_hash(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return hashlib.sha1(f.read().encode("utf-8", "replace")).hexdigest()
    except Exception:
        return None

# 配置文件的路径常量（与 app.py 保持一致）
AXIS_CONFIG_FILE = os.path.join(CONFIG_DIR, "axis_config.json")
A4_CONFIG_FILE = os.path.join(CONFIG_DIR, "a4_config.json")
OVERVIEW_CONFIG_FILE = os.path.join(CONFIG_DIR, "overview_config.json")
RISE_CONFIG_FILE = os.path.join(CONFIG_DIR, "rise_config.json")
LAST_PATH_FILE = os.path.join(CONFIG_DIR, "last_path.json")
COMBO_LAYOUT_CFG = os.path.join(CONFIG_DIR, "combo_layout_config.json")

# 双区视图右区实时窗宽度档位（分钟）与默认档。
# 2026-08-31 由 10/20/30（默认 20）收紧为 1/2/3/5（默认 2）：
# 窄窗下采集点逐秒跳动的细节才可见，10 分钟起太长。
# 该组常量保留给尚未迁移的旧 UI 调用方；持久化新契约使用下方秒级档位。
LIVE_WINDOW_MIN_CHOICES = (1, 2, 3, 5)
LIVE_WINDOW_MIN_DEFAULT = 2

# 主图实时窗口的新配置契约：持久化单位为秒。
LIVE_WINDOW_SEC_CHOICES = (12, 24, 36, 48, 60)
# 默认档 2026-09-11 由 60 收紧为 24：双区图右区实时窗在 24 秒下细节更可读（用户拍板）。
LIVE_WINDOW_SEC_DEFAULT = 24

# 监控悬浮卡外观配置契约：唯一底色调 card_color（边框/卡体/顶栏/画布 band 由其派生分层）、
# 整卡不透明度 card_alpha（0~1 全区间，见 CARD_ALPHA_MIN/MAX）、趋势线/填充色 line_color
# （空串=跟随主题强调色，非空=自定义优先）、悬浮球与时间窗口；
# 位置状态契约：ball_mode（docked 吸附位 / free 自由）、ball_corner（吸附位 center 画布正中/
# top_right 右上角/top_left 左上角/bottom_right 右下角，供 docked 态与启动恢复）、
# ball_pos_x/y（free 态球左上角全局坐标，启动越界时自动回默认吸附位）。
LIVE_MONITOR_DEFAULTS = {
    "card_color": "#e8ecef",    # 悬浮卡唯一底色调（边框/卡体/顶栏/画布 band 均由其派生分层）
    "card_alpha": 1.0,          # 整卡不透明度（2026-09-11 默认改 100% 全不透明，用户拍板）
    "line_color": "",           # 趋势线/填充色；空串=跟随当前主题强调色 Theme.ACCENT
    "ball_size": 100,           # 悬浮球（宠物恐龙）直径像素 40~120（2026-09-11 默认改 100）
    "ball_alpha": 1.0,          # 悬浮球不透明度 0.40~1.00
    "window_sec": 16,           # 弹窗时间窗口秒数 5~120（顶部滑动条可调；默认 16s 用户拍板）
    "ball_mode": "docked",      # 悬浮球位置状态：docked=吸附宿主可视区位置预设，free=用户自由放置
    "ball_corner": "bottom_right",  # 吸附位偏好：bottom_right（默认，屏幕右下角，2026-09-11 起）| center | top_right | top_left
    "ball_pos_x": 0,            # free 态保存的球左上角全局 X（docked 态忽略）
    "ball_pos_y": 0,            # free 态保存的球左上角全局 Y（docked 态忽略）
    "ball_show_max_temp": True, # 球心显示可见通道最高温（数值直显，实时采集生效）
    "ball_temp_warn_pct": 90,   # 接近报警上限的百分比阈值（≥ 则琥珀警示）；0 = 不启用琥珀档
    "ball_temp_warn_high": 0.0, # 自定义警示上限 ℃；0 = 跟随报警上限/温度轴（与曲线报警同源）
    "ball_enabled": True,       # 悬浮球总开关（托盘右键菜单底部勾选项）：False=悬浮球族即使在线采集中也隐藏
    # —— 生态舱形象（pet_style=cabin 时生效；dino 分支完全忽略这些键）——
    "pet_style": "dino",        # 悬浮球桌宠形象：dino=测温恐龙（默认）| cabin=玻璃生态舱
    "cabin_fluct_rate": 2.0,    # 波动档灵敏度 ℃/s（|ΔT|≥rate×Δt 记波动）；0=关闭波动档
    "cabin_channel_highs": {},  # 逐通道显示阈值 {通道稳定键: ℃}；缺省通道跟随全局报警上限
    # 全通道悬浮面板（独立于球与趋势弹窗的第三块悬浮显示）：
    "panel_enabled": False,     # 显示开关（实时会话时显示全部通道名+温度+进度条）
    "panel_alpha": 0.85,        # 面板不透明度 0.40~1.00
    "panel_theme": "light",     # 卡片皮肤：light=浅色卡（默认）| dark=深色卡（骨架色与场景主题解耦）
    "panel_pos_x": 0,           # 面板左上角全局 X（拖拽后持久化，启动越界自动回默认位）
    "panel_pos_y": 0,           # 面板左上角全局 Y（同上）
}

# 卡片不透明度允许区间。旧版钳到 0.30~0.95，导致输入「100%」被静默压成 95%、
# 「0%」被抬成 30%，用户直观感受就是「透明度设置不生效」，故此处放开到全区间：
# 0.0 = 全透明，1.0 = 完全不透明，仅做范围校验而不再压缩可用档位。
CARD_ALPHA_MIN = 0.0
CARD_ALPHA_MAX = 1.0


def _clamp_float(value, lo, hi, default):
    """把 value 转成 [lo, hi] 内的有限浮点，非法回退 default。"""
    if isinstance(value, bool):
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    return max(lo, min(hi, v))


def parse_percent(value, minimum=0.0, maximum=100.0):
    """把百分比输入解析为数值，同时兼容「100%」与「0~100」两种写法。

    返回 ``(ok, value, message)`` 三元组：
    - ``ok``：输入是否为可解析的数字；无法解析时为 ``False``，``value`` 回退下限。
    - ``value``：解析结果，已钳制到 ``[minimum, maximum]``。
    - ``message``：非法或越界时的提示文案，合法且在范围内时为空串。

    约定：裸数字按「百分比」理解（与设置页 0~100 的量程一致），
    因此 ``100``、``"100"``、``"100%"`` 三者等价，``60`` 与 ``"60%"`` 等价。
    """
    invalid_msg = f"请输入 {minimum:g}~{maximum:g} 之间的数字（可带 %）"
    if isinstance(value, bool):
        return False, minimum, invalid_msg
    text = None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("%"):
            text = text[:-1].strip()
    try:
        number = float(text) if text is not None else float(value)
    except (TypeError, ValueError):
        return False, minimum, invalid_msg
    if not math.isfinite(number):
        return False, minimum, invalid_msg
    clamped = max(minimum, min(maximum, number))
    if clamped != number:
        return True, clamped, (
            f"已超出范围（{minimum:g}~{maximum:g}），自动修正为 {clamped:g}")
    return True, clamped, ""


def clamp_alpha(value):
    """把任意输入钳到卡片不透明度允许区间（0.0 全透明 ~ 1.0 完全不透明）。

    纯函数放在本模块（而非弹窗 UI 模块），避免 settings_dialog 反向 import
    图表模块连带初始化 matplotlib 后端；非法/非有限输入回退默认不透明度。
    """
    if isinstance(value, bool):
        return LIVE_MONITOR_DEFAULTS["card_alpha"]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return LIVE_MONITOR_DEFAULTS["card_alpha"]
    if not math.isfinite(number):
        return LIVE_MONITOR_DEFAULTS["card_alpha"]
    return max(CARD_ALPHA_MIN, min(CARD_ALPHA_MAX, number))


def _normalize_hex_color(value, default):
    """校验 #rrggbb 颜色字符串，非法回退 default。"""
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("#") and len(s) == 7:
            try:
                int(s[1:], 16)
                return s.lower()
            except ValueError:
                return default
    return default


def _normalize_optional_hex_color(value, default=""):
    """校验「可为空」的颜色值：空串/None 表示未自定义（跟随主题），其余按 #rrggbb 校验。

    用于趋势线自定义色：空串是合法取值（表示跟随主题色），非空但非法时回退
    ``default``（默认空串，即退回跟随主题）。
    """
    if value is None:
        return default
    if isinstance(value, str) and value.strip() == "":
        return ""
    return _normalize_hex_color(value, default)


def _normalize_live_monitor_config(config):
    """归一化监控悬浮卡外观配置，越界/非法回退默认值。

    外观采用「由唯一底色调 card_color 派生的多层」：卡体/顶栏 band/画布 band/
    边框均由 card_color 按明暗分层，整卡透明度统一走一个 card_alpha。
    趋势线/填充色 line_color 为空串时跟随当前主题强调色，非空则优先使用自定义色。
    兼容历史键迁移——新配置只写 card_*，旧配置才带 canvas_/border_/popup_ 键，
    故按「绘图主色优先」取第一个存在的键即可无歧义还原：
    - 颜色：canvas_color > border_color > card_color；
    - 透明度：canvas_alpha > border_alpha > popup_alpha > card_alpha。

    card_alpha 取值范围为 CARD_ALPHA_MIN~CARD_ALPHA_MAX（0.0~1.0），越界钳到端点，
    不再压缩到 0.30~0.95，以保证「全透明 / 完全不透明」两档可以真正设置到。
    """
    def _first_present(*keys):
        for k in keys:
            v = config.get(k)
            if v is not None:
                return v
        return None

    cfg = dict(LIVE_MONITOR_DEFAULTS)
    if isinstance(config, dict):
        cfg["card_color"] = _normalize_hex_color(
            _first_present("canvas_color", "border_color", "card_color"),
            LIVE_MONITOR_DEFAULTS["card_color"])
        cfg["card_alpha"] = _clamp_float(
            _first_present("canvas_alpha", "border_alpha", "popup_alpha",
                           "card_alpha"),
            CARD_ALPHA_MIN, CARD_ALPHA_MAX, LIVE_MONITOR_DEFAULTS["card_alpha"])
        # 趋势线/填充色：空串=跟随主题，非空必须是合法 #rrggbb
        cfg["line_color"] = _normalize_optional_hex_color(
            config.get("line_color"), LIVE_MONITOR_DEFAULTS["line_color"])
        cfg["ball_size"] = int(_clamp_float(
            config.get("ball_size"), 40, 120,
            LIVE_MONITOR_DEFAULTS["ball_size"]))
        cfg["ball_alpha"] = _clamp_float(
            config.get("ball_alpha"), 0.40, 1.00,
            LIVE_MONITOR_DEFAULTS["ball_alpha"])
        cfg["window_sec"] = int(_clamp_float(
            config.get("window_sec"), 5, 120,
            LIVE_MONITOR_DEFAULTS["window_sec"]))
        # 位置状态：mode / corner 仅接受枚举值，其余回退默认；pos 宽松钳制防脏数据
        cfg["ball_mode"] = config.get("ball_mode")
        if cfg["ball_mode"] not in ("docked", "free"):
            cfg["ball_mode"] = LIVE_MONITOR_DEFAULTS["ball_mode"]
        cfg["ball_corner"] = config.get("ball_corner")
        if cfg["ball_corner"] not in ("bottom_right", "top_right",
                                      "top_left", "center"):
            cfg["ball_corner"] = LIVE_MONITOR_DEFAULTS["ball_corner"]
        cfg["ball_pos_x"] = int(_clamp_float(
            config.get("ball_pos_x"), -40000, 40000,
            LIVE_MONITOR_DEFAULTS["ball_pos_x"]))
        cfg["ball_pos_y"] = int(_clamp_float(
            config.get("ball_pos_y"), -40000, 40000,
            LIVE_MONITOR_DEFAULTS["ball_pos_y"]))
        # 球心最高温显示：开关稳健解析布尔；pct 0~100；high 0=跟随报警上限
        raw_show = config.get("ball_show_max_temp")
        if isinstance(raw_show, str):
            show = raw_show.strip().lower() in ("1", "true", "yes", "on")
        else:
            show = bool(raw_show) if raw_show is not None else \
                LIVE_MONITOR_DEFAULTS["ball_show_max_temp"]
        cfg["ball_show_max_temp"] = show
        cfg["ball_temp_warn_pct"] = int(_clamp_float(
            config.get("ball_temp_warn_pct"), 0, 100,
            LIVE_MONITOR_DEFAULTS["ball_temp_warn_pct"]))
        cfg["ball_temp_warn_high"] = _clamp_float(
            config.get("ball_temp_warn_high"), 0.0, 9999.0,
            LIVE_MONITOR_DEFAULTS["ball_temp_warn_high"])
        # 悬浮球总开关（托盘菜单底部勾选项）：布尔稳健解析，与
        # ball_show_max_temp 同规则；键缺失/None 回退默认开启
        raw_ball_on = config.get("ball_enabled")
        if isinstance(raw_ball_on, str):
            ball_on = raw_ball_on.strip().lower() in ("1", "true", "yes", "on")
        else:
            ball_on = bool(raw_ball_on) if raw_ball_on is not None else \
                LIVE_MONITOR_DEFAULTS["ball_enabled"]
        cfg["ball_enabled"] = ball_on
        # 生态舱：形象枚举钳制；波动灵敏度 0~20；逐通道阈值仅保留正数浮点，
        # 非 dict 忽略（旧配置无键=默认 dino / 跟随全局上限）
        cfg["pet_style"] = config.get("pet_style")
        if cfg["pet_style"] not in ("dino", "cabin"):
            cfg["pet_style"] = LIVE_MONITOR_DEFAULTS["pet_style"]
        cfg["cabin_fluct_rate"] = _clamp_float(
            config.get("cabin_fluct_rate"), 0.0, 20.0,
            LIVE_MONITOR_DEFAULTS["cabin_fluct_rate"])
        highs = config.get("cabin_channel_highs")
        clean_highs = {}
        if isinstance(highs, dict):
            for k, v in highs.items():
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                if v > 0 and isinstance(k, str) and k:
                    clean_highs[k] = v
        cfg["cabin_channel_highs"] = clean_highs
        # 全通道悬浮面板：开关稳健解析布尔（与 ball_show_max_temp 同规则）；
        # 透明度与球同区间 0.40~1.00；位置宽松钳制防脏数据
        raw_panel = config.get("panel_enabled")
        if isinstance(raw_panel, str):
            panel_on = raw_panel.strip().lower() in ("1", "true", "yes", "on")
        else:
            panel_on = bool(raw_panel) if raw_panel is not None else \
                LIVE_MONITOR_DEFAULTS["panel_enabled"]
        cfg["panel_enabled"] = panel_on
        cfg["panel_alpha"] = _clamp_float(
            config.get("panel_alpha"), 0.40, 1.00,
            LIVE_MONITOR_DEFAULTS["panel_alpha"])
        # 卡片皮肤：仅接受 light/dark 枚举，其余回退默认浅色卡
        cfg["panel_theme"] = config.get("panel_theme")
        if cfg["panel_theme"] not in ("light", "dark"):
            cfg["panel_theme"] = LIVE_MONITOR_DEFAULTS["panel_theme"]
        cfg["panel_pos_x"] = int(_clamp_float(
            config.get("panel_pos_x"), -40000, 40000,
            LIVE_MONITOR_DEFAULTS["panel_pos_x"]))
        cfg["panel_pos_y"] = int(_clamp_float(
            config.get("panel_pos_y"), -40000, 40000,
            LIVE_MONITOR_DEFAULTS["panel_pos_y"]))
    return cfg


def _normalize_live_window_sec(value, legacy_minutes=False):
    """把秒级实时窗口值归一化到当前档位。

    ``legacy_minutes=True`` 仅用于读取旧的 axis.live_window_min，先将
    分钟转换为秒，再按秒级档位取最近值；距离相等时取较大的档位。
    """
    if isinstance(value, bool):
        return LIVE_WINDOW_SEC_DEFAULT
    try:
        v = float(value)
        if legacy_minutes:
            v *= 60.0
    except (TypeError, ValueError, OverflowError):
        return LIVE_WINDOW_SEC_DEFAULT
    if not math.isfinite(v) or v <= 0.0:
        return LIVE_WINDOW_SEC_DEFAULT
    return min(LIVE_WINDOW_SEC_CHOICES, key=lambda c: (abs(c - v), -c))


def _normalize_live_window_min(value):
    """把存量 live_window_min 归一化到旧 UI 档位。

    仅保留给尚未迁移的旧运行时调用方；新配置迁移必须使用
    :func:`_normalize_live_window_sec` 并先进行分钟到秒的换算。
    """
    if isinstance(value, bool):
        return LIVE_WINDOW_MIN_DEFAULT
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return LIVE_WINDOW_MIN_DEFAULT
    if not math.isfinite(v) or v <= 0.0:
        return LIVE_WINDOW_MIN_DEFAULT
    return min(LIVE_WINDOW_MIN_CHOICES, key=lambda c: (abs(c - v), -c))


class ConfigIO:
    """JSON 配置文件读写工具类。
    所有方法均为 @staticmethod，接受 main_window 参数（主窗口引用）以读写其属性。
    """

    @staticmethod
    def load_section(section, default=None, legacy_files=()):
        """读取统一设置文件中的一个分区；首次使用时兼容旧 JSON 文件。"""
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                value = data.get(section)
                if value is not None:
                    return value
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            pass
        for path in legacy_files:
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        value = json.load(f)
                    ConfigIO.save_section(section, value)
                    return value
            except Exception as e:
                print(f"[CONFIG] 配置操作异常: {e}", flush=True)
                continue
        return default

    @staticmethod
    def save_section(section, value):
        """原子更新统一设置文件中的一个分区，避免覆盖其他设置。"""
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            data = {}
            if os.path.exists(SETTINGS_FILE):
                try:
                    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data = loaded
                except Exception as e:
                    print(f"[CONFIG] 配置操作异常: {e}", flush=True)
                    data = {}
            data[section] = value
            data["_meta"] = {"version": CONFIG_VERSION}
            temp = SETTINGS_FILE + ".tmp"
            with open(temp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp, SETTINGS_FILE)
            global _WRITTEN_HASH
            _WRITTEN_HASH = _file_hash(SETTINGS_FILE)
            return True
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            return False

    @staticmethod
    def written_hash():
        """返回本进程最后一次写入 settings.json 的内容哈希（可能为 None）。"""
        return _WRITTEN_HASH

    # ---- 上次路径 ----
    @staticmethod
    def load_last_dir():
        """读取上次打开文件的目录路径。"""
        try:
            cfg = ConfigIO.load_section("last_path", {}, (LAST_PATH_FILE,))
            d = cfg.get("last_dir", "")
            if d and os.path.isdir(d):
                return d
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            pass
        return os.getcwd()

    @staticmethod
    def save_last_dir(path):
        """保存文件所在目录为上次路径。"""
        try:
            d = os.path.dirname(path)
            ConfigIO.save_section("last_path", {"last_dir": d})
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            pass

    @staticmethod
    def load_last_export_dir():
        """读取上次导出目录；目录失效时返回图片导出根目录（<软件根>/导出图片）。"""
        try:
            cfg = ConfigIO.load_section("last_path", {}, (LAST_PATH_FILE,))
            directory = cfg.get("last_export_dir", "")
            if directory and os.path.isdir(directory):
                return directory
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            pass
        return _resolve_export_root_dir()

    @staticmethod
    def save_last_export_dir(directory):
        """保存导出目录，保留现有 last_path 分区中的其他字段。"""
        try:
            if not directory or not os.path.isdir(directory):
                return False
            cfg = ConfigIO.load_section("last_path", {}, (LAST_PATH_FILE,))
            if not isinstance(cfg, dict):
                cfg = {}
            cfg["last_export_dir"] = os.path.abspath(directory)
            return ConfigIO.save_section("last_path", cfg)
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            return False

    # ---- 导出任务勾选记忆 ----
    @staticmethod
    def load_export_selected_tasks():
        """读取导出抽屉勾选任务 id 列表；从未保存过时返回 None。

        返回 None 表示没有可用的记忆（首次使用或历史数据损坏），
        由调用方决定默认勾选；空列表是有效的记忆（用户全部取消过）。
        """
        try:
            cfg = ConfigIO.load_section("export", None)
            if not isinstance(cfg, dict) or "selected_tasks" not in cfg:
                return None
            tasks = cfg.get("selected_tasks")
            if (not isinstance(tasks, list)
                    or not all(isinstance(item, str) for item in tasks)):
                return None
            return tasks
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            return None

    @staticmethod
    def save_export_selected_tasks(task_ids):
        """保存导出抽屉勾选任务 id 列表，保留 export 分区中的其他字段。"""
        try:
            cfg = ConfigIO.load_section("export", None)
            if not isinstance(cfg, dict):
                cfg = {}
            cfg["selected_tasks"] = [str(item) for item in (task_ids or [])]
            return ConfigIO.save_section("export", cfg)
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            return False

    # ---- 组合图布局调优 ----
    LAYOUT_KEYS = ["margin_left", "margin_right", "margin_top",
                   "margin_bottom", "gap_h", "gap_v", "cbar_reserve"]

    @staticmethod
    def load_combo_layout_config(main_window):
        """从 combo_layout_config.json 加载布局覆盖参数。"""
        try:
            cfg = ConfigIO.load_section("combo_layout", {}, (COMBO_LAYOUT_CFG,))
            ovr = {}
            for k in ConfigIO.LAYOUT_KEYS:
                v = cfg.get(k)
                if isinstance(v, (int, float)):
                    ovr[k] = v
            main_window._layout_ovr = ovr
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            pass

    @staticmethod
    def save_combo_layout_config(main_window, ovr):
        """将布局参数保存到 combo_layout_config.json。"""
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            defaults = _combo_layout_defaults(main_window)
            ConfigIO.save_section("combo_layout", {k: int(ovr.get(k, defaults[k]))
                           for k in ConfigIO.LAYOUT_KEYS})
        except Exception as exc:
            if hasattr(main_window, "statusBar"):
                main_window.statusBar().showMessage(
                    f"组合图布局保存失败：{exc}", 5000)

    # ---- 网络服务配置（TCP / HTTP / 配对码）----
    _NETWORK_SERVICE_DEFAULTS = {
        "tcp_enabled": True,
        "http_enabled": False,
        "pairing_enabled": False,
        "tcp_port": 9527,
        "http_port": 8080,
    }

    # ---- 概览 ----
    @staticmethod
    def load_overview_config(main_window):
        try:
            cfg = ConfigIO.load_section("overview", {}, (OVERVIEW_CONFIG_FILE,))
            wf = cfg.get("win_front")
            if isinstance(wf, (list, tuple)) and len(wf) == 3:
                main_window.win_front = [float(x) for x in wf]
            main_window.show_window_stats = bool(
                cfg.get("show_window_stats", True))
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            main_window.show_window_stats = True

    @staticmethod
    def save_overview_config(main_window):
        try:
            return ConfigIO.save_section("overview", {
                "win_front": list(main_window.win_front),
                "show_window_stats": bool(
                    getattr(main_window, "show_window_stats", True)),
            })
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            return False

    # ---- 温升三阶段自适应分析 ----
    @staticmethod
    def load_rise_config(main_window):
        """读取新的相对趋势参数，不读取旧统计阈值。"""
        try:
            from utils.core import RiseAnalysisConfig
            cfg = ConfigIO.load_section("rise", {})
            main_window.rise_config = RiseAnalysisConfig(
                filter_window_sec=cfg.get("filter_window_sec", 60.0),
                steady_duration_sec=cfg.get("steady_duration_sec", 300.0),
                slope_decay_sensitivity=cfg.get(
                    "slope_decay_sensitivity", 0.50),
            ).normalized()
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            from utils.core import RiseAnalysisConfig
            main_window.rise_config = RiseAnalysisConfig()

    @staticmethod
    def save_rise_config(main_window):
        """只保存新的三个相对趋势参数。"""
        try:
            config = main_window.rise_config.normalized()
            return ConfigIO.save_section("rise", {
                "filter_window_sec": config.filter_window_sec,
                "steady_duration_sec": config.steady_duration_sec,
                "slope_decay_sensitivity": config.slope_decay_sensitivity,
            })
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            return None

    # ---- 温度报警（全局阈值 + 动作开关 + Modbus 输出参数）----
    @staticmethod
    def load_alarm_config(main_window):
        """读取报警配置。

        旧 settings.json 无 alarm 段时，全部字段取 AlarmConfig 默认值，
        天然兼容；字段缺失时由 cfg.get(key, default) 补齐。
        """
        try:
            from utils.core import AlarmConfig
            cfg = ConfigIO.load_section("alarm", {})
            main_window.alarm_config = AlarmConfig(
                enabled=cfg.get("enabled", True),
                temp_high=cfg.get("temp_high", 100.0),
                follow_axis=cfg.get("follow_axis", True),
                follow_offset=cfg.get("follow_offset", 2.0),
                rate_threshold=cfg.get("rate_threshold", 10.0),
                diff_threshold=cfg.get("diff_threshold", 20.0),
                act_highlight=cfg.get("act_highlight", True),
                act_sound=cfg.get("act_sound", True),
                act_popup=cfg.get("act_popup", True),
                act_log=cfg.get("act_log", True),
                act_serial=cfg.get("act_serial", False),
                serial_port=cfg.get("serial_port", ""),
                serial_baud=cfg.get("serial_baud", 9600),
                modbus_slave=cfg.get("modbus_slave", 1),
                modbus_coil=cfg.get("modbus_coil", 0),
                sound_file=cfg.get("sound_file", ""),
                clear_margin=cfg.get("clear_margin", 1.0),
                clear_hold_sec=cfg.get("clear_hold_sec", 3.0),
            ).normalized()
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            from utils.core import AlarmConfig
            main_window.alarm_config = AlarmConfig()

    @staticmethod
    def save_alarm_config(main_window):
        """保存报警配置到 settings.json 的 alarm 分区。"""
        try:
            config = main_window.alarm_config.normalized()
            return ConfigIO.save_section("alarm", {
                "enabled": config.enabled,
                "temp_high": config.temp_high,
                "follow_axis": config.follow_axis,
                "follow_offset": config.follow_offset,
                "rate_threshold": config.rate_threshold,
                "diff_threshold": config.diff_threshold,
                "act_highlight": config.act_highlight,
                "act_sound": config.act_sound,
                "act_popup": config.act_popup,
                "act_log": config.act_log,
                "act_serial": config.act_serial,
                "serial_port": config.serial_port,
                "serial_baud": config.serial_baud,
                "modbus_slave": config.modbus_slave,
                "modbus_coil": config.modbus_coil,
                "sound_file": config.sound_file,
                "clear_margin": config.clear_margin,
                "clear_hold_sec": config.clear_hold_sec,
            })
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            return None

    # ---- 网络服务配置（服务端：TCP / HTTP / 配对码开关与端口，常驻运行）----
    NETWORK_SERVICE_DEFAULTS = {
        "tcp_enabled": True,      # 向后兼容：旧版本采集时默认只启动 TCP
        "tcp_port": 9527,
        "http_enabled": False,
        "http_port": 8080,
        "pairing_enabled": False,
        "advertise_ip": "",       # 对外通告网卡 IP；空 = 自动选择默认路由出口
    }

    @staticmethod
    def load_network_service_config():
        """读取网络服务配置（勾选开关 + 端口），缺失字段用默认值补齐。"""
        cfg = ConfigIO.load_section("network_service", {}, ())
        merged = dict(ConfigIO.NETWORK_SERVICE_DEFAULTS)
        if isinstance(cfg, dict):
            for k in ConfigIO.NETWORK_SERVICE_DEFAULTS:
                v = cfg.get(k)
                if v is not None:
                    merged[k] = v
        return merged

    @staticmethod
    def save_network_service_config(config):
        """保存网络服务配置到 settings.json 的 network_service 分区。"""
        try:
            out = {}
            for k in ConfigIO.NETWORK_SERVICE_DEFAULTS:
                out[k] = config.get(k, ConfigIO.NETWORK_SERVICE_DEFAULTS[k])
            return ConfigIO.save_section("network_service", out)
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            return False

    # ---- 轴 ----
    @staticmethod
    def load_axis_config(main_window):
        defaults = {
            "ax_time_mode": "auto",
            "ax_time_min": 0.0,
            "ax_time_max": 60.0,
            "ax_time_step": 5.0,
            "ax_temp_base_lo": 20.0,
            "ax_temp_base_hi": 40.0,
            "ax_temp_lo_factor": 0.90,
            "ax_temp_hi_factor": 1.30,
            "ax_live_window_sec": LIVE_WINDOW_SEC_DEFAULT,
            "ax_live_window_min": LIVE_WINDOW_MIN_DEFAULT,
            "ax_dual_view_enabled": False,  # 2026-09-11 默认改不启动双区图（用户拍板）
        }
        try:
            cfg = ConfigIO.load_section("axis", {}, (AXIS_CONFIG_FILE,))
            if not isinstance(cfg, dict):
                cfg = {}

            values = {
                "ax_time_mode": cfg.get("time_mode", defaults["ax_time_mode"]),
                "ax_time_min": cfg.get("time_min", defaults["ax_time_min"]),
                "ax_time_max": cfg.get("time_max", defaults["ax_time_max"]),
                "ax_time_step": cfg.get("time_step", defaults["ax_time_step"]),
            }
            # 温度轴统一智能模式：基础窗口 + 越界扩展系数。
            # 旧字段 temp_mode / temp_min / temp_max / temp_step 已废弃，加载时忽略。
            values.update({
                "ax_temp_base_lo": cfg.get(
                    "temp_base_lo", defaults["ax_temp_base_lo"]),
                "ax_temp_base_hi": cfg.get(
                    "temp_base_hi", defaults["ax_temp_base_hi"]),
                "ax_temp_lo_factor": cfg.get(
                    "temp_lo_factor", defaults["ax_temp_lo_factor"]),
                "ax_temp_hi_factor": cfg.get(
                    "temp_hi_factor", defaults["ax_temp_hi_factor"]),
            })

            # 新契约以秒为单位；旧分钟键只在新键缺失时参与迁移。
            if "live_window_sec" in cfg:
                live_window_sec = _normalize_live_window_sec(
                    cfg.get("live_window_sec"))
                # 旧调用方仍可能只读写分钟影子属性。若配置同时存在旧键，
                # 影子值沿用旧键；否则使用旧 UI 的默认影子值。
                live_window_min = _normalize_live_window_min(
                    cfg.get("live_window_min", defaults["ax_live_window_min"]))
            else:
                legacy = cfg.get("live_window_min", None)
                live_window_sec = _normalize_live_window_sec(
                    legacy if "live_window_min" in cfg else
                    defaults["ax_live_window_sec"],
                    legacy_minutes=("live_window_min" in cfg))
                # 旧属性仅作为尚未迁移调用方的兼容影子。
                live_window_min = _normalize_live_window_min(
                    legacy if "live_window_min" in cfg
                    else defaults["ax_live_window_min"])

            values.update({
                "ax_live_window_sec": live_window_sec,
                "ax_live_window_min": live_window_min,
                "ax_dual_view_enabled": bool(
                    cfg.get("dual_view_enabled",
                           defaults["ax_dual_view_enabled"])),
            })
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            values = defaults

        for name, value in values.items():
            setattr(main_window, name, value)

        # 保存时用快照识别旧调用方对分钟影子属性的改写，避免新属性
        # 的存在把旧 UI 的修改静默遮蔽。
        main_window._axis_live_window_sec_snapshot = (
            values["ax_live_window_sec"])
        main_window._axis_live_window_min_snapshot = (
            values["ax_live_window_min"])

    @staticmethod
    def save_axis_config(main_window):
        try:
            existing = ConfigIO.load_section("axis", {}, (AXIS_CONFIG_FILE,))
            axis = dict(existing) if isinstance(existing, dict) else {}
            has_seconds = hasattr(main_window, "ax_live_window_sec")
            has_minutes = hasattr(main_window, "ax_live_window_min")
            if has_seconds:
                live_window_sec = _normalize_live_window_sec(
                    getattr(main_window, "ax_live_window_sec"))
                # 旧 UI 仍可能修改分钟影子属性。只有当秒属性未改、且
                # 分钟属性相对加载快照发生变化时，才采用分钟属性的改写。
                min_value = getattr(main_window, "ax_live_window_min", None)
                sec_snapshot = getattr(
                    main_window, "_axis_live_window_sec_snapshot", None)
                min_snapshot = getattr(
                    main_window, "_axis_live_window_min_snapshot", None)
                min_changed = (has_minutes and min_snapshot is not None
                               and min_value != min_snapshot)
                sec_changed = (sec_snapshot is not None
                               and main_window.ax_live_window_sec
                               != sec_snapshot)
                if min_changed and not sec_changed:
                    live_window_sec = _normalize_live_window_sec(
                        min_value, legacy_minutes=True)
            else:
                # 兼容没有秒属性的旧调用方：旧属性按分钟输入，落盘仍只写秒键。
                # 保存后的分钟影子值可能已被归一化；未被用户改写时沿用
                # 上次实际秒值，避免把影子值再次当作分钟输入迁移。
                min_value = getattr(main_window, "ax_live_window_min",
                                    LIVE_WINDOW_MIN_DEFAULT)
                sec_snapshot = getattr(
                    main_window, "_axis_live_window_sec_snapshot", None)
                min_snapshot = getattr(
                    main_window, "_axis_live_window_min_snapshot", None)
                if (has_minutes and sec_snapshot is not None
                        and min_value == min_snapshot):
                    live_window_sec = _normalize_live_window_sec(sec_snapshot)
                else:
                    live_window_sec = _normalize_live_window_sec(
                        min_value, legacy_minutes=True)
            axis.update({
                "time_mode": main_window.ax_time_mode,
                "time_min": main_window.ax_time_min,
                "time_max": main_window.ax_time_max,
                "time_step": main_window.ax_time_step,
                "temp_base_lo": main_window.ax_temp_base_lo,
                "temp_base_hi": main_window.ax_temp_base_hi,
                "temp_lo_factor": main_window.ax_temp_lo_factor,
                "temp_hi_factor": main_window.ax_temp_hi_factor,
                "dual_view_enabled": getattr(
                    main_window, "ax_dual_view_enabled", True),
                "live_window_sec": live_window_sec,
            })
            # 旧键只允许作为读取迁移输入，不能继续写回统一配置。
            axis.pop("live_window_min", None)
            result = ConfigIO.save_section("axis", axis)
            if result:
                # 记录并回写实际落盘的有效值，避免下一次无关保存
                # 重新按旧的输入属性触发迁移前状态。
                if has_seconds:
                    main_window.ax_live_window_sec = live_window_sec
                if has_minutes:
                    main_window.ax_live_window_min = _normalize_live_window_min(
                        getattr(main_window, "ax_live_window_min", None))
                main_window._axis_live_window_sec_snapshot = live_window_sec
                main_window._axis_live_window_min_snapshot = (
                    getattr(main_window, "ax_live_window_min", None))
            return result
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            return False

    # ---- 监控悬浮卡外观 ----
    @staticmethod
    def load_live_monitor_config():
        """读取并归一化监控悬浮卡外观配置（缺失/损坏回退默认）。"""
        try:
            cfg = ConfigIO.load_section("live_monitor", {}, ())
            return _normalize_live_monitor_config(cfg)
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            return dict(LIVE_MONITOR_DEFAULTS)

    @staticmethod
    def save_live_monitor_config(config):
        """保存监控悬浮卡外观配置（归一化后落盘）。"""
        try:
            return ConfigIO.save_section(
                "live_monitor", _normalize_live_monitor_config(config))
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            return False

    # ---- A4 ----
    @staticmethod
    def load_a4_config(main_window):
        try:
            cfg = ConfigIO.load_section("a4", {}, (A4_CONFIG_FILE,))
            main_window.a4_custom1 = tuple(cfg.get("custom1", (0.0, 5.0)))
            main_window.a4_custom2 = tuple(cfg.get("custom2", (0.0, 10.0)))
            main_window.a4_mark = cfg.get("mark", True)
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            pass

    @staticmethod
    def save_a4_config(main_window):
        try:
            ConfigIO.save_section("a4", {
                    "custom1": list(main_window.a4_custom1),
                    "custom2": list(main_window.a4_custom2),
                    "mark": main_window.a4_mark,
                })
        except Exception as e:
            print(f"[CONFIG] 配置操作异常: {e}", flush=True)
            pass


def _combo_layout_defaults(main_window):
    """获取组合图布局参数的默认值（从 main_window 类常量读取）。"""
    renderer = getattr(main_window, "chart_renderer", None)
    source = renderer if renderer is not None else main_window
    return {
        "margin_left":   source.COMBO_MARGIN_LEFT,
        "margin_right":  source.COMBO_MARGIN_RIGHT,
        "margin_top":    source.COMBO_MARGIN_TOP,
        "margin_bottom": source.COMBO_MARGIN_BOTTOM,
        "gap_h":         source.COMBO_GAP_H,
        "gap_v":         source.COMBO_GAP_V,
        "cbar_reserve":  80,
    }
