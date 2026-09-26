# Keil Port Studio

[English](README.en.md) · **2.3.0-dev2 / 工程适配预发布版**

[下载 Windows 完整程序包](https://github.com/embedded-craft-dong/keil-port-studio/releases/download/v2.3.0-dev2/KeilPortStudio-Windows-x64.zip) · [发布说明与源码包](https://github.com/embedded-craft-dong/keil-port-studio/releases/tag/v2.3.0-dev2)

当前源码新增只读[工程体检](docs/DOCTOR.zh-CN.md)与[标准库 RTOS / 组件适配](docs/SPL.zh-CN.md)。
另有 [CubeMX 重新生成保护、旧组件隔离与接入恢复](docs/CUBEMX.zh-CN.md)。
新增[文件/Include 引用移除和器件驱动生成器](docs/DRIVERS.zh-CN.md)，可选软件 I²C、硬件阻塞与 DMA 完成等待接口。
新驱动目前仅主机协议与编译验证，不作为实板或 DMA 性能验收。
当前源码支持 STM32F1/F4 HAL/标准库自动生成硬件 I²C/SPI、软件 I²C 的板级 API；仍需明确总线/引脚并调用初始化。
F1 支持已包含在 `2.3.0-dev2` 源码和 Windows 包中；编译/链接与模拟测试不等于实板验收。
自动 DMA 和其他 STM32 系列尚未覆盖，可显式使用通用回调模式。
`2.3.0-dev2` 提供源码和 Windows 包；旧 `dev1` 和 `2.2.0-rc6` 仍保留供回退。

主要适配 STM32CubeMX 生成的 STM32 HAL + Keil 工程。开发版增加 STM32 SPL 的
FreeRTOS/CMSIS-V2、RT-Thread 启动，以及 FatFS、LwIP、TinyUSB 入口接入。F407/AC5 已有独立实板
RTOS/LVGL 联合、RAM 文件系统、UDP 回环及 TinyUSB CDC 回传/HID 枚举测试；不代表所有组件或硬件组合均已验证。
请先阅读[适配范围与使用前检查](docs/SUPPORT.zh-CN.md)。CubeMX 工程也不等于所有板卡已验证。

最新实板覆盖与剩余条件见[硬件验证矩阵](docs/HARDWARE-MATRIX.zh-CN.md)。

面向 Keil MDK `.uvprojx/.uvproj` 工程的中间件移植、工程管理工具。
独立复制组件源码，逐文件勾选，预览后修改，保留事务记录和恢复入口。
支持 FreeRTOS/CMSIS-RTOS2、RT-Thread、LVGL、FatFS、LittleFS、LwIP、TinyUSB、
SEGGER RTT、CMSIS-DSP 和外设锁模板。生成框架不等于完成硬件驱动。

## 开始使用

- Windows 便携版：完整解压 Windows ZIP，双击 `KeilPortStudio.exe`。
  **不能只复制 EXE**；同目录 `_internal` 是运行依赖，无需自行安装 Python。
- 源码版：安装带 Tkinter 的 Python 3.8+，运行 `python keil_port_tool.py`。
  请保留同目录 `kps_core/`，不要只复制入口 PY 文件。
  CLI 示例和参数见 `python keil_port_tool.py --help`。构建依赖不是运行依赖。
- 右上角 `zh-CN / en` 切换界面语言，保存到当前用户设置；组件选择和文件勾选保留。
  英文界面不会翻译工程路径、宏、原始编译日志；部分底层诊断仍是中文。
- 底栏“指南”可离线阅读中英文操作及移植后指导。

## 文档

- [图形界面教程](docs/GUI.zh-CN.md)
- [每种组件移植后还需要做什么](docs/POST-PORTING.zh-CN.md)
- [Git 管理与安装](docs/GIT.zh-CN.md)
- [工程体检：文件引用与候选引脚冲突](docs/DOCTOR.zh-CN.md)
- [STM32 标准库适配：前置准备、任务入口与实测边界](docs/SPL.zh-CN.md)
- [2.3.0-dev2 更新与已知限制](docs/RELEASE-NOTES-2.3.0-dev2.md)

## 重要边界

运行前关闭目标 Keil 工程并备份。普通文件添加只添加引用，中间件移植才复制独立源码。
FreeRTOS 与 RT-Thread 同一工程只能选择一种；RT-Thread 当前是 Cortex-M4 内核集成，
不是完整 BSP/软件包管理器。工具不修改 CubeMX `.ioc`，不支持完整 CMSIS Solution/RTE 管理。
屏幕、SD/Flash、PHY、USB、DMA/缓存及中断必须按板卡适配，不能只看编译通过。
文档中的硬件结果仅证明具体测试组合；不是所有芯片、组件版本、组合的认证。
已知限制：测试板 USB Host 的 GPIO 控制 VBUS 关断实测未通过，原因未定位；
普通 U 盘读写及实际断电保留已通过。这两项不能混淆。尚无无 Python/Git 的干净 Windows 验收。

Git 面板默认管理当前工程，可换目录；首次安装和远端操作需确认。不存账号密码，不自动上传。
Windows 包未签名：校验 SHA256，按组织安全规则使用，不要关闭安全防护来运行。

## 开发与开源

运行 `python tests/test_git_panel.py` 等 `tests/test_*.py`。Tk 测试需要图形桌面，
Git 测试需要 Git；真实 Keil 编译用环境变量显式启用，详见测试报告。

- [Windows 构建与验证](docs/RELEASE.zh-CN.md)
- [工具栏测试报告](docs/GUI-TEST-REPORT.zh-CN.md)
- [可靠性加固与历史验证](docs/RELIABILITY.zh-CN.md)
- [RC6 云端 CI 记录](https://github.com/embedded-craft-dong/keil-port-studio/actions/runs/35721027566)

CI 不替代实板验证或无 Python/Git 的干净 Windows 验收。

工具代码采用 [MIT](LICENSE)，允许自由使用、修改和再分发，保留版权与许可声明。
第三方中间件及运行库保持各自许可证，参见 [第三方说明](THIRD-PARTY-NOTICES.md)。
参与改进、报告问题和提交硬件验证记录请见 [贡献指南](CONTRIBUTING.md)。
