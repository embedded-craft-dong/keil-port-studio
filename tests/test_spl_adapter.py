"""SPL is a separate startup adapter, not fabricated CubeMX USER CODE tags."""
import sys
import tempfile
import unittest
import contextlib
import io
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import keil_port_tool as m
from kps_core.project_layout import active_sources
from kps_core.ownership import _edit_hunks, _reverse_owned_hunks

MAIN = '#include "board.h"\nint main(void)\n{\n  Board_Init();\n  while (1) { Work(); }\n}\n'
IRQ = ('#include "board.h"\nvoid SVC_Handler(void) {}\n'
       'void PendSV_Handler(void) {}\nvoid SysTick_Handler(void) { /* empty */ }\n')


def project(root, macro='USE_STDPERIPH_DRIVER'):
    path = root / 'app.uvprojx'
    path.write_text('<Project><Targets><Target><TargetName>T</TargetName><TargetOption>'
        '<TargetCommonOption><Device>STM32F407ZG</Device><Cpu>CPUTYPE("Cortex-M4") FPU</Cpu>'
        '</TargetCommonOption><TargetArmAds><Cads><VariousControls><Define>' + macro +
        '</Define><IncludePath>User</IncludePath></VariousControls></Cads></TargetArmAds>'
        '</TargetOption><Groups><Group><GroupName>App</GroupName><Files>'
        '<File><FileName>entry.c</FileName><FileType>1</FileType><FilePath>User/entry.c</FilePath></File>'
        '<File><FileName>irq.c</FileName><FileType>1</FileType><FilePath>User/irq.c</FilePath></File>'
        '</Files></Group></Groups></Target></Targets></Project>', encoding='utf-8')
    (root / 'User').mkdir()
    (root / 'User/entry.c').write_text(MAIN, encoding='utf-8')
    (root / 'User/irq.c').write_text(IRQ, encoding='utf-8')
    return m.KeilProject(path)


