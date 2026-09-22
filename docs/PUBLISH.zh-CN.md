# 首次上传 GitHub

[English](PUBLISH.en.md)

建议先发布 `v2.2.0-rc6` CubeMX/HAL 适配预发布版：源码进仓库，完整便携 ZIP 进 Releases。
不要上传整个本地工作目录，更不要上传原始实板工程、SDK 或 `hardware_tests/`。

## 1. 上传前

- 确认 GitHub 账号、仓库名、公开可见性和 MIT 署名。当前署名是 `Keil Port Studio contributors`。
- 运行 `python tools/audit_publication.py` 和回归测试；检查待提交内容和历史，扫描器不是完整泄密检测。
- 如公开已有历史，检查 Git 提交作者/邮箱是否愿意公开；使用 GitHub 提供的 noreply 邮箱也不会自动修改旧历史。
- 阅读[RC6 发布说明](RELEASE-NOTES-2.2.0-rc6.md)和[适配范围](SUPPORT.zh-CN.md)，保留失败与未验证项，不能把未通过改成未测试。
- 构建及校验方法见[发布指南](RELEASE.zh-CN.md)。旧包保留，源码改变后必须重新构建。

## 2. 发布源码

在 GitHub 创建空仓库；已有本地历史时，不要同时让远端初始化另一份 README/许可证。
在本工具 Git 面板选择本工具源码目录，查看状态，勾选应公开文件，暂存、检查差异后提交。
远端地址使用刚创建仓库的真实 HTTPS/SSH 地址，确认后推送；不要 force push。
Git 登录由用户在可信的 Git/GitHub 认证流程中完成，不能把 token 填进远端 URL 或工程配置。

应公开：`keil_port_tool.py`、`kps_core/`、`docs/`、`tests/`、`tools/`、`.github/`、
`.gitignore`、`README*.md`、`CONTRIBUTING.md`、`LICENSE`、`THIRD-PARTY-NOTICES.md`、`requirements-build.txt`。
提交前检查 Git 暂存列表；`.gitignore` 不会自动去除过去已跟踪的文件。
不要公开：`releases/`、构建缓存、日志、虚拟环境、用户设置、凭据、SDK、原始硬件档案。

## 3. 建立预发布版

打开仓库的 Releases，创建草稿；标签用 `v2.2.0-rc6`，Target 选择与构建源码一致的提交。
标题建议为 `Keil Port Studio 2.2.0-rc6 — CubeMX/HAL`。
正文使用 RC6 发布说明，勾选 pre-release，附加以下同一次构建的文件并最终确认发布：

- `KeilPortStudio-Windows-x64.zip`
- `KeilPortStudio-source.zip`
- `SHA256SUMS.txt`

先保存草稿可在公开前复核附件、正文与标签。操作入口及预发布选项见
[GitHub 官方发布说明](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)。
大二进制作为 Release 附件而非反复提交 Git；相关限制见
[GitHub 文件大小说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)。

## 4. 上传后

检查 Actions 实际结果，失败则修复或保留草稿，不要用本地通过冒充云端通过。
从公开 Release 重新下载、核对 SHA256 并完整解压试用；验证 README 文档链接。
后续在新版本中修复问题，不悄悄替换同一版本附件。欢迎贡献者通过 Issue 模板提供脱敏复现和实板证据。

当前文档是操作指导，本工具不会自动创建仓库、提交、推送或发布。
