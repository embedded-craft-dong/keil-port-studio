import importlib.util
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parents[1]
TOOL = ROOT / 'keil_port_tool.py'
if not TOOL.exists():
    TOOL = ROOT / 'outputs' / 'keil_port_tool.py'
spec = importlib.util.spec_from_file_location('keil_port_tool_phase3', TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


PROJECT_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<Project><Targets><Target><TargetName>Debug</TargetName><pCCUsed>V5.06</pCCUsed><uAC6>0</uAC6>
<TargetOption><TargetCommonOption><Device>STM32F407VG</Device>
<Cpu>CPUTYPE("Cortex-M4") FPU</Cpu></TargetCommonOption>
<TargetArmAds><Cads><VariousControls><MiscControls/><Define>BASE</Define>
<IncludePath>../Core/Inc;../Drivers/CMSIS/Include</IncludePath>
</VariousControls></Cads></TargetArmAds></TargetOption>
<Groups><Group><GroupName>Application</GroupName><Files>
<File><FileName>freertos_app.c</FileName><FileType>1</FileType>
<FilePath>../Core/Src/freertos_app.c</FilePath></File>
<File><FileName>cmsis_os2.c</FileName><FileType>1</FileType>
<FilePath>../Middlewares/Third_Party/FreeRTOS/CMSIS_RTOS_V2/cmsis_os2.c</FilePath></File>
</Files></Group></Groups></Target></Targets></Project>
'''


def write(path, content='/* fixture */\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    project_root = base / 'Demo'
    mdk = project_root / 'MDK-ARM'
    mdk.mkdir(parents=True)
    project = mdk / 'Demo.uvprojx'
    write(project, PROJECT_XML)
    write(project_root / 'Core' / 'Src' / 'freertos_app.c', '''\
#include "cmsis_os2.h"
void MX_FREERTOS_Init(void)
{
    /* tasks */
}
''')
    write(project_root / 'Core' / 'Inc' / 'main.h')
    write(project_root / 'Drivers' / 'CMSIS' / 'Include' / 'core_cm4.h')
    actual_app = (project_root / 'Core' / 'Src' / 'freertos_app.c').read_text(encoding='utf-8')
    actual_patched, actual_changed = m.patch_guard_init(actual_app)
    assert actual_changed and 'RTOS_PeripheralGuard_Init();' in actual_patched

    rtt = base / 'SDK' / 'RTT-Package'
    for name in ('SEGGER_RTT.c', 'SEGGER_RTT_printf.c'):
        write(rtt / 'RTT' / name)
    write(rtt / 'RTT' / 'SEGGER_RTT_ASM_ARMv7M.S')
    write(rtt / 'RTT' / 'SEGGER_RTT.h')
    write(rtt / 'Config' / 'SEGGER_RTT_Conf.h', '/* SEGGER RTT config */\n')
    write(rtt / 'Syscalls' / 'SEGGER_RTT_Syscalls_KEIL.c')

    littlefs = base / 'SDK' / 'littlefs'
    for name in ('lfs.c', 'lfs.h', 'lfs_util.c', 'lfs_util.h'):
        write(littlefs / name)

    dsp = base / 'SDK' / 'CMSIS-DSP'
    write(dsp / 'Include' / 'arm_math.h')
    write(dsp / 'PrivateInclude' / 'arm_math_private.h')
    write(dsp / 'Source' / 'BasicMathFunctions' / 'BasicMathFunctions.c')
    write(dsp / 'Source' / 'BasicMathFunctions' / 'BasicMathFunctionsF16.c')
    write(dsp / 'Source' / 'TransformFunctions' / 'TransformFunctions.c')

    proj = m.KeilProject(project)
    opts = SimpleNamespace(
        interactive=False, yes=True, dry_run=False, no_download=True,
        sdk_dir=None, diff_file=None,
        segger_rtt=str(rtt), rtt_files=None, rtt_no_printf=False,
        rtt_no_syscalls=False, rtt_no_asm=False,
        littlefs=str(littlefs), littlefs_files=None,
        littlefs_mode='auto', littlefs_port=True,
        cmsis_dsp=str(dsp), cmsis_dsp_files=None, dsp_modules=[],
        dsp_float16=False,
        guard_resources=['UART', 'SPI', 'FLASH'])
    changed = m.run_tasks(
        proj, ['segger_rtt', 'littlefs', 'cmsis_dsp', 'rtos_guard'], opts)
    assert changed

    local = project_root / 'Middlewares' / 'Third_Party'
    assert (local / 'SEGGER_RTT' / 'RTT' / 'SEGGER_RTT.c').is_file()
    assert (local / 'LittleFS' / 'lfs.c').is_file()
    assert (local / 'CMSIS-DSP' / 'Source' / 'TransformFunctions' /
            'TransformFunctions.c').is_file()
    assert (project_root / 'Config' / 'SEGGER_RTT' / 'SEGGER_RTT_Conf.h').is_file()
    lfs_port = (project_root / 'Core' / 'Src' / 'littlefs_port.c').read_text(encoding='utf-8')
    assert '#include "cmsis_os2.h"' in lfs_port
    assert 'return lfs_mount' in lfs_port
    assert 'lfs_format' in lfs_port
    guard = (project_root / 'Core' / 'Src' / 'rtos_peripheral_guard.c').read_text(
        encoding='utf-8')
    assert '#include "cmsis_os2.h"' in guard
    guard_h = (project_root / 'Core' / 'Inc' / 'rtos_peripheral_guard.h').read_text(
        encoding='utf-8')
    assert 'RTOS_GUARD_UART' in guard_h and 'RTOS_GUARD_SPI' in guard_h
    assert 'RTOS_GUARD_FLASH' in guard_h and 'RTOS_GUARD_I2C' not in guard_h
    app = (project_root / 'Core' / 'Src' / 'freertos_app.c').read_text(encoding='utf-8')
    assert '#include "rtos_peripheral_guard.h"' in app
    assert 'RTOS_PeripheralGuard_Init();' in app

    root = ET.parse(project).getroot()
    files = {f.findtext('FileName'): f.findtext('FileType') for f in root.findall('.//File')}
    assert files['SEGGER_RTT.c'] == '1'
    assert 'SEGGER_RTT_ASM_ARMv7M.S' not in files
    assert files['lfs.c'] == '1' and files['lfs_util.c'] == '1'
    assert files['BasicMathFunctions.c'] == '1'
    assert 'BasicMathFunctionsF16.c' not in files
    define = root.findtext('.//Cads/VariousControls/Define')
    assert 'RTT_USE_ASM=0' in define
    assert 'LFS_THREADSAFE' in define
    assert 'ARM_MATH_CM4' in define
    assert 'DISABLEFLOAT16' in define

    manifest = json.loads((project_root / '.keil-port-tool' / 'manifest.json').read_text(
        encoding='utf-8'))
    assert {'segger_rtt', 'littlefs', 'cmsis_dsp', 'rtos_guard'} <= set(
        manifest['components'])

    gcc = shutil.which('gcc')
    if gcc:
        stubs = base / 'stubs'
        write(stubs / 'lfs.h', '''\
#include <stdint.h>
typedef uint32_t lfs_block_t; typedef uint32_t lfs_off_t; typedef uint32_t lfs_size_t;
typedef struct { int unused; } lfs_t;
#define LFS_ERR_IO (-5)
#define LFS_ERR_NOMEM (-12)
struct lfs_config {
 int (*read)(const struct lfs_config*,lfs_block_t,lfs_off_t,void*,lfs_size_t);
 int (*prog)(const struct lfs_config*,lfs_block_t,lfs_off_t,const void*,lfs_size_t);
 int (*erase)(const struct lfs_config*,lfs_block_t);
 int (*sync)(const struct lfs_config*);
#ifdef LFS_THREADSAFE
 int (*lock)(const struct lfs_config*); int (*unlock)(const struct lfs_config*);
#endif
 lfs_size_t read_size, prog_size, block_size, block_count, cache_size;
 lfs_size_t lookahead_size; int32_t block_cycles;
 void *read_buffer; void *prog_buffer; void *lookahead_buffer;
};
int lfs_mount(lfs_t*, const struct lfs_config*);
int lfs_format(lfs_t*, const struct lfs_config*);
''')
        write(stubs / 'cmsis_os2.h', '''\
#include <stddef.h>
#include <stdint.h>
typedef void *osMutexId_t; typedef int32_t osStatus_t;
#define osOK 0
#define osWaitForever 0xFFFFFFFFU
static inline osMutexId_t osMutexNew(const void *a) {(void)a; return (void*)1;}
static inline osStatus_t osMutexAcquire(osMutexId_t m,uint32_t t){(void)m;(void)t;return osOK;}
static inline osStatus_t osMutexRelease(osMutexId_t m){(void)m;return osOK;}
static inline uint32_t osKernelGetTickFreq(void){return 1000U;}
''')
        include_args = ['-I', str(stubs), '-I', str(project_root / 'Core' / 'Inc')]
        subprocess.run([gcc, '-std=c99', '-Werror=implicit-function-declaration', '-DLFS_THREADSAFE', '-fsyntax-only',
                        str(project_root / 'Core' / 'Src' / 'littlefs_port.c')] + include_args,
                       check=True)
        subprocess.run([gcc, '-std=c99', '-Werror=implicit-function-declaration', '-fsyntax-only',
                        str(project_root / 'Core' / 'Src' / 'rtos_peripheral_guard.c')] +
                       include_args, check=True)

    uninstall_proj = m.KeilProject(project)
    assert m.uninstall_component(uninstall_proj, 'rtos_guard', yes=True)
    assert not (project_root / 'Core' / 'Src' / 'rtos_peripheral_guard.c').exists()
    assert not (project_root / 'Core' / 'Inc' / 'rtos_peripheral_guard.h').exists()
    app_after_uninstall = (project_root / 'Core' / 'Src' / 'freertos_app.c').read_text(
        encoding='utf-8')
    assert 'rtos_peripheral_guard.h' not in app_after_uninstall
    assert 'RTOS_PeripheralGuard_Init();' not in app_after_uninstall
    manifest_after = json.loads((project_root / '.keil-port-tool' / 'manifest.json').read_text(
        encoding='utf-8'))
    assert 'rtos_guard' not in manifest_after['components']

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    project = root / 'empty.uvprojx'
    write(project, PROJECT_XML)
    before = project.read_bytes()
    proj = m.KeilProject(project)
    bad_opts = SimpleNamespace(littlefs=str(root / 'missing'), littlefs_mode='baremetal',
                               littlefs_port=True, no_download=True, dry_run=False,
                               yes=True, diff_file=None, sdk_dir=None)
    assert not m.run_tasks(proj, ['littlefs'], bad_opts)
    assert project.read_bytes() == before
    assert not (root / 'Middlewares').exists()

print('phase 3 component integration tests passed')
