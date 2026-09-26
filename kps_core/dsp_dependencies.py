"""Conservative source-level checks for DSP aggregate-module dependencies.

Not a C preprocessor/linker: include all conditional branches. This intentionally
asks for the complete referenced module when a custom subset cannot be proven.
Never silently override a user's source deselection.
"""
import re
from pathlib import Path
from .errors import ToolError
from .source_patches import _c_code


def missing_modules(root, available, selected, reader):
    root = Path(root).resolve()
    available = [Path(p).resolve() for p in available]
    selected = {Path(p).resolve() for p in selected}
    if selected == set(available):
        return []
    definitions, references = {}, {}
    owners = {}
    for module in available:
        seen, chunks, pending = set(), [], [module]
        while pending:
            path = pending.pop().resolve()
            if path in seen:
                continue
            if root not in path.parents or not path.is_file():
                raise ToolError('CMSIS-DSP aggregate include missing/outside SDK: ' + str(path))
            if len(seen) >= 2048 or path.stat().st_size > 16 * 1024 * 1024:
                raise ToolError('CMSIS-DSP dependency scan limit: ' + str(path))
            seen.add(path)
            text = reader(path)
            code = _c_code(text)
            chunks.append(code)
            for match in re.finditer(r'^\s*#\s*include\s+"([^"\r\n]+\.c)"', text, re.M):
                if '#' in code[match.start():match.end()]:
                    pending.append(path.parent / match[1])
        code = '\n'.join(chunks)
        names = set(re.findall(r'\b(arm_\w+)\s*\([^;{}]*\)\s*\{', code))
        # Only file-scope tables/instances, never local constants such as `n`.
        for match in re.finditer(r'\bconst\s+\w+\s+(\w+)\s*(?:\[[^;{}]*\])?\s*=', code):
            prefix = code[:match.start()]
            if prefix.count('{') == prefix.count('}'):
                names.add(match[1])
        definitions[module] = names
        references[module] = set(re.findall(r'\b[A-Za-z_]\w*\b', code))
        for name in names:
            owners.setdefault(name, set()).add(module)
    needed, pending = set(selected), list(selected)
    while pending:
        module = pending.pop()
        for name in references[module] - definitions[module]:
            providers = owners.get(name, set())
            if providers and not providers.intersection(needed):
                # Multiple alternative providers are ambiguous; requiring all
                # would risk duplicate symbols. Ask for manual configuration.
                if len(providers) != 1:
                    raise ToolError('CMSIS-DSP ambiguous symbol provider: ' + name)
                provider = next(iter(providers))
                needed.add(provider)
                pending.append(provider)
    return sorted(needed - selected)
