#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
多通道温度分析仪 —— Windows 打包脚本
构建出 Windows 7 与 Windows 10/11 两套 32/64 位可执行文件。

==================== 前置条件（务必先在“构建机”上准备）====================
1. Windows 7 构建使用 Python 3.8.10；Windows 10/11 构建使用 Python 3.9.x。
   - 32 位构建机：使用对应 x86 / win32 安装包
   - 64 位构建机：使用对应 x64 / amd64 安装包
2. 按目标配置安装构建依赖：
       python -m pip install -r build/requirements_build_win7.txt
       python -m pip install -r build/requirements_build.txt
3. 使用 `--profile` 选择目标配置；脚本会校验 Python 版本和解释器位数：

   # 64 位 + 目录模式（推荐）
   python build/build_win.py --profile win10 --arch x64 --bundle onedir
   # 32 位 + 目录模式
   python build/build_win.py --profile win10 --arch x86 --bundle onedir
   # 单文件模式（配置写入 exe 同级的 用户数据/，可持久化）
   python build/build_win.py --profile win10 --arch x64 --bundle onefile

   # Windows 7 兼容版（必须在 Python 3.8.10 环境执行）
   python build/build_win.py --profile win7 --arch x86 --bundle onedir
   python build/build_win.py --profile win7 --arch x64 --bundle onedir

   不指定 --arch 时，自动取当前解释器的位数。

==================== 产物 ====================
   dist/多通道温度分析仪_x64/            （onedir）
   dist/多通道温度分析仪_x64.exe         （onefile）
   32 位对应 多通道温度分析仪_x86 / x86.exe
