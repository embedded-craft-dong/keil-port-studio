"""Read-only project health checks. No Tk, global configuration, downloads or writes.

Pin findings are *candidates*: this is not a C preprocessor or call-graph analyzer.
Only explicit HAL/SPL AF assignments in selected, enabled project C/C++ files and
one unambiguous/explicit CubeMX IOC are inspected. No MCU pin database is guessed.
"""
import json
import os
from pathlib import Path
import re
from .cubemx import inspect_coexistence

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_FILES = 1500

LIMITS = {
    'zh-CN': '只读静态检查，不等于编译或实板验证。引脚项仅为候选冲突；未分析调用关系、完整预处理、头文件初始化、宏别名、寄存器写法或实际接线。无发现不代表无问题。导出前注意路径隐私。',
    'en': 'Read-only static checks, not build or hardware validation. Pin findings are candidates: no call graph, full preprocessing, header initializers, macro aliases, register writes or physical wiring analysis. No findings does not prove correctness. Reports may contain private paths.',
}


def _read(path):
    data = path.read_bytes()
    for encoding in (('utf-16',) if data.startswith((b'\xff\xfe', b'\xfe\xff'))
                     else ('utf-8-sig', 'gb18030')):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError('Unsupported text encoding')


def _mask(text):
    # Preserve offsets/line numbers; ignore comments, strings and character literals.
    pattern = r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\''
    code = re.sub(pattern, lambda m: re.sub(r'[^\n]', ' ', m[0]), text)
    stack, output = [], []
    for line in code.splitlines(True):
        directive = re.match(r'\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)', line)
        if directive:
            kind, expr = directive.groups()
            if kind in ('if', 'ifdef', 'ifndef'):
                # Each entry: current branch disabled, simple known condition, any prior branch taken.
                simple = expr.strip() if kind == 'if' else None
                known = simple in ('0', '1')
                stack.append([simple == '0', known, simple == '1'])
            elif kind == 'else' and stack:
                entry = stack[-1]
                entry[0] = entry[2] if entry[1] else False
                entry[2] = True
            elif kind == 'elif' and stack:
                entry = stack[-1]
                simple = expr.strip()
                if entry[1] and (entry[2] or simple in ('0', '1')):
                    entry[0] = entry[2] or simple == '0'
                    entry[2] = entry[2] or simple == '1'
                else:
                    entry[:] = [False, False, False]
            elif kind == 'endif' and stack:
                stack.pop()
            # Keep directives as parser state barriers, including disabled branches.
            output.append(line)
        elif any(entry[0] for entry in stack):
            output.append(re.sub(r'[^\n]', ' ', line))
        else:
            output.append(line)
    return ''.join(output)


_EVENTS = re.compile(
    r'(?P<barrier>^[ \t]*\#[^\n]*)|'
    r'(?P<decl>\bGPIO_InitTypeDef\s+(?P<var>[A-Za-z_]\w*)\s*(?:=[^;]*)?;)|'
    r'(?P<assign>\b(?P<owner>[A-Za-z_]\w*)\.(?P<field>Pin|Mode|Alternate)\s*=\s*(?P<value>[^;]+);)|'
    r'(?P<hal>\bHAL_GPIO_Init\s*\(\s*GPIO(?P<port>[A-K])\s*,\s*&\s*(?P<arg>[A-Za-z_]\w*)\s*\))|'
    r'(?P<spl>\bGPIO_PinAFConfig\s*\(\s*GPIO(?P<sp>[A-K])\s*,\s*GPIO_PinSource(?P<sn>\d+)\s*,\s*(?P<af>GPIO_AF\w+)\s*\))',
    re.M)


def _peripheral(value):
    return value.split('_', 1)[0]


def source_pin_claims(text, source):
    """Extract explicit AF claims, with file and call-site line evidence."""
    code, state, claims = _mask(text), {}, []
    for match in _EVENTS.finditer(code):
        if match['barrier']:
            state.clear()  # Never combine fields from alternative preprocessor branches.
            continue
        if match['decl']:
            state[match['var']] = {}
            continue
        if match['assign']:
            state.setdefault(match['owner'], {})[match['field']] = match['value'].strip()
            continue
        if match['hal']:
            fields = state.get(match['arg'], {})
            if fields.get('Mode') not in ('GPIO_MODE_AF_PP', 'GPIO_MODE_AF_OD'):
                continue
            # Reject partial parsing of expressions containing aliases, masks or arithmetic.
            pins = fields.get('Pin', '')
            if not re.fullmatch(r'[\s()|]*(?:GPIO_PIN_\d+[\s()|]*)+', pins):
                continue
            numbers = [int(n) for n in re.findall(r'GPIO_PIN_(\d+)', pins)]
            af = fields.get('Alternate', '')
            port = match['port']
        else:
            numbers, af, port = [int(match['sn'])], match['af'], match['sp']
        role = re.fullmatch(r'GPIO_AF\d*_([A-Za-z][A-Za-z0-9_]*)', af)
        if not role:
            continue
        for number in sorted(set(numbers)):
            if number > 15:
                continue
            claims.append({'pin': 'P%s%d' % (port, number), 'role': role[1],
                           'peripheral': _peripheral(role[1]), 'file': source,
                           'line': code.count('\n', 0, match.start()) + 1,
                           'origin': 'source', 'confidence': 'candidate'})
    return claims


