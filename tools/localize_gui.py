"""Maintenance helper: emit an apply_patch diff, never rewrite source directly.

Only GUI string literals are routed through _tr. Docstrings remain untouched.
"""
import ast
import difflib
from pathlib import Path
import sys

path = Path(__file__).parents[1] / 'keil_port_tool.py'
source = path.read_text(encoding='utf-8-sig')
tree = ast.parse(source)
parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
lines = source.encode('utf-8').splitlines(keepends=True)
offsets = [0]
for line in lines: offsets.append(offsets[-1] + len(line))
edits = []
messages = set()
for cls in tree.body:
    if not isinstance(cls, ast.ClassDef) or cls.name not in ('KeilPortGUI', 'CheckTree'):
        continue
    for node in ast.walk(cls):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str): continue
        if not any('\u4e00' <= ch <= '\u9fff' for ch in node.value): continue
        parent = parents.get(node)
        if isinstance(parent, ast.Expr): continue  # class/method docstrings
        if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and parent.func.id == '_tr':
            continue
        messages.add(node.value)
        begin = offsets[node.lineno-1] + node.col_offset
        end = offsets[node.end_lineno-1] + node.end_col_offset
        edits.append((begin, end))
if '--list' in sys.argv:
    import json
    print(json.dumps(sorted(messages), ensure_ascii=False, indent=2))
else:
    updated = source.encode('utf-8')
    for begin, end in sorted(edits, reverse=True):
        updated = updated[:begin] + b'_tr(' + updated[begin:end] + b')' + updated[end:]
    diff = list(difflib.unified_diff(source.splitlines(True), updated.decode('utf-8').splitlines(True)))
    if diff:
        print('*** Begin Patch\n*** Update File: ' + path.as_posix())
        print(''.join(diff[2:]), end='')
        print('*** End Patch')
