# STM32 Standard Peripheral Library: initial adapter (development)

[简体中文](SPL.zh-CN.md)

Available in `2.3.0-dev1` source and Windows prerelease packages, not the older RC6 EXE. Initial
coverage is FreeRTOS native API and CMSIS-RTOS V2 startup on Cortex-M3/M4 ports.
RT-Thread 5.2.2 single-core startup is also supported on Cortex-M4. Hardware evidence
currently covers only STM32F407ZGT6 with AC5; additional combinations require
individual acceptance. Non-STM32 automatic integration has not been enabled.

## Additional component support

### September 26: physical LAN8720A Ethernet

Four separate no-HAL fixtures used tool-generated LwIP startup/OS ports with a
private vendor SPL MAC driver and bounded DMA descriptor adapter. Each was backed
up, programmed with verification, and explicitly reset. Static-IPv4 direct-link
checks passed four pings, 80 UDP packets (33910 bytes) and 57852 exact TCP bytes
per mode. Each then ran simultaneous UDP/TCP for 60 seconds; stress-only totals:

| Mode | UDP packets | UDP bytes | TCP bytes |
| --- | ---: | ---: | ---: |
| Bare metal | 19310 | 28424320 | 221184 |
| Native FreeRTOS | 16408 | 24152576 | 229376 |
| CMSIS-V2 startup | 16133 | 23747776 | 221184 |
| RT-Thread | 16302 | 23996544 | 253952 |

Host comparisons and board counters agreed, heartbeats advanced, and failure,
descriptor-drop and transmit-timeout counters remained zero. This is correctness/
sustained-operation evidence, not a peak-throughput benchmark. Builds have zero
errors and 17/25/25/27 warnings respectively. RTOS fixtures use 32 KiB kernel heaps
and 4 KiB TCP/IP stacks; these are test budgets, not product defaults.
CMSIS-V2 also passed a repeated complete baseline after verifying the running
image and resetting without reprogramming.

Runtime fixes were in the board adapter and test echo application: vendor default
broadcast filtering blocked ARP; UDP accounting sampled length after headers were
prepended; whole-pbuf TCP sends could retry forever against a smaller send buffer.
Broadcast acceptance, pre-send payload accounting, segmented writes and sent/poll
continuation passed the unchanged test load. A host C harness also checks pbuf
ownership, retry without duplication, delayed close and error cleanup. Original
failures are retained. The tool does not automatically supply these full drivers
or TCP services.

The wireless RX lead on PA2 was disconnected before programming to avoid the
UART2_TX/ETH_MDIO conflict. State was read over SWD; no host Wi-Fi/IP/firewall/
sharing changes or SD/NOR I/O occurred. These images do not run LVGL/storage/USB
concurrently. Cable hot-replug, DHCP, IPv6, HTTP/MQTT/TLS and other PHYs/MCUs are
outside this batch's acceptance.

### September 26: physical SD and NOR storage addendum

Separate no-HAL fixtures installed FatFS + LittleFS with the actual tool and
private board drivers. Bare metal, native FreeRTOS, CMSIS-V2 and RT-Thread each
passed verified programming, real approximately 8 GB SD / 16 MiB SPI NOR file
write, sync, append, remount and byte-exact readback. Each mode then passed
read-only retention verification after a software reset. Both final files contain
8962 bytes; host and board CRC32 agree on `1197FD2E`, with zero failures and a
continuing heartbeat. After the user confirmed a full power cycle, the retained
RT-Thread image passed read-only verification: unchanged CRCs, zero SD writes /
NOR programs / erases, and an advancing heartbeat. This cold-retention result
applies to that RT-Thread image, not independent cold tests of the other three
modes, and is not power-loss fault injection during a write.

The SD is not formatted; only a dedicated test file is overwritten. NOR access
is bounded to its first 512 KiB. These tests use 3 MHz single-sector polling SDIO,
2.625 MHz SPI and one test worker, not high-speed DMA, concurrent physical I/O or
endurance acceptance. All four builds have zero errors, retaining 5/6/6/8 vendor
or upstream warnings. Main/worker stacks are 8 KiB and RTOS heaps are 32 KiB;
these are fixture budgets, not universal defaults.

Preparation fixed vendor error exits leaving interrupts masked, disabled long
filenames, missing fixture error hooks and a debugger-mailbox acknowledgement
race. Failed attempts remain separate. Exact NOR JEDEC, capacity and bounds
checks stay enabled. SWD state and host data comparison are authoritative;
wireless logs require valid CRCs. This validates the listed components with this
board adapter, **not automatic generation of arbitrary SD/NOR drivers**.

