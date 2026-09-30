# -*- coding: utf-8 -*-
"""
主题引擎 —— 四档工业 HMI 场景配色系统（办公分析 + 产线采集）。

设计总则（定稿记录，演示与审查见 文档-docs/主题配色审查报告-theme-color-audit-2026-08-30.md）：
1. 两分区布局：工具栏/左栏等界面骨架（BG_*）用浅色（工业中灰为指定的
   中灰白字、石墨中黑为中黑），趋势画布（PLOT_FACE/PLOT_GRID/PLOT_AXIS）
   比骨架更深一档——温度趋势曲线在深底上最清晰；净白精工为用户裁定的
   唯一浅画布例外（浅底深字，通道色板配深艳系）。
2. 全线不用金色背景与文字、不用深黑（近纯黑）底。
3. 文字纯度：正文白字纯白 #ffffff / 黑字纯黑 #000000，禁止发灰；
   画布文字 PLOT_TEXT 深画布纯白、净白精工浅画布深字 #1f2a37；
   TEXT_MUTED 是唯一灰色角色。
4. 状态胶囊：单色文字 + 同色 1px 边框、无底色填充（capsule_qss）；
   可读性由前景自适应保证（readable_text / on_color_fg / _ensure_contrast）。
5. 通道色板每套专属：对各自画布 ≥3:1，前 8 通道两两 CIEDE2000 ≥8.0。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PyQt5.QtWidgets import QApplication


class Theme:
    """全局主题：四档工业 HMI 场景配色 + 引擎自适应前景。"""

    DEFAULT_THEME = "office_light"

    _ACTIVE = DEFAULT_THEME

    THEME_NAMES = {
        "office_light":          "办公明亮",
        "office_focus":          "办公专注",
        "production_standard":   "产线标准",
        "production_low_light":  "产线低照度",
        "grey_industrial":        "浅灰工业",
    }

    # 办公主题与 浅灰工业使用浅色骨架；产线主题逐级压低亮度，
    # 低照度主题不使用纯黑。
    _LIGHT_THEMES = ("office_light", "office_focus", "grey_industrial")

    # 历史及本轮退役主题键 → 最接近的四档场景主题，保证已有配置可继续启动。
    _LEGACY_THEME_MAP = {
        "industrial_slate": "office_light",
        "industrial_light_gray": "office_focus",
        "industrial_mid_gray": "production_standard",
        "porcelain_light": "office_light",
        "ice_glass": "office_focus",
        "graphite_mid": "production_low_light",
        "industrial_light": "office_light",
        "apple_glass": "office_focus",
        "azure_light": "office_focus",
        "steel_blue": "office_focus",
        "graphite_dark": "production_low_light",
        "warm_silver": "office_focus",
        "industrial_gray": "production_standard",
        "classic_industrial_light": "office_focus",
        "classic_industrial_gray": "production_standard",
        "classic_steel_blue": "office_focus",
        "classic_graphite_dark": "production_low_light",
        "classic_warm_silver": "office_focus",
    }

    # 更早的历史别名（级联到新主题）
    _LEGACY_THEME_ALIASES = {
        "dark": "production_low_light",
        "light": "office_light",
        "milkup": "office_focus",
        "glass": "office_focus",
        "tech_midnight": "office_focus",
        "obsidian_dark": "production_low_light",
        "professional_zinc": "production_standard",
    }

    # 全局可见状态不再退回灰色：信息（青）、当前（蓝）、正常（绿）、
    # 警告（橙）、错误（红）。浅色主题的原色偏深以保证文字对比度，
    # 深色主题使用高亮色；semantic_text 负责最终的背景补偿。
    _SEMANTIC_SOURCES = {
        "office_light": {
            "INFO": "#00677a", "CURRENT": "#1769aa",
        },
        "office_focus": {
            "INFO": "#006273", "CURRENT": "#356faf",
        },
        "production_standard": {
            "INFO": "#55d9d1", "CURRENT": "#9fdcff",
        },
        "production_low_light": {
            "INFO": "#55e4dc", "CURRENT": "#82d2fa",
        },
        "grey_industrial": {
            "INFO": "#006273", "CURRENT": "#0052d9",
        },
    }
    _SEMANTIC_ATTRS = {
        "info": "INFO", "current": "CURRENT", "success": "SUCCESS",
        "warning": "WARNING", "error": "ERROR",
    }

    # 深色部件专属覆盖（浅灰工业：工具栏与状态栏回归一体浅灰，仅保留白底
    # 黑字按钮、深灰数据表行/表头两个深色部件）。只有注册了覆盖的
    # 主题（当前仅 grey_industrial）在 qss()/statusbar_surface()/
    # table_face() 注入对应部件配色，其余主题取各自常规计算路径。
    _DARK_PART_OVERRIDES = {
        "grey_industrial": {
            # 顶部工具栏（浅灰与页面一体 + 白底黑字按钮，悬停 #d8d8d8）
            "TOOLBAR_BG": "#e5e5e5",
            "TOOLBAR_BORDER": "#8f8f8f",
            "TOOLBAR_TEXT": "#111111",
            "TOOLBAR_BTN_BG": "#ffffff",
            "TOOLBAR_BTN_TEXT": "#111111",
            "TOOLBAR_BTN_BORDER": "#b9b9b9",
            "TOOLBAR_BTN_HOVER_BG": "#d8d8d8",
            # 底部状态栏（浅灰一体，黑字）
            "STATUSBAR_SURFACE": "#e5e5e5",
            # 数据表（表头 #494c4f + 深灰行白字，选中行腾讯蓝白字）
            "TABLE_HEADER_BG": "#494c4f",
            "TABLE_HEADER_TEXT": "#ffffff",
            "TABLE_ROW_BG": "#585f66",
            "TABLE_ROW_ALT_BG": "#50565c",
            "TABLE_ROW_TEXT": "#ffffff",
            "TABLE_SELECTED_BG": "#0052d9",
            "TABLE_SELECTED_TEXT": "#ffffff",
        },
    }

    _CHECK_PNG: dict = {}
    _ARROW_PNG: dict = {}

    # 灰白蓝三色体系（2026-09-03 用户定稿）：灰做骨架层次、白做文字
    # 与按钮面、蓝做选中引导填充；文字非黑即白；激活/运行用微信绿实
    # 色填充（黑字）；红色仅保留报警/错误等安全语义文字与报警灯。
    # EMPHASIS_FILL 采用腾讯蓝 #0052D9（用户选定，废 #1e6bb0 钢蓝）。
    EMPHASIS_FILL = "#0052d9"   # 选中/勾选/胶囊/关键引导填充（白字）
    RUN_FILL = "#07c160"        # 激活/运行填充：微信绿（黑字）
    _SPIN_ARROW_PNG: dict = {}

    # 顶级设计规范令牌
    FONT_SIZE = 10
    # 字体角色体系：ui=界面英文优先 / text=正文中文 / plot=图表 / mono=等宽数字。
    # chart_renderer 以 Theme.font("plot"/"text"/"mono") 作为 matplotlib
    # family=，Segoe UI 无中文字形，角色表被抽空会导致画布中文全部渲染失败
    # （2026-08-20 重构回归，见 live_rendering/live_time_axis 基线）。
    FONT_ROLES = {
        "ui": "Segoe UI",
        "text": "Noto Sans SC",
        "plot": "Microsoft YaHei",
        "mono": "Consolas",
    }
    # 回退链含捆绑字体（Noto Sans SC / Source Han Sans SC，
    # register_bundled_qt_fonts 注册），缺中文字体的机器靠它渲染 CJK，
    # 不可裁剪（见 test_theme_keeps_compatibility_fonts_in_central_fallback_chain）；
    # configure_fonts 会按角色重建本链
    FONT_FAMILY = ["Segoe UI", "Noto Sans SC", "Source Han Sans SC",
                   "Microsoft YaHei UI", "Microsoft YaHei", "sans-serif"]
    RADIUS = 3            # 工业标准：控件统一小圆角（唯一档）
    CARD_RADIUS = 3       # 卡片同档（胶囊 pill 的 8px 为已拍板标记样式，不受此限）
    RADIUS_SM = 3         # 小控件同档
    SPACE = 8             # 基础组件间距
    CARD_PADDING = 16     # 卡片内边距
    HOVER_HIGHLIGHT = "#f4d35e"  # 趋势图悬浮信息卡片当前通道黄色描边

    TYPE_SCALE = {
        "hero": 18,
        "title": 14,
        "section": 11.5,
        "body": 10,
        "caption": 9,
        "numeric": 16,
    }
    PLOT_TICK_SIZE = 10
    PLOT_LABEL_SIZE = 11
    PLOT_LEGEND_SIZE = 9
    PLOT_TITLE_WEIGHT = "medium"

    # ================================================================== #
    #  四档工业 HMI 配色：办公两档 + 产线两档。画布独立于界面骨架，        #
    #  参考传统工控界面采用中深灰 / 深蓝灰绘图区与克制网格；高亮色只        #
    #  传达选中、采集、注意、报警和数据身份，避免大面积彩色底干扰值守。   #
    # ================================================================== #
    _PALETTES = {
        # 办公明亮：冷白、浅雾灰与纯黑文字（灰白蓝体系：文字非黑即白）。
        "office_light": {
            "BG_PRIMARY": "#f4f6f8", "BG_CARD": "#fbfcfd",
            "BG_INPUT": "#fdfeff", "BORDER": "#cbd3da",
            "TEXT": "#000000", "TEXT_MUTED": "#595959",
            "PLOT_TEXT": "#f2f5f7", "ACCENT": "#1769aa",
            "ACCENT_HOVER": "#0e5b98", "ACCENT_PRESSED": "#094877",
            "ACCENT_SOFT": "#e1edf8", "FOCUS_RING": "#1769aa",
            "GREEN": "#167a47", "RED": "#b52d24",
            "ORANGE": "#b25a14", "BG_HOVER": "#e8edf1",
            "BG_PRESSED": "#d8e0e6", "SCROLL_HANDLE": "#84909a",
            "SCROLL_HANDLE_HOVER": "#5e6a74", "SEL_TEXT": "#ffffff",
            "INDICATOR_BORDER": "#cbd3da", "GRID": "#d4dbe1",
            "PLOT_FACE": "#4b5055", "PLOT_GRID": "#363b40",
            "PLOT_AXIS": "#111315",
        },
        # 办公专注：雾灰、浅银灰与纯黑文字，降低长时间复核的白底疲劳。
        "office_focus": {
            "BG_PRIMARY": "#e3e7eb", "BG_CARD": "#f0f3f5",
            "BG_INPUT": "#f7f9fa", "BORDER": "#b8c2ca",
            "TEXT": "#000000", "TEXT_MUTED": "#595959",
            "PLOT_TEXT": "#f2f5f7", "ACCENT": "#356faf",
            "ACCENT_HOVER": "#295f9a", "ACCENT_PRESSED": "#234f80",
            "ACCENT_SOFT": "#d8e5f1", "FOCUS_RING": "#356faf",
            "GREEN": "#1a7845", "RED": "#ab3028",
            "ORANGE": "#ab5416", "BG_HOVER": "#d7dee4",
            "BG_PRESSED": "#c5ced6", "SCROLL_HANDLE": "#7a8791",
            "SCROLL_HANDLE_HOVER": "#56636d", "SEL_TEXT": "#ffffff",
            "INDICATOR_BORDER": "#b8c2ca", "GRID": "#c8d0d6",
            "PLOT_FACE": "#41474d", "PLOT_GRID": "#30363b",
            "PLOT_AXIS": "#15181a",
        },
        # 产线标准：金属中灰、纯白文字与高亮工业蓝，适合常规车间照度。
        "production_standard": {
            "BG_PRIMARY": "#535c63", "BG_CARD": "#5e676e",
            "BG_INPUT": "#4b545b", "BORDER": "#93a0a8",
            "TEXT": "#ffffff", "TEXT_MUTED": "#e6e6e6",
            "PLOT_TEXT": "#f4f7f8", "ACCENT": "#9fdcff",
            "ACCENT_HOVER": "#c2ebff", "ACCENT_PRESSED": "#78c7ee",
            "ACCENT_SOFT": "#294a5b", "FOCUS_RING": "#9fdcff",
            "GREEN": "#5ad18a", "RED": "#ff8585",
            "ORANGE": "#ffc05c", "BG_HOVER": "#626d74",
            "BG_PRESSED": "#485159", "SCROLL_HANDLE": "#aab6bd",
            "SCROLL_HANDLE_HOVER": "#d6e0e4", "SEL_TEXT": "#ffffff",
            "INDICATOR_BORDER": "#93a0a8", "GRID": "#7f8b93",
            "PLOT_FACE": "#343b41", "PLOT_GRID": "#4d5961",
            "PLOT_AXIS": "#b8c1c6",
        },
        # 产线低照度：柔和深灰而非纯黑；纯白文字；深蓝灰趋势画布沿用现场操作软件的静稳层级。
        "production_low_light": {
            "BG_PRIMARY": "#39434a", "BG_CARD": "#455159",
            "BG_INPUT": "#313b42", "BORDER": "#72838d",
            "TEXT": "#ffffff", "TEXT_MUTED": "#cfcfcf",
            "PLOT_TEXT": "#f3f6f7", "ACCENT": "#82d2fa",
            "ACCENT_HOVER": "#a7e3ff", "ACCENT_PRESSED": "#55b9e6",
            "ACCENT_SOFT": "#244b5b", "FOCUS_RING": "#82d2fa",
            "GREEN": "#52d28b", "RED": "#ff8585",
            "ORANGE": "#ffbd5c", "BG_HOVER": "#4e5b63",
            "BG_PRESSED": "#2e373d", "SCROLL_HANDLE": "#8fa1ab",
            "SCROLL_HANDLE_HOVER": "#c5d1d7", "SEL_TEXT": "#ffffff",
            "INDICATOR_BORDER": "#72838d", "GRID": "#65757f",
            "PLOT_FACE": "#26333c", "PLOT_GRID": "#3d5060",
            "PLOT_AXIS": "#92a6b3",
        },
        # 浅灰工业：参考工业 HMI 控制台实机像素采样（2026-08-30，
        # 移植自 feat/浅灰工业工业浅灰主题 分支终版）。扁平浅灰——界面一块
        # #e5e5e5 底（工具栏/状态栏同底一体，白底黑字按钮是唯一凸起）；
        # 正文纯黑（灰白蓝体系）；强调随全局腾讯蓝 #0052D9；趋势画布
        # 调为中性工业炭灰 #242a30（2026-09-03 用户定稿，弃偏蓝的
        # #212d36）配近白图表文字与亮荧光通道色板；数据表保留深灰行
        # 白字深色部件（见 _DARK_PART_OVERRIDES）。
        "grey_industrial": {
            "BG_PRIMARY": "#e5e5e5", "BG_CARD": "#e5e5e5",
            "BG_INPUT": "#e5e5e5", "BORDER": "#8f8f8f",
            "TEXT": "#000000", "TEXT_MUTED": "#595959",
            "PLOT_TEXT": "#f5f7f8", "ACCENT": "#0052d9",
            "ACCENT_HOVER": "#0046bc", "ACCENT_PRESSED": "#003a9c",
            "ACCENT_SOFT": "#dce6f8", "FOCUS_RING": "#0052d9",
            "GREEN": "#2e7d32", "RED": "#c62828",
            "ORANGE": "#b85a00", "BG_HOVER": "#d8d8d8",
            "BG_PRESSED": "#cccccc", "SCROLL_HANDLE": "#ababab",
            "SCROLL_HANDLE_HOVER": "#808080", "SEL_TEXT": "#ffffff",
            "INDICATOR_BORDER": "#8f8f8f", "GRID": "#cfcfcf",
            "PLOT_FACE": "#242a30", "PLOT_GRID": "#3f464d",
            "PLOT_AXIS": "#8b939b",
        },
    }
    # 高对比通道配色：每套主题专属，对各自 PLOT_FACE ≥3:1（大图形），
    # 前 8 通道两两 CIEDE2000 ≥8.0（test_theme_contrast 钉死；曲线色为
    # 数据身份不做文字补偿，温度数值等文字场景走 readable_text）
    # 统一默认通道色板（「高对比配色 · 统一默认」方案行）：中深通用色系，
    # 对全部 4 套中深画布（PLOT_FACE）≥3:1、两两 ΔE≥8，不随主题变化
    DEFAULT_HIGH_CONTRAST_CHANNELS = [
        "#ff8b8b", "#ffc06a", "#fff06b", "#72e6a4", "#58e6df",
        "#8cbcff", "#d3a8ff", "#ff90c6", "#c2ec75", "#88dcff",
    ]

    HIGH_CONTRAST_CHANNELS = {
        # 前 8 通道固定红/橙/黄/绿/青/蓝/紫/品红色相梯；办公画布更亮，
        # 对应色板提高亮度，产线画布更深，对应色板提高饱和度。
        "office_light": ["#ff9494", "#ffc36b", "#fff06b", "#76e8a7", "#5de8e0", "#91c1ff", "#d6afff", "#ff98ca", "#c5ef78", "#8cddff"],
        "office_focus": ["#ff8686", "#ffbd61", "#ffed59", "#6fe39f", "#52e1dc", "#82b7ff", "#cea4ff", "#ff8bc1", "#bced6f", "#7fd7ff"],
        "production_standard": ["#ff7d7d", "#b5ff64", "#ffe94a", "#61dc91", "#42d9d5", "#72abff", "#c797ff", "#ff7db7", "#afe860", "#6ed1ff"],
        "production_low_light": ["#ff7070", "#ffab3d", "#ffe34b", "#55d98a", "#35d6d4", "#66a5ff", "#c28eff", "#ff70ae", "#a8e650", "#61cbff"],
        # 浅灰工业：深画布（#212d36）配亮荧光色系，曲线在深底上清楚
        # 醒目（移植自 浅灰工业 分支终版，与产线深色画布思路一致）
        "grey_industrial": ["#ff5252", "#ff9100", "#ffd740", "#69f0ae", "#18ffff", "#448aff", "#b388ff", "#ff80ab", "#e040fb", "#76ff03"],
    }
    # 浅灰工业 通道色板注：中性工业炭灰画布（#242a30）与原 #212d36 亮度
    # 相当，荧光色板对比度结论可平移（test_theme_contrast 钉死）。

    @classmethod
    def default_high_contrast_colors(cls) -> list:
        return list(cls.DEFAULT_HIGH_CONTRAST_CHANNELS)

    @classmethod
    def high_contrast_colors(cls, theme_key: str | None = None) -> list:
        if theme_key is None:
            theme_key = cls._ACTIVE
        return list(cls.HIGH_CONTRAST_CHANNELS.get(theme_key, cls.DEFAULT_HIGH_CONTRAST_CHANNELS))

    @classmethod
    def activate(cls, name: str) -> None:
        """激活指定主题，将配色注入类属性。"""
        resolved = cls._resolve_theme_name(name)
        cls._ACTIVE = resolved
        palette = cls._PALETTES[resolved]
        for k, v in palette.items():
            setattr(cls, k, v)
        semantic = cls._SEMANTIC_SOURCES[resolved]
        cls.INFO = semantic["INFO"]
        cls.CURRENT = semantic["CURRENT"]
        cls.SUCCESS = cls.GREEN
        cls.WARNING = cls.ORANGE
        cls.ERROR = cls.RED

    @classmethod
    def active(cls) -> str:
        """返回当前激活的主题键。"""
        return cls._ACTIVE

    @classmethod
    def current(cls) -> str:
        return cls._ACTIVE

    @classmethod
    def _resolve_theme_name(cls, name: str) -> str:
        """安全解析主题名称（兼容大小写、2026-08-30 前旧键/别名及未知输入）。"""
        if not name or not isinstance(name, str):
            return cls.DEFAULT_THEME
        cleaned = name.strip().lower()
        if cleaned in cls._PALETTES:
            return cleaned
        if cleaned in cls._LEGACY_THEME_MAP:
            return cls._LEGACY_THEME_MAP[cleaned]
        if cleaned in cls._LEGACY_THEME_ALIASES:
            return cls._LEGACY_THEME_ALIASES[cleaned]
        return cls.DEFAULT_THEME

    @classmethod
    def configure_fonts(cls, roles: dict) -> None:
        """应用字体角色；字体不存在时由调用方传入已回退的结果。"""
        if not isinstance(roles, dict):
            return
        for key in cls.FONT_ROLES:
            value = roles.get(key)
            if isinstance(value, str) and value.strip():
                cls.FONT_ROLES[key] = value.strip()
        cls.FONT_FAMILY = [cls.FONT_ROLES["ui"], cls.FONT_ROLES["text"],
                          cls.FONT_ROLES["plot"],
                          "Source Han Sans SC", "Noto Sans SC",
                          "Microsoft YaHei UI", "Microsoft YaHei", "sans-serif"]

    @classmethod
    def font(cls, role: str = "text") -> str:
        """返回界面、正文或等宽数字字体角色。"""
        return cls.FONT_ROLES.get(role, cls.FONT_ROLES["text"])

    @classmethod
    def apply_to(cls, app: QApplication, theme_name: str | None = None) -> None:
        """将主题应用到 QApplication 实例（兼容 app.py 调用）。"""
        if theme_name:
            cls.activate(theme_name)
        app.setStyleSheet(cls.qss())

    @classmethod
    def screen_dpi(cls) -> int:
        """画布渲染 DPI：随设备像素比放大（HiDPI 防糊），下限 100。

        固定 144 之类常量在 100% 档会把画布内 pt 文字相对放大 1.44 倍，
        必须按 devicePixelRatio 动态取值；无 QApplication 时回退 100。
        """
        try:
            from PyQt5.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                return max(100, round(100 * app.devicePixelRatio()))
        except Exception:
            pass
        return 100

    @classmethod
    def font_family_css(cls, role: str = "text") -> str:
        """返回供 HTML/CSS 使用的字体回退列表。"""
        primary = cls.font(role)
        families = [primary]
        for family in cls.FONT_FAMILY:
            if family not in families and family != "sans-serif":
                families.append(family)
        families.append("sans-serif")
        quoted = ["'{}'".format(family) for family in families[:-1]]
        return ", ".join(quoted + [families[-1]])

    @classmethod
    def lighten(cls, hex_color: str, factor: float = 0.08) -> str:
        try:
            h = hex_color.lstrip('#')
            rgb = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
            new_rgb = tuple(min(255, int(c + (255 - c) * factor)) for c in rgb)
            return f"#{new_rgb[0]:02x}{new_rgb[1]:02x}{new_rgb[2]:02x}"
        except Exception:
            return hex_color

    @classmethod
    def darken(cls, hex_color: str, factor: float = 0.08) -> str:
        try:
            h = hex_color.lstrip('#')
            rgb = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
            new_rgb = tuple(max(0, int(c * (1 - factor))) for c in rgb)
            return f"#{new_rgb[0]:02x}{new_rgb[1]:02x}{new_rgb[2]:02x}"
        except Exception:
            return hex_color

    @classmethod
    def saturate(cls, hex_color: str, factor: float = 1.35) -> str:
        """提高颜色饱和度（状态栏关键文字「不加粗、靠色彩醒目」的手段）。

        灰阶（无色相）与无效色原样返回；饱和度封顶 1.0。先提饱和再交
        readable_text 做底色补偿，状态文字更艳且对比度不受损。
        """
        try:
            c = QColor(hex_color)
            if not c.isValid():
                return hex_color
            h, s, v, _ = c.getHsvF()
            if h < 0 or s <= 0:
                return hex_color
            c.setHsvF(h, min(1.0, s * factor), v)
            return c.name()
        except Exception:
            return hex_color

    # ================================================================== #
    #  引擎自适应助手（2026-08-30 从 0 重构批次新增）                     #
    # ================================================================== #
    NEAR_BLACK_FG = "#0b1220"   # 亮底（荧光绿/亮橙等）上的近黑文字

    @staticmethod
    def _relative_luminance(color: str) -> float:
        """sRGB 相对亮度（WCAG 2.1 定义）。"""
        h = color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

        def linearize(c):
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * linearize(r) + 0.7152 * linearize(g) + 0.0722 * linearize(b)

    @classmethod
    def contrast_ratio(cls, a: str, b: str) -> float:
        """两色 WCAG 对比度。"""
        la, lb = cls._relative_luminance(a), cls._relative_luminance(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    @classmethod
    def _ensure_contrast(cls, fg: str, bg: str, target: float = 4.5) -> str:
        """前景对底自动补偿至 target 对比度。

        方向按可达上限选（提亮封顶 1.05/(bgL+0.05)、加深封顶
        (bgL+0.05)/0.05——中灰底提亮物理封顶 ~3.4:1，必须按可达性选），
        两向皆可达时按前景相对背景的亮度关系选；二分逼近。"""
        if cls.contrast_ratio(fg, bg) >= target:
            return fg
        bg_l = cls._relative_luminance(bg)
        fg_l = cls._relative_luminance(fg)
        max_lighten = 1.05 / (bg_l + 0.05)
        max_darken = (bg_l + 0.05) / 0.05
        if max_lighten >= target and (max_lighten >= max_darken or fg_l > bg_l):
            prefer_light = True
        elif max_darken >= target:
            prefer_light = False
        else:
            prefer_light = max_lighten >= max_darken

        def adjusted(f):
            return cls.lighten(fg, f) if prefer_light else cls.darken(fg, f)

        lo, hi = 0.0, 1.0
        for _ in range(30):
            mid = (lo + hi) / 2
            if cls.contrast_ratio(adjusted(mid), bg) >= target:
                hi = mid
            else:
                lo = mid
            if hi - lo < 1e-3:
                break
        return adjusted(hi)

    @classmethod
    def on_color_fg(cls, bg: str) -> str:
        """填充控件前景自适应：白/近黑按底色亮度选达标者，均不达标取更优。"""
        c_white = cls.contrast_ratio("#ffffff", bg)
        c_black = cls.contrast_ratio(cls.NEAR_BLACK_FG, bg)
        if c_white >= 4.5:
            return "#ffffff"
        if c_black >= 4.5:
            return cls.NEAR_BLACK_FG
        return "#ffffff" if c_white >= c_black else cls.NEAR_BLACK_FG

    @classmethod
    def readable_text(cls, color: str, bg: str | None = None) -> str:
        """直排状态色文字的显示补偿（曲线颜色不变）：对卡底（或指定底）
        不足 4.5:1 时自动加深/提亮，保持状态色相。"""
        surface = bg if bg else cls.BG_CARD
        return cls._ensure_contrast(color, surface, 4.5)

    @classmethod
    def plot_alarm_color(cls) -> str:
        """返回趋势画布上的报警线颜色，确保至少 3:1 图形对比度。

        页面文字继续使用原始 ``RED`` 语义色；趋势画布较深时对红色做
        亮度补偿，避免安全阈值虚线被网格和曲线吞没。
        """
        return cls._ensure_contrast(cls.RED, cls.PLOT_FACE, 3.0)

    @classmethod
    def semantic_color(cls, role: str) -> str:
        """返回状态语义原色，未知角色安全回退为信息色。"""
        return getattr(cls, cls._SEMANTIC_ATTRS.get(role, "INFO"), cls.INFO)

    @classmethod
    def semantic_text(cls, role: str, bg: str | None = None,
                      disabled: bool = False) -> str:
        """返回保持色相的状态文字色。

        禁用态不使用灰色，而是在同色相下允许较低的 3:1 对比；其余可见
        文本维持 4.5:1。调用方只传角色，避免散落主题颜色判断。
        """
        surface = bg if bg else cls.BG_CARD
        target = 3.0 if disabled else 4.5
        return cls._ensure_contrast(cls.semantic_color(role), surface, target)

    @classmethod
    def semantic_fill(cls, role: str = "current") -> str:
        """返回当前态轻量着色背景，深浅主题均保持前景可读。"""
        color = cls.semantic_color(role)
        return (cls.lighten(color, 0.88) if cls.is_light_theme()
                else cls.darken(color, 0.70))

    @classmethod
    def statusbar_surface(cls) -> str:
        """状态信息面板底色（用户定稿：跟随主题骨架，不抢界面层级）。

        浅灰工业 用炭灰与工具栏呼应（深色部件覆盖表），浅色主题用卡片底
        （黑字），产线深色主题用画布加深档（白字）。
        """
        _dp = cls._DARK_PART_OVERRIDES.get(cls._ACTIVE)
        if _dp and "STATUSBAR_SURFACE" in _dp:
            return _dp["STATUSBAR_SURFACE"]
        if cls.is_light_theme():
            return cls.BG_CARD
        return cls.darken(cls.PLOT_FACE, 0.20)

    @classmethod
    def table_face(cls):
        """数据表深色部件面板（浅灰工业 专属）；None 走各控件常规配色路径。

        返回 dict（base/alt/text/sel_bg/sel_text/header_bg/header_text），
        数据弹窗等表格/树控件据此整体换深底白字，逐单元前景补偿改用
        base 作可读性底色（readable_text 的 bg 参数）。
        """
        _dp = cls._DARK_PART_OVERRIDES.get(cls._ACTIVE)
        if not _dp or "TABLE_ROW_BG" not in _dp:
            return None
        return {
            "base": _dp["TABLE_ROW_BG"],
            "alt": _dp["TABLE_ROW_ALT_BG"],
            "text": _dp["TABLE_ROW_TEXT"],
            "sel_bg": _dp["TABLE_SELECTED_BG"],
            "sel_text": _dp["TABLE_SELECTED_TEXT"],
            "header_bg": _dp["TABLE_HEADER_BG"],
            "header_text": _dp["TABLE_HEADER_TEXT"],
        }

    @classmethod
    def semantic_emphasis_qss(cls, role: str, weight: int = 600,
                              radius: int = 8, pad_x: int = 8) -> str:
        """重点状态的高亮胶囊样式（实色填充定稿：按角色二分）。

        success（采集中/已完成等运行成功态）= 微信绿实底黑字；
        其余（info/current 等）= 腾讯蓝实底白字。IP、点数、连接状态、
        当前档位和当前区域均可复用本接口。
        """
        if role == "success":
            fg, bg = "#000000", cls.RUN_FILL
        else:
            fg, bg = "#ffffff", cls.EMPHASIS_FILL
        return (f"color:{fg};background:{bg};font-weight:{weight};"
                f"letter-spacing:0.5px;padding:0 {pad_x}px;"
                f"border:1px solid {bg};border-radius:{radius}px;")

    @classmethod
    def capsule_qss(cls, color: str, weight: int = 500, radius: int = 8,
                    pad_x: int = 8) -> str:
        """状态胶囊（2026-08-30 用户定稿）：单色文字 + 同色 1px 边框，
        无底色填充；前景对卡底自动补偿至 ≥4.5:1（浅底加深、深底提亮为
        淡色系，保持状态色相）。2026-08-31 起关键状态文字不加粗，醒目
        靠饱和度与字间距（weight 默认 500 + letter-spacing）。"""
        fg = cls.readable_text(color)
        return (f"color:{fg};font-weight:{weight};letter-spacing:0.5px;"
                f"padding:0 {pad_x}px;"
                f"border:1px solid {fg};border-radius:{radius}px;")

    @classmethod
    def is_light_theme(cls, key=None) -> bool:
        """当前（或指定）主题是否属浅色表面系（active 态卡片背景提亮判定共用）。

        key 为可选主题键名（str），缺省判定当前激活主题 _ACTIVE。
        （Python 3.8 兼容：key 不加 str | None 注解）
        """
        return (key or cls._ACTIVE) in cls._LIGHT_THEMES

    @classmethod
    def card_qss(cls, radius: int = -1, hover: bool = False, active: bool = False) -> str:
        r = cls.CARD_RADIUS if radius < 0 else radius
        bg = cls.BG_CARD
        bd = cls.BORDER
        if active:
            bg = (cls.lighten(cls.BG_CARD, 0.06) if cls._ACTIVE in cls._LIGHT_THEMES
                  else cls.darken(cls.BG_CARD, 0.08))
            bd = cls.ACCENT
        elif hover:
            bg = cls.BG_HOVER
            bd = cls.INDICATOR_BORDER
        return (
            f"background: {bg};"
            f"color: {cls.TEXT};"
            f"border: 1px solid {bd};"
            f"border-radius: {r}px;"
        )

    @classmethod
    def group_box_qss(cls, radius: int | None = None) -> str:
        """QGroupBox 分区容器统一样式（细边框标题框）。

        遵循「单一主背景」原则：容器不设背景色（透明，透出页面主背景），
        分区仅靠浅色细边框 + 文字颜色 + 留白区分。标题为原生 ``::title``，
        位于边框线上（subcontrol-origin: margin），字体继承当前字号等级并
        加粗，颜色取 ``TEXT``。所有颜色/圆角全部引用已激活主题的类属性，
        切换主题后重新生成本方法返回的片段即自动跟随，调用方无需改动。

        为后续「模块开关（checkable）」预留：启用勾选框后，勾选态对勾可
        复用 _ensure_check_png() 生成的通用对勾图，无需修改本方法签名。
        """
        radius = radius if radius is not None else cls.CARD_RADIUS
        return (
            f"QGroupBox {{ background:transparent;"
            f" border:1px solid {cls.BORDER}; border-radius:{radius}px;"
            f" margin-top:12px; padding-top:6px; font-weight:600; }}"
            f"QGroupBox::title {{ subcontrol-origin: margin;"
            f" subcontrol-position: top left; left:10px;"
            f" padding:0 4px; color:{cls.TEXT}; }}"
        )

    @classmethod
    def _ensure_check_png(cls) -> str | None:
        from PyQt5.QtGui import QGuiApplication, QPixmap, QPainter, QPen, QColor
        from PyQt5.QtCore import Qt, QDir
        import os

        if QGuiApplication.instance() is None:
            return None

        path = cls._CHECK_PNG.get(cls._ACTIVE)
        if path and os.path.exists(path):
            return path

        w, h = 14, 14
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(cls.ACCENT))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawLine(3, 7, 6, 10)
        p.drawLine(6, 10, 11, 3)
        p.end()

        path = os.path.join(QDir.tempPath(), f"mta_check_{cls._ACTIVE}.png")
        pm.save(path, "PNG")
        cls._CHECK_PNG[cls._ACTIVE] = path
        return path

    @classmethod
    def _ensure_arrow_png(cls) -> str | None:
        """确保下拉箭头图标已生成并返回路径。"""
        from PyQt5.QtGui import QGuiApplication, QPixmap, QPainter, QBrush, QColor, QPolygon
        from PyQt5.QtCore import Qt, QDir, QPoint
        import os

        if QGuiApplication.instance() is None:
            return None

        path = cls._ARROW_PNG.get(cls._ACTIVE)
        if path and os.path.exists(path):
            return path

        w, h = 10, 6
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(cls.TEXT_MUTED)))
        p.drawPolygon(QPolygon([QPoint(0, 0), QPoint(w - 1, 0), QPoint(w // 2, h - 1)]))
        p.end()

        path = os.path.join(QDir.tempPath(), f"mta_arrow_{cls._ACTIVE}.png")
        pm.save(path, "PNG")
        cls._ARROW_PNG[cls._ACTIVE] = path
        return path

    @classmethod
    def _ensure_spin_arrow_png(cls, up: bool) -> str | None:
        """确保当前主题的 QSpinBox 微调三角 PNG 已生成，返回文件路径。

        全局 QSS 统一隐藏数字框的上下微调按钮；个别控件用局部样式恢复按钮
        时，QSS 不会自动绘制箭头，必须 ``image: url(...)`` 显式指定。
        这里用 QPainter 画透明底、主题文字色（TEXT_MUTED）的实心三角，
        随主题重新生成，保证每个主题下形状与颜色都正确。

        需在 QApplication 创建后才能生成像素图；import 时 activate() 调用
        一次 qss()，此时尚无 app，返回 None 即可（界面启动后会重新生成）。
        """
        from PyQt5.QtGui import QGuiApplication, QPixmap, QPainter, QBrush, QColor, QPolygon
        from PyQt5.QtCore import Qt, QDir, QPoint
        import os

        if QGuiApplication.instance() is None:
            return None

        key = (cls._ACTIVE, "up" if up else "down")
        path = cls._SPIN_ARROW_PNG.get(key)
        if path and os.path.exists(path):
            return path

        w, h = 12, 7
        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(cls.TEXT_MUTED)))
        if up:
            p.drawPolygon(QPolygon(
                [QPoint(w // 2, 0), QPoint(0, h - 1), QPoint(w - 1, h - 1)]))
        else:
            p.drawPolygon(QPolygon(
                [QPoint(0, 0), QPoint(w - 1, 0), QPoint(w // 2, h - 1)]))
        p.end()

        path = os.path.join(QDir.tempPath(),
                            f"mta_spin_{'up' if up else 'down'}_{w}x{h}_{cls._ACTIVE}.png")
        pm.save(path, "PNG")
        cls._SPIN_ARROW_PNG[key] = path
        return path

    _CLOSE_PNG = {}

    @classmethod
    def _ensure_close_png(cls) -> tuple:
        """页签关闭 X 小图标（常态=BORDER、悬停=悬停边框色，随主题重生成）。

        QSS 环境下未指定的 close-button 回退原生图标，深色主题近乎不可见，
        必须自绘；需 QApplication 存在，import 期无 app 时返回空对。
        """
        from PyQt5.QtGui import QGuiApplication, QPixmap, QPainter, QPen, QColor
        from PyQt5.QtCore import Qt, QDir
        import os

        if QGuiApplication.instance() is None:
            return "", ""

        cached = cls._CLOSE_PNG.get(cls._ACTIVE)
        if cached and all(os.path.exists(p) for p in cached):
            return cached

        paths = []
        for tag, color in (("n", cls.BORDER), ("h", cls.SCROLL_HANDLE_HOVER)):
            pm = QPixmap(12, 12)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing, True)
            pen = QPen(QColor(color))
            pen.setWidthF(1.6)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawLine(3, 3, 8, 8)
            p.drawLine(8, 3, 3, 8)
            p.end()
            path = os.path.join(QDir.tempPath(),
                                f"mta_close_{tag}_{cls._ACTIVE}.png")
            pm.save(path, "PNG")
            paths.append(path.replace("\\", "/"))
        cls._CLOSE_PNG[cls._ACTIVE] = tuple(paths)
        return cls._CLOSE_PNG[cls._ACTIVE]

    @classmethod
    def qss(cls) -> str:
        """返回全界面顶级前端 UI 优化版 QSS 样式表。"""
        _bg1   = cls.BG_PRIMARY
        _bg2   = cls.BG_CARD
        _bg3   = cls.BG_INPUT
        _bg_hover = cls.BG_HOVER
        _bd    = cls.BORDER
        _txt   = cls.readable_text(cls.TEXT, _bg1)
        _info = cls.readable_text(cls.TEXT, _bg2)
        _muted = cls._ensure_contrast(cls.TEXT_MUTED, _bg2, 3.0)
        _accent = cls.ACCENT
        _accent_hover = cls.ACCENT_HOVER
        _accent_pressed = cls.ACCENT_PRESSED
        _accent_soft = cls.ACCENT_SOFT
        _focus_ring = cls.FOCUS_RING
        _current = _txt
        _green = cls.GREEN
        _red   = cls.RED
        _grid  = cls.GRID
        _sh    = cls.SCROLL_HANDLE
        _shh   = cls.SCROLL_HANDLE_HOVER
        _sel   = cls.SEL_TEXT
        _ind   = cls.INDICATOR_BORDER
        _r     = cls.RADIUS
        _rsm   = cls.RADIUS_SM
        _rcd   = cls.CARD_RADIUS
        _ui_font = cls.font_family_css("ui")
        # 灰白蓝状态表达（2026-09-03 定稿）：文字非黑即白，hover 只换
        # 底不换字色；checked/selected/关键引导统一实色深蓝填充 + 白字，
        # 废除"低饱和 ACCENT_SOFT + 细边框"旧规；开始采集实色深蓝、
        # 结束为白底红字红边（红色仅用于安全语义文字，不做填充）。
        _hover_bd = _accent_hover
        _stop_fg = cls.RED
        _sbar = cls.statusbar_surface()
        _sbar_fg = cls.readable_text(cls.TEXT, _sbar)
        # 选中/激活态使用整行（或整按钮）实色深蓝填充 + 白字。
        _active_fill = cls.EMPHASIS_FILL
        _active_fill_fg = cls.on_color_fg(_active_fill)

        _check = cls._ensure_check_png()
        _check = _check.replace("\\", "/") if _check else ""
        _check_rule = f"image: url({_check});" if _check else ""

        _arrow = cls._ensure_arrow_png()
        _arrow = _arrow.replace("\\", "/") if _arrow else ""
        _arrow_rule = (
            "QComboBox::down-arrow {"
            f" image: url({_arrow}); width: 10px; height: 6px;"
            "}"
        ) if _arrow else ""

        _close, _close_hover = cls._ensure_close_png()
        _close_rule = (
            "QTabBar::close-button {"
            f" image: url({_close}); subcontrol-position: right;"
            " padding: 1px; }"
            "QTabBar::close-button:hover {"
            f" image: url({_close_hover}); }}"
        ) if _close else ""

        # 浅灰工业 专属部件 QSS 块：仅当当前主题注册了 _DARK_PART_OVERRIDES
        # 时注入（其余主题为空串，零影响）。块置于样式表末尾——通用规则与
        # 本块同特异性时后到优先；工具栏按钮用「QToolBar#mainToolbar
        # QToolButton」后代选择器（1 id + 2 type）压过 ID 单类型规则
        # （1 id + 1 type），悬停/选中伪态同理。
        _dp = cls._DARK_PART_OVERRIDES.get(cls._ACTIVE)
        _dark_parts = ""
        if _dp:
            _dark_parts = f"""
        /* ── 浅灰工业 部件：浅灰一体工具栏 + 白底黑字按钮 ── */
        QToolBar {{
            background: {_dp['TOOLBAR_BG']};
            border-bottom: 1px solid {_dp['TOOLBAR_BORDER']};
        }}
        QToolBar#mainToolbar QToolButton {{
            background: {_dp['TOOLBAR_BTN_BG']};
            color: {_dp['TOOLBAR_BTN_TEXT']};
            border: 1px solid {_dp['TOOLBAR_BTN_BORDER']};
        }}
        QToolBar#mainToolbar QToolButton:hover {{
            background: {_dp['TOOLBAR_BTN_HOVER_BG']};
            color: {_dp['TOOLBAR_BTN_TEXT']};
            border-color: {_dp['TOOLBAR_BTN_BORDER']};
        }}
        QToolBar#mainToolbar QToolButton:pressed {{
            background: {cls.darken(_dp['TOOLBAR_BTN_HOVER_BG'], 0.06)};
            color: {_dp['TOOLBAR_BTN_TEXT']};
            border-color: {_dp['TOOLBAR_BTN_BORDER']};
        }}
        QToolBar#mainToolbar QToolButton:checked {{
            background: {_accent};
            color: #ffffff;
            border-color: {_accent};
            font-weight: 600;
        }}
        QToolBar#mainToolbar QToolButton:checked:hover {{
            background: {cls.lighten(_accent, 0.08)};
            color: #ffffff;
        }}
        QToolBar#mainToolbar QToolButton:disabled {{
            background: #d9d9d9;
            color: #8f8f8f;
            border-color: #c2c2c2;
        }}
        /* 语义按钮在深色工具栏上保持实色语义（2 个 id 压过上面的后代规则） */
        QToolBar#mainToolbar QToolButton#toolbarStartButton {{
            background: {cls.RUN_FILL};
            color: #000000;
            border-color: {cls.RUN_FILL};
        }}
        QToolBar#mainToolbar QToolButton#toolbarStartButton:hover {{
            background: {cls.darken(cls.RUN_FILL, 0.08)};
            color: #000000;
        }}
        QToolBar#mainToolbar QToolButton#toolbarStopButton {{
            background: {_dp['TOOLBAR_BTN_BG']};
            color: {_stop_fg};
            border-color: {_stop_fg};
        }}
        QToolBar#mainToolbar QLabel {{
            color: {_dp['TOOLBAR_TEXT']};
            background: transparent;
        }}
        /* 状态栏浅灰一体（背景=STATUSBAR_SURFACE），文字由全局规则
           _sbar_fg 按底补偿为黑字，此处不再重复声明 */
        /* ── 浅灰工业 深色部件：数据表深灰行白字 ── */
        QTableWidget {{
            background: {_dp['TABLE_ROW_BG']};
            color: {_dp['TABLE_ROW_TEXT']};
            selection-background-color: {_dp['TABLE_SELECTED_BG']};
            selection-color: {_dp['TABLE_SELECTED_TEXT']};
        }}
        QHeaderView::section {{
            background: {_dp['TABLE_HEADER_BG']};
            color: {_dp['TABLE_HEADER_TEXT']};
            border-bottom: 1px solid {_dp['TOOLBAR_BORDER']};
            border-right: 1px solid rgba(255, 255, 255, 0.12);
        }}
        QTableWidget::item:selected {{
            background: {_dp['TABLE_SELECTED_BG']};
            color: {_dp['TABLE_SELECTED_TEXT']};
        }}
        QTableWidget:focus {{
            border-color: {_accent};
        }}
