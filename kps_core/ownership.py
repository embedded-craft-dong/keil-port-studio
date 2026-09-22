"""Reversible source edit ownership, independent of Keil and Tk."""
import difflib
from .errors import ToolError

def _edit_hunks(before, after):
    """Record exact source ownership, not guessed init names or MCU families."""
    old = before.replace('\r\n', '\n').replace('\r', '\n').splitlines(True)
    new = after.replace('\r\n', '\n').replace('\r', '\n').splitlines(True)
    hunks = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != 'equal':
            hunks.append({'before': ''.join(old[a:b]), 'after': ''.join(new[c:d]),
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
            raise ToolError('卸载停止：工具修改区域已改变或定位不唯一，保留全部文件: %s' % location)
    replacements.sort()
    if any(a[1] > b[0] or a[0] == b[0] for a, b in zip(replacements, replacements[1:])):
        raise ToolError('卸载停止：源码补丁定位重叠，保留全部文件: %s' % location)
    for start, end, before in reversed(replacements):
        current = current[:start] + before + current[end:]
    return current
