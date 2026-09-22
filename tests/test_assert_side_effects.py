"""Compile/run actual generated init bodies with assertions enabled AND disabled.

Host API doubles verify task creation, not USB/Ethernet hardware operation.
"""
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('assert_tool', Path(__file__).parents[1] / 'keil_port_tool.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class AssertTests(unittest.TestCase):
    def test_task_creation_survives_disabled_assert(self):
        gcc = shutil.which('gcc') or ('C:/MinGW/bin/gcc.exe' if Path('C:/MinGW/bin/gcc.exe').is_file() else None)
        if not gcc:
            self.skipTest('host gcc required')
        usb = m.tinyusb_templates(True, 'device', ['CDC'], 'OPT_MCU_STM32F4')[2]
        lwip = m.lwip_port_templates(True, [], False)[5]
        declarations = '''
#include <stddef.h>
typedef int BaseType_t;
#define pdPASS 1
#define tskIDLE_PRIORITY 0
static int calls, result = pdPASS, asserts;
static void TinyUSB_RtosTask(void *p) {(void)p;}
static void LwIP_InputTask(void *p) {(void)p;}
static void LwIP_AddNetif(void *p) {(void)p;}
static void tcpip_init(void (*f)(void *), void *p) {(void)f;(void)p;}
static void TinyUSB_Platform_Init(void) {}
static int tud_init(int x) {(void)x;return 1;}
static int xTaskCreate(void (*f)(void *), const char *name, unsigned depth,
                      void *arg, unsigned priority, void *handle) {
    (void)f;(void)name;(void)depth;(void)arg;(void)priority;(void)handle;
    ++calls; return result;
}
'''
        with tempfile.TemporaryDirectory(prefix='kps-assert-') as temp:
            base = Path(temp)
            for source, name in ((usb, 'TinyUSB_AppInit'), (lwip, 'LwIP_AppInit')):
                match = re.search(r'void ' + name + r'\(void\)\s*\{(.*?)\n\}', source, re.S)
                self.assertIsNotNone(match)
                self.assertNotIn('configASSERT(xTaskCreate', match.group(1))
                for enabled in (False, True):
                    macro = '#define configASSERT(x) do { if (!(x)) ++asserts; } while(0)\n' if enabled else '#define configASSERT(x) ((void)0)\n'
                    harness = declarations + macro + match.group(0) + '''
int main(void) {
    INIT();
    if (calls != 1 || asserts != 0) return 1;
    result = 0;
    INIT();
    if (calls != 2 || asserts != EXPECTED) return 2;
    return 0;
}
'''.replace('INIT', name).replace('EXPECTED', '1' if enabled else '0')
                    file = base / ('test_%s_%d.c' % (name, enabled)); exe = file.with_suffix('.exe')
                    file.write_text(harness, encoding='utf-8')
                    subprocess.run([gcc, '-std=c99', '-Wall', '-Werror', '-Wno-unused-function', str(file), '-o', str(exe)], check=True, capture_output=True)
                    subprocess.run([str(exe)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
