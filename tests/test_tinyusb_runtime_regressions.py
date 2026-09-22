"""Guard runtime failures found on an STM32F407 using actual AC5 firmware."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'keil_port_tool.py'
if not TOOL.exists(): TOOL = ROOT / 'outputs/keil_port_tool.py'
spec = importlib.util.spec_from_file_location('usb_runtime_tool', TOOL)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

class RuntimeRegression(unittest.TestCase):
    def test_rtos_enables_usb_interrupts_only_in_running_owner_task(self):
        for rtthread in (False,True):
            for role in ('device','host','both'):
                source=tool.tinyusb_templates(True,role,['MSC'],'OPT_MCU_STM32F4',rtthread)[2]
                init=source.split('void TinyUSB_AppInit(void)\n{',1)[1].split('\n}',1)[0]
                self.assertNotIn('TinyUSB_Platform_Init();',init)
                self.assertNotIn('tuh_init(',init)
                self.assertNotIn('tud_init(',init)
                task=source.split('static void TinyUSB_RtosTask(void *argument)',1)[1].split('void TinyUSB_AppInit',1)[0]
                self.assertIn('TinyUSB_Platform_Init();',task)
                self.assertLess(task.index('TinyUSB_Platform_Init();'),task.index('for (;;)'))

    def test_auto_sdk_uses_role_and_compiler(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for ver in ('0.17.0','0.18.0','0.21.0'):
                src=root/('tinyusb-'+ver)/'src'
                src.mkdir(parents=True)
                (src/'tusb.c').write_text('/* fixture */')
                (src/'tusb.h').write_text('/* fixture */')
                parts=ver.split('.')
                (src/'tusb_option.h').write_text('\n'.join(
                    '#define TUSB_VERSION_%s %s' % pair for pair in zip(('MAJOR','MINOR','REVISION'),parts)))
                driver=src/'portable/synopsys/dwc2'
                driver.mkdir(parents=True)
                (driver/'dcd_dwc2.c').write_text('')
                if ver!='0.17.0': (driver/'hcd_dwc2.c').write_text('')
            for ac6,mode,expected in ((False,'device','0.17.0'),(False,'host','0.18.0'),
                                       (False,'both','0.18.0'),(True,'host','0.21.0')):
                p=SimpleNamespace(any_ac6=lambda:ac6,device=lambda:'STM32F407ZGT6')
                opts=SimpleNamespace(sdk_dir=td,tinyusb_mode=mode,no_download=True)
                self.assertEqual(tool.ensure_tinyusb_sdk(p,opts,tool.Report('tinyusb')).name,'tinyusb-'+expected)

    def test_new_sdk_timebase_never_returns_a_fake_constant(self):
        self.assertIn('return HAL_GetTick();',tool.tinyusb_timebase_template(use_hal=True))
        self.assertIn('configTICK_RATE_HZ',tool.tinyusb_timebase_template(use_rtos=True))
        self.assertIn('RT_TICK_PER_SECOND',tool.tinyusb_timebase_template(use_rtthread=True))
        unknown=tool.tinyusb_timebase_template()
        self.assertIn('extern uint32_t TinyUSB_Platform_Millis(void)',unknown)
        self.assertNotIn('return 0;',unknown)

    def test_host_fifo_regions_do_not_overlap(self):
        original=('uint16_t ptxfsiz = dfifo_top - (nptxfsiz + rxfsiz);\n'
                  'dfifo_top -= rxfsiz;\n  dwc2->grxfsiz = rxfsiz;\n'
                  'dfifo_top -= nptxfsiz;\n'
                  'dwc2->gnptxfsiz = tu_u32_from_u16(nptxfsiz, dfifo_top);\n'
                  'dfifo_top -= ptxfsiz;\n')
        fixed,changed=tool.patch_tinyusb_dwc2_host_fifo_layout(original)
        self.assertTrue(changed)
        self.assertNotIn('dfifo_top -= rxfsiz;',fixed)
        self.assertIn('dwc2->grxfsiz = rxfsiz;',fixed)
        self.assertEqual(tool.patch_tinyusb_dwc2_host_fifo_layout(fixed),(fixed,False))
        # F407 FS: 320 words total, RX 139, nonperiodic TX 32.
        top,rx,nptx=320,139,32
        periodic=top-rx-nptx
        np_start=top-nptx
        p_start=np_start-periodic
        self.assertEqual(p_start,rx)
        self.assertEqual((p_start+periodic,np_start+nptx),(np_start,top))
        with self.assertRaises(tool.ToolError):
            tool.patch_tinyusb_dwc2_host_fifo_layout(original+original)

    def test_ac5_register_layout_fix_is_scoped_and_repeatable(self):
        original=('#define TUSB_DWC2_TYPES_H_\n'
                  'typedef struct TU_ATTR_PACKED { uint32_t flag:1; const uint32_t rest:31; } dwc2_test_t;\n'
                  'typedef struct TU_ATTR_PACKED { uint8_t value; } wire_t;\n')
        fixed,changed=tool.patch_tinyusb_armcc5_dwc2_registers(original)
        self.assertTrue(changed)
        self.assertIn('struct KPS_DWC2_REG_LAYOUT',fixed)
        self.assertIn('struct TU_ATTR_PACKED { uint8_t value; } wire_t;',fixed)
        self.assertEqual(tool.patch_tinyusb_armcc5_dwc2_registers(fixed),(fixed,False))
        with self.assertRaises(tool.ToolError):
            tool.patch_tinyusb_armcc5_dwc2_registers(original.replace('uint32_t flag:1','uint8_t flag:1'))

    def test_rx_status_pops_exactly_once(self):
        source='const dwc2_grxstsp_t grxstsp_bm = dwc2->grxstsp_bm;'
        fixed,changed=tool.patch_tinyusb_dwc2_fifo_snapshot(source)
        self.assertTrue(changed)
        self.assertEqual(fixed.count('dwc2->grxstsp;'),1)
        self.assertNotIn('dwc2->grxstsp_bm',fixed)
        self.assertEqual(tool.patch_tinyusb_dwc2_fifo_snapshot(fixed),(fixed,False))
        with self.assertRaises(tool.ToolError):
            tool.patch_tinyusb_dwc2_fifo_snapshot('new_format = dwc2->grxstsp_bm;')

    def test_controller_driver_required_for_each_role(self):
        common=Path('src/portable/synopsys/dwc2/dwc2_common.c')
        device=common.with_name('dcd_dwc2.c')
        host=common.with_name('hcd_dwc2.c')
        for mode, files in [('device',[common]),('host',[common]),
                            ('both',[common,device]),('both',[common,host])]:
            with self.subTest(mode=mode, files=files):
                with self.assertRaises(tool.ToolError):
                    tool.validate_tinyusb_controller_sources(files,mode)
        tool.validate_tinyusb_controller_sources([device],'device')
        tool.validate_tinyusb_controller_sources([common,host],'host')
        tool.validate_tinyusb_controller_sources([common,device,host],'both')

    def test_sdk_allowlist_excludes_documentation_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            archive=root/'sdk.zip'
            link=zipfile.ZipInfo('sdk/docs/code_of_conduct.rst')
            link.create_system=3
            link.external_attr=(0o120777 << 16)
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('sdk/src/tusb.c','/* safe source */')
                z.writestr(link,'../../CODE_OF_CONDUCT.rst')
            tool.extract_zip(archive,root/'selected',keep_prefixes=('src/',))
            self.assertTrue((root/'selected/sdk/src/tusb.c').is_file())
            self.assertFalse((root/'selected/sdk/docs').exists())
            with self.assertRaises(tool.ToolError):
                tool.extract_zip(archive,root/'all')
            self.assertFalse((root/'all/sdk/src/tusb.c').exists())

    def test_compact_endpoints(self):
        for name in ('MSC', 'HID', 'MIDI', 'VENDOR'):
            endpoints, count = tool.tinyusb_endpoint_numbers([name])
            self.assertEqual((endpoints[name],count),(1,1))
        self.assertEqual(tool.tinyusb_endpoint_numbers(['CDC','HID']),({'CDC':1,'HID':3},3))
        desc = tool.tinyusb_templates(False,'device',['HID'],'OPT_MCU_STM32F4')[3]
        self.assertIn('sizeof(s_hid_report_desc), 0x81, 16, 10)',desc)
        self.assertNotIn('0x84',desc)

    def test_impossible_f407_combination_rejected_before_copy(self):
        project = SimpleNamespace(device=lambda:'STM32F407ZGT6')
        opts = SimpleNamespace(tinyusb_mode='device',tinyusb_classes=['CDC','MSC','HID'])
        with self.assertRaisesRegex(tool.ToolError,'3 .*IN'):
            tool.do_tinyusb(project,opts,None)

    def test_cdc_has_strong_owner_and_backpressure(self):
        for rtos in (False, True):
            source = tool.tinyusb_templates(rtos,'device',['CDC'],'OPT_MCU_STM32F4')[2]
            self.assertIn('TinyUSB_CdcEcho();',source)
            self.assertIn('tud_cdc_n_write_available(itf)',source)
            self.assertIn('tud_cdc_n_read(itf, buffer, room)',source)
            self.assertNotIn('void tud_cdc_rx_cb(',source)
            self.assertIn('tud_task_ext(%d, false)' % (1 if rtos else 0),source)

if __name__=='__main__': unittest.main(verbosity=2)
