"""Read-only publication checks; not a substitute for human or full secret review.

python tools/audit_publication.py [--output PATH]
Only the build allowlist is scanned. No Git index changes or network operations.
Reports locations/categories rather than echoing potential secret values.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

from build_release import source_files, tool_version

TEXT_SUFFIXES = {'.py', '.md', '.txt', '.yml', '.yaml', '.json', '.toml'}
ALLOWED_SUFFIXES = TEXT_SUFFIXES | {'.png', '.jpg', '.svg'}
CHECKS = {
    'private_user_path': re.compile(r'[A-Za-z]:[/\\]Users[/\\](?!Public\b|YourName\b)[^/\\\s]+', re.I),
    'local_fixture_path': re.compile(r'[A-Za-z]:[/\\](?:[^\s\"\x27/\\]+[/\\])*(?:Tool_Test_|PID_Test)[^\s\"\x27]*', re.I),
    'github_token': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'private_key': re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----'),
}


def audit(repo):
    files = source_files(repo)
    issues = []
    manifest = {}
    for path in files:
        name = path.relative_to(repo).as_posix()
        if not path.is_file():
            issues.append({'file': name, 'kind': 'missing_file'})
            continue
        data = path.read_bytes()
        manifest[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        if path.suffix.lower() not in ALLOWED_SUFFIXES and path.name not in {'LICENSE', '.gitignore'}:
            issues.append({'file': name, 'kind': 'unexpected_file_type'})
        if len(data) > 10 * 1024 * 1024:
            issues.append({'file': name, 'kind': 'oversized_source_file'})
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {'LICENSE', '.gitignore'}:
            continue
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            issues.append({'file': name, 'kind': 'non_utf8_text'})
            continue
        for kind, pattern in CHECKS.items():
            for match in pattern.finditer(text):
                issues.append({'file': name, 'line': text.count('\n', 0, match.start()) + 1, 'kind': kind})
        if path.suffix == '.md':
            for match in re.finditer(r'\]\(([^)\n]+)\)', text):
                href = match[1].strip().split(' "', 1)[0].strip('<>')
                if re.match(r'^[a-zA-Z][\w+.-]*:', href) or href.startswith('#'):
                    continue
                target = (path.parent / unquote(href.split('#')[0])).resolve()
                if not target.exists():
                    issues.append({'file': name, 'kind': 'missing_local_link', 'target': href})
    version = tool_version(repo)
    for name in ('README.md', 'README.en.md', 'docs/RELEASE.zh-CN.md', 'docs/RELEASE.en.md'):
        if version not in (repo / name).read_text(encoding='utf-8'):
            issues.append({'file': name, 'kind': 'version_not_documented'})
    return {'passed': not issues, 'version': version, 'file_count': len(manifest),
            'total_bytes': sum(v['bytes'] for v in manifest.values()),
            'issues': issues, 'files': manifest,
            'limits': 'Heuristic allowlist scan; does not certify absence of all secrets or audit Git history.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = audit(Path(__file__).resolve().parents[1])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in result.items() if k != 'files'}, indent=2, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
