"""Button callback integration tests with REAL files in an isolated directory.

Only native file/confirmation dialogs are substituted. No production backend is
mocked except the explicit build-error test. This is not a physical mouse test.
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('actions_tool', Path(__file__).parents[1] / 'keil_port_tool.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

XML = '''<Project><Targets><Target><TargetName>Debug</TargetName><TargetOption>
<TargetCommonOption><Device>STM32F407ZG</Device><Cpu>CPUTYPE("Cortex-M4") FPU</Cpu></TargetCommonOption>
<TargetArmAds><Cads><VariousControls><Define>BASE</Define><IncludePath/></VariousControls></Cads></TargetArmAds>
</TargetOption><Groups/></Target></Targets></Project>'''

def descendants(w):
    for child in w.winfo_children():
        yield child
        yield from descendants(child)

class ActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kps-actions-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / 'fixture.uvprojx'
        self.project.write_text(XML, encoding='utf-8')
        self.settings = self.base / 'settings.json'
        self.addCleanup(patch.stopall)
        patch.object(m, 'user_settings_path', return_value=self.settings).start()
        self.errors = patch.object(m.messagebox, 'showerror').start()
        self.infos = patch.object(m.messagebox, 'showinfo').start()
        self.confirm = patch.object(m.messagebox, 'askyesno', return_value=True).start()
        self.save_dialog = patch.object(m.filedialog, 'asksaveasfilename').start()
        self.open_dialog = patch.object(m.filedialog, 'askopenfilename').start()
        self.g = m.KeilPortGUI(initial_project=self.project, scaling=96/72)
        self.addCleanup(lambda: self.g._close())
        self.callback_errors = []
        self.g.root.report_callback_exception = lambda *exc: self.callback_errors.append(exc)
        self.g.root.update()

    def click(self, label, scope=None):
        matches = [w for w in descendants(scope or self.g.root)
                   if isinstance(w, m.ttk.Button) and w.cget('text') == label]
        self.assertEqual(len(matches), 1, label)
        matches[0].invoke()
        self.g.root.update()
        self.assertEqual(self.callback_errors, [], label)

    def dialog(self, title):
        return next(w for w in self.g.root.winfo_children()
                    if isinstance(w, m.tk.Toplevel) and w.title() == title)

    def test_device_generation_and_reference_removal_buttons(self):
        with patch.object(self.g, 'confirm_diff_preview', return_value=True):
            self.click('工程工具'); self.click('器件驱动生成器…')
            win=self.dialog('器件驱动生成器')
            self.assertEqual(self.g.root.grab_current(),win)
            self.g.root.update()
            for widget in descendants(win):
                if isinstance(widget,(m.ttk.Button,m.ttk.Combobox,m.ttk.Checkbutton)) and widget.winfo_ismapped():
                    self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),win.winfo_rooty()+win.winfo_height(),str(widget))
            self.click('预览生成…',win)
            self.assertFalse(self.errors.called,str(self.errors.call_args))
            self.assertTrue((self.base/'KPS/DeviceDrivers/kps_board_port.c').is_file())
            self.assertTrue((self.base/'KPS/DeviceDrivers/DRIVER_GUIDE.md').is_file())
            # Owned driver references must not be removed piecemeal.
            self.click('工程工具'); self.click('移除文件与路径…')
            win=self.dialog('移除文件与路径')
            self.click('勾选筛选结果',win); self.click('预览移除…',win)
            self.assertTrue(self.errors.called)
            self.assertIn('Managed component',str(self.errors.call_args))
            self.errors.reset_mock()
            # Independent unowned file, real writes and callback-based preview.
            source=self.base/'extra.c'; source.write_text('int extra;\n')
            p=m.KeilProject(self.project); p.add_file('User','extra.c',1,'extra.c'); p.save()
            self.click('工程工具'); self.click('移除文件与路径…')
            win=self.dialog('移除文件与路径')
            entry=next(w for w in descendants(win) if isinstance(w,m.ttk.Entry))
            entry.insert(0,'extra.c'); self.g.root.update()
            self.click('勾选筛选结果',win); self.click('预览移除…',win)
            self.assertFalse(self.errors.called,str(self.errors.call_args))
            self.assertTrue(source.is_file())
            self.assertFalse(any(r['name']=='extra.c' for r in m.KeilProject(self.project).file_records()))
            self.g.change_language('en'); self.g.root.update()
            self.click('Project tools'); self.click('Device driver generator…')
            win=self.dialog('Device driver generator')
            self.g.root.update()
            for widget in descendants(win):
                if isinstance(widget,(m.ttk.Button,m.ttk.Combobox,m.ttk.Checkbutton)) and widget.winfo_ismapped():
                    self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(),win.winfo_rooty()+win.winfo_height(),str(widget))
            self.click('Close',win)
            self.click('Project tools'); self.click('Remove files and paths…')
            self.click('Close',self.dialog('Remove files and paths'))

    def test_settings_save_restart_cancel_and_validation(self):
        self.click('设置')
        win = self.dialog('Keil Port Studio 设置')
        entries = [w for w in descendants(win) if isinstance(w, m.ttk.Entry)]
        def set_entry(i, value):
            entries[i].delete(0, 'end'); entries[i].insert(0, value)
        set_entry(0, '0'); self.click('保存设置', win)
        self.assertTrue(self.errors.called)
        self.assertFalse(self.settings.exists())
        set_entry(0, '4'); set_entry(3, 'TemporarySkip')
        self.click('保存设置', win)
        self.assertEqual(m.load_user_settings()['download_retries'], 4)
        self.assertIn('temporaryskip', m.SCAN_SKIP_DIRS)
        self.g._close()
        self.g = m.KeilPortGUI(initial_project=self.project, scaling=96/72)
        self.assertEqual(self.g.user_settings['download_retries'], 4)
        self.click('设置'); win = self.dialog('Keil Port Studio 设置')
        entries = [w for w in descendants(win) if isinstance(w, m.ttk.Entry)]
        entries[3].delete(0, 'end')
        self.click('保存设置', win)
        self.assertNotIn('temporaryskip', m.SCAN_SKIP_DIRS)
        original = self.settings.read_bytes()
        self.click('设置'); self.click('取消', self.dialog('Keil Port Studio 设置'))
        self.assertEqual(self.settings.read_bytes(), original)

    def test_preset_roundtrip_all_settings_and_unscanned_selections(self):
        g = self.g
        g.target_var.set('Debug')
        g.scatter_var.set('board.sct'); g.stack_size_var.set('0x2000'); g.heap_size_var.set('0x1000')
        for var in (g.clear_scatter_var, g.clean_includes_var, g.remove_missing_includes_var,
                    g.build_after_var, g.rebuild_var): var.set(True)
        files = [(self.base / 'a.c', ''), (self.base / 'b.c', '')]
        g.add_tree.set_files(self.base, files); g.add_tree.apply_selection_keys(['b.c'])
        expected = g._preset_data()
        path = self.base / 'preset.json'; self.save_dialog.return_value = str(path)
        g.save_preset()
        g.scatter_var.set(''); g.stack_size_var.set(''); g.heap_size_var.set('')
        g.target_var.set('全部 Target')
        for var in (g.clear_scatter_var, g.clean_includes_var, g.remove_missing_includes_var,
                    g.build_after_var, g.rebuild_var): var.set(False)
        g.add_tree.clear(); self.open_dialog.return_value = str(path); g.load_preset()
        self.assertEqual(g.scatter_var.get(), 'board.sct')
        self.assertEqual(g.target_var.get(), 'Debug')
        self.assertTrue(g.build_after_var.get())
        self.assertEqual(g._preset_data(), expected, 'Saving before scanning must retain pending selections')
        g.add_tree.set_files(self.base, files); g._restore_tree_preset('add_tree', g.add_tree)
        self.assertEqual(g.add_tree.selection_keys(), ['b.c'])

    def test_invalid_preset_is_atomic_and_write_error_is_visible(self):
        before = self.g._preset_data()
        path = self.base / 'bad.json'
        path.write_text(json.dumps({'version': 1, 'enabled': {'lvgl': True}, 'values': []}), encoding='utf-8')
        self.open_dialog.return_value = str(path); self.g.load_preset()
        self.assertEqual(self.g._preset_data(), before)
        self.assertTrue(self.errors.called)
        self.save_dialog.return_value = str(self.base)  # directory, not a file
        self.g.save_preset()
        self.assertGreaterEqual(self.errors.call_count, 2)

    def test_exports_gitignore_cancel_idempotency_and_errors(self):
        self.click('工程工具'); win = self.dialog('工程工具')
        before = self.project.read_bytes()
        for ext in ('md', 'json', 'csv'):
            output = self.base / ('inventory.' + ext)
            self.save_dialog.return_value = str(output)
            self.click('导出工程清单（MD / JSON / CSV）', win)
            self.assertGreater(output.stat().st_size, 20)
        data = json.loads((self.base / 'inventory.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(data['targets'][0]['name'], 'Debug')
        self.save_dialog.return_value = str(self.base / 'licenses.md')
        self.click('导出第三方许可证清单', win)
        self.assertTrue((self.base / 'licenses.md').exists())
        self.confirm.return_value = False
        self.click('更新 .gitignore（提交第三方源码）', win)
        self.assertFalse((self.base / '.gitignore').exists())
        self.confirm.return_value = True
        self.click('更新 .gitignore（提交第三方源码）', win)
        initial = (self.base / '.gitignore').read_bytes()
        self.click('更新 .gitignore（提交第三方源码）', win)
        self.assertEqual((self.base / '.gitignore').read_bytes(), initial)
        self.click('更新 .gitignore（忽略第三方源码）', win)
        self.assertIn('/Middlewares/Third_Party/', (self.base / '.gitignore').read_text())
        self.assertEqual(self.project.read_bytes(), before)
        self.save_dialog.return_value = str(self.base)
        self.click('导出工程清单（MD / JSON / CSV）', win)
        self.assertTrue(self.errors.called)

    def test_project_doctor_button_real_readonly_export_and_english(self):
        before = self.project.read_bytes()
        self.click('工程工具')
        tools_win = self.dialog('工程工具')
        self.click('工程体检（只读）', tools_win)
        win = self.dialog('工程体检（只读）')
        # Button.invoke bypasses Tk's input grab. A real click used to be
        # delivered to the tools dialog behind this report, leaving it inert.
        self.assertEqual(self.g.root.grab_current(), win)
        body = next(w for w in descendants(win) if isinstance(w, m.tk.Text))
        self.assertIn('不等于编译或实板验证', body.get('1.0', 'end'))
        self.assertEqual(str(body.cget('state')), 'disabled')
        output = self.base / 'health.json'
        self.save_dialog.return_value = str(output)
        self.click('导出体检报告（JSON）', win)
        report = json.loads(output.read_text(encoding='utf-8'))
        self.assertTrue(report['read_only'])
        self.assertEqual(report['targets'][0]['target'], 'Debug')
        original = output.read_bytes()
        self.click('导出体检报告（JSON）', win)
        self.assertTrue(self.errors.called)
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(self.project.read_bytes(), before)
        self.assertFalse((self.base / '.keil-port-tool').exists())
        self.click('关闭', win)
        self.assertEqual(self.g.root.grab_current(), tools_win)
        self.click('关闭', tools_win)
        self.assertIsNone(self.g.root.grab_current())
        self.g.change_language('en')
        self.g.open_project_doctor()
        self.g.root.update()
        win = self.dialog('Project health check (read-only)')
        body = next(w for w in descendants(win) if isinstance(w, m.tk.Text))
        self.assertIn('No findings within the limited scan scope.', body.get('1.0', 'end'))
        self.assertEqual(self.g.root.grab_current(), win)
        # The title-bar close handler must restore grabs just like the button.
        self.g.root.tk.call(win.protocol('WM_DELETE_WINDOW'))
        self.g.root.update()
        self.assertIsNone(self.g.root.grab_current())

    def test_safety_buttons_real_preview_cancel_uninstall_rollback(self):
        source = self.base / 'User' / 'demo.c'; source.parent.mkdir(); source.write_text('int demo;\n')
        opts = SimpleNamespace(scan_dirs=str(source.parent), scan_files=None, include_h=False,
                               yes=True, dry_run=False, diff_file=None)
        self.assertTrue(m.run_tasks(m.KeilProject(self.project), ['add_files'], opts))
        installed = self.project.read_bytes()
        def preview_click(label):
            # A worker now prepares the preview asynchronously. Wait for the
            # actual button, not a timing-dependent fixed 40 ms deadline.
            attempts = [0]
            def when_ready():
                attempts[0] += 1
                matches = [w for w in descendants(self.g.root)
                           if isinstance(w, m.ttk.Button) and w.cget('text') == label]
                if matches:
                    self.click(label)
                elif attempts[0] < 200:
                    self.g.root.after(25, when_ready)
                else:
                    self.fail('Preview did not appear: ' + label)
            self.g.root.after(25, when_ready)
        self.click('安全与恢复')
        preview_click('取消'); self.click('卸载所选组件')
        self.assertEqual(self.project.read_bytes(), installed)
        self.click('安全与恢复')
        preview_click('确认并继续'); self.click('卸载所选组件')
        self.assertNotIn('demo.c', self.project.read_text(encoding='utf-8'))
        self.assertTrue(source.exists())
        removed = self.project.read_bytes()
        self.click('安全与恢复')
        preview_click('取消'); self.click('回滚最近事务')
        self.assertEqual(self.project.read_bytes(), removed)
        self.click('安全与恢复')
        preview_click('确认并继续'); self.click('回滚最近事务')
        self.assertEqual(self.project.read_bytes(), installed)
        self.assertTrue(source.exists())

    def test_logs_and_build_error_are_visible(self):
        self.click('查看日志')
        m.log('GUI-ACTION-TEST 中文 / English')
        self.assertIn('GUI-ACTION-TEST', self.g.log_box.get('1.0', 'end'))
        self.g.clear_log(); self.assertEqual(self.g.log_box.get('1.0', 'end').strip(), '')
        self.click('隐藏日志'); self.click('查看日志')
        self.click('工程工具')
        with patch.object(m, 'build_keil_targets', side_effect=m.ToolError('deliberate failure')):
            self.click('立即调用 Keil 编译')
        self.assertIn('deliberate failure', str(self.errors.call_args))

    def test_cubemx_buttons_real_isolation_and_recovery(self):
        from test_cubemx_recovery import legacy_fixture
        from test_cubemx_coexistence import MAIN
        p, task, sdk = legacy_fixture(self.base / 'cube')
        self.g.project_var.set(str(p.path)); self.g._project_changed()
        source = self.base / 'cube/Core/Src/main.c'
        installed = source.read_text()
        self.click('工程工具')
        def answer_preview(label):
            attempts = [0]
            def poll():
                attempts[0] += 1
                matches = [w for w in descendants(self.g.root)
                           if isinstance(w, m.ttk.Button) and w.cget('text') == label]
                if matches:
                    self.click(label)
                elif attempts[0] < 200:
                    self.g.root.after(25, poll)
                else:
                    self.fail('No CubeMX preview: ' + label)
            self.g.root.after(25, poll)
        answer_preview('取消')
        self.click('CubeMX 生成前隔离旧组件…')
        self.assertTrue(task.exists())
        answer_preview('确认并继续')
        self.click('CubeMX 生成前隔离旧组件…')
        self.assertFalse(self.errors.called, str(self.errors.call_args))
        self.assertTrue((self.base / 'cube/KPS/FreeRTOS/App/freertos_app.c').is_file())
        source.write_text(MAIN)
        answer_preview('取消')
        self.click('CubeMX 重新生成后恢复接入…')
        self.assertEqual(source.read_text(), MAIN)
        answer_preview('确认并继续')
        self.click('CubeMX 重新生成后恢复接入…')
        self.assertFalse(self.errors.called, str(self.errors.call_args))
        self.assertEqual(source.read_text(), installed)

    @unittest.skipUnless(os.environ.get('KPS_REAL_BUILD_PROJECT'), 'opt-in real Keil build')
    def test_real_keil_build_via_button(self):
        project = Path(os.environ['KPS_REAL_BUILD_PROJECT']).resolve()
        self.g.project_var.set(str(project)); self.g._project_changed()
        self.g.user_settings['uv4_path'] = os.environ.get('KPS_UV4', 'D:/Keil_v5/UV4/UV4.exe')
        self.g.rebuild_var.set(True)
        self.click('工程工具'); self.click('立即调用 Keil 编译')
        self.assertFalse(self.errors.called, str(self.errors.call_args))
        log = (project.parent / 'keil_rebuild.log').read_text(encoding='utf-8', errors='replace')
        self.assertIn('0 Error(s)', log)
        self.assertIn('Program Size:', log)
        self.assertIn('0 Error(s)', self.g.log_box.get('1.0', 'end'))

if __name__ == '__main__': unittest.main(verbosity=2)
