# After porting: what you must implement

[简体中文](POST-PORTING.zh-CN.md) · [GUI workflow](GUI.en.md)

**Scope: CubeMX/HAL projects.** SPL and custom entry points have not completed
automatic-porting acceptance; see [scope](SUPPORT.en.md). Directory fallbacks below
do not establish complete SPL support.

The tool copies middleware, updates Keil references and generates integration
skeletons. It cannot infer your display controller, storage device, PHY, power
wiring or interrupt design from an MCU name. **A successful build is only the
first gate.** Paths below assume a CubeMX layout. Project root means the directory
containing `Core`; MDK directory means the directory containing the `.uvprojx`.
Non-CubeMX projects may use `Application` instead. The preview/log gives actual paths.

## Before running on hardware

1. Commit or back up the project, close it in Keil, then review and apply changes.
   Edit the private project copy, not the shared source repository.
2. Read warnings and generated-file paths. Search for `KPS_USER_ACTION`, `TODO`,
   `Platform_` and `_BD_`. Unimplemented hooks intentionally report failure;
   replacing that with fake success does not implement a driver.
3. Validate HAL clocks, UART, GPIO and raw drivers first. Check RAM/Flash in the
   linker map and task stacks. STM32F407 CCM is not DMA-accessible memory.
4. Keep one RTOS, one owner of its exception handlers, and one USB/network driver
   for each controller. The tool does not update `.ioc`. After CubeMX regeneration,
   recheck initialization, IRQs, project references and configuration differences.
5. Reset after programming and record firmware identity, logs and measured results.
   Tests on another board do not certify yours. Tool rollback cannot restore data
   that firmware erased from Flash or SD.

## Where to start

| Component | Main user-editable entry points | First acceptance gate |
| --- | --- | --- |
| FreeRTOS | `Core/Src/freertos_app.c`, effective `FreeRTOSConfig.h` | Two tasks and correct wall-clock timing |
| RT-Thread | `RTThread/App/rtthread_app.c`, `RTThread/Config/rtconfig.h` | Heartbeat, switching, IPC, stack margin |
| LVGL | MDK `LVGL/porting/lv_port_*_template.*`, library-adjacent `lv_conf.h` | Text, RGB, all edges and animation |
| FatFS | `FatFs/Target/user_diskio.c`, `FatFs/App/fatfs.c`, effective `ffconf.h` | Write, close, reset, compare readback |
| LittleFS | `Core/Src/littlefs_port.c` | Raw block tests, mount, file readback |
| LwIP | `Core/Src/lwip_netif_driver.c`, `lwip_port.c`, `Config/LwIP/lwipopts.h` | PHY link, ARP/Ping, exact UDP/TCP data |
| TinyUSB | `Core/Src/tinyusb_app.c`, `usb_descriptors.c`, `Config/TinyUSB/tusb_config.h` | Enumeration, data integrity, reconnect |
| SEGGER RTT | `Config/SEGGER_RTT/SEGGER_RTT_Conf.h` | Visible logs without blocking on disconnect |
| CMSIS-DSP | Your algorithm source, Target CPU/FPU settings | Known input/reference output comparisons |
| Peripheral guards | `rtos_peripheral_guard.*` and your HAL call sites | Concurrent operations, bounded timeouts |

Existing user files are not simply replaced by new templates. Review and merge
version/configuration conflicts; do not delete working custom drivers to silence warnings.

## FreeRTOS / CMSIS-RTOS2

- Create resources and tasks in the `RTOS_MUTEX`, `RTOS_SEMAPHORES`, `RTOS_TIMERS`
  and `RTOS_THREADS` sections of `MX_FREERTOS_Init()`. Put application work in
  `StartDefaultTask()` or additional task functions.
- CMSIS-RTOS2 `osThreadAttr_t.stack_size` is in bytes. Native `xTaskCreate` depth
  is in `StackType_t` elements, normally four bytes on Cortex-M4. Do not copy the
  same numeric stack value between these APIs without conversion.
- This tool's FreeRTOS CMSIS2 adapter passes the entry directly to the kernel.
  Task functions must not return. Keep persistent tasks in a loop; end a one-shot
  CMSIS2 task with `osThreadExit()`, or a native task with `vTaskDelete(NULL)`.
  Otherwise `prvTaskExitError` can assert even though the scheduler did start.
- Verify `main.c` already contains `osKernelInitialize → MX_FREERTOS_Init →
  osKernelStart`, or `MX_FREERTOS_Init → vTaskStartScheduler` for the native API.
  Do not start a second scheduler. The loop after scheduler startup is not the
  normal application execution path.
