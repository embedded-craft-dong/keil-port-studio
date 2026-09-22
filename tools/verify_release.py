"""Verify archive hashes, source parity, private-file exclusions and runtime payload."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile
from build_release import source_files, tool_version


def main():
    root = Path(sys.argv[1]).resolve()
    source = Path(__file__).resolve().parents[1] / 'keil_port_tool.py'
    for line in (root / 'SHA256SUMS.txt').read_text().splitlines():
        sha, name = line.split('  ', 1)
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == sha, name
    with zipfile.ZipFile(root / 'KeilPortStudio-source.zip') as archive:
        assert archive.read('KeilPortStudio-source/keil_port_tool.py') == source.read_bytes()
        for name in archive.namelist():
            assert not set(Path(name).parts) & {'hardware_tests', '__pycache__', '.git', 'releases', '.venv'}, name
            assert Path(name).suffix not in ('.exe', '.hex', '.axf', '.pyc'), name
    with zipfile.ZipFile(root / 'KeilPortStudio-Windows-x64.zip') as archive:
        prefix = 'KeilPortStudio/'
        info = json.loads(archive.read(prefix + 'BUILD-INFO.json'))
        assert info['tool_version'] == tool_version(source.parent), 'Version mismatch'
        assert info['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
        expected_files = [source] + sorted((source.parent / 'kps_core').glob('*.py'))
        expected_hashes = {p.relative_to(source.parent).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in expected_files}
        assert info['source_files'] == expected_hashes, 'Bundled modules differ from working source'
        with zipfile.ZipFile(root / 'KeilPortStudio-source.zip') as sources:
            reviewed = {p.relative_to(source.parent).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in source_files(source.parent)}
            assert info['reviewed_files'] == reviewed, 'Reviewed source/docs changed'
            assert set(sources.namelist()) == {'KeilPortStudio-source/' + name for name in reviewed}, 'Source allowlist mismatch'
            assert len(sources.namelist()) == len(set(sources.namelist())), 'Duplicate ZIP entries'
            for name, sha in reviewed.items():
                assert hashlib.sha256(sources.read('KeilPortStudio-source/' + name)).hexdigest() == sha, name
                if name.startswith('docs/'):
                    assert hashlib.sha256(archive.read(prefix + name)).hexdigest() == sha, name
                    assert hashlib.sha256(archive.read(prefix + '_internal/' + name)).hexdigest() == sha, name
            for name, sha in expected_hashes.items():
                assert hashlib.sha256(sources.read('KeilPortStudio-source/' + name)).hexdigest() == sha, name
        for name in ('KeilPortStudio.exe', '_internal/python314.dll', '_internal/_tkinter.pyd',
                     'LICENSE', 'runtime-licenses/Python-LICENSE.txt', 'docs/POST-PORTING.en.md',
                     'docs/POST-PORTING.zh-CN.md', '_internal/docs/GIT.en.md'):
            assert prefix + name in archive.namelist(), name
        assert any('libtcl' in name and 'license' in name for name in archive.namelist())
        assert any('libtk' in name and 'license' in name for name in archive.namelist())
    print('PASS: hashes, source parity, archive exclusions, runtime payload and notices')


if __name__ == '__main__':
    main()
