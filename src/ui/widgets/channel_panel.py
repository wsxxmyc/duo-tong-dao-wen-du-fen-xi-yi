# -*- coding: utf-8 -*-
"""
ChannelPanel — 左侧通道列表面板组件。

通道按每 8 个一组分组显示（仪器一组 8 通道）。每组一个 ChannelGroup：
组头（折叠箭头 + 组号 + 状态 + 启用开关）+ 固定尺寸的卡表融合行卡
（ChannelCard，46px 紧凑行：色带 + 开关 + 名称 + 最高 + 实时温度）。
行卡始终展示（无有效数据也显示 --，不自动折叠空白）；用户可手动折叠 /
展开、整体启用 / 停用。

表头行（_TableHeader：名称 / 最高 / 实时温度）常驻滚动区外、命名入口卡
下方；列宽与行卡共用 channel_card 常量，构成卡表融合的列对齐事实源。

面板是普通 QWidget，sizeHint = 分组内容总高度：
  - 内容少 → 高度=内容高度（紧凑），下方由 vbox 的 stretch 吸收；
  - 内容多、超出可用空间 → 被压缩，内部 QScrollArea 出滚动条，卡片不挤压。
"""
import numpy as np
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QScrollArea, QWidget, QVBoxLayout, QFrame, QLabel, QHBoxLayout,
    QGraphicsDropShadowEffect,
)

from device.datastore import store
from device.datastore.channel import seq_label, strip_seq_label
from ui.widgets.channel_card import (
    CARD_SIZE, CLASSIC_CARD_SIZE, HI_WIDTH, TEMP_WIDTH, MARGIN,
    CARD_BORDER_WIDTH, ChannelCard, ClassicChannelCard,
)
from ui.widgets.channel_group import CARD_SPACING, ChannelGroup

from ui.theme import Theme

# 表头行固定高度（px）。注意：QFrame 带布局时 sizeHint() 实返布局
# 高度（offscreen 无字体环境实测 14），并非 setFixedSize 钉住的 20；
# 首次打开窗口尺寸计算必须用本常量（ChannelPanel.table_header_height），
# 不能用 sizeHint()。
TABLE_HEADER_HEIGHT = 20


def preferred_viewport_height(n_cards: int = 8, mode: str = "table") -> int:
    """n 张行卡/经典卡（单组、组头隐藏）完整展示、无滚动所需视口高度。

    与 ChannelGroup 的实际布局一一对应：n 张卡 + (n-1) 个卡片间距；
    mode="table" 用行卡 CARD_SIZE，"classic" 用经典大卡 CLASSIC_CARD_SIZE。
    可用于估算「8 通道全部展开无滚动条」所需的窗口高度。
    """
    if n_cards <= 0:
        return 0
    card_h = CLASSIC_CARD_SIZE[1] if mode == "classic" else CARD_SIZE[1]
    return n_cards * card_h + (n_cards - 1) * CARD_SPACING


def responsive_card_height(available_height, card_count, spacing=1,
                           minimum_height=48):
    """返回固定卡片高度，窗口高度不再改变通道卡片尺寸。"""
    return CARD_SIZE[1]


def responsive_card_scale(card_height, base_height=60):
    """Return a bounded scale for card typography and inner controls."""
    # The card width is fixed at 266px, so growing typography with height
    # would clip the channel name and statistics horizontally.
    return max(0.9, min(1.0, float(card_height) / base_height))


def _empty_qss() -> str:
    """空状态提示样式：调用时按当前主题生成。"""
    return f"""
QLabel {{
    color: {Theme.TEXT_MUTED};
    font-size: 10pt;
    padding: 20px 8px;
}}
"""


