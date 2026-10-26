# Keil Port Studio 2.3.0-dev3

## 中文

2026-10-26。本次在 dev2 基础上改进工程选择，不扩展芯片或硬件驱动的实测范围。

- Windows 启动时后台检测已打开的 Keil：只有一个窗口且完整工程路径可识别时默认选中。
- 多窗口提供下拉选择，展示工程名、路径、PID/窗口标识，支持同名工程和刷新。
- 选择时重新核对当前窗口，避免 Keil 已切换工程却仍沿用旧路径；不使用过时的启动命令或最近工程记录猜测。
- 不覆盖用户明确传入/手动选择的工程。切换工程清空旧文件预览、勾选和扫描目录，并重置 Target。
- 检测失败/超时仍可手动选择；不自动保存、关闭或重载 Keil。写入前请保存并关闭 Keil 中的工程。
- 保持小窗口下组件文件清单可见，更新中英文 GUI 教程。

验证：34 个本地回归脚本、19 项新测试通过；两个真实 Keil 窗口只读切换通过，工程哈希不变。
详细范围见 [GUI 测试记录](GUI-TEST-REPORT.zh-CN.md)。识别仅支持标题中完整路径对应的
`.uvprojx/.uvproj`，不意味着支持完整 `.uvmpw` / CMSIS Solution 工程管理。

下载 Windows ZIP 后请完整解压，保留 `_internal`；源码保留 `kps_core/`。
旧 dev2/dev1/RC6 保留供回退。本版仍是预发布版，未签名。
既有硬件限制、未完成的干净 Windows 验收保持不变，见 [适配范围](SUPPORT.zh-CN.md)。

## English

2026-10-26. This update improves project selection on top of dev2; it does not expand hardware validation claims.

- Read-only background discovery of open Keil windows on Windows. One recognized full project path is selected automatically; multiple windows use a dropdown with names, paths and process/window identifiers.
- Refresh support and selection-time revalidation prevent stale window paths. No guessing from MRU records or process launch arguments.
- Explicit/manual project choices are preserved. Switching projects resets Target, scan directory and old file previews/selections.
- Errors/timeouts retain manual selection. Keil is never automatically saved, closed or reloaded; save and close the project there before writing changes.
- Compact project controls preserve checklist visibility, with bilingual GUI documentation updated.

Validation: all 34 local regression scripts and 19 new cases passed. Read-only selection against two real Keil windows left project hashes unchanged. See the [GUI test report](GUI-TEST-REPORT.en.md).
Only full caption paths to `.uvprojx/.uvproj` are recognized; this is not full workspace/CMSIS Solution support.

Extract the complete Windows ZIP and keep `_internal`; source users must keep `kps_core/`.
Earlier dev2/dev1/RC6 releases remain available for rollback. This unsigned prerelease retains existing hardware and clean-Windows validation limitations; see [scope](SUPPORT.en.md).
