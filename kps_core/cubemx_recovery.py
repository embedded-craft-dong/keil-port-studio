"""Conservative, explicit replay of recorded integration after regeneration.

No old project XML is restored: freshly generated pins/peripherals stay intact.
Missing application source is never replaced by an outdated template/backup.
"""
import json
import os
from pathlib import Path
import re
import copy

from .cubemx import content_root, inspect_coexistence
from .errors import ToolError
from .ownership import _reverse_owned_hunks
from .source_patches import _c_code, _c_function


def plan_isolation(proj, reader, report):
    """Migrate intact legacy copies, including user edits, out of CubeMX trees.

    Return a remapped manifest and byte-for-byte file copy pairs. Old library
    trees remain as unreferenced backups; old app files are transaction-backed.
    """
    root = content_root(proj).resolve()
    if len(list(root.glob('*.ioc'))) != 1:
        raise ToolError('隔离迁移需要唯一的 IOC / Isolation requires one IOC')
    if set(proj.target_names()) != set(proj.target_names(True)):
        raise ToolError('迁移必须选择全部 Target / Select all targets for isolation')
    issues = inspect_coexistence(proj, reader)
    if any(i['severity'] == 'error' for i in issues):
        raise ToolError('请先恢复完整工程再隔离；不从旧 SDK 重建丢失的用户修改 / '
                        'Restore an intact project before isolation:\n' +
                        '\n'.join(i['code'] for i in issues if i['severity'] == 'error'))
    manifest_path = root / '.keil-port-tool/manifest.json'
    if not manifest_path.is_file():
        raise ToolError('没有安装记录 / No installation manifest')
    data = json.loads(manifest_path.read_text(encoding='utf-8'))
    mapping, copies = {}, []
    for component, record in data['components'].items():
        for item in record.get('copied_dirs', []):
            old = (root / item['path']).resolve()
            if root / 'Middlewares/Third_Party' != old.parent:
                continue
            if not old.is_dir():
                raise ToolError('组件目录缺失 / Missing component tree: ' + str(old))
            new = root / 'KPS/ThirdParty' / old.name
            mapping[old] = new
        if component == 'freertos':
            for item in record.get('generated_files', []):
                old = (root / item['path']).resolve()
                if item.get('created') and old in (root / 'Core/Src/freertos_app.c', root / 'Core/Inc/freertos_app.h'):
                    if not old.is_file():
                        raise ToolError('任务文件缺失 / Missing task file: ' + str(old))
                    mapping[old] = root / 'KPS/FreeRTOS/App' / old.name
    for old, new in mapping.items():
        if new.exists():
            raise ToolError('隔离目标已存在，拒绝覆盖 / Isolation destination exists: ' + str(new))
        proj.remap_file_prefix(old, new)
        if old.is_dir():
            proj.remap_include_prefix(old, new)
            report.copy_trees.append((old, new, '隔离现有源码（保留用户修改） / Isolate current source'))
        else:
            # Core/Inc is still needed for CubeMX headers; add, don't remap it.
            proj.add_include_path(os.path.relpath(new.parent, proj.dir), report)
            copies.append((old, new))

    def remap(value, base):
        path = (base / str(value).replace('\\', '/')).resolve()
        for old, new in mapping.items():
            if path == old or old in path.parents:
                target = new / path.relative_to(old) if path != old else new
                return os.path.relpath(target, base).replace('\\', '/')
        return value

    data = copy.deepcopy(data)
    for record in data['components'].values():
        for item in record.get('project_files', []):
            item['path'] = remap(item['path'], proj.dir)
        record['include_paths'] = [remap(p, proj.dir) for p in record.get('include_paths', [])]
        if 'include_paths_by_target' in record:
            record['include_paths_by_target'] = {t: [remap(p, proj.dir) for p in paths]
                                                for t, paths in record['include_paths_by_target'].items()}
        for key in ('copied_dirs', 'generated_files', 'source_edits'):
            for item in record.get(key, []):
                item['path'] = remap(item['path'], root)
    if copies:
        entry = os.path.relpath(root / 'KPS/FreeRTOS/App', proj.dir).replace('\\', '/')
        includes = data['components']['freertos'].setdefault('include_paths', [])
        if entry not in includes:
            includes.append(entry)
    return data, copies