### September 26: mixed-component repeat and uninstall safety

Adjacent components no longer falsely claim each other's main-loop changes.
New records own only the inserted include/init/poll bytes, not a whole shared
loop. Removal still checks exact content and entry/control-flow placement;
modified, duplicated or conditionally moved blocks are not blindly deleted.
Preexisting includes and user work remain. Equal macro values no longer trigger
spacing-only rewrites, so repeating a joint install leaves project/entry bytes intact.

Checks cover all six three-component removal orders, bare/native FreeRTOS/
CMSIS-V2/RT-Thread entries, and real manifest install/repeat/uninstall transactions.
The actual SDK FatFS + LwIP + TinyUSB bare-metal joint fixture built with AC5:
zero errors/23 warnings. A separate copy removed LwIP, then FatFS, then TinyUSB;
each step preserved a user-added counter, passed remaining ownership checks, and
built with zero errors (11/11/5 warnings respectively). These are tooling/build
checks; that joint fixture was not flashed and adds no physical storage/network claim.

Old development records are not silently re-trusted. If legacy line-based SPL
ownership overlaps another component and reports `OWNED_STARTUP_DRIFT`, back up
and compare first, then preview the original install transaction rollback and
reinstallation. Do not blindly roll back later user edits or delete
`.keil-port-tool/manifest.json` to bypass protection.

### Additional component integration (September 25 development source)

FatFS, LwIP and TinyUSB can now attach to actual SPL entries without CubeMX
`USER CODE` markers. Bare-metal integration targets the unique main; RTOS
integration targets the enabled, created default task, not pre-scheduler code.
Only enabled Target sources identify the kernel, not stale configuration headers
or parent directory names. Ambiguous/conditional entries and changed owned blocks
stop automatic edits. Repeat/uninstall regressions preserve user work following
an intact owned initialization block; this is not an arbitrary C semantic rewriter.

| Component / mode | Bare metal | Native FreeRTOS | CMSIS-V2 startup | RT-Thread |
| --- | --- | --- | --- | --- |
| FatFS + RTT | Board RAM-disk pass | Board concurrent RAM-disk pass | Board concurrent RAM-disk pass | Board concurrent RAM-disk pass |
| LwIP 2.2.1 | AC5 build pass | Board UDP loopback pass | AC5 build pass | Board UDP loopback pass |
| TinyUSB 0.17 Device | CDC echo / HID enumeration pass | CDC echo / HID enumeration pass | CDC echo / HID enumeration pass | CDC echo / HID enumeration pass |

The September 25 evidence is limited to F407ZGT6/AC5; the physical SD/NOR addendum
is above, as is the physical Ethernet addendum. **SPL USB Host is not yet certified.** Earlier LVGL/LittleFS/DSP/guard combinations are
documented below. All eight freshly generated LwIP/TinyUSB projects built with
zero errors, with remaining vendor/upstream warnings. The RC6 EXE is unchanged.

#### Work still required in the application

- **FatFS:** requires the ST `ff_gen_drv` layout and modern `ff_mutex_*` interface,
  not every historical release. SPL FreeRTOS uses native mutexes, without HAL or
  a CMSIS wrapper dependency; RT-Thread uses its own backend. `MX_FATFS_Init()`
  registers the disk driver only. Initialize real storage before mounting from a
  running task (or before the bare-metal main loop). Existing RTC and timeout
  settings are preserved. `FF_FS_TIMEOUT` is in kernel ticks, not fixed milliseconds.
  Budget **main/task stack**, including `FIL`, local buffers, LFN and logging;
  free heap alone is insufficient. The original 1 KiB bare-metal test stack
  overflowed into RTT. A 4 KiB stack plus a bottom sentinel passed the repeat test;
  this is a fixture setting, not a universal recommendation.
- **RTT:** ST-Link reads of the control block/ring confirmed advancing logs, not
  J-Link software or all printf redirects. Nonblocking SKIP mode drops logs when
  full; drain promptly or choose an appropriate application policy.
- **LwIP:** bare metal must implement monotonic millisecond
  `LwIP_Platform_Millis()`. NO_SYS assumes a single main-loop owner; IRQs notify or
  queue frames instead of calling the stack. Real `NetworkDriver_*`, PHY, DMA and
  cache handling remain BSP work. UDP loopback exercises the actual stack but no
  NIC, DHCP, TCP, cable or receive/transmit interrupts.
