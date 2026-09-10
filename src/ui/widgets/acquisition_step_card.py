# -*- coding: utf-8 -*-
"""AcquisitionStepCard — 连接→采集全旅程进度卡片。

三个真实步骤贯穿连接与采集（打勾 / 失败全部由真实事件驱动，不按固定
时间演出，时间只用于动画与停留）：
  1. 打开串口 —— 点「连接」或「▶ 采集」后 SerialPortManager.open() 成功
     即打勾，失败标红显示原因；连接旅程下打勾后进入「已连接」停留态；
  2. 设备握手 —— 后台握手线程运行中显示旋转动画，握手成功（协议与
     波特率确定）打勾并显示真实参数，失败标红显示原因；
  3. 启动采集 —— 采集线程启动后等待，首帧数据到达（与心跳转绿同源
     信号）打勾，整卡停留后自动淡出。

另有消息模式（show_notice）：连接 / 暂停 / 继续 / 结束 / 断开等旅程
反馈以单行卡片集中显示（替代零散 toast），停留后自动淡出。

跨主题固定深色半透明底 + 白字（与 ThemeBusyPopup 同一惯例），鼠标
穿透不阻挡操作；通道命名等模态弹窗出现前由主窗口调用 suspend()
让位，关闭后 resume() 恢复；所有状态迁移方法都带前置状态守卫，
乱序 / 重复调用（例如每帧触发的 mark_acquire_done）自动忽略。
注意：全局 QSS 的 QWidget 底色规则已在 theme.py 按 objectName 豁免，
否则卡片方形四角会垫出主底色块（深色画布上呈白角）。

依赖:
  - 无 Theme 依赖（卡片配色跨主题固定，保证任何画布背景上可读）。
"""
from PyQt5.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QFrame, QGraphicsOpacityEffect

# 卡片配色：固定深色半透明，任何主题的画布背景上都可读
_CARD_BG = QColor(0, 0, 0, 190)
_BORDER = QColor(255, 255, 255, 38)
_TITLE_COLOR = QColor(255, 255, 255)
_TITLE_FAIL_COLOR = QColor(255, 120, 120)
_TEXT_COLOR = QColor(255, 255, 255)
_TEXT_DIM = QColor(255, 255, 255, 120)
_SUB_COLOR = QColor(255, 255, 255, 105)
_SPINNER_COLOR = QColor(255, 255, 255, 230)
_OK_COLOR = QColor(7, 193, 96)      # 成功绿（与主题「激活」语义一致）
_BAD_COLOR = QColor(250, 81, 81)    # 失败红
_WARN_COLOR = QColor(255, 176, 32)  # 提醒琥珀（暂停等需注意的旅程反馈）
# 消息模式文字色（kind → 色）
_NOTICE_COLORS = {
    "info": _TITLE_COLOR,
    "success": _OK_COLOR,
    "warning": _WARN_COLOR,
    "error": _BAD_COLOR,
}

# 步骤状态
_PENDING, _ACTIVE, _DONE, _FAILED = 0, 1, 2, 3

_CARD_RADIUS = 12.0
_ENTRANCE_MS = 450          # 入场：淡入 + 上滑
_RESUME_MS = 150            # 模态让位恢复：快速淡入
_CHECK_MS = 300             # 打勾动画
_EXIT_MS = 300              # 淡出退场
_DONE_HOLD_MS = 2200        # 全部打勾后的停留
_FAIL_HOLD_MS = 2000        # 失败后的停留
_CONNECTED_HOLD_MS = 8000   # 连接旅程「已连接」停留（等待点「▶ 采集」）
_MSG_HOLD_MS = 2600         # 消息模式停留
_ROW_H = 36                 # 单步行高
_REASON_H = 40              # 失败原因两行区高度
_CARD_WIDTH = 300
_TITLE_H = 52               # 标题区高度（含上内边距）
_MSG_H = 56                 # 消息模式卡高
_SLIDE_IN_PX = 12


