import importlib.util
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

ROOT = Path(__file__).parents[1]
TOOL = ROOT / 'keil_port_tool.py'
if not TOOL.exists():
    TOOL = ROOT / 'outputs' / 'keil_port_tool.py'
spec = importlib.util.spec_from_file_location('keil_port_tool_safety', TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def project_xml(namespace=''):
    ns = ' xmlns="%s"' % namespace if namespace else ''
    target = '''<Target><TargetName>{name}</TargetName><TargetOption>
<TargetCommonOption><Device>STM32F407VG</Device><Cpu>CPUTYPE("Cortex-M4") FPU</Cpu></TargetCommonOption>
<TargetArmAds><Cads><VariousControls><Define>BASE</Define><IncludePath>Inc</IncludePath></VariousControls></Cads></TargetArmAds>
</TargetOption><Groups><Group><GroupName>App</GroupName><Files /></Group></Groups></Target>'''
    return ('<?xml version="1.0" encoding="UTF-8"?>\r\n'
            '<Project%s>\r\n<!-- 用户保留注释 -->\r\n<Targets>%s%s</Targets>\r\n</Project>\r\n' %
            (ns, target.format(name='Debug'), target.format(name='Release')))


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    path = root / 'namespaced.uvprojx'
    path.write_bytes(b'\xef\xbb\xbf' + project_xml('urn:keil:test').encode('utf-8'))
    proj = m.KeilProject(path)
    assert proj.namespace == 'urn:keil:test'
    assert proj.target_names(True) == ['Debug', 'Release']
    proj.select_targets(['Debug'])
    report = m.Report('add_files')
    proj.add_define('ONLY_DEBUG', report)
    proj.add_file('DebugOnly', 'debug.c', 1, 'debug.c')
    serialized = proj.serialize()
    assert serialized.startswith(b'\xef\xbb\xbf')
    decoded = serialized[3:].decode('utf-8')
    assert '\r\n' in decoded
    assert '<!-- 用户保留注释 -->' in decoded
    assert 'xmlns="urn:keil:test"' in decoded
    parsed = ET.fromstring(serialized[3:])
    ns = {'k': 'urn:keil:test'}
    targets = parsed.findall('k:Targets/k:Target', ns)
    debug_files = targets[0].findall('k:Groups/k:Group/k:Files/k:File', ns)
    release_files = targets[1].findall('k:Groups/k:Group/k:Files/k:File', ns)
    assert len(debug_files) == 1 and not release_files
    assert 'ONLY_DEBUG' in targets[0].findtext(
        'k:TargetOption/k:TargetArmAds/k:Cads/k:VariousControls/k:Define', namespaces=ns)
    assert 'ONLY_DEBUG' not in targets[1].findtext(
        'k:TargetOption/k:TargetArmAds/k:Cads/k:VariousControls/k:Define', namespaces=ns)

    gbk = root / 'legacy.c'
    gbk.write_bytes('/* 中文注释 */\r\nint old_value;\r\n'.encode('gb18030'))
    text = m.read_source_text(gbk).replace('old_value', 'new_value')
    gbk.write_bytes(m.encode_preserving_format(gbk, text))
    assert '中文注释' in gbk.read_bytes().decode('gb18030')
    assert b'\r\n' in gbk.read_bytes()

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    path = root / 'demo.uvprojx'
    path.write_text(project_xml(), encoding='utf-8')
    user = root / 'User'
    user.mkdir()
    source = user / 'demo.c'
    source.write_text('int demo(void) { return 1; }\n', encoding='utf-8')
    header = user / 'demo.h'
    header.write_text('int demo(void);\n', encoding='utf-8')
    proj = m.KeilProject(path)
    opts = SimpleNamespace(scan_dirs=str(user), scan_files=None, include_h=True,
                           yes=True, dry_run=False, diff_file=None)
    assert m.run_tasks(proj, ['add_files'], opts)
    state = root / '.keil-port-tool'
    manifest = json.loads((state / 'manifest.json').read_text(encoding='utf-8'))
    assert 'add_files' in manifest['components']
    assert any(Path(entry.replace('\\', '/')).name == 'User'
               for entry in manifest['components']['add_files']['include_paths'])
    transactions = list((state / 'transactions').glob('*/transaction.json'))
    assert transactions
    assert json.loads(transactions[0].read_text(encoding='utf-8'))['status'] == 'success'

    assert m.uninstall_component(m.KeilProject(path), 'add_files', yes=True)
    after_uninstall = m.KeilProject(path)
    assert not any(Path(x.replace('\\', '/')).name == 'demo.c'
                   for x in after_uninstall.files_in_project())
    assert not any(Path(x.replace('\\', '/')).name == 'User'
                   for cads in after_uninstall._cads_list()
                   for x in (cads.findtext('VariousControls/IncludePath', '') or '').split(';'))
    assert source.is_file()  # 用户自己的源文件只移除工程引用，不删磁盘内容

    assert m.rollback_last_transaction(m.KeilProject(path), yes=True)
    after_rollback = m.KeilProject(path)
    assert any(Path(x.replace('\\', '/')).name == 'demo.c'
               for x in after_rollback.files_in_project())
    restored = json.loads((state / 'manifest.json').read_text(encoding='utf-8'))
    assert 'add_files' in restored['components']

# 扫描 workspace 上层目录时，不得把兄弟 Keil 工程或当前 Target 产物加入工程。
with tempfile.TemporaryDirectory() as td:
    workspace = Path(td)
    current = workspace / 'Current'
    sibling = workspace / 'Sibling'
    for project_root in (current, sibling):
        (project_root / 'Core' / 'Src').mkdir(parents=True)
        (project_root / 'MDK-ARM').mkdir(parents=True)
    current_xml = project_xml().replace(
        '<Device>STM32F407VG</Device>',
        '<Device>STM32F407VG</Device><OutputDirectory>Objects\\</OutputDirectory>')
    current_project = current / 'MDK-ARM' / 'Current.uvprojx'
    sibling_project = sibling / 'MDK-ARM' / 'Sibling.uvprojx'
    current_project.write_text(current_xml, encoding='utf-8')
    sibling_project.write_text(project_xml(), encoding='utf-8')
    own_source = current / 'Core' / 'Src' / 'own.c'
    foreign_source = sibling / 'Core' / 'Src' / 'foreign.c'
    output_object = current / 'MDK-ARM' / 'Objects' / 'own.o'
    own_source.write_text('int own(void) { return 1; }\n', encoding='utf-8')
    foreign_source.write_text('int foreign(void) { return 2; }\n', encoding='utf-8')
    output_object.parent.mkdir()
    output_object.write_bytes(b'object')

    proj = m.KeilProject(current_project)
    report = m.Report('add_files')
    opts = SimpleNamespace(scan_dirs=str(workspace), scan_files=None, include_h=False)
    m.do_add_files(proj, opts, report)
    added = {name for _group, name in report.files}
    assert 'own.c' in added
    assert 'foreign.c' not in added and 'own.o' not in added
    assert any('跨工程' in warning for warning in report.warnings)
    assert any('构建产物' in note for note in report.notes)

    rtos_include = current / 'Drivers' / 'CMSIS' / 'RTOS2' / 'Include'
    rtos_source = current / 'Drivers' / 'CMSIS' / 'RTOS2' / 'Source'
    rtos_include.mkdir(parents=True)
    rtos_source.mkdir(parents=True)
    (rtos_include / 'cmsis_os2.h').write_text('/* CMSIS-RTOS2 */\n', encoding='utf-8')
    (rtos_include / 'os_tick.h').write_text('/* OS tick */\n', encoding='utf-8')
    os_tick = rtos_source / 'os_systick.c'
    os_tick.write_text('#include "os_tick.h"\n', encoding='utf-8')
    assert m.find_project_cmsis_os2_include(proj, os_tick) == rtos_include.resolve()

print('safety foundation tests passed')