- **TinyUSB:** the legacy SPL device-header bridge is restricted to
  F405/F407/F415/F417. It supplies and checks USB bases, endpoint counts and FIFO
  sizes, refusing to guess other chips. Implement 48 MHz clock, GPIO, IRQ and
  VBUS/power handling. The Device runs below use an explicit board port, not an
  automatically runnable arbitrary project or Host acceptance. RT-Thread IRQs
  need correct interrupt enter/leave registration. User configuration is preserved.

#### Board evidence for this batch

Each image was backed up, programmed, verified and explicitly reset. Wireless
UART evidence accepts CRC32-valid lines only. FatFS used a 64 KiB RAM FAT12 disk:
format, mount/remount, 257-byte file write/sync/close/read/compare. **No physical SD
or board Flash was accessed.** Bare metal completed 231750 cycles over about 39 s;
native, CMSIS-V2 and RT-Thread completed about 39400 cycles per worker over about
39 s, with zero errors and successful mutex-contention timeout checks in all
three RTOS cases. Two RTT reads showed advancing sequences. The initial RT-Thread
test incorrectly self-deleted and then returned, causing duplicate cleanup; its
exit path was corrected and failed evidence retained, without disabling asserts.
Native/RT-Thread LwIP each passed about 34 s of 257-byte UDP loopback, 33702/33701
cycles, zero errors, empty-receive timeout and UART query checks. These are short
functional runs, not long-duration endurance tests.

Subsequently all four TinyUSB 0.17 modes passed USB1 Device checks on this board.
Windows recognized CDC and the HID keyboard interface. Each image passed three
open/close cycles and 120 binary echoes totaling 206430 identical bytes, with
lengths 1/63/64/65/257/1024/4096/8192, zero bytes/all byte values, and delayed
application reads. About 37 seconds of monitoring per image showed no restart,
serviced USB IRQs, no CRC-rejected telemetry and successful UART queries.
Native/CMSIS-V2/RT-Thread minimum USB stack margins were 442/440/450 32-bit words;
free heaps were 10360/10360/14040 bytes. Bare metal used a 4 KiB main stack with
an intact bottom sentinel. These do not guarantee worst-case stack use or endurance.
The private BSP configures PA11/PA12, the 48 MHz PLL and actual IRQ. USB1 supplies
the board alone; unwired PA9 VBUS sensing is disabled. **Do not copy this no-sense
configuration into a self-powered product without appropriate VBUS handling.**
HID enumeration only: no keyboard reports were sent. MSC/MIDI/Vendor/Host,
high-speed USB and other chips remain outside this evidence.
Bare metal additionally passed three software resets: re-enumeration, an 8192-byte
echo, ten status samples and UART query per reset, no CRC rejections and intact
stack sentinel. This is not complete power-loss recovery validation.

## Detection and safety

The adapter requires a STM32 Device and `USE_STDPERIPH_DRIVER` in the Target.
It searches enabled sources referenced by selected Targets, not sibling projects
or backups. A unique `int main(void)` and `void SysTick_Handler(void)` are required.
Names and directories may differ from CubeMX. No `.ioc`, HAL or fabricated
`USER CODE` markers are needed. Select all Targets because startup files may be
shared; use separate projects for different hardware/entry points.

Before migrating:

1. Build and run the original bare-metal SPL project first.
2. Use straight-line initialization followed by one top-level `while (1) { ... }`
   or `for (;;) { ... }`. Conditional startup and ambiguous entries are rejected.
3. SysTick must be empty. For FreeRTOS, SVC/PendSV must be empty or provided by
   startup weak symbols. The initial RT-Thread adapter requires empty PendSV and
   empty/stop-loop-only HardFault in the same IRQ source. Its assembly port owns
   these two vectors; SVC is unchanged. Custom exception logic is not overwritten.
4. Port legacy delay functions that reprogram/disable SysTick to an independent
   timer, or short DWT busy waits on supported devices. Tasks should wait using
   `vTaskDelay/osDelay/rt_thread_mdelay`. Visible SysTick register writes/configuration calls are
   blocked, but this is not complete preprocessing or call-graph analysis.
   Audit headers, aliases, direct addresses and binary libraries yourself.
5. Use SPL `NVIC_PriorityGroup_4` and review all RTOS-calling IRQ priorities.
   Changing the grouping changes the meaning of old preemption/subpriority
   settings. The tool does not reassign peripheral priorities for you.

## Native FreeRTOS

Replace the example paths with your project and SDK:

