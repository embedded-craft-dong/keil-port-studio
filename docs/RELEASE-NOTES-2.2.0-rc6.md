# Keil Port Studio 2.2.0-rc6 — CubeMX/HAL

## 中文

CubeMX/HAL 工程适配预发布版。建议先在工程副本上使用；标准库（SPL）及自定义目录
尚未完成完整自动移植验收，见[适配范围](SUPPORT.zh-CN.md)。工具 MIT，第三方保留各自许可证。

### 修复首次云端 CI 发现的问题

- 英文 Windows 的 CP1252 重定向输出无法编码中文日志时，改用转义输出，GUI 日志保留原文；
  日志不再因此中断复制、备份和工程修改。新增严格 CP1252 回归。
- 已勾选文件使用 Windows 8.3 短路径时，先解析到与扫描结果相同的真实路径，避免漏加文件。
  新增真实 Windows 短路径回归。
- 设置页内容能够完全显示时，不再错误要求必须出现滚动；仍检查末尾控件可见。
  高 DPI 布局测试在物理桌面尺寸不足时明确跳过，不能将其当作该缩放比例已验收。

RC5 第一次 GitHub Actions 失败记录保留，RC5 Release 仍为草稿，不作为推荐下载。
本版没有改变中间件 C 模板，未重新烧录历史硬件组合；硬件证据继续以
[矩阵](HARDWARE-MATRIX.zh-CN.md)记录的版本、配置和板卡为准。

### 附件与限制

- 下载完整 `KeilPortStudio-Windows-x64.zip`，解压后保留 `_internal`；无需用户安装 Python。
- 开发者可克隆仓库或下载 `KeilPortStudio-source.zip`，保留 `kps_core/`。
- 对照 `SHA256SUMS.txt` 校验，当前验收详情见 Release 附件 `VALIDATION.json`。
- 没有附带完整中间件离线包或 Keil 编译器；`.ioc` 不回写，CubeMX 再生成后需合并与复测。
- 测试板 USB3 VBUS 软件关断实测未通过，原因未定位；普通 U 盘读写和真实断电保留是独立的已通过项。
- EXE 未签名；无 Python/Git 的干净 Windows 验收未完成。云端 Python 测试不能代替这项验收或实板测试。
- 完整 MDK6 Solution/RTE 管理不支持，RT-Thread 限 Cortex-M4 内核范围，部分底层诊断仍是中文。
- 板级驱动、时基、中断和电源仍需按[移植后指导](POST-PORTING.zh-CN.md)实现与验证。

## English

CubeMX/HAL adaptation prerelease. Start with a project copy. Complete automatic SPL/custom-layout
acceptance remains out of scope; see [support boundaries](SUPPORT.en.md). Tool source is MIT; third-party terms remain separate.

### Fixes from the first hosted CI run

- Unencodable Chinese logs on CP1252 redirected Windows consoles now use escaped console output,
  retaining original text in the GUI sink instead of aborting operations. Adds a strict CP1252 regression.
- Resolve selected Windows 8.3 aliases consistently with scanned paths so checked files are not missed.
  Adds a real short-path regression on Windows.
- Settings content that fits its viewport need not scroll; the final controls must still be visible.
  High-DPI layout checks explicitly skip physically undersized desktops; that DPI is not certified by a skip.

The failed initial RC5 Actions run is retained; the RC5 Release remains a draft, not a recommended download.
No middleware C templates changed in RC6 and historical hardware profiles were not reflashed.
Use the [matrix](HARDWARE-MATRIX.en.md) for exact firmware/board evidence.

### Downloads and limitations

Extract the complete Windows ZIP, keeping `_internal`. Source users keep `kps_core/`.
Verify `SHA256SUMS.txt`; release-time checks are in attached `VALIDATION.json`.
No complete offline SDK bundle or Keil compiler is included. `.ioc` is not updated; reconcile and retest after regeneration.
USB3 software VBUS shutoff failed on the test board with unresolved cause, independently of passing I/O and actual power-loss retention.
The EXE is unsigned. Clean Windows without Python/Git is untested; hosted Python tests are not that acceptance or hardware tests.
Full MDK6 Solution/RTE management is unsupported; RT-Thread targets a Cortex-M4 kernel. Some diagnostics remain Chinese.
Drivers, clocks, interrupts and power need board-specific work; follow the [post-porting guide](POST-PORTING.en.md).
