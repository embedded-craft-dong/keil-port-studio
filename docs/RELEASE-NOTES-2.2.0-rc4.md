# Keil Port Studio 2.2.0-rc4

## 中文

发布候选版，建议先在工程副本上使用。工具代码 MIT，第三方组件遵循各自许可证。
这不是所有芯片、板卡、版本及组件组合的全面认证。

### 本版变更

- 修复 TinyUSB RTOS 初始化顺序：硬件及 USB 栈在运行中的专用任务内初始化。
- 按角色选择 SDK：AC5 Device 默认 0.17、Host 默认 0.18；补齐实际控制器源码检查和真实毫秒时基。
- 修复已复现的 AC5 DWC2 寄存器访问、重复弹出 RX 状态及 Host FIFO 分配问题。
- 保留启动补丁结果检查、按归属卸载、XML 写入校验、GUI 后台操作、中英文指南及 Git 面板。
- 发布包使用统一源码白名单，检查版本、文档链接和明显敏感信息模式；记录源码/文档 SHA256。

### 已验证范围

- 本轮 19 个软件回归脚本通过，包括真实 Tk 控件与文件操作、临时本地 Git 远端、组件生成与安全恢复。
- 普中 F407 板：裸机/FreeRTOS/RT-Thread 的 USB Host + FatFS 读写及只读复核；
  用户实际断电后的数据保留；AC6 Host 枚举/首扇区读取。
- 改后模板的 FreeRTOS/RT-Thread USB Device：CDC 字节一致性、RAM MSC、并发测试；
  HID 只验枚举，不声称实际键盘输入通过。
- 更多存储、网络、显示和内核测试及其具体配置见[硬件矩阵](HARDWARE-MATRIX.zh-CN.md)。
  历史实板结果只覆盖其记录的固件，不代表每次发布重新烧录了全部历史组合。

### 已知限制

- 本测试板 GPIO 控制 USB3 VBUS 关断实测未通过，原因未定位，停止进一步元件级排查。
  这与已通过的 U 盘文件读写和真实断电保留是独立项目。
- 无 Python/Git 的干净 Windows 验收未完成；安装 Git 的真实干净环境流程未验证。
- EXE 未签名，GitHub 云端 CI 在实际上传运行前不宣称通过。
- 完整 MDK6 CMSIS Solution/RTE/CubeMX `.ioc` 协同不支持；RT-Thread 当前为 Cortex-M4 内核范围。
- 英文 GUI 可切换，部分底层诊断仍为中文。下载可受网络/代理影响；使用本地 SDK 也可移植。
- 板级驱动、USB 电源/中断、DMA/缓存等需适配；详见[移植后指导](POST-PORTING.zh-CN.md)。

### 下载与校验

Windows 用户下载 `KeilPortStudio-Windows-x64.zip`，完整解压后运行 EXE，保留 `_internal`。
开发者使用仓库源码或 `KeilPortStudio-source.zip`；请保留 `kps_core/`。
对照同一 Release 的 `SHA256SUMS.txt` 校验 ZIP。不要把旧 RC3 包当成 RC4。

## English

Release candidate: start with a project copy. The tool is MIT; imported middleware
retains its own license. This is not certification of every board/version/combination.

### Changes

- Initialize RTOS USB hardware and stack inside the running owner task.
- Select SDK by role (AC5 Device 0.17 / Host 0.18), validate controller sources and supply real clocks.
- Fix reproduced AC5 DWC2 MMIO access, repeated RX-status pops and Host FIFO allocation.
- Retain checked startup patches, ownership-aware uninstall, XML validation, background GUI work,
  bilingual guides and Git management. Share the packaging allowlist with publication auditing;
  record source/document hashes and check versions, links and obvious sensitive-data patterns.

### Evidence and limitations

All 19 software regression scripts passed, covering real Tk/file operations, temporary local Git remotes and migration/recovery.
On the Puzhong F407, bare/FreeRTOS/RT-Thread Host+FatFS I/O and readback passed;
user-performed complete power loss retained test data. AC6 Host enumeration/sector reads passed.
Fresh FreeRTOS/RT-Thread Device templates passed exact CDC echo, RAM MSC and concurrency checks;
HID enumeration passed, not actual key input. See the [matrix](HARDWARE-MATRIX.en.md) for exact
configurations and historical firmware boundaries; not all historical combinations were reflashed for every release.

GPIO-controlled USB3 VBUS shutoff **failed** on this board, root cause unresolved; component-level
diagnosis has stopped. This is separate from passing file I/O and actual power-loss retention.
Clean Windows without Python/Git and real clean-machine Git installation remain untested.
The EXE is unsigned; hosted GitHub CI is unverified until it actually runs after publication.
Full MDK6 Solution/RTE/`.ioc` collaboration is unsupported; RT-Thread targets a Cortex-M4 kernel.
Some backend diagnostics remain Chinese. Networking can affect SDK downloads; local SDKs work too.
Board drivers, power, interrupts and DMA/cache need adaptation; use the [post-porting guide](POST-PORTING.en.md).

Extract the complete Windows ZIP, retaining `_internal`; developers retain `kps_core/` with source.
Check the archives against `SHA256SUMS.txt` from the same release. Frozen RC3 packages do not contain RC4 fixes.
