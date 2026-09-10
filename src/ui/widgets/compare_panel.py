# -*- coding: utf-8 -*-
"""
ComparePanel — 通道对比标签页组件。
从 app.py 提取 MainWindow 通道对比方法，通过 self.mw 引用 MainWindow。
"""
from PyQt5.QtWidgets import QWidget, QCheckBox

from ui.theme import Theme


def style_compare_toggle(chk, color: str) -> None:
    """通道对比勾选框的主题化样式（单一事实源：重建/改名/切主题共用）。

    浅色主题（办公明亮/办公专注）卡底近白，通道色文字若仅做加深补偿会
    发闷失身份、残留旧主题亮字更是直接不可读；改为同色相浅底胶囊：
    底色 = 通道色向白提亮 0.65（浅浅的背景），文字对胶囊底补偿 ≥4.5:1，
    1px 同字色描边保证胶囊边缘在白底上可见。深色主题保持无底直排，
    文字对卡底补偿。曲线本体颜色始终不变，通道色相身份由胶囊保留。
    """
    if Theme.is_light_theme():
        chip = Theme.lighten(color, 0.65)
        fg = Theme.readable_text(color, chip)
        chk.set_chip(chip, fg)
    else:
        fg = Theme.readable_text(color)
        chk.set_chip(None)
    chk.setStyleSheet(f"color:{fg}; font-weight:bold;")


class ComparePanel(QWidget):
    """通道对比标签页：多通道曲线叠加对比显示。"""
    def __init__(self, mw):
        super().__init__()
        self.mw = mw  # MainWindow 引用

    def refresh_text_colors(self) -> None:
        """主题/配色切换后重放勾选框样式：文字色与胶囊底按新主题重算，
        保留用户勾选状态（不重建控件）。

        勾选样式在构造期按当时主题的卡底固化，深色→浅色切换后亮字残留
        白底（实测对比度跌至 ~1.2:1），必须随主题链与配色方案重放。
        """
        if not hasattr(self.mw, "chk_compare_host"):
            return
        dataset = self.mw.dataset
        chks = self.mw.chk_compare_host.findChildren(QCheckBox)
        for row, chk in enumerate(chks):
            col = None
            if dataset is not None and row < len(dataset.channels):
                col = dataset.channels[row].color
            if col:
                style_compare_toggle(chk, col)

    def checked_names(self) -> list:
        """收集通道对比标签里当前勾选的通道显示名列表。原 MainWindow._checked_compare_names"""
        names = []
        if not hasattr(self.mw, "chk_compare_host"):
            return names
        for chk in self.mw.chk_compare_host.findChildren(QCheckBox):
            if chk.isChecked():
                names.append(chk.text())
        return names

    def sync_label(self, row: int, new_name: str) -> None:
        """通道改名后，同步通道对比悬浮面板里对应勾选框的文本与颜色。原 MainWindow._sync_compare_label"""
        if not hasattr(self.mw, "chk_compare_host"):
            return
        chks = self.mw.chk_compare_host.findChildren(QCheckBox)
        if 0 <= row < len(chks):
            chk = chks[row]
            chk.blockSignals(True)
            chk.setText(new_name)
            if self.mw.dataset is not None and row < len(self.mw.dataset.channels):
                col = self.mw.dataset.channels[row].color
                # 通道色是深画布上的数据身份，直接作浅色骨架上的文字会低对比；
                # 文字色经 style_compare_toggle 按当前主题补偿（浅色主题配
                # 同色相浅底胶囊，≥4.5:1、保持色相），曲线本体颜色不变
                style_compare_toggle(chk, col)
