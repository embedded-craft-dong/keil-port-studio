"""Open-window discovery and real Tk selector tests; no user projects are edited."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from kps_core import keil_windows as backend

spec = importlib.util.spec_from_file_location('window_tool', Path(__file__).parents[1] / 'keil_port_tool.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

XML = '''<Project><Targets><Target><TargetName>Debug</TargetName><TargetOption>
<TargetCommonOption><Device>STM32F407ZG</Device></TargetCommonOption>
<TargetArmAds><Cads><VariousControls><Define/><IncludePath/></VariousControls></Cads></TargetArmAds>
</TargetOption><Groups/></Target></Targets></Project>'''


class CaptionTests(unittest.TestCase):
    def test_unicode_spaces_old_extension_quotes_unc(self):
        for path in (r'C:\工程 空格\same.uvprojx', r'D:\test\old.UVPROJ',
                     r'C:\Study - µVision\name - µVision.uvprojx',
                     r'\\server\share\demo.uvprojx'):
            for suffix in (' - µVision', ' - μVision', ' - uVision5', ''):
                with self.subTest(path=path, suffix=suffix):
                    self.assertEqual(str(backend.project_from_title('"' + path + '"' + suffix)), str(Path(path)))
        self.assertEqual(str(backend.project_from_title(r'C:\a.uvprojx* - µVision')), str(Path(r'C:\a.uvprojx')))

    def test_ambiguous_and_unsupported_titles_never_guess(self):
        for title in ('demo - µVision', 'demo.uvprojx - µVision', 'µVision',
                      r'C:relative.uvprojx - µVision', r'C:\demo.uvmpw - µVision',
                      r'C:\file.c - µVision', r'C:\a.uvprojx - [source.c] - µVision'):
            with self.subTest(title=title):
                self.assertIsNone(backend.project_from_title(title))

    @unittest.skipUnless(os.name == 'nt', 'Win32 path discovery')
    def test_discovery_handles_missing_files_and_keeps_duplicate_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder) / '工程 name.uvprojx'
            project.write_text(XML, encoding='utf-8')
            rows = [(1, 10, str(project) + ' - µVision'),
                    (2, 11, str(project) + ' - µVision'),
                    (3, 12, str(project.with_name('missing.uvprojx')) + ' - µVision'),
                    (4, 13, 'unknown - µVision')]
            with patch.object(backend, '_native_windows', return_value=rows):
                result = backend.discover_keil_windows()
            self.assertEqual(len(result), 4)
            self.assertEqual(result[0].project, project)
            self.assertEqual(result[1].project, project)
            self.assertEqual(result[2].reason, 'missing')
            self.assertIsNone(result[2].project)
            self.assertEqual(result[3].reason, 'unresolved')

    def test_non_windows_is_empty(self):
        with patch.object(backend.os, 'name', 'posix'), patch.object(backend, '_native_windows') as native:
            self.assertEqual(backend.discover_keil_windows(), [])
            native.assert_not_called()


class WindowGuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kps-windows-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.projects = []
        for folder in ('one', 'two'):
            path = self.base / folder / '同名 project.uvprojx'
            path.parent.mkdir()
            path.write_text(XML, encoding='utf-8')
            self.projects.append(path)
        self.windows = [backend.KeilWindow(i + 100, i + 200, str(p) + ' - µVision', p)
                        for i, p in enumerate(self.projects)]
        self.discover = patch.object(m, 'discover_keil_windows', return_value=self.windows).start()
        self.find = patch.object(m, '_find_projects', return_value=[]).start()
        patch.object(m, 'user_settings_path', return_value=self.base / 'settings.json').start()
        self.addCleanup(patch.stopall)
        self.app = None
        self.errors = []
        self.addCleanup(self.close)

    def close(self):
        if self.app is not None:
            self.app._close()
            self.app = None

    def launch(self, initial=None):
        self.app = m.KeilPortGUI(initial_project=initial, language='zh-CN', scaling=96/72)
        self.app.root.report_callback_exception = lambda *error: self.errors.append(error)
        return self.app

    def wait_scan(self):
        deadline = time.monotonic() + 5
        while self.app._keil_scan_after is not None or self.app._keil_scan_running:
            self.app.root.update()
            if time.monotonic() > deadline:
                self.fail('Window detection did not finish')
            time.sleep(.01)
        self.assertEqual(self.errors, [])

    def choose(self, index):
        self.app.keil_window_combo.current(index)
        self.app.keil_window_combo.event_generate('<<ComboboxSelected>>')
        self.wait_scan()

    def test_one_window_auto_selected_and_targets_loaded(self):
        self.discover.return_value = self.windows[:1]
        app = self.launch()
        self.wait_scan()
        self.assertEqual(app.project_var.get(), str(self.projects[0]))
        # Clam defaults to white text for readonly+focus. Our white field
        # background must override that mapping, including inherited styles.
        from tkinter import ttk
        style = ttk.Style(app.root)
        for name in ('TCombobox', 'Project.TCombobox'):
            foreground = style.lookup(name, 'foreground', ('readonly', 'focus'))
            background = style.lookup(name, 'fieldbackground', ('readonly', 'focus'))
            self.assertNotEqual(app.root.winfo_rgb(foreground), app.root.winfo_rgb(background))
        self.assertIn('Debug', app.target_combo.cget('values'))
        self.assertEqual(app.keil_window_combo.current(), 0)

    def test_multiple_windows_no_arbitrary_choice_and_safe_switch(self):
        app = self.launch()
        self.wait_scan()
        self.assertEqual(app.project_var.get(), '')
        labels = app.keil_window_combo.cget('values')
        self.assertEqual(len(set(labels)), 2)
        self.assertTrue(all('PID' in label and 'HWND' in label for label in labels))
        self.assertTrue(all(label.index('PID') < label.index(str(self.projects[i]))
                            for i, label in enumerate(labels)))
        self.choose(0)
        self.assertEqual(app.keil_window_var.get(), app.keil_window_combo.cget('values')[0])
        app.scan_var.set(str(self.projects[0].parent / 'old_sources'))
        app.add_tree.set_files(self.projects[0].parent, [(self.projects[0].parent / 'old.c', '')])
        app._pending_preset_selections = {'add_tree': ['old.c']}
        app.target_var.set('Debug')
        self.choose(1)
        self.assertEqual(app.project_var.get(), str(self.projects[1]))
        self.assertTrue(Path(app.scan_var.get()).samefile(self.projects[1].parent))
        self.assertEqual(app.add_tree.file_count(), 0)
        self.assertEqual(app._pending_preset_selections, {})
        self.assertEqual(app.target_var.get(), '全部 Target')
        for project in self.projects:
            self.assertEqual(project.read_text(encoding='utf-8'), XML)

    def test_explicit_initial_path_not_overridden(self):
        self.discover.return_value = self.windows[:1]
        app = self.launch(self.projects[1])
        self.wait_scan()
        self.assertEqual(app.project_var.get(), str(self.projects[1]))

    def test_no_windows_uses_previous_single_cwd_fallback(self):
        self.discover.return_value = []
        self.find.return_value = [self.projects[0]]
        app = self.launch()
        self.wait_scan()
        self.assertTrue(Path(app.project_var.get()).samefile(self.projects[0]))
        self.assertEqual(str(app.keil_window_combo.cget('state')), 'disabled')

    def test_unknown_window_does_not_guess_cwd_project(self):
        self.discover.return_value = [backend.KeilWindow(1, 2, 'project - µVision')]
        self.find.return_value = [self.projects[0]]
        app = self.launch()
        self.wait_scan()
        self.assertEqual(app.project_var.get(), '')
        self.choose(0)
        self.assertEqual(app.project_var.get(), '')
        self.assertIn('手动', app.status_var.get())

    def test_background_scan_cannot_overwrite_manual_choice(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.discover.side_effect = lambda: (gate.wait(3), self.windows[:1])[1]
        app = self.launch()
        app.refresh_keil_windows()
        beats = []
        app.root.after(10, lambda: beats.append(True))
        end = time.monotonic() + .1
        while time.monotonic() < end:
            app.root.update()
            time.sleep(.005)
        self.assertTrue(beats)
        app.project_var.set(str(self.projects[1]))
        gate.set()
        self.wait_scan()
        self.assertEqual(app.project_var.get(), str(self.projects[1]))

    def test_window_switch_is_revalidated_when_selected(self):
        app = self.launch()
        self.wait_scan()
        self.discover.return_value = [backend.KeilWindow(100, 200, 'changed', self.projects[1])]
        self.choose(0)
        self.assertEqual(app.project_var.get(), str(self.projects[1]))

    def test_closed_window_keeps_previous_project(self):
        app = self.launch(self.projects[1])
        self.wait_scan()
        self.discover.return_value = []
        self.choose(0)
        self.assertEqual(app.project_var.get(), str(self.projects[1]))

    def test_two_windows_for_same_project_preserve_selected_window(self):
        self.discover.return_value = [self.windows[0],
                                     backend.KeilWindow(101, 201, 'same', self.projects[0])]
        app = self.launch()
        self.wait_scan()
        self.choose(1)
        self.assertEqual(app.keil_window_combo.current(), 1)
        self.choose(0)
        self.assertEqual(app.keil_window_combo.current(), 0)

    def test_discovery_error_manual_selection_still_works(self):
        self.discover.side_effect = OSError('fixture denied')
        app = self.launch()
        self.wait_scan()
        self.assertIn('检测失败', app.keil_window_var.get())
        with patch.object(m.filedialog, 'askopenfilename', return_value=str(self.projects[0])):
            app.browse_project()
        self.assertEqual(app.project_var.get(), str(self.projects[0]))

    def test_language_switch_retains_window_choices(self):
        app = self.launch()
        self.wait_scan()
        self.choose(1)
        app.change_language('en')
        app.root.update()
        self.assertEqual(len(app.keil_window_combo.cget('values')), 2)
        self.assertEqual(app.keil_window_combo.current(), 1)
        self.assertEqual(app.keil_refresh_button.cget('text'), 'Refresh windows')

    def test_busy_operation_prevents_window_switch(self):
        app = self.launch(self.projects[0])
        self.wait_scan()
        app._operation_busy = True
        try:
            self.choose(1)
            self.assertEqual(app.project_var.get(), str(self.projects[0]))
        finally:
            app._operation_busy = False

    def test_close_during_scan_is_safe(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.discover.side_effect = lambda: (gate.wait(3), [])[1]
        app = self.launch()
        app.refresh_keil_windows()
        self.close()
        gate.set()
        self.assertEqual(self.errors, [])

    def test_missing_file_after_listing_does_not_replace_selection(self):
        app = self.launch(self.projects[1])
        self.wait_scan()
        self.discover.return_value = [backend.KeilWindow(100, 200, 'missing', None, 'missing')]
        self.choose(0)
        self.assertEqual(app.project_var.get(), str(self.projects[1]))

    def test_scan_timeout_restores_manual_controls(self):
        app = self.launch(self.projects[1])
        self.wait_scan()
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.discover.side_effect = lambda: (gate.wait(3), self.windows)[1]
        with patch.object(m.time, 'monotonic', return_value=0):
            app.refresh_keil_windows()
        with patch.object(m.time, 'monotonic', return_value=9):
            end = time.perf_counter() + 1
            while app._keil_scan_running and time.perf_counter() < end:
                app.root.update()
                time.sleep(.01)
        gate.set()
        self.assertFalse(app._keil_scan_running)
        self.assertEqual(str(app.keil_refresh_button.cget('state')), 'normal')
        self.assertEqual(app.project_var.get(), str(self.projects[1]))
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
