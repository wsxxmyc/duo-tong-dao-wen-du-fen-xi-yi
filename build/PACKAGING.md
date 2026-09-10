# 打包方案说明（Windows 7 / Windows 10·11 · 32/64 位）

## 0. 当前兼容策略

Windows 7 与 Windows 10/11 使用两套构建配置：

| 发布目标 | Python 构建版本 | 构建配置 | 说明 |
|------|------|------|------|
| Windows 7 SP1 | Python 3.8.10 | `--profile win7` | 避免 Python 3.9 在 Win7 上加载缺失的系统 API |
| Windows 10/11 | Python 3.9.x | `--profile win10` | 保留现有已验证的发布基线 |

两种目标系统共用同一套 `src/` 业务源码；只分开维护 Python、第三方依赖、PyInstaller 配置和构建输出。这样新增功能只实现一次，再分别通过最低兼容基线和常规发布环境验证。

本次工作不重写业务源码。只有在 Python 3.8 构建或 Windows 7 真机验证发现明确兼容问题时，才修改对应代码。

## 一、有多少种打包方案？

按 **架构 × 形态** 两个维度组合：

| 维度 | 取值 | 说明 |
|------|------|------|
| 架构 | `x86`（32 位）/ `x64`（64 位） | 由构建机所用 Python 解释器位数决定 |
| 形态 | `onedir`（目录）/ `onefile`（单文件） | 目录模式更稳，单文件便于分发 |

- **构建配置 = 2 × 2 = 4 种**：`x64-onedir`、`x64-onefile`、`x86-onedir`、`x86-onefile`
- **对外发布最小 = 2 个安装包**：`x86` 包（32 位 exe 通过 WOW64 也能在 64 位 Windows 上跑，因此一个 32 位包即可覆盖全部机器）+ `x64` 包（性能更好）。
- **完整发布 = 4 个包**：把单文件版也一并放出，方便“下载即用”。

> ⚠️ 本应用为**绿色版**：运行时所有用户数据（配置、历史数据库、日志）统一写入发布包目录下的
> `用户数据/`（config/data/logs 三个子目录），随软件文件夹走——复制文件夹即带走全部数据。
> **推荐 `onedir` 形态**：把整个发布包目录放到任意**可写位置**即可。
> 本仓库为开源基线：运行数据始终读写 `用户数据/`，不含旧版 `%APPDATA%` 数据迁移逻辑。

## 二、为什么 Windows 7 使用 Python 3.8？

目标要覆盖 **Windows 7**，而原栈（PyQt6 + Python 3.13）做不到：

| 组件 | 对 Win7 的支持 | 结论 |
|------|----------------|------|
| Qt6 / PyQt6 | 官方仅支持 Windows 10 (1809)+ | ❌ 不支持 Win7 |
| PyQt5 / Qt5 | 官方支持 Windows 7 SP1+，含 32/64 位轮子 | ✅ |
| Python 3.13 | 不支持 Win7（最低 Win10） | ❌ |
| Python 3.9 | 启动时可能依赖 Win7 缺少的 `api-ms-win-core-path-l1-1-0.dll` | ❌ |
| Python 3.8.10 | 可作为 Win7 兼容构建基线 | ✅ |

代码已迁移到 PyQt5（作用域枚举、matplotlib 后端 `backend_qt5agg`）。绿色版改造后，所有用户数据（配置/数据库/日志）统一存放在发布包目录的 `用户数据/` 下（由 `utils.helpers._resolve_app_root` 解析软件根目录，launcher 通过 `MTA_HOME` 环境变量透传），不再使用 `%APPDATA%`。

字体资源：发布包内置 `Noto Sans SC` 静态实例（`NotoSansSC-Regular.ttf` / `NotoSansSC-Bold.ttf`，SIL OFL 1.1），用于中文界面和图表；界面数字优先使用系统 `Consolas`，不存在时自动回退到其他等宽字体。字体许可证位于 `assets/fonts/OFL-1.1.txt`。

> **Win7 兼容约定（2026-09-10 起强制）**：内置字体必须是**静态实例，禁止可变字体（VF）**。Win7 的 DirectWrite 不支持 VF，会按默认实例渲染导致界面中文断笔（属 Win7 DirectWrite 已知限制）。`build_win.py` 打包前自动扫描 `assets/fonts/`，发现 fvar 表即拒绝构建。注意：给 Win7 目标机把字体安装进系统**不能**解决此类问题（渲染栈不认 VF），禁止走"管理员装字体"路线；字体修复一律在程序自带资源层面完成。