```powershell
python keil_port_tool.py .\Template.uvprojx --freertos .\SDK\FreeRTOS-Kernel --no-os2 --dry-run
python keil_port_tool.py .\Template.uvprojx --freertos .\SDK\FreeRTOS-Kernel --no-os2
```

In the GUI, uncheck `cmsis_os2.c` while retaining required kernel/queue/timer/stream
sources. New native task templates supply static Idle/Timer memory callbacks;
they do not rely on the CMSIS wrapper to provide them.

## CMSIS-RTOS V2 prerequisites

Use a coherent CMSIS_5 release (5.9.0 matches the existing pinned dependency):

- Place the complete `CMSIS/Core/Include` header set in the project's own Core
  directory. Ensure it actually takes precedence and remove obsolete Core include
  references. Retain ST device headers, startup assembly and system source.
- Add `Libraries/CMSIS/RTOS2/Include` and
  `Libraries/CMSIS/RTOS2/Source/os_systick.c`, retaining upstream licenses.
  Existing supported CMSIS directory layouts also work.
- Rebuild the bare-metal project first. This is an explicit SDK upgrade; the tool
  never silently overwrites the vendor Core. Adding only `cmsis_armcc.h` to old
  Core headers causes duplicate intrinsics and is not a valid upgrade.
- The adapter adds device definitions and C99/GNU settings in the preview. If no
  `RTE_Components.h` exists, it creates a minimal compatibility header. It does
  not define `_RTE_`, select Packs, or manage RTE components.

```powershell
python keil_port_tool.py .\Template.uvprojx --freertos .\SDK\FreeRTOS-Kernel --dry-run
```

## RT-Thread 5.2.2 (single Cortex-M4)

```powershell
python keil_port_tool.py .\Template.uvprojx --rtthread .\SDK\rt-thread-5.2.2 --dry-run
python keil_port_tool.py .\Template.uvprojx --rtthread .\SDK\rt-thread-5.2.2
```

Do not combine with FreeRTOS. The tool inserts `MX_RTTHREAD_Init()` into the actual
SPL main; the historical name does not imply CubeMX dependency. Put business logic
or thread creation in `RTThread_DefaultTask()` in `RTThread/App/rtthread_app.c`,
after kernel/heap initialization. Configure heap/stack in `RTThread/Config/rtconfig.h`.
This initial port requires 1000 Hz. Smaller priority numbers mean higher priority;
stack sizes are bytes. Do not enable `RT_USING_USER_MAIN` or initialize heap/scheduler twice.

RT-Thread alone does not require upgrading old CMSIS Core. Modern CMSIS-DSP does
require a coherent modern Core set, as described for CMSIS-V2 above, but not the
RTOS2 package. DSP trimming checks visible function/table dependencies in aggregate
sources: Statistics needs FastMath, which also needs CommonTables. Missing modules
are reported before writing; deselected files are never silently reselected. This
conservative scan is not a complete C preprocessor and may require modules excluded
by custom conditionals. Actual linking and runtime checks remain necessary.

## Where application code goes

For a usual SPL layout, edit generated `Application/freertos_app.c/.h`:

- Create objects/tasks in `MX_FREERTOS_Init()`. The historical function name does
  not mean CubeMX is required.
- Put business logic in `StartDefaultTask()` or new task functions.
- **The old main loop is preserved but becomes unreachable after scheduling
  starts. Move its application logic into tasks explicitly.** The tool does not
  guess local-variable lifetimes or task stack sizes by moving entire loops.
- After peripheral initialization, the inserted block updates `SystemCoreClock`,
  creates tasks and starts scheduling. Unexpected scheduler return stops for
  diagnosis instead of executing the old bare-metal loop.
- Do not call the legacy SysTick initializer once the RTOS owns that timer.
- Give the display one owner task or appropriate locking. Actual display/touch
  drivers are still board-specific.

Repeat runs do not duplicate startup. Changed owned startup blocks are rejected.
Preview, project-local SDK copies, transaction ownership, uninstall and recovery
remain available; conflicting user edits are preserved, not blindly undone.

## Evidence and limits

Private vendor-SPL fixtures tested on 2026-09-25, with no HAL or `.ioc`:

- Native and CMSIS-V2 firmware both built with AC5, backed up, programmed,
  verified and explicitly reset.
- Each produced 33 samples spanning about 32 seconds: queue/stream/notification
  transfers approximately 200/s, timers approximately 10/s, mutex operations
  successful, zero error count. Free heap was 9200/9192 bytes respectively, specific
  to these test workloads.
