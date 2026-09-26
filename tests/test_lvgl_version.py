"""LVGL v8/v9 header layouts and per-Target compiler warning regressions."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

spec=importlib.util.spec_from_file_location('lvgl_version_tool',Path(__file__).parents[1]/'keil_port_tool.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class LVGLVersionTests(unittest.TestCase):
    def test_download_profile_and_version_header_retention(self):
        for compilers,tag in (((1,0),m.LVGL_V8_TAG),((0,),m.LVGL_V8_TAG),((1,),m.LVGL_V9_TAG)):
            with self.subTest(compilers=compilers),tempfile.TemporaryDirectory() as d:
                sdk=Path(d);(sdk/'src').mkdir();(sdk/'lvgl.h').write_text('/* fixture */')
                proj=SimpleNamespace(targets=compilers,is_ac6=bool)
                opts=SimpleNamespace(sdk_dir=str(sdk),no_download=False,dry_run=False,interactive=False)
                with mock.patch.object(m,'find_existing_sdk',return_value=None), \
                     mock.patch.object(m,'download_archive',return_value={}) as download, \
                     mock.patch.object(m,'extract_zip',return_value=sdk) as extract, \
                     mock.patch.dict(m.CONFIG,{'LVGL_URL':''}), \
                     mock.patch.dict(m.os.environ,{'KEIL_TOOL_LVGL_URL':''}):
                    self.assertEqual(m.ensure_lvgl_sdk(proj,opts,m.Report('lvgl')),sdk)
                    self.assertTrue(download.call_args.args[0].endswith('/'+tag))
                    self.assertIn('lv_version.h',extract.call_args.kwargs['keep_files'])
    def test_v8_public_header_and_comment_decoys(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'lvgl.h').write_text('/*\n#define LVGL_VERSION_MAJOR 99\n*/\n'
                '// #define LVGL_VERSION_MAJOR 42\n#define LVGL_VERSION_MAJOR 8\n')
            self.assertEqual(m.lvgl_version(root),8)
    def test_version_header_layouts(self):
        for name in ('lv_version.h','src/lv_version.h'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
                root=Path(d);header=root/name;header.parent.mkdir(exist_ok=True)
                header.write_text('# define LVGL_VERSION_MAJOR (9)\n')
                self.assertEqual(m.lvgl_version(root),9)
    def test_conflicting_versions_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'lv_version.h').write_text('#define LVGL_VERSION_MAJOR 9\n')
            (root/'lvgl.h').write_text('#define LVGL_VERSION_MAJOR 8\n')
            with self.assertRaises(m.ToolError):m.lvgl_version(root)
    def test_unknown_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'lvgl.h').write_text('/* v9 is mentioned but not defined */')
            self.assertEqual(m.lvgl_version(root),0)
    def test_ac6_target_does_not_hide_ac5_warning(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);sdk=root/'sdk';(sdk/'src').mkdir(parents=True)
            (sdk/'lvgl.h').write_text('#define LVGL_VERSION_MAJOR 9\n')
            (sdk/'lv_conf_template.h').write_text('#if 0\n#define LV_COLOR_DEPTH 16\n#endif\n')
            (sdk/'src/lv_test.c').write_text('/* fixture */')
            path=root/'app.uvprojx'
            targets=''.join('<Target><TargetName>T%d</TargetName><uAC6>%d</uAC6>'
                '<TargetOption><TargetArmAds><Cads><VariousControls><Define/>'
                '<IncludePath/></VariousControls></Cads></TargetArmAds></TargetOption>'
                '<Groups/></Target>'%(i,ac6) for i,ac6 in enumerate((1,0)))
            path.write_text('<Project><Targets>'+targets+'</Targets></Project>')
            p=m.KeilProject(path);rep=m.Report('lvgl')
            m.do_lvgl(p,SimpleNamespace(lvgl=str(sdk),ports=False,color_depth=16),rep)
            self.assertTrue(any('LVGL v9' in w and 'AC5' in w for w in rep.warnings))
            self.assertFalse((root/'Middlewares').exists())

if __name__=='__main__':unittest.main(verbosity=2)
