"""Read-only generator ownership checks; never edit IOC or run CubeMX.

Detect competing middleware owners and drift of recorded tool hooks after code
regeneration. Drift may also be caused by manual edits: do not attribute it to
CubeMX with certainty and never silently overwrite it.
"""
import json
import os
from pathlib import Path
import re

from .errors import ToolError
from .ownership import _reverse_owned_hunks
from .project_layout import active_sources
from .source_patches import _c_code


def content_root(proj):
    parent = proj.dir.parent
    return parent if (parent / 'Core').is_dir() or any(parent.glob('*.ioc')) else proj.dir


def inspect_coexistence(proj, reader, requested=(), ioc_path=None):
    """Return doctor-compatible issues, scoped to selected Targets/components."""
    root = content_root(proj).resolve()
    issues = []
    selected = set(proj.target_names())

    def issue(code, component, zh, en, evidence=(), severity='error', target=''):
        issues.append({'code': code, 'component': component, 'severity': severity,
                       'target': target, 'message': {'zh-CN': zh, 'en': en},
                       'evidence': list(evidence)})

    def resolve_owned(value):
        path = (root / str(value).replace('\\', '/')).resolve()
        if path == root or root not in path.parents:
            raise ValueError('manifest path outside project')
        return path

    state = root / '.keil-port-tool/manifest.json'
    installed = {}
    if state.is_file():
        try:
            if state.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('manifest too large')
            installed = json.loads(state.read_text(encoding='utf-8'))['components']
            if not isinstance(installed, dict):
                raise ValueError('invalid components')
        except (OSError, ValueError, KeyError, TypeError):
            issue('OWNERSHIP_RECORD_INVALID', '', '安装归属记录无法读取，停止自动覆盖。',
                  'Ownership record is unreadable; do not overwrite.', [{'file': str(state)}])
            return issues
    monitored = set(requested) | set(installed)
    iocs = [Path(ioc_path).resolve()] if ioc_path else sorted(set(root.glob('*.ioc')) | set(proj.dir.glob('*.ioc')))
    if len(iocs) > 1:
        issue('CUBEMX_IOC_AMBIGUOUS', '', '存在多个 IOC，需明确工程归属再移植。',
              'Multiple IOC files; resolve ownership before porting.', [{'file': str(p)} for p in iocs],
              'error' if monitored & {'freertos', 'rtthread', 'fatfs', 'lwip', 'tinyusb'} else 'warning')
    else:
        for ioc in iocs:
            try:
                if ioc.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError('IOC too large')
                ioc_text = reader(ioc)
                values = re.findall(r'^Mcu\.IP\d+\s*=\s*(\w+)\s*$', ioc_text, re.M)
            except (OSError, ValueError, ToolError):
                issue('CUBEMX_IOC_UNREADABLE', '', 'IOC 无法完整检查。', 'Cannot inspect IOC.', [{'file': str(ioc)}])
                continue
            if monitored and re.search(r'^ProjectManager\.KeepUserCode=false\s*$', ioc_text, re.M | re.I):
                issue('CUBEMX_USER_CODE_NOT_PRESERVED', '',
                      'IOC 未开启 Keep User Code；请先在 CubeMX 启用用户代码保留。',
                      'Enable Keep User Code in CubeMX before integration.', [{'file': str(ioc)}])
            ownership = {'FREERTOS': ('freertos', 'rtthread'), 'FATFS': ('fatfs',),
                         'LWIP': ('lwip',), 'USB_DEVICE': ('tinyusb',), 'USB_HOST': ('tinyusb',)}
            for value in values:
                for component in ownership.get(value.upper(), ()):
                    if component in monitored:
                        issue('CUBEMX_MIDDLEWARE_OWNER', component,
                              'CubeMX IOC 已启用 %s，与工具组件 %s 存在重复管理风险。选择一个管理方；不自动改写 IOC。' % (value, component),
                              'CubeMX IOC enables %s, competing with %s. Choose one owner; IOC is never rewritten.' % (value, component),
                              [{'file': str(ioc), 'role': value}])

    # Installed references are per Target. Disabled sources also count as lost.
    active_records = set()
    for target in proj.targets:
        name = target.findtext('TargetName', '')
        for group in target.findall('Groups/Group'):
            if group.findtext('GroupOption/CommonProperty/IncludeInBuild') == '0': continue
            for node in group.findall('Files/File'):
                if node.findtext('FileOption/CommonProperty/IncludeInBuild') == '0': continue
                value = node.findtext('FilePath', '').replace('\\', '/')
                active_records.add((name, os.path.normcase(str((proj.dir / value).resolve()))))
    owned_paths = set()
    for component, record in installed.items():
        try:
            if not isinstance(record, dict): raise ValueError('invalid component record')
            targets = set(record.get('targets', [])) & selected
            if record.get('targets') and not targets: continue
            if iocs and any(str(item.get('path', '')).replace('\\', '/').startswith('Middlewares/')
                            for item in record.get('copied_dirs', [])):
                issue('CUBEMX_LEGACY_DIRECTORY', component,
                      '旧组件位于 CubeMX 可清理的 Middlewares 目录；生成前请执行“隔离旧组件”（--cubemx-protect）。',
                      'Legacy copy is under CubeMX-owned Middlewares. Run --cubemx-protect before generation.',
                      [{'file': str(root / 'Middlewares')}], severity='warning')
            for item in record.get('project_files', []):
                path = (proj.dir / item['path'].replace('\\', '/')).resolve()
                owned_paths.add(path)
                name = item.get('target', '')
                if name in selected and not path.is_file():
                    issue('INSTALLED_SOURCE_LOST', component,
                          '已安装源码文件缺失；不能用旧模板覆盖或假装已恢复。请从重新生成前的备份找回。',
                          'Installed source is missing. Restore a pre-generation backup; no stale template replacement.',
                          [{'file': str(path)}], target=name)
                if name in selected and (name, os.path.normcase(str(path))) not in active_records:
                    issue('INSTALLED_REFERENCE_LOST', component,
                          '组件工程引用被删除或禁用；可能由重新生成或手工修改造成。',
                          'Installed reference removed/disabled, possibly by regeneration or manual edits.',
                          [{'file': str(path)}], target=name)
            values = {}
            for edit in reversed(record.get('source_edits', [])):
                path = resolve_owned(edit['path'])
                owned_paths.add(path)
                if path not in values:
                    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                        raise ValueError('owned source missing/too large: ' + str(path))
                    values[path] = reader(path)
                try:
                    values[path] = _reverse_owned_hunks(values[path], edit['hunks'], path)
                except ToolError:
                    legacy_spl = any('KPS SPL COMPONENT ' in h.get('after', '') and
                                     'spl_entry' not in h for h in edit['hunks'])
                    issue('OWNED_STARTUP_DRIFT', component,
                          '记录的源码接入段缺失或被修改。请对比重新生成前的版本；不重复叠加或盲目覆盖。' +
                          (' 此工程含旧开发版 SPL 按行归属记录，也可能是相邻组件造成的重叠。'
                           '请先备份并核对差异，再预览旧安装事务回滚/重新移植；不要删除 manifest 绕过保护。' if legacy_spl else ''),
                          'Recorded source integration is missing/modified. Compare the pre-generation version; no blind replay.' +
                          (' Legacy development SPL line-based ownership can also overlap adjacent components. '
                           'Back up and compare before previewing rollback/reinstallation; do not delete the manifest to bypass protection.' if legacy_spl else ''),
                          [{'file': str(path)}])
                    break
            for item in record.get('generated_files', []):
                path = resolve_owned(item['path'])
                if item.get('created'): owned_paths.add(path)
                if not path.is_file():
                    issue('GENERATED_FILE_LOST', component, '已安装组件的文件缺失。',
                          'Installed generated file is missing.', [{'file': str(path)}])
            for target in proj.targets:
                name = target.findtext('TargetName', '')
                if targets and name not in targets: continue
                controls = proj._target_cads(target)
                incs = {os.path.normcase(str((proj.dir / part.strip().replace('\\', '/')).resolve()))
                        for cads in controls for part in (cads.findtext('VariousControls/IncludePath', '') or '').split(';') if part.strip()}
                for entry in record.get('include_paths_by_target', {}).get(name, record.get('include_paths', [])):
                    path = (proj.dir / str(entry).replace('\\', '/')).resolve()
                    if os.path.normcase(str(path)) not in incs:
                        issue('INSTALLED_INCLUDE_LOST', component, '组件 Include 路径被移除。',
                              'Installed include path was removed.', [{'file': str(path)}], target=name)
                # Match whole comma/semicolon-delimited entries, retaining quoted values.
                definitions = ','.join(c.findtext('VariousControls/Define', '') or '' for c in controls)
                for macro in record.get('defines', []):
                    if not re.search(r'(?:^|[,;])\s*' + re.escape(macro) + r'\s*(?:$|[,;])', definitions):
                        issue('INSTALLED_DEFINE_LOST', component, '组件宏定义被移除或更改。',
                              'Installed define was removed or changed.', [{'file': str(proj.path), 'role': macro}], target=name)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, ToolError):
            issue('OWNERSHIP_RECORD_INVALID', component, '组件归属文件或记录无法完整核对。',
                  'Cannot fully verify component ownership files/records.', [{'file': str(state)}])

    # Detect active foreign cores even if IOC settings were changed but code wasn't regenerated.
    signatures = {
        'freertos.c': ('freertos', 'MX_FREERTOS_Init'), 'freertos_app.c': ('freertos', 'MX_FREERTOS_Init'),
        'tasks.c': ('freertos', 'vTaskStartScheduler'), 'ff.c': ('fatfs', 'f_mount'),
        'fatfs.c': ('fatfs', 'MX_FATFS_Init'), 'lwip.c': ('lwip', 'MX_LWIP_Init'),
        'init.c': ('lwip', 'lwip_init'), 'usbd_core.c': ('tinyusb', 'USBD_Init'),
        'usbh_core.c': ('tinyusb', 'USBH_Init'),
    }
    for path in active_sources(proj):
        spec = signatures.get(path.name.lower())
        if not spec or path in owned_paths: continue
        component, symbol = spec
        affected = ({component, 'rtthread'} if component == 'freertos' else {component}) & monitored
        if not affected: continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024: raise ValueError('too large')
            code = _c_code(reader(path))
            definition = re.search(r'\b' + symbol + r'\s*\([^;{}]*\)\s*\{', code)
            if definition:
                for key in affected:
                    issue('FOREIGN_MIDDLEWARE_OWNER', key,
                          '已有非本工具管理的组件入口/内核；停止重复移植。先明确 CubeMX、Pack 或手工代码的归属。',
                          'Existing foreign middleware entry/core; choose CubeMX, Pack or manual ownership before installing again.',
                          [{'file': str(path), 'line': code[:definition.start()].count('\n') + 1, 'role': symbol}])
        except (OSError, ValueError, ToolError):
            issue('MIDDLEWARE_SOURCE_UNREADABLE', component, '无法检查已有组件源码。',
                  'Cannot inspect existing middleware source.', [{'file': str(path)}])
    return issues
