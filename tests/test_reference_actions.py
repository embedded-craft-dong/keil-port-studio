"""Reference-only removal: Target isolation, ownership, preview, rollback, CLI."""
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import keil_port_tool as m
from test_spl_adapter import project


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='kps-reference-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        p=project(self.root)
        extra=copy.deepcopy(p.targets[0]); extra.find('TargetName').text='Release'
        p.root.find('Targets').append(extra)
        # Fixture construction, not the production API (which intentionally
        # refuses an unexpected change in Target identity while saving).
        p.path.write_bytes(m.ET.tostring(p.root,encoding='utf-8'))
        self.path=p.path
        self.before=self.path.read_bytes()
        self.sources={p:p.read_bytes() for p in (self.root/'User').iterdir()}
        self.silent=contextlib.redirect_stdout(io.StringIO()); self.silent.__enter__()
        self.addCleanup(lambda:self.silent.__exit__(None,None,None))

    def rows(self,p):
        return [r for r in m.reference_inventory(p) if r['kind']=='include' or r['name']=='entry.c']

    def test_target_isolation_and_exact_rollback(self):
        p=m.KeilProject(self.path); p.select_targets(['T'])
        self.assertTrue(m.remove_project_references(p,self.rows(p),yes=True))
        p=m.KeilProject(self.path); rows=m.reference_inventory(p)
        self.assertEqual(len([r for r in rows if r['target']=='T']),1)
        self.assertEqual(len([r for r in rows if r['target']=='Release']),3)
        for path,data in self.sources.items(): self.assertEqual(path.read_bytes(),data)
        self.assertTrue(m.rollback_last_transaction(p,yes=True))
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_cancel_dry_run_stale_and_unknown_selection(self):
        p=m.KeilProject(self.path)
        for opts in ({'dry_run':True},{'preview_callback':lambda _:False}):
            self.assertFalse(m.remove_project_references(p,self.rows(p),**opts))
            self.assertEqual(self.path.read_bytes(),self.before)
            self.assertFalse(m._state_dir(p).exists()); self.assertFalse(p.dirty)
        with self.assertRaises(m.ToolError):
            m.remove_project_references(p,[dict(kind='file',target='T',path='missing.c')],yes=True)
        def concurrent(_):
            self.path.write_bytes(self.before+b'\n'); return True
        with self.assertRaisesRegex(m.ToolError,'Project changed'):
            m.remove_project_references(p,self.rows(p),preview_callback=concurrent)
        self.assertEqual(self.path.read_bytes(),self.before+b'\n')

    def test_ownership_blocks_managed_but_updates_added_files(self):
        p=m.KeilProject(self.path)
        state=m._state_dir(p); state.mkdir()
        manifest={'version':3,'components':{'lvgl':{'project_files':p.file_records(),
            'include_paths':['User'],'targets':['T','Release']}}}
        path=state/'manifest.json'; path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(m.ToolError,'Managed component'):
            m.remove_project_references(p,self.rows(p),yes=True)
        self.assertEqual(self.path.read_bytes(),self.before)
        manifest['components']['add_files']=manifest['components'].pop('lvgl')
        path.write_text(json.dumps(manifest))
        p.select_targets(['T']); m.remove_project_references(p,self.rows(p),yes=True)
        saved=json.loads(path.read_text())['components']['add_files']
        self.assertEqual(len(saved['project_files']),3)
        self.assertEqual(saved['include_paths'],['User']) # Release still needs it
        self.assertEqual(saved['include_paths_by_target'],{'T':[],'Release':['User']})
        issues=m.inspect_coexistence(m.KeilProject(self.path),m.read_source_text)
        self.assertFalse([i for i in issues if i['code']=='INSTALLED_INCLUDE_LOST'],issues)

    def test_cli_exact_paths_dry_run_bad_target_and_no_disk_delete(self):
        script=Path(m.__file__).resolve()
        cmd=[sys.executable,str(script),str(self.path),'--target','T',
             '--remove-file',r'.\User\entry.c','--remove-include','./User']
        run=subprocess.run(cmd+['--dry-run'],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(self.path.read_bytes(),self.before)
        run=subprocess.run(cmd+['--yes'],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(run.returncode,0,run.stderr)
        for path,data in self.sources.items(): self.assertEqual(path.read_bytes(),data)
        run=subprocess.run(cmd+['--yes'],capture_output=True,text=True,encoding='utf-8')
        self.assertNotEqual(run.returncode,0)

    def test_variable_include_exact_and_file_options_untouched(self):
        p=m.KeilProject(self.path)
        c=p.targets[0].find('.//IncludePath'); c.text='$(PackRoot)/inc;User;User/sub'
        file=p.targets[0].find('.//File')
        options=m.ET.SubElement(file,'FileOption'); ads=m.ET.SubElement(options,'FileArmAds')
        cads=m.ET.SubElement(ads,'Cads'); vc=m.ET.SubElement(cads,'VariousControls')
        m.ET.SubElement(vc,'IncludePath').text='$(PackRoot)/inc'
        p.dirty=True; p.save(); p=m.KeilProject(self.path); p.select_targets(['T'])
        selection=[r for r in m.reference_inventory(p) if r['path']=='$(PackRoot)/inc']
        m.remove_project_references(p,selection,yes=True)
        p=m.KeilProject(self.path)
        self.assertEqual(p.targets[0].findtext('.//TargetArmAds/Cads/VariousControls/IncludePath'),'User;User/sub')
        self.assertEqual(p.targets[0].findtext('.//FileOption/FileArmAds/Cads/VariousControls/IncludePath'),'$(PackRoot)/inc')


if __name__=='__main__': unittest.main()
