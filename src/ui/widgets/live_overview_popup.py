# -*- coding: utf-8 -*-
"""桌面常驻悬浮球 + 无边框实时趋势弹窗（分层卡片 · 刚体停靠 · 时间窗口滑块）。

交互：悬浮球是锚点，趋势弹窗像抽屉一样从球的外侧滑出，二者是刚体组合——
单击球滑出/收起；拖球弹窗跟随；拖弹窗球同动；拖弹窗四角/四边缩放（Windows
经 WM_NCHITTEST 由系统接管八向缩放，圆角与光标均正确；非 Windows 走手动命中
热区）；贴屏幕边自动翻边、绝不越界。顶部时间窗口滑块（5~120 秒）决定「看最近
多少秒」，缩放窗口只改清晰度不改秒数；曲线始终跟随最新时刻滚动。

模态让位：悬浮窗/球都是置顶窗，可能正好盖住居中弹出的模态框（如主窗口的
「退出确认」），而模态期间同应用其它窗口无法操作、也无法拖动悬浮窗 → 弹窗启动
200ms 轮询监听应用模态：存在活动模态自动隐藏让位（suspend_for_modal），模态
结束按原状态恢复（restore_after_modal）；主窗口退出确认也会在弹确认框前主动
让位，保证「确认退出」永远可点。

卡片结构（分层，已理清）
================================
弹窗本体是无边框透明窗口，paintEvent 手绘最外层「卡体边框层」（圆角矩形 + 独立
描边色），内部内容区由三段组成，各层同属一个色系但层级分明：

1. 卡体外层（paintEvent 绘制）
   - 底边色 = ``card_color``（用户配置的唯一底色调，三层均由其派生）。
   - 描边色 = 由 card_color 压暗/提亮得到的 *独立* 边框色，与画布 band 区分，
     不再像旧版那样边框与画布融合成单一颜色。
   - 四角绘制 L 形握把提示，指示可拖拽缩放。

2. 顶栏 band（self.header）
   - 背景 = card_color 轻微压暗/提亮的「顶栏 band」，与卡体微差以体现分层；
   - 承载顶部时间窗口 *进度条/滑块*、时间窗口文字（「最近 N 秒」）与关闭按钮。
   - 滑块槽/已填段/握把全部用 card_color 同色系派生色（不再用灰色 rgba），
     整卡透明度统一由窗口 opacity 施加，故进度条、边框、卡体外观融合一致。

3. 画布 band（self.chart_frame + matplotlib 坐标轴）
   - 背景 = 由 card_color 派生的「画布 band」色（明显区别于卡体与边框），
     作为温度轴标题、刻度与时间刻度的 *统一背景色*（这是用户要求的单一底色）。
   - matplotlib figure / axes 的 facecolor 均设为此 band 色，轴标题、刻度
     标签、时间刻度因此落在同一背景上，视觉协调统一。

透明度策略：整卡（顶栏 band / 边框 / 画布 band / 进度条）共用同一个
``card_alpha``（经窗口 setWindowOpacity 统一施加），保证各部件透明度完全一致；
各层只通过「同色系的不同明暗」来分层，避免旧版那种异色 rgba 造成的透明割裂。

``card_alpha`` 取值覆盖 **0.0（全透明）~ 1.0（完全不透明）** 全区间：旧版把它
硬钳在 0.30~0.95，导致输入「100%」被压成 95%、「0%」被抬成 30%，表现为
「透明度设置不生效」。现在只在解析阶段做范围校验与提示，不再压缩可用档位。
设置页输入接受 ``100`` / ``"100%"`` / ``60`` / ``"60%"`` 等写法，
解析函数 ``parse_percent`` / ``clamp_alpha`` 收在 ``utils.config_io``（纯函数）。

趋势图配色（折线 / 填充）
================================
趋势色由 ``line_color`` 决定，遵循「自定义优先于主题」：

- 默认 ``line_color == ""``（跟随主题）：各通道沿用自身通道色（保持既有多通道
  辨识度），无通道色时回落到当前主题强调色 ``Theme.ACCENT``；
- 自定义 ``line_color`` 后：全部折线与填充统一使用该色（自定义优先级最高）；
- 「恢复默认（跟随主题）」即把 ``line_color`` 置回空串。

主题切换时 ``MainWindow._apply_theme_chain`` 会先 ``Theme.activate`` 再调用本类的
``apply_theme``，因此跟随主题的趋势色、填充色与刻度配色都会自动同步刷新。

趋势图刻度线
================================
- Y 轴：按当前温度跨度自适应挑选整齐间隔（1/2/2.5/5/10 × 10^n），输出温度刻度
  线 + 数值标签，单位 °C 由轴标题「温度 (°C)」承载；
- X 轴：按时间窗口跨度自适应挑选时间刻度间隔（1/2/5/10/15/20/30/60… 秒），
  输出时间刻度线 + 时:分:秒 标签；
- 刻度线与网格一律用「文字色 + 透明度」绘制而非画布底色自混，保证在浅色 band
  与深色 band 下都有足够对比度。

所有参数持久化于 ``live_monitor`` 配置段，可在「设置 · 悬浮监控」调节。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import math
import os
import sys
import time

os.environ.setdefault("QT_API", "pyqt5")
import matplotlib
matplotlib.use("Qt5Agg")

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QLineF,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QMouseEvent, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import Theme
from ui.widgets.eco_cabin import (
    EcoCabinPainter, EcoCabinView, aggregate)
from chart.series_sampler import SeriesSampler
from utils.config_io import (
    CARD_ALPHA_MAX,
    CARD_ALPHA_MIN,
    ConfigIO,
    clamp_alpha,
    parse_percent,
)

TEXT_ON_LIGHT = "#1f2a37"   # 浅底上的深字
TEXT_ON_DARK = "#f2f5f7"    # 深底上的白字

CARD_COLOR_DEFAULT = "#e8ecef"   # 悬浮卡唯一底色调（边框/卡体/画布 band 均由其派生分层）
CARD_ALPHA_DEFAULT = 0.60        # 整卡不透明度（统一施加于窗口）

# 趋势线/填充色：空串表示「跟随当前主题强调色 Theme.ACCENT」，
# 非空则为用户自定义色（优先级高于主题色，恢复默认即置回空串）。
LINE_COLOR_DEFAULT = ""

# 不透明度允许区间直接复用配置契约常量，避免两处阈值漂移。
# parse_percent / clamp_alpha 为纯函数，统一收在 utils.config_io（避免
# settings_dialog 反向 import 本图表模块连带初始化 matplotlib 后端）。
ALPHA_MIN = CARD_ALPHA_MIN
ALPHA_MAX = CARD_ALPHA_MAX


def max_temp_level(temp, warn_pct, high):
    """悬浮球球心最高温的警示档位（纯函数，便于单测与主窗复用）。

    - temp >= high                → "alarm"（超过上限，红）
    - temp >= high × warn_pct/100 → "warn"（接近上限，琥珀；warn_pct<=0 时禁用本档）
    - 其余 / high<=0（无上限）     → "normal"
    temp 为 None 时返回 None（无数据显示）。
    """
    if temp is None:
        return None
    if high is None or high <= 0:
        return "normal"
    if temp >= float(high):
        return "alarm"
    if warn_pct and warn_pct > 0 and \
            temp >= float(high) * float(warn_pct) / 100.0:
        return "warn"
    return "normal"


def _nice_step(span, target_ticks):
    """按数据跨度挑一个「整齐」的刻度间隔（1/2/2.5/5/10 × 10^n）。"""
    if span <= 0 or target_ticks <= 0:
        return 1.0
    raw = span / float(target_ticks)
    magnitude = 10.0 ** math.floor(math.log10(raw))
    for multiplier in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= multiplier * magnitude:
            return multiplier * magnitude
    return 10.0 * magnitude


def _monotonic() -> float:
    """独立时钟入口，测试可替换以覆盖节流分支。"""
    return time.monotonic()


BADGE_PHASE_TICK_S = 0.16   # 温度牌浮动相位步长（12 相位 ≈ 1.92s 周期）


def badge_float_phase(now_s):
    """温度牌时间驱动浮动相位：160ms/格、12 格回绕。

    旧实现靠 _float_timer 推进，但其启动调用被类内重复定义的 showEvent/hideEvent
    整体遮蔽吞掉，动画从未真正运行过；且给隐藏态球挂周期重绘定时器在 offscreen
    测试环境会与陈旧控件析构冲突（原生 abort）。改为绘制时按单调时钟推导：
    随既有重绘节奏（数据刷新/眨眼/报警抖动）自然呼吸，不新增任何定时器。
    """
    return int(float(now_s) / BADGE_PHASE_TICK_S) % 12


def _hex_rgb(value, default):
    """#rrggbb → (r,g,b) 0~255；非法回退 default。"""
    try:
        s = str(value).strip().lstrip("#")
        if len(s) == 6:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except (TypeError, ValueError):
        pass
    return default


