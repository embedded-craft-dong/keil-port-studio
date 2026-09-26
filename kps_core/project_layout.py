"""Selected-target source discovery and conservative STM32 SPL integration.

No HAL dependency, downloads or writes. This is deliberately not a C compiler:
ambiguous entry points, conditional entry definitions and custom ISR bodies fail
closed. New vendors can add adapters without teaching CubeMX marker patches
about every project layout.
"""
import os
from pathlib import Path
import re

from .errors import ToolError
from .source_patches import PatchResult, _c_code, _c_function


def active_sources(proj):
    """Only files enabled in selected Targets; never scan backups or siblings."""
    seen = set()
    for target in proj.targets:
        for group in target.findall('Groups/Group'):
            if group.findtext('GroupOption/CommonProperty/IncludeInBuild') == '0':
                continue
            for node in group.findall('Files/File'):
                if node.findtext('FileOption/CommonProperty/IncludeInBuild') == '0':
                    continue
                raw = (node.findtext('FilePath') or '').replace('\\', os.sep)
                path = Path(raw)
                if path.suffix.lower() not in ('.c', '.cpp', '.cc', '.cxx'):
                    continue
                path = (path if path.is_absolute() else proj.dir / path).resolve()
                key = os.path.normcase(str(path))
                if key not in seen:
                    seen.add(key)
                    yield path


def project_profile(proj):
    profiles = set()
    for target in proj.targets:
        macros = ' '.join(node.findtext('VariousControls/Define', '') or ''
                          for node in proj._target_cads(target))
        spl = bool(re.search(r'\bUSE_STDPERIPH_DRIVER\b', macros))
        hal = bool(re.search(r'\bUSE_HAL_DRIVER\b', macros))
        dev = target.findtext('TargetOption/TargetCommonOption/Device', '') or ''
        if spl and hal:
            raise ToolError('HAL/SPL 宏同时启用 / mixed HAL and SPL: ' + dev)
        profiles.add('stm32-spl' if spl and dev.upper().startswith('STM32') else 'legacy')
    if len(profiles) > 1:
        raise ToolError('所选 Target 的框架不同，请分别移植 / select one framework at a time')
    return next(iter(profiles), 'legacy')


def _kernel_source(proj, path):
    # Only known private kernel roots, never a global filename exclusion.
    return any((proj.dir / 'Middlewares/Third_Party' / name).resolve() in path.parents
               for name in ('FreeRTOS', 'RTThread'))


def discover_entry(proj, reader, name='main', return_type='int'):
    candidates = []
    for path in active_sources(proj):
        if _kernel_source(proj, path):
            continue
        if not path.is_file():
            raise ToolError('入口检查源文件缺失 / missing source: ' + str(path))
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ToolError('源文件过大，不能完整检查 / source too large: ' + str(path))
        text = reader(path)
        if re.search(r'\b' + re.escape(name) + r'\s*\(', _c_code(text)):
            span = _c_function(text, name, return_type)
            definitions = list(re.finditer(r'\b' + re.escape(name) +
                                           r'\s*\([^;{}]*\)\s*\{', _c_code(text)))
            if definitions and (len(definitions) != 1 or not span):
                raise ToolError('入口签名或数量不受支持 / unsupported entry definition: ' + str(path))
            if span:
                # Reject conditional function definitions (not header includes).
                level = 0
                for match in re.finditer(r'^\s*#\s*(if|ifdef|ifndef|endif)\b', _c_code(text[:span[0]]), re.M):
                    level += -1 if match[1] == 'endif' else 1
                if level:
                    raise ToolError('入口位于条件编译中 / conditional entry: ' + str(path))
                candidates.append((path, text))
    if len(candidates) != 1:
        raise ToolError('需要唯一的 %s 定义，找到 %d 个 / ambiguous or missing entry' % (name, len(candidates)))
    return candidates[0]


