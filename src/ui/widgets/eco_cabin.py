# -*- coding: utf-8 -*-
"""玻璃生态舱萌宠：状态判定纯函数 + 环形温度球 QPainter 绘制器（v3）。

无 QWidget 依赖，可无头测试；档位语义与 max_temp_level 同源思想
（百分比/绝对双模式 + 迟滞防抖）。坐标约定 100×100 归一化设计稿。

v3 视觉（用户拍板）：8 路液柱取消——改为单一圆形温度隐喻：深色玻璃
圆舱内自底部向上填充（高度=最高温/报警线比例），填充颜色随档位变化，
笑脸居上半部，温度数值在笑脸正下方的圆心处；报警=整舱红+舱顶喷火。
"""
from __future__ import annotations

LV_NONE, LV_NORMAL, LV_WARN, LV_ALARM = "none", "normal", "warn", "alarm"
HYSTERESIS_C = 1.0          # 档位退出迟滞带（℃），常量不开放配置
CELEBRATE_COOL_S = 6.0      # 庆祝冷却（阈值徘徊防抽搐）


def warn_buffer(high, warn_pct, warn_abs):
    """预警缓冲（℃）：warn_abs>0 走绝对（high-warn_abs），否则百分比；None=禁用。"""
    try:
        high = float(high)
    except (TypeError, ValueError):
        return None
    if high <= 0:
        return None
    if warn_abs and float(warn_abs) > 0:
        return max(0.0, high - float(warn_abs))
    if warn_pct and int(warn_pct) > 0:
        return high * (100 - int(warn_pct)) / 100.0
    return None


def channel_level(t, high, buffer, prev=LV_NORMAL, hyst=HYSTERESIS_C):
    """单通道档位（带迟滞）：buffer=None 禁用预警档；t=None → none（缺数据）。

    进入按标准线（high / high-buffer），退出需再回落 hyst ℃，防止阈值
    附近来回横跳（报警闪烁 + 庆祝抽搐）。
    """
    if t is None:
        return LV_NONE
    if high is None or high <= 0:
        return LV_NORMAL
    if t >= high or (prev == LV_ALARM and t >= high - hyst):
        return LV_ALARM
    if buffer is not None and (
            t >= high - buffer
            or (prev == LV_WARN and t >= high - buffer - hyst)):
        return LV_WARN
    return LV_NORMAL


def aggregate(levels, fluct_flags, paused):
    """聚合状态优先级：paused ＞ alarm ＞ warn ＞ fluct ＞ idle。"""
    if paused:
        return "paused"
    best = "idle"
    for i, lv in enumerate(levels):
        if lv == LV_ALARM:
            return "alarm"
        if lv == LV_WARN:
            best = "warn"
        elif (best == "idle" and fluct_flags and i < len(fluct_flags)
                and fluct_flags[i]):
            best = "fluct"
    return best


def fluct_flags(prev_temps, temps, elapsed, rate):
    """逐通道波动标记：|ΔT| ≥ rate×elapsed 即 True；rate=0 关闭；缺数据 False。"""
    if not rate or elapsed <= 0:
        return [False] * len(temps)
    thr = float(rate) * float(elapsed)
    out = []
    for i in range(len(temps)):
        a = prev_temps[i] if i < len(prev_temps) else None
        b = temps[i]
        out.append(bool(a is not None and b is not None and abs(b - a) >= thr))
    return out


def celebrate_edge(prev_agg, agg, now, last_ts, cooldown=CELEBRATE_COOL_S):
    """异常→平稳的恢复边沿且已过冷却才庆祝一次；返回 (触发?, 新last_ts)。"""
    was_bad = prev_agg in ("alarm", "warn", "fluct")
    if was_bad and agg == "idle" and now - last_ts >= cooldown:
        return True, now
    return False, last_ts


# ===========================================================================
#  QPainter 绘制器 v3：环形温度球。
#  深色玻璃圆舱 + 发光内环 + 自底部向上填充（高度=最高温/报警线比例，
#  颜色随档位）+ 笑脸（上半部）+ 温度数值（笑脸正下方圆心处）。无逐通
#  道元素；报警=整舱红+舱顶喷火，打盹=灰舱 zz，庆祝=跳星。所有元素内收
#  于球面之内（悬浮球是精确直径的顶层小窗，溢出即被裁）。
# ===========================================================================
import math  # noqa: E402

from PyQt5.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PyQt5.QtGui import (  # noqa: E402
    QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
    QPainterPath, QPen, QRadialGradient, QPolygonF)

CX, CY = 50.0, 53.0                     # 圆舱中心（设计稿）
R_OUT, R_RING, R_IN = 37.0, 33.0, 30.0  # 外缘 / 发光环 / 液体内圆半径

