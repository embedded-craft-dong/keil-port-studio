# Windows 构建与验证

[English](RELEASE.en.md)

当前 `2.3.0-dev1` 预发布版提供源码和 Windows 包，新增工程体检、CubeMX 共存保护、SPL 适配、引用移除和器件驱动生成。见[本次发布说明](RELEASE-NOTES-2.3.0-dev1.md)。
已发布版本 `2.2.0-rc6` 是 CubeMX/HAL 适配预发布版，不宣称所有组件/芯片/组合已验证。
RC6 修复首次云端 CI 暴露的控制台编码与 Windows 短路径选择问题；见[RC6 说明](RELEASE-NOTES-2.2.0-rc6.md)
和[适配范围](SUPPORT.zh-CN.md)。历史修复见[可靠性说明](RELIABILITY.zh-CN.md)
与[RC4 发布说明](RELEASE-NOTES-2.2.0-rc4.md)；具体实板结果见
[硬件验证矩阵](HARDWARE-MATRIX.zh-CN.md)。本页供需要自行构建或维护项目的开发者使用。

## 构建 Windows 包

在 Windows 上使用独立虚拟环境。当前本机构建环境是 Python 3.14 x64；源码声明的
Python 3.8+ 与打包依赖所需 Python 版本不是同一概念。打包器版本已固定，但这并非
可逐字节复现的构建保证。

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python tools\build_release.py releases\2.3.0-dev1
.venv\Scripts\python tools\verify_release.py releases\2.3.0-dev1
```

输出目录必须不存在，以免覆盖以前的发布。生成：

- `app/KeilPortStudio/KeilPortStudio.exe` 与依赖目录；
- `KeilPortStudio-Windows-x64.zip`（完整便携程序）；
- `KeilPortStudio-source.zip`（白名单源码、测试、文档、构建脚本）；
- `SHA256SUMS.txt` 和程序内 `BUILD-INFO.json`。

源码包使用白名单收集文件，不包含原始硬件档案、测试工程、SDK 和构建缓存。
白名单与自动扫描不能保证内容不存在隐私或凭据，仍需审查实际打包内容。
发布前仍须人工检查 Git 暂存区；ignore 不会移除以前已经跟踪的文件。

便携包没有签名/安装器，不要求管理员权限。Git 是可选外部程序，不打入 EXE。
桌面程序的诊断日志保存在 `%APPDATA%/KeilPortStudio/logs`，会包含工程路径和输出；
分享日志前脱敏，并按需清理。项目事务备份不等于 MCU Flash/SD 数据备份。

开发版在源码树复制、哈希与事务目录备份/清理的文件 I/O 边界使用 Windows 扩展路径，
用于处理 SDK 内超过 260 字符的深层文件，不要求修改系统长路径策略；扩展前缀不会写入
Keil 工程引用或清单。仍建议把工程放在较短路径：这不保证旧 Keil、编译器和其他外部
工具支持任意长路径，也不代表本工具的所有路径操作都已完成超长路径验收。

## 验证要求

1. 运行 `python tools/audit_publication.py`，检查白名单内容、常见隐私模式及文档链接；人工复核提交差异与压缩包。
2. 运行 `tests/test_*.py` 回归脚本。Tk 测试需要图形桌面，Git 测试需要 Git；真实 Keil 编译需显式启用。
3. 用 `verify_release.py` 校验构建产物，并实际解压启动 EXE，检查文件添加、预览、导出和恢复。
4. 保留第三方许可证，记录构建环境、源码版本、校验值和测试范围；不要将第三方代码改标 MIT。

[RC6 云端 CI 记录](https://github.com/embedded-craft-dong/keil-port-studio/actions/runs/35721027566)
覆盖自动化回归，不替代硬件与干净环境验收。无 Python/Git 的干净 Windows 验收仍未完成，
仅修改 PATH 不能代替。测试板 USB Host VBUS 软件关断实测未通过；
普通 U 盘读写及实际断电保留通过是独立结论。不得据此宣称所有硬件组合通过。
