---
name: Hardware result / 实板验证反馈
about: Record exact tested configuration, including failures / 记录具体配置及失败
title: ''
labels: ''
assignees: ''
---

## Configuration / 配置

- Tool version or commit / 工具版本或提交:
- Board/revision, MCU / 板卡版本与芯片:
- SDK/compiler/RTOS/configuration:
- Wiring, clock and single-source supply / 接线、时钟、单路供电:

## Checks actually performed / 实际执行

- [ ] Generation / 工程生成
- [ ] Compilation / 编译
- [ ] Flash + reset / 烧录与复位
- [ ] Runtime data correctness / 运行数据校验
- [ ] Reset retention / 复位保留
- [ ] Complete power-loss retention / 真正断电保留

## Passes, failures and warnings / 通过、失败与警告

## Redacted evidence and reproduction / 脱敏证据及复现

Do not infer other board combinations passed. Confirm media-write authorization;
do not parallel power supplies or attempt unfamiliar live component probing.
不得外推其他硬件组合通过；确认介质写入授权，不并接电源，不做不熟悉的带电测量。
