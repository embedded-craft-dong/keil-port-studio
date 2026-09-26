"""Read-only health checks: real parsing, target isolation and CLI behavior."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kps_core import doctor

spec = importlib.util.spec_from_file_location('doctor_tool', ROOT / 'keil_port_tool.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

UART = '''void HAL_UART_MspInit(void) {
 GPIO_InitTypeDef pins = {0};
 pins.Pin = GPIO_PIN_5;
 pins.Mode = GPIO_MODE_AF_PP;
 pins.Alternate = GPIO_AF7_USART2;
 HAL_GPIO_Init(GPIOD, &pins);
}'''
LCD = '''void LCD_Init(void) {
 GPIO_InitTypeDef pin = {0};
 pin.Mode = GPIO_MODE_AF_PP;
 pin.Alternate = GPIO_AF12_FSMC;
 pin.Pin = GPIO_PIN_4 | GPIO_PIN_5;
 HAL_GPIO_Init(GPIOD, &pin);
}'''


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class PinTests(unittest.TestCase):
    def test_hal_multiline_pin_lists_and_evidence(self):
        claims = doctor.source_pin_claims(LCD, 'display.c')
        self.assertEqual([c['pin'] for c in claims], ['PD4', 'PD5'])
        self.assertEqual(claims[0]['line'], 6)
        self.assertEqual(claims[0]['peripheral'], 'FSMC')

    def test_spl_and_same_peripheral(self):
        text = 'GPIO_PinAFConfig(GPIOD, GPIO_PinSource5, GPIO_AF_FSMC);'
        self.assertEqual(doctor.source_pin_claims(text, 'lcd.c')[0]['pin'], 'PD5')
        self.assertEqual(doctor._peripheral('USART2_TX'), 'USART2')

    def test_comments_strings_and_literal_disabled_branches(self):
        text = '/* ' + UART + ' */\nconst char *s="HAL_GPIO_Init(GPIOD, &pins)";\n#if 0\n' + UART + '\n#endif\n'
        self.assertEqual(doctor.source_pin_claims(text, 'a.c'), [])
        text += '#if 0\n' + LCD + '\n#else\n' + UART + '\n#endif\n'
        claims = doctor.source_pin_claims(text, 'a.c')
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]['peripheral'], 'USART2')

    def test_nested_conditionals_and_no_cross_branch_field_mix(self):
        text = '#if 0\n#if 1\n' + UART + '\n#endif\n#endif\n'
        self.assertEqual(doctor.source_pin_claims(text, 'a.c'), [])
        text = 'GPIO_InitTypeDef p; p.Pin=GPIO_PIN_5; p.Mode=GPIO_MODE_AF_PP;\n#if A\np.Alternate=GPIO_AF7_USART2;\n#else\nHAL_GPIO_Init(GPIOD, &p);\n#endif'
        self.assertEqual(doctor.source_pin_claims(text, 'a.c'), [])

    def test_aliases_non_af_mode_and_invalid_pin_not_guessed(self):
        for old, new in [('GPIO_PIN_5', 'GPIO_PIN_5 | CUSTOM_MASK'),
                         ('GPIO_PIN_5', 'GPIO_PIN_16'),
                         ('GPIO_MODE_AF_PP', 'GPIO_MODE_OUTPUT_PP')]:
            self.assertEqual(doctor.source_pin_claims(UART.replace(old, new), 'a.c'), [])

    def test_ioc_claims(self):
        claims = doctor.ioc_pin_claims('PD5.Signal=USART2_TX\nPA2.Signal=ETH_MDIO\nPA3.GPIO_Label=uart\n', 'board.ioc')
        self.assertEqual([(c['pin'], c['peripheral']) for c in claims], [('PD5', 'USART2'), ('PA2', 'ETH')])


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='kps-doctor-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'sample.uvprojx'
        (self.root / 'uart.c').write_text(UART, encoding='utf-8')
        (self.root / 'lcd.c').write_text(LCD, encoding='utf-8')
        self.make_project([('Debug', ['uart.c', 'lcd.c'])])

    def make_project(self, targets, includes='', namespace=False, disabled_file='', disabled_group=False):
        root = ET.Element('Project')
        container = ET.SubElement(root, 'Targets')
        for name, files in targets:
            target = ET.SubElement(container, 'Target')
            ET.SubElement(target, 'TargetName').text = name
            options = ET.SubElement(target, 'TargetOption')
            ads = ET.SubElement(options, 'TargetArmAds')
            cads = ET.SubElement(ads, 'Cads')
            controls = ET.SubElement(cads, 'VariousControls')
            ET.SubElement(controls, 'IncludePath').text = includes
            group = ET.SubElement(ET.SubElement(target, 'Groups'), 'Group')
            ET.SubElement(group, 'GroupName').text = 'Application'
            if disabled_group:
                props = ET.SubElement(ET.SubElement(group, 'GroupOption'), 'CommonProperty')
                ET.SubElement(props, 'IncludeInBuild').text = '0'
            file_list = ET.SubElement(group, 'Files')
            for name in files:
                file = ET.SubElement(file_list, 'File')
                ET.SubElement(file, 'FileName').text = Path(name).name
                ET.SubElement(file, 'FilePath').text = name
                if name == disabled_file:
                    props = ET.SubElement(ET.SubElement(file, 'FileOption'), 'CommonProperty')
                    ET.SubElement(props, 'IncludeInBuild').text = '0'
        if namespace:
            root.set('xmlns', 'urn:fixture')
        ET.ElementTree(root).write(self.project, encoding='utf-8', xml_declaration=True)

    def inspect(self, target=None, ioc=None):
        project = m.KeilProject(self.project)
        project.select_targets([target] if target else None)
        before = hashes(self.root)
        result = doctor.inspect_project(project, ioc)
        self.assertEqual(hashes(self.root), before)
        self.assertFalse(project.dirty)
        return result

    def test_pd5_conflict_and_pa2_fix(self):
        report = self.inspect()
        conflicts = [i for i in report['issues'] if i['code'] == 'PIN_AF_CONFLICT']
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]['severity'], 'warning')
        self.assertEqual({e['pin'] for e in conflicts[0]['evidence']}, {'PD5'})
        (self.root / 'uart.c').write_text(UART.replace('GPIO_PIN_5', 'GPIO_PIN_2').replace('GPIOD', 'GPIOA'), encoding='utf-8')
        self.assertEqual(self.inspect()['issues'], [])

    def test_targets_never_cross_contaminate(self):
        self.make_project([('Debug', ['uart.c']), ('Release', ['lcd.c'])], namespace=True)
        self.assertEqual(self.inspect()['issues'], [])
        self.assertEqual(len(self.inspect('Debug')['targets']), 1)

    def test_excluded_group_or_file_not_scanned(self):
        self.make_project([('Debug', ['uart.c', 'lcd.c', 'missing.c'])], disabled_group=True)
        self.assertEqual(self.inspect()['issues'], [])
        self.make_project([('Debug', ['uart.c', 'lcd.c'])], disabled_file='lcd.c')
        self.assertEqual(self.inspect()['issues'], [])

    def test_missing_duplicate_and_macro_paths(self):
        self.make_project([('Debug', ['uart.c', './uart.c', 'missing.c', '$Pack$/source.c'])], includes='.;missing_inc;$Pack$/Inc')
        codes = {i['code'] for i in self.inspect()['issues']}
        self.assertEqual(codes, {'DUPLICATE_FILE', 'MISSING_FILE', 'PATH_UNRESOLVED', 'MISSING_INCLUDE', 'INCLUDE_UNRESOLVED'})

    def test_ioc_mismatch_and_explicit_selection(self):
        self.make_project([('Debug', ['lcd.c'])])
        ioc = self.root / 'board.ioc'
        ioc.write_text('PD5.Signal=USART2_TX\n', encoding='utf-8')
        self.assertIn('PIN_AF_CONFLICT', {i['code'] for i in self.inspect()['issues']})
        (self.root / 'other.ioc').write_text('PD5.Signal=FSMC_NWE\n', encoding='utf-8')
        report = self.inspect()
        self.assertEqual([i['code'] for i in report['issues']], ['IOC_AMBIGUOUS'])
        self.assertIn('PIN_AF_CONFLICT', {i['code'] for i in self.inspect(ioc=ioc)['issues']})

    def test_gbk_and_scan_limits(self):
        (self.root / 'uart.c').write_bytes(('// 中文注释\n' + UART).encode('gbk'))
        self.assertIn('PIN_AF_CONFLICT', {i['code'] for i in self.inspect()['issues']})
        with patch.object(doctor, 'MAX_SOURCE_FILES', 1):
            result = self.inspect()
        self.assertIn('SOURCE_SKIPPED', {i['code'] for i in result['issues']})
        self.assertEqual(result['targets'][0]['source_files_skipped'], 1)

    def test_report_languages_and_existing_export_protection(self):
        report = self.inspect()
        self.assertIn('候选冲突', doctor.format_report(report))
        self.assertIn('different peripheral assignments', doctor.format_report(report, 'en'))
        destination = self.root / 'report.json'
        doctor.export_report(report, destination)
        self.assertEqual(json.loads(destination.read_text(encoding='utf-8'))['summary'], report['summary'])
        old = destination.read_bytes()
        with self.assertRaises(FileExistsError):
            doctor.export_report(report, destination)
        self.assertEqual(destination.read_bytes(), old)
        with self.assertRaises(ValueError):
            doctor.export_report(report, self.project)

    def test_cli_readonly_json_target_and_mixed_action_rejection(self):
        report_path = self.root / 'cli.json'
        before = hashes(self.root)
        args = [sys.executable, str(ROOT / 'keil_port_tool.py'), str(self.project), '--doctor', '--target', 'Debug', '--doctor-language', 'en']
        result = subprocess.run(args + ['--doctor-json', str(report_path)], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'PIN_AF_CONFLICT', result.stdout)
        report = json.loads(report_path.read_text(encoding='utf-8'))
        self.assertTrue(report['read_only'])
        after = hashes(self.root)
        self.assertEqual({k: v for k, v in after.items() if k != 'cli.json'}, before)
        bad = subprocess.run(args + ['--build'], capture_output=True, timeout=20)
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(hashes(self.root), after)
        bad = subprocess.run(args + ['--dry-run', '--doctor-json', str(self.root / 'unexpected.json')], capture_output=True, timeout=20)
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(hashes(self.root), after)


if __name__ == '__main__':
    unittest.main(verbosity=2)
