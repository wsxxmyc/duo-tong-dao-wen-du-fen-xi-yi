# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# 多通道温度分析仪 —— PyInstaller 打包配置
# 目标平台：Windows 7 SP1 / Windows 10·11，32 位(x86) / 64 位(x64)
# 技术栈：Python 3.8（Win7）或 Python 3.9（Win10/11） + PyQt5 + matplotlib
#
# 本文件由 build/build_win.py 通过环境变量注入 PKG_ARCH / PKG_BUNDLE /
# PKG_VERSION / PKG_OUT_NAME 后调用；也可手动执行：pyinstaller build/pyinstaller.spec
# =============================================================================
import os
import re
import time
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.dirname(os.path.dirname(SPECPATH))      # 仓库根目录
SPEC_DIR = SPECPATH                                    # build/ 目录
SRC = os.path.join(ROOT, "src")
ASSETS = os.path.join(ROOT, "assets")
FONTS_DIR = os.path.join(ASSETS, "fonts")
ICONS_DIR = os.path.join(ASSETS, "icons")
MIGRATIONS_DIR = os.path.join(SRC, "device", "database", "migrations")


def _pkg_version():
    """版本号：优先 build_win.py 注入的 PKG_VERSION，否则读根目录 version.py。"""
    v = os.environ.get("PKG_VERSION")
    if v:
        return v
    try:
        with open(os.path.join(ROOT, "version.py"), encoding="utf-8") as fh:
            m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)", fh.read())
        if m:
            return m.group(1)
    except OSError:
        pass
    return "1.0.0"


def _gen_version_info(ver):
    """按 version.py 渲染版本资源模板，返回生成文件路径（模板缺失返回 None）。"""
    tpl_path = os.path.join(SPEC_DIR, "file_version_info.txt")
    if not os.path.isfile(tpl_path):
        return None
    with open(tpl_path, encoding="utf-8") as fh:
        out = fh.read()
    nums = (re.findall(r"\d+", ver) + ["0", "0", "0", "0"])[:4]
    out = out.replace("@VERSION@", ver)
    out = out.replace("@VERPARTS@", "(%s, %s, %s, %s)" % tuple(nums))
    gen = os.path.join(SPEC_DIR, "_version_info.generated.txt")
    with open(gen, "w", encoding="utf-8") as fh:
        fh.write(out)
    return gen


# ---- 由 build_win.py 注入的构建参数 ----
PKG_ARCH = os.environ.get("PKG_ARCH", "x64")          # x86 | x64
PKG_BUNDLE = os.environ.get("PKG_BUNDLE", "onedir")   # onedir | onefile
PKG_PROFILE = os.environ.get("PKG_PROFILE", "win10")  # win7 | win10
PKG_VERSION = _pkg_version()
PKG_OUT_NAME = os.environ.get("PKG_OUT_NAME")         # 完整输出目录名（含版本戳/日期/第N版/架构）

APP_NAME = "多通道温度分析仪"
APP_VERSION = PKG_VERSION
# 启动 exe 名：中文名 + 版本戳（如 多通道温度分析仪_v1.0.0.exe）
EXE_NAME = f"{APP_NAME}_v{APP_VERSION}"
# 输出目录名：优先 build_win.py 注入；单独运行 spec 时回退为 名称_版本_架构
OUT_NAME = PKG_OUT_NAME or f"{APP_NAME}_v{APP_VERSION}_{PKG_ARCH}"

block_cipher = None

# ---- 自动收集 PyQt5 资源（Qt 插件、平台 DLL 等，GUI 必须）----
qt_datas, qt_binaries, qt_hiddenimports = collect_all("PyQt5")

# ---- 强制收集 matplotlib 及其依赖（嵌入版 stdlib 在 zip 内，模块图可能遗漏）----
mpl_datas_extra = []
mpl_hidden_extra = []
for _mod in ["matplotlib", "pyparsing", "cycler", "packaging", "PIL", "six",
             "unittest", "doctest"]:
    try:
        _d, _b, _h = collect_all(_mod)
        mpl_datas_extra += _d
        mpl_hidden_extra += _h
    except Exception:
        pass  # 少数模块不支持 collect_all

datas = []
if os.path.isdir(FONTS_DIR):
    datas.append((FONTS_DIR, "assets/fonts"))
if os.path.isdir(os.path.join(ICONS_DIR, "png")):
    datas.append((os.path.join(ICONS_DIR, "png"), "assets/icons/png"))
if os.path.isfile(os.path.join(ICONS_DIR, "file_icon.ico")):
    datas.append((os.path.join(ICONS_DIR, "file_icon.ico"), "assets/icons"))
if os.path.isdir(MIGRATIONS_DIR):
    datas.append((MIGRATIONS_DIR, "device/database/migrations"))

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[SRC, ROOT],
    binaries=qt_binaries,
    datas=qt_datas + mpl_datas_extra + datas,
    hiddenimports=[
        "utils.core",
        "numpy", "pandas",
        "matplotlib",
        "matplotlib.backends.backend_qt5agg",
        "matplotlib.backends.qt_compat",
        "pyparsing", "cycler", "packaging", "six", "PIL",
        # 嵌入版 stdlib 在 python3x.zip 中，PyInstaller 扫描不到，显式声明
        "unittest", "doctest",
        "openpyxl",   # pandas 读 .xlsx 引擎（core.py 显式使用）
        "xlrd",       # pandas 读 .xls 回退引擎（xlrd<2 才支持 xls）
    ] + qt_hiddenimports + mpl_hidden_extra,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 精简体积：剔除不需要的 PyQt5 模块
        "PyQt5.QtQml", "PyQt5.QtQuick", "PyQt5.QtQuickWidgets",
        "PyQt5.QtMultimedia", "PyQt5.QtMultimediaWidgets",
        "PyQt5.QtWebKit", "PyQt5.QtWebKitWidgets", "PyQt5.QtDesigner",
        "PyQt5.QtNetwork", "PyQt5.QtBluetooth", "PyQt5.QtNfc",
        "PyQt5.QtPositioning", "PyQt5.QtLocation", "PyQt5.QtSensors",
        "PyQt5.QtSerialPort", "PyQt5.QtSql", "PyQt5.QtTest",
        "PyQt5.QtWebChannel", "PyQt5.QtWebEngine", "PyQt5.QtWebEngineCore",
        "PyQt5.QtWebEngineWidgets", "PyQt5.QtXml", "PyQt5.QtXmlPatterns",
        # 不能排除 unittest：pyparsing → pyparsing.testing → unittest
        "tkinter", "pydoc",
        "matplotlib.tests", "numpy.random._examples",
        # 注意：不能排除 PIL —— matplotlib.colors 运行时强制 import PIL
        "scipy", "PyQt6", "PySide2", "PySide6",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

version_file = _gen_version_info(APP_VERSION)

# 图标（可选：build/app.ico 存在即启用）
icon_file = os.path.join(SPEC_DIR, "app.ico")
if not os.path.exists(icon_file):
    icon_file = None

if PKG_BUNDLE == "onefile":
    # 单文件模式。运行时数据写入 exe 同级 用户数据/，不依赖临时目录，仍可持久化。
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        name=OUT_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        codesign_identity=None,
        entitlements_file=None,
        version=version_file,
        icon=icon_file,
    )
    coll = None
else:
    # 目录模式（推荐）：便于在任意“可写位置”运行，数据持久化最可靠。
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=EXE_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        codesign_identity=None,
        entitlements_file=None,
        version=version_file,
        icon=icon_file,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name=OUT_NAME,
    )
