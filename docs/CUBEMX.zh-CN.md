# 与 CubeMX 共存：单一管理方与重新生成检查

[English](CUBEMX.en.md)

这是 `2.3.0-dev1` 源码新增的保护，不在公开 RC6 EXE 中。
工具不能阻止你在 CubeMX 中直接覆盖磁盘文件，也不会自动回写 `.ioc`。
保护分三层：**独立源码目录、保留区内接入、生成后的归属检查与显式恢复**。

## 为什么只加引脚也会丢 FreeRTOS

2026-09-25 用本机 CubeMX 实际复现：IOC 没有开启 FREERTOS，工具装入内核后编译通过；
只新增 PA0 输入并重新生成，CubeMX 删除了 `Middlewares` 目录，但 Keil 中还有原引用。
同时 `stm32f4xx_it.c` 的 SVC/PendSV 函数外保护被覆盖。不是仅仅文件名重名。

开发版的新 CubeMX 移植使用 `KPS/ThirdParty/<组件>` 存库源码，FreeRTOS 任务文件使用
`KPS/FreeRTOS/App/freertos_app.c/.h`。不要去共享 SDK 里修改任务。
新的 FreeRTOS 异常接管别名位于中断文件的 `USER CODE Includes` 区；仅给已确认空的
CubeMX 异常壳改名，真正的异常向量仍指向 FreeRTOS 汇编端口。不要在这些空壳里添加业务。

已安装旧版本的工程**不会被普通移植动作悄悄搬目录**。需要先按下文隔离迁移。

## 一个组件只选一个管理方

| 组件 | CubeMX 管理 | 本工具管理 |
| --- | --- | --- |
| FreeRTOS | 保留 CubeMX 的配置、任务文件和内核；不要再勾工具的 FreeRTOS/RT-Thread | `.ioc` 不启用 FREERTOS；由工具生成任务和调度入口 |
| FatFS / LwIP | 保留 CubeMX 核心、配置和驱动；不要重复安装同一组件 | `.ioc` 不启用对应中间件；工具复制私有组件，板级驱动另行接入 |
| USB | ST USB 库已有控制器归属 | 安装 TinyUSB 前明确控制器、中断及 VBUS 归属；不能仅凭库名字不同认为能共用 |
| LVGL / LittleFS / RTT 等 | 外设初始化可继续由 CubeMX 生成 | 工具管理中间件源码，硬件驱动仍按板适配 |

检查包括 IOC `Mcu.IPn` 中的 FREERTOS/FATFS/LWIP/USB_DEVICE/USB_HOST，以及工程实际启用的
常见组件核心/初始化文件。关掉 IOC 选项但没有清理或重新生成旧文件，仍可能被拦截。
这不是完整 Pack 管理或 C 依赖解析；改名、自定义封装及分离双 USB 控制器需要人工核对。

## 推荐操作顺序

1. 在工程副本或干净的 Git 提交上操作，先用 CubeMX 配好时钟、引脚和外设。
2. 明确组件由谁管理，生成并验证裸机/已有基础工程。
3. 关闭 Keil，打开本工具，预览后移植；完成移植后任务、显示、存储等实板验证。
4. 需要再次用 CubeMX 生成时，**先提交或备份工程和 `.keil-port-tool` 状态目录**。
   旧组件仍在 `Middlewares/Third_Party` 时，先点击「工程工具 → CubeMX 生成前隔离旧组件…」。
   该操作复制当前工程内的源码（包括用户修改），不重新下载原版；旧库目录保留为未引用副本，
   旧 FreeRTOS 任务文件保留为 `.kps_migrated_bak`。可在安全与恢复里回滚。
   `Keep User Code` 应启用，但它不是完整保护：中断函数外的保护、工程 XML、宏和路径仍可能改变。
5. 生成后暂不烧录，打开「工程工具 → 工程体检（只读）」或运行：

```powershell
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --doctor
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --doctor-json .\regen-check.json
```

6. 若丢了已记录的引用、Include、宏或可唯一定位的接入段，点击
   「工程工具 → CubeMX 重新生成后恢复接入…」，检查差异再确认。
   **它不恢复旧的整份 main.c 或工程 XML，因此不撤销新引脚/外设配置。**
   恢复后再体检、编译和实测；已经完整时不重复写入。
   不要为消除提示删除 manifest，也不要直接重复移植来覆盖用户更改。

```powershell
# 生成前：仅针对完整的旧安装；--dry-run 可预览
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --cubemx-protect --dry-run
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --cubemx-protect
# 生成后：独立操作，不与移植参数混用
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --cubemx-recover --dry-run
python keil_port_tool.py .\MDK-ARM\MyProject.uvprojx --cubemx-recover
```