- Size the RTOS heap, stacks and priorities for actual tasks, queues and timers.
  Retain tuned settings where appropriate and review compatibility warnings.
  Selecting all source files does not solve insufficient RAM.
- Check unique SysTick/SVC/PendSV ownership and the HAL 1 ms time base. Do not add
  a duplicate RTOS tick. Interrupts calling the kernel must obey
  `configMAX_SYSCALL_INTERRUPT_PRIORITY` and use appropriate FromISR APIs, not
  blocking task APIs. On assertion inspect `g_freertos_assert_file/line` and the stack.
- Test two task heartbeats, queues, timeouts, minimum free heap, stack high-water
  marks and repeated resets before adding other middleware.

## RT-Thread

Current support is the **v5.2.2 standard single-core Cortex-M4 kernel**, not a
complete BSP/Env/DFS/FinSH/SMP/TrustZone project. It is exclusive with FreeRTOS.

- Edit `RTThread_DefaultTask()`. Additional threads can be created once the kernel
  is running; check initialization/start return values. Smaller native priority
  numbers mean higher priority; stack sizes are bytes.
- Set `KPS_RTTHREAD_HEAP_SIZE` and `KPS_RTTHREAD_APP_STACK_SIZE` in `rtconfig.h`.
  The generated shared CubeMX SysTick requires `RT_TICK_PER_SECOND=1000`; do not
  change this to 100 without redesigning the tick integration.
- `MX_RTTHREAD_Init()` already initializes and starts the scheduler. Do not add a
  second `rtthread_startup()`. Wire `KPS_RTTHREAD_Tick()` and exception vectors once.
- Retain `RT_DEBUGING_ASSERT`. Inspect `g_rtthread_assert_line` and the call stack
  on failure. Never hide resource-creation side effects inside `RT_ASSERT(...)`.
- Test heartbeat, context switching, IPC, software timers, floating-point context,
  stack/heap margins and repeated resets.

## LVGL 8 / 9

1. If port templates were requested, open `lv_port_disp_template.c`, enable its
   template compilation switch and implement display initialization and flush.
   Set physical resolution, rotation, RGB/BGR and pixel format. With no touch
   hardware, do not register a dummy input device.
2. Flush-area endpoints are inclusive: the usual pixel count is
   `(x2-x1+1)*(y2-y1+1)`. Window coordinates, controller offsets, row stride and
   transmitted length must agree. Test raw corner pixels, a border, RGB blocks
   and a checkerboard first. Slanted rows suggest window/stride problems, not
   automatically a font problem.
