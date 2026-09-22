"""Build a portable folder + reviewed allowlist source archive, without publishing.

Run from a Windows build venv: python tools/build_release.py OUTPUT_DIRECTORY
Refuses existing output directories. PyInstaller does not need administrator rights.
"""
import hashlib
import ast
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def source_files(repo):
    """Single publication allowlist shared by packaging and read-only auditing."""
    files = [repo / name for name in (
        'keil_port_tool.py', 'LICENSE', 'README.md', 'README.en.md', 'CONTRIBUTING.md',
        'THIRD-PARTY-NOTICES.md', 'requirements-build.txt', '.gitignore')]
    for folder in ('docs', 'tests', 'tools', '.github', 'kps_core'):
        files.extend(p for p in (repo / folder).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts)
    for path in files:
        if path.is_symlink() or repo.resolve() not in path.resolve().parents:
            raise ValueError('Publication file is a symlink or outside repository: ' + str(path))
    return sorted(files)


def tool_version(repo):
    tree = ast.parse((repo / 'keil_port_tool.py').read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'TOOL_VERSION' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('Missing TOOL_VERSION')


def main():
    repo = Path(__file__).resolve().parents[1]
    # Fail before starting a lengthy build if publication checks fail.
    subprocess.run([sys.executable, str(repo / 'tools/audit_publication.py')], check=True)
    files = source_files(repo)
    source_hashes = {p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    out = Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed',
                    '--onedir', '--name', 'KeilPortStudio', '--distpath', str(out / 'app'),
                    '--workpath', str(out / 'build'), '--specpath', str(out / 'build'),
                    '--add-data', str(repo / 'docs') + ';docs',
                    str(repo / 'keil_port_tool.py')], cwd=repo, check=True)
    app = out / 'app' / 'KeilPortStudio'
    for name in ('LICENSE', 'README.md', 'README.en.md', 'THIRD-PARTY-NOTICES.md', 'CONTRIBUTING.md'):
        shutil.copy2(repo / name, app / name)
    shutil.copytree(repo / 'docs', app / 'docs')
    licenses = app / 'runtime-licenses'; licenses.mkdir()
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE.txt', licenses / 'Python-LICENSE.txt')
    for package in ('pyinstaller', 'pyinstaller-hooks-contrib', 'altgraph', 'packaging',
                    'pefile', 'pywin32-ctypes', 'setuptools'):
        distribution = importlib.metadata.distribution(package)
        for source in distribution.files or []:
            if source.name.lower().startswith(('license', 'copying', 'copyright')):
                path = Path(distribution.locate_file(source))
                if path.is_file():
                    dest = licenses / package / str(source)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dest)
    # CPython 3.14 ships Tcl/Tk runtime files and their licenses in zip bundles.
    for bundle in (Path(sys.base_prefix) / 'tcl').glob('*.zip'):
        with zipfile.ZipFile(bundle) as archive:
            for name in archive.namelist():
                if Path(name).name.lower().startswith(('license', 'copyright')) and not name.endswith('/'):
                    dest = licenses / (bundle.stem + '-' + Path(name).name)
                    dest.write_bytes(archive.read(name))
    code_files = [repo / 'keil_port_tool.py'] + sorted((repo / 'kps_core').glob('*.py'))
    metadata = {'python': sys.version, 'platform': sys.platform, 'tool_version': tool_version(repo),
                'reviewed_files': source_hashes,
                'source_sha256': hashlib.sha256((repo / 'keil_port_tool.py').read_bytes()).hexdigest(),
                'source_files': {p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_files},
                'packages': {name: importlib.metadata.version(name) for name in ('pyinstaller', 'pyinstaller-hooks-contrib')},
                'signed': False}
    (app / 'BUILD-INFO.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    source_zip = out / 'KeilPortStudio-source.zip'
    if source_hashes != {p.relative_to(repo).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files(repo)}:
        raise RuntimeError('Source changed during build; do not release this output')
    with zipfile.ZipFile(source_zip, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, 'KeilPortStudio-source/' + path.relative_to(repo).as_posix())
    binary_zip = Path(shutil.make_archive(str(out / 'KeilPortStudio-Windows-x64'), 'zip', root_dir=app.parent, base_dir=app.name))
    checks = [(path.name, hashlib.sha256(path.read_bytes()).hexdigest()) for path in (source_zip, binary_zip)]
    (out / 'SHA256SUMS.txt').write_text(''.join(sha + '  ' + name + '\n' for name, sha in checks), encoding='ascii')
    print(out)


if __name__ == '__main__':
    main()