def replay_hunks(current, hunks, location):
    """Replay only exact, uniquely anchored missing edits; refuse altered edits."""
    text = current.replace('\r\n', '\n').replace('\r', '\n')
    missing = []
    for index, h in enumerate(hunks):
        try:
            _reverse_owned_hunks(text, [h], location)
            continue
        except ToolError:
            pass
        # Both anchors must match. A partial match is not evidence of deletion.
        before, after = h['before'], h['after']
        left, right = h.get('before_left', h['left']), h.get('before_right', h['right'])
        needle = left + before + right
        pattern = (r'\A' if not left else '') + re.escape(needle) + (r'\Z' if not right else '')
        matches = list(re.finditer(pattern, text)) if needle else []
        # Old RT-Thread records used a bare #endif after HardFault's stop loop.
        # Several Cube exceptions end with identical `}\n}\n` context. Bind this
        # closing insertion to the UNIQUE function explicitly named by the
        # preceding recorded opening guard, never choose the first text match.
        if len(matches) > 1 and index and not before and after.strip() == '#endif':
            opening = hunks[index - 1]
            if (not opening['before'] and
                    opening['after'].strip() == '#if !defined(KPS_USING_RTTHREAD)'):
                named = re.match(r'void (HardFault_Handler|PendSV_Handler)\(void\)\n\{\n',
                                 opening.get('before_right', opening['right']))
                span = _c_function(text, named[1]) if named else None
                if span and text[span[2]:span[2] + 1] == '\n':
                    matches = [match for match in matches
                               if match.start() + len(left) == span[2] + 1]
        if len(matches) != 1:
            raise ToolError('重新生成恢复停止：接入段已修改或定位不唯一；保留用户代码 / '
                            'Integration changed or ambiguous: %s' % location)
        # A removed exception guard must never hide newly added user logic.
        for handler, macro in [('SVC_Handler', 'vPortSVCHandler'),
                               ('PendSV_Handler', 'xPortPendSVHandler'),
                               ('PendSV_Handler', 'KPS_USING_RTTHREAD'),
                               ('HardFault_Handler', 'KPS_USING_RTTHREAD')]:
            if macro not in after:
                continue
            span = _c_function(text, handler)
            body = _c_code(text[span[1] + 1:span[2] - 1]).strip() if span else None
            stop_only = (macro == 'KPS_USING_RTTHREAD' and handler == 'HardFault_Handler' and
                         body is not None and re.fullmatch(r'while\s*\(\s*1\s*\)\s*\{\s*\}', body))
            if body is None or (body.strip(' \t\r\n;') and not stop_only):
                raise ToolError('重新生成恢复停止：异常处理函数包含自定义逻辑 / '
                                'Custom exception handler: %s (%s)' % (handler, location))
        start = matches[0].start() + len(left)
        missing.append((start, start + len(before), after))
    missing.sort()
    if any(a[1] > b[0] or a[0] == b[0] for a, b in zip(missing, missing[1:])):
        raise ToolError('恢复补丁重叠 / Overlapping recovery patches: %s' % location)
    for start, end, after in reversed(missing):
        text = text[:start] + after + text[end:]
    _reverse_owned_hunks(text, hunks, location)  # prove original ownership still reversible
    return text if missing else current


