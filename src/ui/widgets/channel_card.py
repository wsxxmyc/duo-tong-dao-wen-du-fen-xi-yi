# -*- coding: utf-8 -*-
"""
channel_card — 单通道卡片，同一文件内含两种可切换风格：

- ChannelCard（table，「卡表融合」）：46px 紧凑行卡（当前默认）。
  坐标固化 + 多行数值竖线对齐，与面板顶部常驻表头行（名称 / 最高 /
  实时温度）组成表格式列表：

    ┌──────────────────────────────────────────────────┐
    │ ▎ [开关]  通道名称                 85.2     24.0  │
    └──────────────────────────────────────────────────┘
      ↑ 左缘 3px 通道色带（垂直居中）

  - 左缘固定 3px 色带：通道曲线色纯展示（颜色由设置页配色方案统一决定，
    不支持单独改色），其后依次为显隐开关、通道名称纯文本。
  - 「最高」「实时温度」为固定宽右对齐数值列：列宽常量 HI_WIDTH /
    TEMP_WIDTH 与行卡水平边距 MARGIN 是行卡与表头的唯一对齐事实源
    （ChannelPanel._TableHeader 直接 import 复用），名称变长 / 缩短
    不挪位，多行卡数值同竖线、与表头列一一对齐。
  - 实时温度不带 ℃ 后缀 —— 表头已注明「实时温度」，单位 ℃。
  - 统计行取消：不再显示最低 / 平均，仅保留「最高」一列。
  - 开关管显隐：隐藏通道整行变暗（名称 / 最高 / 实时温度转 muted 色、
    色带转灰），恢复显示即还原。
  - 悬停 / 图表命中行高亮：行卡边框转强调色 ACCENT。

- ClassicChannelCard（classic，「经典卡片」）：批次1前的 70px 大卡片
  外观原样保留（nameTempBox 内嵌框 + ℃ 后缀 + 16×16 色块 + 统计行），
  供设置页「通道列表风格」切回；唯一行为差异是统计行不显示最低值
  （用户要求），文案为「最高 x · 平均 x」。

两种风格的卡片鸭子类型一致（visibility_toggled / hover_changed 信号 +
set_values / show_none / set_color_swatch / set_visible_state /
set_chart_active / set_hover_active / set_card_height / set_name /
current_name），由 ChannelPanel 按 view_mode 二选一构建。
数值由 ChannelPanel 定期从 Session.buffer 拉取后调用 set_values 刷新。
"""
import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPixmap
from PyQt5.QtWidgets import (
    QFrame, QLabel, QHBoxLayout, QVBoxLayout, QSizePolicy,
)

from ui.theme import Theme
from ui.widgets.toggle_switch import ToggleSwitch


# 行卡固定尺寸（需求：禁止自适应拉伸）
# One device uses one group of eight channels. The 266px width fills the
# 270px panel after its fixed 2px outer margins.
CARD_SIZE = (266, 46)

# 「实时温度」列固定宽度：不带 ℃ 后缀（表头已注明「实时温度」，单位 ℃），
# 锚定行卡右侧，不受名称长度影响
TEMP_WIDTH = 58

# 「最高」统计列固定宽度：右对齐，与表头「最高」列同竖线
HI_WIDTH = 40

# 左缘通道色带尺寸（行卡内垂直居中）
COLOR_BAR_SIZE = (3, 26)

# 行卡内边距：左右边距与表头行共用（列对齐唯一事实源，left, top, right, bottom）
MARGIN = (10, 3, 8, 3)
ROW_SPACING = 6                # 行内控件间距

# 行卡 QSS 边框宽度：QFrame 的 1px 边框会把行内布局 contentsRect 整体
# 内缩 1px（实测 layout geometry = (1,1,264,44)），行卡数值右缘因此比
# 无边框容器少 1px；表头行右缘补偿该值后才能与数值列右缘对齐
# （见 ChannelPanel._TableHeader）
CARD_BORDER_WIDTH = 1

# 左缘通道色带：通道曲线色 + 细描边（替代旧 16×16 色块，色带即颜色指示）
_COLOR_BAR_QSS = """
QFrame {{
    background: {color};
    border: 1px solid {border};
    border-radius: 2px;
}}
"""

