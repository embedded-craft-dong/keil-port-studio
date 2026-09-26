# 器件驱动生成与文件引用移除

[English](DRIVERS.en.md)

这是 `2.3.0-dev2` 源码与 Windows 预发布包的功能，旧 RC6 Windows 包没有这些入口。
工具新增协议驱动，不等于新增对应型号的实板验证。已有板卡中间件测试也不能用来证明这些新驱动。

## 移除文件和 Include 路径

1. 选择工程和 Target，关闭 Keil 中打开的工程。
2. 打开 **工程工具 → 移除文件与路径…**。
3. 输入路径、分组或 Target 文字进行筛选，逐条打勾，也可勾选筛选结果。默认不选任何条目。
4. 文件树引用与工程级 Include 是两种独立条目。移除一个文件不会猜测并自动删掉其头文件目录；
   Include 精确匹配，不递归移除子目录，不改文件级编译选项。
5. 点击 **预览移除…**，检查 Target 和 XML 差异，再确认。取消不会写入。
6. 重新加载 Keil 并编译。误操作可在 **安全与恢复 → 回滚最近事务** 恢复。

只移除引用，**不删除磁盘上的源码、头文件或静态库**。移除 Include 可能使仍保留的源码无法编译，
工具不会宣称能自动推断所有预处理依赖。筛选隐藏的已勾选条目仍在本次选择中，底部会显示总数。
工具管理的中间件/驱动包引用不允许零散删除，请通过“安全与恢复”卸载组件；普通添加文件允许单独移除，
并按 Target 更新归属清单，避免 CubeMX 恢复功能把刻意删除的条目加回来。

```powershell
python keil_port_tool.py Demo.uvprojx --target Debug --remove-file '..\User\old.c' --remove-include '..\OldInclude' --dry-run
python keil_port_tool.py Demo.uvprojx --target Debug --remove-file '..\User\old.c' --remove-include '..\OldInclude' --yes
```

参数可重复；相对路径以 `.uvprojx` 所在目录为基准，不是终端当前目录。没有匹配项会报错，不会按文件名模糊删除。
移除与安装/卸载/体检等操作分开执行。`--diff-file preview.txt` 可保存预览。

## 生成驱动

打开 **工程工具 → 器件驱动生成器…**，取消不需要的型号，选择 I²C/SPI 方式，再预览生成。
输出放在工程的 `KPS/DeviceDrivers/`，添加到所选 Keil Target；不更改 `main.c`、引脚、时钟或 `.ioc`。

### STM32 自动端口（新增）

当前源码检测到 STM32F1/F4 和明确的 `USE_HAL_DRIVER` / `USE_STDPERIPH_DRIVER` 时，界面默认选择 `auto`。
F1 支持从 `2.3.0-dev2` 起包含在源码和 Windows 包中；旧 dev1 EXE 不含该扩展。
首批自动端口支持 **HAL / 标准库的硬件 I²C、硬件 SPI、软件 I²C**，不是所有 STM32 系列通用。
一次选择一个 Target。检测到唯一的已有总线初始化会预选实例；多个实例需选择。
SPI 填低有效 CS（如 PB0），软件 I²C 填 SCL/SDA；不能凭芯片型号推断你的外部接线。
所选 GPIO 必须没有其他用途；PA13/PA14 调试脚拒绝自动重配。
F1 额外保留 PA15/PB3/PB4（JTAG），仅接受 A–G 端口；还需核对具体封装是否引出该脚。
F1 标准库使用 APB2 GPIO 时钟、`GPIO_Mode_Out_PP/Out_OD`，兼容旧 CMSIS 的 DWT 定义缺失。
不改 AFIO 重映射或硬件总线初始化：已有重映射后的 I²C/SPI 仍通过原实例访问。
需要使用 JTAG 引脚时，请自行确认调试配置并选 generic，不会代替用户关闭调试接口。

自动端口不再留总线 API / GPIO / 延时 / 锁的 TODO：

1. 保留并执行工程原有时钟、硬件 I²C/SPI 初始化。软件 I²C 需外部上拉。
2. 包含 `kps_stm32_port.h`，调用 `kps_stm32_init()` 并检查返回值。该调用初始化所选 CS/软件 I²C GPIO，
   启用 DWT 延时，不重设 SysTick，不重置已有 DWT 计数器。