class FloatingBall(QWidget):
    """桌面常驻圆形悬浮按钮：可拖拽、单击/双击分工、拖动时发 moved。

    手势契约（click-double-gesture-2026-09-07）：单击发 :attr:`clicked`
    （切换迷你趋势弹窗）；双击发 :attr:`doubleClicked`（切换全通道面板）。
    双击判定采用「待发 + 二次 release」法：首次松手挂起单击并发一次性
    定时器，定时器到期才真正发 clicked；若定时器窗口内再次松手则取消
    单击改发 doubleClicked。代价是单击有 ``CLICK_DELAY_MS`` 毫秒响应延迟
    （取「不高于系统双击间隔、不迟于 300ms」），换取两手势互不误触。
    """

    clicked = pyqtSignal()
    doubleClicked = pyqtSignal()
    moved = pyqtSignal()
    panelToggleRequested = pyqtSignal()   # 右键菜单「显示/隐藏全通道面板」
    leftButtonClicked = pyqtSignal()      # 宠物左钮：切换全通道面板
    rightButtonClicked = pyqtSignal()     # 宠物右钮：切换迷你趋势图
    expandToggleRequested = pyqtSignal()  # 生态舱单击：切换展开模式（双面板+方向感应）

    DIAMETER = 56
    DRAG_THRESHOLD = 6
    # 单击响应延迟（ms）：窗口内第二次松手=双击；0=立即发单击（测试同步化）。
    # 取值不高于系统双击间隔且封顶 300ms，避免单击手感过钝。
    CLICK_DELAY_MS = 280

    # —— 位置约束契约（bounds-rules-2026-09-04；desktop-drag-2026-09-10 扩围）——
    # docked/free 双态；安全矩形 A = 球心所在屏工作区 S 内缩 SAFE_INSET（桌面自由
    # 拖动：不再受宿主窗口可视区约束，跨屏按球心所在屏自动切换参考系），
    # 球面整圆恒在 A 内（拖动实时夹取，禁裁剪）；松手落在任一锚位 R_snap 内自动吸附。
    # 锚位 = 右上/左上/右下三角 + 屏幕正中 center（默认右下角）。
    MODE_DOCKED = "docked"
    MODE_FREE = "free"
    CORNER_TOP_RIGHT = "top_right"
    CORNER_TOP_LEFT = "top_left"
    CORNER_BOTTOM_RIGHT = "bottom_right"  # 右下角：贴安全矩形 A 右下边并留 CORNER_GAP
    CORNER_CENTER = "center"      # 屏幕正中：球心落在安全矩形 A（=所在屏工作区内缩区）中心
    CORNERS = (CORNER_TOP_RIGHT, CORNER_TOP_LEFT, CORNER_BOTTOM_RIGHT,
               CORNER_CENTER)
    SAFE_INSET = 8            # A 的内缩边距 e（px）
    CORNER_GAP = 8            # 球面与屏幕工作区边缘最小间隙 m（px）
    SNAP_RADIUS_MIN = 48      # R_snap 下限；实际 = max(48, 1.5×d)
    DOCK_ANIM_MS = 150        # 吸附滑入动画时长（与弹窗 ANIM_MS 一致）
    ANIMATE_DOCK = True       # 测试可置 False 跳过滑入动画以保证确定性

    # —— 球心最高温数值直显（max-temp-design-2026-09-04 方案 A）——
    MAXTEMP_NORMAL = "normal"
    MAXTEMP_WARN = "warn"
    MAXTEMP_ALARM = "alarm"
    MAXTEMP_LEVELS = (MAXTEMP_NORMAL, MAXTEMP_WARN, MAXTEMP_ALARM)

    # —— 宠物形象「测测」（pet-ball-design-2026-09-07 方案一：测温小恐龙）——
    # 三态体色/描边/肚皮/墨色/光环；温度显示在头顶光环浮牌（pet-badge-
    # redesign-2026-09-08），肚皮回归纯造型；眨眼与报警抖动为 idle 动画。
    PET_ANIMATE = True        # 测试可置 False：不启动眨眼/抖动定时器
    # 宠物本体缩放/下移（设计稿单位）：顶部让位给光环温度浮牌。
    # paintEvent 的 ppt(x,y) = (x·SCALE, y·SCALE+DY)；按钮命中换算须同式。
    PET_SCALE = 0.88
    PET_DY = 12
    PET_PALETTES = {
        MAXTEMP_NORMAL: {"body": "#7ec850", "stroke": "#5da33f",
                         "belly": "#e8f5d0", "spike": "#5da33f",
                         "ink": "#3d6b2a", "blush": "#ffb3a7",
                         "halo": "#e8d9a0"},
        MAXTEMP_WARN: {"body": "#f5c542", "stroke": "#c79a1e",
                       "belly": "#fdf3d0", "spike": "#c79a1e",
                       "ink": "#8a6a10", "blush": "#f0a35e",
                       "halo": "#f0c060"},
        MAXTEMP_ALARM: {"body": "#e85d5d", "stroke": "#b23a3a",
                        "belly": "#ffe3e0", "spike": "#b23a3a",
                        "ink": "#7a2a2a", "blush": "#ff8d8d",
                        "halo": "#ff9a8a"},
    }
    PET_HALO_ASLEEP = "#93a3b2"   # 打盹光环/星星（灰蓝）
    PET_SWEAT = "#57b8ff"
    # —— 温度联动微反应（pet-react-2026-09-08）——
    # 非 alarm 档且较上次变化 ≥ REACTION_MIN_DELTA 时触发：升温「惊讶」
    # （瞳孔骤大）/降温「舒适」（眯眼笑），持续 REACTION_MS 后自愈；
    # alarm 档有持续抖动+惊恐大瞳，不再叠加（吓呆设定，目光也冻结）。
    REACTION_MIN_DELTA = 0.3   # °C：触发微反应的最小温度变化量
    REACTION_MS = 450          # 微反应持续时长（>500ms 刷新周期的量级内自愈）
    # 两枚功能按钮：左=全通道面板（📋）、右=迷你趋势图（📈）（固定像素半径，保证小尺寸可点）
    # 归一化中心为「宠物本体设计稿坐标」（×100），绘制/命中均经 PET_SCALE/PET_DY 变换
    PET_BTN_RADIUS = 11
    PET_BTN_HIT_RADIUS = 14
    PET_BTN_LEFT_CX = 0.22     # 归一化中心 X（× 直径，再经本体变换）
    PET_BTN_RIGHT_CX = 0.78
    PET_BTN_CY = 0.80          # 归一化中心 Y（× 直径；0.80 保证小直径不裁底）

    # —— 生态舱形象（pet_style=cabin；规格 §5/§6）——
    CABIN_TICK_MS = 90         # 均衡器插值/呼吸相位 tick（仅 cabin+可见+动画）
    CABIN_CELEBR_TICKS = 20    # 庆祝长度（tick 数，×90ms ≈ 1.8s 对齐恐龙规格）
    CABIN_LOWPAD = 20.0        # 环形填充的下限温度 ℃（填满=到达报警线）

    def __init__(self, parent=None):
        super().__init__(None)
        cfg = ConfigIO.load_live_monitor_config()
        self._diameter = int(cfg.get("ball_size", self.DIAMETER))
        self.setObjectName("liveMonitorBall")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)   # 宠物按钮 hover 需要无按键移动事件
        self.setCursor(Qt.OpenHandCursor)
        self.setFixedSize(self._diameter, self._diameter)
        self.setWindowOpacity(float(cfg.get("ball_alpha", 1.0)))

        self._dragging = False
        self._moved = False
        self._press_global = QPoint()
        self._drag_offset = QPoint()
        self._click_pending = False   # 单击待发（双击判定窗口内为 True）
        self._active = False
        # —— 宠物「测测」状态 ——
        self._pet_btn_hover = None    # "left"/"right"/None
        self._pet_btn_pressed = None
        self._eyes_closed = False     # 眨眼帧
        self._reaction = None         # 温度微反应："startle"/"cozy"/None
        self._last_react_temp = None  # 上次反应判定的温度基准
        self._look = QPointF(0.0, 0.0)   # 悬停目光朝向（归一化 ±1）
        self._jitter_phase = 0        # 报警抖动相位
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.setInterval(3000)
        self._blink_timer.timeout.connect(self._do_blink)
        self._jitter_timer = QTimer(self)
        self._jitter_timer.setInterval(110)
        self._jitter_timer.timeout.connect(self._on_jitter_tick)
        # —— 生态舱形象状态（pet_style=cabin 生效；恐龙分支零感知）——
        # ⚠️ _cabin_painter/_cabin_tick_timer 惰性创建（_cabin_tools）：dino 默认
        # 模式下对象图与旧版完全一致。实测给每个球常驻多余 QObject 子对象会改变
        # 泄漏控件的 GC 时序，把 offscreen 存量段错误轮盘赌从 ~1/3 放大到 ~100%。
        self._pet_style = "dino"
        self._cabin_painter = None
        self._cabin_tick_timer = None
        self._cabin_levels = ()          # 每通道档位（主窗判定，含迟滞）
        self._cabin_flags = ()           # 每通道波动标记
        self._cabin_temps = ()           # 通道温度（真值，供圆心温度文本）
        self._cabin_fill = 0.0           # 液面填充比例显示值（0..1，插值）
        self._cabin_fill_target = 0.0    # 目标比例（最高温/报警线）
        self._cabin_temp_text = ""       # 圆心温度文本
        self._cabin_paused = False       # 采集暂停标志（与 _max_temp None 并列）
        self._cabin_phase = 0
        self._celebr_ticks = 0
        self._apply_pet_tooltip()
        # —— 位置约束与状态（bounds）——
        self._host = None                     # 宿主主窗口（提供可视区参考），由 MainWindow 注入
        self._mode = cfg.get("ball_mode", self.MODE_DOCKED)
        if self._mode not in (self.MODE_DOCKED, self.MODE_FREE):
            self._mode = self.MODE_DOCKED
        self._corner = cfg.get("ball_corner", self.CORNER_BOTTOM_RIGHT)
        if self._corner not in self.CORNERS:
            self._corner = self.CORNER_BOTTOM_RIGHT
        self._anim = None                     # 吸附滑入动画
        # —— 球心最高温状态（Feature2：数值直显）——
        self._max_temp = None                 # float | None；None = 打盹模式
        self._max_level = self.MAXTEMP_NORMAL
        self._max_source = ""                 # 最高温来源通道显示名（tooltip）
        self.apply_theme()
    # ------------------------------------------------------------------
    #  球心最高温数值直显（max-temp 方案 A）
    # ------------------------------------------------------------------
    def set_max_temp(self, temp, level=MAXTEMP_NORMAL, source=""):
        """设置肚皮温度屏数值与警示档位；temp=None 回到打盹模式。

        level ∈ normal/warn/alarm（由主窗用 max_temp_level 判定），驱动
        恐龙体色/表情/动画三态联动。仅状态变化才触发重绘，避免高频空刷。
        """
        if temp is None:
            if self._max_temp is not None:
                self._max_temp = None
                self._max_level = self.MAXTEMP_NORMAL
                self._max_source = ""
                self._reaction = None
                self._last_react_temp = None
                self._update_max_temp_colors()
                self.update()
            return
        temp = float(temp)
        if level not in self.MAXTEMP_LEVELS:
            level = self.MAXTEMP_NORMAL
        changed = (temp != self._max_temp
                   or level != self._max_level
                   or str(source) != self._max_source)
        self._max_temp = temp
        self._max_level = level
        self._max_source = str(source)
        if changed:
            self._update_max_temp_colors()
            self._maybe_react(temp)
            self.update()

    def _maybe_react(self, temp):
        """温度联动微反应：升温「惊讶」/降温「舒适」（pet-react-2026-09-08）。

        触发条件：非 alarm 档（吓呆不叠加）、当前无进行中反应（不重触发）、
        有上次基准且变化 ≥ REACTION_MIN_DELTA。反应态由定时器自愈；
        PET_ANIMATE=False（测试）不启动定时，状态留驻便于断言。
        """
        if (self._max_level != self.MAXTEMP_ALARM
                and self._reaction is None
                and self._last_react_temp is not None
                and abs(temp - self._last_react_temp) >= self.REACTION_MIN_DELTA):
            self._reaction = ("startle" if temp > self._last_react_temp
                              else "cozy")
            if self.PET_ANIMATE:
                QTimer.singleShot(self.REACTION_MS, self._clear_reaction)
        self._last_react_temp = temp

    def _clear_reaction(self):
        """微反应到期自愈（QTimer.singleShot 回调）。"""
        if self._reaction is not None:
            self._reaction = None
            self.update()

    def clear_max_temp(self) -> None:
        """回到打盹模式（非实时采集 / 停采后调用）。"""
        self.set_max_temp(None)

    def _update_max_temp_colors(self):
        """档位变化后刷新体色相关状态并联动 idle 动画（恐龙不维护纯色底）。"""
        self._update_jitter_timer()
        self._cabin_update_tick_timer()
        if (self._max_temp is not None and self.PET_ANIMATE
                and not self._blink_timer.isActive()):
            self._start_blink_timer()

    # ------------------------------------------------------------------
    #  生态舱形象（pet_style=cabin）：数据入口 / 快照组装 / 动画 tick
    # ------------------------------------------------------------------
    def set_pet_style(self, style):
        """切换桌宠形象（dino|cabin）。非法值回退 dino；不重置采集状态。"""
        style = style if style in ("dino", "cabin") else "dino"
        if style == self._pet_style:
            return
        self._pet_style = style
        self._apply_pet_tooltip()
        if style == "cabin":
            self._cabin_tools()
        else:
            # 恐龙分支不消费舱数据；清空避免切回舱时闪现陈旧帧
            self._cabin_temps = ()
            self._cabin_levels = ()
            self._cabin_flags = ()
            self._cabin_fill = 0.0
            self._cabin_temp_text = ""
        self._cabin_update_tick_timer()
        self.update()

    def pet_style(self):
        return self._pet_style

    def _apply_pet_tooltip(self):
        """悬停提示按形象给出对应手势说明（cabin 无球面按钮）。"""
        if self._pet_style == "cabin":
            self.setToolTip("单击：开/关组合悬浮窗（全通道面板 + 迷你趋势）\n"
                            "拖动：移动位置｜右键：更多操作")
        else:
            self.setToolTip("左钮 📋：全通道面板｜右钮 📈：迷你趋势图\n"
                            "拖动：移动位置｜右键：更多操作")

    def _cabin_tools(self):
        """惰性创建舱绘制器与均衡器 tick（仅 cabin 首次使用时）。

        dino 默认模式下球对象图与旧版严格一致：实测常驻额外 QObject 子对象
        会放大 offscreen 存量段错误轮盘赌（见 __init__ 注释）。
        """
        if self._cabin_painter is None:
            self._cabin_painter = EcoCabinPainter()
        if self._cabin_tick_timer is None:
            self._cabin_tick_timer = QTimer(self)
            self._cabin_tick_timer.setInterval(self.CABIN_TICK_MS)
            self._cabin_tick_timer.timeout.connect(self._on_cabin_tick)
        return self._cabin_painter

    def set_channels(self, temps, levels, flags, highs, paused=False):
        """生态舱逐通道数据帧（仅 cabin 消费；dino 模式空转安全）。

        temps/levels/flags/highs 由主窗对齐同长度；显示值走均衡器插值
        （PET_ANIMATE=False 测试态直写，保证断言确定性）。
        """
        if self._pet_style != "cabin":
            return
        self._cabin_paused = bool(paused)
        self._cabin_levels = tuple(levels)
        self._cabin_flags = tuple(flags)
        self._cabin_temps = tuple(temps)
        valid_t = [t for t in temps if t is not None]
        valid_h = [h for h in highs if h]
        t_max = max(valid_t) if valid_t else None
        thr = max(valid_h) if valid_h else 0.0
        # 填充比例：最高温相对「报警线=圆填满」（下限 CABIN_LOWPAD）
        self._cabin_fill_target = (
            max(0.0, min(1.0, (t_max - self.CABIN_LOWPAD)
                         / max(1e-6, thr - self.CABIN_LOWPAD)))
            if (t_max is not None and thr > self.CABIN_LOWPAD) else 0.0)
        self._cabin_temp_text = ("%.1f" % t_max) if t_max is not None else ""
        if not self.PET_ANIMATE:
            self._cabin_fill = self._cabin_fill_target
        self._cabin_update_tick_timer()
        self.update()

    def _cabin_view(self):
        """组装本帧绘制快照（测试直接断言此对象）。"""
        paused = self._cabin_paused
        agg = aggregate(self._cabin_levels, self._cabin_flags, paused)
        return EcoCabinView(
            agg=agg, fill=self._cabin_fill,
            temp_text=self._cabin_temp_text,
            paused=paused, blink=self._eyes_closed,
            look=QPointF(self._look.x() * 1.6, self._look.y() * 1.6),
            phase=self._cabin_phase,
            celebrate=(1.0 - self._celebr_ticks / float(self.CABIN_CELEBR_TICKS)
                       if self._celebr_ticks > 0 else -1.0),
            fluct=any(self._cabin_flags))

    def _cabin_update_tick_timer(self):
        """舱动画启停：仅 cabin + 可见 + 动画开 + 有温度数据时跑。"""
        running = (self._pet_style == "cabin" and self.PET_ANIMATE
                   and self.isVisible())
        if running:
            self._cabin_tools()
        timer = self._cabin_tick_timer
        if timer is None:
            return
        if running and not timer.isActive():
            timer.start()
        elif not running and timer.isActive():
            timer.stop()

    def _on_cabin_tick(self):
        """均衡器插值 + 相位推进（舱常驻呼吸，随可见性停表，见启停函数）。"""
        self._cabin_phase = (self._cabin_phase + 1) % 24
        d = self._cabin_fill_target - self._cabin_fill
        if abs(d) > 0.002:
            self._cabin_fill += d * 0.25
        if self._celebr_ticks > 0:
            self._celebr_ticks -= 1
        self.update()

    def trigger_celebrate(self):
        """主窗在「异常→平稳」恢复边沿调用：一次性播放庆祝（dino 模式忽略）。"""
        if self._pet_style != "cabin":
            return
        self._celebr_ticks = self.CABIN_CELEBR_TICKS
        self._cabin_update_tick_timer()
        self.update()

    def _paint_cabin(self, painter):
        """环形温度球绘制（温度数值已入圆心，头顶温度牌取消）。"""
        view = self._cabin_view()
        self._cabin_tools().paint(painter, self._diameter, view)

    # ------------------------------------------------------------------
    #  宿主与安全矩形（bounds）
    # ------------------------------------------------------------------
    def set_host(self, host) -> None:
        """注入宿主主窗口：仅作球心落在屏外死区时的屏幕兜底参考与面板可见性查询。"""
        self._host = host

    def _host_screen(self):
        """宿主当前所在屏幕（球心不在任何屏时的兜底；无宿主取主屏）。"""
        mw = self._host
        if mw is None:
            return QApplication.primaryScreen()
        sc = QApplication.screenAt(mw.frameGeometry().center())
        return sc or QApplication.primaryScreen()

    def safe_rect(self) -> QRect:
        """安全矩形 A = 球心所在屏工作区内缩 e（desktop-drag-2026-09-10）。

        球是桌面级常驻窗：拖动范围=整个桌面工作区（所在屏可用区，避开
        任务栏），不再与宿主窗口可视区相交；球心落在屏外死区（拔屏等）
        时退回宿主所在屏，A 放不下整球时退回未内缩工作区（硬边界兜底）。
        """
        d = self._diameter
        sc = (QApplication.screenAt(self.frameGeometry().center())
              or self._host_screen())
        if sc is None:
            return QRect(0, 0, max(d * 2, 200), max(d * 2, 200))
        base = sc.availableGeometry()
        A = base.adjusted(self.SAFE_INSET, self.SAFE_INSET,
                          -self.SAFE_INSET, -self.SAFE_INSET)
        if A.width() < d or A.height() < d:
            A = base
        return A

    def _snap_radius(self) -> int:
        return max(self.SNAP_RADIUS_MIN, int(self._diameter * 1.5))

    def _corner_topleft(self, corner: str, rect: QRect) -> QPoint:
        """把位置锚偏好换算成球左上角。

        角锚（top_left/top_right/bottom_right）贴屏幕工作区边并留 CORNER_GAP
        边距；center 锚把球心对准 rect（安全矩形 A，=所在屏工作区内缩区）的
        正中心，球心距 A 各边均 ≥ d/2。
        """
        d = self._diameter
        if corner == self.CORNER_CENTER:
            return QPoint(rect.center().x() - d // 2,
                          rect.center().y() - d // 2)
        if corner == self.CORNER_TOP_LEFT:
            return QPoint(rect.left() + self.CORNER_GAP,
                          rect.top() + self.CORNER_GAP)
        if corner == self.CORNER_BOTTOM_RIGHT:
            return QPoint(rect.right() - d - self.CORNER_GAP,
                          rect.bottom() - d - self.CORNER_GAP)
        return QPoint(rect.right() - d - self.CORNER_GAP,
                      rect.top() + self.CORNER_GAP)

    def _corner_center(self, corner: str, rect: QRect):
        tl = self._corner_topleft(corner, rect)
        return QPoint(tl.x() + self._diameter // 2,
                      tl.y() + self._diameter // 2)

    def _clamped_pos(self, target: QPoint) -> QPoint:
        """把目标左上角夹取进安全矩形 A（整球可见，永不越出）。"""
        A = self.safe_rect()
        d = self._diameter
        x = max(A.left(), min(int(target.x()), A.right() - d))
        y = max(A.top(), min(int(target.y()), A.bottom() - d))
        return QPoint(x, y)

    def _dock_move(self, corner: str = None, animate: bool = False) -> None:
        """吸附到指定角（默认当前偏好角）；animate 时播放滑入动画。"""
        A = self.safe_rect()
        tl = self._corner_topleft(corner or self._corner, A)
        if animate and self.ANIMATE_DOCK and self.isVisible():
            self._animate_to(tl)
        else:
            self.move(tl)

    def _animate_to(self, dest: QPoint) -> None:
        if self._anim is not None and \
                self._anim.state() == QAbstractAnimation.Running:
            self._anim.stop()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(self.DOCK_ANIM_MS)
        anim.setStartValue(self.pos())
        anim.setEndValue(dest)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(self._on_dock_anim_finished)
        self._anim = anim
        anim.start()

    def _on_dock_anim_finished(self) -> None:
        self._anim = None

    def _hit_snap_corner(self):
        """松手时球心是否落在某启用吸附角的 R_snap 内；命中返回角名，否则 None。"""
        center = QRect(self.pos(), self.size()).center()
        radius = self._snap_radius()
        A = self.safe_rect()
        best = None
        for corner in self.CORNERS:
            cc = self._corner_center(corner, A)
            dx = center.x() - cc.x()
            dy = center.y() - cc.y()
            dist = math.sqrt(dx * dx + dy * dy)
            if dist <= radius and (best is None or dist < best[0]):
                best = (dist, corner)
        return best[1] if best else None

    def _persist_position(self) -> None:
        """落定后保存当前 mode/corner/pos（位置契约见 config_io）。"""
        try:
            cfg = ConfigIO.load_live_monitor_config()
            cfg["ball_mode"] = self._mode
            cfg["ball_corner"] = self._corner
            p = self.pos()
            cfg["ball_pos_x"] = int(p.x())
            cfg["ball_pos_y"] = int(p.y())
            ConfigIO.save_live_monitor_config(cfg)
        except Exception:
            pass

    # ------------------------------------------------------------------
    #  状态切换（bounds）
    # ------------------------------------------------------------------
    def restore_initial(self) -> None:
        """启动/构造后按持久化偏好放置：docked→角锚；free→原坐标并在越界时回默认角。"""
        if self._mode == self.MODE_DOCKED:
            self._dock_move(self._corner, animate=False)
            return
        cfg = ConfigIO.load_live_monitor_config()
        self.move(QPoint(int(cfg.get("ball_pos_x", 0)),
                         int(cfg.get("ball_pos_y", 0))))
        A = self.safe_rect()
        d = self._diameter
        if A.width() < d or A.height() < d:
            return
        clamped = self._clamped_pos(self.pos())
        if clamped != self.pos():
            # 记忆位已越界（拔屏/分辨率变化）→ 回默认吸附角，绝不让球消失
            self._mode = self.MODE_DOCKED
            self._dock_move(self._corner, animate=False)
        self._persist_position()

    def bring_back_to(self, corner: str) -> None:
        """把球吸附回指定角并持久化（设置页选角 / 右键「吸附到默认角」入口）。"""
        if corner not in self.CORNERS:
            return
        self._corner = corner
        self._mode = self.MODE_DOCKED
        self._dock_move(corner, animate=True)
        self._persist_position()

    def bring_back(self) -> None:
        """兜底找回：吸附到当前偏好角。"""
        self.bring_back_to(self._corner)

    def apply_corner_pref(self, corner: str) -> None:
        """配置热重载同步角偏好：docked 态立即吸附；free 态仅记录不打扰。"""
        if corner not in self.CORNERS:
            return
        changed = corner != self._corner
        self._corner = corner
        if changed and self._mode == self.MODE_DOCKED:
            self._dock_move(corner, animate=True)
            self._persist_position()

    def on_host_geometry_changed(self) -> None:
        """宿主 move/resize 后兜底自校正：docked→重落角锚；free→越界才夹取。

        桌面拖动扩围后球位不再依赖宿主几何，本方法保留为安全网
        （球因拔屏/分辨率变化落到屏外时被拉回宿主所在屏安全区）。
        """
        if self._host is None or not self.isVisible():
            return
        if self._mode == self.MODE_DOCKED:
            self._dock_move(self._corner, animate=False)
        else:
            A = self.safe_rect()
            d = self._diameter
            if A.width() >= d and A.height() >= d:
                clamped = self._clamped_pos(self.pos())
                if clamped != self.pos():
                    self.move(clamped)
                    self._persist_position()

    def set_active(self, active: bool):
        active = bool(active)
        if active != self._active:
            self._active = active
            self.update()

    def set_diameter(self, diameter: int):
        diameter = max(40, min(120, int(diameter)))
        if diameter != self._diameter:
            self._diameter = diameter
            self.setFixedSize(diameter, diameter)
            if self._mode == self.MODE_DOCKED:
                self._dock_move(self._corner, animate=False)
            else:
                A = self.safe_rect()
                if A.width() >= diameter and A.height() >= diameter:
                    self.move(self._clamped_pos(self.pos()))
            self.update()

    def set_ball_alpha(self, alpha: float):
        self.setWindowOpacity(max(0.40, min(1.0, float(alpha))))

    def apply_theme(self):
        self._update_max_temp_colors()
        self.update()

    def paintEvent(self, _event):
        """宠物「测测」绘制：头顶光环温度浮牌 + 背刺 + 身体（三态体色）
        + 纯色肚皮 + 表情 + 两枚功能按钮（左📋全通道面板 / 右📈迷你趋势图）。

        坐标以 100×100 归一化设计稿为基准、按直径缩放；宠物本体经
        PET_SCALE/PET_DY 缩放下移（ppt），顶部让位给光环温度浮牌（pt）。
        报警态叠加低频抖动（_jitter_timer 驱动），无数据态为灰绿闭眼打盹。
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        d = self._diameter
        k = d / 100.0

        def pt(x, y):
            return QPointF(x * k, y * k)

        def ppt(x, y):
            return QPointF(x * self.PET_SCALE * k,
                           (y * self.PET_SCALE + self.PET_DY) * k)

        if self._pet_style == "cabin":
            self._paint_cabin(painter)
            return

        level = self._max_level if self._max_temp is not None else None
        pal = self.PET_PALETTES.get(level, self.PET_PALETTES[self.MAXTEMP_NORMAL])
        asleep = self._max_temp is None

        # 报警抖动偏移（±1px 低频摇晃）
        if (self._max_level == self.MAXTEMP_ALARM and self._max_temp is not None
                and self._jitter_phase % 2):
            # 抖动相位取 ±1 双向对称摇摆（dx、dy 各自独立翻转）
            painter.translate(1.0 if self._jitter_phase % 4 < 2 else -1.0,
                              0.0 if self._jitter_phase % 4 < 2 else -1.0)

        stroke_w = max(2.0, 2.2 * k)

        # —— 头顶光环 + 温度浮牌 + 星星（温度彻底脱离肚皮约束，防截断）——
        self._paint_temp_badge(painter, pt, k, pal, asleep)

        # —— 背刺 ——
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(pal["spike"]))
        spike = QPolygonF([ppt(30, 30), ppt(36, 18), ppt(42, 28),
                           ppt(50, 14), ppt(58, 28), ppt(64, 18), ppt(70, 30),
                           ppt(50, 34)])
        painter.drawPolygon(spike)

        # —— 小手（画在身体下层，从身侧探出）——
        painter.setBrush(QColor(pal["body"]))
        painter.setPen(QPen(QColor(pal["stroke"]), stroke_w))
        painter.drawEllipse(ppt(20, 60), 7 * k, 5 * k)
        painter.drawEllipse(ppt(80, 60), 7 * k, 5 * k)

        # —— 身体 ——
        body_pen = QPen(QColor(pal["stroke"]), stroke_w)
        if self._active:
            body_pen = QPen(QColor(Theme.ACCENT), stroke_w + 0.6)
        painter.setPen(body_pen)
        painter.setBrush(QColor(pal["body"]))
        painter.drawEllipse(ppt(50, 52), 33 * k, 30 * k)

        # —— 肚皮（纯造型，不显示数字）——
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(pal["belly"]))
        painter.drawEllipse(ppt(50, 58), 21 * k, 19 * k)

        # —— 表情 ——
        ink = QColor(pal["ink"])
        eye_r = 8 * k
        pupil_r = 3.6 * k
        if asleep:
            # 闭眼打盹：两条下弯弧
            painter.setPen(QPen(ink, max(1.6, 1.8 * k)))
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(QRectF(ppt(31, 30), ppt(45, 44)), 180 * 16, 180 * 16)
            painter.drawArc(QRectF(ppt(55, 30), ppt(69, 44)), 180 * 16, 180 * 16)
        elif self._eyes_closed and self.PET_ANIMATE:
            painter.setPen(QPen(ink, max(1.8, 2.0 * k)))
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QLineF(ppt(31, 38), ppt(45, 38)))
            painter.drawLine(QLineF(ppt(55, 38), ppt(69, 38)))
        else:
            if self._reaction == "cozy":
                # 降温舒适：眯眼笑（∩ ∩），瞳孔/目光隐藏（pet-react）
                painter.setPen(QPen(ink, max(1.8, 2.0 * k)))
                painter.setBrush(Qt.NoBrush)
                painter.drawArc(QRectF(ppt(31, 30), ppt(45, 44)), 180 * 16, 180 * 16)
                painter.drawArc(QRectF(ppt(55, 30), ppt(69, 44)), 180 * 16, 180 * 16)
            else:
                painter.setPen(QPen(ink, 1.4))
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(ppt(38, 38), eye_r, eye_r)
                painter.drawEllipse(ppt(62, 38), eye_r, eye_r)
                painter.setBrush(QColor("#222222"))
                if self._max_level == self.MAXTEMP_ALARM:
                    pupil_r = 4.6 * k        # 惊恐大瞳
                if self._reaction == "startle":
                    pupil_r = 5.2 * k        # 升温惊讶：瞳孔骤大
                # 悬停目光跟随（归一化 ±1 → 设计稿 ±1.6）；alarm 吓呆/cozy 已上面分支
                lk = self._look * 1.6
                painter.drawEllipse(ppt(40, 40) + lk, pupil_r, pupil_r)
                painter.drawEllipse(ppt(60, 40) + lk, pupil_r, pupil_r)
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(ppt(41.5, 38), 1.2 * k, 1.2 * k)
                painter.drawEllipse(ppt(61.5, 38), 1.2 * k, 1.2 * k)
            if self._max_level == self.MAXTEMP_WARN:
                # 皱眉 + 汗滴
                painter.setPen(QPen(ink, max(1.8, 2.2 * k)))
                painter.drawLine(QLineF(ppt(32, 33), ppt(41, 30)))
                painter.drawLine(QLineF(ppt(68, 33), ppt(59, 30)))
                painter.setPen(QPen(QColor(self.PET_SWEAT), max(1.8, 2.2 * k)))
                painter.setBrush(Qt.NoBrush)
                painter.drawArc(QRectF(ppt(70, 26), ppt(78, 38)), 30 * 16, 140 * 16)
            elif self._max_level == self.MAXTEMP_ALARM:
                # 两侧大汗
                painter.setPen(QPen(QColor(self.PET_SWEAT), max(2.0, 2.4 * k)))
                painter.setBrush(Qt.NoBrush)
                painter.drawArc(QRectF(ppt(22, 24), ppt(32, 40)), 20 * 16, 150 * 16)
                painter.drawArc(QRectF(ppt(68, 24), ppt(78, 40)), 10 * 16, 150 * 16)

        # —— 嘴 ——
        painter.setPen(QPen(ink, max(1.6, 1.8 * k)))
        painter.setBrush(Qt.NoBrush)
        if asleep:
            painter.drawArc(QRectF(ppt(43, 44), ppt(57, 54)), 200 * 16, 140 * 16)
        elif self._max_level == self.MAXTEMP_ALARM:
            painter.setBrush(QColor(ink))
            painter.drawEllipse(ppt(50, 53), 3.4 * k, 4.6 * k)   # 张嘴喘气
        elif self._max_level == self.MAXTEMP_WARN:
            painter.drawArc(QRectF(ppt(43, 47), ppt(57, 57)), 200 * 16, 140 * 16)
        else:
            painter.drawArc(QRectF(ppt(43, 44), ppt(57, 54)), 260 * 16, 140 * 16)

        # —— 腮红 ——
        if not asleep:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(pal["blush"]))
            painter.drawEllipse(ppt(30, 47), 3.5 * k, 2.8 * k)
            painter.drawEllipse(ppt(70, 47), 3.5 * k, 2.8 * k)

        # —— 两枚功能按钮（本体坐标系）——
        for name in ("left", "right"):
            self._paint_pet_button(painter, name, ppt, k)
        painter.end()

    def _pet_button_rect(self, name):
        """功能按钮的局部命中矩形（固定像素半径保证小尺寸可点）。

        中心 = 宠物本体设计稿坐标 ×100 经 PET_SCALE/PET_DY 变换后的
        像素位置（与 paintEvent 的 ppt 完全同式，勿单独改动一侧）。
        """
        d = self._diameter
        cx = d * (self.PET_BTN_LEFT_CX if name == "left"
                  else self.PET_BTN_RIGHT_CX) * self.PET_SCALE
        cy = d * (self.PET_BTN_CY * self.PET_SCALE
                  + self.PET_DY / 100.0)
        r = self.PET_BTN_HIT_RADIUS
        return QRectF(cx - r, cy - r, r * 2, r * 2)

    def _paint_pet_button(self, painter, name, ppt, k):
        """圆钮：白底 + 蓝描边 + 图标（左折线📈 / 右表格📋），hover 加深。

        ppt 为宠物本体坐标变换（与恐龙绘制同一坐标系）。
        """
        cx = (self.PET_BTN_LEFT_CX if name == "left"
              else self.PET_BTN_RIGHT_CX) * 100.0
        cy = self.PET_BTN_CY * 100.0
        center = ppt(cx, cy)
        cpx, cpy = center.x(), center.y()
        # 半径随直径缩放（8px 下限），避免默认档按钮外缘被窗口矩形裁底
        r = max(self.PET_BTN_RADIUS * k, 8.0)
        hovered = self._pet_btn_hover == name
        pressed = self._pet_btn_pressed == name
        fill = QColor("#ffffff")
        if hovered:
            fill = QColor("#e8f0ff" if not pressed else "#d8e6ff")
        painter.setPen(QPen(QColor("#0052d9"), max(1.8, 2.0 * k)))
        painter.setBrush(fill)
        painter.drawEllipse(center, r, r)
        pen = QPen(QColor("#0052d9"), max(1.6, 1.8 * k))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        s = r * 0.55
        if name == "left":      # 表格面板图标（左钮=全通道面板）
            rect = QRectF(cpx - s * 0.8, cpy - s * 0.8, s * 1.6, s * 1.6)
            painter.drawRoundedRect(rect, 2, 2)
            painter.drawLine(QLineF(QPointF(rect.left(), rect.center().y()),
                                    QPointF(rect.right(), rect.center().y())))
            painter.drawLine(QLineF(QPointF(rect.center().x(), rect.top()),
                                    QPointF(rect.center().x(), rect.bottom())))
        else:                   # 折线趋势图标（右钮=迷你趋势图）
            path_pts = [(cpx - s, cpy + s * 0.5), (cpx - s * 0.25, cpy - s * 0.4),
                        (cpx + s * 0.2, cpy + s * 0.1), (cpx + s, cpy - s * 0.6)]
            # 注意：本机 PyQt5 的 drawLine(QPointF, QPointF) 重载原生崩溃，
            # 一律经 QLineF 构造走 drawLine(QLineF) 安全路径
            for i in range(len(path_pts) - 1):
                painter.drawLine(QLineF(QPointF(*path_pts[i]),
                                        QPointF(*path_pts[i + 1])))

    def _badge_temp_text_and_font(self, k):
        """头顶温度浮牌的显示文本与字号。

        胶囊底板 + 大号白字（视觉焦点），文本可用宽 34 设计单位（胶囊
        上限 46 含 pad）；字号 15 设计单位，收缩下限随球径缩放
        （max(6, 8k)——固定像素下限在大球上会缩成尘埃小字），仍放不下
        退化为整数兜底（真机雅黑数字 ≈0.55em，"31.3" 仅 ~29 单位）。
        """
        temp = self._max_temp
        text = (f"{temp:.1f}" if abs(temp) < 100 else f"{temp:.0f}")
        font = QFont(self.font())
        font.setPixelSize(max(8, int(15 * k)))
        font.setBold(True)
        fm = QFontMetrics(font)
        max_w = 34 * k
        floor = max(6, int(8 * k))
        while fm.horizontalAdvance(text) > max_w and font.pixelSize() > floor:
            font.setPixelSize(font.pixelSize() - 1)
            fm = QFontMetrics(font)
        if fm.horizontalAdvance(text) > max_w:
            text = f"{temp:.0f}"
        return text, font

    def _paint_temp_badge(self, painter, pt, k, pal, asleep):
        """头顶悬浮温度牌：光环 + 发光胶囊（大号白字）+ 星星，焦点级突出。

        灵动设计（pet-badge-redesign-2026-09-08，参考用户给的
        狐狸/海豹/白兔/圆球精灵图）：
        - 温度做成视觉焦点：档位 ink 深色胶囊底 + 15 单位大号白字 +
          halo 色发光底晕（呼吸动效）+ 下投影 + halo 细描边；
        - 光环/星星/胶囊整体以 ~2s 周期上下浮动（悬浮呼吸感），
          与身体体色上下呼应（halo 随档位联动）；
        - 打盹时环心浮「z z」，无胶囊。
        温度脱离肚皮后宽度不再受椭圆约束，截断物理上不可能。
        """
        halo = QColor(self.PET_HALO_ASLEEP if asleep else pal["halo"])
        phase = badge_float_phase(_monotonic()) if self.PET_ANIMATE else 0
        w = 2.0 * math.pi * phase / 12.0
        yoff = -1.6 * math.sin(w) * k                        # 悬浮上下浮动
        glow_a = 42 + 28 * (0.5 + 0.5 * math.sin(w + 1.0))   # 光晕呼吸（相位错开）
        painter.save()
        painter.translate(0.0, yoff)
        # —— 光环（扁椭圆环，微倾 -8°） ——
        painter.setPen(QPen(halo, max(1.2, 1.5 * k)))
        painter.setBrush(Qt.NoBrush)
        painter.save()
        painter.translate(pt(50, 12.5))
        painter.rotate(-8)
        painter.drawEllipse(QPointF(0, 0), 24 * k, 5.5 * k)
        painter.restore()
        # —— 星星点缀（菱形四角星，错落三颗） ——
        painter.setPen(Qt.NoPen)
        painter.setBrush(halo)
        for sx, sy, sr in ((22, 17, 2.4), (78, 13, 1.8), (50, 22.5, 1.2)):
            star = QPolygonF([pt(sx, sy - sr), pt(sx + sr * 0.38, sy),
                              pt(sx, sy + sr), pt(sx - sr * 0.38, sy)])
            painter.drawPolygon(star)
        if asleep:
            # 打盹：环心浮 z z（灰蓝），无胶囊
            painter.setPen(QPen(QColor("#aebccb"), 1.6))
            f = QFont(self.font())
            f.setPixelSize(max(7, int(11 * k)))
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(QRectF(pt(37, 5), pt(63, 20)),
                             Qt.AlignCenter, "z z")
            painter.restore()
            return
        # —— 温度胶囊（视觉焦点：发光底晕 + 阴影 + 底板 + 大号白字） ——
        text, font = self._badge_temp_text_and_font(k)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text)
        bh = 17 * k
        bw = max(18 * k, min(46 * k, tw + 12 * k))
        center = pt(50, 12.5)
        half = QPointF(bw / 2.0, bh / 2.0)
        rect = QRectF(center - half, center + half)
        # 发光底晕（halo 色，呼吸）
        glow_half = QPointF(bw / 2.0 + 4 * k, bh / 2.0 + 4 * k)
        glow = QColor(halo)
        glow.setAlpha(int(glow_a))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawRoundedRect(QRectF(center - glow_half, center + glow_half),
                                (bh / 2.0 + 4 * k), (bh / 2.0 + 4 * k))
        # 下投影（柔影）
        shadow = QColor(0, 0, 0, 55)
        painter.setBrush(shadow)
        painter.drawRoundedRect(rect.translated(0.0, 2.2 * k),
                                bh / 2.0, bh / 2.0)
        # 底板 + halo 细描边
        painter.setPen(QPen(halo, max(1.0, 1.2 * k)))
        painter.setBrush(QColor(pal["ink"]))
        painter.drawRoundedRect(rect, bh / 2.0, bh / 2.0)
        painter.setFont(font)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.restore()

    # ------------------------------------------------------------------
    #  宠物 idle 动画（眨眼 / 报警抖动；温度牌呼吸为时间驱动相位）
    # ------------------------------------------------------------------
    def _do_blink(self):
        if not self.PET_ANIMATE or self._max_temp is None:
            self._start_blink_timer()
            return
        self._eyes_closed = True
        self.update()
        QTimer.singleShot(130, self._end_blink)

    def _end_blink(self):
        self._eyes_closed = False
        self.update()
        self._start_blink_timer()

    def _start_blink_timer(self):
        if self.PET_ANIMATE and self.isVisible():
            self._blink_timer.start()

    def _on_jitter_tick(self):
        self._jitter_phase += 1
        self.update()

    def _update_jitter_timer(self):
        """报警态低频抖动（~4.5Hz），离开报警即停。"""
        if (self.PET_ANIMATE and self._max_temp is not None
                and self._max_level == self.MAXTEMP_ALARM and self.isVisible()):
            if not self._jitter_timer.isActive():
                self._jitter_timer.start()
        else:
            self._jitter_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._start_blink_timer()
        self._update_jitter_timer()
        self._cabin_update_tick_timer()

    def hideEvent(self, event):
        self._blink_timer.stop()
        self._jitter_timer.stop()
        if self._cabin_tick_timer is not None:
            self._cabin_tick_timer.stop()
        super().hideEvent(event)

    def _pet_button_at(self, pos):
        """命中测试：pos 落在宠物哪枚功能按钮上；未命中返回 None。

        命中半径取固定像素（PET_BTN_HIT_RADIUS），保证小直径下仍可点。
        生态舱形象无球面按钮（展开模式单击接管），恒返回 None。
        """
        if self._pet_style == "cabin":
            return None
        for name in ("left", "right"):
            if self._pet_button_rect(name).contains(QPointF(pos)):
                return name
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            hit = self._pet_button_at(event.pos())
            if hit is not None:
                # 功能按钮命中：不启动拖拽，press/release 同域才触发
                self._pet_btn_pressed = hit
                self._pet_btn_hover = hit
                self.update()
                event.accept()
                return
            self._dragging = True
            self._moved = False
            self._press_global = event.globalPos()
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and (event.buttons() & Qt.LeftButton):
            if (event.globalPos() - self._press_global).manhattanLength() \
                    > self.DRAG_THRESHOLD:
                self._moved = True
            if self._moved:
                # 边界夹取：拖动全程球面不出安全矩形 A（bounds 硬约束）
                self.move(self._clamped_pos(event.globalPos() - self._drag_offset))
                self.moved.emit()
            event.accept()
            return
        # hover：功能按钮高亮 + 光标手型（依赖 __init__ 开启的 setMouseTracking）
        hit = self._pet_button_at(event.pos())
        if hit != self._pet_btn_hover:
            self._pet_btn_hover = hit
            self.setCursor(Qt.PointingHandCursor if hit else Qt.OpenHandCursor)
            self.update()
        # hover 目光跟随：瞳孔朝鼠标方向（归一化 ±1）；拖拽中/打盹/
        # alarm 吓呆时不跟，保持宠物情绪一致性（pet-react-2026-09-08）
        if (not self._dragging and self._max_temp is not None
                and self._max_level != self.MAXTEMP_ALARM):
            v = QPointF(event.pos()) - QPointF(self.width() / 2.0,
                                               self.height() / 2.0)
            n = math.hypot(v.x(), v.y())
            look = QPointF(v.x() / n, v.y() / n) if n > 1e-6 else QPointF(0, 0)
            if look != self._look:
                self._look = look
                self.update()

    def leaveEvent(self, event):
        if self._pet_btn_hover is not None or self._look != QPointF(0, 0):
            self._pet_btn_hover = None
            self._look = QPointF(0, 0)
            self.setCursor(Qt.OpenHandCursor)
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            pressed = self._pet_btn_pressed
            self._pet_btn_pressed = None
            if pressed is not None:
                hit = self._pet_button_at(event.pos())
                if hit == pressed:
                    if hit == "left":
                        self.leftButtonClicked.emit()
                    else:
                        self.rightButtonClicked.emit()
                self.update()
                event.accept()
                return
            was_drag = self._dragging and self._moved
            self._dragging = False
            self._moved = False
            if was_drag:
                # 拖拽松手：取消任何待发单击（防「点一下再拖」时弹窗被
                # 无预期切出——单击延迟窗口内的二次按压走了拖拽路径）
                self._click_pending = False
                self._settle_after_drag()
            elif self._pet_style == "cabin":
                # 生态舱手势：单击即切展开模式（无双击判定、无 280ms 延迟）
                self.expandToggleRequested.emit()
            else:
                # 单击/双击判定：窗口内第二次松手=双击（取消待发单击改发双击）
                if self._click_pending:
                    self._click_pending = False
                    self.doubleClicked.emit()
                elif self.CLICK_DELAY_MS <= 0:
                    self.clicked.emit()          # 延迟禁用：立即发（测试用）
                else:
                    self._click_pending = True
                    QTimer.singleShot(self.CLICK_DELAY_MS, self._fire_single_click)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _settle_after_drag(self):
        """拖拽松手落位（两形象共用）：命中角吸附位 → docked 滑入；否则 free；持久化。"""
        hit = self._hit_snap_corner()
        if hit is not None:
            self._mode = self.MODE_DOCKED
            self._corner = hit
            self._dock_move(hit, animate=True)
        else:
            self._mode = self.MODE_FREE
        self._persist_position()

    def _fire_single_click(self):
        """单击待发定时器到期：仍处于待发态才发（未被双击取消）。"""
        if self._click_pending:
            self._click_pending = False
            self.clicked.emit()

    def contextMenuEvent(self, event):
        """右键：悬浮面板显隐切换 + 「吸附到默认位置」找回入口（bounds 兜底）。"""
        menu = QMenu(self)
        # 全通道面板找回入口（面板隐藏/收起时显示「显示」，可见时显示「隐藏」；
        # 由主窗连接 panelToggleRequested 决定动作）
        act_panel = menu.addAction("隐藏全通道面板" if self._panel_visible()
                                   else "显示全通道面板")
        menu.addSeparator()
        act_bring_back = menu.addAction("吸附到默认位置（恢复默认位置）")
        act_corner_c = menu.addAction("吸附到屏幕居中")
        act_corner_tr = menu.addAction("吸附到右上角")
        act_corner_tl = menu.addAction("吸附到左上角")
        chosen = menu.exec_(event.globalPos())
        if chosen is act_panel:
            self.panelToggleRequested.emit()
        elif chosen is act_bring_back:
            self.bring_back()
        elif chosen is act_corner_c:
            self.bring_back_to(self.CORNER_CENTER)
        elif chosen is act_corner_tr:
            self.bring_back_to(self.CORNER_TOP_RIGHT)
        elif chosen is act_corner_tl:
            self.bring_back_to(self.CORNER_TOP_LEFT)
        event.accept()

    def _panel_visible(self) -> bool:
        """查宿主的全通道面板是否可见（菜单文案用；查不到视为不可见）。"""
        host = self._host
        panel = getattr(host, "_live_channel_panel", None) if host else None
        return bool(panel is not None and panel.isVisible())


class LiveOverviewPopup(QWidget):
    """无边框分层趋势弹窗：卡体边框层 + 顶栏 band + 画布 band，刚体停靠、可拖可缩放。"""

    closed = pyqtSignal()
    window_sec_changed = pyqtSignal(int)

    REFRESH_MIN_INTERVAL = 0.5
    CARD_RADIUS = 10
    BORDER_WIDTH = 1
    CARD_PAD = 6
    HEADER_H = 26
    DEFAULT_W, DEFAULT_H = 460, 320
    DOCK_GAP = 10
    RESIZE_MARGIN = 14
    DRAG_THRESHOLD = 4
    ANIM_MS = 150
    ANIMATE = True
    WINDOW_MIN_SEC = 5
    WINDOW_MAX_SEC = 120
    WINDOW_DEFAULT_SEC = 30

    # 不透明度允许区间（0=全透明，1=完全不透明）；与配置契约同源，见 ALPHA_MIN/MAX
    ALPHA_MIN = ALPHA_MIN
    ALPHA_MAX = ALPHA_MAX
    # 趋势线下方填充的不透明度
    FILL_ALPHA = 0.18
    # 刻度线/网格取「文字色 + 透明度」绘制，保证深浅 band 下均有对比度
    GRID_ALPHA = 0.26
    SPINE_ALPHA = 0.62
    # X 轴时间刻度的候选间隔（秒），按窗口跨度挑最贴近的一档
    TIME_STEPS_SEC = (1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1800)

    # Windows 非客户区命中测试常量（WM_NCHITTEST 八向缩放）
    _WM_NCHITTEST = 0x0084
    _HTCLIENT = 1
    _HTLEFT = 10
    _HTRIGHT = 11
    _HTTOP = 12
    _HTTOPLEFT = 13
    _HTTOPRIGHT = 14
    _HTBOTTOM = 15
    _HTBOTTOMLEFT = 16
    _HTBOTTOMRIGHT = 17

    def __init__(self, mw, parent=None):
        super().__init__(None)
        self.mw = mw
        cfg = ConfigIO.load_live_monitor_config()
        self._card_alpha = clamp_alpha(cfg.get("card_alpha", CARD_ALPHA_DEFAULT))
        self._card_color = cfg.get("card_color", CARD_COLOR_DEFAULT)
        # 趋势线/填充色：空串=跟随主题强调色，非空=用户自定义色（优先）
        self._line_color = cfg.get("line_color", LINE_COLOR_DEFAULT)
        self._window_sec_value = int(cfg.get("window_sec", self.WINDOW_DEFAULT_SEC))
        self.setObjectName("liveOverviewPopup")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMinimumSize(320, 240)
        self.resize(self.DEFAULT_W, self.DEFAULT_H)

        # 分层调色板（由 card_color 派生，apply_theme 计算并刷新）
        self._body_rgb = self._card_rgb()
        self._body_hex = self._card_color
        self._header_hex = self._card_color
        self._chart_hex = self._card_color
        self._border_hex = self._card_color
        self._chart_rgb = self._body_rgb
        self._border_rgb = self._body_rgb
        self._text_body = TEXT_ON_LIGHT
        self._text_chart = TEXT_ON_LIGHT

        self._pending = False
        self._last_refresh_ts = 0.0
        self._positioned_once = False
        self._anchor = None
        self._side = "right"
        self._drag_mode = None
        self._resize_zone = ""
        self._press_global = QPoint()
        self._start_geom = QRect()
        self._start_ball_pos = QPoint()
        self._moved = False
        self._anim = None
        self._throttle_timer = QTimer(self)
        self._throttle_timer.setSingleShot(True)
        self._throttle_timer.setInterval(int(self.REFRESH_MIN_INTERVAL * 1000))
        self._throttle_timer.timeout.connect(self._on_throttle_timeout)

        # 分层布局：卡体边框层(paintEvent) → 顶栏 band → 画布 band
        inner = QVBoxLayout(self)
        inner.setContentsMargins(
            self.CARD_PAD, self.CARD_PAD, self.CARD_PAD, self.CARD_PAD)
        inner.setSpacing(0)

        # —— 顶栏 band（进度条 + 时间窗口文字 + 关闭）——
        self.header = QWidget(self)
        self.header.setObjectName("liveMonitorHeader")
        self.header.setFixedHeight(self.HEADER_H)
        self.header.setAttribute(Qt.WA_StyledBackground, True)
        hbar = QHBoxLayout(self.header)
        hbar.setContentsMargins(6, 0, 4, 0)
        hbar.setSpacing(8)
        self.window_slider = QSlider(Qt.Horizontal, self.header)
        self.window_slider.setObjectName("liveMonitorRange")
        self.window_slider.setRange(self.WINDOW_MIN_SEC, self.WINDOW_MAX_SEC)
        self.window_slider.setValue(self._window_sec_value)
        self.window_slider.setFocusPolicy(Qt.NoFocus)
        self.window_label = QLabel(self.header)
        self.window_label.setObjectName("liveMonitorRangeLabel")
        self.window_label.setMinimumWidth(74)
        self.window_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.btn_close = QToolButton(self.header)
        self.btn_close.setObjectName("liveMonitorClose")
        self.btn_close.setText("×")
        self.btn_close.setToolTip("关闭监控悬浮窗")
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.setFocusPolicy(Qt.NoFocus)
        hbar.addWidget(self.window_slider, 1)
        hbar.addWidget(self.window_label)
        hbar.addWidget(self.btn_close)
        inner.addWidget(self.header)

        # —— 画布 band（温度轴标题/刻度/时间刻度统一底色）——
        self.chart_frame = QWidget(self)
        self.chart_frame.setObjectName("liveMonitorChartFrame")
        self.chart_frame.setAttribute(Qt.WA_StyledBackground, True)
        cfl = QVBoxLayout(self.chart_frame)
        cfl.setContentsMargins(0, 0, 0, 0)
        cfl.setSpacing(0)
        self.fig = Figure(figsize=(4.4, 2.6), dpi=100, facecolor="none",
                          constrained_layout=True)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setAttribute(Qt.WA_TranslucentBackground)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.setMinimumSize(1, 1)
        self.canvas.setFocusPolicy(Qt.NoFocus)
        self.canvas.setCursor(Qt.SizeAllCursor)
        self.canvas.setMouseTracking(True)   # 非 Windows 下悬停边缘给缩放光标
        self.ax = self.fig.add_subplot(111)
        cfl.addWidget(self.canvas)
        inner.addWidget(self.chart_frame, 1)

        self.window_slider.valueChanged.connect(self._on_window_slider_live)
        self.window_slider.sliderReleased.connect(self._on_window_slider_commit)
        self.btn_close.clicked.connect(self.close_popup)

        self.canvas.installEventFilter(self)
        self.header.installEventFilter(self)

        self._sync_window_label()

        # —— 模态让位：本窗与悬浮球都是置顶窗，可能正好盖住居中弹出的模态框
        # （如「退出确认」），而模态期间同应用的其它窗口既不可操作、也无法拖动
        # 悬浮窗 → 监听应用模态，有模态自动隐藏让位，模态结束按原状态恢复。
        self._modal_suspend_active = False
        self._modal_suspend_popup_visible = False
        self._modal_suspend_ball_visible = False
        self._restore_hook = None  # 宿主注入：模态恢复后的显隐复核回调
        self._modal_watch = QTimer(self)
        self._modal_watch.setInterval(200)
        self._modal_watch.timeout.connect(self._watch_modal_yield)
        self._modal_watch.start()

        self.apply_theme()
        self.hide()

    # ------------------------------------------------------------------
    # 单色解析（由唯一底色调派生三层）
    # ------------------------------------------------------------------
    def _card_rgb(self):
        return _hex_rgb(self._card_color, (232, 236, 239))

    @staticmethod
    def _is_light(rgb):
        r, g, b = rgb
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0 > 0.60

    @classmethod
    def _text_for(cls, rgb):
        return TEXT_ON_LIGHT if cls._is_light(rgb) else TEXT_ON_DARK

    def _compute_palette(self):
        """由唯一底色调 ``card_color`` 派生三层配色，保证同色系但层级分明：

        - 顶栏 band：在卡体色基础上轻微压暗/提亮（与卡体微差，体现分层）；
        - 画布 band：明显压暗/提亮（与卡体、边框均区分，作为轴标题/刻度/时间
          刻度的统一背景）；
        - 边框：在卡体色基础上压暗/提亮到最深/最浅，作为独立描边色。
        """
        base = self._card_color
        body = self._card_rgb()
        light = self._is_light(body)
        if light:
            header = Theme.darken(base, 0.05)
            chart = Theme.darken(base, 0.52)
            border = Theme.darken(base, 0.70)
        else:
            # 暗卡：画布 band 明显提亮为「浅 band」，配深字，与暗卡体形成强对比分层
            header = Theme.lighten(base, 0.05)
            chart = Theme.lighten(base, 0.70)
            border = Theme.lighten(base, 0.82)
        chart_rgb = _hex_rgb(chart, body)
        border_rgb = _hex_rgb(border, body)
        header_rgb = _hex_rgb(header, body)
        text_body = TEXT_ON_LIGHT if light else TEXT_ON_DARK
        chart_light = self._is_light(chart_rgb)
        text_chart = TEXT_ON_LIGHT if chart_light else TEXT_ON_DARK
        self._body_hex = base
        self._body_rgb = body
        self._header_hex = header
        self._header_rgb = header_rgb
        self._chart_hex = chart
        self._border_hex = border
        self._chart_rgb = chart_rgb
        self._border_rgb = border_rgb
        self._text_body = text_body
        self._text_chart = text_chart

    # ------------------------------------------------------------------
    # 外观参数（供设置页/热重载即时应用）
    # ------------------------------------------------------------------
    def set_card_color(self, color):
        if _hex_rgb(color, None) is not None:
            self._card_color = color
            self.apply_theme()

    def set_card_alpha(self, alpha):
        """设置整卡不透明度，接受 0.0~1.0（旧的 0.30~0.95 硬钳制已移除）。"""
        self._card_alpha = clamp_alpha(alpha)
        self.apply_theme()

    def set_card_alpha_percent(self, percent):
        """按百分比设置不透明度，兼容 ``100`` / ``"100%"`` / ``"60"`` 等写法。

        返回 ``(ok, message)``：``ok`` 表示输入可解析并已应用；``message`` 为
        非法或越界时的提示文案（合法且在范围内时为空串）。
        """
        ok, value, message = parse_percent(percent, self.ALPHA_MIN * 100.0,
                                           self.ALPHA_MAX * 100.0)
        if ok:
            self._card_alpha = clamp_alpha(value / 100.0)
            self.apply_theme()
        return ok, message

    def set_line_color(self, color):
        """设置趋势线/填充色；空串或 None 表示恢复默认（跟随主题强调色）。"""
        if color is None:
            self._line_color = LINE_COLOR_DEFAULT
        elif isinstance(color, str) and color.strip() == "":
            self._line_color = LINE_COLOR_DEFAULT
        elif _hex_rgb(color, None) is not None:
            self._line_color = color
        else:
            return False
        self.apply_theme()
        return True

    def _resolve_line_color(self):
        """趋势色：自定义色优先，未自定义时跟随当前主题强调色。

        空串（默认）返回 ``Theme.ACCENT``，主题切换后由 ``apply_theme`` 重新解析，
        因此跟随主题色会自动同步更新。
        """
        custom = (self._line_color or "").strip()
        if custom and _hex_rgb(custom, None) is not None:
            return custom
        return getattr(Theme, "ACCENT", "#1769aa")

    @property
    def using_custom_line_color(self):
        """是否处于「用户自定义趋势色」状态（True 时自定义色覆盖通道色）。"""
        custom = (self._line_color or "").strip()
        return bool(custom) and _hex_rgb(custom, None) is not None

    def set_appearance(self, cfg):
        """一次性应用全部外观配置：属性更新 + 单次重绘（供批量应用）。

        颜色字段与单个 setter 同款校验：card_color 必须是合法 hex（否则忽略本次
        值），line_color 只接受合法 hex 或空串（空串=跟随主题），避免非法值流入
        ``_compute_palette`` 触发 ``Theme.darken/lighten`` 的解析异常。
        """
        color = cfg.get("card_color", self._card_color)
        if _hex_rgb(color, None) is not None:
            self._card_color = color
        self._card_alpha = clamp_alpha(cfg.get("card_alpha", self._card_alpha))
        line = cfg.get("line_color", self._line_color)
        if isinstance(line, str) and (line.strip() == ""
                                      or _hex_rgb(line, None) is not None):
            self._line_color = line
        self.set_window_sec(cfg.get("window_sec", self._window_sec_value))
        self.apply_theme()

    # ------------------------------------------------------------------
    # 时间窗口滑块
    # ------------------------------------------------------------------
    def _window_sec(self):
        return self._window_sec_value

    def _sync_window_label(self):
        self.window_label.setText(f"最近 {self._window_sec_value} 秒")

    def _on_window_slider_live(self, value):
        self._window_sec_value = int(value)
        self._sync_window_label()
        self.refresh_curves(force=True)

    def _on_window_slider_commit(self):
        self.window_sec_changed.emit(self._window_sec_value)

    def set_window_sec(self, value):
        value = max(self.WINDOW_MIN_SEC,
                    min(self.WINDOW_MAX_SEC, int(value)))
        self._window_sec_value = value
        self.window_slider.blockSignals(True)
        self.window_slider.setValue(value)
        self.window_slider.blockSignals(False)
        self._sync_window_label()
        self.refresh_curves(force=True)

    # ------------------------------------------------------------------
    # 与悬浮球的刚体停靠
    # ------------------------------------------------------------------
    def set_anchor(self, ball):
        self._anchor = ball
        if ball is not None:
            ball.moved.connect(self._follow_anchor)

    def _avail(self, ref_rect):
        screen = QApplication.screenAt(ref_rect.center()) \
            or QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None \
            else QRect(0, 0, 1920, 1080)

    def _fits(self, side, bg, avail, w, h):
        gap = self.DOCK_GAP
        if side == "right":
            return bg.right() + 1 + gap + w <= avail.right()
        if side == "left":
            return bg.left() - gap - w >= avail.left()
        if side == "top":
            return bg.top() - gap - h >= avail.top()
        if side == "bottom":
            return bg.bottom() + 1 + gap + h <= avail.bottom()
        return False

    def _choose_side(self, bg, avail, w, h):
        for side in ("right", "left", "top", "bottom"):
            if self._fits(side, bg, avail, w, h):
                return side
        rooms = {
            "right": avail.right() - bg.right(),
            "left": bg.left() - avail.left(),
            "top": bg.top() - avail.top(),
            "bottom": avail.bottom() - bg.bottom(),
        }
        return max(rooms, key=rooms.get)

    def _docked_rect(self):
        ball = self._anchor
        if ball is None or not ball.isVisible():
            return QRect(self.geometry())
        bg = ball.frameGeometry()
        avail = self._avail(bg)
        w, h = self.width(), self.height()
        gap = self.DOCK_GAP
        if not self._fits(self._side, bg, avail, w, h):
            self._side = self._choose_side(bg, avail, w, h)
        side = self._side
        if side == "right":
            x, y = bg.right() + 1 + gap, bg.center().y() - h // 2
        elif side == "left":
            x, y = bg.left() - gap - w, bg.center().y() - h // 2
        elif side == "top":
            x, y = bg.center().x() - w // 2, bg.top() - gap - h
        else:
            x, y = bg.center().x() - w // 2, bg.bottom() + 1 + gap
        x = max(avail.left(), min(x, avail.right() - w + 1))
        y = max(avail.top(), min(y, avail.bottom() - h + 1))
        return QRect(x, y, w, h)

    def _follow_anchor(self):
        if self.isVisible():
            self.setGeometry(self._docked_rect())

    # ------------------------------------------------------------------
    # 显隐与抽屉动画
    # ------------------------------------------------------------------
    def _target_opacity(self):
        """整卡统一透明度（窗口级），开合动画淡入到此值而非 1.0。

        覆盖 0.0~1.0 全区间：0.0 全透明、1.0 完全不透明，不再压到 0.30~0.95。
        """
        return clamp_alpha(self._card_alpha)

    def show_popup(self):
        if self._anchor is None and not self._positioned_once:
            self._move_to_default()
            self._positioned_once = True
        self.setGeometry(self._docked_rect())
        self.show()
        self.raise_()
        if self.ANIMATE and self.ANIM_MS > 0:
            self._animate_open()
        else:
            self.setWindowOpacity(self._target_opacity())
        self.refresh_curves(force=True)

    def _animate_open(self):
        target = QRect(self.geometry())
        nudge = {"right": (-24, 0), "left": (24, 0),
                 "top": (0, 24), "bottom": (0, -24)}.get(self._side, (0, 0))
        start = QRect(target)
        start.translate(*nudge)
        self.setGeometry(start)
        self.setWindowOpacity(0.0)
        pos_anim = QPropertyAnimation(self, b"pos", self)
        pos_anim.setDuration(self.ANIM_MS)
        pos_anim.setStartValue(start.topLeft())
        pos_anim.setEndValue(target.topLeft())
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(self.ANIM_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(self._target_opacity())
        self._anim = (pos_anim, fade)
        pos_anim.start()
        fade.start()

    def _stop_anim(self):
        if self._anim:
            for anim in self._anim:
                anim.stop()
            self._anim = None

    # ------------------------------------------------------------------
    # 模态让位（悬浮置顶窗不得盖住模态框 → 模态期间自动隐藏，结束后恢复）
    # ------------------------------------------------------------------
    @staticmethod
    def _current_modal_widget():
        """当前应用的活动模态框。

        应用模态进 ``QApplication.activeModalWidget``；而 QDialog 带父窗时默认
        WindowModal，exec 期间不进该表，需回退检查「活动窗口的 modality ≠ NonModal」。
        """
        modal = QApplication.activeModalWidget()
        if modal is not None:
            return modal
        active = QApplication.activeWindow()
        if active is not None and active.windowModality() != Qt.NonModal:
            return active
        return None

    def suspend_for_modal(self):
        """让位于模态对话框：记录可见性并隐藏本窗与悬浮球（不发 closed 信号）。

        修复交互：退出按钮弹出的「确认退出」若正好落在本置顶窗之下，用户点不到
        也无法退出；模态期间同应用其它窗口又不可操作（含拖动悬浮窗）。故先让位
        隐藏，模态结束后由 ``restore_after_modal`` 按原状态恢复。
        """
        if getattr(self, "_modal_suspend_active", False):
            return
        self._modal_suspend_active = True
        self._modal_suspend_popup_visible = self.isVisible()
        if self.isVisible():
            self.hide()
        ball = self._anchor
        self._modal_suspend_ball_visible = bool(
            ball is not None and ball.isVisible())
        if ball is not None and ball.isVisible():
            ball.hide()

    def restore_after_modal(self):
        """模态结束恢复：仅恢复此前可见的悬浮窗/球（位置与透明度保持原状）。

        恢复后回调 ``_restore_hook``：主窗据此重申「仅在线采集中显示」的
        显隐规则——采集在模态期间结束（如拔线自动停采）时，让位记录的
        可见性已失效，须由主窗按当前采集态收回，不能凭旧记录复显。
        """
        if not getattr(self, "_modal_suspend_active", False):
            return
        self._modal_suspend_active = False
        if getattr(self, "_modal_suspend_popup_visible", False):
            self._modal_suspend_popup_visible = False
            self.show()
            self.raise_()
            self.setWindowOpacity(self._target_opacity())
            self.refresh_curves(force=True)
        ball = self._anchor
        if ball is not None and getattr(self, "_modal_suspend_ball_visible", False):
            self._modal_suspend_ball_visible = False
            ball.show()
        hook = getattr(self, "_restore_hook", None)
        if hook is not None:
            hook()

    def set_restore_hook(self, hook) -> None:
        """注入模态恢复后的显隐复核回调（由宿主主窗提供，可为 None）。"""
        self._restore_hook = hook

    def cancel_suspend(self):
        """退出已确认等场景：清除让位状态但不再恢复显示（窗口即将关闭）。"""
        self._modal_suspend_active = False
        self._modal_suspend_popup_visible = False
        self._modal_suspend_ball_visible = False

    def _watch_modal_yield(self):
        """200ms 轮询：存在活动模态 → 让位；无模态 → 恢复（幂等，见内部标志）。"""
        if self._current_modal_widget() is not None:
            self.suspend_for_modal()
        else:
            self.restore_after_modal()

    def close_popup(self):
        self._pending = False
        self._throttle_timer.stop()
        self._stop_anim()
        self.setWindowOpacity(self._target_opacity())
        self.hide()
        self.closed.emit()

    def closeEvent(self, event):
        self._throttle_timer.stop()
        self._stop_anim()
        self.hide()
        self.closed.emit()
        event.accept()

    def _move_to_default(self):
        if isinstance(self.mw, QWidget) and self.mw.isVisible():
            host = self.mw.frameGeometry()
            self.move(host.right() - self.width() - 18, host.top() + 50)
            return
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.move(available.right() - self.width() - 18, available.top() + 40)

    # ------------------------------------------------------------------
    # 拖动（刚体移动）与缩放（向远离球方向生长）
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        if obj in (self.canvas, self.header):
            et = event.type()
            if et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                # 子控件上的按下先判窗口边缘缩放热区：边缘→缩放，其余→拖动
                zone = self._resize_zone_at(obj.mapTo(self, event.pos()))
                if zone:
                    self._begin_drag("resize", event.globalPos(), zone)
                else:
                    self._begin_drag("move", event.globalPos())
                event.accept()
                return True
            if et == QEvent.MouseMove:
                if self._drag_mode is not None:
                    self._update_drag(event.globalPos())
                else:
                    # 非 Windows 下无 WM_NCHITTEST，靠手动给边缘/角缩放光标
                    self._apply_zone_cursor(
                        self._resize_zone_at(obj.mapTo(self, event.pos())), obj)
                event.accept()
                return True
            if et == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton \
                    and self._drag_mode is not None:
                self._end_drag()
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def nativeEvent(self, event_type, message):
        """Windows 下用 WM_NCHITTEST 接管八向缩放：圆角、光标、拖拽均由系统处理。"""
        if sys.platform == "win32" and event_type == "windows_generic_MSG":
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
            except (ValueError, OSError):
                return super().nativeEvent(event_type, message)
            if msg.message == self._WM_NCHITTEST:
                x = msg.pt.x - self.x()
                y = msg.pt.y - self.y()
                ht = self._hit_test(x, y)
                return True, ht
        return super().nativeEvent(event_type, message)

    def _hit_test(self, x, y):
        """把窗口内坐标映射为 HT 命中码（四角/四边→缩放，其余→客户区）。"""
        m = self.RESIZE_MARGIN
        w, h = self.width(), self.height()
        left = x <= m
        right = x >= w - m
        top = y <= m
        bottom = y >= h - m
        if top and left:
            return self._HTTOPLEFT
        if top and right:
            return self._HTTOPRIGHT
        if bottom and left:
            return self._HTBOTTOMLEFT
        if bottom and right:
            return self._HTBOTTOMRIGHT
        if left:
            return self._HTLEFT
        if right:
            return self._HTRIGHT
        if top:
            return self._HTTOP
        if bottom:
            return self._HTBOTTOM
        return self._HTCLIENT

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            zone = self._resize_zone_at(event.pos())
            if zone:
                self._begin_drag("resize", event.globalPos(), zone)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_mode == "resize":
            self._update_drag(event.globalPos())
            event.accept()
            return
        if self._drag_mode is None:
            self._apply_zone_cursor(self._resize_zone_at(event.pos()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_mode == "resize":
            self._end_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _begin_drag(self, mode, global_pos, zone=""):
        self._drag_mode = mode
        self._resize_zone = zone
        self._press_global = global_pos
        self._start_geom = QRect(self.geometry())
        self._start_ball_pos = (QPoint(self._anchor.pos())
                                if self._anchor is not None else QPoint())
        self._moved = False

    def _update_drag(self, global_pos):
        delta = global_pos - self._press_global
        if delta.manhattanLength() > self.DRAG_THRESHOLD:
            self._moved = True
        if self._drag_mode == "move":
            if self._anchor is not None:
                self._anchor.move(self._start_ball_pos + delta)
            self.setGeometry(self._docked_rect())
        elif self._drag_mode == "resize":
            self._apply_resize(delta)

    def _end_drag(self):
        self._drag_mode = None
        self._resize_zone = ""

    def _resize_zone_at(self, pos):
        m = self.RESIZE_MARGIN
        w, h = self.width(), self.height()
        left = pos.x() <= m
        right = pos.x() >= w - m
        top = pos.y() <= m
        bottom = pos.y() >= h - m
        return (("L" if left else "R" if right else "")
                + ("T" if top else "B" if bottom else ""))

    def _apply_resize(self, delta):
        g = QRect(self._start_geom)
        zone = self._resize_zone
        if "L" in zone:
            g.setLeft(g.left() + delta.x())
        if "R" in zone:
            g.setRight(g.right() + delta.x())
        if "T" in zone:
            g.setTop(g.top() + delta.y())
        if "B" in zone:
            g.setBottom(g.bottom() + delta.y())
        min_w, min_h = self.minimumWidth(), self.minimumHeight()
        if g.width() < min_w:
            if "L" in zone:
                g.setLeft(g.right() - min_w + 1)
            else:
                g.setRight(g.left() + min_w - 1)
        if g.height() < min_h:
            if "T" in zone:
                g.setTop(g.bottom() - min_h + 1)
            else:
                g.setBottom(g.top() + min_h - 1)
        self.resize(g.width(), g.height())
        self.setGeometry(self._docked_rect())

    def _apply_zone_cursor(self, zone, target=None):
        cursor = {
            "LT": Qt.SizeFDiagCursor, "RB": Qt.SizeFDiagCursor,
            "RT": Qt.SizeBDiagCursor, "LB": Qt.SizeBDiagCursor,
            "L": Qt.SizeHorCursor, "R": Qt.SizeHorCursor,
            "T": Qt.SizeVerCursor, "B": Qt.SizeVerCursor,
        }.get(zone)
        widget = target if target is not None else self
        widget.setCursor(cursor if cursor is not None else Qt.SizeAllCursor)

    # ------------------------------------------------------------------
    # 数据刷新（500ms 节流 + 时间跨度=滑块 + 跟随最新时刻滚动）
    # ------------------------------------------------------------------
    def refresh_curves(self, force=False):
        if not self.isVisible():
            return
        now = _monotonic()
        if not force and now - self._last_refresh_ts < self.REFRESH_MIN_INTERVAL:
            self._pending = True
            if not self._throttle_timer.isActive():
                delay_ms = max(1, int((self.REFRESH_MIN_INTERVAL
                                       - (now - self._last_refresh_ts)) * 1000) + 10)
                self._throttle_timer.start(delay_ms)
            return
        self._throttle_timer.stop()
        self._last_refresh_ts = now
        self._pending = False
        self._do_refresh()

    def _on_throttle_timeout(self):
        if self._pending and self.isVisible():
            self.refresh_curves(force=True)
        else:
            self._pending = False

    def _do_refresh(self):
        panel = getattr(self.mw, "channel_panel", None)
        if panel is None:
            return
        try:
            series = panel.visible_series(None)
        except Exception:
            series = []
        renderer = getattr(self.mw, "chart_renderer", None)
        alarm = set(getattr(renderer, "_alarm_channels", ()) or ())

        window_min = self._window_sec() / 60.0
        max_pts = max(200, min(2000, self.width()))

        self.ax.clear()
        end_min = 0.0
        for name, x, v in series:
            x = np.asarray(x, dtype=float)
            v = np.asarray(v, dtype=float)
            count = min(x.size, v.size)
            if count == 0:
                continue
            x, v = x[:count], v[:count]
            finite = x[np.isfinite(x)]
            if finite.size:
                end_min = max(end_min, float(finite.max()))

        start_min = end_min - window_min
        lo, hi = self._main_ylim()
        # 自定义趋势色优先：设置后折线与填充统一用该色；否则各通道沿用自身色，
        # 无通道色时回落到当前主题强调色（跟随主题，随主题切换自动同步）。
        custom = self._resolve_line_color() if self.using_custom_line_color else ""
        theme_color = self._resolve_line_color()
        for name, x, v in series:
            xs, vs = SeriesSampler.window(x, v, start_min, end_min, max_pts)
            if xs.size == 0:
                continue
            if custom:
                color = custom
            elif name in alarm:
                color = Theme.RED
            else:
                try:
                    color = panel.color_of(name) or theme_color
                except Exception:
                    color = theme_color
            self.ax.plot(xs, vs, color=color, linewidth=1.0, zorder=2)
            # 填充：以温度轴下限为基线，用该条曲线自身的颜色低透明填充
            self.ax.fill_between(xs, vs, lo, color=color, alpha=self.FILL_ALPHA,
                                 linewidth=0, zorder=1)

        end_for_axis = end_min if end_min > 0.0 else window_min
        start_for_axis = end_for_axis - window_min
        self.ax.set_xlim(start_for_axis, end_for_axis)
        self.ax.set_ylim(lo, hi)
        self.ax.set_xlabel("采集时间 (时:分:秒)", fontsize=7)
        self.ax.set_ylabel("温度 (°C)", fontsize=7)
        self._apply_ticks(start_for_axis, end_for_axis, lo, hi)
        # 刻度设置会把刻度位置并入视图区间，可能微调上下限；这里再钉一次，
        # 保证弹窗纵轴始终与主画布一致（set_*ticks 撑大视图的坑见 _apply_y_ticks）。
        self.ax.set_xlim(start_for_axis, end_for_axis)
        self.ax.set_ylim(lo, hi)
        self._style_axes()
        self.canvas.draw_idle()

    @staticmethod
    def _format_axis_time(minutes):
        total_sec = max(0, int(round(float(minutes) * 60.0)))
        hours, rem = divmod(total_sec, 3600)
        minutes_part, seconds_part = divmod(rem, 60)
        if hours:
            return f"{hours}:{minutes_part:02d}:{seconds_part:02d}"
        return f"{minutes_part}:{seconds_part:02d}"

    # ------------------------------------------------------------------
    # 刻度线：按数据范围自适应间隔（Y=温度，X=时间）
    # ------------------------------------------------------------------
    def _apply_ticks(self, x_start, x_end, y_lo, y_hi):
        """设置 X（时间）与 Y（温度）刻度线及标签，间隔随数据范围自适应。

        - Y 轴：按温度跨度挑整齐间隔（1/2/2.5/5/10 × 10^n），输出温度刻度线与
          数值标签，单位 °C 由轴标题「温度 (°C)」承载；
        - X 轴：按时间跨度从 TIME_STEPS_SEC 中挑最贴近的一档，输出时间刻度线
          与时:分:秒 标签。

        标签数量还按画布尺寸收缩，避免小卡片上刻度标签互相重叠。
        """
        self._apply_y_ticks(y_lo, y_hi)
        self._apply_x_ticks(x_start, x_end)

    def _apply_y_ticks(self, lo, hi):
        """Y 轴温度刻度：跨度自适应 + 整齐间隔 + 数值标签。

        刻度必须裁剪在 ``[lo, hi]`` 之内——matplotlib 的 ``set_yticks`` 会把刻度
        位置并入视图区间，一旦生成超出上界的刻度就会把 ``set_ylim`` 反向撑大
        （实测 15~55 被撑成 15~60），导致弹窗纵轴与主画布不再一致。
        """
        lo, hi = float(lo), float(hi)
        span = hi - lo
        if not math.isfinite(span) or span <= 0:
            span = 1.0
        target = max(3, min(6, max(1, self.chart_frame.height()) // 42))
        step = _nice_step(span, target)
        if step <= 0:
            step = 1.0
        eps = step * 1e-9
        start = math.ceil((lo - eps) / step) * step
        ticks = []
        value = start
        while value <= hi + eps and len(ticks) < 32:
            ticks.append(value)
            value += step
        if not ticks:
            ticks = [lo]
        self.ax.set_yticks(ticks)
        decimals = 0 if step >= 1.0 else 1
        self.ax.set_yticklabels([f"{t:.{decimals}f}" for t in ticks])

    def _apply_x_ticks(self, start_min, end_min):
        """X 轴时间刻度：按窗口跨度挑整齐秒数间隔 + 时:分:秒 标签。

        与 Y 轴同理，刻度裁剪在 ``[start_min, end_min]`` 内，避免撑大 X 视图区间。
        """
        start_min, end_min = float(start_min), float(end_min)
        span_sec = (end_min - start_min) * 60.0
        if not math.isfinite(span_sec) or span_sec <= 0:
            span_sec = float(self._window_sec())
        target = max(3, min(7, max(1, self.chart_frame.width()) // 110))
        raw = span_sec / float(target)
        step_sec = self.TIME_STEPS_SEC[-1]
        for candidate in self.TIME_STEPS_SEC:
            if candidate >= raw:
                step_sec = candidate
                break
        step_min = step_sec / 60.0
        eps = step_min * 1e-9
        first = math.ceil((max(0.0, start_min) - eps) / step_min) * step_min
        ticks = []
        value = first
        while value <= end_min + eps and len(ticks) < 32:
            ticks.append(value)
            value += step_min
        if not ticks:
            ticks = [end_min]
        self.ax.set_xticks(ticks)
        self.ax.set_xticklabels([self._format_axis_time(t) for t in ticks])

    def _main_ylim(self):
        tab = getattr(self.mw, "tab_all", None)
        ax = getattr(tab, "ax", None)
        if ax is not None and len(getattr(ax, "lines", []) or ()):
            try:
                lo, hi = map(float, ax.get_ylim())
                if np.isfinite(lo) and np.isfinite(hi) and hi > lo:
                    return lo, hi
            except Exception:
                pass
        lo = float(getattr(self.mw, "ax_temp_base_lo", 20.0))
        hi = float(getattr(self.mw, "ax_temp_base_hi", 40.0))
        return (lo, hi) if hi > lo else (20.0, 40.0)

    # ------------------------------------------------------------------
    # 主题与 Qt 事件
    # ------------------------------------------------------------------
    def _style_axes(self):
        """画布 band 统一背景：温度轴标题、刻度、时间刻度均落在此底色上。

        刻度线与网格改用「文字色 + 透明度」绘制——旧版拿画布底色自混、alpha 仅
        0.16/0.38，在深色 band 上几乎不可见。文字色本身已按 band 亮度选过深浅
        （TEXT_ON_LIGHT / TEXT_ON_DARK），因此无论浅色 band 还是深色 band，
        刻度线都能保持足够对比度。
        """
        text = self._text_chart
        tr, tg, tb = _hex_rgb(text, (31, 42, 55))
        grid = (tr / 255.0, tg / 255.0, tb / 255.0, self.GRID_ALPHA)
        spine = (tr / 255.0, tg / 255.0, tb / 255.0, self.SPINE_ALPHA)
        self.ax.set_facecolor(self._chart_hex)
        self.ax.tick_params(axis="both", which="major", color=spine,
                            labelcolor=text, labelsize=7, pad=1.5,
                            length=3.5, width=0.8, direction="out")
        self.ax.grid(True, axis="both", which="major", color=grid,
                     linewidth=0.6, linestyle="-")
        self.ax.set_axisbelow(True)
        for s in self.ax.spines.values():
            s.set_color(spine)
            s.set_linewidth(0.8)
        for label in (self.ax.xaxis.label, self.ax.yaxis.label):
            label.set_color(text)
            label.set_fontsize(7)
            label.set_fontfamily(Theme.font("plot"))
        for tick in self.ax.get_xticklabels() + self.ax.get_yticklabels():
            tick.set_color(text)
            tick.set_fontsize(7)
            tick.set_fontfamily(Theme.font("mono"))

    def apply_theme(self):
        self._compute_palette()
        cr, cg, cb = self._chart_rgb
        # 画布统一采用 chart band 背景（温度轴标题/刻度/时间刻度同底），
        # 整卡透明度统一由窗口 opacity 施加（见下），故 figure 不透明。
        self.fig.patch.set_facecolor((cr / 255, cg / 255, cb / 255))
        self.fig.patch.set_alpha(1.0)
        self.setWindowOpacity(clamp_alpha(self._card_alpha))

        # 分层着色：顶栏 band / 画布 band / 边框 三者同色系、不同明暗
        br, bg, bb = self._border_rgb
        trk = _hex_rgb(
            Theme.darken(self._card_color, 0.22)
            if self._is_light(self._body_rgb)
            else Theme.lighten(self._card_color, 0.22),
            self._body_rgb)
        groove = f"rgba({trk[0]},{trk[1]},{trk[2]},0.35)"
        sub = f"rgba({trk[0]},{trk[1]},{trk[2]},0.65)"
        self.setStyleSheet(
            f"QWidget#liveMonitorHeader {{ background: {self._header_hex};"
            f" border-bottom: 1px solid rgba({br},{bg},{bb},0.55); }}"
            f"QWidget#liveMonitorChartFrame {{ background: {self._chart_hex}; }}"
            f"QLabel#liveMonitorRangeLabel {{ color: {self._text_body};"
            f" background: transparent; font-size: 9pt; }}"
            # 进度条/滑块全部采用 card_color 同色系派生色，与卡体/边框融合一致
            f"QSlider#liveMonitorRange::groove:horizontal {{"
            f" height: 4px; background: {groove}; border-radius: 2px; }}"
            f"QSlider#liveMonitorRange::sub-page:horizontal {{"
            f" background: {sub}; border-radius: 2px; }}"
            f"QSlider#liveMonitorRange::handle:horizontal {{"
            f" width: 12px; height: 12px; margin: -4px 0; border-radius: 6px;"
            f" background: {self._border_hex}; border: 1px solid {self._text_body}; }}")
        self.btn_close.setStyleSheet(
            f"QToolButton#liveMonitorClose {{ color: {self._text_body};"
            f" background: rgba({br},{bg},{bb},0.18); border: none;"
            f" border-radius: 11px; font-size: 12pt; font-weight: 600;"
            f" padding: 0; margin: 0; min-width: 0; min-height: 0; }}"
            f"QToolButton#liveMonitorClose:hover {{ background: {Theme.RED};"
            f" color: #ffffff; }}")
        # 主题切换后按（可能已变化的）主题色重绘曲线与刻度，使「跟随主题」的
        # 趋势色、填充色与刻度配色自动同步；未显示时 refresh_curves 会直接返回。
        self._style_axes()
        self.refresh_curves(force=True)
        self.canvas.draw_idle()
        self.update()

    def paintEvent(self, _event):
        """手绘最外层卡体边框层：圆角矩形（卡体色填充）+ 独立描边色 + 四角握把。

        透明度不在这里做，而是整窗 setWindowOpacity 统一施加——保证顶栏 band、
        边框、画布 band、进度条透明度完全一致（各层只以同色系明暗分层）。
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        br, bg, bb = self._body_rgb
        edge = QColor(*self._border_rgb)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(edge, self.BORDER_WIDTH))
        painter.setBrush(QColor(br, bg, bb))
        painter.drawRoundedRect(rect, self.CARD_RADIUS, self.CARD_RADIUS)
        self._draw_corner_grips(painter)
        painter.end()

    def _draw_corner_grips(self, painter):
        """四角 L 形握把，提示可拖拽四角/四边缩放。"""
        pad = self.CARD_PAD + 3
        L, R = pad, self.width() - pad
        T, B = pad, self.height() - pad
        g = QColor(*self._border_rgb)
        g.setAlpha(170)
        pen = QPen(g, 1.5)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        s = 7
        for cx, cy in ((L, T), (R, T), (L, B), (R, B)):
            hx = cx + s if cx == L else cx - s
            vy = cy + s if cy == T else cy - s
            painter.drawLine(cx, cy, hx, cy)
            painter.drawLine(cx, cy, cx, vy)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_curves()
