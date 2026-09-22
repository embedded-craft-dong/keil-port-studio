"""Regressions found on hosted Windows: console encoding and path aliases."""
import ctypes
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('portability_tool', Path(__file__).parents[1] / 'keil_port_tool.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class PortabilityTests(unittest.TestCase):
    def test_cp1252_log_keeps_unicode_sink_and_does_not_abort(self):
        stream = io.BytesIO()
        output = io.TextIOWrapper(stream, encoding='cp1252', errors='strict')
        seen = []
        with patch.object(sys, 'stdout', output), patch.object(m, '_LOG_SINK', seen.append):
            m.log('中文 / English')
        output.flush()
        self.assertEqual(seen, ['中文 / English'])
        self.assertIn(b'English', stream.getvalue())
        self.assertIn(b'\\u4e2d', stream.getvalue())
        output.detach()

    @unittest.skipUnless(os.name == 'nt', 'Windows 8.3 path aliases')
    def test_short_path_selection_matches_resolved_scan(self):
        with tempfile.TemporaryDirectory(prefix='kps long path selection ') as folder:
            root = Path(folder)
            source = root / 'User' / 'new.c'
            source.parent.mkdir()
            source.write_text('int example;\n', encoding='utf-8')
            buffer = ctypes.create_unicode_buffer(32768)
            count = ctypes.windll.kernel32.GetShortPathNameW(str(source), buffer, len(buffer))
            self.assertTrue(count)
            alias = Path(buffer.value)
            if str(alias).lower() == str(source.resolve()).lower():
                self.skipTest('8.3 aliases are disabled on this volume')
            project = root / 'demo.uvprojx'
            project.write_text('<Project><Targets><Target><TargetName>Debug</TargetName><TargetOption>'
                               '<TargetArmAds><Cads><VariousControls/></Cads></TargetArmAds>'
                               '</TargetOption><Groups/></Target></Targets></Project>', encoding='utf-8')
            proj = m.KeilProject(project)
            report = m.Report('add_files')
            m.do_add_files(proj, SimpleNamespace(scan_dirs=str(source.parent), scan_files={alias}, include_h=False), report)
            self.assertIn('new.c', [record['name'] for record in proj.file_records()])


if __name__ == '__main__':
    unittest.main(verbosity=2)