3. `kps_bus bus = kps_stm32_bus();`，再调用所选器件函数，检查返回值；不会自动擦写存储。

自动端口仅允许任务/主循环调用且要求中断已开启；并发调用会返回错误而不是交错事务，外部总线使用者也需统一调度。
DWT 延时是阻塞等待，不是 RTOS 睡眠。**自动 DMA 端口尚未实现和验证，选择时会明确拒绝，不会静默降级**；
DMA 仍可选 `generic` 框架并手工接入。其他 STM32 系列和无法识别的库也需明确选择 `generic`。
这里的“自动填 API”不等于已验证实际接线/电气/传感器；仍需按生成指南验收。

```powershell
python keil_port_tool.py Demo.uvprojx --target Debug --driver sht3x --driver-port auto --driver-i2c-instance hi2c1 --yes
python keil_port_tool.py Demo.uvprojx --target Debug --driver w25q128jv --driver-port auto --driver-spi-instance SPI1 --driver-cs PB0 --yes
python keil_port_tool.py Demo.uvprojx --target Debug --driver sht3x --driver-port auto --driver-i2c software --driver-scl PB6 --driver-sda PB7 --yes
```

下面的手填接口说明仅针对 `generic`；自动生成包以其 `DRIVER_GUIDE.md` 顶部的 STM32 指引为准。

| 选项 | 首版明确支持的范围 |
| --- | --- |
| W25Q128JV | SPI NOR，16 MiB，256 字节页，4 KiB 扇区；ID/读取/分页编程/扇区擦除/写后校验 |
| 24LC02B | I²C EEPROM，256 字节、8 字节页、1 字节地址 |
| 24LC256 | I²C EEPROM，32 KiB、64 字节页、2 字节地址 |
| SHT3x-DIS | SHT30/31/35，单次温湿度测量，两个 CRC 校验 |
| BH1750FVI | 单次高分辨率光照采样，明确设置 MTreg=69 |
| ADS1115 | 四路单端 ADC，128 SPS、±4.096 V 量程、微伏输出；不是 ADS1015 |
| SSD1306 | I²C、128×32/64、内置电荷泵、页式帧缓冲；不是 SH1106，也不附字体库 |

不支持凭类似名称推断所有 W25Q/AT24、NAND 或 MCU 内部 Flash；也不自动将这些存储驱动绑定到 FatFS/LittleFS。
具体限制、原厂手册链接和每类器件的调用步骤都在生成的双语 `DRIVER_GUIDE.md` 中。

### 用户具体填哪里

- `kps_board_port.c`：搜索 **TODO**，填写总线收发、毫秒延时、加锁和解锁。
- `kps_board_port.h`：上下文结构；可以保存 HAL/SPL 句柄或自定义 BSP 上下文。
- 软件 I²C 还需填写 `kps_soft_i2c.h` 中的开漏 GPIO、读引脚、微秒延时回调。
- DMA 还需填写 `kps_dma.h` 中的启动、完成等待、中止并停止访问缓冲区的回调，分配持久 DMA 内存。
- `kps_devices.h`：公开 API，参数及单位说明见生成的指南。初始化外设后自行调用，必须检查返回值。

没有填写的端口返回 `KPS_ENOSYS`，不会假装成功。驱动不依赖 HAL、SPL 或 RTOS，
但“协议层库无关”不表示替你配置了任何芯片外设。使用 HAL 时注意其地址参数是否需要把 7 位地址左移一次。

### 通信方式与性能

| 方式 | 适用情况 | 代价与用户工作 |
| --- | --- | --- |
| `hardware` | 首次接线验证、小数据报文 | 硬件阻塞回调，最容易调试；需要有限超时 |
| `software`（仅 I²C） | 无空闲 I²C 控制器、低速传感器 | 开漏模拟，ACK/重复 START/时钟拉伸超时已实现；CPU 占用高，不支持多主仲裁 |
| `dma` | 较长传输、希望降低 CPU 搬运占用 | 需要配置 DMA/IRQ/完成通知/缓存；小报文不一定更快 |