# agg: (光环色, 舱顶高光, 舱底色, 舱缘色, 墨色, 液体亮色, 液体深色)
_AGG_STYLE = {
    "idle":   ("#48c8ff", QColor(58, 128, 180, 70), QColor(12, 26, 52, 235),
               "#7fd8ff", "#dfeefc", "#7ae4ff", "#1f7fc0"),
    "warn":   ("#ffaa33", QColor(255, 170, 60, 64), QColor(46, 26, 8, 238),
               "#ffc873", "#ffe9c4", "#ffd07a", "#e0801a"),
    "alarm":  ("#ff4455", QColor(255, 80, 80, 80), QColor(52, 10, 14, 242),
               "#ff9a9a", "#ffe0e0", "#ff8a7a", "#c01828"),
    "fluct":  ("#7fd8ff", QColor(150, 130, 255, 50), QColor(18, 20, 58, 238),
               "#c8b8ff", "#e8e2ff", "#9fd8ff", "#3a7fc0"),
    "paused": ("#5d7085", QColor(120, 150, 180, 30), QColor(14, 20, 32, 240),
               "#8fa0b4", "#aebccb", "#7d8fa5", "#43505f"),
}


class EcoCabinView:
    """一帧绘制快照（FloatingBall 组装；绘制器只读不改）。

    agg 聚合态（idle/warn/alarm/fluct/paused）；fill 液面填充比例 0..1
    （最高温相对报警线、均衡器插值后的显示值）；temp_text 圆心温度文本；
    paused/blink/look 表情；phase 全局相位；celebrate 庆祝进度（<0 无）；
    fluct 波动标记（液面波纹与气泡加速）。
    """

    __slots__ = ("agg", "fill", "temp_text", "paused", "blink", "look",
                 "phase", "celebrate", "fluct")

    def __init__(self, agg="idle", fill=0.0, temp_text="", paused=False,
                 blink=False, look=None, phase=0, celebrate=-1.0,
                 fluct=False):
        self.agg = agg
        self.fill = max(0.0, min(1.0, float(fill)))
        self.temp_text = str(temp_text)
        self.paused = paused
        self.blink = blink
        self.look = look
        self.phase = int(phase)
        self.celebrate = float(celebrate)
        self.fluct = bool(fluct)


