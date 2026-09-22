# Keil Port Studio 2.2.0-rc5 — CubeMX/HAL

## 中文

本版为 **CubeMX/HAL 工程适配预发布版**，建议在工程副本上使用。
相对 RC4，仅更新版本号和发布文档，不改变移植算法或硬件模板。

- 在中英文 README、界面教程、移植后教程中明确适配范围，新增[使用前检查](SUPPORT.zh-CN.md)。
- 标准库（SPL）及自定义目录工程不宣称完整自动移植支持；CubeMX 工程也不代表任意板卡或组合已验证。
- 不修改 `.ioc`；再次生成 CubeMX 代码后需要检查差异并重新测试。
- 既有功能修复、软件与硬件证据见 [RC4 记录](RELEASE-NOTES-2.2.0-rc4.md)及[硬件矩阵](HARDWARE-MATRIX.zh-CN.md)。
  RC5 没有重新烧录全部历史硬件组合；本次检查结果另见发布目录的 `VALIDATION.json`。

### 已知限制与附件

测试板 USB3 VBUS 软件关断实测失败、原因未定位；U 盘普通读写及实际断电保留另有通过记录。
干净 Windows 验收未完成，EXE 未签名，云端 CI 在上传运行前不能称为通过。
完整 MDK6 Solution/RTE 管理不支持，RT-Thread 限 Cortex-M4 内核范围；部分底层诊断仍为中文。
板级驱动和时基仍需适配，见[移植后指导](POST-PORTING.zh-CN.md)。

附件为 `KeilPortStudio-Windows-x64.zip`、`KeilPortStudio-source.zip` 和 `SHA256SUMS.txt`。
完整解压 Windows 包，保留 `_internal`；源码保留 `kps_core/`。请使用同一发布目录的附件并校验 SHA256。
当前不附带中间件离线包，不捆绑 Keil 编译器。工具 MIT，第三方代码遵循各自许可证。
旧 RC4 包保持不变，不包含本次范围说明。

## English

This is a **CubeMX/HAL project adaptation prerelease**. Start with a project copy.
Compared with RC4, only the version and publication documentation change; migration algorithms and hardware templates are unchanged.

- Bilingual READMEs, GUI and post-porting guides now state the scope; see [preflight checks](SUPPORT.en.md).
- Complete automatic SPL/custom-layout support is not claimed. CubeMX origin is not certification of every board or combination.
- `.ioc` is not updated. Review diffs and retest after CubeMX regeneration.
- Previous fixes and evidence remain in the [RC4 record](RELEASE-NOTES-2.2.0-rc4.md) and [hardware matrix](HARDWARE-MATRIX.en.md).
  Historical hardware combinations were not all reflashed for RC5. Current packaging/check results are recorded separately in release-directory `VALIDATION.json`.

USB3 VBUS software shutoff failed on the test board with unresolved cause; normal file I/O and physical power-loss retention passed separately.
Clean Windows acceptance is incomplete, the EXE is unsigned and hosted CI is unverified until it runs after upload.
Full MDK6 Solution/RTE management is unsupported; RT-Thread targets a Cortex-M4 kernel. Some backend diagnostics remain Chinese.
Drivers and clocks still require adaptation; see the [post-porting guide](POST-PORTING.en.md).

Assets: `KeilPortStudio-Windows-x64.zip`, `KeilPortStudio-source.zip`, `SHA256SUMS.txt`.
Extract the entire Windows archive, retaining `_internal`; keep `kps_core/` with source. Verify hashes from the same release.
No complete middleware offline bundle or Keil compiler is included. Tool source is MIT; third-party terms remain separate.
Frozen RC4 archives do not contain these scope clarifications.
