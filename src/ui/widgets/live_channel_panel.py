# -*- coding: utf-8 -*-
"""全通道悬浮监控面板（悬浮球 / 趋势弹窗之外的第三块独立悬浮显示）。

定位与边界
================================
- 独立顶级悬浮窗：无边框 + Tool + 置顶 + 半透明，与悬浮球、趋势弹窗互不依附，
  不读球/弹窗的任何状态；显示开关、透明度与位置持久化由主窗接线（panel_*
  配置字段），本模块只负责布局、渲染与动画，不直接读写配置。
- 显示在线采集会话中全部可见通道的一行摘要：通道名 + 当前温度 + 胶囊进度条，
  顶部附全局采样状态行（采样点数 · 最新时刻）。

视觉配方（借鉴 LiteMonitor 竖屏面板 + 本项目分层卡片惯例）
================================
- 圆角卡体 + 独立描边；卡片皮肤为**深浅两套固定骨架色板**（PANEL_PALETTES，
  用户经设置页二选，与场景主题解耦、四档主题下观感恒定），琥珀/红状态色仍走
  Theme 语义色保持全应用一致；整窗透明度由 setWindowOpacity 统一施加；
- 每通道行 = 通道色圆点 + 名称（左，超长省略）+ 温度（右，等宽字体防抖动）
  + 胶囊进度条（圆角 = 条高一半）；
- 进度条量程 = 0 → 报警上限（与曲线报警线同源），含义是「距超温的余量」；
- 填充色分档：normal 用通道色（与主界面曲线呼应）/ warn 琥珀橙 / alarm 纯红常亮
  （与三维热区语义一致）；温度文字 normal 用正文色，warn/alarm 跟档位色；
- 无效通道（value=None，协议的通道关/溢出/开路已在采集层统一转 None）显示
  "--" 灰态、条只画底槽，绝不当作 0 ℃ 参与显示。

动效（克制原则：数据百毫秒级刷新，动画必须短而稳）
================================
- 数值平滑：显示值每 tick（约 30fps）向真实值线性插值，差值小于吸附阈值直接
  贴合（LiteMonitor TickSmooth 模式）；进度条宽度跟显示值走，数字与条同步滑动；
- 状态变色：档位切换时颜色 200ms 渐变过渡，不做闪烁；
- 文本缓存：温度文本仅在显示值变化超过阈值时重建，热路径零字符串分配；
- 布局/渲染分离：所有矩形由 :func:`compute_panel_layout` 一次算好缓存，
  paintEvent 零测量零数学；仅通道增删（结构变化）才重排，其余帧只画。

动画经类变量 ``ANIMATE`` 开关（测试置 False 保证确定性，同 FloatingBall 惯例）。

定位（bounds：与悬浮球刚体关联）
================================
- 面板不自由拖拽、不持久化位置：主窗显示前调 :meth:`anchor_to` 贴球外侧
  展开（球在屏幕工作区左半 → 面板去右侧，右半 → 左侧；趋势弹窗已占用
  优先边时自动翻边避让），球移动时主窗再次调用即可跟随；
- 全程经安全矩形 A 夹取（A = 面板所在屏工作区，内缩 8px；放不下时退回
  未内缩兜底；契约同 FloatingBall 桌面扩围——球可拖到桌面任意处，面板
  跟到哪里都只在屏内夹取），面板绝不越出屏幕；宿主 move/resize 后越界自动夹回；
- 模态弹窗（如退出确认）前经 ``suspend_for_modal`` 让位，取消/结束后按
  原状态恢复或放弃恢复；「– 收起」为本次会话隐藏，自动显隐逻辑尊重之。
"""
from __future__ import annotations

import math

from PyQt5.QtCore import QPoint, QRect, Qt, QParallelAnimationGroup, QPropertyAnimation, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QToolTip, QWidget

from ui.theme import Theme
from ui.widgets.live_overview_popup import max_temp_level
from utils.config_io import ConfigIO

# ---------------------------------------------------------------------------
#  布局常量（像素；B3 起面板宽度/透明度走 live_monitor 配置，其余保持固定）
# ---------------------------------------------------------------------------
PANEL_WIDTH = 248           # 面板总宽
CARD_RADIUS = 10            # 卡体圆角半径
PAD_X = 12                  # 卡体水平内边距
PAD_TOP = 10                # 顶部内边距
PAD_BOTTOM = 12             # 底部内边距
TITLE_H = 26                # 标题行高
STATUS_H = 16               # 采样状态行高
STATUS_GAP = 4              # 状态行与标题行的间距
DIVIDER_PAD = 8             # 分隔线上下留白
ROW_TEXT_H = 24             # 通道行文字区高
BAR_H = 6                   # 进度条高（胶囊形）
BAR_GAP = 5                 # 文字区与进度条间距
ROW_BOTTOM_PAD = 9          # 行内底部余量
ROW_H = ROW_TEXT_H + BAR_GAP + BAR_H + ROW_BOTTOM_PAD   # 单行高 = 44
ROW_SPACING = 4             # 行间距
DOT_D = 6                   # 通道色圆点直径
DOT_GAP = 7                 # 圆点与名称间距
VALUE_W = 76                # 温度数值区预留宽（等宽字体下 -888.8℃ 不溢出）
CLOSE_SIZE = 18             # 标题行右上角三枚小按钮（皮肤/收起/关闭）的边长（px）
BTN_GAP = 4                 # 按钮间距（px）
TITLE_BTNS = ("skin", "min", "close")   # 从左到右：◐ 皮肤切换 / – 收起 / × 关闭