class _ArrowLabel(QLabel):
    """右箭头标签：支持水平偏移量，绘制时平移（不影响布局）。"""

    def __init__(self, parent=None):
        super().__init__("›", parent)
        self._offset = 0
        self.refresh_theme()

    def refresh_theme(self) -> None:
        """主题切换后重设样式（基样式构造期固化，颜色取当前主题强调色，
        文字用 readable_text 对卡底补偿后的强调色）。"""
        self.setStyleSheet(
            f"QLabel {{ background: transparent; border: none;"
            f" color: {Theme.readable_text(Theme.ACCENT)};"
            f" font-size: 11pt; }}")
        self.update()

    def set_offset(self, value: int) -> None:
        """设置水平偏移量并重绘。"""
        self._offset = value
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 箭头作文字用，经 readable_text 对卡底补偿（浅色主题恒等）
        arrow_color = Theme.readable_text(Theme.ACCENT)
        painter.setPen(QColor(arrow_color))
        font = self.font()
        # 箭头字形取大数值档 14pt（四档字号整改：原 setPixelSize(18) 为 px）
        font.setPointSize(14)
        painter.setFont(font)
        # 平移绘制箭头文本，实现右移动画
        painter.drawText(
            self.rect().adjusted(self._offset, 0, self._offset, 0),
            Qt.AlignCenter, "›")
        painter.end()


# 命名入口卡片样式：纯色卡片底（paintEvent 平涂）+ 强调色边框 + 阴影；
# 图标作文字用经 readable_text 对卡底补偿的强调色（主题工业标准 G0-2：无渐变）
_NAMING_ENTRY_LABEL_QSS = """
QLabel#namingEntryIcon {{
    background: transparent;
    border: none;
    color: {accent};
    font-size: 11pt;
}}
QLabel#namingEntryText {{
    background: transparent;
    border: none;
    color: {text};
    font-size: {caption}pt;
    font-weight: 600;
}}
"""