class EcoCabinPainter:
    """把 EcoCabinView 快照画到任意 QPainter（size=球直径像素）。"""

    def paint(self, painter, size, view):
        p = painter
        p.setRenderHint(QPainter.Antialiasing)
        k = size / 100.0
        aura, tint_top, tint_bot, rim, ink_c, liq_a, liq_b = _AGG_STYLE.get(
            view.agg, _AGG_STYLE["idle"])
        ink = QColor(ink_c)
        cx, cy = CX * k, CY * k
        ph = view.phase

        if view.agg == "alarm" and ph % 2:      # 报警高频 ±1px 抖动
            p.save()
            p.translate(1.0 if ph % 4 < 2 else -1.0,
                        0.0 if ph % 4 < 2 else -1.0)

        self._draw_aura(p, k, aura, view)
        self._draw_body(p, k, cx, cy, tint_top, tint_bot, rim)
        p.save()
        self._clip_inner(p, k, cx, cy)
        self._draw_liquid(p, k, cx, cy, view, liq_a, liq_b)
        p.restore()
        self._draw_ring(p, k, cx, cy, aura)
        self._draw_face(p, k, view, ink)
        self._draw_temp_text(p, k, cx, view)
        if view.agg == "alarm":
            self._draw_flames(p, k, cx, cy, view)
        if 0.0 <= view.celebrate <= 1.0:
            self._draw_stars(p, k, cx, cy, view)
        if view.agg == "alarm" and ph % 2:
            p.restore()

    # ---------------- 部件 ----------------
    def _draw_aura(self, p, k, aura, view):
        """外圈环境光：呼吸柔光 + 细描边环（报警频闪）。"""
        base = QColor(aura)
        breath = 0.35 + 0.3 * abs(math.sin(math.pi * view.phase / 12.0))
        soft = QColor(base)
        soft.setAlphaF(0.16 * breath + 0.06)
        p.setPen(Qt.NoPen)
        p.setBrush(soft)
        p.drawEllipse(QRectF((CX - 46) * k, (CY - 44) * k, 92 * k, 88 * k))
        ring = QColor(base)
        ring.setAlphaF(0.55 + 0.4 * breath)
        if view.agg == "alarm" and view.phase % 4 >= 2:
            ring.setAlphaF(0.25)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(ring, max(1.0, 1.2 * k)))
        p.drawEllipse(QPointF(CX * k, CY * k), 44 * k, 42 * k)

    def _draw_body(self, p, k, cx, cy, tint_top, tint_bot, rim):
        """玻璃圆舱：径向渐变深色球体 + 亮缘 + 左上高光弧。"""
        g = QRadialGradient(QPointF(cx - 10 * k, cy - 14 * k), 52 * k)
        g.setColorAt(0.0, tint_top)
        g.setColorAt(0.68, tint_bot)
        p.setPen(QPen(QColor(rim).darker(135), max(1.6, 2.0 * k)))
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), R_OUT * k, R_OUT * k)
        p.setPen(QPen(QColor(255, 255, 255, 66), max(1.0, 1.5 * k)))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF((cx - 26) * k, (cy - 30) * k, 40 * k, 24 * k),
                  100 * 16, 55 * 16)

    def _clip_inner(self, p, k, cx, cy):
        """内圆裁剪：液体全部收在 R_IN 之内。"""
        path = QPainterPath()
        path.addEllipse(QPointF(cx, cy), R_IN * k, R_IN * k)
        p.setClipPath(path)

    def _draw_liquid(self, p, k, cx, cy, view, liq_a, liq_b):
        """自底部向上的液体填充：高度=fill；液面波纹随相位起伏（波动加速），
        液体内两颗缓升气泡。"""
        r = R_IN * k
        top_y = cy + r - 2 * r * view.fill
        speed = 2.5 if view.fluct else 6.0
        wob = math.sin(view.phase * math.pi / speed) * 1.6 * k
        lg = QLinearGradient(QPointF(cx, top_y), QPointF(cx, cy + r))
        lg.setColorAt(0.0, QColor(liq_a))
        lg.setColorAt(1.0, QColor(liq_b))
        p.setPen(Qt.NoPen)
        p.setBrush(lg)
        p.drawRect(QRectF(cx - r, top_y, 2 * r, cy + r - top_y + 1))
        pen = QPen(QColor(255, 255, 255, 150), max(0.8, 1.0 * k))
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(cx - r, top_y + wob),
                   QPointF(cx + r, top_y - wob))
        if not view.paused and view.fill > 0.12:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 70))
            for j in (0, 1):
                t = ((view.phase + j * 7) % 12) / 12.0
                bx = cx + (r * 0.5) * math.sin(view.phase + j * 2.4)
                by = cy + r - t * 2 * r * view.fill
                p.drawEllipse(QPointF(bx, by), 1.2 * k, 1.2 * k)

    def _draw_ring(self, p, k, cx, cy, aura):
        """发光内环：玻璃球青色描边环（液体外缘，参考图样式）。"""
        ring = QColor(aura)
        ring.setAlphaF(0.9)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(ring, max(1.2, 1.6 * k)))
        p.drawEllipse(QPointF(cx, cy), R_RING * k, R_RING * k)
        glow = QColor(aura)
        glow.setAlphaF(0.22)
        p.setPen(QPen(glow, max(2.4, 3.2 * k)))
        p.drawEllipse(QPointF(cx, cy), R_RING * k, R_RING * k)

    def _draw_face(self, p, k, view, ink):
        """表情（球体上半部）：眼/眉/嘴随聚合态；打盹闭眼、庆祝眯眼笑。"""
        lk = view.look or QPointF(0.0, 0.0)
        px, py = lk.x() * k, lk.y() * k
        eye_y = CY - 18
        p.setBrush(QColor("#ffffff"))
        if view.paused:
            self._arc_eyes(p, k, ink, eye_y - 4, 200 * 16, 140 * 16)
            return
        if 0.0 <= view.celebrate <= 1.0:
            self._arc_eyes(p, k, ink, eye_y - 2, 0, 180 * 16)
        elif view.blink:
            p.setPen(QPen(ink, max(1.4, 1.6 * k)))
            for ex in (CX - 15, CX + 2):
                p.drawLine(QPointF(ex * k, eye_y * k),
                           QPointF((ex + 13) * k, eye_y * k))
            p.setBrush(QColor("#ffffff"))
        else:
            big = view.agg == "alarm"
            for ex in (CX - 16, CX + 3):
                p.setPen(QPen(ink, max(1.0, 1.2 * k)))
                p.setBrush(QColor("#ffffff"))
                p.drawEllipse(QRectF(ex * k, (eye_y - 8) * k,
                                     13 * k, 17 * k))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#16202e"))
                pr = (3.4 if big else 2.6) * k
                p.drawEllipse(QPointF((ex + 6.5) * k + px * 0.6,
                                      eye_y * k + py * 0.6), pr, pr * 1.2)
        if view.agg in ("warn", "alarm"):        # 皱眉
            q = 2.6 if view.agg == "alarm" else 1.6
            p.setPen(QPen(ink, max(1.2, 1.4 * k)))
            p.drawLine(QPointF((CX - 16) * k, (eye_y - 12) * k),
                       QPointF((CX - 6) * k, (eye_y - 12 - q) * k))
            p.drawLine(QPointF((CX + 16) * k, (eye_y - 12) * k),
                       QPointF((CX + 6) * k, (eye_y - 12 - q) * k))
        p.setPen(QPen(ink, max(1.2, 1.4 * k)))
        p.setBrush(Qt.NoBrush)
        if view.agg in ("alarm", "fluct"):       # 张嘴
            p.setBrush(QColor("#20060a"))
            p.drawEllipse(QRectF((CX - 5) * k, (eye_y + 12) * k,
                                 10 * k, 9 * k))
        elif view.agg == "warn":
            p.drawLine(QPointF((CX - 6) * k, (eye_y + 15) * k),
                       QPointF((CX + 6) * k, (eye_y + 15) * k))
        else:
            p.drawArc(QRectF((CX - 8) * k, (eye_y + 10) * k,
                             16 * k, 10 * k), 200 * 16, 140 * 16)
        if view.agg == "warn":                    # 汗滴
            p.setPen(QPen(QColor("#57b8ff"), max(1.0, 1.2 * k)))
            p.drawArc(QRectF((CX + 17) * k, (eye_y - 12) * k,
                             5 * k, 9 * k), 30 * 16, 140 * 16)

    def _arc_eyes(self, p, k, ink, y, a0, span):
        p.setPen(QPen(ink, max(1.2, 1.6 * k)))
        p.setBrush(Qt.NoBrush)
        for ex in (CX - 16, CX + 3):
            p.drawArc(QRectF(ex * k, y * k, 13 * k, 12 * k), a0, span)

    def _draw_temp_text(self, p, k, cx, view):
        """圆心温度数值（笑脸正下方）：深色描边 + 白色主体，任何液面下可读。

        ⚠️ 必须用 int 版 drawText(x, y, text)：本机 PyQt5 offscreen 下
        QPointF/QRectF 浮点重载全部静默失效（实测白像素为 0）。
        """
        text = view.temp_text or "--"
        f = QFont(p.font())
        f.setPixelSize(max(8, int(13 * k)))
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)
        x = int(cx - fm.horizontalAdvance(text) / 2.0)
        y = int((CY - 1) * k) + fm.ascent()
        p.setBrush(Qt.NoBrush)
        edge = QColor(0, 0, 0, 170)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.setPen(QPen(edge, max(1.2, 1.4 * k)))
            p.drawText(x + int(dx * k * 0.6), y + int(dy * k * 0.6), text)
        p.setPen(QPen(QColor("#ffffff")))
        p.drawText(x, y, text)
        if view.paused:
            f2 = QFont(f)
            f2.setPixelSize(max(7, int(11 * k)))
            p.setFont(f2)
            fm2 = QFontMetrics(f2)
            p.setPen(QPen(QColor("#aebccb")))
            p.drawText(int(cx - fm2.horizontalAdvance("z z") / 2.0),
                       int((CY + 18) * k) + fm2.ascent(), "z z")

    def _draw_flames(self, p, k, cx, cy, view):
        """报警火苗：舱顶两角对称喷出（相位闪烁）。"""
        base_y = cy - R_OUT * k + 5 * k
        for j, fx in enumerate((cx - 16 * k, cx + 16 * k)):
            flick = 0.75 + 0.5 * abs(math.sin(view.phase * math.pi / 4.0
                                              + j * 1.7))
            h = 12.0 * flick * k
            w = 4.5 * k
            sway = (j - 0.5) * 2.0 + 0.8 * math.sin(view.phase * math.pi / 6.0)
            g = QLinearGradient(QPointF(fx, base_y), QPointF(fx, base_y - h))
            g.setColorAt(0.0, QColor("#ff5a1f"))
            g.setColorAt(0.55, QColor("#ffb300"))
            g.setColorAt(1.0, QColor("#fff176"))
            p.setBrush(g)
            p.drawPolygon(QPolygonF([
                QPointF(fx - w, base_y),
                QPointF(fx + sway * k, base_y - h),
                QPointF(fx + w, base_y)]))

    def _draw_stars(self, p, k, cx, cy, view):
        prog = min(1.0, max(0.0, view.celebrate))
        col = QColor(255, 226, 122, int(255 * (1.0 - prog)))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        for a in range(8):
            th = a * math.pi / 4.0
            rr = 16.0 + 26.0 * prog
            sx = cx + rr * math.cos(th) * k
            sy = cy + rr * math.sin(th) * 0.82 * k
            sz = 2.4 * (1.0 - prog * 0.5)
            p.drawPolygon(QPolygonF([
                QPointF(sx, sy - sz * k),
                QPointF(sx + sz * 0.4 * k, sy),
                QPointF(sx, sy + sz * k),
                QPointF(sx - sz * 0.4 * k, sy)]))
