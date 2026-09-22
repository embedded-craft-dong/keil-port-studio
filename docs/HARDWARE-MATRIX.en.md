# Hardware validation matrix

[简体中文](HARDWARE-MATRIX.zh-CN.md)

Updated 2026-09-22. Platform: Puzhong STM32F407ZGT6, ARMCC 5.06 update 7,
LAN8720, 16 MiB SPI NOR, approximately 8 GB SD, external SPI LCD, USB FS Device.
UART2 uses a wireless serial bridge. Every programming operation was verified and
followed by reset. User baseline projects were preserved. Board adapters are explicit,
not inferred wiring or universal generated drivers.

## September 22 USB Host addendum (unreleased working-tree fixes, not frozen RC3)

USB3 hosts a real 16,122,970,112-byte FAT32 thumb drive with 512-byte sectors.
USB2 is the sole power source; USB1 remains empty. Tests create dedicated new
files only: no formatting, filesystem repair or host-PC disk access.

| Check | Result and boundary |
| --- | --- |
| AC5 / TinyUSB 0.18 bare-metal Host | Enumeration, INQUIRY and sector0 read passed across 3 starts |
| FreeRTOS / RT-Thread Host | Both passed enumeration, INQUIRY and sector0 read |
| Host + FatFS, bare metal / FreeRTOS / RT-Thread | Each created, wrote, patched across sectors, appended, synced, renamed and remounted; all 66,153 bytes matched, FNV32 DD7D393E |
| File retention after reset | All three modes used separately flashed read-only verification firmware; full readback, zero sector writes |
| User-performed complete power loss | User confirmed all connections removed simultaneously for at least five seconds; after restoring wiring, the existing FreeRTOS verifier checked all 66,153 bytes with zero writes, no reflash or format |
| FreeRTOS diagnostics | Stack check level2 and malloc-failure hook enabled; write test passed with 1,024 unused application-stack bytes and 10,904 free heap bytes; fixture-specific budget |
| Fresh automatic generation | AC5 Host selected 0.18 and generated a real HAL timebase; fresh project passed file verification on board |
| AC6 6.22 / TinyUSB 0.21 | Bare-metal enumeration, INQUIRY and sector0 read passed; not AC6 acceptance of all components |
| Software VBUS power cycling | **Failed**: the original Host test saw no disconnect five seconds after PA15 went low. Subsequent isolated GPIO/multimeter checks measured PA15 at 0.01V low and 3.22V high, but unloaded USB3 remained around 4V in both states. No effective shutoff observed; root cause unresolved, not a physical power-cycle pass |

Power diagnostics used USB2 as the sole supply, USB1/USB3 empty and debug-only
ST-Link, with no firmware or storage writes. GPIO mode and input/output readback
were checked before and after measurements: board 5V/USB3 measured 4.39V/4.05V
in the high state and 4.33V/3.97V in the low state. A separate physical PA15-high
measurement was 3.22V with USB3 at 3.93V. These are user-reported multimeter
readings; the nominal board 5V rail was also low. Supply drop, switch circuitry,
alternate power paths and board-revision differences remain unresolved; neither
a damaged MOSFET nor a TinyUSB software defect has been established. The first
low-state reading was excluded because configuration had reverted; subsequent
state-retaining checks were used instead. Original evidence is retained.

This is extended board-level USB Host power-switching/fault-recovery acceptance,
separate from the passing thumb-drive file I/O tests. At the test participant's
request, further component-level diagnosis has stopped; no desoldering or further
small-component probing is required. Public status remains "tested on this board,
failed, root cause unresolved"; other boards/power-switch configurations are
unverified. Contributors with suitable hardware and measurement equipment are
welcome to reproduce and add evidence. This check is not labelled "never tested".

The drive was physically replugged via the PC before communication recovered;
do not attribute recovery exclusively to software changes. All failed runs and
wireless serial truncation remain recorded. Two real FreeRTOS assertions exposed
USB interrupts before scheduler start and an invalid fixture IRQ priority. The
generator now initializes RTOS USB inside its running owner task; the adapter
derives IRQ priority from the actual kernel threshold. USB Device regression of
the new template has now been independently completed after rewiring, as described
below. Earlier historical results are retained, not substituted for new-firmware tests.

Additional fixes cover AC5 DWC2 MMIO access, one-shot RX status popping, overlapping
Host FIFOs, role-specific SDK selection, millisecond clocks and missing controller
drivers. Eleven TinyUSB regression cases and all 18 software test scripts passed.
Three upstream AC5 warnings remain visible in the build logs.

## September 22 new USB Device initialization regression

USB1 supplies both PC data and the only board power; USB2/USB3 are empty and
ST-Link supplies no 5V. Fresh projects use the updated RTOS owner-task startup.
MSC is a 64 KiB board-RAM backend, never a physical thumb drive, SD or NOR.
Every file-test round checks disk model, serial, USB bus, capacity and identity
before writing a newly created test file.

| Check | FreeRTOS | RT-Thread |
| --- | --- | --- |
| CDC + MSC, 3 reset/start rounds | Every round: 14 lengths, 1,121,247 exact echo bytes; passed | Same; passed |
| MSC file checks in each round | 16 KiB random file, 1,031-byte overwrite at offset507, sync, uncached readback, rename/delete; passed | Same; passed |
| 30 s concurrent CDC/MSC | 12,099,584 exact echo bytes alongside 17 MSC rounds; passed | 11,311,104 exact echo bytes alongside 18 MSC rounds; passed |
| CDC + HID | 1,121,247 echo bytes passed; Windows CDC/HID/keyboard interfaces enumerated normally | Same; passed |

