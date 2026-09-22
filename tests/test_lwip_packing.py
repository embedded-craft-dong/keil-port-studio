"""Compile the generated LwIP protocol layouts with actual Arm Compiler 5."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = Path(os.environ.get('KEIL_TOOL_UNDER_TEST', ROOT / 'keil_port_tool.py'
                          if (ROOT / 'keil_port_tool.py').is_file() else ROOT / 'outputs/keil_port_tool.py'))
SPEC = importlib.util.spec_from_file_location('tool_packing_test', TOOL)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)
SDK = Path(os.environ['LWIP_SDK']) if os.environ.get('LWIP_SDK') else None
ARMCC = Path(os.environ['ARMCC']) if os.environ.get('ARMCC') else None
SOURCE = '''
#include "lwip/prot/ethernet.h"
#include "lwip/prot/etharp.h"
#include "lwip/prot/ip4.h"
#include "lwip/prot/tcp.h"
#include "lwip/prot/udp.h"
#include "lwip/prot/icmp.h"
PACK_STRUCT_BEGIN
struct packing_probe { PACK_STRUCT_FLD_8(u8_t x); PACK_STRUCT_FIELD(u32_t y); }
PACK_STRUCT_STRUCT;
PACK_STRUCT_END
typedef char packing_probe_must_be_5[sizeof(struct packing_probe) == 5 ? 1 : -1];
typedef char eth_must_be_14[sizeof(struct eth_hdr) == 14 ? 1 : -1];
typedef char arp_must_be_28[sizeof(struct etharp_hdr) == 28 ? 1 : -1];
typedef char ip_must_be_20[sizeof(struct ip_hdr) == 20 ? 1 : -1];
typedef char tcp_must_be_20[sizeof(struct tcp_hdr) == 20 ? 1 : -1];
typedef char udp_must_be_8[sizeof(struct udp_hdr) == 8 ? 1 : -1];
typedef char icmp_must_be_8[sizeof(struct icmp_echo_hdr) == 8 ? 1 : -1];
'''

class PackingTests(unittest.TestCase):
    def test_new_rtos_config_uses_runtime_clock(self):
        self.assertIn('extern uint32_t SystemCoreClock;', tool.DEFAULT_FREERTOS_CONFIG)
        self.assertIn('#define configCPU_CLOCK_HZ                      ( ( uint32_t ) SystemCoreClock )', tool.DEFAULT_FREERTOS_CONFIG)
        self.assertNotIn('72000000', tool.DEFAULT_FREERTOS_CONFIG)

    def test_generated_thread_handoff_and_packing(self):
        bare = tool.lwip_port_templates(False, [], False)
        rtos = tool.lwip_port_templates(True, [], False)
        self.assertIn('LwIP_NetifInit, ethernet_input)', bare[5])
        self.assertIn('LwIP_NetifInit, tcpip_input)', rtos[5])
        self.assertNotIn('LwIP_NetifInit, ethernet_input)', rtos[5])
        self.assertIn('while (budget-- &&', rtos[5])
        self.assertEqual(rtos[3].count('if (timeout && ticks == 0) ticks = 1;'), 2)
        self.assertIn('#define PACK_STRUCT_BEGIN __packed', rtos[1])

    @unittest.skipUnless(ARMCC and ARMCC.is_file() and SDK and SDK.is_dir(),
                         'Set ARMCC and LWIP_SDK for the optional actual AC5 test')
    def test_real_ac5_layout_and_negative_control(self):
        cc = tool.lwip_port_templates(False, [], False)[1]
        for patched in (False, True):
            with self.subTest(patched=patched), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / 'arch').mkdir()
                header = cc
                if not patched:
                    header = header.replace('#define PACK_STRUCT_BEGIN __packed', '#define PACK_STRUCT_BEGIN')
                (root / 'arch/cc.h').write_text(header, encoding='utf-8')
                (root / 'lwipopts.h').write_text('#define NO_SYS 1\n#define LWIP_SOCKET 0\n#define LWIP_NETCONN 0\n', encoding='utf-8')
                (root / 'probe.c').write_text(SOURCE, encoding='utf-8')
                compiled = subprocess.run([str(ARMCC), '--cpu=Cortex-M4.fp', '--c99', '-c',
                    '-I' + str(root), '-I' + str(SDK / 'src/include'), str(root / 'probe.c'),
                    '-o', str(root / 'probe.o')], capture_output=True, text=True, timeout=30)
                if patched:
                    self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                    self.assertNotIn('warning:', compiled.stderr.lower())
                else:
                    self.assertNotEqual(compiled.returncode, 0, 'Negative control must detect the original defect')
                    self.assertIn('packing_probe_must_be_5', compiled.stderr + compiled.stdout)

if __name__ == '__main__':
    unittest.main(verbosity=2)