def validate_spl_rtos(proj, reader, ignore_sources=()):
    """Catch visible SysTick owners before planning anything. Not full C analysis."""
    ignored = {Path(p).resolve() for p in ignore_sources if p}
    for path in active_sources(proj):
        if path in ignored:
            continue
        if not path.is_file():
            raise ToolError('标准库源文件缺失 / missing SPL source: ' + str(path))
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ToolError('源文件过大 / source too large: ' + str(path))
        code = _c_code(reader(path))
        # misc.c defines these SPL primitives; their existence isn't a call.
        # Inspect every call site, but do not report the library implementation
        # itself as an application claiming the timer or selecting a grouping.
        if path.name.lower() == 'misc.c' and any(
                re.fullmatch(r'STM32\w+_StdPeriph_Driver', part, re.I) for part in path.parts):
            for match in reversed(list(re.finditer(
                    r'\bvoid\s+(?:SysTick_CLKSourceConfig|NVIC_PriorityGroupConfig)\s*\([^()]*\)\s*\{', code))):
                end, depth = match.end(), 1
                while end < len(code) and depth:
                    depth += (code[end] == '{') - (code[end] == '}')
                    end += 1
                if depth:
                    raise ToolError('SPL misc.c function is malformed: ' + str(path))
                code = code[:match.start()] + re.sub(r'[^\n]', ' ', code[match.start():end]) + code[end:]
        # Kernel/OS_Tick sources added on repeat runs legitimately own SysTick.
        # Only exclude the tool's own copied middleware, not arbitrary file names.
        if _kernel_source(proj, path):
            continue
        # The generated RT-Thread port configures SysTick once. Do not exempt
        # user task bodies in that same file from the timebase-conflict check.
        if path == (proj.dir / 'RTThread/App/rtthread_app.c').resolve():
            span = _c_function(code, 'MX_RTTHREAD_Init')
            calls = list(re.finditer(r'\bSysTick_Config\s*\(SystemCoreClock / RT_TICK_PER_SECOND\)', code))
            if span and len(calls) == 1 and span[1] < calls[0].start() < span[2]:
                call = calls[0]
                code = code[:call.start()] + ' ' * (call.end() - call.start()) + code[call.end():]
        hazard = re.search(r'\bSysTick\s*->\s*(?:CTRL|LOAD|VAL)\s*(?:[|&^+\-]?=|\+\+|--)|'
                           r'\b(?:SysTick_Config|SysTick_CLKSourceConfig)\s*\(', code)
        if hazard:
            raise ToolError('标准库时基冲突 / SysTick is already controlled by %s:%d. '
                            'RTOS owns SysTick; migrate legacy delay to DWT/TIM first. '
                            '请先迁移延时函数，参见 docs/SPL.zh-CN.md；未改写原文件。' %
                            (path, code[:hazard.start()].count('\n') + 1))
        for call in re.finditer(r'\bNVIC_PriorityGroupConfig\s*\(\s*([^)]*)\)', code):
            if call[1].strip() != 'NVIC_PriorityGroup_4':
                raise ToolError('RTOS 标准库适配要求 NVIC_PriorityGroup_4 / check IRQ priorities: ' + str(path))
        for call in re.finditer(r'\bNVIC_SetPriorityGrouping\s*\(\s*([^)]*)\)', code):
            if not re.fullmatch(r'[0-3][uU]?', call[1].strip()):
                raise ToolError('NVIC grouping cannot be proven RTOS-safe: ' + str(path))


def validate_spl_cmsis(proj, reader, purpose='CMSIS-V2'):
    """Do not silently mix SPL-era core intrinsics with CMSIS-5 compiler headers."""
    found = False
    for folder in proj.include_dirs_abs():
        for name in ('core_cm3.h', 'core_cm4.h'):
            path = Path(folder) / name
            if not path.is_file():
                continue
            found = True
            text = reader(path)
            if not re.search(r'#\s*include\s+[<"]cmsis_compiler\.h[>"]', text):
                raise ToolError('CMSIS Core 过旧或混用 / legacy CMSIS Core: %s. '
                                'Use a coherent CMSIS_5 Core/Include set (and RTOS2 for CMSIS-V2), '
                                'not individual compiler headers; see docs/SPL.en.md.' % path)
    if not found:
        raise ToolError('SPL %s: core_cm3.h/core_cm4.h not found in Include paths' % purpose)