No actual HID key reports were sent: enumeration is not keyboard-input acceptance.
Mounted heartbeats continued during load without reported assertions. Only files
created by the test were deleted. The fixture also initializes its RAM backend
before exposing USB interfaces, avoiding a higher-priority USB task racing the
application's initialization. Assertions and strict byte comparisons were retained.

## Executed

| Test | Mode | Result |
| --- | --- | --- |
| FreeRTOS/CMSIS2 | Assertions enabled | 3 resets x 95 checks: tasks, IPC, timers, heap reclamation |
| RT-Thread 5.2.2 | Assertions enabled | 3 resets x 23 checks: queues, floating-point context, timebases |
| LVGL 8.4 + DSP + RTT | RT-Thread | 3 resets x 19 checks; user confirmed no visible defects; RTT buffer writes, not a new Viewer session |
| LittleFS + FatFS | Bare metal | 32 sequential rounds, 22 checks; 13 retention checks after each of two resets |
| LittleFS + FatFS + peripheral locks | RT-Thread | 32 dual-thread rounds, 23 checks; 13 checks after each of two resets |
| LittleFS + FatFS + peripheral locks | FreeRTOS/CMSIS2 | SDIO DMA, 32 dual-thread rounds, 23 checks; 13 read-only retention checks after each of two resets and one user-performed full power cycle |
| LwIP 2.2.1 + LAN8720 | RT-Thread | 12 pings, 80 UDP packets, 13,116 exact TCP bytes; 3 software PHY down/up cycles; 60 s, 12,318 UDP packets and 25,214,976 exact TCP bytes |
| LwIP 2.2.1 + LAN8720 | FreeRTOS | Same baseline checks; 3 PHY down/up cycles; 60 s, 12,582 UDP packets and 25,755,648 exact TCP bytes |
| TinyUSB CDC | RT-Thread and FreeRTOS | Each: 14 lengths, 1,121,247 byte-exact USB echo bytes |
| TinyUSB CDC + MSC | Bare metal and FreeRTOS | Each: CDC byte checks plus a 16 KiB random file, cross-sector overwrite, rename/delete and uncached readback on the 64 KiB RAM disk |
| TinyUSB CDC + HID | Bare metal | CDC checks passed; Windows HID/keyboard class enumerated correctly; no real key reports sent, so this is not input-function acceptance |
| TinyUSB CDC + MSC + HID | F407 USB FS | Correctly rejected before migration: 4 non-control IN endpoints required, only 3 available |

Device MSC above accesses only the firmware's RAM disk; the new Host campaign uses
the physical thumb drive, never host-PC disks. Storage campaigns
format the first 512 KiB NOR partition and overwrite SD `RTT0.BIN`/`RTT1.BIN`, without
formatting the SD card. The files remain for retention checks. Networking uses the
existing dedicated wired static-address link, without changing Wi-Fi, proxy, firewall
or Internet-sharing configuration.

## Findings and corrections

- Concurrent FreeRTOS storage exposed SDIO polling RX FIFO overrun (`0x20`). The
  board adapter now uses DMA, an aligned SRAM bounce buffer and bounded waits.
  An initial DMA attempt also left a tail incomplete; peripheral flow control and
  alignment were corrected before the passing run. This is not a generic SD driver claim.
- A test worker translated from RT-Thread returned directly. Debugger reads identified
  `port.c:236`, `prvTaskExitError`. Explicit `osThreadExit()` fixed the fixture. The
  generated default persistent task already loops.
- Bare-metal HIL failed with the baseline 1 KiB main stack and passed with 8 KiB.
  That is a fixture budget, not a universal project default.
- The wireless bridge loses leading characters on a particular SD log line. Preserve
  raw logs, replay retained board check records, buffer complete host lines and require
  all check numbers. A summary alone cannot pass. Do not equate log loss with disk corruption.
- Initial RT-Thread networking had one host-side ping transmit failure. Keep the
  failed run; reset recheck and stress passed. PHY software cycling is not cable unplugging.
- One USB runner attempted to open a serial port still owned by the network runner;
  no programming took place. A serialized retry passed. Preparation-script errors are
  retained separately and are not hardware passes.

Generated comments and both post-porting guides now explain task exit, SDIO timing,
DMA memory and stack budgeting. Assertions were not disabled to obtain a pass.
Third-party AC5 warnings remain visible in build logs.

## Remaining requirements, not one universal checkbox

Changing MCU, driver, compiler, RTOS, SDK version or memory layout changes interrupt,
DMA/cache and stack assumptions. Passing configurations do not prove all combinations.

| Item | Requirement |
| --- | --- |
| Extended USB Host / actual HID input | Basic MSC and physical power-loss retention passed; measured VBUS shutoff failed with root cause unresolved; other drives/hubs/HID require their own acceptance |
| IPv6, DHCP, MQTT, TLS | Dedicated service fixtures, test firmware and protocol-level acceptance; this round covers static IPv4, ICMP, UDP/TCP and OS ports |
| AC6, LVGL v9, more trimming combinations | Matching SDK/toolchain and independent build/runtime cases |
| Other MCUs, flash devices, displays | Matching hardware, wiring and adapters; F407 cannot validate H7 DCache or M33 TrustZone behavior |
| Windows without Python/Git | A clean machine or isolated VM; restricting PATH on this host is not equivalent |
| MDK6/CMSIS Solution, RTE/Pack, `.ioc` synchronization | Not implemented capabilities, outside supported-feature acceptance |

Raw evidence stays local; public documents omit user paths, chip UIDs and probe serials.
