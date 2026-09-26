# Keil Port Studio 2.3.0-dev1

## 中文

这是功能扩展**预发布版**，不是“所有芯片、驱动与硬件组合都已实测”的稳定版。
旧 RC6 保留，不覆盖旧资产。请先在工程副本上预览并验证。

### 新增与改进

- 工程工具新增文件引用 / Include 路径批量移除：按 Target 精确选择、预览、事务回滚；不删除磁盘源码。
- 新增器件驱动生成：W25Q128JV、24LC02B、24LC256、SHT3x-DIS、BH1750FVI、ADS1115、SSD1306。
  支持软件 I²C、硬件阻塞、通用 DMA 完成等待接口；器件型号与功能范围见驱动指南。
- STM32F4 HAL / 标准库首批自动端口：检测已有 I²C/SPI 实例，明确选择 GPIO 后生成硬件 I²C/SPI、
  软件 I²C、延时和并发保护 API；无需手填这些回调，仍需执行原有总线初始化并显式调用生成接口。
- 新增只读工程体检、CubeMX 组件归属冲突检查、独立 KPS 源码布局、旧布局隔离与重新生成后的显式接入恢复。
  不改 `.ioc`，不静默覆盖 CubeMX 或用户代码。
- 新增 SPL RTOS/组件入口适配、共享主循环的精确补丁归属与卸载、DSP 依赖检查和 Windows 深层目录处理。
- 完善双语教程、GUI 配置与预览；修复回滚后空目录阻挡再生成、窗口版 EXE 命令行错误弹窗阻塞等问题。

### 验证与限制

- 发布前本机完整回归：32 组测试脚本通过，包含 GUI 回调、事务、重复移植、协议模拟执行及 Keil 编译检查。
- 新器件协议与自动端口使用生成 C 的主机模拟测试；STM32F4 HAL/SPL 端口通过真实 SDK 头文件的 AC5 编译。
  这些**不等于新增器件已实板验收**，也没有 DMA 性能测量。
- 自动端口目前仅 STM32F4 的上述模式；**自动 DMA 和其他 STM32 系列未覆盖**，显式使用 generic 接口另行适配。
- 已有 F407/AC5 中间件运行、显示触摸、存储、网络和 USB Device 的具体证据与限制，见硬件矩阵和 SPL 指南；
  它们不为新增传感器驱动背书。USB Host VBUS 软件关断未通过；没有新增该项通过结论。
- 没有无 Python/Git 的干净 Windows 环境验收；本机 EXE 与缩减 PATH 测试不能替代。程序未数字签名。
- CI 无商业 Keil / 私有实板工程时会明确跳过相应测试；云端通过不意味着硬件通过。

### 下载与升级

- `KeilPortStudio-Windows-x64.zip`：完整解压，运行其中的 `KeilPortStudio.exe`，保留同目录 `_internal`。
- `KeilPortStudio-source.zip`：源码、测试和双语文档；入口 PY 需与 `kps_core/` 一起使用。
- `SHA256SUMS.txt`：上述两个压缩包的 SHA-256 校验值。
- 升级前备份工程与 `.keil-port-tool` 记录。不要删除 manifest 来绕过归属保护；也不要直接回滚覆盖后续业务修改。
- 源码与生成驱动采用 MIT；第三方组件仍适用各自许可证。发行资产不含私有测试工程、个人路径日志或第三方 SDK 包。

指南：[驱动与引用移除](DRIVERS.zh-CN.md) · [CubeMX 共存](CUBEMX.zh-CN.md) · [SPL](SPL.zh-CN.md) · [硬件矩阵](HARDWARE-MATRIX.zh-CN.md)

## English

This is a **feature prerelease**, not certification of all chips, drivers or hardware combinations.
RC6 remains available. Preview and validate on a project copy before upgrading production work.

### Changes

- Target-specific file/include reference removal with preview and rollback; physical source files are retained.
- Original drivers for W25Q128JV, 24LC02B, 24LC256, SHT3x-DIS, BH1750FVI, ADS1115 and SSD1306.
  Select software I2C, blocking hardware or generic DMA completion adapters.
- Initial STM32F4 HAL/SPL automatic blocking-I2C/SPI and software-I2C APIs after explicit bus/pin selection.
  Existing hardware initialization and explicit application calls remain necessary.
- Read-only project health checks, CubeMX ownership/conflict checks, private KPS source layout,
  legacy isolation and explicit post-regeneration recovery; no `.ioc` rewriting or silent ownership takeover.
- SPL startup/component adapters, precise shared-loop patch ownership, DSP dependency checks and deep Windows paths.
- Bilingual guides/UI; fixed regeneration after rollback leaves an empty directory, and frozen CLI error-dialog hangs.

### Evidence and limits

The local 32-script regression passed, including GUI callbacks, transactions, protocol execution and Keil checks.
Generated protocols/board APIs have host mock tests; F4 HAL/SPL APIs compile against actual SDK headers with AC5.
**New device drivers have no physical acceptance or DMA performance measurements. Automatic DMA and other
STM32 families are not covered.** Use explicit generic ports for unsupported combinations.
Existing F407/AC5 middleware HIL results are separately scoped in the hardware matrix/SPL guide and do not
certify the new sensor drivers. USB Host VBUS software-off remains a failed hardware item.
Clean Windows without Python/Git has not been tested; reduced PATH is not a substitute. The EXE is unsigned.
Hosted CI skips checks requiring unavailable commercial compilers/private board fixtures; CI is not hardware acceptance.

Extract the entire Windows ZIP and keep `_internal` beside the EXE. The source ZIP includes `kps_core/`, tests
and bilingual documentation. `SHA256SUMS.txt` verifies both archives. Back up projects and transaction records;
do not remove the manifest to bypass ownership checks or roll back over later application changes.
The tool and generated original drivers use MIT; upstream components retain their own licenses. Assets exclude
private projects, personal-path logs and third-party SDK archives.

Guides: [drivers/removal](DRIVERS.en.md) · [CubeMX](CUBEMX.en.md) · [SPL](SPL.en.md) · [hardware matrix](HARDWARE-MATRIX.en.md)
