"""Real Git + real Tk callbacks, isolated local repos. No external pushes/install."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('git_tool', Path(__file__).parents[1] / 'keil_port_tool.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@unittest.skipUnless(m.find_git(), 'Git required for integration tests')
class GitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kps-git-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.work = self.base / '中文 project'
        self.work.mkdir()
        self.repo = m.GitRepository(self.work)
        self.repo.initialize()
        self.repo.set_identity('KPS Test', 'test@example.invalid')
        # Do not inherit user signing/hooks/default branch settings in fixtures.
        self.repo.run('config', 'commit.gpgsign', 'false')
        self.repo.run('config', 'core.hooksPath', str(self.base / 'no-hooks'))

    def write(self, name, text='test\n'):
        (self.work / name).write_text(text, encoding='utf-8')

    def commit(self, name='a.c', text='one\n'):
        self.write(name, text)
        self.repo.stage([name]); self.repo.commit('test: ' + name)

    def test_stage_commit_rename_delete_unstage_and_literal_paths(self):
        for name in ('中文 文件.c', '-dash.c', '[special].c'):
            self.write(name)
        self.repo.stage(['中文 文件.c'])
        self.repo.unstage(['中文 文件.c'])
        self.assertTrue((self.work / '中文 文件.c').exists())
        self.repo.stage(['中文 文件.c', '-dash.c', '[special].c'])
        self.repo.commit('初次提交')
        self.assertEqual(self.repo.status(), [])
        self.assertIn('初次提交', self.repo.history())
        self.repo.run('mv', '中文 文件.c', 'renamed.c')
        entries = self.repo.status()
        self.assertEqual(entries[0]['original'], '中文 文件.c')
        self.repo.unstage(['renamed.c', '中文 文件.c'])
        self.assertTrue((self.work / 'renamed.c').exists())
        self.repo.stage(['renamed.c', '中文 文件.c'])
        self.repo.commit('rename')
        (self.work / '-dash.c').unlink()
        self.repo.stage(['-dash.c'])
        self.repo.commit('delete')
        self.assertEqual(self.repo.status(), [])
        with self.assertRaises(m.ToolError): self.repo.stage(['../outside'])
        with self.assertRaises(m.ToolError): self.repo.commit('')
        with self.assertRaises(m.ToolError): self.repo.initialize()

    def test_real_push_pull_divergence_and_dirty_protection(self):
        self.commit()
        remote = self.base / 'remote.git'; remote.mkdir()
        m.GitRepository(remote).run('init', '--bare')
        self.repo.set_remote('origin', str(remote))
        branch = self.repo.branch()
        self.repo.sync('push', 'origin', branch)
        other = self.base / 'other'; other.mkdir()
        peer = m.GitRepository(other); peer.initialize()
        peer.set_identity('Peer', 'peer@example.invalid')
        peer.run('config', 'commit.gpgsign', 'false')
        peer.run('config', 'core.hooksPath', str(self.base / 'no-hooks'))
        peer.set_remote('origin', str(remote)); peer.sync('pull', 'origin', branch)
        self.assertEqual((other / 'a.c').read_bytes(), (self.work / 'a.c').read_bytes())
        self.commit('a.c', 'two\n'); self.repo.sync('push', 'origin', branch)
        peer.sync('fetch', 'origin', branch); peer.sync('pull', 'origin', branch)
        self.assertEqual((other / 'a.c').read_text(), 'two\n')
        (other / 'dirty.c').write_text('keep')
        with self.assertRaisesRegex(m.ToolError, '未提交|modified'):
            peer.sync('pull', 'origin', branch)
        self.assertEqual((other / 'dirty.c').read_text(), 'keep')
        peer.stage(['dirty.c']); peer.commit('peer diverges')
        self.commit('a.c', 'three\n'); self.repo.sync('push', 'origin', branch)
        before = peer.run('rev-parse', 'HEAD').stdout
        with self.assertRaises(m.ToolError): peer.sync('pull', 'origin', branch)
        self.assertEqual(before, peer.run('rev-parse', 'HEAD').stdout)
        with self.assertRaises(m.ToolError): peer.sync('push', 'origin', branch)
        self.assertEqual(self.repo.run('rev-parse', 'HEAD').stdout,
                         m.GitRepository(remote).run('rev-parse', 'refs/heads/' + branch).stdout)

    def test_validation_and_missing_git(self):
        for url in ('https://secret@example.com/repo', 'ext::bad', '--upload-pack=bad', 'http://bad/repo'):
            with self.assertRaises(m.ToolError): self.repo.set_remote('origin', url)
        with patch.object(m, 'find_git', return_value=None):
            with self.assertRaises(m.ToolError): m.GitRepository(self.work)
        self.assertNotIn('secret', self.repo.redact('fatal https://secret@example.com/test'))
        with patch.object(m.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 120)):
            with self.assertRaises(m.ToolError): self.repo.status()

    def test_tk_buttons_real_roundtrip_and_install_cancel(self):
        with patch.object(m, 'user_settings_path', return_value=self.base / 'settings.json'), \
             patch.object(m.messagebox, 'showerror') as error, \
             patch.object(m.messagebox, 'askyesno', return_value=True):
            app = m.KeilPortGUI(language='en')
            self.addCleanup(app._close)
            app.project_var.set(str(self.work))
            app.open_git(); panel = app.git_panel
            callback_errors = []
            app.root.report_callback_exception = lambda *args: callback_errors.append(args)
            def wait():
                deadline = time.monotonic() + 20
                while panel.busy and time.monotonic() < deadline:
                    app.root.update(); time.sleep(.02)
                app.root.update()
                self.assertFalse(panel.busy)
                self.assertEqual(callback_errors, [])
            def click(label):
                controls = [w for w in panel.controls if isinstance(w, m.ttk.Button) and w.cget('text') == label]
                self.assertEqual(len(controls), 1, label)
                controls[0].invoke(); wait()
            wait()
            self.assertEqual(panel.repository.folder, self.work.resolve())
            self.write('ui.c'); click('Refresh / detect')
            panel.tree.selection_set(panel.tree.get_children()[0]); click('Stage selected')
            panel.message.set('real UI commit'); click('Commit staged…')
            self.assertEqual(self.repo.status(), [])
            click('History'); self.assertIn('real UI commit', panel.details.get('1.0', 'end'))
            self.write('ui.c', 'changed'); click('Refresh / detect')
            panel.tree.selection_set(panel.tree.get_children()[0]); click('Stage selected')
            click('View diff'); self.assertIn('+changed', panel.details.get('1.0', 'end'))
            panel.tree.selection_set(panel.tree.get_children()[0]); click('Unstage selected')
            self.assertEqual(self.repo.status()[0]['status'], ' M')
            # Saving a staged rename again must not pass the now-absent old name to git add.
            self.repo.stage(['ui.c']); self.repo.commit('before rename')
            self.repo.run('mv', 'ui.c', 'renamed.c')
            click('Refresh / detect')
            panel.tree.selection_set(panel.tree.get_children()[0]); click('Stage selected')
            self.assertEqual(self.repo.status()[0]['status'], 'R ')
            with patch.object(m, 'find_git', return_value=None), \
                 patch.object(m.messagebox, 'askyesno', return_value=False), \
                 patch.object(m.subprocess, 'Popen') as launch, patch.object(m.webbrowser, 'open') as browser:
                click('Install Git…'); launch.assert_not_called(); browser.assert_not_called()
            # Accepted install routing is mocked; do not install/uninstall the host's Git.
            with patch.object(m, 'find_git', return_value=None), \
                 patch.object(m, 'git_install_command', return_value=['winget', 'install', '--id', 'Git.Git', '--interactive']), \
                 patch.object(m.subprocess, 'Popen') as launch:
                click('Install Git…')
                self.assertIn('--interactive', launch.call_args.args[0])
                self.assertNotIn('--silent', launch.call_args.args[0])
            with patch.object(m, 'find_git', return_value=None), \
                 patch.object(m, 'git_install_command', return_value=None), patch.object(m.webbrowser, 'open') as browser:
                click('Install Git…')
                self.assertTrue(browser.call_args.args[0].startswith('https://git-scm.com/'))
            self.assertFalse(error.called, error.call_args_list)
            panel.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
