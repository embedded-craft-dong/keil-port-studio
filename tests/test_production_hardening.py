import importlib.util
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).parents[1]
TOOL = ROOT / 'keil_port_tool.py'
if not TOOL.exists():
    TOOL = ROOT / 'outputs' / 'keil_port_tool.py'
spec = importlib.util.spec_from_file_location('keil_port_tool_hardening', TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def write(path, content='/* fixture */\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


# 已有 FreeRTOSConfig 值不得被静默覆盖。
conflicts = []
original = '''#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H
#define configMAX_PRIORITIES 8
#define configUSE_TIMERS 0
#endif
'''
patched, changed = m.patch_freertos_config(
    original, 'stm32f4xx.h', conflicts_out=conflicts)
assert changed
assert '#define configMAX_PRIORITIES 8' in patched
assert '#define configUSE_TIMERS 0' in patched
assert ('configMAX_PRIORITIES', '8', '56') in conflicts
assert ('configUSE_TIMERS', '0', '1') in conflicts

# ARMCC5 会吞掉带引号宏后方的定义；工具必须把普通宏排到其前面。
assert m.split_keil_defines(r'A,FUNC=(1,2),HEADER=\"device.h\"') == [
    'A', 'FUNC=(1,2)', r'HEADER=\"device.h\"']


with tempfile.TemporaryDirectory() as td:
    root = Path(td)

    # ZIP 路径穿越必须在写出任何越界文件之前停止。
    malicious = root / 'malicious.zip'
    with zipfile.ZipFile(str(malicious), 'w') as archive:
        archive.writestr('pkg/good.txt', 'ok')
        archive.writestr('../escape.txt', 'bad')
    try:
        m.extract_zip(malicious, root / 'extract')
        raise AssertionError('zip slip was not rejected')
    except m.ToolError:
        pass
    assert not (root / 'escape.txt').exists()

    project_root = root / 'Demo'
    mdk = project_root / 'MDK-ARM'
    project_xml = '''<Project><Targets>
<Target><TargetName>Debug</TargetName><TargetOption><TargetCommonOption>
<Device>STM32F407VG</Device><Cpu>CPUTYPE("Cortex-M4") FPU</Cpu>
</TargetCommonOption><TargetArmAds><ArmAdsMisc><OnChipMemories>
<IRAM><StartAddress>0x20000000</StartAddress><Size>0x8000</Size></IRAM>
</OnChipMemories></ArmAdsMisc><Cads><VariousControls><Define />
<IncludePath>../Core/Inc</IncludePath></VariousControls></Cads></TargetArmAds></TargetOption>
<Groups><Group><GroupName>App</GroupName><Files>
<File><FileName>fatfs.c</FileName><FileType>1</FileType><FilePath>../Core/Src/fatfs.c</FilePath></File>
<File><FileName>sd_diskio.c</FileName><FileType>1</FileType><FilePath>../Core/Src/sd_diskio.c</FilePath></File>
</Files></Group></Groups></Target>
<Target><TargetName>Release</TargetName><TargetOption><TargetCommonOption>
<Device>STM32F407VG</Device><Cpu>CPUTYPE("Cortex-M4") FPU</Cpu>
</TargetCommonOption><TargetArmAds><Cads><VariousControls><Define />
<IncludePath>../Core/Inc</IncludePath></VariousControls></Cads></TargetArmAds></TargetOption>
<Groups /></Target></Targets></Project>'''
    write(mdk / 'Demo.uvprojx', project_xml)
    write(project_root / 'Core' / 'Src' / 'fatfs.c',
          '#include "fatfs.h"\nvoid MX_FATFS_Init(void) { }\n')
    write(project_root / 'Core' / 'Inc' / 'fatfs.h', 'void MX_FATFS_Init(void);\n')
    write(project_root / 'Core' / 'Src' / 'sd_diskio.c')

    fatfs = root / 'SDK' / 'FatFS'
    for name in ('ff.c', 'ff.h', 'diskio.c', 'ff_gen_drv.c', 'ff_gen_drv.h'):
        write(fatfs / name)
    write(fatfs / 'ffconf_template.h',
          '#define FF_FS_REENTRANT 0\n#define FF_FS_TIMEOUT 1000\n#define FF_FS_NORTC 1\n#endif\n')
    proj = m.KeilProject(mdk / 'Demo.uvprojx')
    assert proj.ram_size_bytes() == 32 * 1024
    assert m.embedded_memory_profile(proj)['lvgl_heap_kb'] == 4
    quoted = proj.root.find('.//Cads/VariousControls/Define')
    quoted.text = r'BASE,CMSIS_device_header=\"stm32f4xx.h\"'
    macro_report = m.Report('project_settings')
    proj.add_define('LFS_THREADSAFE', macro_report)
    assert quoted.text.index('LFS_THREADSAFE') < quoted.text.index('CMSIS_device_header')
    rep = m.Report('fatfs')
    opts = SimpleNamespace(fatfs=str(fatfs), fatfs_mode='baremetal', fatfs_app=True,
                           fatfs_files=None, dry_run=False, no_download=True)
    m.do_fatfs(proj, opts, rep)
    generated_names = {Path(path).name for path, _content, _desc in rep.gen_files}
    added_names = {name for _group, name in rep.files}
    assert 'fatfs.c' not in generated_names and 'user_diskio.c' not in generated_names
    assert 'fatfs.c' not in added_names and 'user_diskio.c' not in added_names
    assert any('复用' in note for note in rep.notes)
    assert any('磁盘驱动' in note for note in rep.notes)

    # 不完整的工程私有组件目录不得被“存在即跳过”。
    incomplete = project_root / 'Middlewares' / 'Third_Party' / 'TinyUSB'
    incomplete.mkdir(parents=True)
    source = root / 'SDK' / 'tinyusb-source'
    write(source / 'src' / 'tusb.c')
    try:
        m.plan_project_library_copy(proj, source, 'TinyUSB', m.locate_tinyusb_root, m.Report('tinyusb'))
        raise AssertionError('incomplete target directory was accepted')
    except m.ToolError:
        pass

    # 多 Target 必须逐个调用 UV4，并产生可区分日志名。
    fake_uv4 = root / 'UV4.exe'
    fake_uv4.write_bytes(b'')
    completed = type('Completed', (), {'returncode': 0, 'stdout': '0 Error(s), 0 Warning(s).'})()
    with mock.patch.object(m.subprocess, 'run', return_value=completed) as runner:
        results = m.build_keil_targets(proj, targets=['Debug', 'Release'],
                                       uv4_path=fake_uv4, log_file=root / 'build.log')
        assert len(results) == 2 and runner.call_count == 2
        commands = [call[0][0] for call in runner.call_args_list]
        assert any('Debug' in command for command in commands)
        assert any('Release' in command for command in commands)

    # 重复运行只产生配置变化时，不能丢失旧 manifest 中的工程文件所有权。
    state = m._state_dir(proj)
    state.mkdir(parents=True, exist_ok=True)
    manifest_path = state / 'manifest.json'
    old_record = {
        'version': m.MANIFEST_VERSION,
        'components': {'fatfs': {
            'targets': ['Debug'],
            'project_files': [{'target': 'Debug', 'group': 'App', 'name': 'fatfs.c',
                               'path': '../Core/Src/fatfs.c'}],
            'include_paths': ['../Core/Inc'], 'defines': ['LFS_THREADSAFE'],
            'generated_files': [], 'copied_dirs': [], 'sources': []}}}
    manifest_path.write_text(m.json.dumps(old_record), encoding='utf-8')
    refreshed = project_root / 'Core' / 'Inc' / 'fatfs_generated.h'
    write(refreshed, '/* refreshed */\n')
    rerun = m.Report('fatfs')
    rerun.gen_files.append((refreshed, '/* refreshed */\n', 'refresh'))
    transaction = SimpleNamespace(id='rerun', meta={'created_files': []})
    m.update_component_manifest(proj, [rerun], transaction)
    saved = m.json.loads(manifest_path.read_text(encoding='utf-8'))['components']['fatfs']
    assert any(item['path'] == '../Core/Src/fatfs.c' for item in saved['project_files'])
    assert '../Core/Inc' in saved['include_paths']
    assert 'LFS_THREADSAFE' in saved['defines']

print('production hardening tests passed')