def plan_recovery(proj, reader, report, file_types):
    """Mutate an in-memory project only; return proposed source changes in report."""
    root = content_root(proj).resolve()
    manifest_path = root / '.keil-port-tool/manifest.json'
    if not manifest_path.is_file():
        raise ToolError('没有安装记录，不能猜测恢复内容 / No installation manifest')
    issues = inspect_coexistence(proj, reader)
    allowed = {'OWNED_STARTUP_DRIFT', 'INSTALLED_REFERENCE_LOST',
               'INSTALLED_INCLUDE_LOST', 'INSTALLED_DEFINE_LOST'}
    fatal = [i for i in issues if i['severity'] == 'error' and i['code'] not in allowed]
    if fatal:
        raise ToolError('恢复前检查失败 / Recovery preflight failed:\n' + '\n'.join(
            i['code'] + ': ' + i['message']['zh-CN'] + ' / ' + i['message']['en'] for i in fatal))
    data = json.loads(manifest_path.read_text(encoding='utf-8'))['components']
    if not data:
        raise ToolError('没有已安装组件 / No installed components')
    selected = proj.target_names()
    # Shared source hooks cannot be restored for only one of several targets.
    if set(selected) != set(proj.target_names(True)):
        raise ToolError('恢复共享源码必须选择全部 Target / Select all targets for recovery')
    text_updates = {}

    def safe_path(value, base=root):
        path = (base / str(value).replace('\\', '/')).resolve()
        if root not in path.parents or not path.exists():
            raise ToolError('恢复所需路径缺失或超出工程；不生成空模板代替用户源码 / '
                            'Missing or external recovery path: %s' % path)
        return path

    all_records = proj.file_records()
    for component, record in data.items():
        target_names = record.get('targets', [])
        if not target_names or not set(target_names).issubset(selected):
            raise ToolError('安装 Target 已改名/缺失，需人工核对 / Installed target missing: ' + component)
        for edit in record.get('source_edits', []):
            path = safe_path(edit['path'])
            current = text_updates.get(path, reader(path))
            text_updates[path] = replay_hunks(current, edit['hunks'], path)
        for item in record.get('project_files', []):
            path = safe_path(item['path'], proj.dir)
            target = item['target']
            if target not in target_names:
                raise ToolError('归属 Target 不一致 / Inconsistent target ownership')
            proj.select_targets([target])
            present = [r for r in all_records if r['target'] == target and
                       os.path.normcase(str((proj.dir / r['path'].replace('\\', '/')).resolve())) == os.path.normcase(str(path))]
            if present:
                if any(i['code'] == 'INSTALLED_REFERENCE_LOST' and i.get('target') == target and
                       any(e.get('file') == str(path) for e in i['evidence']) for i in issues):
                    raise ToolError('文件或分组已被禁用，不能假定是 CubeMX 删除 / Disabled source: ' + str(path))
                continue
            if path.suffix.lower() not in file_types or not item.get('group'):
                raise ToolError('不完整的文件归属记录 / Incomplete file ownership: ' + str(path))
            if proj.add_file(item['group'], item.get('name') or path.name,
                             file_types[path.suffix.lower()], item['path']):
                report.files.append((item['group'], path.name))
        for target in target_names:
            proj.select_targets([target])
            for entry in record.get('include_paths_by_target', {}).get(target, record.get('include_paths', [])):
                safe_path(entry, proj.dir)
                proj.add_include_path(entry, report)
            for macro in record.get('defines', []):
                name = macro.split('=', 1)[0].strip()
                existing = [part.strip() for c in proj._cads_list()
                            for part in re.split('[,;]', c.findtext('VariousControls/Define', '') or '') if part.strip()]
                if any(part.split('=', 1)[0].strip() == name and part != macro for part in existing):
                    raise ToolError('宏值已改动，不能自动覆盖 / Changed macro: ' + name)
                proj.add_define(macro, report)
    proj.select_targets(selected)
    for path, updated in text_updates.items():
        if updated.replace('\r\n', '\n') != reader(path).replace('\r\n', '\n'):
            report.gen_files.append((path, updated, '恢复记录的组件接入 / Restore recorded integration'))
    # Validate the completed proposal using planned source text, before any writes.
    remaining = inspect_coexistence(proj, lambda p: text_updates.get(Path(p).resolve(), reader(p)))
    if any(i['severity'] == 'error' for i in remaining):
        raise ToolError('恢复规划未通过体检 / Recovery plan failed health check: ' +
                        ', '.join(sorted({i['code'] for i in remaining if i['severity'] == 'error'})))
