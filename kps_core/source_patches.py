"""Conservative C startup patches; pure text in, verified result out."""
import re
from .errors import ToolError

class PatchResult:
    """Explicit outcome; iterable preserves the legacy (text, changed) API."""
    def __init__(self, original, text=None, status='already_correct', reason=''):
        self.text = original if text is None else text
        self.changed = self.text != original
        self.status = status
        self.reason = reason

    def __iter__(self):
        return iter((self.text, self.changed))

    def require_safe(self, location):
        if self.status not in ('applied', 'already_correct', 'not_applicable'):
            raise ToolError('关键启动补丁未通过验证 [%s]: %s (%s)' % (self.status, location, self.reason))
        return self


def _c_code(text):
    """Mask comments/literals but retain offsets/newlines for conservative matching."""
    return re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  lambda m: re.sub(r'[^\r\n]', ' ', m.group()), text, flags=re.S)


def _c_function(text, name, return_type='void'):
    code = _c_code(text)
    matches = list(re.finditer(r'\b' + return_type + r'\s+' + re.escape(name) + r'\s*\(\s*void\s*\)\s*\{', code))
    if len(matches) != 1:
        return None
    start = matches[0].start()
    opening = matches[0].end() - 1
    depth = 1
    for i in range(opening + 1, len(code)):
        depth += (code[i] == '{') - (code[i] == '}')
        if depth == 0:
            return start, opening, i + 1
    return None


def patch_cmsis_wrapper_systick(text):
    """让 ARM v10.5.x cmsis_os2.c 支持由 STM32Cube 接管 SysTick。"""
    code = _c_code(text)
    guarded = list(re.finditer(r'#if\s+defined\s*\(\s*SysTick\s*\)\s*&&\s*!defined\s*\(\s*USE_CUSTOM_SYSTICK_HANDLER_IMPLEMENTATION\s*\)\s*\n\s*#undef\s+SysTick_Handler', code))
    pat = re.compile(r'#if\s+defined\s*\(\s*SysTick\s*\)\s*\r?\n\s*(#undef\s+SysTick_Handler)')
    matches = list(pat.finditer(code))
    span = _c_function(text, 'SysTick_Handler')
    if len(matches) + len(guarded) != 1 or not span:
        return PatchResult(text, status='unsupported', reason='CMSIS SysTick 条件结构未知，不能确认向量唯一归属')
    m = (guarded or matches)[0]
    depth, end = 0, None
    for directive in re.finditer(r'^\s*#\s*(if|ifdef|ifndef|endif|else|elif)\b', code[m.start():], re.M):
        name = directive.group(1)
        if name in ('if', 'ifdef', 'ifndef'):
            depth += 1
        elif name == 'endif':
            depth -= 1
            if depth == 0:
                end = m.start() + directive.start()
                break
        elif depth == 1:
            return PatchResult(text, status='unsupported', reason='CMSIS SysTick 顶层条件存在其他分支')
    if end is None or not (m.end() <= span[0] and span[2] <= end):
        return PatchResult(text, status='unsupported', reason='SysTick 条件保护未覆盖处理函数')
    if guarded:
        return PatchResult(text)
    replacement = ('#if defined(SysTick) && !defined(USE_CUSTOM_SYSTICK_HANDLER_IMPLEMENTATION)\n'
                   + m.group(1))
    return PatchResult(text, text[:m.start()] + replacement + text[m.end():], 'applied')


def patch_project_systick(text):
    """在 CubeMX USER CODE 区中加入 FreeRTOS tick，返回 (文本, 是否修改)。"""
    span = _c_function(text, 'SysTick_Handler')
    if not span:
        return PatchResult(text, status='unsupported', reason='SysTick_Handler 定义不唯一或格式未知')
    body = _c_code(text[span[1]:span[2]])
    calls = re.findall(r'\bxPortSysTickHandler\s*\(\s*\)\s*;', body)
    if calls:
        guard = r'if\s*\(\s*xTaskGetSchedulerState\s*\(\s*\)\s*!=\s*taskSCHEDULER_NOT_STARTED\s*\)\s*\{\s*xPortSysTickHandler\s*\(\s*\)\s*;\s*\}'
        if len(calls) == 1 and re.search(guard, body):
            return PatchResult(text)
        return PatchResult(text, status='conflict', reason='已有 FreeRTOS tick 调用，但不能确认启动前保护和单次调用')
    inc_end = '/* USER CODE END Includes */'
    irq_begin = '/* USER CODE BEGIN SysTick_IRQn 0 */'
    if text.count(inc_end) != 1 or text.count(irq_begin) != 1 or not (span[1] < text.index(irq_begin) < span[2]):
        return PatchResult(text, status='unsupported', reason='SysTick USER CODE 标记缺失或位于函数外')
    original = text
    includes = ('#include "FreeRTOS.h"\n#include "task.h"\n'
                'extern void xPortSysTickHandler(void);\n')
    text = text.replace(inc_end, includes + inc_end, 1)
    tick = ('\n  if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED)\n'
            '  {\n'
            '    xPortSysTickHandler();\n'
            '  }\n')
    text = text.replace(irq_begin, irq_begin + tick, 1)
    return PatchResult(original, text, 'applied')