class SPLTests(unittest.TestCase):
    def test_main_patch_reversible_repeatable(self):
        for os2 in (True, False):
            result = m.patch_spl_main(MAIN, os2).require_safe('entry')
            self.assertTrue(result.changed)
            self.assertIn('Work();', result.text)  # never moved/erased
            self.assertIn('SystemCoreClockUpdate();', result.text)
            self.assertFalse(m.patch_spl_main(result.text, os2).changed)
            self.assertEqual(_reverse_owned_hunks(result.text, _edit_hunks(MAIN, result.text), 'main'), MAIN)

    def test_refuse_custom_flow_and_modified_startup(self):
        for original in (MAIN.replace('Board_Init();', 'if (ready) Board_Init();'),
                         MAIN.replace('while (1)', 'while (ready)'),
                         MAIN.replace('Board_Init();', 'osKernelStart();'),
                         MAIN.replace('Board_Init();', '#if X\nBoard_Init();\n#endif')):
            with self.assertRaises(m.ToolError):
                m.patch_spl_main(original).require_safe('entry')
        patched = m.patch_spl_main(MAIN).text.replace('osKernelStart();', '/* removed */')
        with self.assertRaises(m.ToolError):
            m.patch_spl_main(patched).require_safe('entry')

    def test_tick_exceptions_roundtrip(self):
        new = m.patch_spl_tick(IRQ).require_safe('irq').text
        new = m.patch_project_rtos_exceptions(new).require_safe('irq').text
        self.assertLess(new.index('#include "FreeRTOS.h"'), new.index('#if !defined(vPortSVCHandler)'))
        self.assertFalse(m.patch_spl_tick(new).changed)
        self.assertEqual(_reverse_owned_hunks(new, _edit_hunks(IRQ, new), 'irq'), IRQ)
        with self.assertRaises(m.ToolError):
            m.patch_spl_tick(IRQ.replace('/* empty */', 'UserTick();')).require_safe('irq')

    def test_windows_crlf_patches_are_idempotent(self):
        for patcher, original in ((m.patch_spl_main, MAIN), (m.patch_spl_tick, IRQ)):
            original = original.replace('\n', '\r\n')
            once = patcher(original).require_safe('windows.c').text.replace('\n', '\r\n')
            result = patcher(once).require_safe('windows.c')
            self.assertFalse(result.changed)
            self.assertEqual(result.text, once)

    def test_discovery_no_directory_assumption_and_exclusions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = project(root)
            self.assertEqual(m.project_profile(p), 'stm32-spl')
            self.assertEqual(m.discover_entry(p, m.read_source_text)[0], root / 'User/entry.c')
            (root / 'backup.c').write_text(MAIN)
            self.assertEqual(len(list(active_sources(p))), 2)
            p.add_file('App', 'backup.c', 1, 'backup.c')
            with self.assertRaises(m.ToolError):
                m.discover_entry(p, m.read_source_text)
            node = p.root.find('.//File[FileName="backup.c"]')
            import xml.etree.ElementTree as ET
            ET.SubElement(ET.SubElement(ET.SubElement(node, 'FileOption'), 'CommonProperty'), 'IncludeInBuild').text = '0'
            self.assertEqual(m.discover_entry(p, m.read_source_text)[0].name, 'entry.c')

    def test_timebase_priority_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = project(root)
            for call in ('SysTick->CTRL &= ~1;', 'SysTick_Config(100);',
                         'NVIC_PriorityGroupConfig(NVIC_PriorityGroup_2);'):
                (root / 'User/entry.c').write_text(MAIN.replace('Board_Init();', call))
                with self.assertRaises(m.ToolError):
                    m.validate_spl_rtos(p, m.read_source_text)
            (root / 'User/entry.c').write_text(MAIN.replace('Board_Init();', 'NVIC_PriorityGroupConfig(NVIC_PriorityGroup_4); /* SysTick->CTRL=0; */'))
            m.validate_spl_rtos(p, m.read_source_text)

    def test_application_uses_actual_main_and_keeps_vendor_encoding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = project(root)
            path = root / 'User/entry.c'
            original = ('/* 用户标准库 */\r\n' + MAIN.replace('\n', '\r\n')).encode('gbk')
            path.write_bytes(original)
            report = m.Report('freertos')
            m.add_freertos_application(p, True, report)
            patched = next(value for file, value, _ in report.gen_files if file == path)
            self.assertIn('用户标准库', m.encode_preserving_format(path, patched).decode('gbk'))
            self.assertEqual(path.read_bytes(), original)  # planning only
            self.assertTrue(any('while(1)' in w for w in report.warnings))

    def test_mixed_hal_spl_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = project(Path(td), 'USE_STDPERIPH_DRIVER,USE_HAL_DRIVER')
            with self.assertRaises(m.ToolError):
                m.project_profile(p)

    def test_native_static_system_tasks_have_memory(self):
        source = m.freertos_app_templates(False)[1]
        self.assertIn('vApplicationGetIdleTaskMemory', source)
        self.assertIn('vApplicationGetTimerTaskMemory', source)
        self.assertNotIn('vApplicationGetIdleTaskMemory', m.freertos_app_templates(True)[1])

    def test_spl_primitives_are_not_application_calls(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = project(root)
            source = root / 'Libraries/STM32F4xx_StdPeriph_Driver/src/misc.c'
            source.parent.mkdir(parents=True)
            source.write_text('void SysTick_CLKSourceConfig(uint32_t source) { SysTick->CTRL = source; }\n'
                              'void NVIC_PriorityGroupConfig(uint32_t group) { SCB->AIRCR = group; }')
            p.add_file('SPL', source.name, 1, str(source))
            m.validate_spl_rtos(p, m.read_source_text)
            source.write_text(source.read_text() + '\nvoid bad(void) { SysTick->LOAD = 1; }')
            with self.assertRaises(m.ToolError):
                m.validate_spl_rtos(p, m.read_source_text)

    def test_cp1252_vendor_comments_roundtrip_not_binary(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'system.c'
            data = b'/* ST board note: \x96 jumper OFF */\r\nvoid f(void) {}\r\n'
            source.write_bytes(data)
            text = m.read_source_text(source)
            self.assertEqual(m.encode_preserving_format(source, text), data)
            source.write_bytes(b'void \x96 bad(void) {}')
            with self.assertRaises(m.ToolError):
                m.read_source_text(source)

    def test_cmsis_core_must_be_coherent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = project(root)
            source = root / 'User/core_cm4.h'
            source.write_text('#include "core_cmFunc.h"')
            with self.assertRaises(m.ToolError):
                m.validate_spl_cmsis(p, m.read_source_text)
            source.write_text('#include "cmsis_compiler.h"')
            m.validate_spl_cmsis(p, m.read_source_text)

    def test_device_header_discovery_does_not_search_siblings(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = project(root)
            source = root / 'User/system_stm32f4xx.c'
            source.write_text('/* long vendor preamble */\n' * 800 + '#include "stm32f4xx.h"\n')
            p.add_file('SPL', source.name, 1, str(source))
            self.assertEqual(m.detect_cmsis_device_header(p), 'stm32f4xx.h')

    def test_modified_startup_cannot_be_hidden_in_a_condition(self):
        text = m.patch_spl_main(MAIN).text
        text = text.replace('/* KPS SPL FREERTOS START */', 'if (ready) {\n/* KPS SPL FREERTOS START */')
        text = text.replace('/* KPS SPL FREERTOS END */', '/* KPS SPL FREERTOS END */\n}')
        with self.assertRaises(m.ToolError):
            m.patch_spl_main(text).require_safe('entry')

    def test_real_transaction_rerun_and_uninstall_without_cubemx(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            p = project(root)
            sdk = root / 'sdk'
            names = ['include/FreeRTOS.h', 'include/task.h', 'tasks.c', 'queue.c',
                     'list.c', 'timers.c', 'event_groups.c', 'stream_buffer.c', 'croutine.c',
                     'portable/MemMang/heap_4.c', 'portable/RVDS/ARM_CM4F/port.c']
            for name in names:
                file = sdk / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('/* fixture only, not compiled */\n')
            before = {file: file.read_bytes() for file in (root / 'User').glob('*.c')}
            opts = SimpleNamespace(freertos=str(sdk), no_os2=True, freertos_app=True,
                no_download=True, dry_run=False, yes=True, interactive=False,
                freertos_files=None, heap_file='heap_4.c', port_rel='ARM_CM4F')
            self.assertTrue(m.run_tasks(p, ['freertos'], opts))
            self.assertFalse(m.run_tasks(m.KeilProject(p.path), ['freertos'], opts))
            self.assertTrue(m.uninstall_component(m.KeilProject(p.path), 'freertos', yes=True))
            for file, data in before.items():
                self.assertEqual(file.read_bytes(), data)
            self.assertFalse((root / 'Application/freertos_app.c').exists())

    def test_unsafe_spl_transaction_does_not_write(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            p = project(root)
            file = root / 'User/entry.c'
            file.write_text(MAIN.replace('Board_Init();', 'SysTick->CTRL = 0;'))
            before = {f: f.read_bytes() for f in root.rglob('*') if f.is_file()}
            opts = SimpleNamespace(freertos=str(root / 'missing-sdk'), no_os2=True,
                                   freertos_app=True, yes=True, dry_run=False)
            self.assertFalse(m.run_tasks(p, ['freertos'], opts))
            self.assertTrue(p._planning_failed)
            self.assertEqual(before, {f: f.read_bytes() for f in root.rglob('*') if f.is_file()})


if __name__ == '__main__':
    unittest.main()