- A dedicated task updated the onboard LCD using its board driver at about 50/s,
  with a sampled maximum frame gap of 20 ms. This is neither LVGL validation nor
  panel scan-rate measurement. The user confirmed the CMSIS-V2 display was perfect.
- USART2 round-trip passed. SD, EEPROM, Ethernet and USB were not accessed.
- Explicit board ports were required: UART pins, DWT delay, NVIC grouping and
  volatile LCD MMIO. The CMSIS-V2 fixture additionally upgraded the whole Core
  header set. These are not evidence of automatic BSP generation.

Vendor sources are not relicensed to MIT. Unapproved SDKs, device identifiers,
raw logs and local machine paths stay outside the public repository. Further
component combinations and other vendors require separate adapter tests.

Additional RT-Thread board run on the same date: independent SPL fixture, full AC5
build with 0 errors and 8 warnings, backup/program/verify/reset. 33 samples over about
32 seconds: message queue/mailbox/semaphore/event pipeline at 200/s, soft timer about
10/s, zero errors, 9736 bytes free heap. LCD driver calls about 50/s, maximum gap 20 ms.
**No new visual acceptance while the user was away.** UART round-trip passed; the
previously accepted CMSIS-V2 firmware was then restored and explicitly reset.

Combined RT-Thread + LittleFS 2.11.3 + CMSIS-DSP (from CMSIS_5 5.9.0) + peripheral locks:

- RAM-simulated NOR, 512 bytes x 32 blocks: **not a physical Flash/SD driver or
  power-loss test**. Two workers repeatedly created/wrote/closed/read their own files,
  with byte comparisons: 3606/3605 completed cycles, zero errors.
- Deliberately held the peripheral mutex across scheduling: 3143 immediate-lock
  failures followed by successful waits; no simultaneous critical-section owner.
- 7212 known-answer sets for f32 dot product/mean and q15 dot product, zero errors.
- About 32 seconds, 5024 bytes free heap; original IPC/timer/LCD driver tasks remained
  active. Some wireless UART lines lost characters and were excluded, not repaired
  into passing samples. 28 complete base and 31 complete combination samples showed
  growing counters and zero cumulative errors. UART query passed; accepted firmware
  was restored, verified and reset afterward.
- The first combined build exposed missing FastMath dependencies from Statistics.
  Keep that failed evidence. Selecting FastMath/CommonTables produced a full build
  with 0 errors and 8 warnings. Dependency preflight and regressions now cover this;
  assertions were not disabled to pass.

After the user returned on the same date, the identical RT-Thread combination image
was programmed, verified and reset again. The 32-second runtime check and UART query
passed again. The user confirmed the display and animation were normal. This visual
acceptance covers that onboard LCD workload only.

A separate raw-touch test image then captured the user's presses and horizontal/
vertical drags; the user confirmed changing coordinate digits. Eight complete UART
records showed 1134 accumulated valid samples, 2 rejected samples, 33 press and 33
release transitions, and a final released state. RTOS/combination error counters
remained zero with 5024 bytes free heap. Transitions may include contact bounce:
**these are not physical click counts or debounce acceptance**. This only validates
raw acquisition on this board, not calibration, positional accuracy or LVGL input.
Calibration EEPROM was neither read nor written.

A subsequent RAM-only calibration fixture passed: the user completed five targets
and confirmed `CAL OK`. Three points fit an affine transform; independent checks
at the other two points had recomputed errors of about 4.54 and 7.38 pixels (12-pixel
test limit). These include manual targeting error and **do not guarantee full-panel
accuracy**. Valid-sample press detection, release debounce and stable-hold sampling
were added; host state-machine tests cover bounce, early release, invalid ADC data
and retry after failed verification. During about 33 seconds of combined board
testing, error counters stayed zero, LCD updates averaged about 50/s with a 21 ms
maximum gap, and free heap was 5024 bytes. Background IPC, RAM filesystem and DSP
workloads continued during interaction. Parameters are volatile, with no EEPROM
writes. **LVGL widget interaction is still unverified**; this private calibration
fixture is not a generic automatically generated BSP.

### Subsequent LVGL widget integration (same date)

A fresh SPL + RT-Thread fixture used the actual tool to install LVGL 8.4, followed
by private board-specific synchronous FSMC RGB565 flush and calibrated touch code.
Only one thread calls LVGL; RAM LittleFS, DSP, IPC, timers and peripheral mutex tests
run concurrently. No HAL, external SRAM or physical filesystem access is involved.