class _NamingEntry(QFrame):
    """面板顶部命名入口卡片：⚙ 图标 + 「自定义各通道名称」文案。

    工业标准形态（2026-08-30 主题整改）：
    - 默认态：纯色卡片底（BG_CARD 平涂）+ 强调色边框 + 微弱阴影
    - Hover 态：仅边框换中性悬停边框色（SCROLL_HANDLE_HOVER）+ 箭头右移 2px 动画
    - 按下态：边框回强调色 + 缩小 scale 0.98 + 阴影减弱，模拟按压凹陷
    - 释放：弹出通道名称编辑弹窗
    """

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("namingEntry")
        self.setCursor(Qt.PointingHandCursor)

        # 悬浮效果：卡片比通道卡片小一圈，形成阶梯形
        self.setFixedWidth(250)
        self.setFixedHeight(36)

        # 状态标记
        self._hovered = False
        self._pressed = False
        self._arrow_offset = 0

        # 设置标签样式
        self.refresh_theme()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)

        self.lbl_icon = QLabel("⚙")
        self.lbl_icon.setObjectName("namingEntryIcon")
        lay.addWidget(self.lbl_icon)

        self.lbl_text = QLabel("自定义各通道名称")
        self.lbl_text.setObjectName("namingEntryText")
        self.lbl_text.setToolTip(
            "打开通道命名弹窗，一次性为各通道设置名称\n（名称也可在「设置 → 通道管理 → 名称列表」里维护）")
        lay.addWidget(self.lbl_text, 1)

        self.lbl_arrow = _ArrowLabel()
        lay.addWidget(self.lbl_arrow)

        # 箭头位移动画
        self._arrow_anim = QPropertyAnimation(self, b"arrowOffset", self)
        self._arrow_anim.setDuration(150)
        self._arrow_anim.setEasingCurve(QEasingCurve.OutCubic)

        # 悬浮阴影效果
        self._shadow = QGraphicsDropShadowEffect(self)
        self._set_shadow(blur=12, dy=4, alpha=64)
        self.setGraphicsEffect(self._shadow)

    def _set_shadow(self, blur: int, dy: int, alpha: int) -> None:
        """按当前主题强调色设置阴影（blur/垂直偏移/透明度随交互态变化）。"""
        self._shadow.setBlurRadius(blur)
        self._shadow.setOffset(0, dy)
        self._shadow.setColor(QColor(
            int(Theme.ACCENT[1:3], 16),
            int(Theme.ACCENT[3:5], 16),
            int(Theme.ACCENT[5:7], 16),
            alpha
        ))

    def refresh_theme(self) -> None:
        """主题切换后重设标签样式、阴影强调色并触发重画。

        标签 QSS 与阴影色在构造时固化，不重设会停留旧主题；卡片底色
        与边框在 paintEvent 实时取 Theme 色，update() 触发重画即跟随。
        """
        self.setStyleSheet(_NAMING_ENTRY_LABEL_QSS.format(
            accent=Theme.readable_text(Theme.ACCENT), text=Theme.TEXT,
            caption=f"{Theme.FONT_SIZE:.0f}"))
        # 构造期首次调用时子控件/阴影尚未创建，与 arrowOffset 同款防御
        if hasattr(self, "lbl_arrow"):
            self.lbl_arrow.refresh_theme()
        if hasattr(self, "_shadow"):
            self._set_shadow(blur=12, dy=4, alpha=64)
        self.update()

    @pyqtProperty(int)
    def arrowOffset(self) -> int:
        """箭头偏移量属性（用于动画）。"""
        return self._arrow_offset

    @arrowOffset.setter
    def arrowOffset(self, value: int) -> None:
        self._arrow_offset = value
        if hasattr(self, 'lbl_arrow'):
            self.lbl_arrow.set_offset(value)

    def paintEvent(self, event) -> None:
        """绘制纯色卡片底和边框（无渐变，G0-2 裁定）。"""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        radius = 5

        # 按下态：轻微缩小 scale 0.98，模拟按压凹陷
        if self._pressed:
            painter.translate(self.width() / 2, self.height() / 2)
            painter.scale(0.98, 0.98)
            painter.translate(-self.width() / 2, -self.height() / 2)

        rect = self.rect()

        # 绘制背景：纯色卡片底平涂（交互态不再换底色）
        painter.setBrush(QColor(Theme.BG_CARD))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, radius, radius)

        # 绘制边框：默认/按下保持强调色（入口卡片强调边框保留），
        # 悬停仅换中性悬停边框色（与全局 hover 边框约定一致）
        if self._hovered and not self._pressed:
            border_color = Theme.SCROLL_HANDLE_HOVER
        else:
            border_color = Theme.ACCENT
        border_width = 2
        painter.setPen(QPen(QColor(border_color), border_width))
        painter.setBrush(Qt.NoBrush)
        border_rect = rect.adjusted(1, 1, -1, -1)
        painter.drawRoundedRect(border_rect, radius, radius)

        painter.end()

    def enterEvent(self, event) -> None:
        """鼠标进入：边框换悬停色 + 阴影放大 + 箭头右移动画。"""
        super().enterEvent(event)
        self._hovered = True
        self.update()

        # 阴影放大：0 4px 12px rgba(accent,0.25)
        self._set_shadow(blur=12, dy=4, alpha=64)

        # 箭头右移动画
        self._arrow_anim.setStartValue(self._arrow_offset)
        self._arrow_anim.setEndValue(2)
        self._arrow_anim.start()

    def leaveEvent(self, event) -> None:
        """鼠标离开：恢复默认状态。"""
        super().leaveEvent(event)
        self._hovered = False
        self._pressed = False
        self.update()

        # 恢复默认阴影
        self._set_shadow(blur=12, dy=4, alpha=64)

        # 箭头归位动画
        self._arrow_anim.setStartValue(self._arrow_offset)
        self._arrow_anim.setEndValue(0)
        self._arrow_anim.start()

    def mousePressEvent(self, event) -> None:
        """按下：缩小 scale 0.98 + 阴影减弱。"""
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()

            # 阴影减弱
            self._set_shadow(blur=8, dy=2, alpha=40)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """释放：弹出通道名称编辑弹窗。"""
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self.update()

            # 恢复阴影
            if self._hovered:
                self._set_shadow(blur=12, dy=4, alpha=64)

            # 发出点击信号
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _TableHeader(QFrame):
    """通道列表表头行：名称（左对齐）/ 最高 / 实时温度（右对齐数值列）。

    卡表融合的列对齐事实源在 channel_card 常量：左右内边距复用行卡
    MARGIN 的水平值（右缘再补偿行卡 QSS 1px 边框内缩），「最高」
    「实时温度」列宽直接取 HI_WIDTH / TEMP_WIDTH —— 多行卡数值竖线
    与表头列一一对齐（行卡列间距 6 vs 表头 4，「最高」列右缘恒差
    2px，属设计内偏差）。
    样式构造期生成，主题切换后由 refresh_theme() 重放。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(CARD_SIZE[0], TABLE_HEADER_HEIGHT)

        lay = QHBoxLayout(self)
        # 右缘 + CARD_BORDER_WIDTH：行卡 1px 边框把行内布局 contentsRect
        # 内缩 1px，表头无边框，需补偿该 1px 才能与数值列右缘对齐
        lay.setContentsMargins(
            MARGIN[0], 0, MARGIN[2] + CARD_BORDER_WIDTH, 0)
        lay.setSpacing(4)

        self.lbl_name = QLabel("名称")
        self.lbl_name.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lay.addWidget(self.lbl_name, 1)

        self.lbl_hi = QLabel("最高")
        self.lbl_hi.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_hi.setFixedWidth(HI_WIDTH)
        lay.addWidget(self.lbl_hi, 0, Qt.AlignRight | Qt.AlignVCenter)

        self.lbl_temp = QLabel("实时温度")
        self.lbl_temp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_temp.setFixedWidth(TEMP_WIDTH)
        lay.addWidget(self.lbl_temp, 0, Qt.AlignRight | Qt.AlignVCenter)

        self.refresh_theme()

    def refresh_theme(self) -> None:
        """主题切换后重放表头标签样式（构造期固化，不重放会停留旧主题）。"""
        self.setStyleSheet(self._header_qss())

    def _header_qss(self) -> str:
        return (f"QLabel {{ background: transparent; border: none;"
                f" color: {Theme.TEXT_MUTED};"
                f" font-size: {Theme.FONT_SIZE}pt;"
                f" font-family: {Theme.font_family_css('text')}; }}")


class ChannelPanel(QWidget):
    """左侧通道分组列表（内容超高时内部出现滚动条）。"""

    # 垂直滚动需求变化（True=内容超高需滚动条，False=完整展示无滚动）。
    # 左面板 270px 被 266px 固定卡片铺满，滚动条出现会从视口里挤占卡片
    # 空间；主窗口据此信号把滚动条宽度额外分配给面板（见
    # MainWindow._on_left_scrollbar_space），卡片空间始终完整。
    scrollbar_space_changed = pyqtSignal(bool)

    def __init__(self, mw):
        super().__init__()
        self.mw = mw  # MainWindow 引用
        self._hover_time_min = None  # 离线图表悬浮时的时间位置（分钟）
        self._hover_channel_name = None
        self._chart_hover_channel_name = None
        self._locked_channel_name = None
        # 视图模式：table=卡表融合行卡（默认）/ classic=经典 70px 大卡；
        # 由 MainWindow 启动加载（channel_view.mode）与设置页切换下发
        self._view_mode = "table"
        self._has_channels = False   # populate 时记录，驱动表头显隐
        self.groups: dict[int, ChannelGroup] = {}   # group_idx -> ChannelGroup
        self.cards: dict[int, object] = {}          # channel index -> ChannelCard

        # 用户对分组的显式状态（populate 重建时保留）
        self._user_collapsed: set[int] = set()   # 用户手动折叠的组
        self._user_expanded: set[int] = set()    # 用户手动展开的组
        self._disabled_groups: set[int] = set()  # 用户停用的组

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 面板顶部：命名入口小卡片（⚙ + 文字，点击打开通道命名弹窗）──
        # 悬浮效果：卡片居中显示，上下留出间距
        self._naming_entry = _NamingEntry()
        self._naming_entry.clicked.connect(self._on_naming_entry_clicked)
        self._naming_entry.hide()   # 无会话时不显示
        # 使用居中对齐，卡片左右自动留出边距形成阶梯形
        outer.addWidget(self._naming_entry, 0, Qt.AlignHCenter | Qt.AlignTop)
        outer.addSpacing(4)  # 卡片与下方滚动区域之间留出间距

        # ── 表头行（卡表融合）：名称 / 最高 / 实时温度，常驻滚动区外 ──
        self._table_header = _TableHeader()
        self._table_header.hide()   # 无会话时不显示
        outer.addWidget(self._table_header, 0, Qt.AlignHCenter | Qt.AlignTop)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(self._scroll)

        self._container = QWidget()
        self._container.setStyleSheet("background: transparent;")
        self._cards_layout = QVBoxLayout(self._container)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(6)
        self._cards_layout.setAlignment(Qt.AlignTop)
        self._scroll.setWidget(self._container)

        # 空状态提示（无会话 / 无通道）
        self._empty_label = QLabel("暂无通道数据\n点击「在线采集」或「导入文件」开始")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet(_empty_qss())
        self._cards_layout.addWidget(self._empty_label)

        # 滚动需求检测：范围变化 → 布局稳定后判断是否需要滚动条
        self._scrollbar_space_active = False
        self._scroll.verticalScrollBar().rangeChanged.connect(
            self._check_scrollbar_space)

    def refresh_theme(self) -> None:
        """主题切换后重设命名入口卡片与空状态标签（构造时固化的部分）。

        populate() 只重建通道卡片，入口卡片、表头行与空状态标签是构造期
        一次性样式，须由 MainWindow._apply_theme 显式调用本方法重放。
        """
        self._naming_entry.refresh_theme()
        self._table_header.refresh_theme()
        self._empty_label.setStyleSheet(_empty_qss())

    def table_header_height(self) -> int:
        """返回表头行固定高度 TABLE_HEADER_HEIGHT。

        QFrame 带布局时 sizeHint() 实返布局高度（非 setFixedSize
        钉住的值），需要表头行高度时应取本常量而不是控件 sizeHint。
        """
        return TABLE_HEADER_HEIGHT

    # ------------------------------------------------------------------
    #  视图模式（卡表融合 table ⇄ 经典卡片 classic）
    # ------------------------------------------------------------------
    @property
    def view_mode(self) -> str:
        """当前视图模式："table"（卡表融合行卡）或 "classic"（经典大卡）。"""
        return self._view_mode

    def set_view_mode(self, mode) -> None:
        """设置视图模式并即时刷新表头显隐（卡片类在 populate 时按模式构建）。

        mode 仅接受 "table" / "classic"，非法值抛 ValueError。切换后须
        调 populate() 重建卡片（ChannelGroup 按 panel.view_mode 选卡类）。
        """
        if mode not in ("table", "classic"):
            raise ValueError(f"未知通道列表视图模式：{mode!r}")
        self._view_mode = mode
        self._refresh_header_visibility()

    def _refresh_header_visibility(self) -> None:
        """表头行仅卡表融合视图且有通道时显示（经典卡视图无表头）。"""
        self._table_header.setVisible(
            self._view_mode == "table" and self._has_channels)

    def _check_scrollbar_space(self, *_):
        """滚动范围变化 → 延迟到布局稳定后评估滚动需求。

        rangeChanged 触发时滚动条可见性可能尚未同步，用 0ms 定时器
        推迟到事件循环下一拍再判断 maximum()。
        """
        QTimer.singleShot(0, self._emit_scrollbar_space)

    def _emit_scrollbar_space(self):
        """滚动需求变化时发 scrollbar_space_changed（状态未变不打扰）。"""
        sb = self._scroll.verticalScrollBar()
        needed = sb.maximum() > 0
        if needed != self._scrollbar_space_active:
            self._scrollbar_space_active = needed
            self.scrollbar_space_changed.emit(needed)

    def resizeEvent(self, event) -> None:
        """窗口尺寸变化时保持固定卡片尺寸，由滚动区域处理超高内容。"""
        super().resizeEvent(event)
        self._resize_cards_to_viewport()

    def _resize_cards_to_viewport(self):
        """保持固定卡片尺寸；通道过多时由滚动区域处理。"""
        session = store.active
        if session is None or len(session.channels) != 8 or len(self.groups) != 1:
            return
        group = next(iter(self.groups.values()))
        if group.collapsed:
            return
        size = CLASSIC_CARD_SIZE if self._view_mode == "classic" else CARD_SIZE
        for card in group.cards.values():
            card.setFixedSize(*size)

    # ------------------------------------------------------------------
    #  布局
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    #  填充
    # ------------------------------------------------------------------
    def populate(self) -> None:
        """按当前活跃会话重建通道分组。"""
        self.mw._loading = True
        self._clear_cards()
        self.groups.clear()
        self.cards.clear()
        s = store.active
        self._has_channels = bool(s is not None and s.channels)
        if s is not None and s.channels:
            self._empty_label.hide()
            self._naming_entry.show()
            self._refresh_header_visibility()
            # The device layout is one fixed group of eight channels.
            # Keep the grouping calculation for compatibility with imported
            # or simulated sessions, while the normal device view remains
            # header-free and tightly packed.
            n_groups = (len(s.channels) + 7) // 8
            no_frames = s.buffer.n == 0  # 尚未采到数据（live 刚创建）
            for g in range(n_groups):
                chs = s.channels[g * 8:g * 8 + 8]
                grp = ChannelGroup(g, chs, self.mw, self)
                # 仅一组通道时隐藏组头，直接展示通道数据（省垂直空间 / 少滚动）
                grp.set_header_visible(n_groups > 1)
                has = grp.has_data(s) if not no_frames else False
                # 折叠状态：仅用户手动折叠的组保持折叠；其余始终展开。
                # 无有效数据也显示卡片（温度 --），避免卡片列表空白。
                grp.set_collapsed(g in self._user_collapsed)
                # 启用状态
                if g in self._disabled_groups:
                    grp.set_enabled(False)
                # 状态提示
                if not has and not no_frames:
                    grp.set_status("无数据")
                grp.refresh_values(s)
                grp.sync_card_checks()
                self._cards_layout.addWidget(grp)
                self.groups[g] = grp
                # 组启停 → 采集组数联动（采集中即时生效，见 main_window._on_live_group_toggled）
                grp.enabled_changed.connect(self._on_group_enabled_changed)
                for idx, card in grp.cards.items():
                    self.cards[idx] = card
        else:
            self._empty_label.show()
            self._naming_entry.hide()
            self._refresh_header_visibility()
        self.mw._loading = False
        self._resize_cards_to_viewport()

    def _clear_cards(self):
        while self._cards_layout.count():
            item = self._cards_layout.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._empty_label:
                w.deleteLater()

    def _refresh_card(self, card, ch):
        """按统一分析结果刷新卡片；在线模式额外保留最新原始测量值。"""
        s = store.active
        if s is None or s.is_open_circuit(ch):
            card.show_none()
            return
        result = self.mw.pipeline.get(s, ch)
        if result is None:
            card.show_none()
            return
        t_processed, values_processed, _anomaly = result
        if values_processed.size == 0:
            card.show_none()
            return

        if s.is_live:
            # 在线采集卡片的大号温度表示仪器最新原始测量值。
            raw_values = s.buffer.column(ch.index)
            current = raw_values[-1] if raw_values.size else np.nan
        elif self._hover_time_min is None:
            # 离线未悬浮时显示该通道分析结果中的最高温度。
            finite = values_processed[np.isfinite(values_processed)]
            current = finite.max() if finite.size else np.nan
        else:
            # 离线悬浮时按该通道自身的最近分析采样点显示温度。
            x_min = (t_processed - t_processed[0]) / 60.0
            valid = np.isfinite(x_min) & np.isfinite(values_processed)
            if not valid.any():
                current = np.nan
            else:
                indexes = np.flatnonzero(valid)
                nearest = indexes[np.argmin(
                    np.abs(x_min[indexes] - self._hover_time_min))]
                current = values_processed[nearest]
        card.set_values(current, values_processed)

    # ------------------------------------------------------------------
    #  折叠 / 展开状态记录
    # ------------------------------------------------------------------
    def _on_group_enabled_changed(self, group_idx: int, enabled: bool) -> None:
        """组启用开关变化 → 转发主窗口联动采集组数。"""
        if hasattr(self.mw, "_on_live_group_toggled"):
            self.mw._on_live_group_toggled(group_idx, enabled)

    def _on_group_collapse_changed(self, group_idx, collapsed):
        if collapsed:
            self._user_collapsed.add(group_idx)
            self._user_expanded.discard(group_idx)
        else:
            self._user_expanded.add(group_idx)
            self._user_collapsed.discard(group_idx)

    def _on_naming_entry_clicked(self):
        """点击面板顶部命名入口卡片 → 主窗口打开通道命名弹窗。"""
        if hasattr(self.mw, "_open_channel_naming_dialog"):
            self.mw._open_channel_naming_dialog()

    # ------------------------------------------------------------------
    #  数据刷新 / 同步
    # ------------------------------------------------------------------
    @staticmethod
    def effective_focus(locked_name, card_hover_name, chart_hover_name):
        """按锁定、左侧卡片悬停、图表命中的顺序返回视觉焦点。"""
        return locked_name or card_hover_name or chart_hover_name

    def update_temperatures(self) -> None:
        """刷新各卡片当前值 + 统计；无有效数据也保持卡片显示（温度 --）。"""
        s = store.active
        if s is None:
            return
        for g, grp in self.groups.items():
            has = grp.has_data(s)
            # 有数据自动展开（用户手动折叠的除外）；无数据保持展开并提示
            if has and grp.collapsed and g not in self._user_collapsed:
                grp.set_collapsed(False)
            grp.set_status("" if has else "无数据")
            grp.refresh_values(s)

    def update_hover_temperature(self, time_min) -> None:
        """离线图表悬浮时，按分钟位置刷新左侧卡片温度。"""
        s = store.active
        if s is None or s.is_live:
            return
        try:
            time_min = float(time_min)
        except (TypeError, ValueError):
            self.clear_hover_temperature()
            return
        if not np.isfinite(time_min):
            self.clear_hover_temperature()
            return
        self._hover_time_min = time_min
        self.update_temperatures()

    def update_hover_channel(self, selected_name) -> None:
        """只更新鼠标命中的通道卡片高亮，不改变在线温度值。"""
        self._chart_hover_channel_name = selected_name
        self._refresh_card_highlight()

    def set_locked_hover_channel(self, selected_name) -> None:
        """同步趋势图点击锁定通道，锁定焦点优先于所有临时 hover。"""
        self._locked_channel_name = selected_name
        self._refresh_card_highlight()

    def _on_card_hover_changed(self, selected_name):
        """左侧卡片悬停时同步卡片和右侧曲线的高亮状态。"""
        if selected_name is not None:
            selected_name = str(selected_name).strip() or None
        self._hover_channel_name = selected_name
        self._refresh_card_highlight()
        if hasattr(self.mw, "refresh_plots"):
            self.mw.refresh_plots()

    def clear_hover_temperature(self) -> None:
        """清除离线悬浮位置，恢复显示各通道最高分析温度。"""
        self._hover_time_min = None
        self.update_temperatures()
        self._chart_hover_channel_name = None
        self._refresh_card_highlight()

    def _refresh_card_highlight(self):
        """合并左侧卡片悬停和图表命中状态，更新卡片视觉高亮。"""
        selected_name = self.effective_focus(
            self._locked_channel_name,
            self._hover_channel_name,
            self._chart_hover_channel_name)
        s = store.active
        for idx, card in self.cards.items():
            name = card.current_name()
            card.set_chart_active(name == selected_name)

    def _set_hover_card(self, selected_name):
        """兼容旧调用：更新图表命中的通道卡片。"""
        self._chart_hover_channel_name = selected_name
        self._refresh_card_highlight()

    def sync_checks(self) -> None:
        """同步各组卡片勾选与通道 visible 一致。"""
        s = store.active
        if s is None:
            return
        for grp in self.groups.values():
            grp.sync_card_checks()

    # ------------------------------------------------------------------
    #  信号槽（原 MainWindow._on_channel_check_changed / _on_name_combo_changed /
    #  _pick_color 迁移而来）
    # ------------------------------------------------------------------
    def _on_visibility_changed(self, idx, visible):
        if self.mw._loading:
            return
        s = store.active
        if s is None or not (0 <= idx < len(s.channels)):
            return
        ch = s.channels[idx]
        ch.visible = visible
        # 静默持久化（notify=False）：显隐切换只做局部刷新，
        # 不触发 channels_changed → 整面板重建（重建会销毁刚点的开关、
        # 杀掉滑块动画，造成卡顿）
        if store.config is not None:
            store.config.set_visible(ch.key, visible, notify=False)
        # 本组启用状态同步（全显/全隐/半选）+ 刷新图一次
        self._sync_group_checks(idx)
        self.mw.refresh_plots()

    def _sync_group_checks(self, idx):
        """更新包含 idx 卡片的组的启用状态（替代整面板重建）。"""
        for grp in self.groups.values():
            if idx in grp.cards:
                grp.sync_card_checks()
                return

    def _on_name_changed(self, idx, full_label):
        """通道改名统一入口（命名弹窗下拉选中时调用）。

        full_label 是「序号前缀 + 自定义名」的完整名（或 CHn（默认）），
        剥离前缀后存纯自定义名；经 ChannelConfig 全局生效，触发
        channels_changed → 面板重建（卡片批次更新为纯文本显示名）。
        """
        s = store.active
        if s is None or not (0 <= idx < len(s.channels)):
            return
        c = s.channels[idx]
        default_label = f"{c.key}（默认）"
        new_name = "" if str(full_label).strip() == default_label else strip_seq_label(full_label)
        c.name = new_name
        # 通道改名通过 ChannelConfig 全局生效（Pipeline 用 index 作键，无需迁移 processed）
        if store.config:
            store.config.set_name(c.key, new_name)
        if self.mw.compare_panel is not None:
            self.mw.compare_panel.sync_label(idx, c.display_name)
        self._refresh_name_candidates()
        self.mw.refresh_plots()

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    #  名称候选 / 通道切换（原 MainWindow 方法迁移）
    # ------------------------------------------------------------------
    def name_candidates(self, ch) -> list:
        """收集某通道可选名称（完整名：固定序号前缀 + 候选名）。"""
        s = store.active
        used_names = {
            other.name for other in (s.channels if s is not None else [])
            if other.index != ch.index and other.name
        }
        config = getattr(store, "config", None)
        for key, record in getattr(config, "channels", {}).items():
            if key != ch.key and isinstance(record, dict):
                name = str(record.get("name") or "").strip()
                if name:
                    used_names.add(name)
        default_label = f"{ch.key}（默认）"
        cands = [default_label] + [
            name for name in self.mw.name_list
            if name not in used_names or name == ch.name
        ]
        full = []
        seen = set()
        for base in cands:
            label = base if base == default_label else seq_label(ch.key, base)
            if label not in seen:
                full.append(label)
                seen.add(label)
        return full

    def _refresh_name_candidates(self):
        """名称池 / 占用变化 → 刷新命名弹窗各行下拉候选（卡片已为纯文本）。"""
        dlg = getattr(self.mw, "_naming_dialog", None)
        if dlg is not None and hasattr(dlg, "refresh_channel_candidates"):
            dlg.refresh_channel_candidates()

    def toggle_channels(self) -> None:
        """通道切换：轮询显示单个通道 → 全部通道 → 下一通道。"""
        s = store.active
        if s is None:
            return
        channels = [c for c in s.channels if not s.is_open_circuit(c)]
        if not channels:
            return
        n = len(channels)
        self.mw._channel_cycle_idx = (self.mw._channel_cycle_idx + 1) % (n + 1)

        for c in s.channels:
            c.visible = False

        if self.mw._channel_cycle_idx == n:
            for c in s.channels:
                c.visible = True
            self.mw.statusBar().showMessage("通道：全部显示")
        else:
            ch = channels[self.mw._channel_cycle_idx]
            ch.visible = True
            self.mw.statusBar().showMessage(f"通道：{ch.display_name}")

        self.sync_checks()
        self.mw.refresh_plots()

    # ------------------------------------------------------------------
    #  取数（供图表 / 导出使用）
    # ------------------------------------------------------------------
    def color_of(self, name):
        """返回通道颜色。"""
        if self.mw.dataset is None:
            return "#e74c3c"
        c = next((c for c in self.mw.dataset.channels if c.display_name == name), None)
        return c.color if c else "#e74c3c"

    def visible_series(self, max_minutes=None, only=None):
        """收集可见通道的 (名称, 分钟x, 温度v)——从 Pipeline 统一管道取数。"""
        out = []
        s = store.active
        if s is None:
            return out
        for c in s.visible_channels():
            if only is not None and c.display_name != only:
                continue
            x, v = self.mw.pipeline.series_minutes(s, c, max_minutes)
            if x.size:
                out.append((c.display_name, x, v))
        return out

    def visible_series_window(self, start_min, end_min):
        """收集指定时间窗口内的可见通道数据——从 Pipeline 统一管道取数。"""
        out = []
        s = store.active
        if s is None:
            return out
        for c in s.visible_channels():
            x, v = self.mw.pipeline.series_window(s, c, start_min, end_min)
            if x.size:
                out.append((c.display_name, x, v))
        return out

    def current_max_temperature(self, only_visible: bool = True):
        """各（可见）通道『最新值』的最大温度，供悬浮球球心直显。

        返回 (最高温度 float, Channel) 或 None（无会话 / 无有效数据）。
        last_value 由实时采集链路每帧维护；离线会话不保证为最新。
        """
        s = store.active
        if s is None:
            return None
        channels = s.visible_channels() if only_visible else s.channels
        best = None
        for c in channels:
            v = c.last_value
            if v is None or not c.enabled or v != v:   # None / 未使能 / NaN
                continue
            if best is None or v > best[0]:
                best = (v, c)
        return best