def patch_project_rtos_exceptions(text):
    """让 FreeRTOS Cortex-M 汇编端口直接接管 SVC/PendSV 异常向量。

    CubeMX 会生成空的 SVC_Handler/PendSV_Handler。若保留这些强符号，
    port.c 中的汇编处理函数无法进入向量表，调度器会停在首次 svc 0。
    FreeRTOSConfig.h 通过宏把端口函数重命名为异常向量名；这里用条件编译
    禁用 CubeMX 空壳，同时保留文件结构和 USER CODE 区，便于再次生成代码。
    """
    original = text
    # New CubeMX installs keep ownership entirely inside a preserved USER CODE
    # block. Rename only this translation unit's empty generated stubs; port.c
    # still exports the real naked exception handlers under the vector names.
    # Existing guarded installs retain their old ownership until explicit recovery.
    include_end = '/* USER CODE END Includes */'
    marker = '/* KPS FREERTOS EXCEPTION ALIASES BEGIN */'
    aliases = (marker + '\n'
               '#define SVC_Handler KPS_CubeMX_Unused_SVC_Handler\n'
               '#define PendSV_Handler KPS_CubeMX_Unused_PendSV_Handler\n'
               '/* KPS FREERTOS EXCEPTION ALIASES END */\n')
    old_guards = '#if !defined(vPortSVCHandler)' in text or '#if !defined(xPortPendSVHandler)' in text
    if text.count(include_end) == 1 and not old_guards:
        for handler in ('SVC_Handler', 'PendSV_Handler'):
            span = _c_function(text, handler)
            if not span or _c_code(text[span[1] + 1:span[2] - 1]).strip(' \t\r\n;'):
                return PatchResult(original, status='conflict', reason=handler + ' 不存在、重复或含用户逻辑，拒绝接管')
        if marker in text:
            if aliases not in text.replace('\r\n', '\n'):
                return PatchResult(original, status='conflict', reason='FreeRTOS 异常别名接入块已修改')
            return PatchResult(original)
        if re.search(r'^\s*#\s*define\s+(SVC_Handler|PendSV_Handler)\b|KPS_CubeMX_Unused_', _c_code(text), re.M):
            return PatchResult(original, status='conflict', reason='已有异常重命名，拒绝重复接管')
        return PatchResult(original, text.replace(include_end, aliases + include_end, 1), 'applied')
    for handler, port_macro in (
            ('SVC_Handler', 'vPortSVCHandler'),
            ('PendSV_Handler', 'xPortPendSVHandler')):
        guard = '#if !defined(%s)' % port_macro
        span = _c_function(text, handler)
        if not span:
            if re.search(r'\b' + handler + r'\b', _c_code(text)):
                return PatchResult(original, status='unsupported', reason='无法唯一定位 ' + handler)
            continue
        start, opening, end = span
        if re.search(re.escape(guard) + r'\s*$', _c_code(text[:start])) and re.match(r'\s*#endif\b', _c_code(text[end:])):
            continue
        if _c_code(text[opening + 1:end - 1]).strip(' \t\r\n;'):
            return PatchResult(original, status='conflict', reason=handler + ' 含用户逻辑，拒绝接管')
        block = text[start:end]
        wrapped = ('%s\n%s\n#endif /* %s */' %
                   (guard, block, port_macro))
        text = text[:start] + wrapped + text[end:]
    return PatchResult(original, text, 'applied' if text != original else 'already_correct')



