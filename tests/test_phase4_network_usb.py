import importlib.util
import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parents[1]
TOOL = ROOT / 'keil_port_tool.py'
if not TOOL.exists():
    TOOL = ROOT / 'outputs' / 'keil_port_tool.py'
spec = importlib.util.spec_from_file_location('keil_port_tool_phase4', TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

tinyusb_compiler = '''\
#if defined(__GNUC__)
  #if defined(__XC16)
    #define TU_BSWAP16(u16) (__builtin_swap(u16))
    #define TU_BSWAP32(u32) (u32)
  #else
    #define TU_BSWAP16(u16) (__builtin_bswap16(u16))
    #define TU_BSWAP32(u32) (__builtin_bswap32(u32))
  #endif
#endif
'''
patched_compiler, compiler_changed = m.patch_tinyusb_armcc5_compiler(tinyusb_compiler)
assert compiler_changed and 'Keil Port Studio AC5' in patched_compiler
assert '__builtin_bswap16' in patched_compiler  # GCC/Clang 分支仍保留
assert not m.patch_tinyusb_armcc5_compiler(patched_compiler)[1]

PROJECT_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<Project><Targets><Target><TargetName>Debug</TargetName><pCCUsed>V5.06</pCCUsed><uAC6>0</uAC6>
<TargetOption><TargetCommonOption><Device>STM32F407VG</Device>
<Cpu>CPUTYPE("Cortex-M4") FPU</Cpu></TargetCommonOption>
<TargetArmAds><Cads><VariousControls><MiscControls/><Define>BASE</Define>
<IncludePath>../Core/Inc</IncludePath></VariousControls></Cads></TargetArmAds></TargetOption>
<Groups><Group><GroupName>Application</GroupName><Files>
<File><FileName>freertos_app.c</FileName><FileType>1</FileType>
<FilePath>../Core/Src/freertos_app.c</FilePath></File>
<File><FileName>tasks.c</FileName><FileType>1</FileType>
<FilePath>../Middlewares/Third_Party/FreeRTOS/tasks.c</FilePath></File>
</Files></Group></Groups></Target></Targets></Project>'''


def write(path, content='/* fixture */\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


with tempfile.TemporaryDirectory() as td:
    base = Path(td)
    root = base / 'Demo'
    mdk = root / 'MDK-ARM'
    write(mdk / 'Demo.uvprojx', PROJECT_XML)
    write(root / 'Core' / 'Src' / 'freertos_app.c', '''\
/* USER CODE BEGIN Includes */
/* USER CODE END Includes */
void MX_FREERTOS_Init(void)
{
}
''')
    write(root / 'Core' / 'Inc' / 'main.h')

    lwip = base / 'SDK' / 'lwip'
    write(lwip / 'src' / 'include' / 'lwip' / 'init.h')
    for name in ('init.c', 'mem.c', 'memp.c', 'netif.c', 'pbuf.c', 'sys.c',
                 'timeouts.c', 'tcp.c', 'udp.c'):
        write(lwip / 'src' / 'core' / name)
    for name in ('ip4.c', 'etharp.c', 'dhcp.c'):
        write(lwip / 'src' / 'core' / 'ipv4' / name)
    write(lwip / 'src' / 'core' / 'ipv6' / 'ip6.c')
    write(lwip / 'src' / 'netif' / 'ethernet.c')
    for name in ('api_lib.c', 'tcpip.c'):
        write(lwip / 'src' / 'api' / name)
    write(lwip / 'src' / 'apps' / 'http' / 'httpd.c')
    write(lwip / 'src' / 'apps' / 'mqtt' / 'mqtt.c')

    tusb = base / 'SDK' / 'tinyusb'
    write(tusb / 'src' / 'tusb.h')
    write(tusb / 'src' / 'tusb.c')
    write(tusb / 'src' / 'common' / 'tusb_fifo.c')
    write(tusb / 'src' / 'osal' / 'osal_freertos.h')
    write(tusb / 'src' / 'device' / 'usbd.c')
    write(tusb / 'src' / 'class' / 'cdc' / 'cdc_device.c')
    write(tusb / 'src' / 'class' / 'msc' / 'msc_device.c')
    write(tusb / 'src' / 'class' / 'hid' / 'hid_device.c')
    write(tusb / 'src' / 'portable' / 'synopsys' / 'dwc2' / 'dwc2_common.c')
    write(tusb / 'src' / 'portable' / 'synopsys' / 'dwc2' / 'dcd_dwc2.c')

    project = mdk / 'Demo.uvprojx'
    opts = SimpleNamespace(
        interactive=False, yes=True, dry_run=False, no_download=True,
        sdk_dir=None, diff_file=None,
        lwip=str(lwip), lwip_mode='auto', lwip_ipv6=True,
        lwip_apps=['http', 'mqtt'], lwip_files=None, lwip_driver='stm32_eth',
        tinyusb=str(tusb), tinyusb_mode='device',
        tinyusb_classes=['CDC', 'MSC'], tinyusb_files=None)
    assert m.run_tasks(m.KeilProject(project), ['lwip', 'tinyusb'], opts)

    local = root / 'Middlewares' / 'Third_Party'
    assert (local / 'LwIP' / 'src' / 'core' / 'init.c').is_file()
    assert (local / 'TinyUSB' / 'src' / 'tusb.c').is_file()
    assert (root / 'Config' / 'LwIP' / 'lwipopts.h').is_file()
    assert (root / 'Config' / 'LwIP' / 'arch' / 'sys_arch.h').is_file()
    assert (root / 'Config' / 'TinyUSB' / 'tusb_config.h').is_file()
    driver = (root / 'Core' / 'Src' / 'lwip_netif_driver.c').read_text(encoding='utf-8')
    assert 'STM32 ETH MAC + PHY' in driver and 'NetworkDriver_Send' in driver
    lwip_opts = (root / 'Config' / 'LwIP' / 'lwipopts.h').read_text(encoding='utf-8')
    assert '#define NO_SYS                          0' in lwip_opts
    assert '#define LWIP_IPV6                       1' in lwip_opts
    assert '#define LWIP_HTTPD                      1' in lwip_opts
    assert '#define LWIP_MQTT                       1' in lwip_opts
    assert '#define LWIP_PROVIDE_ERRNO              1' in lwip_opts
    assert '#define LWIP_NUM_NETIF_CLIENT_DATA      0' in lwip_opts
    assert '#define TCPIP_THREAD_STACKSIZE          1024' in lwip_opts
    assert '#define TCPIP_MBOX_SIZE                 16' in lwip_opts
    assert '#define DEFAULT_TCP_RECVMBOX_SIZE       16' in lwip_opts
    assert '#define DEFAULT_ACCEPTMBOX_SIZE         8' in lwip_opts
    lwip_cc = (root / 'Config' / 'LwIP' / 'arch' / 'cc.h').read_text(encoding='utf-8')
    lwip_port = (root / 'Core' / 'Src' / 'lwip_port.c').read_text(encoding='utf-8')
    assert '#define errno lwip_errno' in lwip_cc and 'int lwip_errno;' in lwip_port
    tusb_cfg = (root / 'Config' / 'TinyUSB' / 'tusb_config.h').read_text(encoding='utf-8')
    assert 'OPT_MCU_STM32F4' in tusb_cfg
    assert '#define CFG_TUSB_OS OPT_OS_FREERTOS' in tusb_cfg
    assert '#define CFG_TUD_CDC 1' in tusb_cfg
    assert '#define CFG_TUD_MSC 1' in tusb_cfg
    assert '#define CFG_TUD_HID 0' in tusb_cfg
    desc = (root / 'Core' / 'Src' / 'usb_descriptors.c').read_text(encoding='utf-8')
    assert 'TUD_CDC_DESCRIPTOR' in desc
    assert 'TUD_MSC_DESCRIPTOR' in desc
    assert 'TUD_HID_DESCRIPTOR' not in desc
    app = (root / 'Core' / 'Src' / 'freertos_app.c').read_text(encoding='utf-8')
    assert '#include "lwip_port.h"' in app and 'LwIP_AppInit();' in app
    assert '#include "tinyusb_app.h"' in app and 'TinyUSB_AppInit();' in app

    xml = ET.parse(project).getroot()
    files = {node.findtext('FileName') for node in xml.findall('.//File')}
    assert {'init.c', 'tcpip.c', 'httpd.c', 'mqtt.c', 'lwip_port.c',
            'lwip_netif_driver.c', 'sys_arch.c'} <= files
    assert {'tusb.c', 'usbd.c', 'cdc_device.c', 'msc_device.c',
            'dcd_dwc2.c', 'tinyusb_app.c', 'usb_descriptors.c'} <= files
    manifest = json.loads((root / '.keil-port-tool' / 'manifest.json').read_text(encoding='utf-8'))
    assert {'lwip', 'tinyusb'} <= set(manifest['components'])

    gcc = shutil.which('gcc')
    if gcc:
        stubs = base / 'stubs'
        write(stubs / 'lwip' / 'err.h', '''\
#ifndef STUB_ERR_H
#define STUB_ERR_H
typedef int err_t;
#define ERR_OK 0
#define ERR_IF -1
#define ERR_BUF -2
#endif
''')
        write(stubs / 'lwip' / 'netif.h', '''\
#ifndef STUB_NETIF_H
#define STUB_NETIF_H
#include <stdint.h>
typedef uint16_t u16_t;
struct pbuf;
struct netif { uint8_t hwaddr_len; uint8_t hwaddr[6]; uint8_t flags; };
#define NETIF_FLAG_LINK_UP 1
#endif
''')
        write(stubs / 'lwip' / 'pbuf.h', '''\
#ifndef STUB_PBUF_H
#define STUB_PBUF_H
#include <stdint.h>
typedef uint16_t u16_t;
typedef int err_t;
struct pbuf { struct pbuf *next; void *payload; u16_t len; u16_t tot_len; };
#define PBUF_RAW 0
#define PBUF_POOL 0
struct pbuf *pbuf_alloc(int layer, u16_t length, int type);
err_t pbuf_take(struct pbuf *p, const void *data, u16_t length);
void pbuf_free(struct pbuf *p);
#endif
''')
        subprocess.run([gcc, '-std=c99', '-fsyntax-only',
                        '-I', str(stubs), '-I', str(root / 'Core' / 'Inc'),
                        str(root / 'Core' / 'Src' / 'lwip_netif_driver.c')],
                       check=True, capture_output=True, text=True)

    assert m.uninstall_component(m.KeilProject(project), 'tinyusb', yes=True)
    app = (root / 'Core' / 'Src' / 'freertos_app.c').read_text(encoding='utf-8')
    assert 'tinyusb_app.h' not in app and 'TinyUSB_AppInit();' not in app
    assert 'lwip_port.h' in app and 'LwIP_AppInit();' in app
    assert m.uninstall_component(m.KeilProject(project), 'lwip', yes=True)
    app = (root / 'Core' / 'Src' / 'freertos_app.c').read_text(encoding='utf-8')
    assert 'lwip_port.h' not in app and 'LwIP_AppInit();' not in app

opts_h, _cc, sys_h, sys_c, _port_h, port_c = m.lwip_port_templates(
    False, ['http'], False)
assert '#define NO_SYS                          1' in opts_h
assert sys_h is None and sys_c is None
assert 'lwip_init();' in port_c and 'sys_check_timeouts();' in port_c
rtos_opts_h, _cc, _sys_h, _sys_c, _port_h, _port_c = m.lwip_port_templates(
    True, [], False)
assert '#define TCPIP_THREAD_STACKSIZE          1024' in rtos_opts_h
assert '#define TCPIP_THREAD_PRIO               4' in rtos_opts_h
assert '#define TCPIP_MBOX_SIZE                 16' in rtos_opts_h
assert '#define DEFAULT_RAW_RECVMBOX_SIZE       8' in rtos_opts_h
assert '#define DEFAULT_UDP_RECVMBOX_SIZE       8' in rtos_opts_h
assert '#define DEFAULT_TCP_RECVMBOX_SIZE       16' in rtos_opts_h
assert '#define DEFAULT_ACCEPTMBOX_SIZE         8' in rtos_opts_h
main_text = '''\
#include "main.h"
/* USER CODE END Includes */
int main(void) {
/* USER CODE END 2 */
while (1) {
/* USER CODE BEGIN 3 */
}
}
'''
patched, changed = m._patch_component_init(main_text, 'tinyusb_app.h',
                                           'TinyUSB_AppInit();', False)
patched, loop_changed = m._patch_main_loop_call(patched, 'TinyUSB_AppTask();')
assert changed and loop_changed
assert 'TinyUSB_AppInit();' in patched and 'TinyUSB_AppTask();' in patched
cfg, _h, _c, _d = m.tinyusb_templates(False, 'both', ['CDC'], 'OPT_MCU_STM32F4')
assert 'CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE' in cfg
assert 'CFG_TUSB_RHPORT1_MODE (OPT_MODE_HOST' in cfg

print('phase 4 network/USB integration tests passed')
