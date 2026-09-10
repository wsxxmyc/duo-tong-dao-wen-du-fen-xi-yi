# -*- coding: utf-8 -*-
"""多通道温度分析仪 —— 根目录启动器（免安装包根目录的唯一文件）。

发布包 onedir 目录版结构：
    多通道温度分析仪_v<版本戳>.exe   本启动器（位于发布包根目录，用户双击入口）
    程序文件/                     完整 PyInstaller onedir 产物（主程序 + 全部依赖）
    说明文档/                     使用说明

背景：PyInstaller onedir 的主程序必须与其依赖（python38.dll、Qt DLL、pyd 等）
同目录才能加载（sys._MEIPASS = exe 所在目录），因此主程序不能上移到根目录。
本启动器通过路径映射：定位自身所在目录 → 拼接 程序文件/ → 找到主程序 →
以 程序文件/ 为工作目录启动主程序，并透传退出码。
仅依赖 Python 标准库，由构建脚本（build_win.py）在对应架构环境打包为 onefile。
"""
import glob
import os
import subprocess
import sys

APP_NAME = "多通道温度分析仪"
RUNTIME_DIR = "程序文件"


def find_main_exe(runtime_dir):
    """在运行时目录中查找主程序 exe（多通道温度分析仪_*.exe），返回完整路径。

    多个匹配时按名称排序取第一个；目录不存在或没有匹配时返回 None。
    """
    if not os.path.isdir(runtime_dir):
        return None
    matches = sorted(glob.glob(os.path.join(runtime_dir, APP_NAME + "_*.exe")))
    return matches[0] if matches else None


def _show_error(text):
    """弹出中文错误提示框（仅 Windows；其他环境退化为打印到 stderr）。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, 0x10)
    except Exception:
        sys.stderr.write(text + "\n")


def main():
    # 冻结为 onefile 后，sys.executable 即本启动器 exe 的完整路径
    here = os.path.dirname(os.path.abspath(sys.executable))
    runtime_dir = os.path.join(here, RUNTIME_DIR)
    main_exe = find_main_exe(runtime_dir)
    if not main_exe:
        _show_error(
            "未找到主程序文件。\n"
            "请确认整个发布目录解压完整，"
            "且本启动器与“程序文件”文件夹位于同一目录。"
        )
        return 1
    try:
        # 注入软件根目录（launcher 所在的发布包根），让主程序把所有数据写到本目录，
        # 实现免安装绿色版（主程序经 utils.helpers._resolve_app_root 读取此变量）
        child_env = {**os.environ, "MTA_HOME": here}
        proc = subprocess.Popen([main_exe], cwd=runtime_dir, env=child_env)
    except OSError as exc:
        _show_error("启动主程序失败：%s" % exc)
        return 1
    return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
