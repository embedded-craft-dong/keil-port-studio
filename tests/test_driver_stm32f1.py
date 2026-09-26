"""F1 generated ports: host execution, ownership and optional real AC5 SDK compile.

No board is programmed. Real SDK tests require KPS_DRIVER_F1_HAL_SDK (Cube root)
and KPS_DRIVER_F1_SPL_SDK (STM32F10x StdPeriph package root), plus ARMCC.
"""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_driver_stm32 as base
from kps_core.device_drivers import CATALOG


class F1BindingTests(base.STM32BindingTests):
    family='f1'

    def fixture(self, root, hal):
        p=super().fixture(root,hal)
        xml=p.path.read_text(encoding='utf-8').replace('STM32F407ZG','STM32F103C8').replace('Cortex-M4','Cortex-M3').replace(' FPU','')
        p.path.write_text(xml,encoding='utf-8')
        return base.m.KeilProject(p.path)

    def test_family_gpio_remap_and_safe_rejection(self):
        for hal in (True,False):
            with self.subTest(hal=hal),tempfile.TemporaryDirectory() as td:
                root=Path(td); p=self.fixture(root,hal)
                entry=root/'User/entry.c'
                original=entry.read_bytes()
                cfg=base.binding(p,base.options(driver_i2c='software'),base.m.read_source_text)
                self.assertEqual(cfg['family'],'f1')
                files=base.render_pack(['sht3x','w25q128jv'],'software','hardware',cfg)
                source=files['kps_board_port.c']
                self.assertNotIn('stm32f4',source)
                self.assertNotIn('AHB1',source)
                self.assertNotIn('GPIO_OType',source)
                self.assertNotIn('AFIO->',source)
                self.assertNotIn('GPIO_PinRemapConfig',source)
                self.assertIn('HAL_GPIO_Init' if hal else 'GPIO_Mode_Out_OD',source)
                self.assertIn('HAL_GPIO_Init' if hal else 'RCC_APB2PeriphClockCmd',source)
                self.assertEqual(entry.read_bytes(),original)
                for pin in ('PA13','PA14','PA15','PB3','PB4','PH0','PI0'):
                    with self.subTest(pin=pin),self.assertRaises(base.m.ToolError):
                        base.binding(p,base.options(driver_cs=pin),base.m.read_source_text)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertTrue(base.m.run_tasks(p,['device_drivers'],base.options(driver_i2c='software')))
                    self.assertTrue(base.m.uninstall_component(base.m.KeilProject(p.path),'device_drivers',yes=True))
                self.assertEqual(entry.read_bytes(),original)

    def test_other_families_and_mixed_libraries_still_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p=self.fixture(Path(td),True)
            device=p.targets[0].find('TargetOption/TargetCommonOption/Device')
            for name in ('STM32F030C8','STM32H743ZI','GD32F103C8'):
                device.text=name
                with self.assertRaises(base.m.ToolError): base.binding(p,base.options(),base.m.read_source_text)
            device.text='STM32F103C8'
            p.targets[0].find('.//Define').text='USE_HAL_DRIVER,USE_STDPERIPH_DRIVER'
            with self.assertRaises(base.m.ToolError): base.binding(p,base.options(),base.m.read_source_text)

    def test_real_keil_ac5_sdk_headers(self):
        compiler=Path(os.environ.get('ARMCC','D:/Keil_v5/ARM/ARMCC/Bin/armcc.exe'))
        roots=[os.environ.get('KPS_DRIVER_F1_HAL_SDK'),os.environ.get('KPS_DRIVER_F1_SPL_SDK')]
        if not all(roots) or not compiler.is_file():
            self.skipTest('Set F1 HAL/SPL SDK roots and ARMCC; no target compile pass claimed')
        for hal in (True,False):
            sdk=Path(roots[0 if hal else 1])
            if hal:
                includes=[sdk/'Core/Inc',sdk/'Drivers/STM32F1xx_HAL_Driver/Inc',
                          sdk/'Drivers/CMSIS/Include',sdk/'Drivers/CMSIS/Device/ST/STM32F1xx/Include']
                common=['USE_HAL_DRIVER','HAL_I2C_MODULE_ENABLED','HAL_SPI_MODULE_ENABLED']
                devices=['STM32F103xB','STM32F103xE','STM32F107xC']
            else:
                includes=[sdk/'Project/STM32F10x_StdPeriph_Template',
                          sdk/'Libraries/STM32F10x_StdPeriph_Driver/inc',
                          sdk/'Libraries/CMSIS/CM3/CoreSupport',
                          sdk/'Libraries/CMSIS/CM3/DeviceSupport/ST/STM32F10x']
                common=['USE_STDPERIPH_DRIVER']; devices=['STM32F10X_MD','STM32F10X_HD','STM32F10X_CL']
            for path in includes: self.assertTrue(path.is_dir(),str(path))
            for device in devices:
                for mode in ('hardware','software'):
                    with self.subTest(hal=hal,device=device,mode=mode),tempfile.TemporaryDirectory() as td:
                        root=Path(td); p=self.fixture(root,hal)
                        cfg=base.binding(p,base.options(driver_i2c=mode),base.m.read_source_text)
                        files=base.render_pack(list(CATALOG),mode,'hardware',cfg)
                        for name,content in files.items(): (root/name).write_text(content,encoding='utf-8')
                        for name in files:
                            if not name.endswith('.c'): continue
                            args=[str(compiler),'--cpu','Cortex-M3','--c99','-O2','-c',str(root/name),'-o',str(root/(name+'.o'))]
                            args+=['-I'+str(i) for i in includes]+['-D'+d for d in common+[device]]
                            result=subprocess.run(args,cwd=root,capture_output=True,text=True,errors='replace')
                            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                            self.assertNotIn('warning:',result.stderr.lower(),result.stderr)
                        # Link against real vendor implementations as well as headers.
                        # This image is a link fixture only, never flashed or run.
                        if hal:
                            vendor=[sdk/('Drivers/STM32F1xx_HAL_Driver/Src/stm32f1xx_hal'+suffix+'.c')
                                    for suffix in ('','_gpio','_gpio_ex','_rcc','_rcc_ex','_i2c','_spi','_dma','_cortex')]
                            vendor+=[sdk/'Drivers/CMSIS/Device/ST/STM32F1xx/Source/Templates/system_stm32f1xx.c']
                            entry='#include "stm32f1xx_hal.h"\nI2C_HandleTypeDef hi2c1;\nSPI_HandleTypeDef hspi1;\n'
                        else:
                            vendor=[sdk/('Libraries/STM32F10x_StdPeriph_Driver/src/stm32f10x_'+part+'.c')
                                    for part in ('gpio','rcc','i2c','spi')]
                            vendor+=[sdk/'Libraries/CMSIS/CM3/CoreSupport/core_cm3.c',
                                     sdk/'Libraries/CMSIS/CM3/DeviceSupport/ST/STM32F10x/system_stm32f10x.c']
                            entry=''
                        entry+='#include "kps_stm32_port.h"\nint main(void){kps_bus b; (void)kps_stm32_init(); b=kps_stm32_bus(); return b.lock(b.ctx,100);}\n'
                        (root/'link_entry.c').write_text(entry,encoding='utf-8')
                        vendor+=[root/'link_entry.c']
                        for index,path in enumerate(vendor):
                            args=[str(compiler),'--cpu','Cortex-M3','--c99','-O2','-c',str(path),'-o',str(root/('vendor%d.o'%index))]
                            args+=['-I'+str(root)]+['-I'+str(i) for i in includes]+['-D'+d for d in common+[device]]
                            result=subprocess.run(args,cwd=root,capture_output=True,text=True,errors='replace')
                            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        linker=compiler.with_name('armlink.exe')
                        args=[str(linker),'--cpu','Cortex-M3','--entry=main','--ro_base=0x08000000','--rw_base=0x20000000',
                              '--libpath='+str(compiler.parent.parent/'lib'),'-o',str(root/'link-only.axf')]
                        args += [str(obj) for obj in root.glob('*.o')]
                        result=subprocess.run(args,cwd=root,capture_output=True,text=True,errors='replace')
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        self.assertNotIn('warning:',result.stderr.lower(),result.stderr)


if __name__=='__main__': unittest.main(verbosity=2)