def ioc_pin_claims(text, source):
    claims = []
    for line_number, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r'\s*(P[A-K](?:1[0-5]|[0-9]))(?:\\?\([^=]*\))?\.Signal\s*=\s*(\w+)\s*', line)
        if match and re.match(r'^(?:USART\d+|UART\d+|FSMC|FMC|SPI\d+|I2C\d+|ETH|SDIO|SDMMC\d*|TIM\d+|USB|OTG)_', match[2]):
            claims.append({'pin': match[1], 'role': match[2], 'peripheral': _peripheral(match[2]),
                           'file': source, 'line': line_number, 'origin': 'ioc', 'confidence': 'declaration'})
    return claims


def _path(base, value):
    value = value.strip().strip('"')
    if not value or '$' in value or '%' in value:
        return None
    return (base / value.replace('\\', '/')).resolve()


def _label(base, path):
    try:
        return os.path.relpath(str(path), str(base)).replace('\\', '/')
    except ValueError:
        return str(path)


def inspect_project(proj, ioc_path=None):
    """Inspect selected Targets without saving or changing the supplied project."""
    base = Path(proj.dir)
    issues, results, cache = [], [], {}
    total_bytes, source_count = 0, 0

    def issue(code, severity, target, zh, en, evidence=()):
        issues.append({'code': code, 'severity': severity, 'target': target,
                       'message': {'zh-CN': zh, 'en': en}, 'evidence': list(evidence)})

    ioc = None
    if ioc_path:
        ioc = Path(ioc_path).resolve()
        if not ioc.is_file() or ioc.suffix.lower() != '.ioc':
            raise ValueError('Expected an existing .ioc file')
    else:
        search_dirs = [base]
        if base.name.lower() in ('mdk-arm', 'mdk', 'keil') or (base.parent / 'Core').is_dir():
            search_dirs.append(base.parent)
        candidates = sorted({p for folder in search_dirs for p in folder.glob('*.ioc')})
        if len(candidates) == 1:
            ioc = candidates[0]
        elif len(candidates) > 1:
            issue('IOC_AMBIGUOUS', 'info', '', '发现多个 IOC，未猜测工程归属；可显式指定。',
                  'Multiple IOC files found; none guessed. Select one explicitly.')
    ioc_claims = []
    if ioc:
        if ioc.stat().st_size > MAX_FILE_BYTES:
            issue('IOC_SKIPPED', 'warning', '', 'IOC 过大，未读取。', 'IOC too large; skipped.')
        else:
            try:
                ioc_claims = ioc_pin_claims(_read(ioc), _label(base, ioc))
            except (OSError, ValueError) as exc:
                issue('IOC_SKIPPED', 'warning', '', 'IOC 无法读取。', 'Cannot read IOC.',
                      [{'file': _label(base, ioc), 'reason': type(exc).__name__}])

    for target in proj.targets:
        name = target.findtext('TargetName', '')
        claims, seen, scanned = list(ioc_claims), {}, 0
        skipped = 0
        for group in target.findall('Groups/Group'):
            if group.findtext('GroupOption/CommonProperty/IncludeInBuild') == '0':
                continue
            for node in group.findall('Files/File'):
                if node.findtext('FileOption/CommonProperty/IncludeInBuild') == '0':
                    continue
                value = node.findtext('FilePath', '')
                path = _path(base, value)
                evidence = [{'file': value, 'group': group.findtext('GroupName', '')}]
                if path is None:
                    issue('PATH_UNRESOLVED', 'info', name, '文件路径含未解析变量或为空。',
                          'File path is empty or contains unresolved variables.', evidence)
                    continue
                key = os.path.normcase(str(path))
                if key in seen:
                    issue('DUPLICATE_FILE', 'warning', name, '同一 Target 重复引用文件。',
                          'File referenced more than once in this Target.', evidence)
                    continue
                seen[key] = value
                if not path.is_file():
                    issue('MISSING_FILE', 'error', name, '工程引用的文件不存在。',
                          'Referenced project file is missing.', evidence)
                    continue
                if path.suffix.lower() not in ('.c', '.cpp', '.cxx', '.cc'):
                    continue
                if key not in cache:
                    try:
                        size = path.stat().st_size
                        if size > MAX_FILE_BYTES or total_bytes + size > MAX_SOURCE_BYTES or source_count >= MAX_SOURCE_FILES:
                            cache[key] = None
                        else:
                            source_count += 1
                            total_bytes += size
                            cache[key] = source_pin_claims(_read(path), _label(base, path))
                    except (OSError, ValueError):
                        cache[key] = None
                if cache[key] is None:
                    skipped += 1
                    issue('SOURCE_SKIPPED', 'warning', name, '源码无法读取或超出扫描上限，未检查。',
                          'Source unreadable or scan limit exceeded; not inspected.', evidence)
                else:
                    scanned += 1
                    claims.extend(cache[key])
        for cads in proj._target_cads(target):
            for value in (cads.findtext('VariousControls/IncludePath', '') or '').split(';'):
                if not value.strip():
                    continue
                path = _path(base, value)
                if path is None:
                    issue('INCLUDE_UNRESOLVED', 'info', name, 'Include 含变量，未解析。',
                          'Include directory contains unresolved variables.', [{'file': value}])
                elif not path.is_dir():
                    issue('MISSING_INCLUDE', 'warning', name, 'Include 目录不存在。',
                          'Include directory does not exist.', [{'file': value}])
        by_pin = {}
        for claim in claims:
            by_pin.setdefault(claim['pin'], []).append(claim)
        for pin, evidence in sorted(by_pin.items()):
            roles = sorted({c['peripheral'] for c in evidence})
            if len(roles) > 1:
                issue('PIN_AF_CONFLICT', 'warning', name,
                      '%s 存在不同外设配置：%s。请确认是否同时初始化、是否有条件编译或 IOC 已过期；不要直接自动改线。' % (pin, ', '.join(roles)),
                      '%s has different peripheral assignments: %s. Check simultaneous initialization, conditional code and stale IOC; do not rewire automatically.' % (pin, ', '.join(roles)), evidence)
        results.append({'target': name, 'source_files_scanned': scanned, 'source_files_skipped': skipped,
                        'pin_claims': claims})
    coexistence = inspect_coexistence(proj, _read, ioc_path=ioc)
    issues.extend(item for item in coexistence if not (
        item['code'] == 'CUBEMX_IOC_AMBIGUOUS' and item['severity'] != 'error' and
        any(existing['code'] == 'IOC_AMBIGUOUS' for existing in issues)))
    counts = {level: sum(i['severity'] == level for i in issues) for level in ('error', 'warning', 'info')}
    return {'schema_version': 1, 'read_only': True, 'status': 'findings' if issues else 'no_findings',
            'project': str(proj.path), 'ioc': str(ioc) if ioc else None,
            'summary': counts, 'targets': results, 'issues': issues, 'limits': dict(LIMITS)}


