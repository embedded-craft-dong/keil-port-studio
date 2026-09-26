"""SPL middleware entry safety and native FatFS backend executable tests."""
import contextlib
import io
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_spl_adapter import project, MAIN
import keil_port_tool as m
from kps_core.ownership import _edit_hunks, _reverse_owned_hunks


class SPLEntryTests(unittest.TestCase):
    def test_preexisting_include_and_managed_spacing_remain_user_owned(self):
        for include in ('#include "fatfs.h"\n', '#include <fatfs.h>\n', '# include   "fatfs.h"\n'):
            original = include + MAIN
            updated = m.patch_spl_component(original, 'fatfs.h', 'MX_FATFS_Init();').require_safe('header').text
            hunks = m.spl_insertion_hunks(original, updated, 'fatfs.h', 'MX_FATFS_Init();')
            self.assertEqual(_reverse_owned_hunks(updated, hunks, 'header'), original)
            self.assertFalse(any('#include' in h['after'] for h in hunks))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'config.h'
            path.write_text('#define CFG_TUD_CDC 1\n#define USER_VALUE 42\n')
            rep = m.Report('tinyusb')
            self.assertFalse(m._plan_managed_config(path, '#define CFG_TUD_CDC    1\n', ['CFG_TUD_CDC'], 'test', rep))
            self.assertFalse(rep.gen_files)
            self.assertTrue(m._plan_managed_config(path, '#define CFG_TUD_CDC    0\n', ['CFG_TUD_CDC'], 'test', rep))
            self.assertIn('#define USER_VALUE 42', rep.gen_files[0][1])

    def test_insertion_ownership_all_removal_orders_and_user_work(self):
        entries = [(MAIN, None), (MAIN.replace('{ Work(); }', '{\n    Work();\n  }'), None),
                   (m.freertos_app_templates(False)[1], 'StartDefaultTask'),
                   (m.freertos_app_templates(True)[1], 'StartDefaultTask'),
                   (m.rtthread_app_templates('stm32f4xx.h')[1], 'RTThread_DefaultTask')]
        for original, task in entries:
            specs = [('fatfs.h', 'MX_FATFS_Init();', None),
                     ('lwip_port.h', 'LwIP_AppInit();', None if task else 'LwIP_Poll();'),
                     ('tinyusb_app.h', 'TinyUSB_AppInit();', None if task else 'TinyUSB_AppTask();')]
            current, records = original, []
            for header, call, poll in specs:
                updated = m.patch_spl_component(current, header, call, poll, task).require_safe('install').text
                records.append(m.spl_insertion_hunks(current, updated, header, call, poll, task))
                current = updated
            for order in itertools.permutations(range(3)):
                live = current
                for index in order:
                    live = _reverse_owned_hunks(live, records[index], 'combined entry')
                self.assertEqual(live, original)
            damaged = current.replace('MX_FATFS_Init();', 'User_Init();')
            with self.assertRaises(m.ToolError):
                _reverse_owned_hunks(damaged, records[0], 'modified owned call')
            if not task:
                live = current.replace('MX_FATFS_Init();\n', 'MX_FATFS_Init();\n  User_Mount();\n')
                for index in (0, 1, 2):
                    live = _reverse_owned_hunks(live, records[index], 'user work')
                self.assertIn('User_Mount();', live)

    def test_combined_real_manifest_repeat_and_arbitrary_uninstall(self):
        def installer(header, call, poll):
            return lambda proj, opts, rep: m._plan_spl_component_entry(proj, rep, header, call, poll)
        funcs = dict(fatfs=installer('fatfs.h', 'MX_FATFS_Init();', None),
                     lwip=installer('lwip_port.h', 'LwIP_AppInit();', 'LwIP_Poll();'),
                     tinyusb=installer('tinyusb_app.h', 'TinyUSB_AppInit();', 'TinyUSB_AppTask();'))
        for order in itertools.permutations(funcs):
            with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()), patch.dict(m.TASK_FUNCS, funcs):
                p = project(Path(td)); source = Path(td) / 'User/entry.c'
                original = source.read_text()
                opts = SimpleNamespace(yes=True, dry_run=False)
                self.assertTrue(m.run_tasks(p, list(funcs), opts))
                after = source.read_bytes()
                p = m.KeilProject(p.path)
                self.assertFalse(m.run_tasks(p, list(funcs), opts))
                self.assertFalse(p._planning_failed)
                self.assertEqual(source.read_bytes(), after)
                for component in order:
                    self.assertTrue(m.uninstall_component(m.KeilProject(p.path), component, yes=True))
                    issues = m.inspect_coexistence(m.KeilProject(p.path), m.read_source_text)
                    self.assertFalse([i for i in issues if i['severity'] == 'error'], issues)
                self.assertEqual(source.read_text(), original)

    def test_legacy_overlapping_ownership_is_not_silently_retrusted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); p = project(root)
            first = m.patch_spl_component(MAIN, 'lwip_port.h', 'LwIP_AppInit();', 'LwIP_Poll();').require_safe('first').text
            second = m.patch_spl_component(first, 'tinyusb_app.h', 'TinyUSB_AppInit();', 'TinyUSB_AppTask();').require_safe('second').text
            source = root / 'User/entry.c'; source.write_text(second)
            state = root / '.keil-port-tool'; state.mkdir()
            manifest = state / 'manifest.json'
            manifest.write_text(json.dumps({'version': 3, 'components': {'lwip': {
                'source_edits': [{'path': 'User/entry.c', 'hunks': m._edit_hunks(MAIN, first)}]}}}))
            before = manifest.read_bytes(), source.read_bytes()
            issues = m.inspect_coexistence(p, m.read_source_text)
            drift = [i for i in issues if i['code'] == 'OWNED_STARTUP_DRIFT']
            self.assertTrue(drift)
            self.assertIn('Legacy development SPL', drift[0]['message']['en'])
            self.assertEqual((manifest.read_bytes(), source.read_bytes()), before)

    def test_multiple_components_keep_each_other_on_repeat(self):
        entries = [(MAIN, None), (m.freertos_app_templates(False)[1], 'StartDefaultTask'),
                   (m.freertos_app_templates(True)[1], 'StartDefaultTask'),
                   (m.rtthread_app_templates('stm32f4xx.h')[1], 'RTThread_DefaultTask')]
        for original, task in entries:
            specs = [('fatfs.h', 'MX_FATFS_Init();', None),
                     ('lwip_app.h', 'LwIP_AppInit();', None if task else 'LwIP_AppTask();'),
                     ('tinyusb_app.h', 'TinyUSB_AppInit();', None if task else 'TinyUSB_AppTask();')]
            text = original
            for header, call, poll in specs:
                text = m.patch_spl_component(text, header, call, poll, task).require_safe('combined').text
            for header, call, poll in reversed(specs):
                again = m.patch_spl_component(text, header, call, poll, task).require_safe('combined repeat')
                self.assertEqual(again.text, text)
                self.assertEqual(text.count(call), 1)
                if poll:
                    self.assertEqual(text.count(poll), 1)

    def test_user_work_after_owned_init_keeps_order(self):
        text = m.patch_spl_component(MAIN, 'fatfs.h', 'MX_FATFS_Init();').text
        text = text.replace('MX_FATFS_Init();\n', 'MX_FATFS_Init();\n  MountAndStartWorkers();\n')
        result = m.patch_spl_component(text, 'fatfs.h', 'MX_FATFS_Init();').require_safe('user work')
        self.assertEqual(result.text, text)

    def test_owned_poll_cannot_move_outside_loop_or_into_condition(self):
        text = m.patch_spl_component(MAIN, 'component.h', 'Component_Init();', 'Component_Poll();').require_safe('initial').text
        poll = '\n    /* KPS SPL COMPONENT Component_Init POLL */\n    Component_Poll();\n'
        for changed in (text.replace(poll, '').replace('Component_Init();', 'Component_Init();' + poll),
                        text.replace(poll, '\n    if (ready) {' + poll + '    }\n')):
            with self.assertRaises(m.ToolError):
                m.patch_spl_component(changed, 'component.h', 'Component_Init();', 'Component_Poll();').require_safe('moved poll')

    def test_existing_timeout_rtc_preserved(self):
        original = '#define FF_FS_TIMEOUT 25\n#define FF_FS_NORTC 0\n#define FF_FS_REENTRANT 0\n'
        updated, _ = m.patch_fatfs_config(original, True, preserve_user=True)
        self.assertIn('#define FF_FS_TIMEOUT 25', updated)
        self.assertIn('#define FF_FS_NORTC 0', updated)
        updated, _ = m.patch_fatfs_config('#define FF_FS_TIMEOUT 25\n', True, preserve_user=True)
        self.assertEqual(updated.count('FF_FS_TIMEOUT'), 1)

    def test_stale_config_is_not_an_enabled_spl_kernel(self):
        with tempfile.TemporaryDirectory(prefix='FreeRTOS-RTThread-') as td:
            p = project(Path(td))
            cfg = Path(td) / 'FreeRTOS/Config/FreeRTOSConfig.h'
            cfg.parent.mkdir(parents=True); cfg.write_text('/* leftover */')
            self.assertFalse(m.project_uses_freertos(p))
            self.assertFalse(m.project_uses_rtthread(p))
            p.add_file('Disabled', 'freertos_app.c', 1, 'Application/freertos_app.c')
            import xml.etree.ElementTree as ET
            node = next(f for f in p.targets[0].findall('.//File') if f.findtext('FileName') == 'freertos_app.c')
            ET.SubElement(ET.SubElement(ET.SubElement(node, 'FileOption'), 'CommonProperty'), 'IncludeInBuild').text = '0'
            self.assertFalse(m.project_uses_freertos(p))

    def test_usb_bridge_limits_and_no_sys_contract(self):
        template = m.tinyusb_templates(True, 'device', ['CDC', 'HID'], 'OPT_MCU_STM32F4', use_rtthread=True)
        self.assertTrue(any('rt_interrupt_enter()' in part and 'rt_interrupt_leave()' in part for part in template))
        for part in ('STM32F405RG', 'STM32F407ZG', 'STM32F415RG', 'STM32F417IG'):
            header = m.tinyusb_spl_device_header(part)
            self.assertIn('USB_OTG_FS_PERIPH_BASE 0x50000000UL', header)
            self.assertIn('USB_OTG_FS_MAX_IN_ENDPOINTS 4U', header)
            self.assertIn('USB_OTG_HS_TOTAL_FIFO_SIZE 4096U', header)
            self.assertIn('#error "SPL USB device constants disagree', header)
        with self.assertRaises(m.ToolError):
            m.tinyusb_spl_device_header('STM32F429ZI')
        bare = m.lwip_port_templates(False, [], False)
        rtos = m.lwip_port_templates(True, [], False)
        self.assertIn('#define SYS_LIGHTWEIGHT_PROT            0', bare[0])
        self.assertIn('#define SYS_LIGHTWEIGHT_PROT            1', rtos[0])
        self.assertIn('((uint64_t)xTaskGetTickCount() * 1000U) / configTICK_RATE_HZ', rtos[3])

    def test_main_and_tasks_repeat_and_reverse(self):
        entries = [(MAIN, None), (m.freertos_app_templates(False)[1], 'StartDefaultTask'),
                   (m.freertos_app_templates(True)[1], 'StartDefaultTask'),
                   (m.rtthread_app_templates('stm32f4xx.h')[1], 'RTThread_DefaultTask')]
        for original, task in entries:
            for poll in (None, 'Component_Poll();'):
                with self.subTest(task=task, poll=poll):
                    result = m.patch_spl_component(original, 'component.h', 'Component_Init();', poll, task)
                    result.require_safe('fixture')
                    self.assertTrue(result.changed)
                    self.assertFalse(m.patch_spl_component(result.text, 'component.h', 'Component_Init();', poll, task).require_safe('again').changed)
                    self.assertEqual(_reverse_owned_hunks(result.text, _edit_hunks(original, result.text), 'entry'), original)
                    changed = result.text.replace('Component_Init();', 'User_Init();')
                    with self.assertRaises(m.ToolError):
                        m.patch_spl_component(changed, 'component.h', 'Component_Init();', poll, task).require_safe('changed')

    def test_comment_not_init_and_unsafe_flow_rejected(self):
        result = m.patch_spl_component('// Component_Init();\n' + MAIN, 'x.h', 'Component_Init();')
        self.assertTrue(result.require_safe('comment').changed)
        for text in (MAIN.replace('Board_Init();', 'if (ready) Board_Init();'),
                     MAIN.replace('Board_Init();', 'if (ready)'),
                     MAIN.replace('Work();', 'return 0;'),
                     MAIN.replace('Board_Init();', 'vTaskStartScheduler();'),
                     MAIN.replace('Board_Init();', 'Component_Init();'),
                     '#if X\n' + MAIN + '#endif',
                     MAIN.replace('Board_Init();', '#if X\nBoard_Init();\n#endif'),
                     '#if X\n#include "x.h"\n#endif\n' + MAIN):
            with self.subTest(text=text), self.assertRaises(m.ToolError):
                m.patch_spl_component(text, 'x.h', 'Component_Init();').require_safe('unsafe')

    def test_planned_task_and_arbitrary_main_path(self):
        with tempfile.TemporaryDirectory() as td:
            p = project(Path(td))
            rep = m.Report('fatfs')
            m._plan_spl_component_entry(p, rep, 'fatfs.h', 'MX_FATFS_Init();')
            self.assertEqual(rep.gen_files[0][0], Path(td) / 'User/entry.c')
            app = Path(td) / 'Application/freertos_app.c'
            p.add_file('FreeRTOS', app.name, 1, str(app))
            p._planned_generated_files = {os.path.normcase(str(app.resolve())): m.freertos_app_templates(False)[1]}
            rep = m.Report('fatfs')
            m._plan_spl_component_entry(p, rep, 'fatfs.h', 'MX_FATFS_Init();')
            self.assertEqual(rep.gen_files[0][0], app)
            self.assertIn('KPS SPL COMPONENT MX_FATFS_Init INIT', rep.gen_files[0][1])
            with self.assertRaises(m.ToolError):
                m._plan_spl_component_entry(p, rep, 'usb.h', 'Usb_Init();', 'Usb_Poll();')

    def test_fatfs_real_transaction_reapply_uninstall(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            p = project(root)
            original = (root / 'User/entry.c').read_bytes()
            sdk = root / 'sdk'; sdk.mkdir()
            for name in ('ff.c', 'diskio.c', 'ff_gen_drv.c', 'ffsystem_baremetal.c'):
                (sdk / name).write_text('/* fixture */\n')
            (sdk / 'ff.h').write_text('int ff_mutex_create(int vol);\n')
            (sdk / 'ff_gen_drv.h').write_text('/* LBA_t */\n')
            (sdk / 'ffconf_template.h').write_text('#define FF_FS_REENTRANT 0\n#define FF_FS_TIMEOUT 1000\n#define FF_FS_NORTC 1\n')
            opts = SimpleNamespace(fatfs=str(sdk), fatfs_mode='baremetal', yes=True, dry_run=False)
            self.assertTrue(m.run_tasks(p, ['fatfs'], opts))
            modified = (root / 'User/entry.c').read_bytes()
            self.assertNotEqual(original, modified)
            # User code outside intact owned init must not become tool-owned
            # merely because the old while line was reindented by the patch.
            live = root / 'User/entry.c'
            changed = live.read_text().replace('MX_FATFS_Init();\n', 'MX_FATFS_Init();\n  User_Mount();\n')
            live.write_text(changed)
            modified = live.read_bytes()
            p = m.KeilProject(p.path)
            self.assertFalse(m.run_tasks(p, ['fatfs'], opts))  # idempotent no-op
            self.assertFalse(getattr(p, '_planning_failed', False))
            self.assertEqual((root / 'User/entry.c').read_bytes(), modified)
            self.assertTrue(m.uninstall_component(m.KeilProject(p.path), 'fatfs', yes=True))
            self.assertIn('User_Mount();', live.read_text())
            self.assertNotIn('MX_FATFS_Init();', live.read_text())


class FatFSNativeTests(unittest.TestCase):
    def test_compiled_mutex_lifecycle_failure_timeout_and_lfn(self):
        gcc = shutil.which('gcc') or ('C:/MinGW/bin/gcc.exe' if Path('C:/MinGW/bin/gcc.exe').exists() else None)
        if not gcc:
            self.skipTest('host GCC required')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ('ff.h', 'FreeRTOS.h', 'semphr.h'):
                (root / name).write_text('')
            for static in (0, 1):
                for width in (16, 32):
                    for timeout in (0, 1000, 70000, 4294967295):
                        with self.subTest(static=static, width=width, timeout=timeout):
                            stub = '''#include <stdint.h>
#include <stdlib.h>
#include <assert.h>
typedef unsigned int UINT;
typedef uintWIDTH_t TickType_t;
typedef void *SemaphoreHandle_t;
typedef int StaticSemaphore_t;
#define FF_USE_LFN 3
#define FF_FS_REENTRANT 1
#define FF_VOLUMES 2
#define FF_FS_TIMEOUT @TIMEOUT@ULL
#define configUSE_MUTEXES 1
#define configSUPPORT_STATIC_ALLOCATION @STATIC@
#define portMAX_DELAY ((TickType_t)~(TickType_t)0)
#define pdTRUE 1
static unsigned created, deleted, taken, given, seen;
static int fail, busy;
static void *pvPortMalloc(size_t n) { return malloc(n); }
static void vPortFree(void *p) { free(p); }
static void *xSemaphoreCreateMutex(void) { ++created; return fail?NULL:(void *)1; }
static void *xSemaphoreCreateMutexStatic(StaticSemaphore_t *p) { (void)p; return xSemaphoreCreateMutex(); }
static void vSemaphoreDelete(void *p) { assert(p); ++deleted; }
static int xSemaphoreTake(void *p,TickType_t t) { assert(p); ++taken; seen=t; return !busy; }
static int xSemaphoreGive(void *p) { assert(p); ++given; return 1; }
'''.replace('WIDTH', str(width)).replace('@TIMEOUT@', str(timeout)).replace('@STATIC@', str(static))
                            harness = stub + m.fatfs_freertos_system_template() + '''
int main(void) {
 void *p;
 assert(!ff_mutex_create(-1) && !ff_mutex_create(3));
 assert(!ff_mutex_take(0)); ff_mutex_give(0); ff_mutex_delete(0);
 fail=1; assert(!ff_mutex_create(0)); fail=0;
 assert(ff_mutex_create(0) && ff_mutex_create(0)); assert(created==2);
 assert(ff_mutex_create(FF_VOLUMES));
 assert(ff_mutex_take(0)); assert(seen==EXPECTEDU);
 busy=1; assert(!ff_mutex_take(0)); busy=0;
 ff_mutex_give(0); assert(given==1);
 ff_mutex_delete(0); ff_mutex_delete(0); assert(deleted==1);
 assert(!ff_mutex_take(0)); assert(ff_mutex_create(0));
 p=ff_memalloc(64); assert(p); ff_memfree(p);
 return 0;
}
'''.replace('EXPECTED', str(min(timeout, (1 << width) - 2)))
                            source = root / 'test.c'; exe = root / 'test.exe'
                            source.write_text(harness)
                            run = subprocess.run([gcc, '-std=c99', '-Wall', '-Werror', '-Wno-unused-function', '-I', str(root), str(source), '-o', str(exe)], capture_output=True, text=True)
                            self.assertEqual(run.returncode, 0, run.stderr)
                            self.assertEqual(subprocess.run([str(exe)], timeout=5).returncode, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
