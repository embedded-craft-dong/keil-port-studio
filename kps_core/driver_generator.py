"""Planning and bilingual guidance for project-private driver generation."""
import json
from pathlib import Path

from .device_drivers import CATALOG, I2C_MODES, SPI_MODES, render_devices
from .driver_transport import render_port
from .driver_stm32 import binding, render_stm32
from .errors import ToolError

LICENSE = '''MIT License

Copyright (c) 2026 Keil Port Studio contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
'''


def guide(selected, i2c_mode, spi_mode):
    return '''# Board porting checklist / 板级移植指引

These are original MIT-licensed protocol drivers, not vendor HAL drivers.
No pin/clock/DMA channel/ISR is guessed. No code is inserted into main().
这是与芯片库无关的协议驱动，不自动配置引脚/时钟/中断，也不自动读取、擦除或格式化。
Validation boundary: host protocol tests, NOT physical sensor acceptance.
验证边界：主机协议模拟测试，不代表对应传感器/模块已经实板验收。

## 1. Selected models / 已选型号

''' + '\n'.join('- %s: %s. [Original manufacturer datasheet](%s)' % CATALOG[k] for k in selected) + '''

## 2. Transport choice / 通信方式

I2C: **%s**; SPI: **%s**.

| Mode | Work to complete / 用户需要填写 | Trade-off / 性能边界 |
| --- | --- | --- |
| hardware | kps_board_port.c: board_i2c / board_spi, board_delay, board_lock/unlock | Blocking hardware; simplest, short transfers often fastest. 硬件阻塞，适合短报文。 |
| software (I2C) | context.i2c_soft: scl/sda open-drain release, read_scl/read_sda, delay_us, half_period_us>=5 | <=100kHz before GPIO overhead; ACK/stretch/STOP handling generated. No multimaster/arbitration or automatic stuck-bus recovery. 软件模拟，CPU占用高，支持时钟拉伸超时。 |
| dma | context.i2c_dma / spi_dma: persistent buffers, start, wait, abort_quiesce, user; also delay/lock/unlock | Completion-waiting API, CPU offload, NOT fire-and-forget and NOT necessarily faster. DMA配置、IRQ、完成通知仍需板级填写。 |

Start with hardware blocking to verify wiring, then change transport in a separate
test project. Protocol functions are identical. Profiles cannot be silently changed
over user-edited ports. Export/backup your changes before uninstall/regenerate.
先硬件阻塞验证接线，再在工程副本评估 DMA。不能将旧版用户端口静默覆盖。

### Required callback rules / 必须遵守的接口约定

1. Configure the correct IO voltage, clocks, alternate functions, pull-ups and CS.
   Read the exact module schematic; MCU 3.3V does NOT make every peripheral 3.3V-safe.
   核对电压和模块原理图；软件 I2C 只能开漏拉低/释放，不能推挽输出高电平。
2. I2C addresses in this API are **7-bit**. Shift once in your HAL callback only if
   that HAL API requires it. tx+rx means a repeated START, not STOP+START.
   地址统一为7位，读寄存器/EEPROM采用重复起始；错误必须返回负值。
3. All transfers are synchronous at this boundary. Keep finite timeouts and return
   KPS_OK only after **all requested bytes** completed; partial transfer is an error.
   不得用“已启动 DMA”作为成功完成。不要使用无限等待。
4. The driver locks the whole operation, including delays and page writes. Bind a
   common mutex across all devices on that physical bus. No recursive locking in
   transport hooks, no ISR use, no whole-operation interrupt disabling. Bare metal
   single owner may implement lock as explicit OK and unlock as no-op.
   一个总线共用一把锁；阻塞操作只在任务/主循环执行。高并发应用可自行改为状态机。
5. No heap is used. Largest protocol local buffer is about 256 bytes, plus your
   transport/library stack. Size task stacks using measured high-water marks.
   不动态分配内存；仍应测量任务栈，不要仅凭能编译就认定栈足够。

### DMA safety / DMA 安全

- Allocate separate TX/RX buffers of at least 288 bytes for EACH DMA link, with
  permanent lifetime, required alignment and DMA-accessible RAM (not F4 CCM).
  Zero-initialize the context once at startup. Do not use automatic stack buffers.
- Example allocation on Keil AC5: `__align(32) static uint8_t tx[288], rx[288];`.
  On GCC/AC6 use the appropriate aligned attribute. For M7/cache-enabled devices,
  configure a non-cacheable region or maintain cache correctly in start/wait/abort.
  不同链路禁止复用缓冲区，不允许缓冲区相互重叠。
- `start` launches the WHOLE logical transaction; for I2C it includes repeated START
  and for SPI it holds CS across command/data. You may implement it as an IRQ state
  machine. `wait` waits for the final bus idle/STOP/CS release, not an intermediate IRQ.
- On every start/wait error the adapter calls `abort_quiesce`. If abort fails, the
  link is quarantined and refuses all further transfers. Its bounce memory stays
  alive; stale data is never copied to the caller. Clear quarantine only after
  resetting the peripheral/DMA and guaranteeing all IRQ/callback accesses stopped.
  超时不释放仍被 DMA 使用的内存；中止失败后隔离链路，不准直接重试覆盖缓冲区。
- Hook stubs deliberately fail (KPS_ENOSYS). This is a board-integration skeleton,
  not a configured STM32 DMA channel or measured throughput claim. Measure CPU,
  latency and bus waveforms on YOUR hardware before claiming an improvement.

## 3. Call sequence / 使用步骤

```c
#include "kps_board_port.h"
#include "kps_devices.h"
static kps_board_context port; /* persistent; configure selected hooks here */
/* After clocks/GPIO/bus init and, if used, scheduler/mutex init: */
/* kps_bus bus = kps_board_bus(&port); */
/* Example only if SHT3x was selected:
   int32_t t; uint32_t h;
   int rc = kps_sht3x_read(&bus, 0x44, &t, &h);
   if (rc == KPS_OK) { // t in milli-degrees C, h in milli-percent RH
       // display/store values
   } else { // log rc; do not use stale values
   }
*/
```

First locate every `TODO` in kps_board_port.c and the selected transport header.
Unimplemented ports never report success. Existing generated files are preserved
on identical reruns, including your edits. Reopen Keil and build after generation.
先搜索 TODO 填好，再显式调用；重复生成保留用户修改，不自动向 main.c 插入调用。

## 4. Device-specific setup and acceptance / 每类器件还需要做什么

- **W25Q128JV**: standard SPI mode 0 or 3, MSB first, start with a conservative clock.
  Explicit probe expects EF 40 18, not arbitrary JEDEC-compatible devices. Call
  `kps_w25q_probe` then read first. Wake from power-down/reset per board policy.
  `program` splits 256-byte pages, rejects 0->1 transitions and verifies results.
  `erase4k` requires 4096 alignment and verifies erased bytes. These two calls are
  destructive, never auto-run. No chip erase, protection-register writes, QSPI,
  NAND, internal MCU flash or >16MiB/4-byte-address support. Errors may leave earlier
  pages written; use a filesystem/journal for atomicity and wear management.
  只支持该明确型号；先读ID，再对授权测试分区写入，禁止擦到程序或用户分区。
- **24LC02B/24LC256**: pass model 2 or 256, address 0x50..0x57 as appropriate for
  the exact chip/module. 24LC02B ignores A0..A2; normally use 0x50. Configure WP.
  Read/write offset is byte-based, never bit-based. Writes split 8/64-byte pages,
  wait 6ms per page, read back and verify (including write-protect suppression).
  NO bank-address guessing for 24C04/08/16 or other 24Cxx variants. Test cross-page
  writes only in an authorized region; no automatic erase or wear levelling.
- **SHT3x-DIS**: address 0x44/0x45, after power-up settling. Single-shot high
  repeatability, 16ms wait, both CRCs checked. Values: milli-C and milli-percent RH.
  Compare with a reference instrument; no calibration/heater/periodic mode setup.
- **BH1750FVI**: address 0x23/0x5c. One-shot H-resolution, explicit MTreg=69, 180ms
  wait, result millilux. Optical window transmission may need application calibration.
- **ADS1115**: address 0x48..0x4b, channel 0..3 relative to GND, 128 SPS single shot,
  +/-4.096V PGA, signed result in microvolts (125uV/LSB). This full-scale range does
  NOT authorize input voltage outside device supply rails. No ALERT IRQ/continuous
  mode. Compare ground and a safe known input; readout error/timeout must be handled.
- **SSD1306**: actual SSD1306, 128x32 or 128x64, I2C 0x3c/0x3d, internal charge pump.
  Apply panel reset/power sequence FIRST. `init` then `flush` a zeroed 512/1024-byte
  framebuffer to clear uninitialized display RAM. Buffer page layout:
  `pixels[(y/8)*128+x] |= 1u << (y%%8)`. Init mirror is A1/C8; adjust for your panel.
  No font assets or SH1106 column-offset guessing; not an LVGL display-port generator.

For each device: read/probe -> invalid address/unplug timeout -> normal operation ->
RTOS mutex concurrency (if used) -> DMA completion/abort faults (if used). Storage:
cross-page/boundary/write-protect/readback/power-cycle tests. Record model, wiring,
clock, firmware version and results. Host mocks cannot verify real signal integrity.
按上述顺序逐项验收，保留实物型号/接线/频率/版本；模拟测试不能替代实板电气验证。
''' % (i2c_mode, spi_mode)