def format_report(report, language='zh-CN'):
    lang = 'en' if language == 'en' else 'zh-CN'
    lines = [('Project health check (read-only)' if lang == 'en' else '工程体检（只读）'),
             report['project'], report['limits'][lang], '',
             'error={error}  warning={warning}  info={info}'.format(**report['summary'])]
    for target in report['targets']:
        lines.append('Target: %s | scanned=%d | skipped=%d | pin claims=%d' % (
            target['target'], target['source_files_scanned'], target['source_files_skipped'], len(target['pin_claims'])))
    if not report['issues']:
        lines.append('No findings within the limited scan scope.' if lang == 'en' else '在本次有限扫描范围内未发现问题。')
    for item in report['issues']:
        lines.extend(('', '[%s] %s / %s' % (item['severity'].upper(), item['target'], item['code']), item['message'][lang]))
        for evidence in item['evidence']:
            lines.append('  %s%s%s' % (evidence.get('file', ''),
                         ':' + str(evidence['line']) if 'line' in evidence else '',
                         '  ' + evidence['role'] if 'role' in evidence else ''))
    return '\n'.join(lines) + '\n'


def export_report(report, destination):
    """Only the explicit report is written; refuse existing files (including source)."""
    path = Path(destination)
    if path.suffix.lower() != '.json':
        raise ValueError('Report destination must end in .json')
    with path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return path