3. Signal flush completion only after transfer completion: `lv_disp_flush_ready`
   in v8, `lv_display_flush_ready` in v9. Do not reuse a DMA source buffer early.
   Handle RGB565 SPI byte order in one place, not twice.
   See the [LVGL 8.4 display interface](https://lvgl.io/docs/open/8.4/porting/display).
4. Call `lv_init()`, initialize the display port, then create objects. Call
   `lv_timer_handler()` periodically from one GUI task or the bare-metal loop.
   Other tasks must communicate with that task or use consistent locking.
5. For v8 choose a real millisecond tick such as `lv_tick_inc(1)` from a 1 ms
   source, or `LV_TICK_CUSTOM`, not both. For v9 choose that version's tick callback
   or `lv_tick_inc`; do not copy v8 macros blindly. A loop iteration is not a
   guaranteed millisecond. Match v9 OS settings to the actual kernel.
6. Configure memory, fonts, widgets and color depth in the effective `lv_conf.h`,
   not the library's forwarding header. Use the compatible v8 path for AC5 and
   supported AC6 settings for v9.
7. Verify readable text, colors, all four edges, animation, sustained refresh,
   DMA errors and the responsiveness of other tasks.

Zero offsets worked on the previously tested F407 display; that is not a universal
default for every ST7735/ST7789 module.

## FatFS

- Implement `USER_initialize/status/read/write/ioctl`. `sector` is a logical
  sector index, not a byte address; `count` can exceed one. Check completion,
  timeouts, card capacity and DMA alignment.
- SDIO polling can overrun/underrun its FIFO when a task is preempted. FatFS locks
  serialize filesystem access, not FIFO service timing. Use a reviewed DMA/IRQ
  adapter with actual completion/error handling, bounded timeouts and an aligned
  DMA-accessible bounce buffer for unaligned, task-stack or CCM data. Do not hide
  the failure by disabling assertions or masking interrupts for long periods.
- Bare-metal code has a stack budget too: startup-file main stack and RTOS task
  stacks are separate. Local file objects/buffers, formatting and nested calls
  can exceed the initial main stack. Our HIL failed with 1 KiB and passed with
  8 KiB; this is not a universal 8 KiB recommendation. Check map files, measured
  stack margins and worst-case call paths in your own application.
- `CTRL_SYNC` waits for writes to finish. `GET_SECTOR_COUNT` returns sectors,
  `GET_SECTOR_SIZE` (when required) returns bytes, and `GET_BLOCK_SIZE` returns
  erase-block size in sectors. Do not invent geometry. Raw NOR is not an SD card:
  erase-before-write and write amplification need a suitable adaptation layer.
  See the units in [FatFS disk_ioctl](https://elm-chan.org/fsw/ff/doc/dioctl.html).
- `MX_FATFS_Init()` registers a driver; it does not prove a successful mount.
  Once hardware is ready, call `f_mount(&USERFatFS, USERPath, 1)` and inspect
  `FRESULT`, from a task under an RTOS. Reuse existing CubeMX filesystem objects
  and drive paths instead of defining a second `MX_FATFS_Init()`.
- Configure version-appropriate `FF_USE_LFN`, `FF_CODE_PAGE`, Unicode APIs,
  read-only/exFAT/formatting options in `ffconf.h`. Chinese filenames require
  consistent encoding and `ffunicode.c`; LFN mode 3 needs allocation hooks.
  RTOS reentrancy needs the matching system layer, but does not make ISR file I/O
  safe or automatically protect a shared `FIL` object.
- A failed mount is not permission to format. Check hardware, block I/O, partitions
  and errors first. Use `f_mkfs` only when erasure is authorized, with the signature
  from your installed FatFS headers.
- Test bounded raw multi-sector I/O, file write/sync/close/readback, reset readback,
  concurrent files and removal errors. Do not let USB MSC and the MCU filesystem
  write the same volume simultaneously.

## LittleFS

- Implement `LittleFS_BD_Read/Prog/Erase/Sync`; strong functions in a separate
  source file can override the weak hooks. Return zero on success or an appropriate
  negative error. Sync must not report completion while Flash is still busy.
- Set `LFS_BLOCK_SIZE/BLOCK_COUNT/READ_SIZE/PROG_SIZE` for the actual device and
  partition. The 4096×128 template is an example, not detected geometry. Address
  calculation is `partition_start + block*block_size + off`. Validate bounds,
  page-program limits, busy timeout and write enable.
- STM32 internal Flash can have unequal sectors; do not assume 4 KB sectors.
  Raw NAND requires ECC/bad-block handling not generated here. Never overlap
  firmware, calibration or another filesystem's partition.
- RTOS variants create the lock before mount/format. Call from a thread, not an ISR.
- Try `LittleFS_Init()` first. Call `LittleFS_Format()` only for known blank media
  or an authorized erase, then mount again. Do not automatically format after
  every mount error: a wiring fault must not become data loss.
- Test raw erase/program, cross-page writes, file length/content, remount, reset
  readback and carefully bounded power-loss behavior.

## LwIP

- Implement `NetworkDriver_Init/Send/Receive/GetMac` in `lwip_netif_driver.c` or
  override the weak hooks in your own file. Init/Send return zero on success;
  Receive returns a frame length or zero when idle. Send must copy the frame or
  finish using it before returning success: the template reuses its TX buffer.
- For STM32 ETH, verify RMII/MII, reference clock, PHY address/reset, MDIO/MDC,
  DMA descriptors and buffers. Add cache maintenance on DCache-equipped MCUs.
  The W5500 adapter expects MACRAW Ethernet, not its hardware TCP socket API.
- Choose DHCP or static addressing in `LwIP_AddNetif()` and `lwipopts.h`. A direct
  PC link without DHCP needs distinct addresses on one subnet. A wireless campus
  connection does not provide DHCP on the wired adapter. Do not casually disable firewalls.
- Bare metal must call `LwIP_Poll()` for input/timeouts. Under an RTOS, verify
  the generated input task and TCP/IP thread. Use raw APIs in the proper TCP/IP
  context. Add runtime PHY disconnect/reconnect link-state notifications.
- Selecting HTTP/MQTT sources does not start a server/client. Supply content,
  callbacks, server address, ports and connection logic. TLS/certificates and
  Internet access are not automatically provided.
- Test PHY ID/link, ARP/Ping, sequenced UDP/TCP payload comparisons, reconnect and load.

## TinyUSB

- AC5 auto preparation is role-specific: Device uses 0.17.0, Host/dual-role uses
  0.18.0 with narrowly scoped DWC2 register/FIFO fixes. 0.17 lacks the DWC2 Host
  controller implementation. AC6 Host auto uses 0.21.0; this is not a claim that
  every compiler/role/board combination has passed hardware acceptance.
- SDK >=0.18 needs a real millisecond clock. Generated `tusb_time_millis_api()`
  uses the selected RTOS tick or `HAL_GetTick()` when an STM32 HAL source is in
  the project. Otherwise implement `TinyUSB_Platform_Millis()` as instructed in
  the source. Ensure the tick advances during enumeration; a constant clock hangs.
- Implement `TinyUSB_Platform_Init()` for clocks, pins, controller, VBUS and IRQ.
  Forward the actual IRQ to the corresponding `tud_int_handler`/`tuh_int_handler`.
  Do not also initialize HAL_PCD/USB_DEVICE for that controller. F407 USB FS needs
  an actual 48 MHz USB clock, not merely a plausible CPU clock. Respect RTOS IRQ priorities.
  For FreeRTOS derive the unshifted HAL/NVIC priority from this project's
  `configMAX_SYSCALL_INTERRUPT_PRIORITY` and `__NVIC_PRIO_BITS`; do not assume
  that priority 5 or 6 is always allowed. The generated RTOS USB owner initializes
  the controller inside its running task, not before scheduler start.
- Select role, root hub and classes in `tusb_config.h`. Endpoints are limited;
  not every class can always coexist. Device+Host needs two available controllers
  and appropriate wiring; Host additionally needs controlled VBUS power.
- Edit product strings, serial number, VID/PID and report descriptors in
  `usb_descriptors.c`. Example IDs are not a product ID allocation. Default HID
  is a keyboard descriptor, not an implementation of arbitrary HID devices.
- CDC initially echoes. Test one-byte through large transfers and slow host reads
  before replacing it with application logic; an openable COM port is insufficient.
- MSC needs ready/capacity/read10/write10/scsi callbacks, offset/length checks,
  flushing and eject handling. Zero capacity and -1 returns mean no backend yet.
  PC and MCU must not mount/write the same FAT volume concurrently.
- The callbacks above describe **Device MSC**, not a USB thumb drive in Host
  mode. Host MSC must wait for `tuh_msc_mounted()` and readiness, then connect
  `tuh_msc_read10/write10` to FatFS `user_diskio.c`. Transfers are asynchronous:
  wait for a successful CSW with a timeout, validate sector size/ranges and keep
  the USB service task running. Do not recursively pump `tuh_task` from callbacks
  or create a second RTOS USB owner. Serialize filesystem/backend access; handle
  unplug/error without reusing buffers still owned by an unfinished transfer.
  File sync/readback is not proof of surprise-power-loss durability of drive caches.
- Bare metal must call `TinyUSB_AppTask()`. RTOS mode already creates a service
  task; do not add a second controller owner. Add application callbacks and data
  handling for HID/MIDI/Vendor as required.
- Verify descriptors, each class, exact large transfers, slow readers, reconnect,
  reset and safe storage removal.

## SEGGER RTT, CMSIS-DSP and peripheral guards

**RTT:** start with `SEGGER_RTT_WriteString` or other direct APIs in a debugger
frontend that supports RTT. Configure buffers and full-buffer behavior; blocking
mode can stall without a host reader. Confirm the project configuration and
critical sections are effective. Avoid duplicate `fputc/_write` redirects.
Not every ST-Link frontend provides an RTT viewer.

**DSP:** include `arm_math.h` in your algorithm source. Check CPU/FPU, floating-point
ABI and AC5/AC6 source selection; do not compile both aggregators and their child
sources. FFT instances, buffer sizes, fixed-point scaling, saturation and alignment
are algorithm-specific. Compare known vectors with explicit tolerances before
measuring timing; printing “DSP OK” is not a numerical test.

**Guards:** wrap your own UART/SPI/I2C/Flash operations with
`RTOS_PeripheralGuard_Lock/Unlock`, check acquisition status and release on every
exit path. Initialize once. Generated locks are per resource category, not bound
to HAL handles, and do not automatically wrap existing calls. Protect asynchronous
DMA transactions until completion, not just until launch. Do not take blocking
mutexes from an ISR.

## Combined acceptance

Raw driver → RTOS kernel → individual middleware → one additional component at a
time. Keep reproducible firmware and record clocks/IRQs, RAM allocation, heap/stack
margins, timeout/error counters and repeated resets. Display/storage/network/USB
compete for RAM, DMA, bandwidth and scheduling time. Individual passes do not prove
the combined build. Feature lists describe generation capabilities; test reports
describe the hardware combinations actually exercised.
