# 发布候选版与构建

[English](RELEASE.en.md)

当前版本 `2.2.0-rc6` 是 CubeMX/HAL 适配预发布版，不宣称所有组件/芯片/组合已验证。
RC6 修复首次云端 CI 暴露的控制台编码与 Windows 短路径选择问题；见[RC6 说明](RELEASE-NOTES-2.2.0-rc6.md)
和[适配范围](SUPPORT.zh-CN.md)。以下为继承的功能修复与历史验证记录。
本版包含 RC3 冻结后完成的 USB Host、RTOS USB 启动修复及独立 Device 回归；
旧 RC3 压缩包未被覆盖，不包含这些后续修复。完整变更与限制见
[RC4 发布说明](RELEASE-NOTES-2.2.0-rc4.md)。
本轮加固启动补丁、源码卸载归属、XML 写入自检与后台任务，并开始拆出纯核心模块。
新生成 FreeRTOS/CMSIS-V2 与 RT-Thread 内核均已烧录、校验、复位实测；
具体范围见[可靠性说明](RELIABILITY.zh-CN.md)。既有硬件证据另包含特定存储、USB、网络、显示测试，
其中本轮显示画面已由用户确认；新增存储/USB/网络最终断言配置复测见
[硬件验证矩阵](HARDWARE-MATRIX.zh-CN.md)。
不要将旧配置的测试结果当作所有发布配置的认证。

另修复 TinyUSB/LwIP FreeRTOS 初始化中的断言副作用：任务创建不再藏在
`configASSERT` 中。以宿主机 API 替身做了开/关断言的 C 编译运行验证，并以这次
模板修正重新生成、烧录 USB/网络测试并通过本板所列项目。此处结果只覆盖已列配置，
USB Host 的裸机/FreeRTOS/RT-Thread U 盘文件读写与断电保留已有实测；
本板 VBUS 软件关断未通过且停止元件级排查，不能将其他 MCU 或外围组合推断为通过。

## 构建 Windows 包

在 Windows 上使用独立虚拟环境。当前本机构建环境是 Python 3.14 x64；源码声明的
Python 3.8+ 与打包依赖所需 Python 版本不是同一概念。打包器版本已固定，但这并非
可逐字节复现的构建保证。

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python tools\build_release.py releases\2.2.0-rc6
.venv\Scripts\python tools\verify_release.py releases\2.2.0-rc6
```

输出目录必须不存在，以免覆盖以前的发布。生成：

- `app/KeilPortStudio/KeilPortStudio.exe` 与依赖目录；
- `KeilPortStudio-Windows-x64.zip`（完整便携程序）；
- `KeilPortStudio-source.zip`（白名单源码、测试、文档、构建脚本）；
- `SHA256SUMS.txt` 和程序内 `BUILD-INFO.json`。

源码包不包括原始硬件档案、用户名路径、测试工程、SDK、私有凭据和构建缓存。
`hardware_tests/` 的本地原始证据保留在磁盘，但通过 `.gitignore` 排除。
发布前仍须人工检查 Git 暂存区；ignore 不会移除以前已经跟踪的文件。

便携包没有签名/安装器，不要求管理员权限。Git 是可选外部程序，不打入 EXE。
桌面程序的诊断日志保存在 `%APPDATA%/KeilPortStudio/logs`，会包含工程路径和输出；
分享日志前脱敏，并按需清理。项目事务备份不等于 MCU Flash/SD 数据备份。

## 上传 GitHub 前清单

1. 阅读 MIT，确认公开仓库名称、作者/版权署名、第三方来源；不要把第三方代码改标 MIT。
2. 运行全部测试，验证 ZIP 解压后的 EXE。无 Python/Git 的干净 Windows 验收仍待有条件的贡献者完成，不能以修改 PATH 冒充；本候选版须披露此限制。
3. 审查提交差异、密钥、真实设备标识和大文件，确认只公开计划公开的文件。
4. 源码进入仓库；EXE ZIP、源码 ZIP、校验值放 GitHub Releases。建议先标记 prerelease。
5. 发布说明列出已测与未测内容。首次 GitHub 登录、创建仓库/公开发布由用户确认。

目前提供构建与 CI 配置，不自动创建仓库、提交、推送或发布。
首次公开的具体步骤见[上传指南](PUBLISH.zh-CN.md)。
