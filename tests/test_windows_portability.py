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
from kps_core.filesystem import extended_windows_path, filesystem_path, copy_tree, remove_tree


class PortabilityTests(unittest.TestCase):
    def test_extended_prefix_is_io_only_and_handles_unc(self):
        self.assertEqual(extended_windows_path(r'C:\sdk\..\sdk\file.c'), r'\\?\C:\sdk\file.c')
        self.assertEqual(extended_windows_path(r'\\server\share\sdk\file.c'), r'\\?\UNC\server\share\sdk\file.c')
        value = r'\\?\C:\sdk\file.c'
        self.assertEqual(extended_windows_path(value), value)

    @unittest.skipUnless(os.name == 'nt', 'Windows extended-length tree I/O')
    def test_deep_tree_hash_copy_transaction_restore_and_cleanup(self):
        with tempfile.TemporaryDirectory(prefix='kps-deep-sdk-') as folder:
            root = Path(folder).resolve()
            project = root / 'app.uvprojx'
            project.write_text('<Project><Targets><Target><TargetName>T</TargetName><TargetOption>'
                '<TargetArmAds><Cads><VariousControls/></Cads></TargetArmAds></TargetOption>'
                '<Groups/></Target></Targets></Project>', encoding='utf-8')
            p = m.KeilProject(project)
            sdk = root / 'SDK'; clone = root / 'clone'
            relative = Path('中文目录' + 'a' * 85) / ('b' * 95) / ('c' * 95) / 'driver.h'
            leaf = sdk / relative
            self.assertGreater(len(str(leaf)), 300)
            try:
                filesystem_path(leaf.parent).mkdir(parents=True)
                filesystem_path(leaf).write_bytes(b'original')
                original = m.sha256_tree(sdk)
                copy_tree(sdk, clone)
                self.assertEqual(m.sha256_tree(clone), original)
                self.assertEqual(filesystem_path(clone / relative).read_bytes(), b'original')
                transaction = m.ProjectTransaction(p, 'deep-fixture', [])
                transaction.snapshot_dir(sdk)
                transaction.created_dir(clone)
                transaction.save_meta('prepared')
                filesystem_path(leaf).write_bytes(b'changed')
                self.assertNotEqual(m.sha256_tree(sdk), original)
                transaction.rollback('intentional test')
                self.assertEqual(m.sha256_tree(sdk), original)
                self.assertFalse(clone.exists())
                # Win32 extended prefixes must never be persisted as project paths.
                self.assertNotIn('\\\\?\\', project.read_text(encoding='utf-8'))
                self.assertNotIn('\\\\?\\', transaction.meta_path.read_text(encoding='utf-8'))
            finally:
                for path in (sdk, clone, root / '.keil-port-tool'):
                    self.assertIn(root, path.resolve().parents)
                    if path.exists():
                        remove_tree(path)

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

    def test_download_progress_cp1252_and_no_console(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.bin'
            source.write_bytes(b'payload' * 1024)
            stream = io.BytesIO()
            output = io.TextIOWrapper(stream, encoding='cp1252', errors='strict')
            with patch.object(sys, 'stdout', output), patch.object(m, '_LOG_SINK', None):
                m.download_file(source.as_uri(), Path(folder) / 'copied.bin', '中文')
            output.flush()
            self.assertIn(b'100%', stream.getvalue())
            self.assertEqual((Path(folder) / 'copied.bin').read_bytes(), source.read_bytes())
            output.detach()
            with patch.object(sys, 'stdout', None), patch.object(m, '_LOG_SINK', None):
                m.download_file(source.as_uri(), Path(folder) / 'windowed.bin', '中文')
            self.assertEqual((Path(folder) / 'windowed.bin').read_bytes(), source.read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
