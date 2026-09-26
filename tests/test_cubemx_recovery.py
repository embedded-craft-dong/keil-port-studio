"""Recovery is opt-in, transactional, and never replaces user task bodies."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import keil_port_tool as m
from kps_core.cubemx_recovery import replay_hunks
from test_cubemx_coexistence import fixture, install_record, MAIN


def snapshot(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def legacy_fixture(root):
    p, source = fixture(root)
    install_record(p, source)
    sdk = root / 'Middlewares/Third_Party/FreeRTOS'
    sdk.mkdir(parents=True)
    (sdk / 'tasks.c').write_text('/* user kernel modification */\n')
    task = root / 'Core/Src/freertos_app.c'
    task.write_text('void MX_FREERTOS_Init(void) { /* user task */ }\n')
    p.add_file('RTOS', 'tasks.c', 1, '../Middlewares/Third_Party/FreeRTOS/tasks.c')
    p.add_file('App', task.name, 1, '../Core/Src/freertos_app.c')
    p.save()
    state = root / '.keil-port-tool/manifest.json'
    data = json.loads(state.read_text())
    record = data['components']['freertos']
    record['project_files'] = p.file_records()
    record['copied_dirs'] = [{'path':'Middlewares/Third_Party/FreeRTOS', 'sha256':'original-sdk-hash'}]
    record['generated_files'] = [{'path':'Core/Src/freertos_app.c', 'sha256':'original-task-hash', 'created':True}]
    state.write_text(json.dumps(data))
    return p, task, sdk


class RecoveryTests(unittest.TestCase):
    def test_rtthread_repeated_fault_closings_are_bound_to_named_function(self):
        original = '/* USER CODE END Includes */\n'
        for name in ('HardFault_Handler', 'MemManage_Handler', 'BusFault_Handler'):
            original += ('/**\n * ' + name + '\n */\nvoid ' + name + '(void)\n{\n'
                         '  while (1)\n  {\n    /* stop */\n  }\n}\n\n')
        original += ('/**\n * PendSV\n */\nvoid PendSV_Handler(void)\n{\n}\n\n'
                     '/** SysTick */\nvoid SysTick_Handler(void) {\n'
                     '/* USER CODE END SysTick_IRQn 0 */\nHAL_IncTick();\n}\n')
        patched = m.patch_rtthread_irq(original)
        hunks = m._edit_hunks(original, patched)
        self.assertEqual(replay_hunks(original, hunks, 'irq'), patched)
        with self.assertRaises(m.ToolError):
            replay_hunks(original.replace('/* stop */', 'SaveFault();', 1), hunks, 'irq')
        with self.assertRaises(m.ToolError):
            replay_hunks(original.replace('HardFault_Handler(void)', 'Renamed_Handler(void)'), hunks, 'irq')

    def test_source_preview_does_not_treat_crlf_as_whole_file_change(self):
        with tempfile.TemporaryDirectory() as td:
            p, _ = fixture(Path(td))
            source = Path(td) / 'test.c'
            before = ''.join('int line%d;\r\n' % i for i in range(60))
            source.write_bytes(before.encode('utf-8'))
            rep = m.Report('rtthread')
            after = before.replace('\r\n', '\n').replace('int line30;', 'int changed;')
            rep.gen_files.append((source, after, 'source patch'))
            preview = m.build_diff_preview(p, [rep])
            self.assertIn('-int line30;', preview)
            self.assertIn('+int changed;', preview)
            self.assertNotIn('-int line0;', preview)
            encoded = m.encode_preserving_format(source, after)
            self.assertEqual(encoded.count(b'\r\n'), 60)

    def test_rtthread_empty_fault_loop_recoverable_but_custom_fault_is_not(self):
        original = ('/* USER CODE END Includes */\n'
                    'void PendSV_Handler(void) {}\n'
                    'void HardFault_Handler(void) { while (1) { /* stop */ } }\n'
                    'void SysTick_Handler(void) {\n/* USER CODE END SysTick_IRQn 0 */\nHAL_IncTick();\n}\n')
        patched = m.patch_rtthread_irq(original)
        hunks = m._edit_hunks(original, patched)
        self.assertEqual(replay_hunks(original, hunks, 'irq'), patched)
        with self.assertRaises(m.ToolError):
            replay_hunks(original.replace('/* stop */', 'SaveFault();'), hunks, 'irq')

    def test_legacy_isolation_preserves_modified_source_and_rollback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, task, sdk = legacy_fixture(root)
            original_task = task.read_bytes()
            original_xml = p.path.read_bytes()
            before = snapshot(root)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(m.protect_cubemx_project(p, dry_run=True))
                self.assertFalse(m.protect_cubemx_project(p, preview_callback=lambda _:False))
            self.assertEqual(before, snapshot(root))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(m.protect_cubemx_project(p, yes=True))
                self.assertFalse(m.protect_cubemx_project(p, yes=True))
            self.assertEqual((root / 'KPS/FreeRTOS/App/freertos_app.c').read_bytes(), original_task)
            self.assertEqual((root / 'KPS/ThirdParty/FreeRTOS/tasks.c').read_bytes(), (sdk / 'tasks.c').read_bytes())
            self.assertFalse(task.exists())
            self.assertTrue(task.with_name(task.name + '.kps_migrated_bak').exists())
            record = m.installed_components(m.KeilProject(p.path))['freertos']
            self.assertEqual(record['generated_files'][0]['sha256'], 'original-task-hash', 'do not claim user changes as generated')
            self.assertEqual(record['copied_dirs'][0]['sha256'], 'original-sdk-hash')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(m.rollback_last_transaction(m.KeilProject(p.path), yes=True))
            self.assertEqual(p.path.read_bytes(), original_xml)
            self.assertEqual(task.read_bytes(), original_task)
            self.assertFalse((root / 'KPS/ThirdParty/FreeRTOS').exists())

    def test_legacy_isolation_rejects_existing_destination(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, _, _ = legacy_fixture(root)
            (root / 'KPS/ThirdParty/FreeRTOS').mkdir(parents=True)
            before = snapshot(root)
            with self.assertRaises(m.ToolError): m.protect_cubemx_project(p, yes=True)
            self.assertEqual(snapshot(root), before)

    def test_deleted_hook_restored_new_pin_code_preserved_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, source = fixture(root)
            installed = install_record(p, source)
            regenerated = MAIN + '\nvoid New_GPIO_Init(void) { /* new pin configuration */ }\n'
            source.write_text(regenerated)
            before = snapshot(root)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(m.recover_cubemx_project(p, dry_run=True))
                self.assertFalse(m.recover_cubemx_project(p, preview_callback=lambda _:False))
            self.assertEqual(before, snapshot(root))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(m.recover_cubemx_project(p, yes=True))
                self.assertFalse(m.recover_cubemx_project(p, yes=True))
            self.assertEqual(source.read_text(), installed + '\nvoid New_GPIO_Init(void) { /* new pin configuration */ }\n')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(m.rollback_last_transaction(m.KeilProject(p.path), yes=True))
            self.assertEqual(source.read_text(), regenerated)

    def test_changed_owned_code_refused_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, source = fixture(root)
            install_record(p, source)
            source.write_text(source.read_text().replace('osKernelStart();', 'myKernelStart();'))
            before = snapshot(root)
            with self.assertRaises(m.ToolError):
                m.recover_cubemx_project(p, yes=True)
            self.assertEqual(before, snapshot(root))

    def test_ioc_owner_switch_refused_without_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, source = fixture(root)
            install_record(p, source)
            (root / 'App.ioc').write_text('Mcu.IP0=FREERTOS\n')
            before = snapshot(root)
            with self.assertRaises(m.ToolError):
                m.recover_cubemx_project(p, yes=True)
            self.assertEqual(before, snapshot(root))

    def test_missing_user_file_is_not_recreated_from_template(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, source = fixture(root)
            install_record(p, source)
            source.unlink()
            before = snapshot(root)
            with self.assertRaises(m.ToolError):
                m.recover_cubemx_project(p, yes=True)
            self.assertEqual(before, snapshot(root))

    def test_recovery_writes_rollback_on_postcheck_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, source = fixture(root)
            install_record(p, source)
            source.write_text(MAIN)
            with patch.object(m, 'inspect_coexistence', return_value=[{'severity':'error'}]), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(m.ToolError):
                    m.recover_cubemx_project(p, yes=True)
            self.assertEqual(source.read_text(), MAIN)

    def test_restore_references_and_includes_preserves_task_body(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, source = fixture(root)
            install_record(p, source)
            task = root / 'KPS/Tasks/task.c'
            task.parent.mkdir(parents=True)
            task.write_text('void UserTask(void) { /* my modified task */ }\n')
            manifest = root / '.keil-port-tool/manifest.json'
            data = json.loads(manifest.read_text())
            record = data['components']['freertos']
            record['project_files'].append({'target':'Debug', 'group':'User tasks', 'name':'task.c', 'path':'../KPS/Tasks/task.c'})
            record['include_paths'].append('../KPS/Tasks')
            record['defines'].append('KPS_TEST=1')
            manifest.write_text(json.dumps(data))
            before_task = task.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(m.recover_cubemx_project(p, yes=True))
            self.assertEqual(task.read_bytes(), before_task)
            self.assertIn('task.c', p.path.read_text())
            self.assertIn('KPS_TEST=1', p.path.read_text())
            self.assertFalse(m.inspect_coexistence(m.KeilProject(p.path), m.read_source_text))

    def test_legacy_guard_replay_rejects_user_exception_body(self):
        original = 'void SVC_Handler(void)\n{\n}\n\nvoid PendSV_Handler(void)\n{\n}\n'
        patched = m.patch_project_rtos_exceptions(original).require_safe('test').text
        self.assertEqual(replay_hunks(original, m._edit_hunks(original, patched), 'test'), patched)
        with self.assertRaises(m.ToolError):
            replay_hunks(original.replace('{\n', '{\nmy_handler();\n', 1), m._edit_hunks(original, patched), 'test')

    def test_new_cube_aliases_survive_replacing_outside_user_regions(self):
        before = '/* USER CODE BEGIN Includes */\n/* USER CODE END Includes */\nvoid SVC_Handler(void)\n{\n}\nvoid PendSV_Handler(void)\n{\n}\n'
        after = m.patch_project_rtos_exceptions(before).require_safe('test').text
        self.assertIn('#define SVC_Handler KPS_CubeMX_Unused_SVC_Handler', after)
        self.assertNotIn('#if !defined', after)
        self.assertEqual(m.patch_project_rtos_exceptions(after).require_safe('test').text, after)
        with self.assertRaises(m.ToolError):
            m.patch_project_rtos_exceptions(after.replace('void SVC_Handler(void)\n{', 'void SVC_Handler(void)\n{\nuser_logic();')).require_safe('test')

    def test_boundary_indent_change_allowed_but_not_c_code_change(self):
        before = '/* USER CODE BEGIN 2 */\n  /* USER CODE END 2 */\n'
        after = '/* USER CODE BEGIN 2 */\nstart();\n/* USER CODE END 2 */\n'
        indented = after.replace('/* USER CODE END', '  /* USER CODE END')
        edits = m._edit_hunks(before, after)
        self.assertEqual(m._reverse_owned_hunks(indented, edits, 'test'), before)
        self.assertEqual(replay_hunks(indented, edits, 'test'), indented)
        with self.assertRaises(m.ToolError):
            replay_hunks(indented.replace('start();', 'other();'), edits, 'test')

    def test_inline_init_marker_cannot_report_startup_success(self):
        source = '/* USER CODE END Includes */\nint main(void) { HAL_Init(); /* USER CODE END 2 */\nwhile(1) {}\n}\n'
        with self.assertRaises(m.ToolError):
            m.patch_main_start_scheduler(source).require_safe('inline main')

    def test_new_cube_library_copy_is_outside_middlewares(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, _ = fixture(root)
            sdk = root / 'SDK'; sdk.mkdir(); (sdk / 'tasks.c').write_text('x')
            rep = m.Report('freertos')
            _, local = m.plan_project_library_copy(p, sdk, 'FreeRTOS', lambda path:path if (path / 'tasks.c').is_file() else None, rep)
            self.assertEqual(local, root / 'KPS/ThirdParty/FreeRTOS')
            self.assertFalse(local.exists())

    def test_cli_recovery_cannot_mix_install_or_doctor(self):
        for extra in ['--doctor', '--freertos']:
            result = subprocess.run([sys.executable, str(Path(m.__file__)), '--cubemx-recover', extra], capture_output=True)
            self.assertEqual(result.returncode, 2)

    def test_peripheral_guard_finds_isolated_freertos_app(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, _ = fixture(root)
            app = root / 'KPS/FreeRTOS/App/freertos_app.c'
            app.parent.mkdir(parents=True)
            app.write_text(m.freertos_app_templates(False)[1], encoding='utf-8')
            p.add_file('FreeRTOS/Application', app.name, 1, '../KPS/FreeRTOS/App/freertos_app.c')
            rep = m.Report('rtos_guard')
            m.do_rtos_guard(p, SimpleNamespace(guard_resources=['UART']), rep)
            edits = [text for path, text, _ in rep.gen_files if path == app]
            self.assertEqual(len(edits), 1)
            self.assertIn('RTOS_PeripheralGuard_Init();', edits[0])

    def test_keep_user_code_disabled_blocks_before_planning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, source = fixture(root)
            install_record(p, source)
            (root / 'App.ioc').write_text('ProjectManager.KeepUserCode=false\nMcu.IP0=RCC\n')
            before = snapshot(root)
            with self.assertRaises(m.ToolError): m.recover_cubemx_project(p, yes=True)
            self.assertEqual(snapshot(root), before)

    def test_lwip_and_tinyusb_find_isolated_task_entry(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, _ = fixture(root)
            app = root / 'KPS/FreeRTOS/App/freertos_app.c'
            app.parent.mkdir(parents=True)
            app.write_text(m.freertos_app_templates(False)[1], encoding='utf-8')
            p.add_file('FreeRTOS/Application', app.name, 1, '../KPS/FreeRTOS/App/freertos_app.c')
            def make(sdk, names):
                for name in names:
                    f = sdk / name; f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text('/* SDK fixture */\n')
            lwip = root / 'SDK/lwip'
            make(lwip, ['src/include/lwip/init.h', 'src/core/init.c', 'src/core/mem.c',
                'src/core/memp.c', 'src/core/netif.c', 'src/core/pbuf.c', 'src/core/sys.c',
                'src/core/timeouts.c', 'src/core/tcp.c', 'src/core/udp.c', 'src/core/ipv4/ip4.c',
                'src/core/ipv4/etharp.c', 'src/core/ipv4/dhcp.c', 'src/netif/ethernet.c',
                'src/api/api_lib.c', 'src/api/tcpip.c'])
            usb = root / 'SDK/usb'
            make(usb, ['src/tusb.h', 'src/tusb.c', 'src/common/tusb_fifo.c', 'src/osal/osal_freertos.h',
                'src/device/usbd.c', 'src/class/cdc/cdc_device.c',
                'src/portable/synopsys/dwc2/dwc2_common.c', 'src/portable/synopsys/dwc2/dcd_dwc2.c'])
            opts = SimpleNamespace(lwip=str(lwip), lwip_mode='rtos', lwip_apps=[], lwip_ipv6=False,
                lwip_driver='stm32_eth', tinyusb=str(usb), tinyusb_mode='device', tinyusb_classes=['CDC'],
                no_download=True, dry_run=False, yes=True)
            for task, call in [('lwip', 'LwIP_AppInit();'), ('tinyusb', 'TinyUSB_AppInit();')]:
                rep = m.Report(task)
                m.TASK_FUNCS[task](p, opts, rep)
                changes = [text for path, text, _ in rep.gen_files if path == app]
                self.assertEqual(len(changes), 1, task)
                self.assertIn(call, changes[0])
                self.assertTrue(any('KPS/ThirdParty' in str(dst).replace('\\', '/') for _, dst, _ in rep.copy_trees))


if __name__ == '__main__':
    unittest.main(verbosity=2)
