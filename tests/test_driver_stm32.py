"""Automatic binding detection and real STM32F4 SDK compilation. Not device HIL."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import keil_port_tool as m
from kps_core.driver_stm32 import discover, binding
from kps_core.driver_generator import render_pack
from test_spl_adapter import project

MOCK_H = r'''
#ifndef MOCK_STM32_H
#define MOCK_STM32_H
#include <stdint.h>
#include <stddef.h>
typedef struct {uint32_t CYCCNT,CTRL;} Dwt;
typedef struct {uint32_t DEMCR;} Debug;
extern Debug debug_regs; Dwt *mock_dwt(void);
#define DWT mock_dwt()
#define CoreDebug (&debug_regs)
#define CoreDebug_DEMCR_TRCENA_Msk 1
#define DWT_CTRL_CYCCNTENA_Msk 1
extern uint32_t SystemCoreClock;
void SystemCoreClockUpdate(void);
unsigned __get_IPSR(void); unsigned __get_PRIMASK(void);
void __disable_irq(void); void __set_PRIMASK(unsigned);
void __DMB(void); void __NOP(void);
typedef struct {uint32_t CR1,SR1,SR2;} Periph;
extern Periph i2c_regs,spi_regs;
#define I2C1 (&i2c_regs)
#define SPI1 (&spi_regs)
#define GPIOB ((void *)1)
#define GPIO_Pin_0 1
#define GPIO_PIN_0 1
#define ENABLE 1
#define DISABLE 0
#define RESET 0
#define GPIO_PIN_SET 1
#define GPIO_PIN_RESET 0
#define GPIO_MODE_OUTPUT_PP 1
#define GPIO_NOPULL 0
#define GPIO_SPEED_FREQ_HIGH 1
#define GPIO_Mode_OUT 1
#define GPIO_OType_PP 1
#define GPIO_PuPd_NOPULL 0
#define GPIO_Speed_50MHz 1
#define RCC_AHB1Periph_GPIOB 1
#define __HAL_RCC_GPIOB_CLK_ENABLE() ((void)0)
typedef struct {unsigned Pin,Mode,Pull,Speed,GPIO_Pin,GPIO_Mode,GPIO_OType,GPIO_PuPd,GPIO_Speed;} GPIO_InitTypeDef;
void HAL_GPIO_WritePin(void *,unsigned,unsigned); void HAL_GPIO_Init(void *,GPIO_InitTypeDef *);
void GPIO_SetBits(void *,unsigned); void GPIO_ResetBits(void *,unsigned);
void GPIO_StructInit(GPIO_InitTypeDef *); void GPIO_Init(void *,GPIO_InitTypeDef *);
void RCC_AHB1PeriphClockCmd(unsigned,unsigned);
typedef int HAL_StatusTypeDef;
#define HAL_OK 0
#define HAL_TIMEOUT 3
#define HAL_ERROR 1
#define I2C_MEMADD_SIZE_16BIT 2
#define I2C_MEMADD_SIZE_8BIT 1
#define SPI_DATASIZE_8BIT 8
#define SPI_MODE_MASTER 1
#define SPI_DIRECTION_2LINES 2
#define SPI_NSS_SOFT 1
#define SPI_FIRSTBIT_MSB 0
typedef struct {void *Instance;} I2C_HandleTypeDef;
typedef struct {unsigned DataSize,Mode,Direction,NSS,FirstBit;} SpiInit;
typedef struct {void *Instance;SpiInit Init;} SPI_HandleTypeDef;
HAL_StatusTypeDef HAL_I2C_Mem_Read(I2C_HandleTypeDef *,uint16_t,uint16_t,uint16_t,uint8_t *,uint16_t,uint32_t);
HAL_StatusTypeDef HAL_I2C_Master_Transmit(I2C_HandleTypeDef *,uint16_t,uint8_t *,uint16_t,uint32_t);
HAL_StatusTypeDef HAL_I2C_Master_Receive(I2C_HandleTypeDef *,uint16_t,uint8_t *,uint16_t,uint32_t);
HAL_StatusTypeDef HAL_SPI_Transmit(SPI_HandleTypeDef *,uint8_t *,uint16_t,uint32_t);
HAL_StatusTypeDef HAL_SPI_TransmitReceive(SPI_HandleTypeDef *,uint8_t *,uint8_t *,uint16_t,uint32_t);
#define SPI_CR1_SPE 1
#define SPI_CR1_MSTR 2
#define SPI_CR1_DFF 4
#define SPI_CR1_LSBFIRST 8
#define SPI_CR1_BIDIMODE 16
#define SPI_CR1_RXONLY 32
#define SPI_I2S_FLAG_TXE 1
#define SPI_I2S_FLAG_RXNE 2
#define SPI_I2S_FLAG_BSY 3
int SPI_I2S_GetFlagStatus(Periph *,uint32_t);
void SPI_I2S_SendData(Periph *,uint16_t); uint16_t SPI_I2S_ReceiveData(Periph *);
#define I2C_CR1_PE 1
#define I2C_SR1_AF 1
#define I2C_SR1_BERR 2
#define I2C_SR1_ARLO 4
#define I2C_SR1_OVR 8
#define I2C_SR2_MSL 1
#define I2C_FLAG_BUSY 1
#define I2C_FLAG_SB 2
#define I2C_FLAG_ADDR 3
#define I2C_FLAG_TXE 4
#define I2C_FLAG_BTF 5
#define I2C_FLAG_RXNE 6
#define I2C_NACKPosition_Current 0
#define I2C_NACKPosition_Next 1
#define I2C_Direction_Transmitter 0
#define I2C_Direction_Receiver 1
int I2C_GetFlagStatus(Periph *,uint32_t);
void I2C_AcknowledgeConfig(Periph *,int); void I2C_NACKPositionConfig(Periph *,int);
void I2C_GenerateSTART(Periph *,int); void I2C_GenerateSTOP(Periph *,int);
void I2C_Send7bitAddress(Periph *,uint8_t,int); void I2C_SendData(Periph *,uint8_t);
uint8_t I2C_ReceiveData(Periph *);
#endif
'''

MOCK_C = r'''
#include "stm32f4xx_hal.h"
#include "kps_stm32_port.h"
#include <assert.h>
#include <string.h>
Debug debug_regs; static Dwt cycles; uint32_t SystemCoreClock=1000000;
Periph i2c_regs={1,0,0},spi_regs={3,0,0};
I2C_HandleTypeDef hi2c1={I2C1}; SPI_HandleTypeDef hspi1={SPI1,{8,1,2,1,0}};
static unsigned irq,ipsr,cs=1; static int status,memcalls,txcalls,rxcalls,starts,stops,received,ack_at;
static uint16_t address,regaddr,regsize; static uint8_t sent[300]; static unsigned sentn;
Dwt *mock_dwt(void) {cycles.CYCCNT+=100;return &cycles;}
void SystemCoreClockUpdate(void) {} unsigned __get_IPSR(void){return ipsr;}
unsigned __get_PRIMASK(void){return irq;} void __disable_irq(void){irq=1;}
void __set_PRIMASK(unsigned v){irq=v;} void __DMB(void){} void __NOP(void){}
void HAL_GPIO_WritePin(void *p,unsigned n,unsigned v){(void)p;(void)n;cs=v;}
void HAL_GPIO_Init(void *p,GPIO_InitTypeDef *g){(void)p;(void)g;}
void GPIO_SetBits(void *p,unsigned n){HAL_GPIO_WritePin(p,n,1);}
void GPIO_ResetBits(void *p,unsigned n){HAL_GPIO_WritePin(p,n,0);}
void GPIO_StructInit(GPIO_InitTypeDef *g){memset(g,0,sizeof(*g));}
void GPIO_Init(void *p,GPIO_InitTypeDef *g){HAL_GPIO_Init(p,g);}
void RCC_AHB1PeriphClockCmd(unsigned p,unsigned v){(void)p;(void)v;}
HAL_StatusTypeDef HAL_I2C_Mem_Read(I2C_HandleTypeDef *h,uint16_t a,uint16_t reg,uint16_t sz,uint8_t *r,uint16_t n,uint32_t ms){
 (void)h;assert(ms);address=a;regaddr=reg;regsize=sz;memcalls++;memset(r,0x5a,n);return status;}
HAL_StatusTypeDef HAL_I2C_Master_Transmit(I2C_HandleTypeDef *h,uint16_t a,uint8_t *t,uint16_t n,uint32_t ms){
 (void)h;assert(ms);address=a;memcpy(sent,t,n);sentn=n;txcalls++;return status;}
HAL_StatusTypeDef HAL_I2C_Master_Receive(I2C_HandleTypeDef *h,uint16_t a,uint8_t *r,uint16_t n,uint32_t ms){
 (void)h;assert(ms);address=a;memset(r,0x5a,n);rxcalls++;return status;}
HAL_StatusTypeDef HAL_SPI_Transmit(SPI_HandleTypeDef *h,uint8_t *t,uint16_t n,uint32_t ms){
 (void)h;assert(ms && !cs);memcpy(sent+sentn,t,n);sentn+=n;return status;}
HAL_StatusTypeDef HAL_SPI_TransmitReceive(SPI_HandleTypeDef *h,uint8_t *t,uint8_t *r,uint16_t n,uint32_t ms){
 (void)h;assert(ms && !cs && n==1 && *t==0xff);*r=0x5a;return status;}
int SPI_I2S_GetFlagStatus(Periph *p,uint32_t f){(void)p;return f==SPI_I2S_FLAG_BSY ? 0 : !status;}
void SPI_I2S_SendData(Periph *p,uint16_t v){(void)p;assert(!cs);sent[sentn++]=(uint8_t)v;}
uint16_t SPI_I2S_ReceiveData(Periph *p){(void)p;return 0x5a;}
int I2C_GetFlagStatus(Periph *p,uint32_t f){assert(!irq);return f==I2C_FLAG_BUSY ? !!p->SR2 : !status;}
void I2C_AcknowledgeConfig(Periph *p,int v){(void)p;if(!v)ack_at=received;}
void I2C_NACKPositionConfig(Periph *p,int v){(void)p;(void)v;}
void I2C_GenerateSTART(Periph *p,int v){(void)v;p->SR2=1;starts++;}
void I2C_GenerateSTOP(Periph *p,int v){(void)v;p->SR2=0;stops++;}
void I2C_Send7bitAddress(Periph *p,uint8_t a,int d){(void)p;(void)d;address=a;}
void I2C_SendData(Periph *p,uint8_t v){(void)p;sent[sentn++]=v;}
uint8_t I2C_ReceiveData(Periph *p){(void)p;received++;return 0x5a;}
int main(void) {
 kps_bus b;uint8_t cmd[2]={0x12,0x34},out[6];unsigned n;
 assert(kps_stm32_init()==0 && cs==1);b=kps_stm32_bus();
 assert(b.lock(b.ctx,100)==0);assert(b.lock(b.ctx,100)==KPS_EIO);b.unlock(b.ctx);
 ipsr=1;assert(b.lock(b.ctx,100)==KPS_EIO);ipsr=0;
 assert(b.i2c(b.ctx,0x50,cmd,2,out,2,100)==0 && address==0xa0 && out[0]==0x5a);
#ifdef TEST_HAL
 assert(memcalls==1 && regaddr==0x1234 && regsize==2);
 assert(b.i2c(b.ctx,0x44,cmd,1,out,1,100)==0 && regaddr==0x12 && regsize==1);
 assert(b.i2c(b.ctx,0x44,cmd,2,0,0,100)==0 && txcalls==1 && sentn==2);
 assert(b.i2c(b.ctx,0x44,0,0,out,6,100)==0 && rxcalls==1);
 (void)n;
#else
 assert(starts==2 && stops==1 && !irq && received==2 && ack_at==0);
 for(n=1;n<=6;n++) {
   received=starts=stops=0;ack_at=-1;
   assert(b.i2c(b.ctx,0x44,0,0,out,n,100)==0);
   assert(received==(int)n && starts==1 && stops==1 && !irq);
   assert(ack_at==(n<=2 ? 0 : (int)n-3));
 }
#endif
 status=3;assert(b.i2c(b.ctx,0x44,0,0,out,6,1)==KPS_ETIMEOUT);assert(!irq);status=0;
 sentn=0;assert(b.spi(b.ctx,cmd,2,0,out,6,100)==0 && cs==1 && out[0]==0x5a);
 status=3;assert(b.spi(b.ctx,cmd,2,0,out,6,1)==KPS_ETIMEOUT && cs==1);
 return 0;
}
'''


def options(**kwargs):
    values=dict(drivers=['sht3x','w25q128jv'],driver_port='auto',driver_i2c='hardware',driver_spi='hardware',
                driver_cs='PB0',driver_scl='PB6',driver_sda='PB7',yes=True,dry_run=False)
    values.update(kwargs); return SimpleNamespace(**values)


class STM32BindingTests(unittest.TestCase):
    def fixture(self,root,hal):
        p=project(root,'USE_HAL_DRIVER' if hal else 'USE_STDPERIPH_DRIVER')
        (root/'User/entry.c').write_text('''I2C_HandleTypeDef hi2c1;
SPI_HandleTypeDef hspi1;
void init(void) { hi2c1.Instance=I2C1; HAL_I2C_Init(&hi2c1);
hspi1.Instance=SPI1; HAL_SPI_Init(&hspi1); }
''' if hal else 'void init(void) { I2C_Init(I2C1,&ic); SPI_Init(SPI1,&sc); }')
        return p

    def test_detect_generate_and_no_user_api_todos(self):
        for hal in (False,True):
            with self.subTest(hal=hal),tempfile.TemporaryDirectory() as td,contextlib.redirect_stdout(io.StringIO()):
                root=Path(td); p=self.fixture(root,hal)
                config=binding(p,options(),m.read_source_text)
                self.assertEqual(config['profile'],'hal' if hal else 'spl')
                self.assertEqual(config['i2c'],'hi2c1' if hal else 'I2C1')
                before=(root/'User/entry.c').read_bytes()
                self.assertTrue(m.run_tasks(p,['device_drivers'],options()))
                source=(root/'KPS/DeviceDrivers/kps_board_port.c').read_text(encoding='utf-8')
                self.assertNotIn('TODO',source); self.assertNotIn('@BUS@',source)
                self.assertIn('kps_stm32_init',source)
                self.assertEqual(before,(root/'User/entry.c').read_bytes())
                self.assertFalse(m.run_tasks(m.KeilProject(p.path),['device_drivers'],options()))

    def test_ambiguity_missing_pins_dma_and_injection_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); p=self.fixture(root,True)
            source=root/'User/entry.c'
            source.write_text(source.read_text()+'\nI2C_HandleTypeDef hi2c2;\nvoid other(void){hi2c2.Instance=I2C2;HAL_I2C_Init(&hi2c2);}')
            with self.assertRaises(m.ToolError): binding(p,options(),m.read_source_text)
            cfg=binding(p,options(driver_i2c_instance='hi2c2'),m.read_source_text)
            self.assertEqual(cfg['i2c'],'hi2c2')
            for overrides in ({'driver_cs':''},{'driver_cs':'PB0);evil('},{'driver_i2c':'dma'},
                              {'driver_spi':'dma'},{'driver_i2c':'software','driver_sda':'PB6'},
                              {'driver_i2c_instance':'not_found'}):
                with self.subTest(overrides=overrides),self.assertRaises(m.ToolError):
                    binding(p,options(**overrides),m.read_source_text)
            source.write_text('/* I2C_HandleTypeDef fake; */\nstatic I2C_HandleTypeDef hidden;\nextern I2C_HandleTypeDef missing;')
            self.assertEqual(discover(p,m.read_source_text)['i2c'],[])
            with self.assertRaises(m.ToolError): binding(p,options(),m.read_source_text)

    def test_real_keil_ac5_sdk_headers(self):
        compiler=Path(os.environ.get('ARMCC','D:/Keil_v5/ARM/ARMCC/Bin/armcc.exe'))
        if not os.environ.get('KPS_DRIVER_HAL_SDK') or not os.environ.get('KPS_DRIVER_SPL_SDK'):
            self.skipTest('Set KPS_DRIVER_HAL_SDK and KPS_DRIVER_SPL_SDK to local F4 SDK fixture roots')
        hal_root=Path(os.environ['KPS_DRIVER_HAL_SDK'])
        spl_root=Path(os.environ['KPS_DRIVER_SPL_SDK'])
        if not compiler.is_file() or not hal_root.is_dir() or not spl_root.is_dir():
            self.skipTest('Real AC5 / F4 SDK fixtures unavailable; not a target compile pass')
        for hal in (False,True):
            sdk=hal_root if hal else spl_root
            if hal:
                includes=[sdk/'Core/Inc',sdk/'Drivers/STM32F4xx_HAL_Driver/Inc',
                          sdk/'Drivers/CMSIS/Include',sdk/'Drivers/CMSIS/Device/ST/STM32F4xx/Include']
                defines=['USE_HAL_DRIVER','STM32F407xx','HAL_I2C_MODULE_ENABLED','HAL_SPI_MODULE_ENABLED']
            else:
                includes=[sdk/'User',sdk/'Libraries/CMSIS',sdk/'Libraries/STM32F4xx_StdPeriph_Driver/inc']
                defines=['USE_STDPERIPH_DRIVER','STM32F40_41xxx']
            for mode in ('hardware','software'):
                with self.subTest(hal=hal,mode=mode),tempfile.TemporaryDirectory() as td:
                    root=Path(td); p=self.fixture(root,hal)
                    cfg=binding(p,options(driver_i2c=mode),m.read_source_text)
                    files=render_pack(['sht3x','w25q128jv'],mode,'hardware',cfg)
                    for name,content in files.items(): (root/name).write_text(content,encoding='utf-8')
                    for name in ('kps_board_port.c','kps_sht3x.c','kps_w25q128jv.c','kps_bus.c'):
                        args=[str(compiler),'--cpu','Cortex-M4','--c99','-O2','-c',str(root/name),'-o',str(root/(name+'.o'))]
                        args+=['-I'+str(i) for i in includes]+['-D'+d for d in defines]
                        result=subprocess.run(args,cwd=root,capture_output=True,text=True,errors='replace')
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        self.assertNotIn('warning:',result.stderr.lower(),result.stderr)

    def test_execute_generated_hal_and_spl_api_bindings(self):
        gcc=Path(os.environ.get('KPS_GCC','C:/MinGW/bin/gcc.exe'))
        if not gcc.is_file(): self.skipTest('GCC unavailable; no host binding execution')
        for hal in (True,False):
            with self.subTest(hal=hal),tempfile.TemporaryDirectory() as td:
                root=Path(td); p=self.fixture(root,hal)
                cfg=binding(p,options(),m.read_source_text)
                files=render_pack(['sht3x','w25q128jv'],'hardware','hardware',cfg)
                for name,text in files.items(): (root/name).write_text(text,encoding='utf-8')
                for name in ('stm32f4xx_hal.h','stm32f4xx_gpio.h','stm32f4xx_rcc.h','stm32f4xx_spi.h','stm32f4xx_i2c.h'):
                    (root/name).write_text(MOCK_H)
                (root/'mock.c').write_text(MOCK_C)
                args=[str(gcc),'-std=c99','-O2','-Wall','-Wextra','-Werror']
                if hal: args+=['-DTEST_HAL']
                args += [str(path) for path in root.glob('*.c')]+['-o',str(root/'mock.exe')]
                result=subprocess.run(args,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                result=subprocess.run([str(root/'mock.exe')],capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)


if __name__=='__main__': unittest.main()
