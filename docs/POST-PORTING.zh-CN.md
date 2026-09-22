# 移植完成以后：需要你填写什么

[English](POST-PORTING.en.md) · [界面操作](GUI.zh-CN.md)

**适配范围：CubeMX/HAL 工程。** 标准库（SPL）及自定义入口尚未完成自动移植验收，
见[适配范围](SUPPORT.zh-CN.md)。下文提到的目录回退机制不代表标准库完整支持。

本工具复制中间件、配置 Keil 引用并生成接入骨架，**不能通过芯片型号推导出你的
屏幕、存储芯片、PHY、电源连接或中断优先级设计**。编译通过只是第一关。
下面的路径以 CubeMX 工程为例；`<工程目录>` 指含 `Core` 的目录，
`<MDK目录>` 指 `.uvprojx` 所在目录。非 CubeMX 工程可能使用 `Application` 目录，
以执行前差异预览与执行日志中的实际路径为准。

## 先完成这五件事

1. 保存 Git 提交或完整工程备份，关闭 Keil，再应用预览。共享库不是修改入口。
2. 查看日志中的警告和生成文件清单；在工程内搜索 `KPS_USER_ACTION`、`TODO`、
   `Platform_` 和 `_BD_`。占位函数返回失败是有意的，不能改成假成功。
3. 先验证 HAL 时钟、串口、GPIO 和原始驱动，再接中间件；比较 `.map` 中 RAM/Flash
   用量和任务栈。STM32F407 的 CCM 不能作为 DMA 缓冲区。
4. 只保留一套 RTOS、一套相应异常处理、一套同控制器的 USB/网络驱动。
   工具不修改 `.ioc`；重新运行 CubeMX 后重新检查入口、IRQ、文件引用与配置差异。
5. 每次烧录后复位，记录固件版本、串口日志与实际测试结果。不能把别的板型验证
   结果当成你自己的板型验证。回滚恢复的是工具事务，不是硬件 Flash/SD 内容。

## 文件入口速查

| 组件 | 主要填写位置 | 首个验收目标 |
| --- | --- | --- |
| FreeRTOS | `Core/Src/freertos_app.c`、有效 `FreeRTOSConfig.h` | 两个任务持续运行，时间与墙钟一致 |
| RT-Thread | `RTThread/App/rtthread_app.c`、`RTThread/Config/rtconfig.h` | 心跳、线程切换、IPC、栈余量 |
| LVGL | `<MDK目录>/LVGL/porting/lv_port_*_template.*`、库同级 `lv_conf.h` | 文字/RGB/边框/动画均正确 |
| FatFS | `FatFs/Target/user_diskio.c`、`FatFs/App/fatfs.c`、有效 `ffconf.h` | 写入、关闭、复位后读回比对 |
| LittleFS | `Core/Src/littlefs_port.c` | 原始块驱动通过后挂载、文件读回 |
| LwIP | `Core/Src/lwip_netif_driver.c`、`lwip_port.c`、`Config/LwIP/lwipopts.h` | PHY Link、ARP/Ping、UDP/TCP 内容比对 |
| TinyUSB | `Core/Src/tinyusb_app.c`、`usb_descriptors.c`、`Config/TinyUSB/tusb_config.h` | 枚举、数据完整性、拔插恢复 |
| SEGGER RTT | `Config/SEGGER_RTT/SEGGER_RTT_Conf.h` | 日志可见，调试器断开不阻塞 |
| CMSIS-DSP | 自己的算法调用文件、Target CPU/FPU 设置 | 已知输入与参考结果比较 |
| RTOS 外设锁 | `rtos_peripheral_guard.*` 与自己的 HAL 调用处 | 并发访问无串扰、超时可退出 |

已有用户文件不会简单被新模板替换。若看到版本/配置冲突，应比较差异并手动合并，
不要通过删除自己写好的驱动来“消除提示”。

## FreeRTOS / CMSIS-RTOS2