"""
import os
import sys
import struct
import shutil
import subprocess
import importlib
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "build", "pyinstaller.spec")

# 当前版本号：唯一事实源为根目录 version.py，打包链路（spec/版本资源）全部由此注入
def _read_app_version():
    with open(os.path.join(ROOT, "version.py"), encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("version.py 缺少 __version__")

APP_VERSION = _read_app_version()
# 统一发布归档目录（根目录下，按版本号分子目录，便于追溯每个版本更新次数）
RELEASE_ROOT = os.path.join(ROOT, "发布版本")
# 预发布标识：含这些标识的版本号视为测试版本，不生成标准发布文档
PRE_RELEASE_MARKERS = ("-beta", "-rc", "-alpha", "-dev", "-preview")
# 根目录启动器（onedir 目录版根目录的唯一文件，路径映射启动 程序文件/ 下的主程序）
LAUNCHER_SPEC = os.path.join(ROOT, "build", "launcher.spec")
LAUNCHER_WORK = os.path.join(ROOT, "build", "work_launcher")
LAUNCHER_DIST = os.path.join(ROOT, "build", "launcher_dist")


def is_release_version(version=None):
    """是否为正式发布版本（版本号不含预发布标识）。"""
    version = version or APP_VERSION
    return not any(marker in str(version).lower() for marker in PRE_RELEASE_MARKERS)

BUILD_PROFILES = {
    "win7": {
        "python_major_minor": (3, 8),
        "requirements_file": "requirements_build_win7.txt",
        "description": "Windows 7 兼容构建",
    },
    "win10": {
        "python_major_minor": (3, 9),
        "requirements_file": "requirements_build.txt",
        "description": "Windows 10/11 构建",
    },
}

BUILD_IMPORTS = (
    ("PyInstaller", "PyInstaller"),
    ("PyQt5", "PyQt5"),
    ("matplotlib", "matplotlib"),
    ("numpy", "numpy"),
    ("pandas", "pandas"),
    ("PIL", "Pillow"),
    ("openpyxl", "openpyxl"),
    ("xlrd", "xlrd"),
    ("serial", "pyserial"),
)


def find_missing_dependencies(importer=None):
    """返回构建时缺失的 Python 包名称。"""
    importer = importer or importlib.import_module
    missing = []
    for module_name, package_name in BUILD_IMPORTS:
        try:
            importer(module_name)
        except ImportError:
            missing.append(package_name)
    return missing


def detect_arch():
    """运行本脚本的 Python 解释器位数，即目标架构。"""
    return "x86" if struct.calcsize("P") == 4 else "x64"


def get_output_name(profile_name, arch, version=None, edition=None, date_stamp=None):
    """生成带目标配置隔离标识的发布名称。

    命名规则：多通道温度分析仪_v<版本戳>_<YYYYMMDD>_第<N>版_<架构>[_Win7]
    版本戳、时间戳、第 N 版三者齐全，便于追溯每个版本更新次数。
    """
    get_build_profile(profile_name)
    if arch not in ("x86", "x64"):
        raise ValueError(f"未知架构 {arch}，可选值：x86、x64")
    version = version or APP_VERSION
    date_stamp = date_stamp or time.strftime("%Y%m%d")
    edition = edition or 1
    suffix = "_Win7" if profile_name == "win7" else ""
    return f"多通道温度分析仪_v{version}_{date_stamp}_第{edition}版{suffix}_{arch}"


def get_build_profile(profile_name):
    """返回构建配置；未知配置直接抛出可读错误。"""
    try:
        return BUILD_PROFILES[profile_name]
    except KeyError:
        supported = ", ".join(sorted(BUILD_PROFILES))
        raise ValueError(f"未知构建配置 {profile_name}，可选值：{supported}")


def validate_build_environment(profile_name, arch, version_info=None,
                               pointer_size=None):
    """校验 Python 主次版本和解释器位数是否匹配目标构建。"""
    profile = get_build_profile(profile_name)
    version_info = version_info or sys.version_info
    pointer_size = pointer_size or struct.calcsize("P")

    expected_version = profile["python_major_minor"]
    actual_version = tuple(version_info[:2])
    if actual_version != expected_version:
        major, minor = expected_version
        raise ValueError(
            f"{profile_name} 配置必须使用 Python {major}.{minor}.x，"
            f"当前为 Python {version_info[0]}.{version_info[1]}"
        )

    expected_pointer_size = 4 if arch == "x86" else 8 if arch == "x64" else None
    if expected_pointer_size is None:
        raise ValueError(f"未知架构 {arch}，可选值：x86、x64")
    if pointer_size != expected_pointer_size:
        actual_arch = "x86" if pointer_size == 4 else "x64" if pointer_size == 8 else "未知"
        raise ValueError(
            f"目标架构 {arch} 与当前 Python 解释器位数不一致："
            f"当前解释器为 {actual_arch}"
        )

    return profile


def find_variable_fonts(font_dir=None):
    """扫描发布包内置字体目录，返回含 fvar 表（可变字体）的文件名列表。

    Win7 的 DirectWrite 不支持可变字体，会按默认实例渲染（本项目字体默认
    实例为 Thin/100），10pt 下细笔画整段消失（2026-09-10 真机回归，见
    文档-docs/PITFALLS.md 第 13 节）。发布包内置字体必须是静态实例。
    仅用标准库解析 sfnt 表目录，不依赖构建环境未安装的 fontTools。
    """
    font_dir = font_dir or os.path.join(ROOT, "assets", "fonts")
    offenders = []
    if not os.path.isdir(font_dir):
        return offenders
    for name in sorted(os.listdir(font_dir)):
        if not name.lower().endswith((".ttf", ".otf")):
            continue
        try:
            with open(os.path.join(font_dir, name), "rb") as fh:
                head = fh.read(16)
                if head[:4] == b"ttcf":  # TrueType 集合：定位第一个字体的表目录
                    fh.seek(12)
                    base = struct.unpack(">I", fh.read(4))[0]
                else:
                    base = 0
                fh.seek(base + 4)
                num_tables = struct.unpack(">H", fh.read(2))[0]
                fh.seek(base + 12)  # 表记录区从偏移 12 开始（头 12 字节）
                tags = set()
                for _ in range(num_tables):
                    rec = fh.read(16)
                    if len(rec) < 16:
                        break
                    tags.add(rec[:4])
                if b"fvar" in tags:
                    offenders.append(name)
        except OSError:
            continue
    return offenders


def main():
    # 1) 解析参数；帮助信息不应依赖当前 Python 版本。
    args = sys.argv[1:]
    profile_name = "win10"
    arch = None
    bundle = "onedir"
    edition = 1
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--profile":
            if i + 1 >= len(args):
                sys.stderr.write("ERROR: --profile 必须指定配置名称。\n")
                sys.exit(2)
            profile_name = args[i + 1]
            i += 2
            continue
        if a == "--arch":
            if i + 1 >= len(args):
                sys.stderr.write("ERROR: --arch 必须指定 x86 或 x64。\n")
                sys.exit(2)
            arch = args[i + 1]; i += 2; continue
        if a == "--bundle":
            if i + 1 >= len(args):
                sys.stderr.write("ERROR: --bundle 必须指定 onedir 或 onefile。\n")
                sys.exit(2)
            bundle = args[i + 1]; i += 2; continue
        if a == "--edition":
            # 第 N 版：用于发布目录名，记录本版本的更新次数
            if i + 1 >= len(args):
                sys.stderr.write("ERROR: --edition 必须指定第几版（数字）。\n")
                sys.exit(2)
            edition = args[i + 1]; i += 2; continue
        if a in ("-h", "--help"):
            print(__doc__); sys.exit(0)
        sys.stderr.write(f"ERROR: 未知参数 {a}\n")
        sys.exit(2)

    if arch is None:
        arch = detect_arch()
    if bundle not in ("onedir", "onefile"):
        sys.stderr.write(f"ERROR: 未知形态 {bundle}\n"); sys.exit(2)

    try:
        profile = validate_build_environment(profile_name, arch)
    except ValueError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        sys.exit(2)

    missing = find_missing_dependencies()
    if missing:
        requirements_path = os.path.join(ROOT, "build", profile["requirements_file"])
        sys.stderr.write(
            "ERROR: 构建环境缺少依赖：" + ", ".join(missing) + "。\n"
            f"       请先执行：python -m pip install -r {requirements_path}\n"
        )
        sys.exit(2)

    variable_fonts = find_variable_fonts()
    if variable_fonts:
        sys.stderr.write(
            "ERROR: 发布包内置字体含可变字体（fvar 表），Win7 会按默认实例渲染"
            "导致界面中文断笔：\n       " + "、".join(variable_fonts) + "\n"
            "       请替换为静态实例（fontTools varLib.instancer 生成的 "
            "Regular/Bold，约定见 build/PACKAGING.md）。\n"
        )
        sys.exit(2)

    output_name = get_output_name(profile_name, arch, edition=edition)
    print(
        f"[build] profile={profile_name} ({profile['description']})  "
        f"Python {sys.version.split()[0]} ({arch})  bundle={bundle}  "
        f"version=v{APP_VERSION}  edition={edition}  output={output_name}"
    )

    # 3) 注入环境变量并调用 PyInstaller
    env = os.environ.copy()
    env["PKG_ARCH"] = arch
    env["PKG_BUNDLE"] = bundle
    env["PKG_PROFILE"] = profile_name
    env["PKG_VERSION"] = APP_VERSION
    env["PKG_OUT_NAME"] = output_name

    cmd = [sys.executable, "-m", "PyInstaller",
           "--noconfirm", "--clean",
           "--workpath", os.path.join(ROOT, "build", "work"),
           "--distpath", os.path.join(ROOT, "dist"),
           SPEC]
    print("[build] 运行:", " ".join(cmd))
    rc = subprocess.call(cmd, env=env)
    if rc != 0:
        sys.stderr.write(f"ERROR: PyInstaller 返回 {rc}\n")
        sys.exit(rc)

    # 4) 把使用说明一并放进发布目录（可选，失败不影响主程序）
    copy_docs(profile_name, arch, bundle, edition=edition)

    # 4.5) 目录版：构建并安装根目录启动器（根目录唯一启动文件，路径映射到 程序文件/）
    if bundle == "onedir":
        launcher_exe = build_launcher()
        install_launcher(os.path.join(ROOT, "dist", output_name), launcher_exe)

    # 5) 统一归档：dist/<output_name> 移动到 发布版本/<版本号>/<output_name>
    archive_to_release(output_name, edition)

    # 6) 标准发布文档：仅正式版本生成，测试版本跳过以节约时间
    if is_release_version():
        create_release_docs(output_name, edition)
    else:
        print(f"[build] 测试版本 v{APP_VERSION}：跳过标准发布文档生成")

    # 7) 清理 PyInstaller 中间构件（build/work），释放空间
    clean_build_artifacts()

    print("[build] 完成。发布包位于:", os.path.join(RELEASE_ROOT, f"v{APP_VERSION}", output_name))


RELEASE_DOCS = ("README.md", os.path.join("docs", "使用说明.md"))  # 发布包内随带说明（相对仓库根）


def copy_docs(profile_name, arch, bundle, edition=1):
    """把发布说明文档复制进发布目录（README + docs/使用说明.md），缺失即跳过。"""
    output_name = get_output_name(profile_name, arch, edition=edition)
    if bundle == "onedir":
        dest = os.path.join(ROOT, "dist", output_name)
        organize_onedir_output(dest, ROOT)
        return
    dest = os.path.join(ROOT, "dist")
    if not os.path.isdir(dest):
        return
    for rel in RELEASE_DOCS:
        src = os.path.join(ROOT, rel)
        if os.path.exists(src):
            try:
                shutil.copy(src, dest)
            except Exception as e:
                print(f"[build] 复制说明文档失败（可忽略）: {e}")


def create_release_docs(output_name, edition=1):
    """为正式发布版本生成标准文档（VERSION.md / RELEASE_NOTES.md / PACKAGE_MANIFEST.md）。

    仅正式版本调用；测试版本（版本号含 -beta/-rc 等预发布标识）跳过，
    以节约打包时间。文档写入发布归档目录内。
    """
    release_dir = os.path.join(RELEASE_ROOT, f"v{APP_VERSION}")
    target_dir = os.path.join(release_dir, output_name)
    if not os.path.isdir(target_dir):
        print(f"[build] 警告: 未找到发布目录 {target_dir}，跳过标准文档生成")
        return

    app_name = "多通道温度分析仪"
    exe_name = f"{app_name}_v{APP_VERSION}.exe"
    exe_dir = os.path.join(target_dir, "程序文件") if os.path.isdir(os.path.join(target_dir, "程序文件")) else target_dir
    exe_path = os.path.join(exe_dir, exe_name)
    exe_size = os.path.getsize(exe_path) if os.path.isfile(exe_path) else 0

    # 计算 exe 哈希
    exe_hash = ""
    if os.path.isfile(exe_path):
        try:
            import hashlib
            with open(exe_path, "rb") as f:
                exe_hash = hashlib.sha256(f.read()).hexdigest().upper()
        except Exception as e:
            print(f"[build] 计算 exe 哈希失败（可忽略）: {e}")

    date_stamp = time.strftime("%Y%m%d")
    version_full = f"v{APP_VERSION}"
    docs = {
        "VERSION.md": (
            "# 版本标识文件\n\n"
            f"## 版本信息\n\n"
            f"- **版本号**: {version_full}\n"
            f"- **版本戳**: {APP_VERSION}\n"
            f"- **时间戳**: {date_stamp}\n"
            f"- **第 N 版**: 第 {edition} 版\n"
            f"- **架构**: {arch_of(output_name)}\n"
            f"- **构建时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"- **状态**: 正式版本\n\n"
            "---\n\n"
            "## 文件校验\n\n"
            f"```\n文件名: {exe_name}\n大小: {exe_size} 字节\n"
            f"SHA-256: {exe_hash}\n```\n"
        ),
        "RELEASE_NOTES.md": (
            f"# {app_name} {version_full} 发布说明\n\n"
            f"**发布日期**: {date_stamp}\n"
            f"**版本状态**: 正式版本\n"
            f"**版本戳**: {APP_VERSION}\n"
            f"**第 N 版**: 第 {edition} 版\n"
            f"**架构**: {arch_of(output_name)}\n\n"
            "---\n\n"
            "## 本版新增\n\n"
            "- **四层架构**：界面（ui/）、图表（chart/）、设备（device/）、工具（utils/）分层，主窗口只做编排，代码可维护性好。\n"
            "- **健壮性加固（C1~C8）**：后台线程加载 / 采集解析失败可诊断 / 配置损坏降级 / 图表热路径异常日志全覆盖，关键路径不再静默吞错。\n"
            "- **串口采集自动化**：COM 自动扫描 + 端口记忆、波特率默认 2400 自动遍历、协议自动/新/旧三模式握手、失败原因引导调参。\n"
            "- **采集组数自动**：组数与左面板联动，连接设备后按实际通道数自动确定。\n"
            "- **组合图布局像素级重排**：边距/间隔与文字实测自适应（DPI 缩放、不随窗口大小变化）、导出与前端共用同一布局算法（所见即所得）。\n"
            "- **回到最新交互**：任意标签页点击【回到最新】即回到整体趋势全量视图；整体趋势恒显示全量数据，不受自定义时间轴裁剪。\n"
            "- **.tpx 双击打开**：注册文件关联，双击采集数据文件直接进入分析。\n"
            "- **专用图标**：桌面/任务栏/文件图标统一为程序专用图标（非系统默认）。\n"
            "- **远程离线访问（A23）**：远程数据拉取移至后台线程，大会话不再假死界面。\n\n"
            "## 已知问题\n\n"
            "- **Win7 真机验证待完成**：Win7 x86/x64 发布包已构建，需在 Win7 真机验证串口与绘图行为。\n"
            "- **远程数据全量物化**：远程拉取大会话时内存峰值较高（界面已不冻结），流式物化优化待后续版本。\n"
            "- **设备场景待验证**：真实设备的多组数/协议变体/异常断开场景需在产线环境复核。\n"
            "- **测试环境限制**：`test_live_rendering` 与 `test_live_time_axis` 共 4 个用例在本机无 CJK 字体的测试环境失败（生产环境不受影响）；`test_overview_hover_stays_visible` 在全量顺序下偶发失败（顺序敏感，单跑稳定）。\n"
            "- **离屏渲染限制**：无头/离屏环境下组合图与导出对比需分进程执行（Qt 与 PIL 同进程偶发段错误），桌面正常使用不受影响。\n\n"
            "---\n\n"
            "## 发布包信息\n\n"
            f"- **发布目录**: `{output_name}`\n"
            f"- **启动文件**: `{exe_name}`（发布包根目录启动器，双击即可启动）\n"
            f"- **主程序**: `程序文件/{exe_name}`（与全部依赖同目录）\n\n"
            "## 文件校验\n\n"
            f"```\n文件名: {exe_name}\n大小: {exe_size} 字节\n"
            f"SHA-256: {exe_hash}\n```\n"
        ),
        "PACKAGE_MANIFEST.md": (
            "# 发布包清单\n\n"
            f"**版本**: {version_full}（第 {edition} 版）\n"
            f"**时间戳**: {date_stamp}\n"
            f"**构建时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"**架构**: {arch_of(output_name)}\n"
            f"**构建配置**: `build/build_win.py --bundle onedir --edition {edition}`\n\n"
            "---\n\n"
            "## 启动方式\n\n"
            f"- 双击发布包根目录的 `{exe_name}`（启动器）即可启动。\n"
            f"- 主程序与全部依赖位于 `程序文件/` 内；启动器负责路径映射，无需进入子目录。\n\n"
            "---\n\n"
            "## 主程序校验\n\n"
            f"```\n文件名: {exe_name}\n大小: {exe_size} 字节\n"
            f"SHA-256: {exe_hash}\n```\n"
        ),
    }

    for filename, content in docs.items():
        file_path = os.path.join(target_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[build] 标准文档已生成: {file_path}")


def arch_of(output_name):
    """从发布目录名提取架构标识（x86 / x64）。"""
    for arch in ("x86", "x64"):
        if output_name.endswith(f"_{arch}") or f"_{arch}_" in output_name:
            return arch
    return ""


def archive_to_release(output_name, edition=1):
    """统一归档到 发布版本/<版本号>/<output_name>。

    保留每个版本的历史包，目录名/文件名含版本戳/时间戳/第N版/架构，
    便于按版本追溯更新次数。onedir 移动目录，onefile 移动单文件。
    """
    dist_path = os.path.join(ROOT, "dist", output_name)
    if not os.path.exists(dist_path):
        print(f"[build] 警告: 未找到 dist 产物 {output_name}，跳过归档")
        return

    release_dir = os.path.join(RELEASE_ROOT, f"v{APP_VERSION}")
    os.makedirs(release_dir, exist_ok=True)
    target = os.path.join(release_dir, output_name)

    # 同名产物已存在时覆盖（同一版本第 N 版重复构建场景）
    if os.path.isdir(target):
        shutil.rmtree(target)
    elif os.path.isfile(target):
        os.remove(target)
    shutil.move(dist_path, target)
    print(f"[build] 归档完成: {target}")


def clean_build_artifacts():
    """清理 PyInstaller 中间构件（build/work、work_launcher），释放磁盘空间。"""
    for work_dir in (os.path.join(ROOT, "build", "work"), LAUNCHER_WORK):
        if os.path.isdir(work_dir):
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
                print(f"[build] 已清理中间构件: {work_dir}")
            except Exception as e:
                print(f"[build] 清理中间构件失败（可忽略）: {e}")
    # 启动器 onefile 产物已安装到发布包根目录，删除临时 dist 目录
    if os.path.isdir(LAUNCHER_DIST):
        try:
            shutil.rmtree(LAUNCHER_DIST, ignore_errors=True)
            print(f"[build] 已清理启动器临时产物: {LAUNCHER_DIST}")
        except Exception as e:
            print(f"[build] 清理启动器临时产物失败（可忽略）: {e}")


def launcher_exe_name(version=None):
    """根目录启动器 exe 名（与主程序同名：多通道温度分析仪_v<版本戳>.exe）。"""
    version = version or APP_VERSION
    return f"多通道温度分析仪_v{version}.exe"


def build_launcher():
    """构建根目录启动器（onefile），返回启动器 exe 的完整路径。

    启动器仅依赖 Python 标准库，由同一构建环境（同架构 Python）构建，
    保证与主程序架构一致、Win7 x86/x64 兼容。使用独立 workpath/distpath，
    避免与主程序构建产物冲突。
    """
    env = os.environ.copy()
    env["PKG_VERSION"] = APP_VERSION
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--workpath", LAUNCHER_WORK,
        "--distpath", LAUNCHER_DIST,
        LAUNCHER_SPEC,
    ]
    print("[build] 构建根目录启动器:", " ".join(cmd))
    rc = subprocess.call(cmd, env=env)
    if rc != 0:
        sys.stderr.write(f"ERROR: 启动器构建失败（PyInstaller 返回 {rc}）\n")
        sys.exit(rc)
    launcher = os.path.join(LAUNCHER_DIST, launcher_exe_name())
    if not os.path.isfile(launcher):
        sys.stderr.write(f"ERROR: 未找到启动器产物 {launcher}\n")
        sys.exit(2)
    return launcher


def install_launcher(bundle_dir, launcher_exe=None):
    """把根目录启动器复制到发布包根目录（onedir 形态的根目录唯一文件）。

    返回是否安装成功；启动器缺失时打印警告但不中断构建。
    """
    if not launcher_exe or not os.path.isfile(launcher_exe):
        print("[build] 警告: 未找到启动器，跳过安装到发布包根目录")
        return False
    shutil.copy2(launcher_exe, os.path.join(bundle_dir, os.path.basename(launcher_exe)))
    print(f"[build] 启动器已安装到发布包根目录: {os.path.basename(launcher_exe)}")
    return True


def organize_onedir_output(bundle_dir, docs_source_dir=None):
    """整理目录版输出，避免运行文件和说明文档散落在根目录。"""
    if not os.path.isdir(bundle_dir):
        return

    runtime_dir = os.path.join(bundle_dir, "程序文件")
    docs_dir = os.path.join(bundle_dir, "说明文档")
    os.makedirs(runtime_dir, exist_ok=True)
    os.makedirs(docs_dir, exist_ok=True)

    for name in os.listdir(bundle_dir):
        if name in ("程序文件", "说明文档"):
            continue
        source = os.path.join(bundle_dir, name)
        target = os.path.join(runtime_dir, name)
        if os.path.exists(target):
            if os.path.isdir(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
        shutil.move(source, target)

    if docs_source_dir and os.path.isdir(docs_source_dir):
        for rel in RELEASE_DOCS:
            source = os.path.join(docs_source_dir, rel)
            if os.path.isfile(source):
                try:
                    shutil.copy2(source, os.path.join(docs_dir, os.path.basename(rel)))
                except Exception as exc:
                    print(f"[build] 复制说明文档失败（可忽略）: {exc}")


if __name__ == "__main__":
    main()
