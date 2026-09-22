import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
TOOL = ROOT / 'keil_port_tool.py'
if not TOOL.exists():
    TOOL = ROOT / 'outputs' / 'keil_port_tool.py'
spec = importlib.util.spec_from_file_location('keil_port_tool_phase5', TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


PROJECT_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<Project><Targets><Target><TargetName>Debug</TargetName><TargetOption>
<TargetCommonOption><Device>STM32F407VG</Device><Cpu>CPUTYPE("Cortex-M4") FPU</Cpu></TargetCommonOption>
<TargetArmAds><Cads><VariousControls><Define>USE_HAL,VALUE=2</Define>
<IncludePath>../Core/Inc;../Drivers/Inc</IncludePath></VariousControls></Cads></TargetArmAds>
</TargetOption><Groups><Group><GroupName>App</GroupName><Files><File>
<FileName>main.c</FileName><FileType>1</FileType><FilePath>../Core/Src/main.c</FilePath>
</File></Files></Group></Groups></Target></Targets></Project>
'''


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / 'Core' / 'Src').mkdir(parents=True)
    (root / 'Core' / 'Inc').mkdir(parents=True)
    project_dir = root / 'MDK-ARM'
    project_dir.mkdir()
    project = project_dir / 'Demo.uvprojx'
    project.write_text(PROJECT_XML, encoding='utf-8')
    proj = m.KeilProject(project)

    settings_path = root / 'settings.json'
    saved = m.save_user_settings({
        'download_retries': 4,
        'proxy': 'http://127.0.0.1:7890',
        'mirror_prefix': 'https://mirror.invalid',
        'extra_scan_skip_dirs': 'Build;Out',
        'uv4_path': 'C:/Keil/UV4.exe',
    }, settings_path)
    assert saved == settings_path
    loaded = m.load_user_settings(settings_path)
    assert loaded['download_retries'] == 4 and loaded['proxy'].endswith('7890')
    m.apply_user_settings(loaded)
    assert m.CONFIG['DOWNLOAD_RETRIES'] == 4
    assert {'build', 'out'}.issubset(m.SCAN_SKIP_DIRS)

    # 本地 file:// 下载也经过临时文件、校验与原子替换逻辑。
    source = root / 'archive.bin'
    source.write_bytes(b'phase-five')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    destination = root / 'download.bin'
    m.CONFIG['DOWNLOAD_PROXY'] = ''
    m.CONFIG['MIRROR_PREFIX'] = ''
    m.download_file(source.as_uri(), destination, 'test', expected_sha256=digest)
    assert destination.read_bytes() == b'phase-five'

    for suffix in ('.md', '.json', '.csv'):
        output = root / ('manifest' + suffix)
        m.export_project_manifest(proj, output)
        assert output.is_file() and output.stat().st_size > 20
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8-sig'))
    target = manifest['targets'][0]
    assert target['name'] == 'Debug' and target['device'] == 'STM32F407VG'
    assert 'USE_HAL' in target['defines'] and target['files'][0]['name'] == 'main.c'

    license_report = root / 'licenses.md'
    m.export_license_report(proj, license_report)
    assert '许可证' in license_report.read_text(encoding='utf-8-sig')

    assert m.update_project_gitignore(proj, False)
    gitignore = root / '.gitignore'
    assert '/.keil-port-tool/' in gitignore.read_text(encoding='utf-8')
    assert not m.update_project_gitignore(proj, False)
    assert m.update_project_gitignore(proj, True)
    assert '/Middlewares/Third_Party/' in gitignore.read_text(encoding='utf-8')

    fake_uv4 = root / 'UV4.exe'
    fake_uv4.write_bytes(b'')
    completed = type('Completed', (), {'returncode': 0, 'stdout': '0 Error(s), 0 Warning(s).'})()
    with mock.patch.object(m.subprocess, 'run', return_value=completed) as runner:
        code, log_path = m.build_keil_project(proj, target='Debug', uv4_path=fake_uv4,
                                              log_file=root / 'build.log')
        assert code == 0
        command = runner.call_args[0][0]
        assert '-b' in command and '-t' in command and 'Debug' in command
        # Hosted Windows TEMP may use RUNNER~1 while the API returns a resolved path.
        assert log_path == (root / 'build.log').resolve()

    # UV4 有时会在日志明确失败时仍返回 0，不得误报成功。
    failed_log = root / 'failed-build.log'
    failed_log.write_text(
        "error - cannot create command input file 'objects\\main.__i'\n"
        'Target not created.\n', encoding='utf-8')
    failed = type('Completed', (), {'returncode': 0, 'stdout': failed_log.read_text()})()
    with mock.patch.object(m.subprocess, 'run', return_value=failed):
        try:
            m.build_keil_project(proj, target='Debug', uv4_path=fake_uv4,
                                 log_file=failed_log)
            raise AssertionError('failed Keil log was accepted as success')
        except m.ToolError as exc:
            assert 'Target not created' in str(exc)
    # A stale successful log + an empty new result must never report success.
    stale = root / 'stale.log'
    stale.write_text('0 Error(s), 0 Warning(s).')
    empty = type('Completed', (), {'returncode': 0, 'stdout': ''})()
    with mock.patch.object(m.subprocess, 'run', return_value=empty):
        try:
            m.build_keil_project(proj, target='Debug', uv4_path=fake_uv4, log_file=stale)
            raise AssertionError('empty build was accepted')
        except m.ToolError as exc:
            assert '不能确认成功' in str(exc)
    assert list(root.glob('stale.log.previous_*'))
    parsed = m.analyze_keil_build_output(
        'Program Size: Code=12\n"Demo" - 0 Error(s), 3 Warning(s).\n')
    assert parsed['errors'] == 0 and parsed['warnings'] == 3

    cli_output = root / 'from-config.json'
    cli_config = root / 'cli.json'
    cli_config.write_text(json.dumps({
        'project': str(project), 'export_project': str(cli_output), 'quiet': True,
        'download_retries': 2,
    }), encoding='utf-8')
    with mock.patch.object(m.sys, 'argv', ['keil_port_tool.py', '--config', str(cli_config)]):
        m.main()
    assert cli_output.is_file() and m.CONFIG['DOWNLOAD_RETRIES'] == 2

print('phase 5 engineering tests passed')