- 在 `MX_FREERTOS_Init()` 的 `RTOS_MUTEX`、`RTOS_SEMAPHORES`、`RTOS_TIMERS`、
  `RTOS_THREADS` 区创建资源和任务；业务写入 `StartDefaultTask()` 或自己的任务函数。
- CMSIS-RTOS2 的 `osThreadAttr_t.stack_size` 单位是字节；原生 `xTaskCreate` 的栈深度
  单位是 `StackType_t` 元素，在 Cortex-M4 上通常为 4 字节。别把两个数直接照抄。
- 本工具采用的 FreeRTOS CMSIS2 适配层把入口直接交给内核，任务函数不能直接返回。
  常驻任务循环运行；一次性 CMSIS2 任务完成后调用 `osThreadExit()`，原生任务调用
  `vTaskDelete(NULL)`。否则可能进入 `prvTaskExitError`，不是调度器没有启动。
- 检查 `main.c` 是否已生成 `osKernelInitialize → MX_FREERTOS_Init → osKernelStart`
  （原生 API 则 `MX_FREERTOS_Init → vTaskStartScheduler`）。不要再次启动调度器；
  调度器启动后的 `while(1)` 不是正常业务入口。
- 保留已有调好参数；按任务、队列、定时器实际需求计算 RTOS 堆、任务栈和优先级。
  必需依赖不能为了裁剪而任意去掉；全选也不能代替内存评估。
- 核对 SysTick、SVC、PendSV 的唯一归属和 HAL 1 ms 时间基准；不要重复加 RTOS tick。
  使用 RTOS API 的中断遵守 `configMAX_SYSCALL_INTERRUPT_PRIORITY`；ISR 使用对应
  FromISR API，不用阻塞 API。断言时检查 `g_freertos_assert_file/line` 与调用栈。
- 验收：两任务心跳、队列收发、超时、最小剩余堆、栈高水位、反复复位；最后再加 LVGL 等。

## RT-Thread

当前是 **v5.2.2 标准单核 Cortex-M4 内核接入**，不是完整 BSP、Env、DFS、FinSH、
SMP 或 TrustZone 工程。不能与 FreeRTOS 同选。

- 修改 `RTThread_DefaultTask()`，新增线程可在内核运行后创建，检查返回值并启动。
  原生 RT-Thread 数值越小优先级越高；栈大小以字节计。
- `KPS_RTTHREAD_HEAP_SIZE`、`KPS_RTTHREAD_APP_STACK_SIZE` 在 `rtconfig.h` 中配置。
  当前 CubeMX 共用 SysTick 路径要求 `RT_TICK_PER_SECOND=1000`，不要单独改成 100。
- `MX_RTTHREAD_Init()` 已初始化内核并启动调度器，不要再调用另一份 `rtthread_startup()`。
  `KPS_RTTHREAD_Tick()` 与异常向量应只接一次。
- 保留 `RT_DEBUGING_ASSERT`；断言时查看 `g_rtthread_assert_line` 和调试调用栈。
  不要把带副作用的资源创建函数只放在 `RT_ASSERT(...)` 里。
- 验收：心跳、线程切换、信号量/队列、软定时器、浮点上下文、栈/堆和多次复位。

## LVGL 8 / 9

1. 勾选生成 port 模板后，打开 `lv_port_disp_template.c`。按模板启用其编译开关，
   设置实际横纵分辨率、旋转、RGB/BGR 和颜色格式，填写屏幕初始化与 flush 回调。
   没有触摸就不注册输入设备，不必硬接一个空驱动。
2. `area` 的两个端点均包含在刷新区域中：像素数通常为
   `(x2-x1+1)*(y2-y1+1)`。窗口坐标、控制器偏移、像素行宽和数据量必须一致。
   先用纯驱动画四角、边框、RGB 和棋盘格；扭曲/斜行先查窗口和行跨度，不直接归咎于字体。
