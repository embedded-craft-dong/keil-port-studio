import importlib.util
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import xml.etree.ElementTree as ET

ROOT = Path(__file__).parents[1]
TOOL = ROOT / 'keil_port_tool.py'
if not TOOL.exists():
    TOOL = ROOT / 'outputs' / 'keil_port_tool.py'
spec = importlib.util.spec_from_file_location('keil_port_tool', TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def touch(path, text='/* test */\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    project = root / 'demo.uvprojx'
    project.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<Project><Targets><Target><TargetName>T1</TargetName><TargetOption>
<TargetCommonOption><Device>STM32F407VG</Device><Cpu>CPUTYPE("Cortex-M4") FPU</Cpu></TargetCommonOption>
<TargetArmAds><Cads><VariousControls><Define>USE_HAL</Define><IncludePath>Inc</IncludePath></VariousControls></Cads></TargetArmAds>
</TargetOption><Groups><Group><GroupName>App</GroupName><Files /></Group></Groups></Target></Targets></Project>''', encoding='utf-8')
    touch(root / 'Drivers' / 'CMSIS' / 'RTOS2' / 'Source' / 'os_systick.c')

    app = root / 'User'
    touch(app / 'new.c')
    touch(app / 'new.h')
    proj = m.KeilProject(project)
    assert m.find_cmsis_os_tick_source(proj).name == 'os_systick.c'
    actual_irq = root / 'Core' / 'Src' / 'stm32f4xx_it.c'
    stale_irq = (root / '.keil-port-tool' / 'transactions' / 'old' / 'before' /
                 'Core' / 'Src' / 'stm32f4xx_it.c')
    irq_text = 'void SysTick_Handler(void) {}\n'
    touch(actual_irq, irq_text)
    touch(stale_irq, irq_text)
    proj.add_file('Core', actual_irq.name, 1, 'Core\\Src\\stm32f4xx_it.c')
    found_irq, _found_text = m.find_project_systick_source(proj)
    assert found_irq == actual_irq.resolve()
    assert '.keil-port-tool' not in found_irq.parts
    rep = m.Report('add_files')
    opts = SimpleNamespace(scan_dirs=str(app), scan_files={app / 'new.c', app / 'new.h'}, include_h=True)
    m.do_add_files(proj, opts, rep)
    assert proj.root.find('.//Target/Groups/Group[GroupName="User"]/Files/File[FileName="new.c"]') is not None
    assert proj.root.find('./Groups') is None

    fr = root / 'FreeRTOS-Kernel'
    touch(fr / 'include' / 'FreeRTOS.h')
    touch(fr / 'include' / 'task.h',
          '#define tskKERNEL_VERSION_MAJOR 10\n#define tskKERNEL_VERSION_MINOR 5\n'
          '#define tskKERNEL_VERSION_BUILD 1\n')
    assert m.freertos_kernel_version(fr) == (10, 5, 1)
    for name in ('tasks.c', 'list.c', 'queue.c', 'timers.c', 'event_groups.c', 'stream_buffer.c', 'croutine.c'):
        touch(fr / name)
    touch(fr / 'portable' / 'MemMang' / 'heap_4.c')
    touch(fr / 'portable' / 'RVDS' / 'ARM_CM4F' / 'port.c')
    selected_fr = {fr / 'tasks.c', fr / 'list.c', fr / 'queue.c',
                   fr / 'stream_buffer.c', fr / 'portable' / 'MemMang' / 'heap_4.c',
                   fr / 'portable' / 'RVDS' / 'ARM_CM4F' / 'port.c'}
    opts = SimpleNamespace(freertos=str(fr), port_rel='ARM_CM4F', heap_file='heap_4.c',
                           freertos_files=selected_fr, no_os2=True, dry_run=False,
                           no_download=True, interactive=False, freertos_app=False)
    rep = m.Report('freertos')
    m.do_freertos(proj, opts, rep)
    assert rep.copy_trees and rep.copy_trees[0][1].name == 'FreeRTOS'
    names = [f.findtext('FileName') for f in proj.root.findall('.//Target/Groups/Group/Files/File')]
    assert 'tasks.c' in names and 'stream_buffer.c' in names and 'timers.c' not in names
    task_path = proj.root.find('.//Target/Groups/Group/Files/File[FileName="tasks.c"]/FilePath').text
    assert 'Middlewares\\Third_Party\\FreeRTOS' in task_path
    cfg = [x for x in rep.gen_files if Path(x[0]).name == 'FreeRTOSConfig.h'][0][1]
    assert '#define configUSE_TIMERS                        0' in cfg
    assert '#define configUSE_CO_ROUTINES                 0' in cfg
    assert '#define CMSIS_device_header "stm32f4xx.h"' not in cfg  # no_os2=True

    # CMSIS-RTOS2 的 os_systick.c 是强制依赖：即使 GUI 传入的
    # FreeRTOS 可选文件集没有它，也必须自动加入工程。
    os2_c = fr / 'CMSIS_RTOS_V2' / 'cmsis_os2.c'
    touch(os2_c)
    touch(root / 'Drivers' / 'CMSIS' / 'RTOS2' / 'Include' / 'cmsis_os2.h')
    touch(root / 'Drivers' / 'CMSIS' / 'RTOS2' / 'Include' / 'os_tick.h')
    os2_selected = set(selected_fr)
    os2_selected.add(os2_c)
    os2_opts = SimpleNamespace(
        freertos=str(fr), port_rel='ARM_CM4F', heap_file='heap_4.c',
        freertos_files=os2_selected, no_os2=False, dry_run=False,
        no_download=True, interactive=False, freertos_app=False)
    os2_rep = m.Report('freertos')
    m.do_freertos(proj, os2_opts, os2_rep)
    names = [f.findtext('FileName') for f in proj.root.findall(
        './/Target/Groups/Group/Files/File')]
    assert 'cmsis_os2.c' in names
    assert 'os_systick.c' in names
    assert any(name == 'os_systick.c' for _group, name in os2_rep.files)

    patched, changed = m.patch_freertos_config(
        '#ifndef FREERTOS_CONFIG_H\n#define FREERTOS_CONFIG_H\n'
        '#define INCLUDE_vTaskDelayUntil 1\n#define INCLUDE_xTaskDelayUntil 1\n#endif\n',
        'stm32f4xx.h')
    assert changed
    assert 'INCLUDE_vTaskDelayUntil' not in patched
    assert '#define CMSIS_device_header "stm32f4xx.h"' in patched
    assert '#define vPortSVCHandler SVC_Handler' in patched
    assert '#define xPortPendSVHandler PendSV_Handler' in patched

    wrapper, wrapper_changed = m.patch_cmsis_wrapper_systick(
        '#if defined(SysTick)\n#undef SysTick_Handler\nvoid SysTick_Handler (void) {}\n#endif\n')
    assert wrapper_changed and '!defined(USE_CUSTOM_SYSTICK_HANDLER_IMPLEMENTATION)' in wrapper
    irq, irq_changed = m.patch_project_systick(
        '/* USER CODE END Includes */\nvoid SysTick_Handler(void)\n{\n'
        '/* USER CODE BEGIN SysTick_IRQn 0 */\nHAL_IncTick();\n}\n')
    assert irq_changed and '#include "FreeRTOS.h"' in irq and 'xPortSysTickHandler();' in irq
    wrapped_irq, wrapped_changed = m.patch_project_rtos_exceptions(
        'void SVC_Handler(void)\n{\n  /* empty CubeMX stub */\n}\n'
        'void PendSV_Handler(void)\n{\n  /* empty CubeMX stub */\n}\n')
    assert wrapped_changed
    assert '#if !defined(vPortSVCHandler)' in wrapped_irq
    assert '#if !defined(xPortPendSVHandler)' in wrapped_irq
    wrapped_again, wrapped_again_changed = m.patch_project_rtos_exceptions(wrapped_irq)
    assert not wrapped_again_changed and wrapped_again == wrapped_irq

    main_text = ('/* USER CODE BEGIN Includes */\n/* USER CODE END Includes */\n'
                 'int main(void) {\n/* USER CODE BEGIN 2 */\napp_init();\n'
                 '/* USER CODE END 2 */\nwhile (1) {}\n}\n')
    patched_main, main_changed = m.patch_main_start_scheduler(main_text, True)
    assert main_changed
    assert '#include "freertos_app.h"' in patched_main
    assert 'osKernelInitialize();' in patched_main and 'osKernelStart();' in patched_main
    unrelated = '#include "freertos.h"\nvoid f(void) { osKernelStart(); }\n'
    result = m.patch_main_start_scheduler(unrelated, True)
    assert result.status == 'unsupported' and result.text == unrelated
    app_h, app_c = m.freertos_app_templates(True)
    assert 'MX_FREERTOS_Init' in app_h and 'osThreadNew' in app_c and 'StartDefaultTask' in app_c
    assert '#include "FreeRTOS.h"' in app_c and '#include "freertos_app.h"' in app_c
    assert 'void vAssertCalled(' in app_c
    assert 'g_freertos_assert_file' in app_c and 'g_freertos_assert_line' in app_c
    assert 'INCLUDE_vTaskDelayUntil' not in cfg
    assert 'INCLUDE_xTaskDelayUntil' in cfg
    assert '#define vPortSVCHandler                         SVC_Handler' in cfg
    assert '#define xPortPendSVHandler                      PendSV_Handler' in cfg
    assert 'vAssertCalled(__FILE__, __LINE__)' in cfg
    patched, changed = m.patch_freertos_config(
        '#define INCLUDE_vTaskDelayUntil 1\n#define INCLUDE_xTaskDelayUntil 1\n')
    assert changed and 'INCLUDE_vTaskDelayUntil' not in patched
    old_assert_cfg = (
        '#define configASSERT( x ) if( ( x ) == 0 ) { taskDISABLE_INTERRUPTS(); for( ;; ); }\n')
    patched_assert, assert_changed = m.patch_freertos_config(old_assert_cfg)
    assert assert_changed and 'void vAssertCalled(' in patched_assert
    assert 'vAssertCalled(__FILE__, __LINE__)' in patched_assert

    lv = root / 'lvgl'
    touch(lv / 'lvgl.h')
    touch(lv / 'lv_conf_template.h', '#if 0 /* enable content */\n#define LV_COLOR_DEPTH 16\n#endif\n')
    touch(lv / 'src' / 'core' / 'lv_a.c')
    touch(lv / 'src' / 'widgets' / 'lv_b.c')
    opts = SimpleNamespace(lvgl=str(lv), lvgl_files={lv / 'src' / 'core' / 'lv_a.c'},
                           color_depth=32, ports=False)
    rep = m.Report('lvgl')
    m.do_lvgl(proj, opts, rep)
    assert rep.copy_trees and rep.copy_trees[0][1].name == 'LVGL'
    names = [f.findtext('FileName') for f in proj.root.findall('.//Target/Groups/Group/Files/File')]
    assert 'lv_a.c' in names and 'lv_b.c' not in names
    lv_path = proj.root.find('.//Target/Groups/Group/Files/File[FileName="lv_a.c"]/FilePath').text
    assert 'Middlewares\\Third_Party\\LVGL' in lv_path
    assert any('LV_COLOR_DEPTH 32' in x[1] for x in rep.gen_files)
    opts.yes = True
    opts.dry_run = False
    m.run_tasks(proj, ['lvgl'], opts)
    assert (root / 'Middlewares' / 'Third_Party' / 'LVGL' / 'lvgl.h').is_file()

    fat = root / 'stm32-mw-fatfs' / 'source'
    for name in ('ff.c', 'ff.h', 'diskio.c', 'diskio.h', 'ff_gen_drv.c',
                 'ffsystem_baremetal.c', 'ffsystem_cmsis_os.c', 'ffunicode.c'):
        touch(fat / name)
    touch(fat / 'ff_gen_drv.h',
          '#include "ff.h"\ntypedef struct {\n'
          'DRESULT (*disk_read)(BYTE, BYTE*, LBA_t, UINT);\n'
          'DRESULT (*disk_write)(BYTE, const BYTE*, LBA_t, UINT);\n'
          '} Diskio_drvTypeDef;\n')
    touch(fat / 'ffconf_template.h',
          '#define FF_FS_NORTC 0\n#define FF_FS_REENTRANT 0\n'
          '#define FF_FS_TIMEOUT 1000\n')
    assert m.locate_fatfs_root(fat.parent) == fat
    patched_ffconf, ffconf_changed = m.patch_fatfs_config(
        '#define FF_FS_NORTC 0\n#define FF_FS_REENTRANT 0\n#define FF_FS_TIMEOUT 10\n', True)
    assert ffconf_changed
    assert '#define FF_FS_REENTRANT      1' in patched_ffconf
    assert '#define FF_FS_NORTC          1' in patched_ffconf

    selected_fat = {fat / 'ff.c', fat / 'diskio.c', fat / 'ff_gen_drv.c',
                    fat / 'ffsystem_baremetal.c'}
    touch(root / 'FatFs' / 'Target' / 'ffconf.h',
          '#define FF_FS_NORTC 0\n#define FF_FS_REENTRANT 1\n#define FF_FS_TIMEOUT 25\n')
    fat_opts = SimpleNamespace(fatfs=str(fat.parent), fatfs_files=selected_fat,
                               fatfs_mode='baremetal', fatfs_app=True,
                               no_download=True, dry_run=False)
    fat_rep = m.Report('fatfs')
    m.do_fatfs(proj, fat_opts, fat_rep)
    fat_names = [f.findtext('FileName') for f in proj.root.findall(
        './/Target/Groups/Group/Files/File')]
    assert 'ff.c' in fat_names and 'ffsystem_baremetal.c' in fat_names
    assert 'ffsystem_cmsis_os.c' not in fat_names and 'ffunicode.c' not in fat_names
    fat_cfg = [x for x in fat_rep.gen_files if Path(x[0]).name == 'ffconf.h'][0][1]
    assert re.search(r'#define\s+FF_FS_REENTRANT\s+0\b', fat_cfg)
    assert any(Path(x[0]).parent.name == 'Target' for x in fat_rep.gen_files
               if Path(x[0]).name == 'ffconf.h')
    assert any(Path(x[0]).name == 'user_diskio.c' and 'LBA_t sector' in x[1]
               for x in fat_rep.gen_files)
    assert any(Path(x[0]).name == 'fatfs.c' for x in fat_rep.gen_files)
    main_updates = [x[1] for x in fat_rep.gen_files if Path(x[0]).name == 'main.c']
    if main_updates:
        assert 'MX_FATFS_Init();' in main_updates[-1]

    selected_rtos = {fat / 'ff.c', fat / 'diskio.c', fat / 'ff_gen_drv.c',
                     fat / 'ffsystem_cmsis_os.c', fat / 'ffunicode.c'}
    rtos_opts = SimpleNamespace(fatfs=str(fat), fatfs_files=selected_rtos,
                                fatfs_mode='rtos', fatfs_app=False,
                                no_download=True, dry_run=False)
    rtos_rep = m.Report('fatfs')
    m.do_fatfs(proj, rtos_opts, rtos_rep)
    rtos_names = [f.findtext('FileName') for f in proj.root.findall(
        './/Target/Groups/Group/Files/File')]
    assert 'ffsystem_cmsis_os.c' in rtos_names
    assert 'ffsystem_baremetal.c' not in rtos_names
    rtos_configs = [x for x in rtos_rep.gen_files if Path(x[0]).name == 'ffconf.h']
    # Existing reentrant=1 is already correct. Do not rewrite user RTC/timeout
    # just to create an update record on a no-op reapply.
    rtos_cfg = rtos_configs[0][1] if rtos_configs else m.read_source_text(root / 'FatFs/Target/ffconf.h')
    assert re.search(r'#define\s+FF_FS_REENTRANT\s+1\b', rtos_cfg)
    assert re.search(r'#define\s+FF_FS_TIMEOUT\s+25\b', rtos_cfg)
    assert re.search(r'#define\s+FF_FS_NORTC\s+0\b', rtos_cfg)
    assert m.fatfs_rtos_mode(proj, SimpleNamespace(fatfs_mode='auto')) is True

    mixed_main, mixed_changed = m.patch_main_fatfs_init(
        '/* USER CODE BEGIN Includes */\n/* USER CODE END Includes */\n'
        'int main(void) {\n/* USER CODE BEGIN 2 */\n'
        '  osKernelInitialize();\n  MX_FREERTOS_Init();\n  osKernelStart();\n'
        '/* USER CODE END 2 */\n}\n')
    assert mixed_changed and '#include "fatfs.h"' in mixed_main
    assert mixed_main.index('MX_FATFS_Init();') < mixed_main.index('osKernelInitialize();')

# Regression: exercise a real C preprocessor's header lookup, rather than
# accepting a correctly generated but unused lv_conf.h by string comparison.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    sdk = root / 'SDK' / 'lvgl'
    original_config = ('#ifndef LV_CONF_H\n#define LV_CONF_H\n'
                       '#define LV_MEM_SIZE (48U * 1024U)\n'
                       '#define LV_COLOR_DEPTH 16\n#endif\n')
    touch(sdk / 'lvgl.h')
    touch(sdk / 'lv_conf.h', original_config)
    touch(sdk / 'lv_conf_template.h', original_config)
    # These are the two lookup paths used by LVGL 8's lv_conf_internal.h.
    touch(sdk / 'src' / 'lv_conf_internal.h',
          '#ifdef LV_CONF_INCLUDE_SIMPLE\n#include "lv_conf.h"\n'
          '#else\n#include "../../lv_conf.h"\n#endif\n')
    touch(sdk / 'src' / 'probe.c',
          '#include "lv_conf_internal.h"\n'
          '#if LV_MEM_SIZE != EXPECTED_LV_HEAP\n'
          '#error Wrong LVGL config selected by the preprocessor\n#endif\n'
          'int lvgl_config_probe;\n')
    project_xml = ('<Project><Targets><Target><TargetName>T1</TargetName>'
                   '<TargetOption><TargetArmAds><Cads><VariousControls>'
                   '<IncludePath/><Define/></VariousControls></Cads>'
                   '</TargetArmAds></TargetOption><Groups/>'
                   '</Target></Targets></Project>')

    def make_lvgl_project(name):
        path = root / name / 'demo.uvprojx'
        touch(path, project_xml)
        return path

    options = SimpleNamespace(lvgl=str(sdk), lvgl_files=None, color_depth=16,
                              ports=False, yes=True, dry_run=False)
    path = make_lvgl_project('new-install')
    assert m.run_tasks(m.KeilProject(path), ['lvgl'], options)
    local = path.parent / 'Middlewares' / 'Third_Party' / 'LVGL'
    config = local.parent / 'lv_conf.h'
    assert m.is_lvgl_conf_forwarder(m.read_source_text(local / 'lv_conf.h'))
    assert (sdk / 'lv_conf.h').read_text(encoding='utf-8') == original_config
    # A user changes the advertised project configuration after installation.
    # Library-root-first IncludePath must still resolve these edits.
    touch(config, original_config.replace('48U', '16U'))
    compiler = shutil.which('gcc') or shutil.which('clang')
    if compiler:
        for simple in (False, True):
            command = [compiler, '-std=c99', '-fsyntax-only',
                       '-DEXPECTED_LV_HEAP=16384', '-I' + str(local),
                       '-I' + str(local.parent)]
            if simple:
                command.append('-DLV_CONF_INCLUDE_SIMPLE')
            command.append(str(local / 'src' / 'probe.c'))
            subprocess.run(command, check=True, capture_output=True, text=True)
    else:
        print('SKIP real LVGL include-order regression: gcc/clang unavailable')
    # Exact tool forwarders are reusable. User changes and custom headers are
    # not silently overwritten, even if the original tool marker remains.
    before_config = config.read_bytes()
    before_forwarder = (local / 'lv_conf.h').read_bytes()
    assert not m.run_tasks(m.KeilProject(path), ['lvgl'], options)
    assert config.read_bytes() == before_config
    assert (local / 'lv_conf.h').read_bytes() == before_forwarder
    for custom in (original_config,
                   m.LVGL_CONF_FORWARDER + '#define USER_CUSTOM_SETTING 1\n'):
        touch(local / 'lv_conf.h', custom)
        before_xml = path.read_bytes()
        before_custom = (local / 'lv_conf.h').read_bytes()
        assert not m.run_tasks(m.KeilProject(path), ['lvgl'], options)
        assert path.read_bytes() == before_xml
        assert config.read_bytes() == before_config
        assert (local / 'lv_conf.h').read_bytes() == before_custom
    # A write failure AFTER copy/forwarder creation must roll back both the
    # copied library and generated header, retaining a pre-existing sibling.
    failure_path = make_lvgl_project('failed-install')
    failure_local = failure_path.parent / 'Middlewares' / 'Third_Party' / 'LVGL'
    failure_config = failure_local.parent / 'lv_conf.h'
    touch(failure_config, original_config.replace('48U', '12U'))
    saved_config = failure_config.read_bytes()
    saved_project = failure_path.read_bytes()
    with mock.patch.object(m.KeilProject, 'save', side_effect=OSError('injected save failure')):
        try:
            m.run_tasks(m.KeilProject(failure_path), ['lvgl'], options)
            raise AssertionError('injected write failure was not reported')
        except m.ToolError as exc:
            assert 'injected save failure' in str(exc)
    assert not failure_local.exists()
    assert failure_config.read_bytes() == saved_config
    assert failure_path.read_bytes() == saved_project
    assert (sdk / 'lv_conf.h').read_text(encoding='utf-8') == original_config
    # Other explicit configuration sources cannot be silently bypassed either.
    for macro in ('LV_CONF_PATH=custom_config.h', 'LV_CONF_SKIP'):
        override_path = make_lvgl_project('override-' + macro.split('=', 1)[0])
        touch(override_path, project_xml.replace('<Define/>', '<Define>' + macro + '</Define>'))
        assert not m.run_tasks(m.KeilProject(override_path), ['lvgl'], options)
        assert not (override_path.parent / 'Middlewares').exists()
    override_path = make_lvgl_project('include-override')
    touch(override_path, project_xml.replace('<IncludePath/>', '<IncludePath>Config</IncludePath>'))
    custom_config = override_path.parent / 'Config' / 'lv_conf.h'
    touch(custom_config, original_config)
    assert not m.run_tasks(m.KeilProject(override_path), ['lvgl'], options)
    assert not (override_path.parent / 'Middlewares').exists()
    assert custom_config.read_text(encoding='utf-8') == original_config
    override_path = make_lvgl_project('source-directory-override')
    touch(sdk / 'src' / 'lv_conf.h', original_config)
    assert not m.run_tasks(m.KeilProject(override_path), ['lvgl'], options)
    assert not (override_path.parent / 'Middlewares').exists()

print('all tests passed')