def patch_spl_main(text, use_os2=True, rtos='freertos'):
    """Insert before one top-level forever loop; do NOT silently move user work."""
    if '\r' in text:
        result = patch_spl_main(text.replace('\r\n', '\n').replace('\r', '\n'), use_os2, rtos)
        return PatchResult(text, result.text if result.changed else text, result.status, result.reason)
    if rtos not in ('freertos', 'rtthread'):
        raise ValueError('Unsupported SPL RTOS: ' + rtos)
    span = _c_function(text, 'main', 'int')
    if not span:
        return PatchResult(text, status='unsupported', reason='需要唯一 int main(void) / unique main required')
    code = _c_code(text)
    body = code[span[1] + 1:span[2] - 1]
    calls = ('SystemCoreClockUpdate();\n  osKernelInitialize();\n  MX_FREERTOS_Init();\n  osKernelStart();'
             if use_os2 else 'SystemCoreClockUpdate();\n  MX_FREERTOS_Init();\n  vTaskStartScheduler();')
    if rtos == 'rtthread':
        calls = 'SystemCoreClockUpdate();\n  MX_RTTHREAD_Init();'
    header = '#include "%s_app.h"' % rtos
    marker = '/* KPS SPL %s START */' % rtos.upper()
    startup = r'\b(?:osKernelStart|vTaskStartScheduler|MX_RTTHREAD_Init|rtthread_startup|rt_system_scheduler_start)\s*\('
    block = (marker + '\n  ' + calls + '\n'
             '  /* Scheduler must not return; inspect heap/assert state if it does. */\n'
             '  for (;;) { }\n  /* KPS SPL %s END */' % rtos.upper())
    if marker in text:
        if text.count(block) == 1 and text.count(header) == 1:
            before, after = text.split(block)
            prefix = _c_code(text[span[1] + 1:len(before)])
            if (span[1] < len(before) < len(before) + len(block) < span[2] and
                    text.index(header) < span[0] and
                    not re.search(r'\b(if|for|while|switch|return|goto|do)\b|[{}]|^\s*#', prefix, re.M) and
                    not re.search(startup, _c_code(before + after))):
                return PatchResult(text)
        return PatchResult(text, status='conflict', reason='已生成启动区域被修改 / startup block changed')
    if re.search(startup, body) or re.search(r'\b(?:MX_FREERTOS_Init|osKernelInitialize)\s*\(', body):
        return PatchResult(text, status='conflict', reason='已有 RTOS 启动代码 / existing RTOS startup')
    loops = list(re.finditer(r'\b(?:while\s*\(\s*1\s*\)|for\s*\(\s*;\s*;\s*\))\s*\{', body))
    if len(loops) != 1:
        return PatchResult(text, status='unsupported', reason='需要唯一顶层 while(1)/for(;;) / unique forever loop required')
    prefix = body[:loops[0].start()]
    if re.search(r'\b(if|for|while|switch|return|goto|do)\b|[{}]|^\s*#', prefix, re.M):
        return PatchResult(text, status='unsupported', reason='初始化存在控制流 / non-linear initialization')
    pos = span[1] + 1 + loops[0].start()
    patched = text[:pos] + block + '\n  ' + text[pos:]
    # Insert directly before main, leaving vendor copyright/includes untouched.
    patched = patched[:span[0]] + header + '\n' + patched[span[0]:]
    return PatchResult(text, patched, 'applied')


def patch_spl_rtthread_irq(text):
    """RT-Thread owns PendSV/HardFault and the sole SPL SysTick timebase."""
    if '\r' in text:
        result = patch_spl_rtthread_irq(text.replace('\r\n', '\n').replace('\r', '\n'))
        return PatchResult(text, result.text if result.changed else text, result.status, result.reason)
    original = text
    span = _c_function(text, 'SysTick_Handler')
    tick = '\n  /* KPS SPL RTTHREAD TICK */\n  KPS_RTTHREAD_Tick();\n'
    header = '#include "rtthread_app.h"\n'
    if not span:
        return PatchResult(original, status='unsupported', reason='unique SysTick_Handler required')
    body = text[span[1] + 1:span[2] - 1]
    if body != tick:
        if _c_code(body).strip(' \t\n;'):
            return PatchResult(original, status='conflict', reason='SysTick 含用户逻辑 / nonempty SysTick')
        text = text[:span[1] + 1] + tick + text[span[2] - 1:]
    first = re.search(r'\bvoid\s+\w+\s*\(\s*void\s*\)\s*\{', _c_code(text))
    if header not in text:
        text = text[:first.start()] + header + text[first.start():]
    elif text.count(header) != 1 or text.index(header) > first.start():
        return PatchResult(original, status='conflict', reason='RT-Thread header placement is ambiguous')
    for name in ('PendSV_Handler', 'HardFault_Handler'):
        span = _c_function(text, name)
        if not span:
            return PatchResult(original, status='unsupported', reason='unique ' + name + ' required')
        start, opening, end = span
        body = _c_code(text[opening + 1:end - 1]).strip()
        allowed = not body.strip('; \n\t') or (name == 'HardFault_Handler' and
            re.fullmatch(r'while\s*\(\s*1\s*\)\s*\{\s*\}', body))
        if not allowed:
            return PatchResult(original, status='conflict', reason=name + ' 含用户逻辑 / custom exception')
        guard = '#if !defined(KPS_USING_RTTHREAD)'
        if re.search(re.escape(guard) + r'\s*$', _c_code(text[:start])) and re.match(r'\s*#endif\b', _c_code(text[end:])):
            continue
        # Do not wrap unknown preprocessor ownership in another guard.
        level = 0
        for directive in re.finditer(r'^\s*#\s*(if|ifdef|ifndef|endif)\b', _c_code(text[:start]), re.M):
            level += -1 if directive[1] == 'endif' else 1
        if level:
            return PatchResult(original, status='conflict', reason=name + ' has conditional ownership')
        text = text[:start] + guard + '\n' + text[start:end] + '\n#endif /* KPS_USING_RTTHREAD */' + text[end:]
    return PatchResult(original, text, 'applied' if text != original else 'already_correct')


