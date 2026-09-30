# 多通道温度分析仪  (`duo-tong-dao-wen-du-fen-xi-yi`)

> 多通道温度分析仪（Multi-Channel Temperature Analyzer） Windows 桌面上位机，用于多通道温度测试仪的  离线数据分析  与  串口在线采集  ： 多通道实时趋势、统计与组合图、报警…

## 这是什么

多通道温度分析仪（Multi-Channel Temperature Analyzer） Windows 桌面上位机，用于多通道温度测试仪的  离线数据分析  与  串口在线采集  ： 多通道实时趋势、统计与组合图、报警、历史库、PNG / Excel / A4 报告导出， 以及局域网远程访问

## 技术栈与架构

- **分类**：桌面应用 / 图像/视觉 / 数据处理/报表 / 硬件/通信
- **主要语言**：Python、SQL
- **关键依赖 / 框架**：PyQt5、matplotlib、numpy、pandas、Pillow、openpyxl、xlrd、pyserial

## 目录内容（顶层）

- `assets/`
- `docs/`
- `src/`
- `导出图片/`
- `用户数据/`

## 规模与完整度

- 完整度评分：**97%**
- 源码文件：81 个
- 子目录：24 个
- 体积：22.4MB

## 备注

- 仓库类型：GitHub 私有仓库
- 本文档由代码整理工具自动生成，用于快速了解仓库用途。

---

## 📄 项目原始说明（保留）

# 多通道温度分析仪（Multi-Channel Temperature Analyzer）

Windows 桌面上位机，用于多通道温度测试仪的**离线数据分析**与**串口在线采集**：
多通道实时趋势、统计与组合图、报警、历史库、PNG / Excel / A4 报告导出，
以及局域网远程访问。纯 Python + PyQt5 实现，绿色免安装，兼容 Windows 7 SP1 ~ Windows 11（x86 / x64）。

## 功能一览

- **离线分析**：导入 `.xls` / `.xlsx` / `.csv` / `.txt` / `.tpx` 数据文件，自动识别通道与时段。
- **在线采集**：RS232 串口协议（自动检测 / 新版 / 旧版三模式），COM 口扫描与记忆、
  波特率自动遍历、通道组数自适应、断线重连与特殊值（通道关闭 / 溢出 / 热电偶开路）无效化处理。
- **实时绘图**：多通道趋势、前 N 分钟窗口、整体趋势全量视图、组合图、通道对比，
  秒级刷新限流与降采样，避免全量重绘卡顿；智能时间轴与取数记忆化保证长会话流畅。
- **统计与报警**：均值 / 极值 / 标准差，上下限报警、声音提示与报警跟随，Modbus RTU 继电器输出。
- **历史库**：SQLite 会话持久化、按日期浏览、远程会话拉取、旧版本数据一键迁移。
- **跨会话对比**：把多个历史会话的同一通道曲线放在一张图对比（含采集中会话），
  相对 / 绝对时间轴切换、每条曲线最高 / 最低 / 平均统计、悬停取值与 PNG 导出。
- **主题与外观**：多套工业 HMI 主题（深色 / 浅灰工业等）、通道配色方案、字体与坐标轴配置、高 DPI。
- **导出**：PNG 图表、Excel 数据、分析报告、A4 横向报告（含底部统计表）。
- **远程访问**：TCP / HTTP 局域网服务 + 配对码，移动端可视化与数据下载（防火墙一键检测放行）。
- **悬浮球 / 监控弹窗**：采集状态悬浮球与无边框实时监控窗。

## 运行环境

| 目标 | Python | 说明 |
| --- | --- | --- |
| Windows 7 SP1 | 3.8.10（x86 / x64） | Win7 不能运行 Python 3.9+ 构建产物；需 VS2015-2019 运行库 |
| Windows 10 / 11 | 3.9.x（x86 / x64） | 常规发布基线 |

