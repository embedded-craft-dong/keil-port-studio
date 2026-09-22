import importlib.util
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parents[1]
TOOL = ROOT / 'keil_port_tool.py'
if not TOOL.exists():
    TOOL = ROOT / 'outputs' / 'keil_port_tool.py'
spec = importlib.util.spec_from_file_location('keil_port_tool_settings', TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def target(name, startup):
    return '''<Target><TargetName>{name}</TargetName><TargetOption>
<TargetCommonOption><Device>STM32F407VG</Device><Cpu>CPUTYPE("Cortex-M4") FPU</Cpu>
<DebugInformation>1</DebugInformation></TargetCommonOption>
<TargetArmAds><Cads><Optim>1</Optim><oTime>0</oTime><VariousControls>
<MiscControls /><Define>BASE,VALUE=1,OLD</Define>
<IncludePath>Inc;./Inc;Missing</IncludePath></VariousControls></Cads>
<LDads><umfTarg>1</umfTarg><useFile>0</useFile><ScatterFile /></LDads></TargetArmAds>
</TargetOption><Groups><Group><GroupName>Startup</GroupName><Files><File>
<FileName>{startup}</FileName><FileType>2</FileType><FilePath>{startup}</FilePath>
</File></Files></Group></Groups></Target>'''.format(name=name, startup=startup)


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / 'Inc').mkdir()
    project = root / 'demo.uvprojx'
    project.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<Project><Targets>' +
                       target('Debug', 'startup_debug.s') +
                       target('Release', 'startup_release.s') +
                       '</Targets></Project>\n', encoding='utf-8')
    for name in ('startup_debug.s', 'startup_release.s'):
        (root / name).write_text(
            'Stack_Size      EQU     0x400\nHeap_Size       EQU     0x200\n',
            encoding='utf-8')
    scatter = root / 'memory.sct'
    scatter.write_text('LR_IROM1 0x08000000 0x10000 { ER_IROM1 0x08000000 0x10000 { * (+RO) } }\n',
                       encoding='utf-8')
    source = root / 'Mixed'
    source.mkdir()
    names = ('code.c', 'class.cpp', 'port.S', 'legacy.asm', 'prebuilt.o',
             'vendor.lib', 'vendor.a', 'api.hpp')
    for name in names:
        (source / name).write_text('/* test */\n', encoding='utf-8')

    proj = m.KeilProject(project)
    proj.select_targets(['Debug'])
    add_report = m.Report('add_files')
    add_opts = SimpleNamespace(scan_dirs=str(source),
                               scan_files={source / name for name in names}, include_h=True)
    m.do_add_files(proj, add_opts, add_report)
    types = {record['name']: next(
        f.findtext('FileType') for target_el in proj.targets
        for f in target_el.findall('.//File') if f.findtext('FileName') == record['name'])
             for record in proj.file_records() if record['name'] in names}
    assert types['code.c'] == '1'
    assert types['class.cpp'] == '8'
    assert types['port.S'] == '2' and types['legacy.asm'] == '2'
    assert types['prebuilt.o'] == '3'
    assert types['vendor.lib'] == '4' and types['vendor.a'] == '4'
    assert types['api.hpp'] == '5'

    settings = SimpleNamespace(
        define_values=['VALUE=2', 'NEW_FEATURE'], remove_defines=['OLD'],
        clean_includes=True, remove_missing_includes=True,
        optimization='O2', debug_information=False,
        scatter_file=str(scatter), clear_scatter=False,
        stack_size='0x800', heap_size='1024')
    report = m.Report('project_settings')
    m.do_project_settings(proj, settings, report)

    debug = proj.targets[0]
    define = debug.findtext('TargetOption/TargetArmAds/Cads/VariousControls/Define')
    assert 'VALUE=2' in define and 'VALUE=1' not in define
    assert 'NEW_FEATURE' in define and 'OLD' not in define
    assert debug.findtext('TargetOption/TargetArmAds/Cads/Optim') == '3'
    assert debug.findtext('TargetOption/TargetCommonOption/DebugInformation') == '0'
    assert debug.findtext('TargetOption/TargetArmAds/LDads/useFile') == '1'
    assert debug.findtext('TargetOption/TargetArmAds/LDads/umfTarg') == '0'
    assert debug.findtext('TargetOption/TargetArmAds/LDads/ScatterFile') == 'memory.sct'
    include = debug.findtext('TargetOption/TargetArmAds/Cads/VariousControls/IncludePath')
    assert include == 'Inc;Mixed'
    startup_update = next(content for path, content, _desc in report.gen_files
                          if Path(path).name == 'startup_debug.s')
    assert 'Stack_Size      EQU     0x800' in startup_update
    assert 'Heap_Size       EQU     0x400' in startup_update

    release = proj.all_targets[1]
    assert release.findtext('TargetOption/TargetArmAds/Cads/Optim') == '1'
    assert release.findtext('TargetOption/TargetCommonOption/DebugInformation') == '1'
    assert 'VALUE=1' in release.findtext(
        'TargetOption/TargetArmAds/Cads/VariousControls/Define')
    assert not any(f.findtext('FileName') == 'code.c' for f in release.findall('.//File'))

print('project settings tests passed')
