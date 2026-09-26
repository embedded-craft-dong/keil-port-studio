# Device drivers and reference removal

[简体中文](DRIVERS.zh-CN.md)

These are `2.3.0-dev2` development features, not part of the published RC6 Windows package.
Existing board/middleware results do not validate these newly generated device drivers.

## Remove files and include paths

Select a Keil project and Target, close the project in Keil, then open
**Project tools → Remove files and paths…**. Filter by path/group/Target and check exact entries.
Nothing is selected initially. File references and project-level Include entries are independent.
The tool does not automatically remove a header directory when removing a source, remove
descendants of an Include path, or change per-file compiler settings.

Choose **Preview removal…**, inspect the Target/XML changes and confirm. Cancellation is read-only.
Reload and compile in Keil afterward. **Safety and recovery → Roll back latest transaction** can restore the change.
Source/header/library files remain on disk. Removing an Include may break remaining code;
the tool does not infer all preprocessor dependencies. Filtered-out checked entries remain selected,
and the total count explicitly includes them.

Managed component/driver references cannot be removed piecemeal: use component uninstall.
Ordinary added files can be removed individually; their per-Target ownership is updated so
regeneration recovery will not re-add intentionally removed entries.

```powershell
python keil_port_tool.py Demo.uvprojx --target Debug --remove-file '..\User\old.c' --remove-include '..\OldInclude' --dry-run
python keil_port_tool.py Demo.uvprojx --target Debug --remove-file '..\User\old.c' --remove-include '..\OldInclude' --yes
```

Repeat either parameter for multiple paths. Relative paths are based on the `.uvprojx` directory,
not the shell working directory. Unknown paths fail; there is no fuzzy filename deletion.
Removal runs separately from installation/uninstallation/health checks. Use `--diff-file preview.txt` to export the preview.

## Generate drivers

Open **Project tools → Device driver generator…**, uncheck unwanted models, choose transports,
and preview generation. Files go into project-private `KPS/DeviceDrivers/` and selected Keil
Targets. No `main.c`, clock, pin, interrupt or `.ioc` edits are made.

| Option | Explicit first-version scope |
| --- | --- |
| W25Q128JV | 16 MiB SPI NOR, 256-byte pages, 4 KiB sectors; probe/read/page program/sector erase/verification |
| 24LC02B | 256-byte I2C EEPROM, 8-byte pages, one address byte |
| 24LC256 | 32 KiB I2C EEPROM, 64-byte pages, two address bytes |
| SHT3x-DIS | SHT30/31/35 single-shot temperature/humidity with both CRC checks |
| BH1750FVI | Single-shot high-resolution light measurement, explicit MTreg=69 |
| ADS1115 | Four single-ended inputs, 128 SPS, +/-4.096 V PGA, microvolt output; not ADS1015 |
| SSD1306 | I2C 128x32/64 internal-charge-pump OLED, page framebuffer; not SH1106, no font assets |

The generator does not guess all W25Q/AT24 geometries or support NAND/internal MCU flash,
nor automatically bind storage to FatFS/LittleFS. Generated bilingual `DRIVER_GUIDE.md`
contains model-specific setup, units, original manufacturer datasheet links and acceptance steps.

### What users fill in

- `kps_board_port.c`: search **TODO**, implement transfer, delay, lock and unlock callbacks.
- `kps_board_port.h`: persistent context carrying your HAL/SPL/BSP handles.
- Software I2C: fill open-drain GPIO, GPIO reads and calibrated microsecond delay in `kps_soft_i2c.h`.
- DMA: implement start, final completion wait and abort/quiesce in `kps_dma.h`; allocate persistent DMA-safe buffers.
- `kps_devices.h`: public APIs. Initialize your hardware first, call explicitly and check every result.

Unimplemented callbacks return `KPS_ENOSYS`, never success. Protocol code has no HAL/SPL/RTOS
dependency, but that does not configure your MCU peripherals. All I2C API addresses are 7-bit;
shift once inside a HAL adapter only if its particular API requires it.

### Transport/performance choices

| Mode | Suitable for | Cost and board work |
| --- | --- | --- |
| `hardware` | Initial bring-up and short messages | Blocking hardware callbacks with finite timeout; simplest to debug |
| `software` (I2C only) | No spare I2C controller, low-speed sensors | Open-drain bit-banging with ACK/repeated START/stretch timeout; CPU-intensive, no multi-master arbitration |
| `dma` | Longer transfers and reduced CPU copying | DMA/IRQ/completion/cache configuration required; short transfers may be slower |

All public device APIs **block until completion**, including DMA. An RTOS wait callback can
sleep on an event; bare metal can poll with a finite timeout. Software I2C half-period is at
least 5us; GPIO and scheduling overhead reduce actual speed. No software SPI, fully asynchronous
device state machine or measured speedup is promised.

DMA uses separate persistent bounce buffers. Every start/wait error attempts abort; failed
quiescence quarantines the link and refuses further transfers. Never free/reuse its memory or
clear quarantine until the peripheral/DMA/IRQs are definitely stopped. Avoid F4 CCM and handle
cache coherence on cached MCUs. Never hand expired stack buffers to a running DMA channel.

