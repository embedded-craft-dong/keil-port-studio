"""Language changes must preserve selections and preset semantics."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('language_tool', Path(__file__).parents[1] / 'keil_port_tool.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class LanguageTests(unittest.TestCase):
    def test_english_layout_all_pages_and_guides(self):
        app = m.KeilPortGUI(language='en', scaling=96/72)
        try:
            for dimensions in ('980x650', '1180x800'):
                app.root.geometry(dimensions)
                for index in range(8):
                    app.notebook.select(index); app.root.update()
                    self.assertLessEqual(app.execute_button.winfo_rootx() + app.execute_button.winfo_width(),
                                         app.root.winfo_rootx() + app.root.winfo_width())
                    self.assertGreater(app.notebook.winfo_height(), 300)
            app.open_guides(); app.root.update()
            win = next(w for w in app.root.winfo_children() if isinstance(w, m.tk.Toplevel) and w.title() == 'Offline guides')
            nb = next(w for w in win.winfo_children() if isinstance(w, m.ttk.Notebook))
            self.assertEqual(len(nb.tabs()), 6)
            for child in nb.winfo_children():
                # ScrolledText inserts a containing Frame into the notebook.
                view = next(w for w in child.winfo_children() if isinstance(w, m.tk.Text))
                self.assertNotIn('Guide not found', view.get('1.0', 'end'))
                self.assertGreater(len(view.get('1.0', 'end')), 1500)
        finally:
            app._close()

    def test_live_switch_selection_and_cross_language_preset(self):
        with tempfile.TemporaryDirectory(prefix='kps-language-') as temp:
            base = Path(temp)
            with patch.object(m, 'user_settings_path', return_value=base / 'settings.json'), \
                 patch.object(m.messagebox, 'showerror') as error:
                app = m.KeilPortGUI(language='zh-CN')
                try:
                    app.add_tree.set_files(base, [(base / 'a.c', '目录'), (base / 'b.c', '')])
                    app.add_tree.apply_selection_keys(['b.c'])
                    app.littlefs_mode.set('裸机')
                    app.define_var.set('用户_MACRO=1')
                    app.append_log('original 中文 log')
                    before = app._preset_data()
                    for language in ('en', 'zh-CN', 'en'):
                        app.change_language(language); app.root.update()
                        self.assertEqual(app.add_tree.selection_keys(), ['b.c'])
                        self.assertEqual(before, app._preset_data())
                        self.assertIn('original 中文 log', app.log_box.get('1.0', 'end'))
                    self.assertEqual(app.target_var.get(), 'All Targets')
                    preset = base / 'preset.json'
                    preset.write_text(json.dumps(before), encoding='utf-8')
                    app.littlefs_mode.set('other')
                    with patch.object(m.filedialog, 'askopenfilename', return_value=str(preset)):
                        app.load_preset()
                    self.assertEqual(app._preset_data(), before)
                    self.assertFalse(error.called, error.call_args_list)
                    self.assertEqual(m.load_user_settings()['language'], 'en')
                    self.assertEqual(len(app._nav_traces), 12)
                finally:
                    app._close()

    def test_setting_failure_keeps_language(self):
        with patch.object(m, 'save_user_settings', side_effect=OSError('readonly')), \
             patch.object(m.messagebox, 'showerror') as error:
            app = m.KeilPortGUI(language='zh-CN')
            try:
                app.change_language('en')
                self.assertEqual(m._UI_LANGUAGE, 'zh-CN')
                self.assertTrue(error.called)
            finally:
                app._close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
