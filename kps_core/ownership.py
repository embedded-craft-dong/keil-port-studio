"""Reversible source edit ownership, independent of Keil and Tk."""
import difflib
import re
from .errors import ToolError


def _spl_fragments(entry):
    header, call, poll, task = (entry[k] for k in ('header', 'call', 'poll', 'task'))
    if (not re.fullmatch(r'[\w./-]+\.h', header) or
            not re.fullmatch(r'[A-Za-z_]\w*\(\);', call) or
            (poll is not None and not re.fullmatch(r'[A-Za-z_]\w*\(\);', poll)) or
            (task is not None and not re.fullmatch(r'[A-Za-z_]\w*', task))):
        raise ToolError('Invalid SPL insertion ownership metadata')
    key = call.split('(')[0]
    return ['#include "%s"\n' % header,
            '  /* KPS SPL COMPONENT %s INIT */\n  %s\n' % (key, call)] + (
            ['\n    /* KPS SPL COMPONENT %s POLL */\n    %s\n' % (key, poll)] if poll else [])


def spl_insertion_hunks(before, after, header, call, poll=None, task=None):
    """Own inserted bytes, not the surrounding loop shared by other components.

    Exact reconstruction proves there are no hidden replacements. Reversal also
    verifies the entry's C control-flow placement, so unique text alone is never
    sufficient authority to remove a block moved elsewhere by the user.
    """
    before = before.replace('\r\n', '\n').replace('\r', '\n')
    after = after.replace('\r\n', '\n').replace('\r', '\n')
    entry = dict(header=header, call=call, poll=poll, task=task)
    spans = []
    for i, fragment in enumerate(_spl_fragments(entry)):
        if i == 0 and after.count(fragment) == before.count(fragment):
            continue  # A preexisting user include is not ours to remove.
        if fragment in before or after.count(fragment) != 1:
            raise ToolError('SPL insertion is not unique or was already present')
        start = after.index(fragment)
        spans.append((start, start + len(fragment), fragment))
    spans.sort()
    restored = after
    for start, end, _fragment in reversed(spans):
        restored = restored[:start] + restored[end:]
    if restored != before:
        raise ToolError('SPL insertion ownership does not reconstruct the original source')
    hunks = []
    removed = 0
    for start, end, fragment in spans:
        old_pos = start - removed
        hunks.append(dict(before='', after=fragment,
                          left=''.join(after[:start].splitlines(True)[-2:]),
                          right=''.join(after[end:].splitlines(True)[:2]),
                          before_left=''.join(before[:old_pos].splitlines(True)[-2:]),
                          before_right=''.join(before[old_pos:].splitlines(True)[:2]),
                          spl_entry=entry))
        removed += len(fragment)
    return hunks

def _edit_hunks(before, after):
    """Record exact source ownership, not guessed init names or MCU families."""
    old = before.replace('\r\n', '\n').replace('\r', '\n').splitlines(True)
    new = after.replace('\r\n', '\n').replace('\r', '\n').splitlines(True)
    hunks = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != 'equal':
            hunks.append({'before': ''.join(old[a:b]), 'after': ''.join(new[c:d]),
                          'before_left': ''.join(old[max(0, a - 2):a]),
                          'before_right': ''.join(old[b:b + 2]),
                          'left': ''.join(new[max(0, c - 2):c]),
                          'right': ''.join(new[d:d + 2])})
    return hunks


def _reverse_owned_hunks(current, hunks, location):
    current = current.replace('\r\n', '\n').replace('\r', '\n')
    replacements = []
    # Locate every edit against the same snapshot. Reversing an adjacent edit
    # first would invalidate the next edit's recorded context.
    for hunk in hunks:
        before, after = hunk['before'], hunk['after']
        if 'spl_entry' in hunk:
            from .project_layout import patch_spl_component
            entry = hunk['spl_entry']
            if before or after not in _spl_fragments(entry) or current.count(after) != 1:
                raise ToolError('SPL owned insertion changed or ambiguous: %s' % location)
            checked = patch_spl_component(current, entry['header'], entry['call'],
                                          entry['poll'], entry['task']).require_safe(location)
            if checked.changed or checked.status != 'already_correct':
                raise ToolError('SPL owned insertion is no longer in its verified entry: %s' % location)
            offset = current.index(after)
            replacements.append((offset, offset + len(after), ''))
            continue
        left, right = hunk['left'], hunk['right']
        offset = current.find(after) if after else -1
        context_matches = (offset >= 0 and (
            (left and current[:offset].endswith(left)) or
            (right and current[offset + len(after):].startswith(right)) or
            (not left and offset == 0) or (not right and offset + len(after) == len(current))))
        if after and current.count(after) == 1 and context_matches:
            replacements.append((offset, offset + len(after), before))
        elif (left or right) and current.count(left + after + right) == 1:
            offset = current.index(left + after + right) + len(left)
            replacements.append((offset, offset + len(after), before))
        else:
            # CubeMX reindents its own USER CODE boundary comments even when
            # KeepUserCode is enabled. Match that formatting change only, never
            # normalize actual C statements, strings or arbitrary comments.
            def marker_pattern(value):
                return ''.join('[ \\t]*' + re.escape(line.lstrip(' \t'))
                    if re.fullmatch(r'[ \t]*/\* USER CODE (?:BEGIN|END) [^\r\n]+ \*/\n?', line)
                    else re.escape(line) for line in value.splitlines(True))
            pattern = marker_pattern(left) + '(' + marker_pattern(after) + ')' + marker_pattern(right)
            matches = list(re.finditer(pattern, current)) if after and (left or right) else []
            if len(matches) != 1:
                raise ToolError('卸载停止：工具修改区域已改变或定位不唯一，保留全部文件: %s' % location)
            start, end = matches[0].span(1)
            replacements.append((start, end, before))
    replacements.sort()
    if any(a[1] > b[0] or a[0] == b[0] for a, b in zip(replacements, replacements[1:])):
        raise ToolError('卸载停止：源码补丁定位重叠，保留全部文件: %s' % location)
    for start, end, before in reversed(replacements):
        current = current[:start] + before + current[end:]
    return current