"""

        return f"""
        /* ── 全局根容器与窗口 ── */
        QWidget {{
            background: {_bg1};
            color: {_txt};
            font-size: 10pt;
            font-family: {_ui_font};
            outline: none;
        }}
        /* 自定义绘制覆盖卡（采集步骤卡/加载卡）：子控件会被上面的全局
           底色规则垫成方形不透明底，paintEvent 画的圆角半透明卡片之外
           露出主底色块（深色画布上四角可见白块）；显式豁免为透明，
           底色与圆角全部由各卡 paintEvent 自绘 */
        QWidget#acqStepCard, QWidget#busyCard {{
            background: transparent;
        }}
        QMainWindow, QDialog, QMessageBox {{
            background: {_bg1};
            color: {_txt};
        }}

        /* ── 顶级综合工具栏（底色=页面主底，与左侧通道/主界面连成
           一整块，仅靠下缘细线分区；浅灰工业 深色部件块在文末覆盖） ── */
        QToolBar {{
            background: {_bg1};
            border: none;
            border-bottom: 1px solid {_bd};
            spacing: 6px;
            padding: 8px 14px;
        }}
        QToolBar::handle {{
            background: transparent;
        }}
        QToolButton {{
            background: transparent;
            color: {_info};
            border: 1px solid {_bd};
            border-radius: {_r}px;
            min-height: 32px;
            padding: 4px 12px;
            font-weight: 500;
        }}
        QToolButton:hover {{
            border-color: {_hover_bd};
            color: {_current};
        }}
        QToolButton:pressed {{
            border-color: {_accent_pressed};
        }}
        QToolButton:checked {{
            background: {_active_fill};
            color: {_active_fill_fg};
            border-color: {_accent};
            font-weight: 600;
        }}
        QToolButton:disabled {{
            color: {_muted};
            border-color: {_bd};
        }}

        /* 特殊工具栏按钮（用户定稿：开始采集微信绿实底黑字；
           结束为白底红字红边——红色仅用于安全语义文字，不做填充） */
        QToolButton#toolbarStartButton {{
            background: {cls.RUN_FILL};
            color: #000000;
            border: 1px solid {cls.RUN_FILL};
            font-weight: 600;
        }}
        QToolButton#toolbarStartButton:hover {{
            background: {cls.darken(cls.RUN_FILL, 0.08)};
            border-color: {cls.RUN_FILL};
        }}
        QToolButton#toolbarStopButton {{
            background: {_bg3};
            color: {_stop_fg};
            border: 1px solid {_stop_fg};
            font-weight: 600;
        }}
        QToolButton#toolbarStopButton:hover {{
            border-color: {_stop_fg};
            background: {_bg_hover};
        }}
        QToolButton#toolbarReturnLatestButton[highlighted="true"] {{
            background: {_active_fill};
            color: {_active_fill_fg};
            border: 1px solid {_accent};
            font-weight: 600;
        }}
        /* 高亮悬停：实色填充再提亮一档 */
        QToolButton#toolbarReturnLatestButton[highlighted="true"]:hover {{
            background: {cls.lighten(cls.EMPHASIS_FILL, 0.08)};
        }}

        QLabel#toolbarGroupLabel {{
            color: {_info};
            font-size: 9pt;
            font-weight: 700;
            padding: 0 4px 0 6px;
        }}
        QLabel#toolbarFieldLabel {{
            color: {_info};
        }}
        /* 左侧面板分区标题（「通道」等）：原为构造期局部样式，切主题不
           重设会停留旧色，上收全局规则随主题自动跟随 */
        QLabel#leftSectionLabel {{
            color: {_info};
            font-weight: 600;
            font-size: {cls.FONT_SIZE}pt;
        }}
        QFrame#toolbarGroupSeparator {{
            background: {_bd};
            margin: 6px 4px;
            max-width: 1px;
        }}

        /* ── 主工具栏专属规则（独立边框与 8px 间距契约，值随通用
           QToolButton 规则保持一致；ID 规则保证通用规则改动时主工具栏
           按钮仍有保底样式，见
           test_main_toolbar_buttons_have_separate_borders_and_spacing）。
           必须同时提供 hover 与 checked 伪态：ID 基础规则特异性高于通用
           伪态规则，若缺 checked 规则，激活态背景会被锁定为 BG_INPUT、
           仅文字色从通用规则流入，深浅主题下都会变成近 1:1 对比（文字
           隐形）；服务/历史/平滑开关三个按钮一并纳入，全工具栏行为统一 ── */
        QToolBar#mainToolbar {{
            spacing: 8px;
        }}
        QToolButton#toolbarOpenButton,
        QToolButton#toolbarConnectButton,
        QToolButton#toolbarPauseButton,
        QToolButton#toolbarRestartButton,
        QToolButton#toolbarLiveViewButton,
        QToolButton#toolbarReturnLatestButton,
        QToolButton#toolbarExportButton,
        QToolButton#toolbarSettingsButton,
        QToolButton#toolbarRemoteButton,
        QToolButton#toolbarHistoryButton,
        QToolButton#toolbarSmoothSwitch {{
            background: transparent;
            border: 1px solid {_bd};
            border-radius: {_r}px;
            padding: 4px 12px;
        }}
        QToolButton#toolbarOpenButton:hover,
        QToolButton#toolbarConnectButton:hover,
        QToolButton#toolbarPauseButton:hover,
        QToolButton#toolbarRestartButton:hover,
        QToolButton#toolbarLiveViewButton:hover,
        QToolButton#toolbarReturnLatestButton:hover,
        QToolButton#toolbarExportButton:hover,
        QToolButton#toolbarSettingsButton:hover,
        QToolButton#toolbarRemoteButton:hover,
        QToolButton#toolbarHistoryButton:hover,
        QToolButton#toolbarSmoothSwitch:hover {{
            border-color: {_hover_bd};
        }}
        QToolButton#toolbarOpenButton:checked,
        QToolButton#toolbarConnectButton:checked,
        QToolButton#toolbarStartButton:checked,
        QToolButton#toolbarPauseButton:checked,
        QToolButton#toolbarStopButton:checked,
        QToolButton#toolbarRestartButton:checked,
        QToolButton#toolbarLiveViewButton:checked,
        QToolButton#toolbarReturnLatestButton:checked,
        QToolButton#toolbarExportButton:checked,
        QToolButton#toolbarSettingsButton:checked,
        QToolButton#toolbarRemoteButton:checked,
        QToolButton#toolbarHistoryButton:checked,
        QToolButton#toolbarSmoothSwitch:checked {{
            background: {_active_fill};
            color: {_active_fill_fg};
            border-color: {_accent};
            font-weight: 600;
        }}
        /* 连接按钮状态着色（connState 由 MainWindow._sync_connect_button 维护；
           ID+属性选择器特异性高于上方通用 :disabled/:checked 组，主题切换后
           QSS 整体重建即恢复配色） */
        QToolButton#toolbarConnectButton[connState="connected"] {{
            background: {cls.RUN_FILL};
            color: #000000;
            border: 1px solid {cls.RUN_FILL};
            font-weight: 600;
        }}
        QToolButton#toolbarConnectButton[connState="connected"]:hover {{
            background: {cls.darken(cls.RUN_FILL, 0.08)};
            border-color: {cls.RUN_FILL};
        }}
        QToolButton#toolbarConnectButton[connState="connected"]:disabled {{
            background: {cls.darken(cls.RUN_FILL, 0.25)};
            color: #000000;
            border-color: {cls.darken(cls.RUN_FILL, 0.15)};
        }}
        QLabel#statusSourceLabel {{
            padding-left: 8px;
            font-weight: 500;
        }}

        /* ── 下拉菜单 ── */
        QMenu {{
            background: {_bg2};
            color: {_info};
            border: 1px solid {_bd};
            border-radius: {_rcd}px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 6px 27px 6px 15px;
            border: 1px solid transparent;
            border-radius: {_rsm}px;
            margin: 2px 0;
        }}
        QMenu::item:selected {{
            background: {_active_fill};
            border-color: {_active_fill};
            color: #ffffff;
        }}
        QMenu::item:disabled {{
            color: {_muted};
        }}
        QMenu::separator {{
            height: 1px;
            background: {_bd};
            margin: 5px 8px;
        }}

        /* ── 卡片与容器面板 ── */
        QFrame#acqCard, QFrame#paramCard, QWidget#cardContainer {{
            background: {_bg2};
            color: {_txt};
            border: 1px solid {_bd};
            border-radius: {_rcd}px;
        }}
        QFrame#exportDrawerOperation {{
            background: {_bg2};
            border: 1px solid {_bd};
            border-radius: 6px;
        }}

        /* ── 标签页 QTabWidget（下划线式：文字不与边框重叠）── */
        QTabWidget::pane {{
            background: {_bg2};
            border: 1px solid {_bd};
            border-top: none;
            border-radius: 0 0 {_rcd}px {_rcd}px;
        }}
        QTabBar {{
            background: transparent;
            border-bottom: 1px solid {_bd};
        }}
        QTabBar::tab {{
            background: transparent;
            color: {_info};
            padding: 8px 20px;
            border: none;
            border-bottom: 2px solid transparent;
            font-size: {cls.FONT_SIZE}pt;
            font-family: {_ui_font};
        }}
        QTabBar::tab:hover {{
            color: {_current};
        }}
        QTabBar::tab:selected {{
            background: {_active_fill};
            color: {_active_fill_fg};
            border: 1px solid {_accent};
            border-bottom: 3px solid {_accent};
            font-weight: 600;
        }}

        /* ── 输入框 QLineEdit ── */
        QLineEdit {{
            background: {_bg3};
            color: {_info};
            border: 1px solid {_bd};
            border-radius: {_r}px;
            padding: 6px 10px;
            selection-background-color: {_accent};
            selection-color: {_sel};
        }}
        QLineEdit:hover {{
            border-color: {_hover_bd};
        }}
        QLineEdit:focus {{
            border-color: {_focus_ring};
        }}
        QLineEdit:disabled {{
            background: {_bg1};
            color: {_muted};
            border-color: {_bd};
        }}

        /* ── 下拉框 QComboBox ── */
        QComboBox {{
            background: {_bg3};
            color: {_info};
            border: 1px solid {_bd};
            border-radius: {_r}px;
            padding: 6px 12px;
            min-height: 22px;
        }}
        QComboBox:hover {{
            border-color: {_hover_bd};
        }}
        QComboBox:focus {{
            border-color: {_focus_ring};
        }}
        QComboBox[semanticRole="current"] {{
            background: {_active_fill};
            color: {_active_fill_fg};
            border-color: {_accent};
            font-weight: 600;
        }}
        QComboBox:disabled {{
            color: {_muted};
            border-color: {_bd};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 26px;
        }}
        {_arrow_rule}
        QComboBox QAbstractItemView {{
            background: {_bg2};
            color: {_info};
            border: 1px solid {_bd};
            border-radius: {_r}px;
            padding: 4px;
            selection-background-color: {_active_fill};
            selection-color: #ffffff;
            outline: none;
        }}
        QComboBox QAbstractItemView::item {{
            padding: 5px 9px;
            border: 1px solid transparent;
            border-radius: {_rsm}px;
        }}
        QComboBox QAbstractItemView::item:selected {{
            background: {_active_fill};
            border-color: {_active_fill};
            color: #ffffff;
        }}

        /* ── 按钮 QPushButton ── */
        QPushButton {{
            background: transparent;
            color: {_info};
            border: 1px solid {_bd};
            border-radius: {_r}px;
            padding: 7px 18px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {_hover_bd};
            color: {_current};
        }}
        QPushButton:pressed {{
            border-color: {_accent_pressed};
        }}
        QPushButton:focus {{
            border-color: {_focus_ring};
        }}
        QPushButton:checked,
        QPushButton[buttonRole="primary"]:enabled {{
            background: {_active_fill};
            color: {_active_fill_fg};
            border-color: {_accent};
        }}
        QPushButton:checked:hover,
        QPushButton[buttonRole="primary"]:enabled:hover {{
            background: {_active_fill};
            border-color: {_accent};
        }}
        QPushButton:checked:pressed,
        QPushButton[buttonRole="primary"]:enabled:pressed {{
            background: {_accent_pressed};
            color: {cls.on_color_fg(_accent_pressed)};
        }}
        QPushButton:disabled {{
            color: {_muted};
            border-color: {_bd};
        }}
        QPushButton[buttonRole="primary"]:disabled {{
            background: {_bg3};
            color: {_muted};
        }}

        /* ── 复选框 QCheckBox ── */
        QCheckBox {{
            color: {_info};
            spacing: 8px;
            background: transparent;
        }}
        QCheckBox[semanticRole="current"] {{
            color: {_current};
            font-weight: 600;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {_ind};
            border-radius: {_rsm}px;
            background: {_bg3};
        }}
        QCheckBox::indicator:hover {{
            border-color: {_hover_bd};
        }}
        QCheckBox::indicator:checked {{
            background: {_bg3};
            border-color: {_accent};
            {_check_rule}
        }}
        QCheckBox:disabled, QRadioButton:disabled {{
            color: {_muted};
        }}

        /* ── 单选框 QRadioButton ── */
        QRadioButton {{
            color: {_info};
            spacing: 8px;
            background: transparent;
        }}
        QRadioButton::indicator {{
            width: 16px;
            height: 16px;
            border: 1px solid {_ind};
            border-radius: 8px;
            background: {_bg3};
        }}
        QRadioButton::indicator:hover {{
            border-color: {_accent};
        }}
        QRadioButton::indicator:checked {{
            background: {_accent};
            border: 4px solid {_bg3};
        }}

        /* ── 表格 QTableWidget ── */
        QTableWidget {{
            background: {_bg2};
            color: {_info};
            border: 1px solid {_bd};
            border-radius: {_r}px;
            gridline-color: transparent;
            selection-background-color: {_active_fill};
            selection-color: {_active_fill_fg};
        }}
        QHeaderView::section {{
            background: {_bg3};
            color: {_info};
            border: none;
            border-bottom: 1px solid {_bd};
            border-right: 1px solid {_bd};
            padding: 8px 10px;
            font-weight: 600;
        }}
        QTableWidget::item {{
            padding: 5px;
            border: none;
        }}
        QTableWidget::item:selected {{
            background: {_active_fill};
            color: {_active_fill_fg};
        }}
        QTableWidget:focus {{
            border-color: {_focus_ring};
        }}

        /* ── 标签与文字 ── */
        QLabel {{
            color: {_info};
            background: transparent;
        }}

        /* ── 数字/日期时间输入框（QDateTimeEdit 是 QAbstractSpinBox 平行子类，须显式列出） ── */
        QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{
            background: {_bg3};
            color: {_info};
            border: 1px solid {_bd};
            border-radius: {_r}px;
            padding: 6px 10px;
        }}
        QSpinBox:hover, QDoubleSpinBox:hover,
        QDateEdit:hover, QTimeEdit:hover, QDateTimeEdit:hover {{
            border-color: {_hover_bd};
        }}
        QSpinBox:focus, QDoubleSpinBox:focus,
        QDateEdit:focus, QTimeEdit:focus, QDateTimeEdit:focus {{
            border-color: {_focus_ring};
        }}
        QSpinBox:disabled, QDoubleSpinBox:disabled,
        QDateEdit:disabled, QTimeEdit:disabled, QDateTimeEdit:disabled {{
            color: {_muted};
            background: {_bg1};
            border-color: {_bd};
        }}
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            width: 0;
            height: 0;
            border: none;
            background: transparent;
        }}

        /* ── 文本浏览框与状态栏 ── */
        QTextBrowser, QTextEdit {{
            background: {_bg2};
            color: {_info};
            border: 1px solid {_bd};
            border-radius: {_r}px;
            padding: 8px;
        }}
        QStatusBar {{
            background: {_sbar};
            color: {_sbar_fg};
            border-top: 1px solid {_bd};
            padding: 2px 8px;
        }}
        QFrame#statusBarSeparator {{
            background: {_bd};
            min-width: 1px;
            max-width: 1px;
            border: none;
            margin: 4px 3px;
        }}

        /* ── 滚动条 QScrollBar ── */
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical {{
            background: {_sh};
            border-radius: 3px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {_shh};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
            background: none;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
            margin: 2px;
        }}
        QScrollBar::handle:horizontal {{
            background: {_sh};
            border-radius: 3px;
            min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {_shh};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
            background: none;
        }}

        /* ── 悬浮提示 QToolTip ── */
        QToolTip {{
            background: {_bg2};
            color: {_txt};
            border: 1px solid {_bd};
            padding: 4px 8px;
        }}

        /* ── 进度条 QProgressBar（量值指示：中性轨道+强调色细条，非状态告警用色） ── */
        QProgressBar {{
            background: {_bg3};
            border: 1px solid {_bd};
            border-radius: {_rsm}px;
            text-align: center;
            color: {_txt};
        }}
        QProgressBar::chunk {{
            background: {_accent};
            border-radius: {_rsm}px;
        }}

        /* ── 树/列表全局三态（边框+文字表达，统一消灭各处局部补丁差异） ── */
        QTreeWidget, QTreeView, QListWidget, QListView {{
            background: {_bg2};
            color: {_txt};
            border: 1px solid {_bd};
            border-radius: {_r}px;
            selection-background-color: {_active_fill};
            selection-color: {_active_fill_fg};
            outline: none;
        }}
        QTreeWidget::item, QTreeView::item,
        QListWidget::item, QListView::item {{
            border: none;
            padding: 5px 6px;
        }}
        QTreeWidget::item:hover, QTreeView::item:hover,
        QListWidget::item:hover, QListView::item:hover {{
            background: {_bg_hover};
        }}
        QTreeWidget::item:selected, QTreeView::item:selected,
        QListWidget::item:selected, QListView::item:selected {{
            background: {_active_fill};
            color: {_active_fill_fg};
        }}
        QTreeWidget:focus, QTreeView:focus,
        QListWidget:focus, QListView:focus {{
            border-color: {_focus_ring};
        }}

        /* ── 分割条：1px 可见 + 悬停反馈 ── */
        QSplitter::handle {{
            background: {_bd};
        }}
        QSplitter::handle:hover {{
            background: {_hover_bd};
        }}

        /* ── 页签关闭钮：自绘 X 小图标，深浅主题均可见 ── */
        {_close_rule}
        {_dark_parts}
        """

    @classmethod
    def mpl_params(cls) -> dict:
        return {
            "figure.facecolor": cls.PLOT_FACE,   # 画布三键与界面骨架解耦（两分区）
            "axes.facecolor":   cls.PLOT_FACE,
            "axes.edgecolor":   cls.PLOT_AXIS,
            "axes.labelcolor":  cls.PLOT_TEXT,
            "xtick.color":      cls.PLOT_TEXT,
            "ytick.color":      cls.PLOT_TEXT,
            "grid.color":       cls.PLOT_GRID,
            "text.color":       cls.PLOT_TEXT,
            # sans-serif 别名只解析为一个最佳匹配字体（ mpl 3.5 打包基线
            # 更是完全没有字形回退），首位必须是无 CJK 缺口的图表字体；
            # 若放 Segoe UI，所有走 rcParams 默认字体的中文（温升统计页
            # 标题/轴标签/图例等未显式指定 family 的文本）都会渲染成方块
            "font.sans-serif":  [cls.font("plot"), "Noto Sans SC",
                                 "Source Han Sans SC", "Segoe UI", "DejaVu Sans"],
            "font.family":      ["sans-serif"],
            "axes.unicode_minus": False,
        }

    @classmethod
    def styled_button(cls, variant: str = "default") -> str:
        """生成变体按钮样式；主操作使用浅色填充突出可执行状态。"""
        color_map = {
            "default": (cls.BORDER, cls.TEXT),
            "primary": (cls.ACCENT, cls.ACCENT),
            "success": (cls.GREEN, cls.GREEN),
            "danger":  (cls.RED, cls.RED),
            "warning": (cls.ORANGE, cls.ORANGE),
        }
        bd_color, semantic = color_map.get(variant, color_map["default"])
        if variant == "default":
            txt_color = cls.TEXT
            hover_bd = cls.SCROLL_HANDLE_HOVER
            pressed_bd = cls.ACCENT
        else:
            txt_color = cls.readable_text(semantic, cls.BG_CARD)
            if cls._ACTIVE in cls._LIGHT_THEMES:
                hover_bd = cls.darken(semantic, 0.25)
                pressed_bd = cls.darken(semantic, 0.45)
            else:
                hover_bd = cls.lighten(semantic, 0.3)
                pressed_bd = cls.lighten(semantic, 0.5)
        active_bg = cls.lighten(cls.ACCENT, 0.82)
        active_fg = cls.on_color_fg(active_bg)
        if variant == "primary":
            primary_state = f"background: {active_bg};"
            txt_color = active_fg
        else:
            primary_state = "background: transparent;"
        return f"""
        QPushButton {{
            {primary_state}
            color: {txt_color};
            border: 1px solid {bd_color};
            border-radius: {cls.RADIUS}px;
            padding: 7px 18px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            border-color: {hover_bd};
        }}
        QPushButton:pressed {{
            background: {cls.ACCENT if variant == 'primary' else 'transparent'};
            color: {cls.on_color_fg(cls.ACCENT) if variant == 'primary' else txt_color};
            border-color: {pressed_bd};
        }}
        QPushButton:disabled {{
            background: {cls.BG_INPUT};
            color: {cls.TEXT_MUTED};
            border-color: {cls.BORDER};
        }}
        """


Theme.activate(Theme.DEFAULT_THEME)