依赖：PyQt5、matplotlib、numpy、pandas、Pillow、openpyxl、xlrd、pyserial（见 `requirements.txt`）。

## 从源码运行

```bat
:: 1) 准备 Python 3.9.x（Win10/11）或 3.8.10（Win7 兼容验证）
:: 2) 安装依赖
python -m pip install -r requirements.txt
:: 3) 启动
python main.py
```

数据文件双击（注册 `.tpx` 关联后）或拖入窗口也可打开。运行时所有用户数据写入

程序同级的 `用户数据/`（config / data / logs），整个文件夹拷走即完成迁移（绿色版）。

## 打包发布（Windows）

```bat
:: Win10/11 x64 目录版（推荐）
python -m pip install -r build/requirements_build.txt
python build/build_win.py --profile win10 --arch x64 --bundle onedir

:: Win7 x86 目录版（必须在 Python 3.8.10 x86 环境执行）
python -m pip install -r build/requirements_build_win7.txt
python build/build_win.py --profile win7 --arch x86 --bundle onedir
```

完整参数（`--arch`、`--bundle onefile`、`--edition N`）、产物命名与归档规则见

[build/PACKAGING.md](build/PACKAGING.md)；发布前逐项验证见

[docs/验证清单.md](docs/验证清单.md)。

## 目录结构

```
open_source/
├── main.py            # 入口：Qt 初始化与 src/ 路径注入
├── version.py         # 版本唯一事实源（打包链路统一读取）
├── src/
│   ├── ui/            # 主题引擎、主窗口编排、widgets 组件、dialogs 对话框
│   ├── chart/         # matplotlib 绘图引擎（渲染器 / 标签页 / 降采样）
│   ├── device/        # 串口协议与采集线程、数据总线(datastore)、SQLite(database)、远程访问(network)
│   └── utils/         # 纯数据模型(core)、配置、导出、字体、报警音、Modbus RTU 等工具
├── assets/            # 内置字体（SIL OFL）与图标资源（含再生成脚本）
├── docs/              # 使用说明 / 模块架构 / 打包指南 / 迁移与兼容性说明 / 验证清单
└── build/             # PyInstaller 规格、启动器、构建编排脚本与依赖清单
```

依赖方向：`main.py → ui → chart / device / utils`；`device`、`utils` 不触碰 Qt 控件，

后台线程一律经 `pyqtSignal` 与主窗口通信。

## 许可证与免责

- 本项目以 **GNU GPL-3.0** 许可证发布（`LICENSE` 为官方全文）。选择原因：程序依赖
  PyQt5（GPL / 商业双许可），在未持有 Qt 商业授权的情况下，GPL-3.0 是合规的开源
  分发方式。您可以自由使用、学习、修改与再分发本项目，惟衍生作品须同样以
  GPL-3.0 开源；该协议不限制您将其用于个人或商业场景。
- 第三方组件：PyQt5（GPL / Riverbank 商业双许可）、matplotlib（PSF/BSD 风格）、
  numpy（BSD）、pandas（BSD-3）、Pillow（MIT-CMU）、openpyxl（MIT）、xlrd（BSD）、
  pyserial（PSF-2.0）；内置字体 Noto Sans SC（SIL OFL 1.1，见
  `assets/fonts/OFL-1.1.txt`），可随程序分发。各组件由其原许可证约束。
- 本程序生成的 `.tpx` 二进制工程格式头部魔数字节与部分温度测试仪配套软件兼容，
  该格式仅用于数据互通，不代表与任何厂商存在关联。
- 串口协议以公开可得的功能行为实现，请在与设备供应商的协议 / 保密约定允许范围内使用。
- 软件按“现状”提供，不构成计量或安全认证依据。

## 贡献与联系

欢迎 Issue / Pull Request。提交前请在目标 Python 环境执行：

`python -m compileall src main.py` 与基本冒烟（打开主窗口、导入 `data/` 样例、串口模拟）。