## 二点五、发布包命名规则（版本戳 / 时间戳 / 第 N 版）

> 本规则自 v1.0.0 起强制生效，用于追溯每个版本的更新次数。

- **发布目录名**：`多通道温度分析仪_v<版本戳>_<YYYYMMDD>_第<N>版_<架构>[_Win7]`
  - 例如：`多通道温度分析仪_v1.0.0_20260810_第1版_x64`
  - `版本戳` = 语义化版本号（如 `1.0.0`）；`YYYYMMDD` = 构建日期；
  - `第<N>版` = 该版本的第 N 次打包（`--edition N` 指定，默认 `1`）；`架构` = `x86` / `x64`；Win7 包带 `_Win7`。
- **启动 exe 名**：`多通道温度分析仪_v<版本戳>.exe`（中文名 + 版本戳）
  - 目录版根目录为**根目录启动器**（onefile，与主程序同名，双击即启动）；
    完整主程序与全部依赖位于 `程序文件/` 内（依赖必须与主程序同目录，PyInstaller onedir 约束）。
- **目录版发布包结构（免安装绿色软件惯例：根目录仅 1 个启动文件）**：
  ```
  多通道温度分析仪_v1.0.0_20260810_第1版_x64/
  ├── 多通道温度分析仪_v1.0.0.exe   # 根目录启动器（用户双击入口，根目录唯一文件）
  ├── 程序文件/                         # 完整 PyInstaller onedir 产物（主程序 + 全部依赖）
  │   └── 多通道温度分析仪_v1.0.0.exe  # 主程序（依赖与其同目录）
  ├── 说明文档/README.md                  # 项目说明与使用入口
  └── 说明文档/使用说明.md                # docs/使用说明.md 副本
  ```
  - 启动器 `build/launcher.py` 负责路径映射：定位自身所在目录 → 进入 `程序文件/` →
    以该目录为工作目录启动主程序并透传退出码；`程序文件/` 缺失或主程序找不到时弹出中文错误提示。
  - 启动器由构建脚本自动生成（`build/launcher.spec`，onefile、无控制台、同图标同版本信息），
    与主程序同架构构建，Win7 x86/x64 自动兼容；onefile 单文件形态不生成启动器。
- **统一归档**：打包完成后自动移动到 `发布版本/v<版本戳>/<发布目录名>/`，同一版本多次打包会按「第 N 版 + 日期」区分并存档，便于追溯更新次数。
- **中间构件清理**：打包完成后自动删除 `build/work/`（PyInstaller 中间产物），释放磁盘空间。
- **标准文档（VERSION.md / RELEASE_NOTES.md / PACKAGE_MANIFEST.md）**：仅**正式版本**（版本号不含 `-beta` / `-rc` / `-alpha` / `-dev` 等预发布标识）由脚本自动生成并写入发布包；**测试版本跳过**，以节约打包时间。

## 三、构建步骤（在构建机上执行）

> 构建机要求：Windows + 对应目标配置的 Python。Win7 使用 Python 3.8.10，Windows 10/11 使用 Python 3.9.x；两套环境不要混装依赖。
> 一台机器一次只能出一种架构，故 x86/x64 两个包需分别在对应位数的 Python 环境下构建。

```bat
:: 1) 准备目标 Python 环境（Win7 使用 3.8.10；Win10/11 使用 3.9.x）
:: 2) 安装构建依赖
:: Win7 环境
python -m pip install -r build/requirements_build_win7.txt
:: Win10/11 环境
python -m pip install -r build/requirements_build.txt

:: 3) 打包（自动取当前解释器位数；也可显式 --arch x86 / x64）
::    可选 --edition N 指定"第 N 版"，默认 1，用于发布目录名记录更新次数
python build/build_win.py --profile win10 --arch x64 --bundle onedir --edition 1
python build/build_win.py --profile win10 --arch x86 --bundle onedir --edition 1

:: Windows 7 兼容版
python build/build_win.py --profile win7 --arch x64 --bundle onedir --edition 1
python build/build_win.py --profile win7 --arch x86 --bundle onedir --edition 1

:: 如需单文件版：
python build/build_win.py --profile win10 --arch x64 --bundle onefile --edition 1
python build/build_win.py --profile win10 --arch x86 --bundle onefile --edition 1

:: Windows 7 单文件版（先完成目录版真机验证后再生成）
python build/build_win.py --profile win7 --arch x64 --bundle onefile --edition 1
python build/build_win.py --profile win7 --arch x86 --bundle onefile --edition 1
```

