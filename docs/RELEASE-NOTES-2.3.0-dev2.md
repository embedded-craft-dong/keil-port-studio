# Keil Port Studio 2.3.0-dev2

## 中文

本预发布版在 dev1 基础上增加 STM32F1 HAL／标准库的器件驱动自动端口，源码与 Windows 包同步更新。
旧 dev1 / RC6 保留；请先在工程副本上预览。此版本不宣称所有 STM32 系列或硬件均已验证。

### 变化

- F1 HAL 复用公共阻塞 I²C/SPI API，F1 标准库使用正确的 APB2 GPIO 时钟和 Out_PP/Out_OD 输出模式。
- 支持硬件 I²C、硬件 SPI、软件 I²C，覆盖现有七种器件生成器；实例与接线仍需明确选择。
- 兼容 F1 旧 CMSIS 缺少的 DWT 定义和异常状态查询，不修改用户 SDK。
- 保留已有 AFIO/总线重映射，不自动关闭 JTAG；CS/软件 I²C 保留 PA13/PA14/PA15/PB3/PB4。
- 修正 SPL 双字节 I²C 接收的 POS、ADDR、ACK 操作顺序；同步双语界面提示与生成指南。

### 验证与限制

- 本机 33 组回归测试通过，包括 F4 回归、GUI 回调、事务、引用移除和协议模拟执行。
- F1 自动 API 已做主机模拟执行；真实 SDK 的 Cortex-M3/C99/O2 编译与链接覆盖 HAL
  STM32F103xB/xE、STM32F107xC 及 SPL STM32F10X_MD/HD/CL，每项含硬件/软件 I²C 与 SPI。
  链接测试使用原厂实现而不只是模拟头文件，测试镜像未烧录。
- **F1 尚未实板验收**；上述结果不代表电气、外设时序、所有封装或 SDK 版本已验证。
- 自动 DMA 和其他 STM32 系列仍未覆盖；generic DMA 需要用户接入，不代表 DMA 性能已测。
- 无 Python/Git 的干净 Windows 验收尚未完成，程序未数字签名。云端 CI 缺少私有 SDK/Keil 时明确跳过对应检查。
- 既有硬件限制不变，包括 USB Host VBUS 软件关断未通过；见 dev1 说明及硬件矩阵。

完整解压 `KeilPortStudio-Windows-x64.zip` 后运行 EXE，保留 `_internal`。
`KeilPortStudio-source.zip` 含 Python 源码、测试和中英文文档；`SHA256SUMS.txt` 用于校验压缩包。
现有生成包保留用户修改，不会静默覆盖；切换板型前备份并卸载旧驱动包，再按新配置生成。
不包含私有工程、原始串口日志或第三方 SDK；工具及原创驱动为 MIT，第三方组件遵循各自许可证。

指南：[器件驱动](DRIVERS.zh-CN.md) · [dev1 基础功能与限制](RELEASE-NOTES-2.3.0-dev1.md)

## English

This prerelease adds STM32F1 HAL/SPL automatic device-driver ports to dev1, in both source and Windows packages.
Older releases remain available. Preview on a project copy; this is not all-STM32 or all-hardware certification.

- Shared blocking HAL I2C/SPI APIs; F1 SPL-specific APB2 GPIO clocks and Out_PP/Out_OD modes.
- Hardware I2C/SPI and software I2C for the existing seven device generators, with explicit bus/pin selection.
- Local compatibility for legacy F1 CMSIS missing DWT/exception helpers, without editing the user's SDK.
- Existing AFIO remapping is preserved; no automatic JTAG disable. Debug pins are reserved for CS/software I2C.
- Corrected SPL two-byte receive POS/ADDR/ACK ordering and updated bilingual UI/generated guidance.

All 33 local regression scripts passed. F1 generated APIs execute against host mocks; actual vendor SDKs
compile and link at Cortex-M3/C99/O2 for HAL STM32F103xB/xE, STM32F107xC and SPL STM32F10X_MD/HD/CL,
each using hardware/software I2C plus SPI. Link fixtures are never flashed.
**No F1 physical-board acceptance has been performed.** This is not coverage of every package or SDK version.
Automatic DMA/other families remain unsupported; generic DMA needs board hooks and is not a performance claim.
Clean Windows without Python/Git remains untested; the EXE is unsigned. Hosted CI explicitly skips unavailable
private SDK/commercial compiler checks. Existing hardware limits, including failed USB Host VBUS software-off,
are unchanged.

Extract the full Windows ZIP, keeping `_internal` beside the EXE. The source ZIP contains Python modules,
tests and bilingual guides; SHA256SUMS verifies both archives. Existing generated files preserve user edits:
back up and uninstall an old pack before changing board configuration. No private projects, raw serial logs
or third-party SDK bundles are included. The tool/original drivers use MIT; upstream licenses still apply.

Guides: [device drivers](DRIVERS.en.md) · [dev1 features and limits](RELEASE-NOTES-2.3.0-dev1.md)