# ---------------------------------------------------------------------------
#  动效常量
# ---------------------------------------------------------------------------
SMOOTH_INTERVAL_MS = 33     # 动画帧间隔 ≈ 30fps
SMOOTH_GAIN = 0.30          # 每 tick 向目标值的逼近比例（30fps 下约 100ms 收敛九成）
SMOOTH_SNAP = 0.05          # 差值小于该阈值（℃）直接贴合，避免无限逼近
COLOR_TRANSITION_MS = 200   # 档位变色渐变时长
TEXT_CACHE_EPS = 0.05       # 温度文本重建阈值（℃），低于它不重建字符串

# 内部档位：无效通道（max_temp_level 的 None 输出映射为它，便于统一查表）
LEVEL_NONE = "none"

# ---------------------------------------------------------------------------
#  卡片皮肤（深浅两套固定骨架色板，用户手动二选；借鉴 LiteMonitor 深灰卡与
#  趋势弹窗浅色卡先例——弹窗/球模块同样持有模块级固定色常量。骨架与场景
#  主题解耦：四档主题下观感恒定；状态色（琥珀/红）仍走 Theme 语义色保持
#  全应用一致。色板仅含中性骨架：底/边/字/灰/条槽透明度。
# ---------------------------------------------------------------------------
PANEL_THEME_DEFAULT = "light"
PANEL_PALETTES = {
    "light": {
        "card": "#eef1f4",     # 冷灰白卡体
        "border": "#c9d0d7",   # 中灰描边
        "text": "#1f2a37",     # 墨色正文
        "muted": "#6b7683",    # 辅助灰
        "track_alpha": 26,     # 条槽 = 正文色低透明度
    },
    "dark": {
        "card": "#262b31",     # 工业深灰卡体（非纯黑）
        "border": "#3d444c",   # 提亮描边
        "text": "#f2f5f7",     # 柔白正文
        "muted": "#99a3ad",    # 辅助灰
        "track_alpha": 42,     # 条槽 = 正文色低透明度（深底需更高才可见）
    },
}


