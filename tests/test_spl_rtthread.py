"""Conservative RT-Thread startup for non-Cube STM32 SPL projects."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import keil_port_tool as m
from test_spl_adapter import MAIN, IRQ, project
from kps_core.ownership import _edit_hunks, _reverse_owned_hunks

IRQ_RT = IRQ + 'void HardFault_Handler(void) { while (1) { /* stop */ } }\n'


def sdk_fixture(root):
    sdk = root / 'sdk'
    files = m.rtthread_source_files(sdk) + [sdk / n for n in (
        'include/rtthread.h', 'include/rtdef.h',
        'components/libc/compilers/common/extension/sys/types.h',
        'components/libc/compilers/common/extension/sys/errno.h')]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('/* synthetic test SDK: not compiled */\n')
    (sdk / 'include/rtdef.h').write_text(
        '#define RT_VERSION_MAJOR 5\n#define RT_VERSION_MINOR 2\n#define RT_VERSION_PATCH 2\n')
    # The kernel's conditional user-main feature must not look like a board entry.
    (sdk / 'src/components.c').write_text('#ifdef RT_USING_USER_MAIN\nint $Sub$$main(void) {}\n#endif')
    return sdk


def fixture(root):
    p = project(root)
    (root / 'User/irq.c').write_text(IRQ_RT)
    (root / 'User/stm32f4xx.h').write_text('/* fixture CMSIS device */')
    system = root / 'User/system_stm32f4xx.c'
    system.write_text('#include "stm32f4xx.h"\n')
    p.add_file('App', system.name, 1, str(system))
    p.save()
    return p


class SPLRTThreadTests(unittest.TestCase):
    def test_main_irq_idempotence_and_owned_reverse(self):
        for original, patcher in ((MAIN, lambda s: m.patch_spl_main(s, rtos='rtthread')),
                                  (IRQ_RT, m.patch_spl_rtthread_irq)):
            patched = patcher(original).require_safe('fixture').text
            self.assertNotIn('USER CODE', patched)
            self.assertFalse(patcher(patched).require_safe('repeat').changed)
            self.assertEqual(_reverse_owned_hunks(patched, _edit_hunks(original, patched), 'fixture'), original)
            crlf = patched.replace('\n', '\r\n')
            self.assertEqual(patcher(crlf).require_safe('CRLF').text, crlf)

    def test_custom_or_missing_exceptions_blocked_even_after_install(self):
        for original in (IRQ_RT, m.patch_spl_rtthread_irq(IRQ_RT).text):
            for bad in (original.replace('/* stop */', 'RecordFault();'),
                        original.replace('void PendSV_Handler(void) {}', 'void PendSV_Handler(void) { Run(); }'),
                        original.replace('void HardFault_Handler', 'void RenamedFault_Handler')):
                with self.assertRaises(m.ToolError):
                    m.patch_spl_rtthread_irq(bad).require_safe('IRQ')
        with self.assertRaises(m.ToolError):
            m.patch_spl_rtthread_irq(IRQ_RT.replace('/* empty */', 'HAL_IncTick();')).require_safe('mixed')

    def test_startup_conflicts_and_non_linear_flow(self):
        for call in ('if (ready) Board_Init();', 'rtthread_startup();', 'osKernelStart();',
                     'MX_RTTHREAD_Init();', 'rt_system_scheduler_start();'):
            with self.assertRaises(m.ToolError):
                m.patch_spl_main(MAIN.replace('Board_Init();', call), rtos='rtthread').require_safe('main')
        patched = m.patch_spl_main(MAIN, rtos='rtthread').text
        with self.assertRaises(m.ToolError):
            m.patch_spl_main(patched.replace('MX_RTTHREAD_Init();', '/* missing */'), rtos='rtthread').require_safe('main')

    def test_lifecycle_preserves_task_edit_and_uninstalls_startup(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            p = fixture(root)
            sdk = sdk_fixture(root)
            opts = SimpleNamespace(rtthread=str(sdk), no_download=True, dry_run=False,
                                   interactive=False, yes=True, rtthread_files=None)
            originals = {path: path.read_bytes() for path in (root / 'User').glob('*.c')}
            self.assertTrue(m.run_tasks(p, ['rtthread'], opts))
            app = root / 'RTThread/App/rtthread_app.c'
            text = app.read_text(encoding='utf-8') + '\n/* user application remains mine */\n'
            app.write_text(text, encoding='utf-8')
            self.assertFalse(m.run_tasks(m.KeilProject(p.path), ['rtthread'], opts))
            self.assertEqual(app.read_text(encoding='utf-8'), text)
            self.assertTrue(m.uninstall_component(m.KeilProject(p.path), 'rtthread', yes=True))
            for path, value in originals.items():
                self.assertEqual(path.read_bytes(), value)
            self.assertTrue(app.is_file())  # changed task source is never deleted

    def test_timebase_in_user_task_is_not_exempt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = fixture(root)
            app = root / 'RTThread/App/rtthread_app.c'
            app.parent.mkdir(parents=True)
            original = m.rtthread_app_templates('stm32f4xx.h')[1]
            app.write_text(original, encoding='utf-8')
            p.add_file('App', app.name, 1, str(app))
            m.validate_spl_rtos(p, m.read_source_text)
            app.write_text(original.replace('++g_rtthread_heartbeat;', 'SysTick->CTRL = 0;'), encoding='utf-8')
            with self.assertRaises(m.ToolError):
                m.validate_spl_rtos(p, m.read_source_text)

    def test_unsafe_preflight_no_download_or_writes(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            p = fixture(root)
            (root / 'User/entry.c').write_text(MAIN.replace('Board_Init();', 'SysTick_Config(100);'))
            before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
            opts = SimpleNamespace(rtthread='auto', no_download=True, yes=True, dry_run=False)
            self.assertFalse(m.run_tasks(p, ['rtthread'], opts))
            self.assertTrue(p._planning_failed)
            self.assertEqual(before, {path: path.read_bytes() for path in root.rglob('*') if path.is_file()})


if __name__ == '__main__':
    unittest.main()
