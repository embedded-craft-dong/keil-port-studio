"""RT-Thread planning regressions. No board, network, or Keil needed."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('tool_rtthread',Path(__file__).parents[1]/'keil_port_tool.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class RTThreadTests(unittest.TestCase):
    def test_irq_empty_cube_handlers(self):
        text='''/* USER CODE END Includes */
void PendSV_Handler(void)
{
 /* USER CODE BEGIN PendSV_IRQn 0 */
 /* USER CODE END PendSV_IRQn 0 */
}
void HardFault_Handler(void)
{
 while (1) { }
}
void SysTick_Handler(void)
{
 /* USER CODE END SysTick_IRQn 0 */
 HAL_IncTick();
}
'''
        result=m.patch_rtthread_irq(text)
        self.assertIn('KPS_RTTHREAD_Tick();', result)
        self.assertEqual(result.count('#if !defined(KPS_USING_RTTHREAD)'),2)
        self.assertIn('HAL_IncTick();',result)
        self.assertEqual(m.patch_rtthread_irq(result),result)
        with self.assertRaises(m.ToolError):
            m.patch_rtthread_irq(text.replace('while (1) { }','report_fault(); while (1) { }'))

    def test_kernel_conflict_preflight(self):
        class Proj:
            def files_in_project(self): return ['Core/Src/rtthread_app.c']
        with self.assertRaises(m.ToolError):
            m.run_tasks(Proj(), ['freertos'], SimpleNamespace())
        with self.assertRaises(m.ToolError):
            m.run_tasks(Proj(), ['freertos','rtthread'], SimpleNamespace())

    def test_startup_not_hidden_inside_assert(self):
        _,text=m.rtthread_app_templates('stm32f4xx.h')
        self.assertIn('status = (rt_err_t)SysTick_Config',text)
        self.assertNotIn('RT_ASSERT(SysTick_Config',text)
        self.assertIn('rt_system_heap_init',text)
        self.assertIn('rt_system_scheduler_start();',text)
        self.assertIn('rt_thread_defunct_init();',text)
        self.assertIn('rt_assert_set_hook(KPS_RTThread_Assert);',text)
        self.assertIn('#if RT_TICK_PER_SECOND != 1000',text)

    def test_failed_combination_never_writes_successful_half(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'test.uvprojx'
            original='<Project><Targets><Target><TargetName>Debug</TargetName><Groups/></Target></Targets></Project>'
            path.write_text(original,encoding='utf-8')
            proj=m.KeilProject(path)
            def good(project,opts,rep):
                project.add_file('Test','good.c',1,'good.c')
                rep.gen_files.append((Path(temp)/'good.c','int good;','fixture'))
            def bad(project,opts,rep):
                raise m.ToolError('fixture rejected')
            with patch.dict(m.TASK_FUNCS,{'lvgl':good,'littlefs':bad}):
                self.assertFalse(m.run_tasks(proj,['lvgl','littlefs'],SimpleNamespace(yes=True)))
            self.assertTrue(proj._planning_failed)
            self.assertEqual(path.read_text(),original)
            self.assertFalse((Path(temp)/'good.c').exists())
            self.assertEqual(proj.files_in_project(),[])

    def test_rtthread_task_entry_insertion(self):
        _,text=m.rtthread_app_templates('stm32f4xx.h')
        result,changed=m._patch_component_init(text,'tinyusb_app.h','TinyUSB_AppInit();',True)
        self.assertTrue(changed)
        self.assertLess(result.index('TinyUSB_AppInit();'),result.index('++g_rtthread_heartbeat'))
        self.assertEqual(m._patch_component_init(result,'tinyusb_app.h','TinyUSB_AppInit();',True),(result,False))

    def test_littlefs_native_backend(self):
        _,text=m.littlefs_port_templates(True,False,True)
        self.assertIn('rt_mutex_take',text)
        self.assertNotIn('FreeRTOS.h',text)
        self.assertNotIn('cmsis_os2.h',text)
        format_body=text.split('int LittleFS_Format(void)')[1].split('LFS_PORT_WEAK')[0]
        self.assertIn('rt_mutex_create',format_body)

    def test_tinyusb_native_backend(self):
        config,_,app,_=m.tinyusb_templates(True,'device',['CDC'],'OPT_MCU_STM32F4',True)
        self.assertIn('OPT_OS_RTTHREAD',config)
        self.assertIn('rt_thread_init',app)
        self.assertNotIn('FreeRTOS.h',app)
        self.assertNotIn('xTaskCreate',app)
        self.assertNotIn('RT_ASSERT(rt_thread_init',app)
        self.assertIn('tud_task_ext(1, false)',app)

    def test_lwip_native_types_and_errno(self):
        opts, cc, sys_h, sys_c, _, port = m.lwip_port_templates(True, [], False, use_rtthread=True)
        self.assertNotIn('#define LWIP_PROVIDE_ERRNO', opts)
        self.assertIn('#define LWIP_ERRNO_INCLUDE              "sys/errno.h"', opts)
        self.assertIn('#include <sys/types.h>', cc)
        self.assertIn('#define SSIZE_MAX LONG_MAX', cc)
        self.assertIn('#define LWIP_NO_UNISTD_H 1', cc)
        self.assertIn('#define errno (*_rt_errno())', cc)
        self.assertNotIn('lwip_errno', cc + port)
        self.assertNotIn('FreeRTOS', sys_h + sys_c + port)
        self.assertIn('rt_mb_create', sys_c)

    def test_kernel_profile_not_smp(self):
        names={p.name for p in m.rtthread_source_files(Path('sdk'))}
        self.assertIn('scheduler_up.c',names)
        self.assertNotIn('scheduler_mp.c',names)
        self.assertIn('context_rvds.S',names)
        self.assertNotIn('#define RT_USING_USER_MAIN',m.RTTHREAD_CONFIG)
        self.assertEqual(len(m.RTTHREAD_SHA256),64)

if __name__=='__main__': unittest.main(verbosity=2)