```powershell
python keil_port_tool.py Demo.uvprojx --driver-port generic --driver w25q128jv --driver 24lc256 --driver sht3x --driver-i2c hardware --driver-spi dma --dry-run
python keil_port_tool.py Demo.uvprojx --driver-port generic --driver w25q128jv --driver 24lc256 --driver sht3x --driver-i2c hardware --driver-spi dma --yes
python keil_port_tool.py Demo.uvprojx --uninstall device_drivers
```

Identical reruns preserve existing files and user edits. A changed model set or transport is
rejected instead of overwriting the old pack. Back up user ports, uninstall, retain protected
modified files safely, then regenerate into a deliberately clean destination. One pack per project;
multiple identical devices reuse APIs with different contexts/addresses. If CubeMX regeneration
removes references, use health check/integration recovery; the tool does not modify `.ioc`.

## Validation boundaries

### Automatic STM32 binding

Current source supports STM32F1/F4 + USE_HAL_DRIVER / USE_STDPERIPH_DRIVER; the GUI defaults to
`auto`. Select one Target. A unique initialized hardware bus is preselected; choose
an instance when ambiguous. Enter the actual active-low SPI CS or software-I2C
SCL/SDA GPIOs (e.g. PB0/PB6/PB7). Never reuse occupied pins; SWD PA13/PA14 is rejected.
Automatic binding fills HAL/SPL hardware I2C/SPI and software-I2C APIs, delays,
GPIO and concurrent-call protection. It is NOT an all-STM32-family implementation.
F1 support is included in dev2 source and Windows packages; the old dev1 EXE does not include it.
F1 SPL uses APB2 GPIO clocks and Out_PP/Out_OD modes, with local compatibility for
old CMSIS missing DWT definitions. Existing AFIO remapping and hardware bus setup
are preserved. F1 also reserves JTAG PA15/PB3/PB4 and accepts GPIO ports A-G only;
check actual package availability. Use generic for deliberate debug-pin reuse.

Run existing clock/hardware-bus initialization first, include `kps_stm32_port.h`,
call and check `kps_stm32_init()`, then obtain `kps_bus bus=kps_stm32_bus()`.
That init configures selected CS/software-I2C pins and enables DWT (preserving the
counter), not SysTick. Software I2C requires external pull-ups. Device calls remain
explicit and storage is never automatically erased. Main/task context only, IRQs
enabled. DWT delays busy-wait; concurrent callers are rejected, not interleaved.
External bus users must participate in ownership. Generic TODO guidance applies
only to generic packs; automatic packs have a dedicated guide at the top.

Automatic DMA binding is **not implemented/validated** and is explicitly rejected,
never silently downgraded. Use `--driver-port generic` for manual DMA hooks or
unsupported/unknown frameworks. CLI defaults to auto, so add that generic option
to the earlier DMA examples. Reopening the generator restores pack settings.
Uninstall requires all installed Targets to protect shared source files. Files
changed during preview stop the transaction rather than being overwritten.

```powershell
python keil_port_tool.py Demo.uvprojx --target Debug --driver sht3x --driver-port auto --driver-i2c-instance hi2c1 --yes
python keil_port_tool.py Demo.uvprojx --target Debug --driver w25q128jv --driver-port auto --driver-spi-instance SPI1 --driver-cs PB0 --yes
python keil_port_tool.py Demo.uvprojx --target Debug --driver sht3x --driver-port auto --driver-i2c software --driver-scl PB6 --driver-sda PB7 --yes
```

`test_driver_stm32.py` checks detection/ambiguity, executes generated HAL/SPL APIs
against mocks (including legacy I2C 1/2/3-byte receive sequencing and SPI CS error
release), and compiles against real F4 HAL/SPL SDK headers with AC5 when available.
This does not certify electrical wiring or physical sensor behavior.
`test_driver_stm32f1.py` reuses API execution checks and checks F1 GPIO/debug-pin
protection, repeat generation and uninstall. With `KPS_DRIVER_F1_HAL_SDK` (Cube
project root), `KPS_DRIVER_F1_SPL_SDK` (full SPL package root) and `ARMCC`, it compiles
all seven device drivers and links real vendor implementations at Cortex-M3/C99/O2.
The matrix covers HAL STM32F103xB/xE and STM32F107xC, SPL STM32F10X_MD/HD/CL,
each with hardware/software I2C plus SPI. Link-only images are never flashed.
Unavailable SDK/compiler tests are explicit skips. This does not cover every F1
part or SDK version, and no F1 physical acceptance has been performed.

`tests/test_device_drivers.py` compiles and executes emitted C against protocol models for page
boundaries, ranges, write protection, readback, CRC, conversions, timeout, lock release, software
I2C and DMA abort quarantine. It also compiles individual models/all transport combinations.
When AC5 is available, Cortex-M4/C99/O2 compile checks run. Missing compilers are explicit skips,
not acceptance evidence. `tests/test_reference_actions.py` covers Target isolation, ownership,
cancel/dry-run, concurrent project changes and byte-exact rollback. GUI callback tests use real
temporary projects; these are not manual mouse acceptance.

**No physical acceptance or DMA performance measurement for this new device batch yet.**
Start with read-only identification, then test authorized storage regions, disconnected-bus
timeouts and power-cycle retention. Do not mark unavailable hardware as tested. Generated
code is MIT; manufacturer PDFs are linked, not redistributed.