所有公开器件函数都是 **阻塞到完成** 的 API，DMA 也不是“启动就返回”。RTOS 用户可以在等待回调中
睡眠等待事件，裸机用户可有限时轮询。软件 I²C 的半周期至少 5 µs，实际速率还受 GPIO 和调度开销影响。
首版不提供软件 SPI、全异步器件状态机或可量化的“高性能倍数”。

DMA 使用独立、持久的收发缓冲区，失败时尝试中止；若不能确认停止访问缓冲区，隔离链路并拒绝继续传输。
只有确认硬件/DMA/IRQ 全部停止后才能恢复。F4 不要把 DMA 缓冲区放 CCM，带缓存芯片需处理缓存一致性。
不要把在栈上创建的临时缓冲区直接交给未完成的 DMA。

### 命令行

```powershell
python keil_port_tool.py Demo.uvprojx --driver-port generic --driver w25q128jv --driver 24lc256 --driver sht3x --driver-i2c hardware --driver-spi dma --dry-run
python keil_port_tool.py Demo.uvprojx --driver-port generic --driver w25q128jv --driver 24lc256 --driver sht3x --driver-i2c hardware --driver-spi dma --yes
python keil_port_tool.py Demo.uvprojx --uninstall device_drivers
```

同样配置重复运行保留已有文件和用户修改。改变型号组合或通信模式时会拒绝覆盖旧驱动包：
先备份用户端口，再卸载，妥善保留被保护的修改文件，最后在清楚的新目录状态下重新生成。
首版每个工程一个驱动包；多个同型号器件可复用 API，传不同总线上下文/地址。
再次打开生成器会带入已有配置。卸载需选择所有安装 Target，避免误删其他 Target 共用源码。
预览期间工程、组件记录或待生成文件发生变化时停止写入。
CubeMX 重新生成后若工程引用被删，使用现有的体检/接入恢复流程；工具不更新 `.ioc`。

## 验证与发布边界

`tests/test_device_drivers.py` 实际编译并运行生成 C 的协议模拟器，覆盖页边界、地址越界、写保护、
读写校验、CRC、传感器转换、超时、锁释放、软件 I²C 和 DMA 中止隔离；并编译所有器件及通信组合。
有 AC5 时还执行 Cortex-M4/C99/O2 编译检查。缺少编译器会明确跳过，不作为通过证据。
`tests/test_reference_actions.py` 覆盖 Target 隔离、归属、取消、dry-run、并发修改保护与原样回滚。
GUI 回调测试使用真实临时工程，不等于人工鼠标验收。
`tests/test_driver_stm32.py` 另测 HAL/SPL 识别、歧义拒绝、自动 API 模拟执行（含 1/2/3 字节 I²C 接收）、
SPI CS 异常释放，并使用真实 STM32F4 HAL/SPL 头文件做 AC5 编译；仍不是对应器件的实板验收。
`tests/test_driver_stm32f1.py` 复用相同 API 执行测试，并检查 F1 GPIO、保留引脚、重复生成和卸载。
真实 SDK 测试使用 Cortex-M3/C99/O2 编译全部 7 种器件，链接原厂 HAL/SPL 实现；覆盖
HAL `STM32F103xB/xE`、`STM32F107xC` 和 SPL `STM32F10X_MD/HD/CL`，各含硬件/软件 I²C 与 SPI。
测试镜像仅用于链接验收，不烧录。配置 `KPS_DRIVER_F1_HAL_SDK`（Cube 工程根目录）、
`KPS_DRIVER_F1_SPL_SDK`（完整标准库包根目录）与 `ARMCC` 后运行；没有 SDK/编译器时明确跳过。
不能据此宣称所有 F1 型号、SDK 版本或实际传感器均已验收。F1 尚无实板测试。

**本批新器件驱动尚未实板验收，也尚无 DMA 性能测量。** 先按指南做只读识别，再在授权区域测试
擦写、拔线超时和断电保留；没有实物的项目不要勾成“已实测”。驱动及生成代码为 MIT，原厂 PDF 不随包分发。