def patch_spl_tick(text):
    if '\r' in text:
        result = patch_spl_tick(text.replace('\r\n', '\n').replace('\r', '\n'))
        return PatchResult(text, result.text if result.changed else text, result.status, result.reason)
    span = _c_function(text, 'SysTick_Handler')
    if not span:
        return PatchResult(text, status='unsupported', reason='需要唯一 SysTick_Handler / unique SysTick required')
    body = _c_code(text[span[1] + 1:span[2] - 1]).strip()
    tick = ('\n  /* KPS SPL RTOS TICK */\n'
            '  if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED)\n'
            '  {\n    xPortSysTickHandler();\n  }\n')
    includes = ('#include "FreeRTOS.h"\n#include "task.h"\n'
                'extern void xPortSysTickHandler(void);\n')
    if text[span[1] + 1:span[2] - 1] == tick and includes in text:
        return PatchResult(text)
    if body.strip('; \r\n\t'):
        return PatchResult(text, status='conflict', reason='SysTick 含用户逻辑，拒绝覆盖 / nonempty SysTick')
    patched = text[:span[1] + 1] + tick + text[span[2] - 1:]
    # Before any exception function: SVC/PendSV guards need port alias macros.
    first = re.search(r'\bvoid\s+\w+\s*\(\s*void\s*\)\s*\{', _c_code(patched))
    if not first:
        return PatchResult(text, status='unsupported', reason='IRQ function layout unknown')
    patched = patched[:first.start()] + includes + patched[first.start():]
    return PatchResult(text, patched, 'applied')


