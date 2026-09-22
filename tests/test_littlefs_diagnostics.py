"""LittleFS diagnostics safety and component lifecycle regressions.

Run: python work/test_littlefs_diagnostics.py
Optional actual-library compiler check: LITTLEFS_SDK + ARMCC + FROMELF env vars.
The Windows paths below are read-only discovery fallbacks; all output is temp.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'keil_littlefs_diagnostics', ROOT / 'keil_port_tool.py' if (ROOT / 'keil_port_tool.py').is_file()
    else ROOT / 'outputs' / 'keil_port_tool.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
MACRO = 'LFS_DEFINES=littlefs_port_config.h'


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def target_xml(name, defines='BASE'):
    return '''<Target><TargetName>%s</TargetName><pCCUsed>V5.06</pCCUsed><uAC6>0</uAC6>
<TargetOption><TargetCommonOption><Device>STM32F407VG</Device>
<Cpu>CPUTYPE("Cortex-M4") FPU</Cpu></TargetCommonOption><TargetArmAds><Cads>
<VariousControls><Define>%s</Define><IncludePath>../Core/Inc</IncludePath>
<MiscControls/></VariousControls></Cads></TargetArmAds></TargetOption><Groups/>
</Target>''' % (name, defines)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'Demo'
        self.project = self.root / 'MDK-ARM' / 'Demo.uvprojx'
        self.sdk = self.base / 'SDK' / 'littlefs'
        write(self.project, '<Project><Targets>' + target_xml('Debug') + '</Targets></Project>')
        write(self.root / 'Core' / 'Src' / 'main.c', 'int main(void) { for (;;) {} }\n')
        write(self.root / 'Core' / 'Inc' / 'main.h', '/* user header */\n')
        for name in ('lfs.c', 'lfs.h', 'lfs_util.c'):
            write(self.sdk / name, '/* fixture */\n')
        write(self.sdk / 'lfs_util.h', '#ifdef LFS_DEFINES\n/* supported */\n#endif\n')
        self.config = self.root / 'Config' / 'LittleFS' / 'littlefs_port_config.h'
        self.diag = self.config.with_name('littlefs_port_diagnostics.c')
        self.options = SimpleNamespace(littlefs=str(self.sdk), littlefs_files=None,
            littlefs_mode='baremetal', littlefs_port=True, interactive=False,
            yes=True, dry_run=False, no_download=True, sdk_dir=None, diff_file=None)

    def plan(self, targets=None):
        proj = m.KeilProject(self.project)
        if targets:
            proj.select_targets(targets)
        report = m.Report('littlefs')
        m.do_littlefs(proj, self.options, report)
        return proj, report

    def run_install(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return m.run_tasks(m.KeilProject(self.project), ['littlefs'], self.options)

    def test_preview_contains_config_source_macro_and_does_not_write(self):
        before = self.project.read_bytes()
        proj, report = self.plan()
        generated = {path.name for path, _, _ in report.gen_files}
        self.assertTrue({'littlefs_port_config.h', 'littlefs_port_diagnostics.c'} <= generated)
        preview = m.build_diff_preview(proj, [report])
        self.assertIn(MACRO, preview)
        self.assertIn('LittleFS_AssertFailed', preview)
        self.assertIn('littlefs_port_diagnostics.c', preview)
        self.assertEqual(self.project.read_bytes(), before)
        self.assertFalse(self.config.exists())
        self.assertFalse((self.root / 'Middlewares').exists())

    def test_dry_run_has_no_project_or_filesystem_mutation(self):
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob('*') if path.is_file()}
        self.options.dry_run = True
        self.assertFalse(self.run_install())
        after = {str(path.relative_to(self.root)): path.read_bytes()
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_install_rerun_uninstall_ownership(self):
        self.assertTrue(self.run_install())
        self.assertTrue(self.config.is_file() and self.diag.is_file())
        manifest_path = self.root / '.keil-port-tool' / 'manifest.json'
        record = json.loads(manifest_path.read_text(encoding='utf-8'))['components']['littlefs']
        self.assertIn(MACRO, record['defines'])
        generated = {item['path']: item for item in record['generated_files']}
        for path in (self.config, self.diag):
            item = generated[path.relative_to(self.root).as_posix()]
            self.assertTrue(item['created'])
        self.assertTrue(any(item['name'] == self.diag.name for item in record['project_files']))
        original = self.config.read_bytes(), self.diag.read_bytes(), self.project.read_bytes()
        self.assertFalse(self.run_install())
        self.assertEqual(original, (self.config.read_bytes(), self.diag.read_bytes(), self.project.read_bytes()))
        previews = []
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(m.uninstall_component(m.KeilProject(self.project), 'littlefs',
                preview_callback=lambda text: previews.append(text) or False))
        self.assertIn(self.config.name, previews[0])
        self.assertIn(self.diag.name, previews[0])
        self.assertTrue(self.config.is_file())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(m.uninstall_component(m.KeilProject(self.project), 'littlefs', yes=True))
        self.assertFalse(self.config.exists() or self.diag.exists())
        after = m.KeilProject(self.project)
        self.assertNotIn(MACRO, m._project_define_tokens(after))
        self.assertNotIn(self.diag.name, self.project.read_text(encoding='utf-8'))

    def test_custom_configuration_is_not_claimed_or_replaced(self):
        for definition in ('LFS_CONFIG=user_lfs.h', 'LFS_DEFINES=user_lfs.h', MACRO):
            with self.subTest(definition=definition):
                write(self.project, '<Project><Targets>' + target_xml('Debug', definition) + '</Targets></Project>')
                proj, report = self.plan()
                self.assertIn(definition, m._project_define_tokens(proj))
                self.assertNotIn(MACRO, report.defines)
                self.assertFalse(any(path.name == self.config.name for path, _, _ in report.gen_files))
                self.assertTrue(any('保留用户 LittleFS 配置' in text for text in report.warnings))

    def test_misc_configuration_is_preserved(self):
        text = self.project.read_text(encoding='utf-8').replace(
            '<MiscControls/>', '<MiscControls>-DLFS_DEFINES=mine.h</MiscControls>')
        write(self.project, text)
        proj, report = self.plan()
        self.assertNotIn(MACRO, m._project_define_tokens(proj))
        self.assertTrue(any('MiscControls' in text for text in report.warnings))

    def test_file_level_configuration_is_preserved(self):
        group = '''<Groups><Group><GroupName>User</GroupName><Files><File>
<FileName>main.c</FileName><FileType>1</FileType><FilePath>../Core/Src/main.c</FilePath>
<FileOption><FileArmAds><Cads><VariousControls><Define>LFS_CONFIG=file_config.h</Define>
</VariousControls></Cads></FileArmAds></FileOption></File></Files></Group></Groups>'''
        write(self.project, self.project.read_text(encoding='utf-8').replace('<Groups/>', group))
        proj, report = self.plan()
        self.assertNotIn(MACRO, m._project_define_tokens(proj))
        self.assertTrue(any('file_config.h' in text for text in report.warnings))

    def test_unselected_target_custom_config_does_not_block_selected_target(self):
        write(self.project, '<Project><Targets>' + target_xml('Debug') +
              target_xml('Release', 'LFS_DEFINES=release_lfs.h') + '</Targets></Project>')
        proj, report = self.plan(['Debug'])
        self.assertIn(MACRO, m._project_define_tokens(proj))
        self.assertFalse(report.warnings)
        release = proj.all_targets[1].findtext('.//Cads/VariousControls/Define')
        self.assertEqual(release, 'LFS_DEFINES=release_lfs.h')

    def test_no_port_still_adds_independent_diagnostics(self):
        self.options.littlefs_port = False
        proj, report = self.plan()
        generated = {path.name for path, _, _ in report.gen_files}
        self.assertEqual(generated, {'littlefs_port_config.h', 'littlefs_port_diagnostics.c'})
        self.assertIn(MACRO, report.defines)
        self.assertIn(('LittleFS/Port', self.diag.name), report.files)

    def test_unowned_collision_preserves_both_file_and_configuration(self):
        for path in (self.config, self.diag):
            with self.subTest(path=path.name):
                write(path, '/* user-owned content */\n')
                proj, report = self.plan()
                self.assertEqual(path.read_text(encoding='utf-8'), '/* user-owned content */\n')
                self.assertNotIn(MACRO, m._project_define_tokens(proj))
                self.assertTrue(any('未归属本工具' in text for text in report.warnings))
                path.unlink()

    def test_edited_config_survives_rerun_and_uninstall(self):
        self.assertTrue(self.run_install())
        edited = self.config.read_text(encoding='utf-8') + '\n/* user log adapter */\n'
        write(self.config, edited)
        self.run_install()
        self.assertEqual(self.config.read_text(encoding='utf-8'), edited)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(m.uninstall_component(m.KeilProject(self.project), 'littlefs', yes=True))
        self.assertEqual(self.config.read_text(encoding='utf-8'), edited)
        self.assertFalse(self.diag.exists())

    def test_unsupported_configuration_entry_has_explicit_warning(self):
        write(self.sdk / 'lfs_util.h', '/* legacy version without injected defines */\n')
        proj, report = self.plan()
        self.assertNotIn(MACRO, report.defines)
        self.assertTrue(any('未提供 LFS_DEFINES' in text for text in report.warnings))

    def test_generated_header_evaluates_assert_once_and_overridable_hook(self):
        gcc = shutil.which('gcc') or ('C:/MinGW/bin/gcc.exe' if Path('C:/MinGW/bin/gcc.exe').is_file() else None)
        if not gcc:
            self.skipTest('native gcc unavailable')
        header, source = m.littlefs_diagnostics_templates()
        write(self.config, header)
        write(self.diag, source)
        test_c = self.base / 'assert_override.c'
        write(test_c, '''#include "littlefs_port_config.h"
#include <setjmp.h>
static jmp_buf jump;
static int calls;
void LittleFS_AssertFailed(const char *expr, const char *file, unsigned int line) {
    (void)expr; (void)file; (void)line; ++calls; longjmp(jump, 1);
}
int main(void) {
    volatile int evaluated = 0;
    LFS_ASSERT(++evaluated == 1);
    LFS_TRACE("test %d", 1); LFS_DEBUG("test"); LFS_WARN("test"); LFS_ERROR("test");
    if (setjmp(jump) == 0) { LFS_ASSERT(++evaluated == 99); return 1; }
    return !(evaluated == 2 && calls == 1);
}
''')
        exe = self.base / 'assert_override.exe'
        subprocess.run([gcc, '-std=c99', '-Wall', '-Wextra', '-Werror', '-DNDEBUG',
            '-DLFS_YES_TRACE', '-I', str(self.config.parent), str(test_c), str(self.diag),
            '-o', str(exe)], check=True, capture_output=True, text=True)
        subprocess.run([str(exe)], check=True, capture_output=True, text=True)

    def test_actual_littlefs_ac5_object_has_no_host_diagnostics_dependencies(self):
        if not all(os.environ.get(name) for name in ('LITTLEFS_SDK', 'ARMCC', 'FROMELF')):
            self.skipTest('set LITTLEFS_SDK, ARMCC and FROMELF for the optional AC5 test')
        sdk = Path(os.environ['LITTLEFS_SDK'])
        armcc = Path(os.environ['ARMCC'])
        fromelf = Path(os.environ['FROMELF'])
        if not all(path.is_file() for path in (sdk / 'lfs.c', armcc, fromelf)):
            self.skipTest('optional real LittleFS/AC5 tools unavailable')
        header, source = m.littlefs_diagnostics_templates()
        write(self.config, header)
        write(self.diag, source)
        args = [str(armcc), '--cpu=Cortex-M4.fp', '--c99', '--split_sections',
                '-DLFS_NO_MALLOC', '-D' + MACRO, '-I' + str(sdk), '-I' + str(self.config.parent)]
        for rtos in (False, True):
            obj = self.base / ('lfs_%d.o' % rtos)
            subprocess.run(args + (['-DLFS_THREADSAFE'] if rtos else []) +
                ['-c', str(sdk / 'lfs.c'), '-o', str(obj)], check=True, capture_output=True, text=True)
            symbols = subprocess.run([str(fromelf), '--text', '-s', str(obj)],
                check=True, capture_output=True, text=True).stdout
            self.assertIn('LittleFS_AssertFailed', symbols)
            for unwanted in ('__2printf', '__aeabi_assert', 'puts', '_sys_open', '_sys_write'):
                self.assertNotIn(unwanted, symbols)
        subprocess.run(args + ['-c', str(self.diag), '-o', str(self.base / 'diagnostics.o')],
                       check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