最终发布包统一归档到 `发布版本/v<版本戳>/`（构建过程中位于 `dist/`，完成后自动移动）：

- 目录版（例如）：`发布版本/v1.0.0/多通道温度分析仪_v1.0.0_20260810_第1版_x64/`
- Windows 7 目录版：`发布版本/v1.0.0/多通道温度分析仪_v1.0.0_20260810_第1版_Win7_x64/`
- 单文件版（例如）：`发布版本/v1.0.0/多通道温度分析仪_v1.0.0_20260810_第1版_x64.exe`
- Windows 7 单文件版：`发布版本/v1.0.0/多通道温度分析仪_v1.0.0_20260810_第1版_Win7_x64.exe`
- 目录版内根目录启动文件：`多通道温度分析仪_v1.0.0.exe`（双击即启动，路径映射到 `程序文件/` 下的主程序）

打包完成后自动清理 `build/work/` 中间构件；`dist/` 在归档后不再保留本次产物。

（可选）把 `build/app.ico` 放一个图标，打包时会自动启用；发布时 `README.md` 与 `docs/使用说明.md` 自动随目录版拷贝进 `说明文档/`。

## 四、目标机（Win7~11）运行前提

1. **操作系统**：Windows 7 SP1 及以上（Qt5 需要 SP1）。
2. **Visual C++ 运行库**：Win7 兼容包基于 Python 3.8.10 构建，目标机仍需要 **VS 2015–2019 运行库（vcruntime140）**。
   绝大多数 Win7+ 已自带；若启动报“缺少 VCRUNTIME140.dll / api-ms-win-crt-*.dll”，
   在目标机安装 [Visual C++ Redistributable 2015–2019 (x86 与 x64 都要装)]，
   并确认已装 **Universal C Runtime (KB2999226)**（Win7 补丁）。
3. **安装位置**：目录版建议解压/复制到**用户有写入权限的目录**
   （如 `D:\Tools\多通道温度分析仪_x64\`、`%LOCALAPPDATA%`、或普通用户目录），
   避免直接放进需要管理员权限的 `C:\Program Files\`。绿色版所有数据（配置、数据库、日志）
   写入发布包目录的 `用户数据/`，**与安装位置耦合**——因此安装位置必须有写权限；
   Program Files 等只读位置会导致数据无法保存。

## 五、文件清单

| 文件 | 作用 |
|------|------|
| `build/pyinstaller.spec` | PyInstaller 规格（按 `PKG_ARCH` / `PKG_BUNDLE` 环境变量生成 4 种变体） |
| `build/launcher.py` | 根目录启动器源码（路径映射启动 `程序文件/` 下的主程序） |
| `build/launcher.spec` | 启动器 PyInstaller 规格（onefile、无控制台、与主程序同名） |
| `build/build_win.py` | 构建编排脚本：校验目标配置、Python 版本、架构和依赖，调用 PyInstaller，并自动构建/安装根目录启动器 |
| `build/requirements_build.txt` | Windows 10/11 Python 3.9 构建依赖 |
| `build/requirements_build_win7.txt` | Windows 7 Python 3.8.10 构建依赖 |
| `build/file_version_info.txt` | exe 版本信息资源（中文，显示在文件属性里） |
| `build/app.ico`（可选） | 应用图标，放入即启用 |

## 六、兼容门禁

涉及 Qt、串口、路径、线程、matplotlib、依赖或构建脚本的改动，提交前必须：

1. 在 Python 3.8.10 环境执行导入和 `compileall` 检查；
2. 按影响范围运行自动化测试；
3. 涉及冻结行为时记录 Win7 x86 / x64 构建结果；
4. 涉及真实设备或目标系统行为时，按 `docs/验证清单.md` 记录真机结果。