# 名称纯文本：无边框、背景透明。
# 命名统一走面板顶部「自定义各通道名称」入口的弹窗，行卡只展示当前显示名。
# font-family 必须用 ID 选择器显式指定思源黑体链：全局 QWidget 规则会把它
# 覆盖为 Segoe UI（无中文字形 → 中文回退微软雅黑，笔画粗、小字号发糊）。
_NAME_LABEL_QSS = """
QLabel#nameLabel {{
    font-family: {text_family};
    background: transparent;
    border: none;
    padding: 2px 4px;
    color: {text};
}}
"""

# 「最高」统计列：浅灰辅助数字，固定宽右对齐。
# 用中文无衬线字体（思源黑体）渲染："最高"含笔画多的"最"字，Consolas
# 无中文字形会回退微软雅黑，小字号下发糊；思源黑体笔画均匀更清晰。
_HI_QSS = """
QLabel {{
    color: {color};
    font-size: {size}pt;
    font-family: {text_family};
    border: none;
    background: transparent;
}}
"""

# 实时温度读数：加粗、颜色跟随通道曲线色、固定宽靠右、字号 11pt 固定
# （不再取 TYPE_SCALE['numeric']，紧凑行高下 16pt 放不下；四档字号整改
#  2026-08-30：13pt 并入 11pt 档）
_TEMP_QSS = """
QLabel {{
    color: {color};
    font-size: {size}pt;
    font-weight: bold;
    font-family: {mono_font};
    border: none;
    background: transparent;
}}
"""

# 实时温度基准字号（pt）：11pt 固定，_apply_content_scale 按 _scale 折算
TEMP_FONT_SIZE = 11