class AcquisitionStepCard(QFrame):
    """主窗口居中的采集启动三步进度卡片（不拦截鼠标事件）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("acqStepCard")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

        self._port_text = ""
        self._n_channels = None
        self._states = [_PENDING, _PENDING, _PENDING]
        self._subs = ["", "", ""]
        self._failed_row = -1
        self._reason = ""
        self._finished = False
        self._suspended = False
        self._mode = "acquire"       # acquire=采集旅程 / connect=连接旅程
        self._message_mode = False   # 消息模式（show_notice）
        self._notice_text = ""
        self._notice_kind = "info"
        self._angle = 0
        self._anim_row = -1
        self._check_progress = 1.0
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._fade_out)

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_fade_done)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._intro = QParallelAnimationGroup(self)
        self._intro.addAnimation(self._fade)
        self._intro.addAnimation(self._slide)
        self._check_anim = QVariantAnimation(self)
        self._check_anim.setDuration(_CHECK_MS)
        self._check_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._check_anim.valueChanged.connect(self._on_check_tick)
        self._spinner_timer = QTimer(self, interval=40, timeout=self._tick)

    # ---- 公开接口（由主窗口按真实事件驱动） ----

    def _reset_runtime(self) -> None:
        """进入任一显示模式前的公共复位（停定时器/动画，清瞬态）。"""
        self._hold_timer.stop()
        self._intro.stop()
        self._fade.stop()
        self._message_mode = False
        self._suspended = False
        self._anim_row = -1
        self._check_progress = 1.0

    def _enter(self, states, port_text: str, mode: str) -> None:
        """按给定步骤态进入显示（acquire/connect 共用骨架）。"""
        was_visible = self.isVisible()
        self._reset_runtime()
        self._mode = mode
        self._port_text = port_text or ""
        self._n_channels = None
        self._states = list(states)
        self._subs = ["", "", ""]
        self._failed_row = -1
        self._reason = ""
        self._finished = False
        self._resize_for_content()
        self._set_opacity(1.0)
        if not was_visible:
            self._play_entrance(_ENTRANCE_MS)
        else:
            self.raise_()   # 已显示（如「已连接」接力）：原地切换不重播入场
        self._sync_spinner()
        self.update()

    def start(self, port_text: str) -> None:
        """点击「▶ 采集」且串口未开：重置为第 1 步「打开串口」进行中。"""
        self._enter([_ACTIVE, _PENDING, _PENDING], port_text, "acquire")

    def start_connect(self, port_text: str) -> None:
        """点击「连接」：进入连接旅程，第 1 步「打开串口」进行中。"""
        self._enter([_ACTIVE, _PENDING, _PENDING], port_text, "connect")

    def resume_connected(self, port_text: str) -> None:
        """点击「▶ 采集」且串口已连接：第 1 步直接完成接力，不重播转圈。"""
        self._enter([_DONE, _PENDING, _PENDING], port_text, "acquire")

    def show_notice(self, text: str, kind: str = "info") -> None:
        """旅程瞬态消息：单行卡片集中显示，停留后自动淡出。

        连接 / 断开 / 暂停 / 继续 / 结束 / 拔线等旅程反馈统一走这里
        （替代零散 toast），kind 决定文字语义色（info/success/warning/
        error）。
        """
        self._hold_timer.stop()
        self._intro.stop()
        self._fade.stop()
        self._message_mode = True
        self._suspended = False
        self._notice_text = str(text)
        self._notice_kind = kind if kind in _NOTICE_COLORS else "info"
        self._states = [_PENDING, _PENDING, _PENDING]
        self._failed_row = -1
        self._reason = ""
        self._finished = False
        self._anim_row = -1
        self._check_progress = 1.0
        self._resize_notice()
        self._set_opacity(1.0)
        self._play_entrance(_ENTRANCE_MS)
        self._sync_spinner()
        self.update()
        self._hold_timer.start(_MSG_HOLD_MS)

    def mark_port_done(self) -> None:
        """第 1 步打勾：串口已真实打开。

        连接旅程下同时进入「已连接」停留态（提示下一步、8 秒后自动
        淡出，点「▶ 采集」则由 resume_connected 接力，停留被打断）。
        """
        if self._states[0] != _ACTIVE:
            return
        self._advance(0)
        if self._mode == "connect":
            self._subs[0] = "点击「▶ 采集」开始"
            self._hold_timer.start(_CONNECTED_HOLD_MS)
        self.update()

    def mark_port_failed(self, reason: str = "") -> None:
        """第 1 步失败：串口未能打开。"""
        if self._states[0] != _ACTIVE:
            return
        self._fail(0, reason)

    def mark_handshake_active(self) -> None:
        """第 2 步进行中：后台握手线程已启动。"""
        if self._states[0] != _DONE or self._states[1] != _PENDING:
            return
        self._states[1] = _ACTIVE
        self._subs[1] = "正在识别协议与波特率…"
        self._sync_spinner()
        self.update()

    def mark_handshake_done(self, proto_label: str = "", baud=None) -> None:
        """第 2 步打勾：握手成功，展示真实协议与波特率。"""
        if self._states[1] != _ACTIVE:
            return
        sub = f"{proto_label}协议" if proto_label else ""
        if baud:
            sub = f"{sub} · {int(baud)}" if sub else str(int(baud))
        self._subs[1] = sub
        self._advance(1)

    def mark_handshake_failed(self, reason: str = "") -> None:
        """第 2 步失败：设备无响应 / 协议不匹配等。"""
        if self._states[1] != _ACTIVE:
            return
        self._fail(1, reason)

    def mark_acquire_active(self, n_channels=None) -> None:
        """第 3 步进行中：采集线程已启动，等待首帧数据。"""
        if self._states[1] != _DONE or self._states[2] != _PENDING:
            return
        self._n_channels = n_channels
        self._states[2] = _ACTIVE
        self._subs[2] = "等待首帧数据…"
        self._sync_spinner()
        self.update()

    def mark_acquire_done(self) -> None:
        """第 3 步打勾：首帧数据真实到达（每帧调用安全，仅首次生效）。"""
        if self._states[2] != _ACTIVE:
            return
        self._subs[2] = "数据链路已建立"
        self._advance(2)
        self._finished = True
        self._hold_timer.start(_DONE_HOLD_MS)
        self.update()

    def mark_acquire_failed(self, reason: str = "") -> None:
        """第 3 步失败：会话创建 / 数据库落盘失败，采集未开始。"""
        if self._states[2] != _ACTIVE:
            return
        self._fail(2, reason)

    def dismiss(self) -> None:
        """任意状态下直接淡出退场（命名取消、连接中断等）。"""
        self._hold_timer.stop()
        self._suspended = False
        if not self.isVisible():
            self._sync_spinner()
            return
        self._fade_out()

    def suspend(self) -> None:
        """模态弹窗让位：暂时隐藏（不重置进度，不触发淡出）。"""
        if not self.isVisible():
            return
        self._suspended = True
        self._hold_timer.stop()
        self._sync_spinner()
        self.hide()

    def resume(self) -> None:
        """模态弹窗关闭：恢复显示并快速淡入。"""
        if not self._suspended:
            return
        self._suspended = False
        self._set_opacity(1.0)
        self._play_entrance(_RESUME_MS)
        self._sync_spinner()
        self.update()

    # ---- 内部：状态推进 ----

    def _advance(self, row: int) -> None:
        self._states[row] = _DONE
        self._anim_row = row
        self._check_progress = 0.0
        self._check_anim.stop()
        self._check_anim.setStartValue(0.0)
        self._check_anim.setEndValue(1.0)
        self._check_anim.start()
        self._sync_spinner()
        self.update()

    def _fail(self, row: int, reason: str) -> None:
        self._states[row] = _FAILED
        self._failed_row = row
        self._reason = (reason or "").strip()
        self._resize_for_content()
        self._center_at()
        self._sync_spinner()
        self._hold_timer.start(_FAIL_HOLD_MS)
        self.update()

    def _on_check_tick(self, value) -> None:
        self._check_progress = float(value)
        self.update()

    def _is_failed(self) -> bool:
        return self._failed_row >= 0

    def _title(self) -> str:
        if self._message_mode:
            return self._notice_text
        if self._is_failed():
            return "串口连接失败" if self._mode == "connect" else "采集启动失败"
        if self._mode == "connect":
            if self._states[0] == _DONE:
                return f"已连接 {self._port_text}".rstrip()
            return "正在连接串口"
        if self._finished:
            n = self._n_channels
            return f"采集已开始（{n}通道）" if n else "采集已开始"
        return "正在开始采集"

    # ---- 内部：动画与定位 ----

    def _tick(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def _sync_spinner(self) -> None:
        """有进行中步骤且可见时旋转，否则停表（省电且避免隐藏期空转）。"""
        running = (self.isVisible() and not self._suspended
                   and any(s == _ACTIVE for s in self._states))
        if running and not self._spinner_timer.isActive():
            self._spinner_timer.start()
        elif not running and self._spinner_timer.isActive():
            self._spinner_timer.stop()

    def _resize_for_content(self) -> None:
        height = _TITLE_H + 3 * _ROW_H + 14
        if self._is_failed() and self._reason:
            height += _REASON_H
        self.setFixedSize(_CARD_WIDTH, height)

    def _resize_notice(self) -> None:
        """消息模式：固定单行卡高（文字超宽由绘制层省略）。"""
        self.setFixedSize(_CARD_WIDTH, _MSG_H)

    def _center_pos(self) -> QPoint:
        parent = self.parentWidget()
        if parent is None:
            return QPoint(0, 0)
        x = max((parent.width() - self.width()) // 2, 12)
        y = max((parent.height() - self.height()) // 2, 12)
        return QPoint(x, y)

    def _center_at(self) -> None:
        pos = self._center_pos()
        self.move(pos)

    def _play_entrance(self, duration_ms: int) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        final = self._center_pos()
        self.show()
        self.raise_()
        self._intro.stop()
        self._fade.stop()
        self._fade.setDuration(duration_ms)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._slide.setDuration(duration_ms)
        self._slide.setStartValue(final + QPoint(0, _SLIDE_IN_PX))
        self._slide.setEndValue(final)
        self._intro.start()

    def _fade_out(self) -> None:
        if not self.isVisible():
            return
        self._intro.stop()
        self._slide.stop()
        self._fade.stop()
        self._fade.setDuration(_EXIT_MS)
        self._fade.setStartValue(self._opacity_effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_done(self) -> None:
        if self._opacity_effect.opacity() <= 0.01:
            self.hide()
            self._sync_spinner()

    def _set_opacity(self, value: float) -> None:
        self._opacity_effect.setOpacity(value)

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_spinner()

    def hideEvent(self, event):
        self._sync_spinner()
        super().hideEvent(event)

    # ---- 绘制 ----

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # 半透明圆角卡片 + 细边框
        painter.setPen(Qt.NoPen)
        painter.setBrush(_CARD_BG)
        painter.drawRoundedRect(QRectF(0, 0, w, h), _CARD_RADIUS, _CARD_RADIUS)
        painter.setPen(QPen(_BORDER, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1),
                                _CARD_RADIUS, _CARD_RADIUS)
        if self._message_mode:
            # 消息模式：单行居中文本，语义色由 kind 决定
            painter.setPen(_NOTICE_COLORS.get(self._notice_kind,
                                              _TITLE_COLOR))
            painter.setFont(QFont(self.font().family(), 10, QFont.Bold))
            metrics = painter.fontMetrics()
            elided = metrics.elidedText(self._notice_text, Qt.ElideRight,
                                        int(w - 32))
            painter.drawText(QRectF(16, 0, w - 32, h),
                             Qt.AlignCenter, elided)
            painter.end()
            return
        # 标题
        painter.setPen(_TITLE_FAIL_COLOR if self._is_failed() else _TITLE_COLOR)
        painter.setFont(QFont(self.font().family(), 10, QFont.Bold))
        painter.drawText(QRectF(20, 12, w - 40, 30),
                         Qt.AlignVCenter | Qt.AlignLeft, self._title())
        # 三步行
        y = float(_TITLE_H - 6)
        for i, state in enumerate(self._states):
            self._paint_row(painter, i, state, y)
            y += _ROW_H
            if i == self._failed_row and self._reason:
                self._paint_reason(painter, y)
                y += _REASON_H
        painter.end()

    def _paint_row(self, painter: QPainter, row: int, state: int,
                   y: float) -> None:
        cx, cy = 32.0, y + _ROW_H / 2
        # 图标
        if state == _PENDING:
            painter.setPen(QPen(_TEXT_DIM, 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(cx - 8, cy - 8, 16, 16))
            painter.setPen(_TEXT_DIM)
            painter.setFont(QFont(self.font().family(), 8))
            painter.drawText(QRectF(cx - 8, cy - 8, 16, 16),
                             Qt.AlignCenter, str(row + 1))
        elif state == _ACTIVE:
            pen = QPen(_SPINNER_COLOR, 2.5)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(QRectF(cx - 8, cy - 8, 16, 16),
                            self._angle * 16, 100 * 16)
        elif state == _DONE:
            self._paint_check(painter, cx, cy, row)
        else:  # _FAILED
            pen = QPen(_BAD_COLOR, 2.5)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QRectF(cx - 6, cy - 6, 12, 12).topLeft(),
                             QRectF(cx - 6, cy - 6, 12, 12).bottomRight())
            painter.drawLine(QRectF(cx - 6, cy - 6, 12, 12).topRight(),
                             QRectF(cx - 6, cy - 6, 12, 12).bottomLeft())
        # 主文字
        color = (_BAD_COLOR if state == _FAILED
                 else _TEXT_COLOR if state in (_ACTIVE, _DONE)
                 else _TEXT_DIM)
        painter.setPen(color)
        painter.setFont(QFont(self.font().family(), 9, QFont.Medium))
        painter.drawText(QRectF(56, y, self.width() - 76, _ROW_H),
                         Qt.AlignVCenter | Qt.AlignLeft, self._row_label(row))
        # 副文字（右对齐）
        sub = self._subs[row]
        if sub:
            painter.setPen(_SUB_COLOR)
            painter.setFont(QFont(self.font().family(), 8))
            metrics = painter.fontMetrics()
            max_w = self.width() - 56 - 16
            elided = metrics.elidedText(sub, Qt.ElideRight, max_w)
            painter.drawText(QRectF(56, y, self.width() - 76, _ROW_H),
                             Qt.AlignVCenter | Qt.AlignRight, elided)

    def _paint_check(self, painter: QPainter, cx: float, cy: float,
                     row: int) -> None:
        """打勾：勾形描边随进度显现（300ms 缩放 + 淡入）。"""
        progress = self._check_progress if row == self._anim_row else 1.0
        alpha = int(255 * progress)
        color = QColor(_OK_COLOR)
        color.setAlpha(alpha)
        scale = 0.6 + 0.4 * progress
        path = QPainterPath()
        points = [(-5.0, 0.5), (-1.5, 4.0), (5.5, -3.5)]
        path.moveTo(cx + points[0][0] * scale, cy + points[0][1] * scale)
        for px, py in points[1:]:
            path.lineTo(cx + px * scale, cy + py * scale)
        pen = QPen(color, 2.5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def _paint_reason(self, painter: QPainter, y: float) -> None:
        """失败原因：失败行下方最多两行（按原文换行拆分），超出省略。"""
        painter.setPen(_BAD_COLOR)
        painter.setFont(QFont(self.font().family(), 8))
        metrics = painter.fontMetrics()
        raw_lines = self._reason.splitlines() or [""]
        lines = [metrics.elidedText(raw, Qt.ElideRight, self.width() - 76)
                 for raw in raw_lines[:2]]
        painter.drawText(QRectF(56, y, self.width() - 76, _REASON_H),
                         Qt.AlignTop | Qt.AlignLeft, "\n".join(lines))

    def _row_label(self, row: int) -> str:
        if row == 0:
            return f"打开串口 {self._port_text}".rstrip()
        if row == 1:
            return "设备握手"
        return "启动采集接收数据"
