# -*- coding: utf-8 -*-
"""全局禁用数值输入控件的鼠标滚轮改值。

滚轮悬停在 QSpinBox / QDoubleSpinBox / QTimeEdit / QDateEdit 等数值控件上时，
Qt 默认会直接修改当前值，鼠标稍一滚动就容易误触碰改值。本模块通过应用级
事件过滤器统一吞掉这些控件的滚轮事件，只允许「鼠标点击上下微调」或
「键盘键入」两种改值方式。

- 覆盖范围是全局的：任何弹窗 / 页面里的数值控件都生效（设置、编辑、服务
  配置、历史远程等），无需逐控件改造。
- QComboBox 在下拉弹层关闭时同样吞掉滚轮（防止悬停误切选项）；弹层打开时
  滚轮仍作用于选项列表，不拦截。
- 不拦截 QAbstractSlider（QScrollBar 必须保持页面滚动）。
"""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import QAbstractSpinBox, QComboBox


class _NoWheelFilter(QObject):
    """吞掉数值输入控件的滚轮事件，返回 True 表示事件已处理。"""

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Wheel:
            return False
        if isinstance(obj, QAbstractSpinBox):
            return True
        if isinstance(obj, QComboBox):
            # 下拉弹层可见时滚轮作用于选项列表（view 是独立的 QListView），
            # 此时不应拦截；仅吞掉关闭态组合框的滚轮。
            try:
                if not obj.view().isVisible():
                    return True
            except Exception:
                return True
        return False


def install_no_wheel_input(app) -> QObject:
    """在 QApplication 上安装滚轮禁用过滤器，返回过滤器对象（需保持引用）。

    过滤器挂在 app 对象属性上，避免被 Python 垃圾回收后事件过滤失效。
    """
    flt = _NoWheelFilter()
    app.installEventFilter(flt)
    setattr(app, "_no_wheel_filter", flt)
    return flt
