# -*- coding: utf-8 -*-
"""
AxisBasePanel — 左侧面板底部温度轴基础窗口快速调节组件。

温度轴统一为智能判断模式后，基础窗口（下限/上限）在主界面左侧底部直接调节，
修改即时生效并自动保存；同时只读显示当前实际生效的温度轴范围。

上下微调按钮使用原生 QSpinBox：点击命中由 Qt 处理（上=加、下=减）。
注意：给 QSpinBox 的 up-button/down-button 设置尺寸时必须保证两钮总高
不超过输入框高度，否则按钮重叠会导致点击方向错乱。箭头图用 QPainter
生成的 PNG（全局 QSS 隐藏了原生箭头，需 image: url(...) 显式绘制）。
"""
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSpinBox,
                             QStyle, QStyleOptionSpinBox, QVBoxLayout)

from ui.theme import Theme


class AxisBasePanel(QFrame):
    """温度轴基础窗口 下限/上限 输入 + 当前生效范围只读标签。"""

    # 基础窗口可设定的温度边界（℃）：超出该范围对现场测量无意义
    BASE_MIN = -50
    BASE_MAX = 200

    # 微调按钮尺寸（像素）：两钮高度之和必须 ≤ 输入框高度，避免重叠错乱
    BUTTON_W = 24
    BUTTON_H = 17

    valueChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("axisQuickCard")
        self._updating = False  # set_base / 边界互斥回写时不触发 valueChanged
        self._arrow_audited = False  # 命中区自检只跑一次（首次显示后）

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 6)
        outer.setSpacing(4)

        self._title = QLabel("温度轴基础窗口")
        outer.addWidget(self._title)

        # 标签与输入框分行：标签居中于对应输入框上方，输入框独占整行宽度，
        # 可做得更大、三角按钮更易点按；两框之间保留间距避免挨得太近
        self._lbl_lo = self._unit_label("下限℃")
        self._lbl_lo.setAlignment(Qt.AlignCenter)
        self._lbl_hi = self._unit_label("上限℃")
        self._lbl_hi.setAlignment(Qt.AlignCenter)
        self.sp_lo = self._make_spin()
        self.sp_hi = self._make_spin()

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(18)
        header.addWidget(self._lbl_lo, 1)
        header.addWidget(self._lbl_hi, 1)
        outer.addLayout(header)

        box_row = QHBoxLayout()
        box_row.setContentsMargins(0, 0, 0, 0)
        box_row.setSpacing(18)
        box_row.addWidget(self.sp_lo, 1)
        box_row.addWidget(self.sp_hi, 1)
        outer.addLayout(box_row)

        self.lbl_range = QLabel("当前范围 —")
        self.lbl_range.setAlignment(Qt.AlignRight)
        outer.addWidget(self.lbl_range)

        # 集中生成全部主题相关局部样式（主题切换时调用 refresh_theme 重新生成）
        self._apply_theme_style()

        self.sp_lo.valueChanged.connect(self._emit_changed)
        self.sp_hi.valueChanged.connect(self._emit_changed)

    # ------------------------------------------------------------------ #
    #  主题
    # ------------------------------------------------------------------ #

    def _apply_theme_style(self) -> None:
        """按当前主题生成全部局部样式（构造与 refresh_theme 共用）。"""
        sub = max(8, Theme.FONT_SIZE - 1)
        self.setStyleSheet(Theme.card_qss())
        self._title.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};font-weight:600;font-size:{sub}pt;"
            f"background:transparent;border:none;")
        self._lbl_lo.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};background:transparent;border:none;")
        self._lbl_hi.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};background:transparent;border:none;")
        self.lbl_range.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};font-size:{sub}pt;"
            f"background:transparent;border:none;")
        for sp in (self.sp_lo, self.sp_hi):
            self._style_spin(sp)

    def refresh_theme(self) -> None:
        """运行时切换主题后调用：按新主题重新生成局部样式。"""
        self._apply_theme_style()
        if self.isVisible():
            self._audit_arrow_hit_geometry("主题刷新")

    def showEvent(self, event):
        """首次显示后自检三角按钮命中区（延迟到样式生效后执行一次）。"""
        super().showEvent(event)
        if not self._arrow_audited:
            self._arrow_audited = True
            QTimer.singleShot(0, lambda: self._audit_arrow_hit_geometry("首次显示"))

    def _audit_arrow_hit_geometry(self, tag: str) -> None:
        """运行期自检 ▲/▼ 微调按钮命中区是否相交 / 越出输入框。

        QSpinBox 原生按钮的点击命中完全由 Qt 按样式几何（QSS 声明的
        up/down-button 尺寸与 padding）决定；若实际输入框高度容纳不下
        两钮总高（高 DPI、全局字体或主题字号变化时可能发生），会出现
        “点三角偶尔没反应 / 方向错乱”的偶发失效（a64bed9 曾修过一版，
        此处留运行期看门狗便于真机直接由日志定位）。正常几何静默通过，
        只在异常时打一条 [AXIS] 告警日志。
        """
        try:
            for sp in (self.sp_lo, self.sp_hi):
                opt = QStyleOptionSpinBox()
                opt.initFrom(sp)
                style = sp.style()
                up = style.subControlRect(QStyle.CC_SpinBox, opt,
                                          QStyle.SC_SpinBoxUp, sp)
                dn = style.subControlRect(QStyle.CC_SpinBox, opt,
                                          QStyle.SC_SpinBoxDown, sp)
                abnormal = (not up.isValid() or not dn.isValid()
                            or up.intersects(dn)
                            or up.bottom() > sp.height()
                            or dn.bottom() > sp.height())
                if abnormal:
                    print(f"[AXIS] 温度轴微调按钮命中区异常（{tag}）："
                          f"框高 {sp.height()}px，上钮 {up}、下钮 {dn}"
                          f" —— 两钮相交/越界会导致点击偶发失效或方向错乱，"
                          f"请保留此日志反馈", flush=True)
                    return
        except Exception as e:
            print(f"[AXIS] 微调按钮命中区自检失败: {e}", flush=True)

    def _style_spin(self, sp: QSpinBox) -> None:
        """生成数字框局部样式：恢复被全局 QSS 隐藏的上下微调三角。

        全局 QSS 对按钮/箭头设了 width:0、height:0、image:none，局部样式
        必须显式覆盖；两钮高度之和 ≤ 框高（见 BUTTON_H 与 setMinimumHeight），
        保证 Qt 原生命中测试正确。箭头用当前主题色的 PNG（TEXT_MUTED）。
        """
        up_path = Theme._ensure_spin_arrow_png(True)
        down_path = Theme._ensure_spin_arrow_png(False)
        up_path = up_path.replace("\\", "/") if up_path else ""
        down_path = down_path.replace("\\", "/") if down_path else ""
        up_rule = f"image: url({up_path});" if up_path else ""
        down_rule = f"image: url({down_path});" if down_path else ""
        sp.setStyleSheet(
            f"QSpinBox {{"
            f" background:{Theme.BG_INPUT}; color:{Theme.TEXT};"
            f" border:1px solid {Theme.BORDER};"
            f" border-radius:{Theme.RADIUS_SM}px;"
            f" padding:4px 8px; }}"
            f"QSpinBox::up-button, QSpinBox::down-button {{"
            f" width:{self.BUTTON_W}px; height:{self.BUTTON_H}px;"
            f" border:none; background:transparent; }}"
            f"QSpinBox::up-arrow {{"
            f" {up_rule} width:12px; height:7px; }}"
            f"QSpinBox::down-arrow {{"
            f" {down_rule} width:12px; height:7px; }}")

    def _unit_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};background:transparent;border:none;")
        return lbl

    def _make_spin(self) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(self.BASE_MIN, self.BASE_MAX)
        sp.setSingleStep(1)
        # QSpinBox 原生最小宽度较大（约 156px），两框并排会撑爆左栏；
        # 限制最大宽度并提高最小高度，保证框够大、三角按钮好点按。
        # 最小高度需 ≥ 2 × BUTTON_H + 边框余量（两钮不重叠，命中才正确）。
        sp.setMaximumWidth(120)
        sp.setMinimumHeight(2 * self.BUTTON_H + 2)
        # 键盘输入：回车 / 失焦提交（基础窗口为整数，无小数）
        sp.setKeyboardTracking(False)
        return sp

    def _emit_changed(self, *_):
        """值变化：先做上下限互斥（保证 下限 < 上限），再通知外部保存刷新。"""
        if self._updating:
            return
        self._updating = True
        try:
            lo, hi = self.sp_lo.value(), self.sp_hi.value()
            if lo >= hi:
                # 谁在动就回退谁：下限追上上限 → 回到上限-1；上限降到下限 → 回到下限+1
                if self.sender() is self.sp_lo:
                    self.sp_lo.setValue(hi - 1)
                else:
                    self.sp_hi.setValue(lo + 1)
        finally:
            self._updating = False
        self.valueChanged.emit()

    def set_base(self, lo: float, hi: float) -> None:
        """程序赋值基础窗口（不触发 valueChanged）。整数输入框按四舍五入取整。"""
        self._updating = True
        try:
            self.sp_lo.setValue(int(round(float(lo))))
            self.sp_hi.setValue(int(round(float(hi))))
        finally:
            self._updating = False

    def base(self):
        """返回当前基础窗口 (下限, 上限)。"""
        return self.sp_lo.value(), self.sp_hi.value()

    def set_current_range(self, lo: float, hi: float) -> None:
        """更新只读「当前生效范围」标签。"""
        self.lbl_range.setText(f"当前范围 [{lo:g}, {hi:g}]")