def patch_main_start_scheduler(text, use_os2=True):
    """在 CubeMX main.c 的 USER CODE 区初始化任务并启动调度器。"""
    original = text
    span = _c_function(text, 'main', 'int')
    if not span:
        return PatchResult(text, status='unsupported', reason='无法唯一定位 int main(void)')
    body = _c_code(text[span[1] + 1:span[2] - 1])
    if re.search(r'\b(osKernelStart|vTaskStartScheduler)\s*\(', body):
        required = ('osKernelInitialize', 'MX_FREERTOS_Init', 'osKernelStart') if use_os2 else ('MX_FREERTOS_Init', 'vTaskStartScheduler')
        positions = [re.search(r'\b' + name + r'\s*\(', body) for name in required]
        if not all(positions) or [p.start() for p in positions] != sorted(p.start() for p in positions):
            return PatchResult(text, status='conflict', reason='已有调度器启动，但无法确认初始化顺序；请手动核对')
        # Conditional/custom startup is not proven by lexical order alone.
        prefix = body[:positions[-1].start()]
        if re.search(r'\b(if|for|while|switch|return|goto)\b|^\s*#', prefix, re.M):
            return PatchResult(text, status='unsupported', reason='启动前存在条件控制流，需要手动验证')
        return PatchResult(text)
    changed = False
    old_header = '#include "freertos.h"'
    new_header = '#include "freertos_app.h"'
    if old_header in text:
        text = text.replace(old_header, new_header, 1)
        changed = True
    inc_end = '/* USER CODE END Includes */'
    init_end = '/* USER CODE END 2 */'
    if text.count(inc_end) != 1 or text.count(init_end) != 1 or not (span[1] < text.index(init_end) < span[2]):
        return PatchResult(original, status='unsupported', reason='main/Includes USER CODE 标记缺失或不唯一')
    insertion_span = _c_function(text, 'main', 'int')
    prefix = _c_code(text[insertion_span[1] + 1:text.index(init_end)])
    if re.search(r'\b(if|for|while|switch|return|goto)\b|^\s*#', prefix, re.M):
        return PatchResult(original, status='unsupported', reason='启动插入点之前存在自定义控制流，需要手动适配')
    header = new_header + '\n'
    if new_header not in text:
        text = text.replace(inc_end, header + inc_end, 1)
        changed = True
    if use_os2:
        start = ('\n  /* Create the RTOS objects and start scheduling. */\n'
                 '  osKernelInitialize();\n'
                 '  MX_FREERTOS_Init();\n'
                 '  osKernelStart();\n')
    else:
        start = ('\n  /* Create the RTOS objects and start scheduling. */\n'
                 '  MX_FREERTOS_Init();\n'
                 '  vTaskStartScheduler();\n')
    text, inserted = re.subn(r'(?m)^[ \t]*' + re.escape(init_end),
                            lambda match: start + match.group(0), text, count=1)
    if inserted != 1:
        return PatchResult(original, status='unsupported', reason='初始化 USER CODE END 2 标记必须独占一行')
    return PatchResult(original, text, 'applied')



def patch_rtthread_irq(text):
    # Fail closed for non-Cube handlers: never erase custom exception handling.
    marker = '/* USER CODE END SysTick_IRQn 0 */'
    include = '/* USER CODE END Includes */'
    span = _c_function(text, 'SysTick_Handler')
    if not span or text.count(marker) != 1 or text.count(include) != 1 or not (span[1] < text.index(marker) < span[2]):
        raise ToolError('RT-Thread 需要 CubeMX SysTick/Includes USER CODE 标记；请手动适配中断后重试')
    if not re.search(r'^\s*#include\s+"rtthread_app.h"', text, re.M):
        text = text.replace(include, '#include "rtthread_app.h"\n' + include, 1)
    span = _c_function(text, 'SysTick_Handler')
    if not re.search(r'\bKPS_RTTHREAD_Tick\s*\(\s*\)\s*;', _c_code(text[span[1]:span[2]])):
        text = text.replace(marker, '  KPS_RTTHREAD_Tick();\n  ' + marker, 1)
    for name in ('PendSV_Handler', 'HardFault_Handler'):
        span = _c_function(text, name)
        if not span:
            raise ToolError('无法安全定位 %s；RT-Thread 移植层拥有此向量' % name)
        start, opening, end = span
        guard = '#if !defined(KPS_USING_RTTHREAD)'
        if re.search(re.escape(guard) + r'\s*$', _c_code(text[:start])) and re.match(r'\s*#endif\b', _c_code(text[end:])):
            continue
        body = _c_code(text[opening + 1:end - 1])
        body = re.sub(r'while\s*\(\s*1\s*\)', '', body)
        if re.sub(r'[\s{};]', '', body):
            raise ToolError('%s 含用户逻辑，不能自动替换为 RT-Thread 向量' % name)
        replacement = guard + '\n' + text[start:end] + '\n#endif'
        text = text[:start] + replacement + text[end:]
    return text
