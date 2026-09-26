"""Explicit reference-only removal. No source deletion or implicit directory scan."""
import copy
import json

from .errors import ToolError


def inventory(proj):
    """Return selectable project-level entries, retaining Target identity."""
    rows = []
    for record in proj.file_records():
        rows.append(dict(record, kind='file'))
    for target in proj.targets:
        for cads in proj._target_cads(target):
            for entry in (cads.findtext('VariousControls/IncludePath', '') or '').split(';'):
                if entry.strip():
                    rows.append(dict(kind='include', target=target.findtext('TargetName', ''),
                                     path=entry.strip(), group='Include', name=''))
    # Identical entries in one Target are removed together, never across Targets.
    return list({(r['kind'], r['target'], proj.norm_file(r['path'])): r for r in rows}.values())


def remove_references(api, proj, selections, yes=False, dry_run=False,
                      preview_callback=None, diff_file=None):
    if not selections:
        raise ToolError('请先选择引用 / Select references first')
    current = {(r['kind'], r['target'], proj.norm_file(r['path'])): r for r in inventory(proj)}
    keys = {(r['kind'], r['target'], proj.norm_file(r['path'])) for r in selections}
    if not keys <= current.keys():
        raise ToolError('引用列表已变化，请刷新 / References changed; refresh the list')
    state = api._state_dir(proj) / 'manifest.json'
    original_state = state.read_bytes() if state.exists() else None
    # A damaged manifest must never silently disable ownership protection.
    try:
        manifest = json.loads(state.read_text(encoding='utf-8')) if state.exists() else {'components': {}}
        components = manifest['components']
        if not isinstance(components, dict):
            raise ValueError('components')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ToolError('无法读取归属清单 / Invalid ownership manifest: %s' % exc)
    for component, data in components.items():
        if component == 'add_files':
            continue
        owned = {('file', r['target'], proj.norm_file(r['path'])) for r in data.get('project_files', [])}
        owned.update(('include', t, proj.norm_file(p)) for p in data.get('include_paths', [])
                     for t in data.get('targets', []))
        if keys & owned:
            raise ToolError('所选引用属于组件 %s，请使用安全与恢复卸载组件 / '
                            'Managed component reference: use component uninstall (%s)' % (component, component))

    # Work on a clone so cancellation/dry-run/error leave the caller unchanged.
    edited = copy.deepcopy(proj)
    for kind, target_name, key in sorted(keys):
        edited.select_targets([target_name])
        raw = current[(kind, target_name, key)]['path']
        if kind == 'file':
            edited.remove_file(raw)
        else:
            # Exact token removal supports $(...) variables without expanding them.
            for cads in edited._cads_list():
                inc = cads.find('VariousControls/IncludePath')
                if inc is not None:
                    inc.text = ';'.join(x.strip() for x in (inc.text or '').split(';')
                                        if x.strip() and edited.norm_file(x.strip()) != key)
                    edited.dirty = True
    edited.select_targets(proj.target_names())
    add = components.get('add_files', {})
    old_includes = list(add.get('include_paths', []))
    per_target = add.get('include_paths_by_target', {})
    if 'add_files' in components:
        add['include_paths_by_target'] = {
            t: [p for p in per_target.get(t, old_includes)
                if ('include', t, proj.norm_file(p)) not in keys]
            for t in add.get('targets', [])}
    add['project_files'] = [r for r in add.get('project_files', [])
                           if ('file', r['target'], proj.norm_file(r['path'])) not in keys]
    # Include ownership has no per-Target field: retain it while any Target uses it.
    all_proj = copy.deepcopy(edited)
    all_proj.select_targets([t.findtext('TargetName', '') for t in all_proj.all_targets])
    remaining = {all_proj.norm_file(r['path']) for r in inventory(all_proj) if r['kind'] == 'include'}
    add['include_paths'] = [p for p in add.get('include_paths', []) if proj.norm_file(p) in remaining]
    preview = ('仅移除工程引用，不删除磁盘文件；Include 删除可能影响剩余文件编译。\n'
               'References only; source files stay on disk. Removing includes may break remaining sources.\n'
               + '\n'.join('%s | %s | %s' % (k, t, current[(k, t, p)]['path']) for k, t, p in sorted(keys))
               + '\n' + api.build_diff_preview(edited, []))
    if diff_file:
        api.Path(diff_file).write_text(preview, encoding='utf-8')
    if dry_run:
        api.log(preview)
        return False
    if not api._confirm_safety_action(preview, yes, preview_callback):
        return False
    # Protect a project modified while its preview dialog was open.
    if proj.path.read_bytes() != proj.original_bytes:
        raise ToolError('工程已被其他程序修改，请重新加载 / Project changed; reload before retrying')
    if (state.read_bytes() if state.exists() else None) != original_state:
        raise ToolError('归属记录已变化，请重新加载 / Ownership changed; reload before retrying')
    tx = api.ProjectTransaction(edited, 'remove-references', [])
    tx.snapshot(edited.path)
    tx.snapshot(state)
    tx.save_meta('prepared')
    try:
        edited.save()
        if state.exists():
            state.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        tx.save_meta('success')
    except Exception as exc:
        tx.rollback(exc)
        raise ToolError('移除失败，已回滚 / Removal rolled back: %s' % exc)
    api.info('已移除 %d 个引用，磁盘源码保留 / Removed %d references; source files preserved' % (len(keys), len(keys)))
    return True