3. flush 必须在传输真正结束后通知完成：v8 使用 `lv_disp_flush_ready`，
   v9 使用 `lv_display_flush_ready`。DMA 未结束时不要提前释放/改写缓冲区。
   RGB565 的 SPI 字节序只在一个地方处理，别交换两次。
   参见 [LVGL 8.4 显示接口](https://lvgl.io/docs/open/8.4/porting/display)。
4. `lv_init()` 后初始化显示端口并创建对象。在一个 GUI 任务或裸机主循环中周期调用
   `lv_timer_handler()`；其他线程通过消息传递或同一把锁访问 GUI，不能无保护同时调用。
5. v8 可用真实 1 ms 中断调用 `lv_tick_inc(1)`，或配置 `LV_TICK_CUSTOM`，两者二选一。
   v9 按所选版本选择 tick 回调或 `lv_tick_inc`，不要照抄 v8 宏。不要在不确定周期的
   主循环中假定每次经过就是 1 ms。v9 OS 配置也必须与实际内核一致。
6. 在有效 `lv_conf.h` 调整内存、字体、控件、色深；库内转发头不是主要编辑位置。
   AC5 使用兼容的 v8 路径，v9 使用支持的 AC6 配置。
7. 验收：静态文字、颜色、四边、动画、长时间刷新、DMA 错误和其他任务是否被饿死。

此前 F407 测试屏的零偏移成功只适用于该屏，不能当作所有 ST7735/ST7789 模组的默认答案。

## FatFS

- 实现 `USER_initialize/status/read/write/ioctl`。`sector` 是逻辑扇区号，不是字节地址；
  `count` 可能大于 1。SD 驱动需检查完成状态、超时、卡容量及 DMA 对齐。
- SDIO 轮询收发可能在任务抢占时 FIFO 溢出/欠载；FatFS 互斥锁只串行化文件系统访问，
  不能保证轮询服务时限。RTOS 下使用经过验证的 DMA/中断驱动，等待真实完成或错误，
  超时必须退出；为非对齐、任务栈或 CCM 中的缓冲区准备 DMA 可访问的对齐中转缓冲。
  不要关闭断言或长时间关闭中断来掩盖问题。
- 裸机也有栈预算：启动文件中的主栈和 RTOS 任务栈是不同配置。文件对象、读写缓冲区、
  格式化和嵌套调用可能超过初始主栈。实板 HIL 的 1 KB 主栈曾失败，改为 8 KB 后通过；
  这不是所有工程固定使用 8 KB 的建议，应结合 map、栈水位和最坏调用路径评估。
- `CTRL_SYNC` 等待所有写操作完成；`GET_SECTOR_COUNT` 返回扇区数，
  `GET_SECTOR_SIZE`（需要时）返回字节，`GET_BLOCK_SIZE` 返回擦除块包含的扇区数。
  不知道容量时不能假填一个值。原始 NOR 不是可直接覆盖写的 SD，必须自行处理擦除和写放大。
  接口单位参见 [FatFS disk_ioctl](https://elm-chan.org/fsw/ff/doc/dioctl.html)。
- `MX_FATFS_Init()` 仅注册磁盘驱动，不代表挂载成功。硬件就绪后调用
  `f_mount(&USERFatFS, USERPath, 1)` 并检查 `FRESULT`；RTOS 下放在线程中。
  已有 CubeMX `fatfs.c` 时复用其对象与磁盘路径，不再新增同名初始化函数。
- `ffconf.h`：按版本确认 `FF_USE_LFN`、`FF_CODE_PAGE`、Unicode API、只读/exFAT/格式化。
  中文文件名需要匹配代码页/Unicode 和 `ffunicode.c`；LFN 模式 3 还需要分配释放函数。
  RTOS 多线程需匹配可重入系统层；它不允许 ISR 访问文件，也不自动保护每个共享 `FIL`。
- 未挂载不等于必须格式化。先查电源、卡检测、块驱动、分区与返回值，确认可擦除后才
  `f_mkfs`。格式化参数随 FatFS 版本变化，请按项目里的头文件签名调用。
- 验收：多扇区原始读写（限定测试区域）、文件写入/`f_sync`/关闭、读回比对、复位读回、
  多文件并发与拔卡错误处理；不要同时让 PC 的 USB MSC 和 MCU 文件系统写同一介质。

## LittleFS

- 实现 `LittleFS_BD_Read/Prog/Erase/Sync`，可在自己的 C 文件中提供同签名强定义覆盖弱函数。
  成功返回 0；底层错误返回合适的负错误码。`Sync` 不能在芯片仍忙时假报完成。
- `littlefs_port.c` 的 `LFS_BLOCK_SIZE/BLOCK_COUNT/READ_SIZE/PROG_SIZE` 必须按芯片和
  分区填写。模板 4096×128 只是示例，不是自动探测结果。地址应为
  `分区起始地址 + block*block_size + off`，检查上下界、页写边界、忙超时和写使能。
- STM32 内部 Flash 扇区可能大小不等，不可直接假定均为 4 KB。裸 NAND 还涉及 ECC/坏块，
  本工具没有生成这些驱动。不要让文件系统分区覆盖程序、校准或其他文件系统。
- RTOS 版本在挂载/格式化前建立锁；从线程调用，不能从 ISR 调用。
- 首先 `LittleFS_Init()`；只有确认空介质或授权清除后才 `LittleFS_Format()`，随后重新挂载。
  不要遇到任意挂载错误就自动格式化，这会把接线故障变成数据丢失。
- 验收：原始擦写、跨页写、文件内容和长度、重挂载、复位读回；掉电测试须限定数据区。

## LwIP

- 在 `lwip_netif_driver.c` 中实现 `NetworkDriver_Init/Send/Receive/GetMac`，或在自己的
  源文件中覆盖这些弱函数。Init/Send 成功为 0；Receive 返回帧长度，没数据返回 0。
  Send 返回成功前必须已复制数据或完成发送，模板会复用 TX 缓冲区。
- STM32 ETH：核对 RMII/MII、50 MHz 参考时钟、PHY 地址与复位、MDIO/MDC、DMA 描述符和
  缓冲区；有 DCache 的芯片另做缓存一致性。W5500 模板走 MACRAW，不是硬件 TCP socket API。
- 在 `LwIP_AddNetif()` 与 `lwipopts.h` 选择 DHCP 或静态 IP。直连电脑没有 DHCP 服务时，
  手工设置双方不同但同网段的地址；无线校园网不等于有线侧有 DHCP。不要未经允许改防火墙。
- 裸机持续调用 `LwIP_Poll()` 处理收包与超时；RTOS 核对自动创建的处理任务和 TCP/IP 线程。
  RAW API 要在正确的 TCP/IP 上下文中使用。补齐运行中 PHY 断连/重连的 link 状态通知。
- HTTP、MQTT 等源文件被选中不代表服务/客户端已启动；还要配置资源、回调、服务器地址、
  端口和连接逻辑。模板没有自动提供 TLS、证书或公网可达性。
- 验收：PHY ID/Link → ARP/Ping → UDP/TCP 带序号及长度的内容比对 → 断线恢复 → 压力测试。

## TinyUSB

- FreeRTOS 的 USB IRQ 优先级须根据本工程 `configMAX_SYSCALL_INTERRUPT_PRIORITY`
  和 `__NVIC_PRIO_BITS` 换算为 HAL/NVIC 使用的未移位值，不能假定 5 或 6 一定合法。
  新模板在 USB 专用任务运行后初始化控制器，避免调度器启动前 USB 中断调用 RTOS API。
- AC5 自动准备按角色选版：Device 使用 0.17.0，Host/双角色使用带定点 DWC2
  寄存器/FIFO 修正的 0.18.0。0.17 没有 DWC2 Host 控制器实现。AC6 Host 自动选择
  0.21.0；选版策略不等于所有编译器、角色和硬件组合均已实板验收。
- SDK >=0.18 需要真实毫秒时基。生成的 `tusb_time_millis_api()` 使用所选 RTOS
  tick；工程包含 STM32 HAL 源文件时，裸机使用 `HAL_GetTick()`。其他裸机工程须按
  注释实现 `TinyUSB_Platform_Millis()`。枚举期间时基必须持续前进，返回常数会卡住。
- **Host U 盘与 Device MSC 回调不是同一接口**：Host 应等待 `tuh_msc_mounted()`
  和就绪状态，再将异步 `tuh_msc_read10/write10` 连接到 FatFS `user_diskio.c`。
  校验扇区大小/范围，等待成功的 CSW 并设超时；等待期间 USB 服务必须继续运行。
  不要在 USB 回调里递归运行 `tuh_task`，RTOS 下不要创建第二个 USB 服务所有者。
  串行化文件系统/块设备访问，拔出或超时后不得复用仍由未完成传输持有的缓冲区。
  文件同步和读回成功，不等于已证明 U 盘内部缓存能承受任意时刻掉电。
- 在 `TinyUSB_Platform_Init()` 中配置 USB 时钟、GPIO、控制器、VBUS 和 IRQ；实际 IRQ
  转发到对应 `tud_int_handler`/`tuh_int_handler`。同一控制器不能同时启动 HAL_PCD/USB_DEVICE。
  F407 USB FS 时钟要实际为 48 MHz，不能只检查系统时钟。中断优先级必须兼容所用 RTOS。
- `tusb_config.h` 选择角色、控制器端口和类别；端点数量有限，别把所有类别当作总能共存。
  Device+Host 需要实际可用的两个控制器及对应电路，Host 还要受控 VBUS 供电。
- `usb_descriptors.c` 修改产品名、序列号、产品所用 VID/PID 和报告描述符；示例 ID 不是
  给你授予的产品 ID。HID 默认键盘，不代表任意 HID 设备已实现。
- CDC 默认回显，替换成业务前测试 1 字节到大块数据及主机慢读；不能只看串口能打开。
- MSC 要实现 ready/capacity/read10/write10/scsi 回调，正确处理 `offset` 和长度边界、
  缓存刷写与弹出；容量零和返回 -1 是未接底层的提示。MCU 与 PC 不能同时挂载写同一 FAT 卷。
- 裸机持续调用 `TinyUSB_AppTask()`；RTOS 路径已有任务，避免再建第二个同控制器处理任务。
  MIDI/Vendor/HID 的业务回调、收发逻辑需要你补齐。
- 验收：枚举描述符、类别功能、大块逐字节比对、慢读、拔插、复位、存储安全卸载。

## SEGGER RTT、CMSIS-DSP、外设锁

**RTT**：从 `SEGGER_RTT_WriteString` 等直接 API 开始，在实际支持 RTT 的调试工具中
观察。调缓冲区大小及满缓冲策略；阻塞模式可能在主机不读取时卡住任务。
确认工程级 `SEGGER_RTT_Conf.h` 生效和临界区实现。不要同时添加多个 `fputc/_write`
重定向；本工具不保证你的 ST-Link 前端都能显示 RTT。

**DSP**：在业务文件包含 `arm_math.h`，核对 Cortex-M/FPU、浮点 ABI、AC5/AC6 对应源码
选择，不要重复编译聚合源文件与其子文件。FFT 实例、缓冲区尺寸、定点缩放、饱和和
内存对齐由算法决定；先用已知向量和误差阈值比对，再测运行时间，不是打印“DSP OK”。

**外设锁**：在自己的 UART/SPI/I2C/Flash 业务调用前后使用
`RTOS_PeripheralGuard_Lock/Unlock`，检查返回值，所有退出路径都释放。初始化只做一次。
锁是按资源类别生成的，不自动绑定 HAL 句柄，也不会自动包裹已有调用。
DMA 异步事务必须保护到真正完成，不能启动后立即释放；ISR 不获取阻塞互斥锁。

## 联合测试顺序

先裸驱动，再 RTOS 内核，再单组件，最后每次加一个组件。每一步保留可复现版本：
时钟与 IRQ、RAM 分配、堆余量、栈高水位、超时、失败计数、至少多次复位。
LVGL+存储+网络+USB 会竞争 RAM、DMA、带宽和优先级；不是单项通过就自动联合通过。
项目支持清单描述的是生成能力，测试报告才描述哪些硬件组合真正测过。
