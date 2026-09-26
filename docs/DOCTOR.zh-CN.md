# 工程体检（只读，开发版）

[English](DOCTOR.en.md)

从 `2.3.0-dev1` 源码开始提供；已发布的 RC6 EXE 不包含本功能。

## GUI

选择工程与 Target，打开底部「工程工具」→「工程体检（只读）」。
扫描在后台执行，结果可滚动阅读，也可导出 JSON。检查本身不修改工程、源码、IOC、
事务记录或板上固件，不下载 SDK、不启动编译。只有明确选择导出时才新建报告文件，
已有文件不会被覆盖；需要选择新文件名。

## CLI

```powershell
python keil_port_tool.py Board.uvprojx --doctor --target Debug
python keil_port_tool.py Board.uvprojx --doctor-json health.json
python keil_port_tool.py Board.uvprojx --doctor --doctor-ioc Board.ioc --doctor-language en
```

`--target` 可重复；未指定时分别检查全部 Target，不把两个 Target 的引脚配置混在一起。
体检不能与移植、工程修改、卸载、回滚、其他导出或编译同次执行。
`--dry-run` 不可与会创建文件的 `--doctor-json` 同用。
正常生成报告时 CLI 返回 0，**不表示工程通过验收**；自动化应读取 JSON 的 `summary` 和 `issues`。

## 第一版检查范围

- 工程中的失效文件引用、同一 Target 重复文件。
- Target 级 Include 目录缺失或含未解析变量。
- 已启用、被工程引用的 C/C++ 文件中，显式 HAL GPIO 复用初始化和 SPL `GPIO_PinAFConfig`。
- 工程目录中的 IOC；常见 MDK 子目录还检查工程根目录。多个 IOC 时不猜测，CLI 可显式指定。
- 文件与组的 `IncludeInBuild=0` 不参与扫描；支持现有工程解析器的 XML 命名空间和 GBK 源码。

例如 PD5 同时出现 UART2 与 FSMC 配置，会给出 `PIN_AF_CONFLICT`，列出配置来源、
行号、外设及 Target。这用于提示检查屏幕写控制与串口是否争用同一引脚；
**不自动改线、不修改 IOC、不生成替代引脚方案**。

## 如何判断结果

- `error`：明确缺失的工程引用，需要处理；体检不会自行拦截其他独立操作。
- `warning`：候选引脚冲突、重复引用、缺失 Include 或扫描遗漏，需要确认。
- `info`：路径变量无法解析、多个 IOC 等范围提示。

候选引脚冲突不是“两个外设一定同时运行”：初始化函数可能未调用、位于不同条件编译分支，
或 IOC 已过期。须对照实际启动流程确认。相同外设重复配置不作为不同外设冲突。

本版不做完整 C 预处理、调用图、头文件内初始化、复杂宏/变量别名、直接寄存器操作、
外部静态库、文件级 Include 覆盖或真实接线分析；仅排除可明确识别的字面量 `#if 0/1` 分支。
单文件超过 2 MiB、源码合计超过 32 MiB 或超过 1500 个唯一源码文件时停止相应读取，并报告遗漏。
没有提示不代表引脚一定正确，也不代表编译、调度器、内存或硬件运行已通过。

报告含工程路径和源码位置。对外分享前请脱敏，不提交完整私有工程。