接入段被用户改写、宏值冲突、文件被禁用、Target 改名或定位不唯一时停止，不猜测覆盖。
**如果旧 `Middlewares` 或任务源码已经被删，先从生成前的 Git/完整备份找回，再做隔离。**
安装时保存的原版 SDK 不能证明包含用户后续修改，工具不会拿旧模板假装恢复成功。
多 Target 必须全部选中；复制和恢复支持事务回滚，但不是 CubeMX 本身的撤销功能。

`.keil-port-tool` 可能包含本机 SDK 路径，通常被 `.gitignore` 忽略，应保留在本地完整备份中，
不要为备份方便直接把它强制上传公开仓库。仅提交 Git 不一定包含这些恢复记录。

## 提示含义

- `CUBEMX_MIDDLEWARE_OWNER`：IOC 和本工具打算管理同一中间件，先选择管理方。
- `FOREIGN_MIDDLEWARE_OWNER`：工程已有非本工具登记的中间件核心或入口。
- `OWNED_STARTUP_DRIFT`：工具记录的源码补丁缺失或被改动，可能是重新生成，也可能是手工修改。
- `INSTALLED_REFERENCE_LOST / INSTALLED_INCLUDE_LOST / INSTALLED_DEFINE_LOST`：登记的文件引用、
  Include 或宏被删除、禁用或修改。
- `GENERATED_FILE_LOST / INSTALLED_SOURCE_LOST`：生成文件或已登记源码缺失。
- `CUBEMX_LEGACY_DIRECTORY`：旧组件还在 CubeMX 可清理的目录，生成前先隔离。
- `CUBEMX_USER_CODE_NOT_PRESERVED`：IOC 明确关闭用户代码保留。
- `OWNERSHIP_RECORD_INVALID`：状态记录损坏、路径不合法或无法完整读取，停止猜测。

组件/工程设置操作在这些错误出现时，会在下载、复制和写入之前停止。
单纯文件添加保持独立；但不意味着已有中间件冲突已解决。
合法修改任务业务不会仅因整个文件哈希变化就被拒绝；重点核对原来记录的补丁区域和工程条目。
体检不写文件；隔离与恢复必须分别预览确认，不会自动撤销其他正确更改。

## 已完成的实际验证

- 原生 FreeRTOS 与 CMSIS-V2：真实 CubeMX 生成 → 移植 → 修改任务文件 → 加 PA0 再生成。
  每套 `KPS` 的 707 个文件逐一哈希相同，用户任务修改和新 GPIO 均保留。
- 两套生成前/后均用 AC5 编译，0 错误、0 警告；生成后的接入归属检查无错误。
- F407 实板各读取 10 个连续 RAM 采样，约 22 秒：任务、RTOS tick、HAL tick 持续递增，
  速率约 1000/s，HAL 与 RTOS 相差最多 1 tick。烧录均校验并复位，之后恢复已确认的 SPL 屏幕固件。
- 自动回归覆盖隔离/恢复的预览取消、幂等、异常回滚、用户源码保留、冲突拒绝及 GUI 按钮后端。
- 旧布局副本也经过真实 CubeMX 验证：先隔离源码、重新生成、恢复丢失的旧异常保护，
  再 AC5 全量编译，0 错误、0 警告；没有恢复旧整份工程来撤销新 GPIO。

- RT-Thread 5.2.2 也经过真实 CubeMX 增加 PA0 并重新生成：127 个独立源码/配置/任务文件
  哈希不变，用户任务修改保留。重新生成会移除旧样式的 PendSV/HardFault 条件保护，
  工具检测到接入漂移；明确执行“CubeMX 生成后恢复”后，新 GPIO 保留、接入检查通过。
  生成前和恢复后全量 AC5 编译均 0 错误、2 个上游警告。
- 该 RT-Thread 恢复固件已烧录校验并复位：10 个连续 RAM 采样约 22 秒，默认任务心跳
  约 1 次/秒，RTOS/HAL tick 约 1000 次/秒、相差不超过 1 tick，断言行号为 0。
  随后校验恢复此前已确认的 SPL 显示固件，并复位。没有存储/网络/USB 操作。

RT-Thread 当前仍需在 CubeMX 重新生成后检查并显式恢复异常保护；不是无感双向同步。
这些结果限定于上述 F407/AC5/FreeRTOS/RT-Thread 组合，不代表所有组件、CubeMX 版本和布局。

## 尚未承诺的能力

不提供 `.ioc` 双向同步、CubeMX 插件、生成后自动执行脚本、任意用户代码的三方自动合并。
冲突报告不能保证所有自定义工程都能识别。模拟覆盖的回归用例与实际 CubeMX 生成测试应分开记录，
不能把测试夹具伪造的覆盖称为运行过 CubeMX。
