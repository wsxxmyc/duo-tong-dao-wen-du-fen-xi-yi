# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# 多通道温度分析仪 —— 根目录启动器 PyInstaller 规格
# 目标平台：Windows 7 SP1 / Windows 10·11，32 位(x86) / 64 位(x64)
#
# 由 build/build_win.py 注入 PKG_VERSION 后调用：
#   python -m PyInstaller --noconfirm --clean \
#     --workpath build/work_launcher --distpath build/launcher_dist build/launcher.spec
#
# 启动器仅依赖 Python 标准库（os/glob/subprocess/sys/ctypes），
# 以 onefile、无控制台形态生成，与主程序同名，便于用户识别与双击启动。
# =============================================================================
import os
import re

SPEC_DIR = SPECPATH  # build/ 目录
ROOT = os.path.dirname(SPEC_DIR)


def _pkg_version():
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
    tpl_path = os.path.join(SPEC_DIR, "file_version_info.txt")
    if not os.path.isfile(tpl_path):
        return None
    with open(tpl_path, encoding="utf-8") as fh:
        out = fh.read()
    nums = (re.findall(r"\d+", ver) + ["0", "0", "0", "0"])[:4]
    out = out.replace("@VERSION@", ver)
    out = out.replace("@VERPARTS@", "(%s, %s, %s, %s)" % tuple(nums))
    gen = os.path.join(SPEC_DIR, "_launcher_version_info.generated.txt")
    with open(gen, "w", encoding="utf-8") as fh:
        fh.write(out)
    return gen


PKG_VERSION = _pkg_version()
APP_NAME = "多通道温度分析仪"
# 启动器 exe 名与主程序一致：多通道温度分析仪_v<版本戳>.exe
EXE_NAME = f"{APP_NAME}_v{PKG_VERSION}"

version_file = _gen_version_info(PKG_VERSION)

# 图标（可选：build/app.ico 存在即启用）
icon_file = os.path.join(SPEC_DIR, "app.ico")
if not os.path.exists(icon_file):
    icon_file = None

a = Analysis(
    [os.path.join(SPEC_DIR, "launcher.py")],
    pathex=[SPEC_DIR],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 启动器不依赖任何第三方库，显式排除以缩小体积、加快启动
        "PyQt5", "matplotlib", "numpy", "pandas", "PIL", "serial",
        "openpyxl", "xlrd", "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
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