- The first interactive build failed a background lock acquisition after its 100 ms
  timeout while another lower-priority worker held the mutex. Failed firmware and
  debugger evidence were retained; LVGL assertions and Cortex fault status were clear.
  Prioritizing bounded background work above the GUI reduced maximum lock wait/hold
  to 3 ms, without relaxing timeouts or disabling assertions.
- The second O0 build looked normal to the user but failed the original 30 ms GUI
  budget with a 32 ms maximum. The tool's O2 setting produced a full build with zero
  errors and 27 upstream/preexisting warnings, retaining the same UI and thresholds.
- The O2 image passed about 33 seconds of combined checks and subsequent interaction
  capture: GUI task about 50 calls/s, maximum handler/loop interval 23 ms (not panel
  scan rate). Background, LVGL flush and assertion error counts remained zero.
- The user confirmed three accurate clicks per A/B button and normal animation.
  Complete UART records confirmed A=3, B=3 and slider range 0..100 with 385 value
  changes; the user separately confirmed reaching zero and releasing normally.
- RT-Thread free heap was 10656 bytes; minimum LVGL free heap in the initial run was
  24580 bytes. Fixture settings were 24 KiB RT-Thread heap, 4 KiB GUI stack and 32 KiB
  LVGL heap: not universal application defaults.

This covers LVGL v8 widget interaction on this board under the specified workload,
not rapid-tap accuracy, long-duration endurance, LVGL v9 or arbitrary displays.
Board calibration values were not put into generic templates. These development
results do not imply an updated RC6 EXE.

### Telemetry integrity

Wireless UART can drop digits while leaving a syntactically valid record, not just
lose whole lines. Subsequent combined tests therefore append a board-side CRC32 to
each line. The host parses only verified records and retains both raw logs and
rejected lines; it never repairs missing characters into passing evidence. Reset
tests separate generations using a CRC-valid boot marker and reject repeated boots
or backwards time, preventing buffered pre-reset records from being mixed with a
new run. This is test-firmware instrumentation, not a logging protocol automatically
injected into users' applications.

The CRC-instrumented RT-Thread combination also passed five minutes of continuous
running and three software resets (45 seconds captured per reset). Base errors,
LVGL assertions and background lock failures remained zero, with about 50 GUI
calls/s. The continuous phase measured a 22 ms maximum loop gap and 6 ms LVGL
handler time, constant 10656-byte RTOS free heap and minimum 25116-byte LVGL free
heap. It accepted 1361 CRC-valid records and rejected 70; reset phases accepted
612 and rejected 28 in total. Each phase passed the UART query. This is short-run
and warm-reset validation, not power-loss or long-duration endurance testing.

### FreeRTOS + CMSIS-V2 comparison (same date)

Another independent SPL fixture used the actual tool to install LVGL 8.4, RAM
LittleFS, DSP and peripheral guards, with the same board-specific LCD/touch code.
It uses CMSIS-V2 task, queue, semaphore, event, mutex and timer APIs. The AC5 O2
full build had zero errors and 25 preexisting/upstream warnings. After verified
flashing and reset, 32 seconds of valid samples measured about 200 queue transfers/s,
10 timer callbacks/s and 50 GUI calls/s, with a 22 ms maximum loop gap, 16784 bytes
of FreeRTOS free heap and zero errors; UART querying passed. The user confirmed
normal interaction. Ten later complete CRC-valid records consistently confirmed
A=3, B=3 and slider range 0..100, with 21 ms maximum LVGL handler time, 25076 bytes
of LVGL free heap and maximum lock wait/hold of 3/4 ms without failures. Fixture
settings are 32 KiB FreeRTOS heap, 4 KiB GUI stack and 32 KiB LVGL heap. No physical
storage was accessed. This is a separate CMSIS-V2 board result, not an extrapolation
from the RT-Thread build.

After fixing guard millisecond/tick conversion, a new full build was flashed,
verified and reset. The same CMSIS-V2 load passed five minutes continuously and
three software resets. The continuous phase measured a maximum 22 ms GUI loop gap,
6 ms handler time, 16784/25116 bytes of RTOS/LVGL free heap and zero errors. Workers
completed 39754/39753 RAM file cycles and 79509 DSP checks. Continuous CRC accepted/
rejected counts were 1330/105; reset phases totaled 588/47. Every phase passed UART
querying. Board ticks remained at 1000 Hz; non-1000 Hz conversion boundaries were
tested by executing generated C on the host, not claimed as other-rate board tests.
