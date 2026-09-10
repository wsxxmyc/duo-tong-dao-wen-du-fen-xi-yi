# -*- coding: utf-8 -*-
"""DataEditDialog —— 历史/已结束会话的数据编辑弹窗（重采样 + 平滑）。

业务定位（2026-08-22 平滑/重采样拆分）：
  - 平滑 = 实时采集的显示层滤波（全局开关，仅录制中的 live 会话）；
  - 重采样 = 完成采集后的编辑操作，按会话参数生效，本弹窗是其唯一入口。

弹窗只写 MainWindow._session_edit_params[session.id] 并触发 apply_and_refresh；
原始数据（Session.buffer）、报警判定、数据库落盘、TPX 导出均不受影响。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QGroupBox,
                             QHBoxLayout, QLabel, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from device.datastore.session import SOURCE_LIVE
from ui.theme import Theme
from ui.widgets.toggle_switch import ToggleSwitch


class DataEditDialog(QDialog):
    """对当前活跃（且不在录制中的）会话做后处理编辑。"""

    def __init__(self, main_window, session):
        super().__init__(main_window)
        self.mw = main_window
        self.session = session
        self.setObjectName("dataEditDialog")
        self.setWindowTitle(f"编辑数据 — {session.title or session.id[:8]}")
        self.setFixedSize(540, 520)
        # 分区容器（_section 的 QGroupBox#sectionCard）统一挂全局组框样式：
        # 透明底 + 1px 细边框 + 原生 ::title（与设置弹窗/服务页同一规范），
        # 弹窗自身原先无组框规则，QGroupBox 会保持系统原生框线
        self.setStyleSheet(Theme.group_box_qss())

        editable = not (session.is_live and session.is_recording)
        edit = dict(main_window._edit_params_for(session))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        # ── 会话信息行：来源 / 通道数 / 点数 ──
        self._session_info_label = QLabel(
            f"来源：{self._source_text()}　·　{len(session.channels)} 通道　·　{session.n} 点")
        self._session_info_label.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};font-size:10pt;")
        lay.addWidget(self._session_info_label)

        intro = QLabel(
            "对已完成的采集数据做后处理编辑：重采样压缩数据点、平滑抑制毛刺。"
            "仅影响曲线显示与处理后导出，不改动原始采集值。")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{Theme.TEXT_MUTED};line-height:1.5;")
        self._intro_label = intro
        lay.addWidget(intro)

        # 参数卡标题/说明标签登记（refresh_theme 重放内联样式用）
        self._card_title_labels = []
        self._card_desc_labels = []

        def _card(title, control, desc):
            card = QWidget()
            card.setObjectName("paramCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(2, 4, 2, 4)
            cl.setSpacing(4)
            top = QHBoxLayout()
            top.setSpacing(8)
            nm = QLabel(title)
            nm.setStyleSheet(
                f"font-weight:600;color:{Theme.TEXT};")
            self._card_title_labels.append(nm)
            top.addWidget(nm)
            top.addStretch(1)
            top.addWidget(control)
            cl.addLayout(top)
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setStyleSheet(f"color:{Theme.TEXT_MUTED};line-height:1.6;")
            self._card_desc_labels.append(d)
            cl.addWidget(d)
            return card

        # ── 重采样区 ──
        self.chk_resample = ToggleSwitch("启用重采样")
        self.chk_resample.setChecked(bool(edit.get("resample", False)))
        self.sp_resample = QDoubleSpinBox()
        self.sp_resample.setRange(0.5, 3600.0)
        self.sp_resample.setDecimals(1)
        self.sp_resample.setValue(float(edit.get("resample_int", 30.0)))
        self.sp_resample.setMaximumWidth(160)
        self.cmb_resample = QComboBox()
        self.cmb_resample.addItems(["区间平均", "最近抽取"])
        self.cmb_resample.setCurrentIndex(int(edit.get("resample_method", 0)))
        self.cmb_resample.setMaximumWidth(160)
        resample_body = QVBoxLayout()
        resample_body.setContentsMargins(0, 0, 0, 0)
        resample_body.setSpacing(4)
        resample_body.addWidget(_card(
            "重采样间隔（秒）", self.sp_resample,
            "重采样的目标间隔；与「重采样方式」配合决定如何抽取点。"))
        resample_body.addWidget(_card(
            "重采样方式", self.cmb_resample,
            "区间平均：取区间内平均温度，最平滑；最近抽取：取区间内最后一个点。"))
        lay.addWidget(self._section(
            "重采样（降采样）", self.chk_resample, resample_body,
            [self.sp_resample, self.cmb_resample]))

        # ── 平滑区 ──
        self.chk_smooth = ToggleSwitch("启用平滑")
        self.chk_smooth.setChecked(bool(edit.get("smooth", False)))
        self.sp_smooth = QSpinBox()
        self.sp_smooth.setRange(1, 99)
        self.sp_smooth.setValue(int(edit.get("smooth_w", 5)))
        self.sp_smooth.setMaximumWidth(160)
        smooth_body = QVBoxLayout()
        smooth_body.setContentsMargins(0, 0, 0, 0)
        smooth_body.setSpacing(4)
        smooth_body.addWidget(_card(
            "平滑窗口（点）", self.sp_smooth,
            "参与平均的相邻点数；窗口越大越平滑，快速变化和峰值可能被削弱。"))
        lay.addWidget(self._section(
            "整体平滑", self.chk_smooth, smooth_body, [self.sp_smooth]))

        # ── 按钮行 + 状态行 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._apply_button = QPushButton("应用并刷新")
        self._apply_button.setProperty("buttonRole", "primary")
        self._apply_button.clicked.connect(self._apply)
        btn_reset = QPushButton("恢复未处理")
        btn_reset.setProperty("buttonRole", "secondary")
        btn_reset.clicked.connect(self._reset_to_raw)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        btn_row.addWidget(self._apply_button)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_close)
        lay.addLayout(btn_row)

        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setStyleSheet(f"color:{Theme.TEXT_MUTED};")
        lay.addWidget(self._status_label)

        if not editable:
            self._apply_button.setEnabled(False)
            btn_reset.setEnabled(False)
            self._set_status("实时采集进行中，不能编辑数据")

    # ------------------------------------------------------------------
    def _source_text(self):
        """会话来源文案：本机采集 / 历史数据库 / 远端数据 / 文件导入。"""
        s = self.session
        if s.source == SOURCE_LIVE:
            return "本机采集"
        path = s.path or ""
        if path.startswith("db://"):
            return "历史数据库"
        if path.startswith("remote://"):
            return "远端数据"
        return "文件导入"

    def _section(self, title, switch, body_lay, controls):
        """标题行（开关）+ 参数体分区；开关只控制参数启用（置灰），参数常显。"""
        group = QGroupBox(title)
        group.setObjectName("sectionCard")
        v = QVBoxLayout(group)
        v.setContentsMargins(8, 6, 8, 8)
        v.setSpacing(6)
        header = QHBoxLayout()
        header.addWidget(switch)
        header.addStretch(1)
        v.addLayout(header)
        v.addLayout(body_lay)
        for c in controls:
            c.setEnabled(switch.isChecked())
        switch.toggled.connect(
            lambda on: self._set_controls_enabled(controls, on))
        return group

    @staticmethod
    def _set_controls_enabled(controls, on):
        for c in controls:
            c.setEnabled(bool(on))

    def _apply(self):
        """写入会话编辑参数并立即重算刷新。"""
        self.mw._session_edit_params[self.session.id] = {
            "resample": self.chk_resample.isChecked(),
            "resample_int": self.sp_resample.value(),
            "resample_method": self.cmb_resample.currentIndex(),
            "smooth": self.chk_smooth.isChecked(),
            "smooth_w": self.sp_smooth.value(),
        }
        self.mw.apply_and_refresh()
        self._set_status("已应用并刷新")

    def _reset_to_raw(self):
        """恢复未处理：重采样与平滑全部关闭后应用。"""
        self.chk_resample.setChecked(False)
        self.chk_smooth.setChecked(False)
        self._apply()
        self._set_status("已恢复未处理状态")

    def _set_status(self, text):
        self._status_label.setText(text)
        # 状态色经 readable_text 对弹窗底补偿（工业标准：文字补偿、无底色填充）
        raw = Theme.GREEN if text.startswith("已") else Theme.ORANGE
        color = Theme.readable_text(raw, Theme.BG_PRIMARY)
        self._status_label.setStyleSheet(
            f"color:{color};font-weight:600;")
        if text.startswith("已"):
            # 瞬时反馈 3 秒后淡出；用绑定方法槽避免无接收者 lambda 在销毁后触发崩溃
            QTimer.singleShot(3000, self._status_label.clear)

    def refresh_theme(self) -> None:
        """切主题后重放全部构造期固化的内联 Theme 样式（6 处）。

        弹窗为非模态：主题可在弹窗打开期间切换，构造期 format 出的
        颜色不会自动跟随，须按同款表达式重设。
        """
        # 组框边框/标题色同样构造期固化，随主题重放
        self.setStyleSheet(Theme.group_box_qss())
        self._session_info_label.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};font-size:10pt;")
        self._intro_label.setStyleSheet(
            f"color:{Theme.TEXT_MUTED};line-height:1.5;")
        for nm in getattr(self, "_card_title_labels", []):
            nm.setStyleSheet(f"font-weight:600;color:{Theme.TEXT};")
        for d in getattr(self, "_card_desc_labels", []):
            d.setStyleSheet(f"color:{Theme.TEXT_MUTED};line-height:1.6;")
        if self._status_label.text():
            self._set_status(self._status_label.text())
        else:
            self._status_label.setStyleSheet(f"color:{Theme.TEXT_MUTED};")