def render_pack(selected, i2c_mode='hardware', spi_mode='hardware', board=None):
    selected = sorted(set(selected))
    if i2c_mode not in I2C_MODES or spi_mode not in SPI_MODES:
        raise ToolError('通信模式无效 / Invalid transport mode')
    files = render_devices(selected)
    files.update(render_port(i2c_mode, spi_mode))
    if board:
        files.update(render_stm32(board))
    files['DRIVER_GUIDE.md'] = guide(selected, i2c_mode, spi_mode)
    files['LICENSE'] = LICENSE
    files['driver-pack.json'] = json.dumps(dict(schema=1, drivers=selected, i2c=i2c_mode,
        spi=spi_mode, validation='host-protocol-tests; no physical acceptance'), indent=2)+'\n'
    if board:
        record=json.loads(files['driver-pack.json']); record['board']=board
        files['driver-pack.json']=json.dumps(record,indent=2)+'\n'
        files['DRIVER_GUIDE.md']='''# STM32F4 automatic binding / 自动端口

The board API bodies are filled for the detected HAL/SPL project. Generic TODO
instructions below apply only when changing to a custom port.
本包已填好 HAL/SPL 收发、GPIO、DWT 延时与并发保护，不需要填写这些回调。

1. Verify the exact wiring below. Existing hardware I2C/SPI clocks, pins and bus
   initialization remain owned by your project; run them before using this pack.
   先执行工程原有的时钟、硬件总线初始化。硬件 I2C 必须为主机、7 位地址。
   SPI 必须为主机、双线8位、MSB、软件NSS、模式0或3；核对速度和器件电压。
2. Include `kps_stm32_port.h`, call `kps_stm32_init()` once and check its result.
   It configures ONLY the selected CS (high) and software-I2C open-drain pins,
   and enables DWT without resetting its counter. It does not replace SysTick.
   软件 I2C 需要外部上拉。请确认所选引脚没有连接其他用途；生成预览会显示引脚。
3. Obtain `kps_bus bus = kps_stm32_bus();` then call selected device functions.
   Explicit application calls are still required; generation never erases storage.
4. Task/main only, IRQs enabled. A concurrent call returns KPS_EIO instead of
   interleaving. External users of that hardware bus must share ownership too.
   延时为 DWT 阻塞等待，不是 RTOS 睡眠；一个任务管理该总线最简单。
5. No automatic DMA binding or physical-device acceptance is claimed. Automatic
   DMA selection is rejected, not silently downgraded. Generic DMA remains available.
   当前自动端口不代表所有 STM32 系列，也不代表对应器件已在实板测过。

```json
''' + json.dumps(board,indent=2) + '\n```\n\n---\n\n' + files['DRIVER_GUIDE.md']
    return files


