"""Compile actual guard templates; verify millisecond conversion via API doubles."""
import importlib.util
from pathlib import Path
import shutil
import re
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('guard_tool', Path(__file__).parents[1] / 'keil_port_tool.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class GuardTimeoutTests(unittest.TestCase):
    def test_generated_poll_tasks_always_block(self):
        gcc = shutil.which('gcc') or ('C:/MinGW/bin/gcc.exe' if Path('C:/MinGW/bin/gcc.exe').exists() else None)
        if not gcc:
            self.skipTest('host gcc required')
        sources = [(m.freertos_app_templates(False)[1], 'StartDefaultTask'),
                   (m.lwip_port_templates(True, [], False)[5], 'LwIP_InputTask')]
        with tempfile.TemporaryDirectory(prefix='kps-poll-') as tmp:
            root = Path(tmp)
            for source, name in sources:
                function = re.search(r'static void ' + name + r'\(void \*argument\)\s*\{.*?\n\}', source, re.S)
                self.assertIsNotNone(function)
                for rate in (100, 128, 1000, 2000):
                    with self.subTest(task=name, rate=rate):
                        harness = '''#include <stdint.h>
#include <setjmp.h>
typedef uint32_t TickType_t;
static jmp_buf done;
static TickType_t seen;
static void LwIP_Poll(void) {}
static void vTaskDelay(TickType_t ticks) { seen=ticks;longjmp(done,1); }
'''
                        harness += '#define pdMS_TO_TICKS(x) ((x)*%dU/1000U)\n' % rate
                        harness += function[0]
                        harness += '\nint main(void) { if(!setjmp(done)) %s(0); return seen==%dU?0:1; }' % (name, max(1, rate // 1000))
                        file = root / 'poll.c'; exe = root / 'poll.exe'
                        file.write_text(harness, encoding='utf-8')
                        compiled = subprocess.run([gcc, '-std=c99', '-Wall', '-Werror', '-Wno-unused-function', str(file), '-o', str(exe)], capture_output=True, text=True)
                        self.assertEqual(compiled.returncode, 0, compiled.stderr)
                        self.assertEqual(subprocess.run([str(exe)], timeout=5).returncode, 0)

    def test_generated_guards_at_multiple_tick_rates(self):
        gcc = shutil.which('gcc') or ('C:/MinGW/bin/gcc.exe' if Path('C:/MinGW/bin/gcc.exe').exists() else None)
        if not gcc:
            self.skipTest('host gcc required')
        common = '#include <stdint.h>\n#include <stddef.h>\nstatic uint32_t seen, calls;\n'
        stubs = {
            'cmsis': '''
typedef void *osMutexId_t;
#define osOK 0
#define osWaitForever UINT32_MAX
static osMutexId_t osMutexNew(void *p) { (void)p; return (void *)1; }
static uint32_t osKernelGetTickFreq(void) { return RATE; }
static int osMutexAcquire(osMutexId_t p,uint32_t t) { (void)p; seen=t; ++calls; return 0; }
static int osMutexRelease(osMutexId_t p) { (void)p; return 0; }
''',
            'native': '''
typedef TICK_TYPE TickType_t;
typedef void *SemaphoreHandle_t;
#define configTICK_RATE_HZ RATE
#define portMAX_DELAY ((TickType_t)~(TickType_t)0)
#define pdMS_TO_TICKS(x) ((TickType_t)(x) * (TickType_t)RATE / 1000U)
#define pdTRUE 1
static SemaphoreHandle_t xSemaphoreCreateMutex(void) { return (void *)1; }
static int xSemaphoreTake(SemaphoreHandle_t p,TickType_t t) { (void)p; seen=t; ++calls; return 1; }
static int xSemaphoreGive(SemaphoreHandle_t p) { (void)p; return 1; }
''',
            'rtthread': '''
typedef void *rt_mutex_t;
typedef int32_t rt_int32_t;
typedef uint64_t rt_uint64_t;
#define RT_TICK_PER_SECOND RATE
#define RT_WAITING_FOREVER (-1)
#define RT_IPC_FLAG_PRIO 1
#define RT_EOK 0
static rt_mutex_t rt_mutex_create(const char *n,int flag) { (void)n;(void)flag;return (void *)1; }
static int rt_mutex_take(rt_mutex_t p,rt_int32_t t) { (void)p;seen=(uint32_t)t;++calls;return 0; }
static int rt_mutex_release(rt_mutex_t p) { (void)p;return 0; }
'''}
        with tempfile.TemporaryDirectory(prefix='kps-guard-') as tmp:
            root = Path(tmp)
            for name in ('cmsis_os2.h', 'FreeRTOS.h', 'semphr.h', 'rtthread.h'):
                (root / name).write_text('', encoding='utf-8')
            for backend in stubs:
                widths = (16, 32) if backend == 'native' else (32,)
                for width in widths:
                    for rate in (100, 128, 1000, 2000):
                        with self.subTest(backend=backend, width=width, rate=rate):
                            header, source = m.rtos_guard_templates(['UART'], backend == 'cmsis', backend == 'rtthread')
                            (root / 'rtos_peripheral_guard.h').write_text(header, encoding='utf-8')
                            forever = (1 << width) - 1
                            cap = 0x7fffffff if backend == 'rtthread' else forever - 1
                            values = [0, 1, 5, 100, 999, 1000, 65535, 1000000, 0xfffffffe, 0xffffffff]
                            cases = []
                            for i, ms in enumerate(values):
                                expected = forever if ms == 0xffffffff else min(cap, (ms * rate + 999) // 1000)
                                cases.append('if(RTOS_PeripheralGuard_Lock(RTOS_GUARD_UART,%uU)!=0 || seen!=%uU)return %d;' % (ms, expected, i + 2))
                            harness = common + re.sub(r'\bRATE\b', str(rate), stubs[backend]).replace('TICK_TYPE', 'uint%d_t' % width)
                            harness += source + '\nint main(void) {\n'
                            harness += 'if(RTOS_PeripheralGuard_Init()!=0)return 1;\n' + '\n'.join(cases)
                            harness += '\nif(calls!=10)return 30;\nif(RTOS_PeripheralGuard_Lock((RTOS_GuardId)-1,1)!=-1 || calls!=10)return 31;\nreturn 0;\n}\n'
                            file = root / 'guard.c'
                            file.write_text(harness, encoding='utf-8')
                            exe = root / 'guard.exe'
                            compiled = subprocess.run([gcc, '-std=c99', '-Wall', '-Werror', '-Wno-unused-function', '-I', str(root), str(file), '-o', str(exe)], capture_output=True, text=True)
                            self.assertEqual(compiled.returncode, 0, compiled.stderr)
                            ran = subprocess.run([str(exe)], capture_output=True)
                            self.assertEqual(ran.returncode, 0, 'case failure %d' % ran.returncode)


if __name__ == '__main__':
    unittest.main(verbosity=2)
