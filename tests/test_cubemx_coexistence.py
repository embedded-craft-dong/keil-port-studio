"""Generator ownership and regeneration drift: no writes, no silent replay."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import keil_port_tool as m
from kps_core.cubemx import inspect_coexistence

MAIN = '/* USER CODE END Includes */\nint main(void) {\nHAL_Init();\n/* USER CODE END 2 */\nwhile (1) {}\n}\n'


def fixture(root):
    (root / 'Core/Src').mkdir(parents=True)
    (root / 'MDK-ARM').mkdir()
    (root / 'App.ioc').write_text('Mcu.IP0=RCC\nMcu.IP1=SYS\nMcu.IPNb=2\n')
    source = root / 'Core/Src/main.c'
    source.write_text(MAIN)
    project = root / 'MDK-ARM/App.uvprojx'
    project.write_text('<Project><Targets><Target><TargetName>Debug</TargetName><TargetOption>'
        '<TargetCommonOption><Device>STM32F407ZG</Device></TargetCommonOption>'
        '<TargetArmAds><Cads><VariousControls><Define>USE_HAL_DRIVER</Define>'
        '<IncludePath>../Core/Src</IncludePath></VariousControls></Cads></TargetArmAds></TargetOption>'
        '<Groups><Group><GroupName>Core</GroupName><Files><File><FileName>main.c</FileName>'
        '<FileType>1</FileType><FilePath>../Core/Src/main.c</FilePath></File></Files></Group></Groups>'
        '</Target></Targets></Project>')
    return m.KeilProject(project), source


def install_record(p, source):
    changed = m.patch_main_start_scheduler(MAIN).require_safe(source).text
    source.write_text(changed)
    state = p.dir.parent / '.keil-port-tool'
    state.mkdir()
    record = {'targets':['Debug'], 'source_edits':[{'path':'Core/Src/main.c', 'hunks':m._edit_hunks(MAIN, changed)}],
              'project_files':[{'path':'../Core/Src/main.c', 'target':'Debug'}],
              'include_paths':['../Core/Src'], 'defines':['USE_HAL_DRIVER'], 'generated_files':[]}
    (state / 'manifest.json').write_text(json.dumps({'version':3, 'components':{'freertos':record}}))
    return changed


class CoexistenceTests(unittest.TestCase):
    def test_ioc_claims_reject_before_download_or_write(self):
        for ip, task in [('FREERTOS','freertos'), ('FREERTOS','rtthread'), ('FATFS','fatfs'),
                         ('LWIP','lwip'), ('USB_DEVICE','tinyusb'), ('USB_HOST','tinyusb')]:
            with self.subTest(ip=ip, task=task), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                p, source = fixture(root)
                (root / 'App.ioc').write_text('Mcu.IP0=' + ip + '\n')
                before = {str(f):f.read_bytes() for f in root.rglob('*') if f.is_file()}
                with patch.dict(m.TASK_FUNCS, {task:lambda *a: self.fail('Planner must not run')}), contextlib.redirect_stdout(io.StringIO()):
                    self.assertFalse(m.run_tasks(p, [task], SimpleNamespace()))
                self.assertTrue(p._planning_failed)
                self.assertEqual(before, {str(f):f.read_bytes() for f in root.rglob('*') if f.is_file()})

    def test_ioc_disabled_but_old_generated_code_still_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, _ = fixture(root)
            foreign = root / 'Core/Src/freertos.c'
            foreign.write_text('void MX_FREERTOS_Init(void) { }')
            p.add_file('Core', foreign.name, 1, '../Core/Src/freertos.c')
            issues = inspect_coexistence(p, m.read_source_text, ['freertos'])
            self.assertIn('FOREIGN_MIDDLEWARE_OWNER', [i['code'] for i in issues])
            node = p.root.find('.//File[FileName="freertos.c"]')
            import xml.etree.ElementTree as ET
            ET.SubElement(ET.SubElement(ET.SubElement(node, 'FileOption'), 'CommonProperty'), 'IncludeInBuild').text = '0'
            self.assertFalse(inspect_coexistence(p, m.read_source_text, ['freertos']))

    def test_regeneration_lost_hooks_reported_in_doctor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, source = fixture(root)
            install_record(p, source)
            self.assertFalse(inspect_coexistence(p, m.read_source_text))
            source.write_text(MAIN)  # simulated generator overwrite, not an actual CubeMX run
            before = source.read_bytes()
            report = m.inspect_project(p)
            self.assertIn('OWNED_STARTUP_DRIFT', [i['code'] for i in report['issues']])
            self.assertEqual(source.read_bytes(), before)

    def test_user_code_outside_owned_hook_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            p, source = fixture(Path(td))
            changed = install_record(p, source)
            source.write_text(changed + '\nvoid MyTask(void) { }\n')
            self.assertFalse(inspect_coexistence(p, m.read_source_text))

    def test_project_references_include_define_loss(self):
        with tempfile.TemporaryDirectory() as td:
            p, source = fixture(Path(td))
            install_record(p, source)
            node = p.root.find('.//Files')
            node.clear()
            p.root.find('.//IncludePath').text = ''
            p.root.find('.//Define').text = ''
            codes = {i['code'] for i in inspect_coexistence(p, m.read_source_text)}
            self.assertTrue({'INSTALLED_REFERENCE_LOST','INSTALLED_INCLUDE_LOST','INSTALLED_DEFINE_LOST'} <= codes)

    def test_manifest_escape_and_corruption_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, source = fixture(root)
            install_record(p, source)
            manifest = root / '.keil-port-tool/manifest.json'
            data = json.loads(manifest.read_text())
            data['components']['freertos']['source_edits'][0]['path'] = '../outside.c'
            manifest.write_text(json.dumps(data))
            self.assertIn('OWNERSHIP_RECORD_INVALID', [i['code'] for i in inspect_coexistence(p, m.read_source_text)])
            manifest.write_text('not json')
            self.assertEqual(inspect_coexistence(p, m.read_source_text)[0]['code'], 'OWNERSHIP_RECORD_INVALID')

    def test_multiple_ioc_cannot_silently_choose_owner(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p, _ = fixture(root)
            (root / 'Other.ioc').write_text('Mcu.IP0=FREERTOS\n')
            items = inspect_coexistence(p, m.read_source_text, ['freertos'])
            self.assertEqual(items[0]['code'], 'CUBEMX_IOC_AMBIGUOUS')
            self.assertEqual(items[0]['severity'], 'error')


if __name__ == '__main__':
    unittest.main()
