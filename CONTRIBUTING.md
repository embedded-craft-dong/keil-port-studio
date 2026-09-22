# Contributing / 参与贡献

中文和英文反馈都欢迎。工具源码采用 MIT；提交者应有权贡献这些修改，第三方许可证不能改标。
Chinese and English contributions are welcome. Contribute only code you may share;
the tool is MIT, while third-party material retains its original terms.

## 报告问题 / Report a problem

- 说明工具版本、源码/EXE、Windows/Python、Keil AC5/AC6、MCU、组件版本和选择的文件。
  Include tool/runtime/compiler/MCU/component versions and selected files.
- 提供最小复现、预期/实际结果，以及脱敏日志；编译失败和运行失败分别描述。
  Provide a minimal reproduction, expected/actual behavior and redacted logs.
- 不上传完整商业工程、密钥、设备 UID、用户路径或没有分发权限的 SDK。
  Do not attach proprietary projects, secrets, device IDs or unauthorized SDKs.
- 疑似安全漏洞不要在公开 Issue 中粘贴可利用细节或凭据；优先使用仓库的私密漏洞报告入口（若已启用）。
  For suspected vulnerabilities, use private reporting if enabled; do not post secrets publicly.

## 开发与测试 / Development and tests

```powershell
python tools/run_tests.py test-results
python tools/audit_publication.py
```

`test-results` 必须是新目录；Tk 测试需要图形桌面，Git 测试使用临时本地仓库。
Use a new output directory. Tk needs a desktop; Git tests use temporary local repositories.
硬件不是常规软件测试的先决条件；未满足条件的可选用例应明确标为 skipped。
Optional tests must report skips honestly, not infer hardware success.

可选真实编译通过环境变量开启：`KPS_REAL_BUILD_PROJECT`（独立测试副本）、`KPS_UV4`、
`ARMCC`、`FROMELF`、`LWIP_SDK`、`LITTLEFS_SDK`。不要把本机路径写进源码。
Enable optional real builds with these environment variables, using disposable project copies.
Do not commit machine-specific paths, generated SDKs or raw hardware archives.

## 硬件证据 / Hardware evidence

记录板卡版本、芯片、时钟、接线、单路供电、SDK/compiler、生成配置、测试源码版本及结果。
Record board revision, MCU, clocks, wiring, single-source power, SDK/compiler/configuration
and the exact tested source version. Separate compile, flash, enumeration, data correctness,
reset retention and actual power-loss tests. Report warnings and failures too.

不要为复现测试并接多个 5V 电源；写入前确认介质身份与授权。USB3 VBUS 关断是当前测试板
的已知未解决项；欢迎补充，但不要求贡献者拆焊或进行不熟悉的带电测量。
Never parallel supplies for testing. Confirm media identity and write authorization.
The recorded USB3 shutoff issue remains open; component repair is not required to contribute.

提交小而聚焦的改动，附回归测试；行为变化同时更新中英文教程与硬件验证边界。
Keep changes focused, add regression coverage and update both language guides when behavior changes.