def plan_drivers(api, proj, opts, rep):
    selected = getattr(opts, 'drivers', None) or []
    board=binding(proj,opts,api.read_source_text)
    files = render_pack(selected, getattr(opts, 'driver_i2c', 'hardware'),
                        getattr(opts, 'driver_spi', 'hardware'), board)
    root = api.project_content_root(proj).resolve()
    destination = root / 'KPS' / 'DeviceDrivers'
    # Python 3.8-compatible symlink boundary check.
    try:
        destination.resolve().relative_to(root)
    except ValueError:
        raise ToolError('驱动目录越界 / Driver directory escapes project')
    config = destination / 'driver-pack.json'
    if destination.exists() and not destination.is_dir():
        raise ToolError('驱动目标不是目录 / Driver destination is not a directory')
    # Rollback deliberately preserves empty directory shells; they do not own data.
    if destination.is_dir() and any(destination.iterdir()):
        if not config.is_file() or config.read_text(encoding='utf-8') != files['driver-pack.json']:
            raise ToolError('驱动目录已存在且配置不同。备份用户端口后，在安全与恢复中卸载旧驱动包再生成；'
                            '不会覆盖。 / Existing driver pack differs; back up and uninstall it first.')
    for name, content in files.items():
        path = destination / name
        if path.is_symlink():
            raise ToolError('拒绝通过符号链接写入 / Refusing symlink: '+name)
        if path.exists():
            if not path.is_file():
                raise ToolError('不是普通文件 / Not a file: '+name)
            rep.notes.append('保留已有文件（含用户修改） / Preserving existing file: '+name)
        else:
            rep.gen_files.append((path, content, 'Device driver / 用户板级接口'))
        if path.suffix in ('.c', '.h'):
            if proj.add_file('KPS Device Drivers', name, 1 if path.suffix=='.c' else 5,
                             api.rel_or_abs(path, proj.dir)):
                rep.files.append(('KPS Device Drivers', name))
    proj.add_include_path(api.rel_or_abs(destination, proj.dir), rep)
    if board:
        rep.notes.append('STM32F4 '+board['profile']+' 自动端口已填 API；完成已有总线初始化后调用 kps_stm32_init。'
                         '核对所选 GPIO，详见 DRIVER_GUIDE.md / Auto APIs filled; verify pins and initialize before use.')
    else:
        rep.notes.append('驱动需要完成 TODO 板级接口；未自动操作硬件。详见 KPS/DeviceDrivers/DRIVER_GUIDE.md / '
                         'Fill TODO callbacks before use; hardware is not auto-initialized.')
    rep.warnings.append('本批驱动无对应实板验收；DMA选项是完成等待适配框架，不是自动配置 DMA / '
                        'No physical device acceptance; DMA requires board hooks and measurement.')