def compute_panel_layout(width, row_count, has_status):
    """计算面板内容布局矩形（纯函数，便于单测）。

    返回 dict：
    - ``title``：标题行矩形；
    - ``btn_close``/``btn_min``/``btn_skin``：标题行右上角三枚按钮矩形
      （× 关闭最右、– 收起居中、◐ 皮肤最左，符合 Windows 按钮惯例）；
    - ``status``：采样状态行矩形（``has_status=False`` 时为 None）；
    - ``divider_y``：分隔线 Y 坐标；
    - ``rows``：每通道行矩形组列表，元素为 dict(row/dot/name/value/bar)；
    - ``height``：面板总高（含底部内边距）。
    """
    inner_w = width - PAD_X * 2
    y = PAD_TOP
    title = QRect(PAD_X, y, inner_w, TITLE_H)
    btn_y = y + (TITLE_H - CLOSE_SIZE) // 2
    btn_close = QRect(PAD_X + inner_w - CLOSE_SIZE, btn_y,
                      CLOSE_SIZE, CLOSE_SIZE)
    btn_min = QRect(btn_close.left() - BTN_GAP - CLOSE_SIZE, btn_y,
                    CLOSE_SIZE, CLOSE_SIZE)
    btn_skin = QRect(btn_min.left() - BTN_GAP - CLOSE_SIZE, btn_y,
                     CLOSE_SIZE, CLOSE_SIZE)
    y += TITLE_H
    status = None
    if has_status:
        status = QRect(PAD_X, y, inner_w, STATUS_H)
        y += STATUS_H + STATUS_GAP
    y += DIVIDER_PAD
    divider_y = y
    y += 1 + DIVIDER_PAD

    name_x = PAD_X + DOT_D + DOT_GAP
    name_w = inner_w - (DOT_D + DOT_GAP) - VALUE_W
    rows = []
    for _ in range(max(0, int(row_count))):
        row = QRect(PAD_X, y, inner_w, ROW_H)
        dot = QRect(PAD_X, y + (ROW_TEXT_H - DOT_D) // 2, DOT_D, DOT_D)
        name = QRect(name_x, y, name_w, ROW_TEXT_H)
        value = QRect(PAD_X, y, inner_w, ROW_TEXT_H)
        bar = QRect(PAD_X, y + ROW_TEXT_H + BAR_GAP, inner_w, BAR_H)
        rows.append({"row": row, "dot": dot, "name": name,
                     "value": value, "bar": bar})
        y += ROW_H + ROW_SPACING
    if rows:
        y -= ROW_SPACING
    y += PAD_BOTTOM
    return {"title": title, "btn_close": btn_close, "btn_min": btn_min,
            "btn_skin": btn_skin, "status": status,
            "divider_y": divider_y, "rows": rows, "height": y}


def _lerp_color(c_from, c_to, t):
    """QColor 线性插值；端点为 None 或 t>=1 时直接回退目标色（不崩即可）。"""
    if c_to is None:
        return None if t >= 1.0 else c_from
    if c_from is None or t >= 1.0:
        return QColor(c_to)
    r = round(c_from.red() + (c_to.red() - c_from.red()) * t)
    g = round(c_from.green() + (c_to.green() - c_from.green()) * t)
    b = round(c_from.blue() + (c_to.blue() - c_from.blue()) * t)
    return QColor(r, g, b)


class _RowState:
    """单通道行的渲染状态（含动画缓存）。

    ``set_channels`` 按稳定键 diff：key 相同的行跨刷新复用本对象，
    数值滚动与颜色过渡的进度因此不因数据帧刷新而丢失。
    """

    __slots__ = ("key", "name", "color", "target", "display", "level",
                 "bar_from", "bar_to", "text_from", "text_to", "color_t",
                 "value_text", "peak")

    def __init__(self, key):
        self.key = key
        self.name = key
        self.color = Theme.ACCENT
        self.target = None        # 真实最新值 float | None（None=无效通道）
        self.display = None       # 动画显示值（平滑动画的当前值）
        self.level = LEVEL_NONE   # 当前档位 none/normal/warn/alarm
        self.bar_from = None      # 进度条填充色过渡起点
        self.bar_to = None        # 进度条填充色过渡终点
        self.text_from = None     # 温度文字色过渡起点
        self.text_to = None       # 温度文字色过渡终点
        self.color_t = 1.0        # 颜色过渡进度 0→1
        self.value_text = ""      # 格式化文本缓存（热路径零分配）
        self.peak = None          # 会话峰值 float | None（None=无有效值，tooltip 用）


class LiveChannelPanel(QWidget):
    """全通道悬浮面板：名 + 温 + 胶囊条的桌面悬浮窗（与悬浮球刚体关联）。

    数据经 :meth:`set_channels` 注入（主窗在实时刷新链路调用，测试可直接喂
    假数据）；量程与琥珀档经 :meth:`set_range` 注入；本类不读配置文件
    （皮肤初始值除外，构造期自读 panel_theme/panel_alpha）。

    与悬浮球的关联：主窗在显示前调 :meth:`anchor_to` 贴球外侧展开（智能
    选边并自动避让趋势弹窗），球移动时再次调用即可跟随；面板自身不可自由
    拖拽，位置完全由球决定。

    标题行右上角三按钮：◐ 皮肤切换（发 :attr:`skinChanged` 由主窗持久化）、
    – 收起（:meth:`collapse`，仅本次会话隐藏，不动配置）、× 关闭（发
    :attr:`closeRequested` 由主窗落盘开关并隐藏）。找回入口统一在悬浮球
    右键菜单与设置页。
    """

    closeRequested = pyqtSignal()
    skinChanged = pyqtSignal(str)   # 参数：切换后的皮肤名 "light"/"dark"

    ANIMATE = True   # 测试可置 False：数值直接贴合、颜色直接切换，保证确定性

    # —— 位置约束（贴球刚体关联；契约同 FloatingBall 的安全矩形 A）——
    SAFE_INSET = 8              # 安全区 A 的内缩边距 e（px）
    CORNER_GAP = 8              # 面板与球边缘的间隙（px）
    FADE_IN_MS = 200            # 显示淡入 + 上滑动画时长
    SLIDE_IN_PX = 12            # 淡入时上滑距离

    def __init__(self, parent=None):
        super().__init__(None)
        self.setObjectName("liveChannelPanel")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)     # hover 行高亮/tooltip 需要无按键移动事件

        self._high = 100.0          # 报警上限（进度条量程终点，与曲线报警同源）
        self._warn_pct = 90         # 琥珀档百分比（0 = 不启用，与球心同契约）
        self._title = "全部通道"
        self._sample_text = ""      # 状态行文本；空串 = 不画状态行
        self._rows = []             # list[_RowState]，顺序即显示顺序
        self._row_by_key = {}
        self._geom = None           # compute_panel_layout 缓存
        self._theme = {}            # apply_theme 解析出的颜色表
        self._fonts = {}            # apply_theme 构建的字体表
        self._panel_theme = PANEL_THEME_DEFAULT   # 卡片皮肤 light/dark

        self._timer = QTimer(self)
        self._timer.setInterval(SMOOTH_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

        # —— 宿主 / 让位状态 ——
        self._host = None           # 宿主主窗口（安全矩形参考系），由主窗注入
        self._hover_key = None      # hover 行（高亮 + tooltip）
        self._btn_hover = None      # 标题按钮 hover（"skin"/"min"/"close"/None）
        self._btn_pressed = None    # 标题按钮按下（press/release 同域才触发）
        self._collapsed = False     # 本次会话收起标志（– 按钮；不动配置）
        self._suspend_visible = False   # 模态让位前的可见状态
        self._show_anim = None      # 淡入动画组（防被 GC）
        self._alpha = 1.0           # 目标不透明度（set_panel_alpha 记录）

        self.apply_theme()
        # 构造期自读持久化外观（与 FloatingBall 自读 ball_size/alpha 同惯例；
        # 运行期变更由主窗 _apply_live_monitor_cfg 下发，热更新即时生效）
        try:
            cfg = ConfigIO.load_live_monitor_config()
            self.set_panel_alpha(cfg.get("panel_alpha", 0.85))
            self.set_panel_theme(cfg.get("panel_theme", PANEL_THEME_DEFAULT))
        except Exception:
            self.set_panel_alpha(0.85)
        # 构造期先建一次空布局：保证 width/height 是真实面板尺寸（默认 640×480
        # 会让 restore_initial/_default_pos 的越界计算按错误大小得出错误位置）
        self._rebuild()

    # ------------------------------------------------------------------
    #  数据注入（主窗 / 测试）
    # ------------------------------------------------------------------
    def set_range(self, high, warn_pct):
        """注入进度条量程终点（报警上限）与琥珀档百分比；语义与球心一致。"""
        try:
            high = float(high)
        except (TypeError, ValueError):
            high = 0.0
        self._high = max(0.0, high)
        try:
            self._warn_pct = int(warn_pct)
        except (TypeError, ValueError):
            self._warn_pct = 0
        # 量程变化影响所有行的档位与条宽，按新量程重判档位
        for row in self._rows:
            self._apply_value(row, row.target)
        self._ensure_timer()
        self.update()

    def set_sample_info(self, points, time_str=""):
        """更新状态行（采样点数 + 最新时刻）；points=None 隐藏状态行。"""
        if points is None:
            text = ""
        else:
            text = f"采样 {points} 点"
            if time_str:
                text = f"{text} · {time_str}"
        if text == self._sample_text:
            return
        had_status = bool(self._sample_text)
        self._sample_text = text
        if had_status != bool(text):
            self._rebuild()      # 状态行出现/消失影响总高
        else:
            self.update()

    def set_channels(self, items):
        """注入通道行数据并按稳定键 diff。

        ``items`` 元素为 dict：``key``（稳定键，决定行身份）、``name``（显示名）、
        ``color``（#rrggbb 通道色）、``value``（最新温度 float|None，None=无效）。
        行集合变化才重排；名称/颜色/值变化只更新对应行状态。
        """
        items = list(items or [])
        new_keys = [str(it.get("key")) for it in items]
        old_keys = [r.key for r in self._rows]
        structure_changed = new_keys != old_keys

        if structure_changed:
            old = self._row_by_key
            self._rows = []
            self._row_by_key = {}
            for it in items:
                key = str(it.get("key"))
                row = old.get(key) or _RowState(key)
                self._rows.append(row)
                self._row_by_key[key] = row

        for it, row in zip(items, self._rows):
            name = str(it.get("name") or row.key)
            color = str(it.get("color") or Theme.ACCENT)
            value = it.get("value")
            try:
                value = None if value is None else float(value)
            except (TypeError, ValueError):
                value = None
            peak = it.get("peak")
            try:
                peak = None if peak is None else float(peak)
            except (TypeError, ValueError):
                peak = None
            if peak is not None and not math.isfinite(peak):
                peak = None           # 会话尚无有效值（-inf）→ 视为无峰值
            row.peak = peak
            row.name = name
            if color != row.color:
                row.color = color
                if row.level == "normal":
                    # normal 档填充色跟随通道色，直接切换（不做过渡，克制）
                    row.bar_to = self._target_bar_color(row)
                    row.color_t = 1.0
            self._apply_value(row, value)

        if structure_changed:
            self._rebuild()
            self._ensure_timer()   # 行增删同帧的档位过渡不冻结到下个 flush
        else:
            self._ensure_timer()
            self.update()

    def clear_channels(self):
        """清空全部通道行（回到仅标题的空面板）。"""
        if not self._rows:
            return
        self._rows = []
        self._row_by_key = {}
        self._rebuild()

    # ------------------------------------------------------------------
    #  外观
    # ------------------------------------------------------------------
    def set_panel_alpha(self, alpha):
        """整窗不透明度（0.0 全透明 ~ 1.0 不透明），与悬浮卡契约一致。"""
        try:
            alpha = float(alpha)
        except (TypeError, ValueError):
            alpha = 1.0
        self._alpha = max(0.0, min(1.0, alpha))
        self.setWindowOpacity(self._alpha)

    def set_panel_theme(self, name):
        """切换卡片皮肤（light 浅色卡 / dark 深色卡）；非法回退默认浅色。

        仅骨架色（底/边/字/灰/条槽）随皮肤切换，琥珀/红状态色仍走 Theme
        语义色；切换即重绘（颜色过渡动画只作用于档位，不做皮肤渐变）。
        """
        if name not in PANEL_PALETTES:
            name = PANEL_THEME_DEFAULT
        changed = name != self._panel_theme
        self._panel_theme = name
        if changed:
            self.apply_theme()
        self.update()

    # ------------------------------------------------------------------
    #  宿主与安全矩形（bounds，契约同 FloatingBall；面板仅 free 单态）
    # ------------------------------------------------------------------
    def set_host(self, host) -> None:
        """注入宿主主窗口：仅作面板中心落在屏外死区时的屏幕兜底参考。"""
        self._host = host

    def _host_screen(self):
        """宿主当前所在屏幕（面板中心不在任何屏时的兜底；无宿主取主屏）。"""
        mw = self._host
        if mw is None:
            return QApplication.primaryScreen()
        sc = QApplication.screenAt(mw.frameGeometry().center())
        return sc or QApplication.primaryScreen()

    def safe_rect(self) -> QRect:
        """安全矩形 A = 面板所在屏工作区内缩 e（契约同 FloatingBall 桌面扩围）。

        面板贴球展开：球可在整个桌面自由拖动，夹取参考系同步改为所在屏
        工作区（球到哪屏面板就能跟到哪屏，不再被宿主可视区裁剪）；中心
        落在屏外死区时退回宿主所在屏，A 放不下整面板时退回未内缩工作区。
        """
        w, h = max(self.width(), 1), max(self.height(), 1)
        sc = (QApplication.screenAt(self.frameGeometry().center())
              or self._host_screen())
        if sc is None:
            return QRect(0, 0, w * 2, h * 2)
        base = sc.availableGeometry()
        A = base.adjusted(self.SAFE_INSET, self.SAFE_INSET,
                          -self.SAFE_INSET, -self.SAFE_INSET)
        if A.width() < w or A.height() < h:
            A = base
        return A

    def _clamped_pos(self, target: QPoint) -> QPoint:
        """把目标左上角夹取进安全矩形 A（整面板可见，永不越出）。"""
        A = self.safe_rect()
        w, h = self.width(), self.height()
        x = max(A.left(), min(int(target.x()), A.right() - w))
        y = max(A.top(), min(int(target.y()), A.bottom() - h))
        return QPoint(x, y)

    def anchor_to(self, anchor_widget, avoid=None) -> None:
        """贴锚点（悬浮球）外侧展开：与球刚体关联的唯一定位方式。

        水平智能选边：锚点中心在安全矩形左半 → 面板贴其右侧；否则贴其
        左侧（与趋势弹窗同思路，屏幕边缘自动翻边）。``avoid`` 传入趋势
        弹窗等已占用的顶层窗口：其可见且占住优先边时面板自动去另一侧。
        垂直顶对齐锚点顶部；最终位置经安全矩形夹取，绝不越出屏幕。
        """
        if anchor_widget is None:
            return
        ag = anchor_widget.frameGeometry()
        anchor_center_x = ag.center().x()
        A = self.safe_rect()
        prefer_right = anchor_center_x < A.center().x()   # 球在左半 → 面板去右侧
        if avoid is not None and avoid.isVisible():
            aw = avoid.frameGeometry()
            if prefer_right and aw.center().x() >= anchor_center_x:
                prefer_right = False        # 优先边被趋势弹窗占用 → 翻边
            elif not prefer_right and aw.center().x() <= anchor_center_x:
                prefer_right = True
        gap = self.CORNER_GAP
        if prefer_right:
            x = ag.right() + 1 + gap
        else:
            x = ag.left() - gap - self.width()
        y = ag.top()
        self.move(self._clamped_pos(QPoint(int(x), int(y))))

    def on_host_geometry_changed(self) -> None:
        """宿主 move/resize/跨屏后校正：面板在屏内越界时夹回（不持久化）。"""
        if self._host is None or not self.isVisible():
            return
        if self._clamped_pos(self.pos()) != self.pos():
            self.move(self._clamped_pos(self.pos()))

    # ------------------------------------------------------------------
    #  显示/隐藏（淡入动画）、收起与模态让位
    # ------------------------------------------------------------------
    def show_panel(self, animate=None) -> None:
        """显示面板（默认 200ms 淡入 + 上滑；ANIMATE=False/animate=False 直接显示）。

        显示会清除「– 收起」标志（收起态被任意入口重新显示即恢复正常跟随）；
        位置由主窗先经 :meth:`anchor_to` 贴球决定，本方法不管定位。
        """
        use_anim = self.ANIMATE if animate is None else bool(animate)
        self._stop_show_anim()
        self._collapsed = False
        self.show()
        if not use_anim or not self.isVisible():
            return
        dest = self.pos()
        self.move(dest + QPoint(0, self.SLIDE_IN_PX))
        group = QParallelAnimationGroup(self)
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(self.FADE_IN_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(self._alpha)
        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(self.FADE_IN_MS)
        slide.setStartValue(self.pos())
        slide.setEndValue(dest)
        group.addAnimation(fade)
        group.addAnimation(slide)
        self._show_anim = group
        group.start()

    def _stop_show_anim(self) -> None:
        if self._show_anim is not None:
            self._show_anim.stop()
            self._show_anim = None

    def hide_panel(self) -> None:
        """隐藏面板（克制：不做淡出，直接隐藏；按钮中间态一并复位）。"""
        self._stop_show_anim()
        self._btn_hover = None
        self._btn_pressed = None
        self.hide()

    def collapse(self) -> None:
        """本次会话收起（– 按钮）：仅隐藏，不动配置；球右键/设置页可再显示。"""
        self._collapsed = True
        self.hide_panel()

    def is_collapsed(self) -> bool:
        """是否处于本次会话收起态（主窗自动显隐逻辑须尊重它）。"""
        return self._collapsed

    def suspend_for_modal(self) -> None:
        """模态弹窗前让位：记住可见性并隐藏（置顶窗可能盖住模态框）。

        让位期间（``is_suspended()`` 为 True）主窗的
        ``_refresh_channel_panel_visibility`` 直接让位，高频数据刷新
        不会把面板在模态框期间重新弹出。
        """
        self._suspend_visible = self.isVisible()
        if self._suspend_visible:
            self.hide()

    def is_suspended(self) -> bool:
        """是否处于模态让位中（restore/cancel 前为 True）。"""
        return self._suspend_visible

    def restore_after_modal(self) -> None:
        """模态取消/结束：按让位前状态恢复显示。"""
        if self._suspend_visible:
            self.show()
        self._suspend_visible = False

    def cancel_suspend(self) -> None:
        """即将真退出：放弃恢复。"""
        self._suspend_visible = False

    # ------------------------------------------------------------------
    #  拖拽与 hover
    # ------------------------------------------------------------------
    def _row_at(self, pos):
        """命中测试：pos 落在哪一行；不落任何行返回 None。"""
        if self._geom is None:
            return None
        for row, rects in zip(self._rows, self._geom["rows"]):
            if rects["row"].contains(pos):
                return row
        return None

    def _row_tooltip(self, row) -> str:
        """hover 提示：名称 + 当前值 + 会话峰值（无数据说明原因）。"""
        lines = [row.name]
        if row.target is None:
            lines.append("无数据 / 通道关闭")
        else:
            lines.append(f"当前 {row.value_text} ℃")
            if row.peak is not None:
                lines.append(f"峰值 {row.peak:.1f} ℃")
        return "\n".join(lines)

    def _button_at(self, pos):
        """命中测试：pos 落在哪枚标题按钮上；未命中返回 None。"""
        if self._geom is None:
            return None
        for name in TITLE_BTNS:
            if self._geom[f"btn_{name}"].contains(pos):
                return name
        return None

    def _trigger_button(self, name):
        """执行标题按钮动作（press/release 同域命中后调用，测试可直接驱动）。"""
        if name == "skin":
            new_name = ("dark" if self._panel_theme == "light"
                        else "light")
            self.set_panel_theme(new_name)
            self.skinChanged.emit(new_name)
        elif name == "min":
            self.collapse()
        elif name == "close":
            self.closeRequested.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            hit = self._button_at(event.pos())
            if hit is not None:
                # 按钮命中：不启动任何拖拽行为（面板不可自由拖拽），等 release
                self._btn_pressed = hit
                self._btn_hover = hit
                self.update()
                event.accept()
                return
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # hover：标题按钮 + 行高亮 + tooltip（setMouseTracking 开启后无按键也会来）
        hit = self._button_at(event.pos())
        if hit != self._btn_hover:
            self._btn_hover = hit
            self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            self.update()
        if self._btn_pressed is not None and hit != self._btn_pressed:
            # 按住移出按钮域 → 取消按压（松手不触发）
            self._btn_pressed = None
            self.update()
        row = self._row_at(event.pos())
        if row is not None:
            QToolTip.showText(event.globalPos(), self._row_tooltip(row), self)
        else:
            QToolTip.hideText()
        hover_key = row.key if row is not None else None
        if hover_key != self._hover_key:
            self._hover_key = hover_key
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            pressed = self._btn_pressed
            self._btn_pressed = None
            hit = self._button_at(event.pos())
            if pressed is not None and hit == pressed:
                self._trigger_button(pressed)
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        QToolTip.hideText()
        if self._btn_hover is not None:
            self._btn_hover = None
            self.setCursor(Qt.ArrowCursor)
            self.update()
        if self._hover_key is not None:
            self._hover_key = None
            self.update()
        super().leaveEvent(event)

    def apply_theme(self):
        """按当前卡片皮肤重建骨架色板 + 解析语义状态色 + 重建字体。

        骨架（底/边/字/灰/条槽）取自 PANEL_PALETTES 当前皮肤，与场景主题
        解耦；琥珀/红取 Theme 语义色（随场景主题联动，深浅卡上均可读）。
        由主窗主题切换链与 set_panel_theme 调用。
        """
        pal = PANEL_PALETTES.get(self._panel_theme,
                                 PANEL_PALETTES[PANEL_THEME_DEFAULT])
        text = QColor(pal["text"])
        border = QColor(pal["border"])
        self._theme = {
            "card": QColor(pal["card"]),
            "border": border,
            "border_soft": QColor(border.red(), border.green(), border.blue(), 110),
            "bar_track": QColor(text.red(), text.green(), text.blue(),
                                pal["track_alpha"]),
            "text": text,
            "muted": QColor(pal["muted"]),
            "orange": QColor(Theme.ORANGE),
            "red": QColor(Theme.RED),
        }
        text_family = Theme.FONT_ROLES.get("text", "Microsoft YaHei")
        ui_family = Theme.FONT_ROLES.get("ui", "Segoe UI")
        mono_family = Theme.FONT_ROLES.get("mono", "Consolas")
        title_font = QFont(text_family)
        title_font.setPixelSize(14)
        title_font.setBold(True)
        status_font = QFont(ui_family)
        status_font.setPixelSize(11)
        name_font = QFont(text_family)
        name_font.setPixelSize(12)
        value_font = QFont(mono_family)
        value_font.setPixelSize(13)
        value_font.setBold(True)
        unit_font = QFont(text_family)
        unit_font.setPixelSize(10)
        self._fonts = {
            "title": title_font, "status": status_font, "name": name_font,
            "value": value_font, "unit": unit_font,
        }
        self.update()

    # ------------------------------------------------------------------
    #  行状态与动画
    # ------------------------------------------------------------------
    def _target_bar_color(self, row):
        """按档位查进度条填充目标色：normal=通道色 / warn 橙 / alarm 红 / 无效 None。"""
        if row.level == "alarm":
            return self._theme["red"]
        if row.level == "warn":
            return self._theme["orange"]
        if row.level == LEVEL_NONE:
            return None
        return QColor(row.color)

    def _target_text_color(self, row):
        """按档位查温度文字目标色：normal 用正文色，warn/alarm 跟档位，无效用灰。"""
        if row.level == "alarm":
            return self._theme["red"]
        if row.level == "warn":
            return self._theme["orange"]
        if row.level == LEVEL_NONE:
            return self._theme["muted"]
        return self._theme["text"]

    def _current_row_colors(self, row):
        """过渡插值后的当前 (填充色, 文字色)。"""
        bar_to = self._target_bar_color(row)
        text_to = self._target_text_color(row)
        if row.color_t >= 1.0 or row.bar_from is None or row.text_from is None:
            return bar_to, text_to
        return (_lerp_color(row.bar_from, bar_to, row.color_t),
                _lerp_color(row.text_from, text_to, row.color_t))

    def _apply_value(self, row, value):
        """更新一行目标值：首值直接贴合、档位变化启动颜色过渡、刷新文本缓存。"""
        row.target = value
        if value is None:
            level = LEVEL_NONE
            # 无效化复位显示值：恢复有效后从新值直接贴合开始，而不是从
            # 上一次有效旧值长距离滚动（违背「首值贴合、无假动画」意图）
            row.display = None
        else:
            level = max_temp_level(value, self._warn_pct, self._high)
        if value is not None and row.display is None:
            # 首值不从 0 滚上来（避免开屏假动画）
            row.display = value
        if level != row.level:
            bar_cur, text_cur = self._current_row_colors(row)
            row.level = level
            row.bar_from = bar_cur
            row.text_from = text_cur
            row.bar_to = self._target_bar_color(row)
            row.text_to = self._target_text_color(row)
            row.color_t = 1.0 if not self.ANIMATE else 0.0
        if not self.ANIMATE:
            row.display = row.target
            row.color_t = 1.0
        self._refresh_row_text(row)

    def _refresh_row_text(self, row):
        """刷新格式化文本缓存（球心同款格式：|v|<100 一位小数，否则取整）。"""
        if row.target is None:
            row.value_text = "--"
            return
        v = row.display if row.display is not None else row.target
        text = f"{v:.1f}" if abs(v) < 100 else f"{v:.0f}"
        if text != row.value_text:
            row.value_text = text

    def _has_active_animation(self):
        """是否存在未完成的数值滚动或颜色过渡。"""
        for row in self._rows:
            if row.color_t < 1.0:
                return True
            if (row.target is not None and row.display is not None
                    and abs(row.target - row.display) >= SMOOTH_SNAP):
                return True
        return False

    def _ensure_timer(self):
        """有活动动画才启动帧时钟；全部到位自动停表（不空转耗电）。"""
        if not self.ANIMATE:
            self._timer.stop()
            return
        if self._has_active_animation():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def _tick(self):
        """帧推进：数值插值 + 颜色过渡 + 文本缓存刷新；全部到位即停表。"""
        for row in self._rows:
            if row.target is not None and row.display is not None:
                diff = row.target - row.display
                if abs(diff) < SMOOTH_SNAP:
                    row.display = row.target
                else:
                    row.display += diff * SMOOTH_GAIN
            if row.color_t < 1.0:
                row.color_t = min(1.0, row.color_t
                                  + SMOOTH_INTERVAL_MS / COLOR_TRANSITION_MS)
            self._refresh_row_text(row)
        if not self._has_active_animation():
            self._timer.stop()
        self.update()

    # ------------------------------------------------------------------
    #  布局
    # ------------------------------------------------------------------
    def _rebuild(self):
        """结构变化后重算布局缓存并贴合窗口尺寸（仅通道增删/状态行增减时）。"""
        self._geom = compute_panel_layout(
            PANEL_WIDTH, len(self._rows), bool(self._sample_text))
        self.setFixedSize(PANEL_WIDTH, self._geom["height"])
        self.update()

    # ------------------------------------------------------------------
    #  渲染
    # ------------------------------------------------------------------
    def paintEvent(self, _event):
        """全量重绘：卡体 → 标题 → 状态行 → 分隔线 → 通道行（零测量零数学）。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        if self._geom is None:
            self._geom = compute_panel_layout(
                PANEL_WIDTH, len(self._rows), bool(self._sample_text))
            self.setFixedSize(PANEL_WIDTH, self._geom["height"])
        t = self._theme
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(t["border"], 1))
        painter.setBrush(t["card"])
        painter.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)

        painter.setFont(self._fonts["title"])
        painter.setPen(t["text"])
        title_text_rect = self._geom["title"].adjusted(
            0, 0, -(len(TITLE_BTNS) * CLOSE_SIZE + (len(TITLE_BTNS) - 1) * BTN_GAP + 4), 0)
        painter.drawText(title_text_rect,
                         Qt.AlignLeft | Qt.AlignVCenter, self._title)
        self._paint_title_buttons(painter)

        if self._geom["status"] is not None and self._sample_text:
            painter.setFont(self._fonts["status"])
            painter.setPen(t["muted"])
            painter.drawText(self._geom["status"],
                             Qt.AlignLeft | Qt.AlignVCenter, self._sample_text)

        dy = self._geom["divider_y"]
        painter.setPen(QPen(t["border_soft"], 1))
        painter.drawLine(PAD_X, dy, PANEL_WIDTH - PAD_X, dy)

        for row, rects in zip(self._rows, self._geom["rows"]):
            self._paint_row(painter, row, rects)
        painter.end()

    def _paint_title_buttons(self, painter):
        """标题行右上角三枚按钮：◐ 皮肤 / – 收起 / × 关闭（hover 淡底 + 加深）。"""
        if self._geom is None:
            return
        t = self._theme
        for name in TITLE_BTNS:
            rect = self._geom[f"btn_{name}"]
            hovered = self._btn_hover == name
            pressed = self._btn_pressed == name
            if hovered:
                halo = QColor(t["text"])
                halo.setAlpha(30 if pressed else 18)
                painter.setPen(Qt.NoPen)
                painter.setBrush(halo)
                painter.drawEllipse(rect)
            ink = QColor(t["text"] if hovered else t["muted"])
            cx, cy = rect.center().x(), rect.center().y()
            r = rect.width() * 0.28
            pen = QPen(ink, 1.6)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            if name == "close":
                painter.drawLine(int(cx - r), int(cy - r),
                                 int(cx + r), int(cy + r))
                painter.drawLine(int(cx - r), int(cy + r),
                                 int(cx + r), int(cy - r))
            elif name == "min":
                painter.drawLine(int(cx - r), int(cy), int(cx + r), int(cy))
            else:   # skin：◐ 半月形（左半实心，示意深浅切换）
                painter.drawEllipse(rect.adjusted(3, 3, -3, -3))
                half = rect.adjusted(3, 3, -3, -3)
                painter.setBrush(ink)
                painter.drawPie(half, 90 * 16, 180 * 16)

    def _paint_row(self, painter, row, rects):
        """绘制单通道行：hover 高亮 + 圆点 + 名称 + 温度 + 胶囊条。"""
        t = self._theme
        bar_color, text_color = self._current_row_colors(row)

        # hover 行高亮（文字色 8% 透明度的圆角底）
        if row.key == self._hover_key:
            hl = QColor(t["text"])
            hl.setAlpha(20)
            painter.setPen(Qt.NoPen)
            painter.setBrush(hl)
            painter.drawRoundedRect(rects["row"].adjusted(0, 0, -1, -1), 6, 6)

        # 通道色圆点
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(row.color))
        painter.drawEllipse(rects["dot"])

        # 名称（超长省略）
        fm = QFontMetrics(self._fonts["name"])
        elided = fm.elidedText(row.name, Qt.ElideRight, rects["name"].width())
        painter.setFont(self._fonts["name"])
        painter.setPen(t["text"])
        painter.drawText(rects["name"], Qt.AlignLeft | Qt.AlignVCenter, elided)

        # 温度（等宽数字 + 小号 ℃ 单位，右对齐组合排布）
        painter.setPen(text_color if text_color is not None else t["muted"])
        if row.target is None:
            painter.setFont(self._fonts["name"])
            painter.drawText(rects["value"],
                             Qt.AlignRight | Qt.AlignVCenter, row.value_text)
        else:
            unit_fm = QFontMetrics(self._fonts["unit"])
            unit_w = unit_fm.horizontalAdvance("℃")
            num_rect = rects["value"].adjusted(0, 0, -(unit_w + 2), 0)
            painter.setFont(self._fonts["value"])
            painter.drawText(num_rect, Qt.AlignRight | Qt.AlignVCenter,
                             row.value_text)
            painter.setFont(self._fonts["unit"])
            painter.setPen(t["muted"])
            painter.drawText(rects["value"], Qt.AlignRight | Qt.AlignVCenter,
                             "℃")

        # 胶囊条：底槽恒画；有效值按显示值比例画填充（最小一个圆头宽）
        bar = rects["bar"]
        painter.setPen(Qt.NoPen)
        painter.setBrush(t["bar_track"])
        painter.drawRoundedRect(bar, BAR_H // 2, BAR_H // 2)
        if row.display is not None and bar_color is not None and self._high > 0:
            pct = max(0.0, min(1.0, float(row.display) / float(self._high)))
            w = int(bar.width() * pct)
            if w > 0:
                fill = QRect(bar.left(), bar.top(), max(w, BAR_H), BAR_H)
                if fill.right() > bar.right():
                    fill.setWidth(bar.width())
                painter.setBrush(bar_color)
                painter.drawRoundedRect(fill, BAR_H // 2, BAR_H // 2)
