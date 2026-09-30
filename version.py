# -*- coding: utf-8 -*-
"""版本唯一事实源。

运行期显示、打包链路（build/build_win.py、build/pyinstaller.spec、
build/launcher.spec、build/file_version_info.txt）全部读取本文件；
升级版本只改这里一处。
"""

__version__ = "5.0.0"

# 数值版本（major, minor, patch），供比较与版本资源注入
VERSION_TUPLE = (1, 0, 0)

RELEASE_DATE = "2026-09-10"
