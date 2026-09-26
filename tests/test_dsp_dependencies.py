"""SDK dependency trimming must fail before creating a broken Keil project."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import keil_port_tool as m
from test_spl_adapter import project


class DSPDependenciesTests(unittest.TestCase):
    def sdk(self, root):
        sdk = root / 'sdk'
        for name, body in {
            'StatisticsFunctions': 'float arm_mean(void) { return arm_sqrt_q15(1); }',
            'FastMathFunctions': 'int arm_sqrt_q15(int a) { return sinTable[a]; }',
            'CommonTables': 'const int sinTable[2] = {1,2};',
            'MatrixFunctions': 'void arm_matrix(void) { const int a = 0; }',
        }.items():
            folder = sdk / 'Source' / name
            folder.mkdir(parents=True)
            (folder / (name + '.c')).write_text('#include "impl.c"\n')
            (folder / 'impl.c').write_text(body)
        (sdk / 'Include').mkdir()
        (sdk / 'Include/arm_math.h').write_text('#include "cmsis_compiler.h"\n')
        return sdk

    def test_transitive_functions_and_global_tables_not_local_constants(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.sdk(Path(td))
            available = m.cmsis_dsp_source_files(root)
            selected = [p for p in available if p.parent.name == 'StatisticsFunctions']
            missing = m.missing_dsp_modules(root, available, selected, m.read_source_text)
            self.assertEqual({p.parent.name for p in missing}, {'CommonTables', 'FastMathFunctions'})
            self.assertEqual(m.missing_dsp_modules(root, available, selected + missing, m.read_source_text), [])

    def test_aggregate_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = self.sdk(Path(td))
            available = m.cmsis_dsp_source_files(root)
            available[0].write_text('#include "../../../../outside.c"\n')
            with self.assertRaises(m.ToolError):
                m.missing_dsp_modules(root, available, available[:1], m.read_source_text)

    def test_missing_dependency_and_old_core_make_no_writes(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            p = project(root)
            sdk = self.sdk(root)
            core = root / 'User/core_cm4.h'
            opts = SimpleNamespace(cmsis_dsp=str(sdk), dsp_modules=['StatisticsFunctions'],
                                   yes=True, no_download=True, dry_run=False)
            for header in ('#include "core_cmFunc.h"', '#include "cmsis_compiler.h"'):
                core.write_text(header)
                before = {f: f.read_bytes() for f in root.rglob('*') if f.is_file()}
                p = m.KeilProject(p.path)
                self.assertFalse(m.run_tasks(p, ['cmsis_dsp'], opts))
                self.assertTrue(p._planning_failed)
                self.assertEqual(before, {f: f.read_bytes() for f in root.rglob('*') if f.is_file()})

    def test_existing_modules_count_but_disabled_files_do_not(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root = Path(td)
            p = project(root)
            sdk = self.sdk(root)
            (root / 'User/core_cm4.h').write_text('#include "cmsis_compiler.h"')
            opts = SimpleNamespace(cmsis_dsp=str(sdk), dsp_modules=[],
                                   yes=True, no_download=True, dry_run=False)
            self.assertTrue(m.run_tasks(p, ['cmsis_dsp'], opts))
            opts.dsp_modules = ['StatisticsFunctions']
            p = m.KeilProject(p.path)
            self.assertFalse(m.run_tasks(p, ['cmsis_dsp'], opts))
            self.assertFalse(getattr(p, '_planning_failed', False))
            p = m.KeilProject(p.path)
            import xml.etree.ElementTree as ET
            node = p.root.find('.//File[FileName="FastMathFunctions.c"]')
            ET.SubElement(ET.SubElement(ET.SubElement(node, 'FileOption'), 'CommonProperty'), 'IncludeInBuild').text = '0'
            p.save()
            self.assertFalse(m.run_tasks(p, ['cmsis_dsp'], opts))
            self.assertTrue(p._planning_failed)


if __name__ == '__main__':
    unittest.main()