def patch_spl_component(text, header, call, loop_call=None, task=None):
    """Attach to a proven main/default-task loop, never to an unreachable main.

    Deliberately fail closed for custom control flow. Exact owned markers allow
    repeat planning without mistaking a commented or conditional call for init.
    The transaction manifest records the actual file/hunks for removal.
    """
    original = text
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    key = call.split('(')[0].strip()
    init = '  /* KPS SPL COMPONENT %s INIT */\n  %s\n' % (key, call)
    poll = ('\n    /* KPS SPL COMPONENT %s POLL */\n    %s\n' % (key, loop_call)
            if loop_call else '')
    marker = '/* KPS SPL COMPONENT %s ' % key
    owned = marker in text
    owned_pos = text.find(init) if owned else None
    owned_poll_pos = text.find(poll) if owned and poll else None
    if owned:
        if text.count(init) != 1 or (poll and text.count(poll) != 1):
            return PatchResult(original, status='conflict', reason='component entry block changed')
        text = text.replace(init, '', 1)
        if poll:
            if owned_poll_pos > owned_pos:
                owned_poll_pos -= len(init)
            text = text.replace(poll, '', 1)
        if marker in text:
            return PatchResult(original, status='conflict', reason='unexpected component marker')
    code = _c_code(text)
    if re.search(r'\b' + re.escape(key) + r'\s*\(', code) or (loop_call and
            re.search(r'\b' + re.escape(loop_call.split('(')[0]) + r'\s*\(', code)):
        return PatchResult(original, status='conflict', reason='existing unowned component call; integrate manually')
    if task:
        matches = list(re.finditer(r'\b(?:static\s+)?void\s+' + re.escape(task) +
                                   r'\s*\(\s*void\s*\*\s*\w+\s*\)\s*\{', code))
        if len(matches) != 1:
            return PatchResult(original, status='unsupported', reason='unique default task required')
        start, opening = matches[0].start(), matches[0].end() - 1
        depth, end = 1, opening + 1
        while end < len(code) and depth:
            depth += (code[end] == '{') - (code[end] == '}')
            end += 1
        span = (start, opening, end) if depth == 0 else None
    else:
        span = _c_function(text, 'main', 'int')
    if not span:
        return PatchResult(original, status='unsupported', reason='unique entry definition required')
    level = 0
    for directive in re.finditer(r'^\s*#\s*(if|ifdef|ifndef|endif)\b', code[:span[0]], re.M):
        level += -1 if directive[1] == 'endif' else 1
    body = code[span[1] + 1:span[2] - 1]
    if level or re.search(r'^\s*#|\b(return|goto|break|continue)\b', body, re.M):
        return PatchResult(original, status='unsupported', reason='conditional or branching entry; integrate manually')
    if not task and re.search(r'\b(osKernelStart|vTaskStartScheduler|MX_RTTHREAD_Init)\s*\(', body):
        return PatchResult(original, status='conflict', reason='scheduler owns main; attach to an RTOS task')
    loops = list(re.finditer(r'\b(?:while\s*\(\s*1\s*\)|for\s*\(\s*;\s*;\s*\))\s*\{', body))
    if len(loops) != 1:
        return PatchResult(original, status='unsupported', reason='unique forever loop required')
    prefix = body[:loops[0].start()]
    # The generated native task contains a finite delay-tick adjustment if.
    # It must still reach this one top-level loop; never accept a nested loop.
    if prefix.count('{') != prefix.count('}') or re.search(r'\b(for|while|switch|do)\b', prefix) or (
            not task and re.search(r'\bif\b|[{}]', prefix)):
        return PatchResult(original, status='unsupported', reason='non-linear initialization')
    if task and re.search(r'\bif\b[^;{}]*$', prefix):
        return PatchResult(original, status='unsupported', reason='conditional loop')
    pos = span[1] + 1 + loops[0].start()
    line_start = text.rfind('\n', 0, pos) + 1
    if not text[line_start:pos].strip():
        pos = line_start  # Preserve the existing loop line, including indentation.
    if owned:
        if not span[1] < owned_pos <= pos:
            return PatchResult(original, status='conflict', reason='init moved outside the entry prefix')
        before_init = code[span[1] + 1:owned_pos]
        if before_init.count('{') != before_init.count('}') or re.search(r'\bif\b[^;{}]*$', before_init):
            return PatchResult(original, status='conflict', reason='init moved into conditional control flow')
        # User hardware/mount work may follow our intact init. Do not reorder it.
        pos = owned_pos
    loop_open = span[1] + 1 + loops[0].end()
    poll_pos = loop_open
    if owned and poll:
        # A later component may have inserted its own poll before ours. Preserve
        # the existing straight-line order rather than moving ours to the front.
        # Never accept a poll moved into a conditional/nested scope or outside
        # the loop. Only comments/whitespace and simple no-argument calls may
        # precede it; anything more complex still needs manual integration.
        if not loop_open <= owned_poll_pos < span[2] - 1 or not re.fullmatch(
                r'(?:\s*[A-Za-z_]\w*\s*\(\s*\)\s*;)*\s*',
                code[loop_open:owned_poll_pos]):
            return PatchResult(original, status='conflict', reason='poll moved into unsupported control flow')
        poll_pos = owned_poll_pos
    patched = text[:poll_pos] + poll + text[poll_pos:]
    patched = patched[:pos] + init + patched[pos:]
    include = '#include "%s"' % header
    includes = list(re.finditer(r'^\s*#\s*include\s*[<"]' + re.escape(header) + r'[>"]', text, re.M))
    if includes:
        if len(includes) != 1 or includes[0].start() > span[0]:
            return PatchResult(original, status='conflict', reason='ambiguous component header')
        level = 0
        for directive in re.finditer(r'^\s*#\s*(if|ifdef|ifndef|endif)\b', code[:includes[0].start()], re.M):
            level += -1 if directive[1] == 'endif' else 1
        if level:
            return PatchResult(original, status='conflict', reason='conditional component header')
    else:
        patched = patched[:span[0]] + include + '\n' + patched[span[0]:]
    if owned and patched != original.replace('\r\n', '\n').replace('\r', '\n'):
        return PatchResult(original, status='conflict', reason='component entry placement changed')
    return PatchResult(original, original if owned else patched,
                       'already_correct' if owned else 'applied')