class ChannelCard(QFrame):
    """单通道行卡：坐标固化 + 最高/实时温度右对齐列。"""

    visibility_toggled = pyqtSignal(int, bool)   # channel index, visible
    hover_changed = pyqtSignal(object)            # 当前通道名称，离开时为 None

    def __init__(self, ch, parent=None):
        super().__init__(parent)
        self._ch_index = ch.index
        self._channel_key = getattr(ch, "key", "")
        self._drag_start_pos = None
        self._channel_color = ch.color or Theme.TEXT
        self._scale = 1.0
        self._dimmed = False
        self._hover_active = False
        self._chart_active = False
        self.setObjectName("channelCard")
        # 固定整体尺寸：行卡宽高固化，所有行卡同宽 → 数值右锚点同一竖线
        self.setFixedSize(*CARD_SIZE)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._apply_card_style()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(*MARGIN)
        lay.setSpacing(ROW_SPACING)

        # ── 左缘通道色带（垂直居中，颜色 = 曲线色 / 隐藏时转灰）──
        self.color_bar = QFrame()
        self.color_bar.setFixedSize(*COLOR_BAR_SIZE)
        self.color_bar.setToolTip("通道曲线颜色（由设置页配色方案决定）")
        self.color_bar.setStyleSheet(self._color_bar_qss())
        lay.addWidget(self.color_bar, 0, Qt.AlignVCenter)

        # ── 显隐开关 ──
        self.chk_visible = ToggleSwitch()
        self.chk_visible.setToolTip("显示 / 隐藏曲线")
        self.chk_visible.setChecked(ch.visible)
        lay.addWidget(self.chk_visible, 0, Qt.AlignVCenter)

        # ── 通道名称（纯文本，stretch=1 吸收剩余宽度，不挤数值列）──
        self.lbl_name = QLabel(ch.display_name)
        self.lbl_name.setObjectName("nameLabel")
        self.lbl_name.setToolTip(ch.display_name)
        # 名称超长时在行内裁剪（不挤数值列）：Ignore 让 label 收缩、文本省略
        self.lbl_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lbl_name.setStyleSheet(self._name_qss())
        lay.addWidget(self.lbl_name, 1, Qt.AlignVCenter)

        # ── 「最高」列：固定宽右对齐（列宽与表头共用常量）──
        self.lbl_hi = QLabel("--")
        self.lbl_hi.setFixedWidth(HI_WIDTH)
        self.lbl_hi.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_hi.setToolTip("统计范围内最高温度（℃）")
        self.lbl_hi.setStyleSheet(self._hi_qss())
        lay.addWidget(self.lbl_hi, 0, Qt.AlignVCenter)

        # ── 「实时温度」列：固定宽右对齐、通道色（不带 ℃ 后缀）──
        self.lbl_temp = QLabel("--")
        self.lbl_temp.setFixedWidth(TEMP_WIDTH)   # 固定宽度，锚定行卡右侧
        self.lbl_temp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # 温度颜色 = 该通道曲线色（与色带、曲线一一对应）
        self.lbl_temp.setStyleSheet(self._temperature_qss())
        # stretch=0：温度位置只由行卡右缘 + 固定宽决定，与名称长度无关
        lay.addWidget(self.lbl_temp, 0, Qt.AlignVCenter)

        # ── 信号（最后连接：构造阶段的 set 不触发回调）──
        self.chk_visible.toggled.connect(
            lambda v, idx=self._ch_index: self.visibility_toggled.emit(idx, v))

    def set_card_height(self, height) -> None:
        """Resize the card and its contents while keeping its width fixed."""
        self.setFixedHeight(int(height))
        scale = max(0.9, min(1.0, float(height) / CARD_SIZE[1]))
        if abs(scale - self._scale) < 0.01:
            return
        self._scale = scale
        self.layout().setContentsMargins(
            max(2, round(MARGIN[0] * scale)), max(1, round(MARGIN[1] * scale)),
            max(2, round(MARGIN[2] * scale)), max(1, round(MARGIN[3] * scale)))
        self.layout().setSpacing(max(2, round(ROW_SPACING * scale)))
        self._apply_content_scale(scale)

    def _apply_card_style(self):
        """根据行卡状态更新表面层级（三态），不改变通道数据。"""
        active = self.visual_active(self._hover_active, self._chart_active)
        self.setStyleSheet(self._row_qss(
            hover=self._hover_active, active=active))

    @staticmethod
    def _row_qss(hover: bool, active: bool) -> str:
        """行卡三态 QSS：常规 / 悬停 / 命中（照抄 Theme.card_qss 的 active
        背景判定逻辑，经 Theme.is_light_theme() 公共判定，悬停边框改为
        强调色 ACCENT；不改 card_qss 本身）。

        文本 / 数字颜色由各子控件自身 QSS 控制，不随卡片状态变化。
        """
        bg, bd = Theme.BG_CARD, Theme.BORDER
        if active:
            bg = (Theme.lighten(Theme.BG_CARD, 0.06)
                  if Theme.is_light_theme()
                  else Theme.darken(Theme.BG_CARD, 0.08))
            bd = Theme.ACCENT
        elif hover:
            bg = Theme.BG_HOVER
            bd = Theme.ACCENT
        return (f"background: {bg}; color: {Theme.TEXT};"
                f" border: 1px solid {bd};"
                f" border-radius: {Theme.RADIUS}px;")

    @staticmethod
    def visual_active(hover_active, chart_active) -> bool:
        """只要鼠标悬停或图表命中，卡片就保持视觉高亮。"""
        return bool(hover_active or chart_active)

    def set_hover_active(self, active) -> None:
        """更新鼠标悬停状态，不覆盖图表命中状态。"""
        active = bool(active)
        if active == self._hover_active:
            return
        self._hover_active = active
        self._apply_card_style()

    def set_chart_active(self, active) -> None:
        """更新图表当前命中的通道状态，不受鼠标离开卡片影响。"""
        active = bool(active)
        if active == self._chart_active:
            return
        self._chart_active = active
        self._apply_card_style()

    def enterEvent(self, event) -> None:
        """鼠标进入卡片：置悬停态并广播当前通道名。"""
        self._hover_active = True
        self._apply_card_style()
        self.hover_changed.emit(self.current_name())
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """鼠标离开卡片：清除悬停态并广播空通道名。"""
        self._hover_active = False
        self._apply_card_style()
        self.hover_changed.emit(None)
        super().leaveEvent(event)

    def _apply_content_scale(self, scale):
        """Apply one scale to all visual elements inside the fixed-width row."""
        def font(size):
            f = QFont(self.font())
            f.setPointSizeF(size * scale)
            return f

        def mono_font(size):
            f = font(size)
            f.setFamily(Theme.font("mono"))
            return f

        def text_font(size):
            # 通道名称/「最高」显式使用中文无衬线字体（思源黑体）。
            # 若不指定，QLabel 继承 Segoe UI，而 Segoe UI 无中文字形，
            # Qt 会回退到系统默认（微软雅黑）——笔画粗、小字号发糊。
            # 思源黑体笔画均匀、小字号屏显更清晰。
            f = font(size)
            f.setFamily(Theme.font("text"))
            return f

        self.chk_visible.set_scale(scale)
        self.lbl_temp.setFixedWidth(
            max(50, round(TEMP_WIDTH * min(scale, 1.15))))
        self.lbl_hi.setFixedWidth(
            max(32, round(HI_WIDTH * min(scale, 1.15))))
        self.lbl_temp.setFont(mono_font(TEMP_FONT_SIZE))
        self.lbl_hi.setFont(text_font(10))
        self.lbl_name.setFont(text_font(10))
        self.lbl_temp.setStyleSheet(self._temperature_qss())
        self.lbl_hi.setStyleSheet(self._hi_qss())
        self.lbl_name.setStyleSheet(self._name_qss())

    # ------------------------------------------------------------------
    #  样式构造（颜色占位符必须填全：QSS 重放是显隐变暗 / 配色跟随的实现方式）
    # ------------------------------------------------------------------
    def _color_bar_qss(self):
        return _COLOR_BAR_QSS.format(
            color=(Theme.INDICATOR_BORDER if self._dimmed
                   else self._channel_color),
            border=Theme.INDICATOR_BORDER,
        )

    def _name_qss(self):
        return _NAME_LABEL_QSS.format(
            text_family=Theme.font_family_css("text"),
            text=(Theme.TEXT_MUTED if self._dimmed else Theme.TEXT),
        )

    def _hi_qss(self):
        return _HI_QSS.format(
            color=Theme.TEXT_MUTED,   # 「最高」恒为辅助灰（显隐不变色）
            size=f"{Theme.FONT_SIZE * self._scale:.1f}",
            text_family=Theme.font_family_css("text"),
        )

    def _temperature_qss(self):
        return _TEMP_QSS.format(
            color=(Theme.TEXT_MUTED if self._dimmed
                   else Theme.readable_text(self._channel_color)),
            size=f"{TEMP_FONT_SIZE * self._scale:.1f}",
            mono_font=Theme.font("mono"),
        )

    # ------------------------------------------------------------------
    #  数据刷新
    # ------------------------------------------------------------------
    # ── D5：拖通道卡到三维热区视图落位（mime 携带稳定键） ──
    DRAG_MIME = "application/x-mta-channel-key"
    DRAG_THRESHOLD_PX = 5

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        start = self._drag_start_pos
        if start is not None and \
                (event.pos() - start).manhattanLength() > self.DRAG_THRESHOLD_PX:
            self._drag_start_pos = None
            if self._channel_key:
                self._start_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # 未超阈值 = 单击：本卡既有点击语义不受影响
        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _start_drag(self):
        from PyQt5.QtCore import QMimeData
        from PyQt5.QtGui import QDrag
        mime = QMimeData()
        mime.setData(self.DRAG_MIME, self._channel_key.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pm = QPixmap(14, 14)
        pm.fill(QColor(self._channel_color))
        drag.setPixmap(pm)
        drag.exec_(Qt.CopyAction)

    def set_values(self, current, col) -> None:
        """按整列数据刷新实时温度 + 最高。col 为 numpy 数组（含 NaN）。"""
        vals = col[np.isfinite(col)]
        if vals.size == 0:
            self.show_none()
            return
        cur = current if np.isfinite(current) else np.nan
        self.lbl_temp.setText(f"{cur:.1f}" if np.isfinite(cur) else "--")
        self.lbl_hi.setText(f"{vals.max():.1f}")

    def show_none(self) -> None:
        """无有效数据：实时温度 / 最高显示 --。"""
        self.lbl_temp.setText("--")
        self.lbl_hi.setText("--")

    # ------------------------------------------------------------------
    #  状态同步
    # ------------------------------------------------------------------
    def set_visible_state(self, visible) -> None:
        """同步显隐勾选 + 整行变暗状态（不触发信号）。"""
        visible = bool(visible)
        if self.chk_visible.isChecked() != visible:
            self.chk_visible.blockSignals(True)
            self.chk_visible.setChecked(visible)
            self.chk_visible.blockSignals(False)
        self._apply_dim(visible)

    def _apply_dim(self, visible: bool) -> None:
        """隐藏通道整行变暗：名称 / 最高 / 实时温度转 muted、色带转灰。

        QSS 重放实现（颜色在样式构造期固化，仅 setText 不换色）；
        _temperature_qss / _hi_qss 内部已按 _scale 折算字号。
        """
        self._dimmed = not bool(visible)
        self.lbl_name.setStyleSheet(self._name_qss())
        self.lbl_hi.setStyleSheet(self._hi_qss())
        self.lbl_temp.setStyleSheet(self._temperature_qss())
        self.color_bar.setStyleSheet(self._color_bar_qss())

    def set_color_swatch(self, color) -> None:
        """同步色带与温度文字颜色（跟随通道曲线色；配色由设置页决定）。"""
        color = color or Theme.TEXT
        self._channel_color = color
        # 复用 QSS getter：它们正确填充全部占位符。此前这里只传 color 导致
        # KeyError: 'numeric_size' 崩溃，进而中断
        # _apply_channel_colors_local → 配色切换不生效、画布不刷新。
        self.color_bar.setStyleSheet(self._color_bar_qss())
        self.lbl_temp.setStyleSheet(self._temperature_qss())

    def current_name(self) -> str:
        """当前展示的通道名（与 display_name 一致，供图表 / 报警高亮匹配）。"""
        return self.lbl_name.text()

    def set_name(self, name) -> None:
        """程序化更新展示名（不触发信号）。"""
        self.lbl_name.setText(name)
        self.lbl_name.setToolTip(name)


# ======================================================================
#  经典卡片（classic）：批次1前的 70px 大卡片外观原样保留，设置页可切回
# ======================================================================
# 注意：行卡常量 MARGIN / TEMP_WIDTH / HI_WIDTH / CARD_BORDER_WIDTH 被
# channel_panel._TableHeader import，不得改名；经典卡私有常量一律加
# CLASSIC_ 前缀避免冲突。COLOR_BLOCK_SIZE 仅经典卡使用，保留原名。
CLASSIC_CARD_SIZE = (266, 70)
# One device uses one group of eight channels. The 266px width fills the
# 270px panel after its fixed 2px outer margins.

# 温度文本固定宽度：锚定容器右侧，不受名称长度影响
CLASSIC_TEMP_WIDTH = 84

# 左侧固定窄区：通道颜色色块尺寸 / 开关与色块间距
COLOR_BLOCK_SIZE = (16, 16)
CLASSIC_LEFT_SPACING = 5

# 卡片内边距：严格从紧，消除多余留白（left, top, right, bottom）
CLASSIC_MARGIN = (4, 3, 4, 2)
CLASSIC_OUTER_SPACING = 4    # 左窄区与右主体之间距

# 通道颜色色块（纯展示，颜色由设置页配色方案统一决定）
_CLASSIC_SWATCH_QSS = """
QLabel {{
    background: {color};
    border: 1px solid {border};
    border-radius: 3px;
}}
"""

# 上行统一圆角容器：下拉框 + 温度 收纳在同一块浅底上。
# 刻意不加边框——卡片外层已有唯一的浅色边框，内层再描边会形成
# 内外双框线、破坏整体感（统一主题：控件内不叠边框）。
_CLASSIC_NAME_BOX_QSS = """
QFrame#nameTempBox {{
    background: {bg_input};
    border: none;
    border-radius: 3px;
}}
"""

# 温度读数：大号加粗、颜色跟随通道曲线色、固定宽靠右（带 ℃ 后缀）
_CLASSIC_TEMP_QSS = """
QLabel {{
    color: {color};
    font-size: {numeric_size}pt;
    font-weight: bold;
    font-family: {mono_font};
    border: none;
    background: transparent;
}}
"""

# 统计文本：浅灰辅助文字，字号略小于温度数字。
# 用中文无衬线字体（思源黑体）渲染："最高/平均"含笔画多的"最"字，
# Consolas 无中文字形会回退微软雅黑，小字号下发糊；思源黑体笔画均匀更清晰。
_CLASSIC_STATS_QSS = """
QLabel {{
    color: {color};
    font-size: {caption_size}pt;
    font-family: {text_family};
    border: none;
    background: transparent;
}}
"""


class ClassicChannelCard(QFrame):
    """经典风格单通道卡片（70px 大卡）：坐标固化 + 温度右对齐。

    批次1前的旧外观原样恢复（nameTempBox 内嵌框 + ℃ 后缀 + 16×16 色块
    + 统计行 + Theme.card_qss 三态边框；无行卡的整行变暗逻辑）。
    唯一行为差异（用户要求）：统计行不显示最低值，文案为
    「最高 x · 平均 x」。

    对外 API 与行卡（ChannelCard）鸭子类型一致，由 ChannelPanel 按
    view_mode 二选一构建、统一驱动。
    """

    visibility_toggled = pyqtSignal(int, bool)   # channel index, visible
    hover_changed = pyqtSignal(object)            # 当前通道名称，离开时为 None

    def __init__(self, ch, parent=None):
        super().__init__(parent)
        self._ch_index = ch.index
        self._channel_color = ch.color or Theme.TEXT
        self._scale = 1.0
        self._hover_active = False
        self._chart_active = False
        self.setObjectName("channelCard")
        # 固定整体尺寸：卡片宽高固化，所有卡片同宽 → 温度右锚点同一竖线
        self.setFixedSize(*CLASSIC_CARD_SIZE)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._apply_card_style()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(*CLASSIC_MARGIN)
        lay.setSpacing(CLASSIC_OUTER_SPACING)

        # ── 左侧固定窄区：开关（上）+ 通道颜色色块（下），垂直居中 ──
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(CLASSIC_LEFT_SPACING)
        left.addStretch(1)
        self.chk_visible = ToggleSwitch()
        self.chk_visible.setToolTip("显示 / 隐藏曲线")
        self.chk_visible.setChecked(ch.visible)
        left.addWidget(self.chk_visible, 0, Qt.AlignHCenter)
        self.color_block = QLabel()
        self.color_block.setFixedSize(*COLOR_BLOCK_SIZE)
        self.color_block.setToolTip("通道曲线颜色（由设置页配色方案决定）")
        self.color_block.setStyleSheet(_CLASSIC_SWATCH_QSS.format(
            color=ch.color or Theme.TEXT_MUTED, border=Theme.INDICATOR_BORDER))
        left.addWidget(self.color_block, 0, Qt.AlignHCenter)
        left.addStretch(1)
        lay.addLayout(left, 0)

        # ── 右侧主体：上行（统一边框容器）/ 下行（统计）──
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(2)
        right.addStretch(1)

        # 上行：圆角边框容器，内部 = 名称 + 温度
        self.name_box = QFrame()
        self.name_box.setObjectName("nameTempBox")
        self.name_box.setStyleSheet(
            _CLASSIC_NAME_BOX_QSS.format(bg_input=Theme.BG_INPUT,
                                         bd=Theme.BORDER))
        box = QHBoxLayout(self.name_box)
        box.setContentsMargins(2, 1, 3, 1)
        box.setSpacing(2)

        self.lbl_name = QLabel(ch.display_name)
        self.lbl_name.setObjectName("nameLabel")
        self.lbl_name.setToolTip(ch.display_name)
        # 名称超长时在容器内裁剪（不挤温度）：Ignore 让 label 收缩、文本省略
        self.lbl_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.lbl_name.setStyleSheet(
            _NAME_LABEL_QSS.format(text_family=Theme.font_family_css("text"),
                                   text=Theme.TEXT))
        # stretch=1 吸收容器内剩余宽度：名称多长都被框内消化，不挤温度
        box.addWidget(self.lbl_name, 1)

        self.lbl_temp = QLabel("--")
        self.lbl_temp.setFixedWidth(CLASSIC_TEMP_WIDTH)  # 固定宽，锚定右侧
        self.lbl_temp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # 温度颜色 = 该通道曲线色（与色块、曲线一一对应）
        self.lbl_temp.setStyleSheet(self._temperature_qss())
        # stretch=0：温度位置只由容器右缘 + 固定宽决定，与名称长度无关
        box.addWidget(self.lbl_temp, 0, Qt.AlignRight | Qt.AlignVCenter)
        # 不传 alignment：边框容器横向铺满整个右侧区域（指定对齐会阻止拉伸，
        # 导致容器只取 sizeHint 宽、卡片右侧留空）
        right.addWidget(self.name_box, 0)

        # 下行：统计文本左对齐（用户要求不显示最低值：最高 / 平均）
        self.lbl_stats = QLabel("最高 -- · 平均 --")
        self.lbl_stats.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_stats.setStyleSheet(self._stats_qss())
        right.addWidget(self.lbl_stats, 0)
        right.addStretch(1)
        lay.addLayout(right, 1)

        # ── 信号（最后连接：构造阶段的 set 不触发回调）──
        self.chk_visible.toggled.connect(
            lambda v, idx=self._ch_index: self.visibility_toggled.emit(idx, v))

    def set_card_height(self, height) -> None:
        """Resize the card and its contents while keeping its width fixed."""
        self.setFixedHeight(int(height))
        scale = max(0.9, min(1.0, float(height) / CLASSIC_CARD_SIZE[1]))
        if abs(scale - self._scale) < 0.01:
            return
        self._scale = scale
        margin_x = max(2, round(4 * scale))
        margin_y = max(2, round(3 * scale))
        self.layout().setContentsMargins(
            margin_x, margin_y, margin_x, max(2, round(2 * scale)))
        self.layout().setSpacing(max(2, round(4 * scale)))
        self._apply_content_scale(scale)

    def _apply_card_style(self):
        """根据卡片状态更新表面层级（Theme.card_qss 三态），不改通道数据。"""
        active = self.visual_active(self._hover_active, self._chart_active)
        self.setStyleSheet(Theme.card_qss(
            Theme.RADIUS, hover=self._hover_active, active=active))

    @staticmethod
    def visual_active(hover_active, chart_active) -> bool:
        """只要鼠标悬停或图表命中，卡片就保持视觉高亮。"""
        return bool(hover_active or chart_active)

    def set_hover_active(self, active) -> None:
        """更新鼠标悬停状态，不覆盖图表命中状态。"""
        active = bool(active)
        if active == self._hover_active:
            return
        self._hover_active = active
        self._apply_card_style()

    def set_chart_active(self, active) -> None:
        """更新图表当前命中的通道状态，不受鼠标离开卡片影响。"""
        active = bool(active)
        if active == self._chart_active:
            return
        self._chart_active = active
        self._apply_card_style()

    def enterEvent(self, event) -> None:
        """鼠标进入卡片：置悬停态并广播当前通道名。"""
        self._hover_active = True
        self._apply_card_style()
        self.hover_changed.emit(self.current_name())
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        """鼠标离开卡片：清除悬停态并广播空通道名。"""
        self._hover_active = False
        self._apply_card_style()
        self.hover_changed.emit(None)
        super().leaveEvent(event)

    def _apply_content_scale(self, scale):
        """Apply one scale to all visual elements inside the fixed-width card."""
        def font(size):
            f = QFont(self.font())
            f.setPointSizeF(size * scale)
            return f

        def mono_font(size):
            f = font(size)
            f.setFamily(Theme.font("mono"))
            return f

        def text_font(size):
            # 通道名称显式使用中文无衬线字体（思源黑体）。
            # 若不指定，QLabel 继承 Segoe UI，而 Segoe UI 无中文字形，
            # Qt 会回退到系统默认（微软雅黑）——笔画粗、小字号发糊。
            f = font(size)
            f.setFamily(Theme.font("text"))
            return f

        self.chk_visible.set_scale(scale)
        block = max(14, round(16 * scale))
        self.color_block.setFixedSize(block, block)
        self.lbl_temp.setFixedWidth(
            max(76, round(CLASSIC_TEMP_WIDTH * min(scale, 1.15))))
        self.lbl_temp.setFont(mono_font(16))
        self.lbl_stats.setFont(text_font(10))
        self.lbl_name.setFont(text_font(10))
        self.name_box.layout().setContentsMargins(
            max(2, round(2 * scale)), max(1, round(scale)),
            max(2, round(3 * scale)), max(1, round(scale)))
        self.name_box.layout().setSpacing(max(2, round(2 * scale)))
        self.lbl_temp.setStyleSheet(self._temperature_qss())
        self.lbl_stats.setStyleSheet(self._stats_qss())

    def _temperature_qss(self):
        return _CLASSIC_TEMP_QSS.format(
            # 文字色经 readable_text 对卡底补偿（与行卡融合行同款调用）：
            # 通道曲线色是深画布上的数据身份，直接作浅色骨架上的文字会低对比
            # （实测最差 1.03:1）；补偿保持色相、曲线色本身不变，
            # 左侧 16×16 色块仍用原始通道色（图形指示）
            color=Theme.readable_text(self._channel_color),
            numeric_size=f"{Theme.TYPE_SCALE['numeric'] * self._scale:.1f}",
            mono_font=Theme.font("mono"),
        )

    def _stats_qss(self):
        return _CLASSIC_STATS_QSS.format(
            color=Theme.TEXT_MUTED,
            caption_size=f"{Theme.FONT_SIZE * self._scale:.1f}",
            text_family=Theme.font_family_css("text"),
        )

    # ------------------------------------------------------------------
    #  数据刷新
    # ------------------------------------------------------------------
    def set_values(self, current, col) -> None:
        """按整列数据刷新温度 + 最高/平均（用户要求不显示最低值）。

        col 为 numpy 数组（含 NaN）。
        """
        vals = col[np.isfinite(col)]
        if vals.size == 0:
            self.show_none()
            return
        cur = current if np.isfinite(current) else np.nan
        self.lbl_temp.setText(f"{cur:.1f} ℃" if np.isfinite(cur) else "--")
        self.lbl_stats.setText(
            f"最高 {vals.max():.1f} · 平均 {vals.mean():.1f}")

    def show_none(self) -> None:
        """无有效数据：温度 / 统计显示 --（不显示最低值）。"""
        self.lbl_temp.setText("--")
        self.lbl_stats.setText("最高 -- · 平均 --")

    # ------------------------------------------------------------------
    #  状态同步
    # ------------------------------------------------------------------
    def set_visible_state(self, visible) -> None:
        """同步显隐勾选（不触发信号；经典卡无整行变暗逻辑）。"""
        if self.chk_visible.isChecked() == bool(visible):
            return
        self.chk_visible.blockSignals(True)
        self.chk_visible.setChecked(visible)
        self.chk_visible.blockSignals(False)

    def set_color_swatch(self, color) -> None:
        """同步色块与温度文字颜色（跟随通道曲线色；配色由设置页决定）。"""
        color = color or Theme.TEXT
        self.color_block.setStyleSheet(_CLASSIC_SWATCH_QSS.format(
            color=color, border=Theme.INDICATOR_BORDER))
        self._channel_color = color
        # 复用 _temperature_qss()：它正确填充 {color}/{numeric_size}/{mono_font}
        # 三个占位符。此前这里只传 color 导致 KeyError: 'numeric_size' 崩溃，
        # 进而中断 _apply_channel_colors_local → 配色切换不生效、画布不刷新。
        self.lbl_temp.setStyleSheet(self._temperature_qss())

    def current_name(self) -> str:
        """当前展示的通道名（与 display_name 一致，供图表 / 报警高亮匹配）。"""
        return self.lbl_name.text()

    def set_name(self, name) -> None:
        """程序化更新展示名（不触发信号）。"""
        self.lbl_name.setText(name)
        self.lbl_name.setToolTip(name)
