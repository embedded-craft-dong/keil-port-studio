"""Conservative STM32 board binding. Never infer wiring from MCU identity alone."""
import re
from .errors import ToolError
from .project_layout import active_sources
from .source_patches import _c_code


def discover(proj, reader):
    profiles=set(); devices=set()
    for target in proj.targets:
        device=target.findtext('TargetOption/TargetCommonOption/Device','').upper()
        devices.add(device)
        macros=' '.join(c.findtext('VariousControls/Define','') for c in proj._target_cads(target))
        hal=bool(re.search(r'\bUSE_HAL_DRIVER\b',macros))
        spl=bool(re.search(r'\bUSE_STDPERIPH_DRIVER\b',macros))
        if hal and spl: raise ToolError('HAL/SPL 冲突 / Conflicting HAL/SPL defines')
        profiles.add(('hal' if hal else 'spl' if spl else 'unknown') if device.startswith('STM32F4') else 'unsupported')
    if len(profiles)!=1 or len(devices)!=1:
        raise ToolError('所选 Target 芯片或库不同，请分别生成 / Select targets with the same MCU/library')
    profile=next(iter(profiles),'unsupported')
    # Small active application files only; never inspect backups, sibling projects or SDK trees.
    code=[]
    for path in active_sources(proj):
        if any(part.lower() in ('libraries','drivers','middlewares','kps') for part in path.parts): continue
        if path.is_file() and path.stat().st_size<=1024*1024:
            code.append(_c_code(reader(path)))
    code='\n'.join(code)
    result=dict(profile=profile,device=next(iter(devices),''),i2c=[],spi=[])
    for kind in ('i2c','spi'):
        if profile=='hal':
            # Exclude static handles; an extern reference could not link to them.
            declarations=re.findall(r'(?m)^\s*'+kind.upper()+r'_HandleTypeDef\s+([A-Za-z_]\w*)\s*;',code)
            result[kind]=sorted({name for name in declarations if
                re.search(r'\bHAL_'+kind.upper()+r'_Init\s*\(\s*&\s*'+re.escape(name)+r'\s*\)',code) and
                re.search(r'\b'+re.escape(name)+r'\s*\.\s*Instance\s*=\s*'+kind.upper()+r'[1-6]\s*;',code)})
        elif profile=='spl':
            result[kind]=sorted(set(re.findall(r'\b'+kind.upper()+r'_Init\s*\(\s*('+kind.upper()+r'[1-6])\s*,',code)))
    return result


def _pin(value, label):
    value=(value or '').strip().upper()
    if not re.fullmatch(r'P[A-I](?:[0-9]|1[0-5])',value):
        raise ToolError(label+' 必须选择实际 GPIO（例如 PB6） / Specify a GPIO, e.g. PB6')
    return value


def binding(proj, opts, reader):
    if getattr(opts,'driver_port','generic')=='generic': return None
    if len(proj.targets)!=1:
        raise ToolError('自动端口请一次选择一个 Target，避免硬件配置混用 / Auto binding requires one selected target')
    found=discover(proj,reader)
    if found['profile'] not in ('hal','spl'):
        raise ToolError('自动端口首批支持 STM32F4 HAL/SPL；本工程未能确认。请显式选 generic / '
                        'Auto binding currently requires confirmed STM32F4 HAL/SPL; choose generic otherwise')
    selected=set(opts.drivers)
    need_i2c=bool(selected-{'w25q128jv'}); need_spi='w25q128jv' in selected
    i2c_mode=getattr(opts,'driver_i2c','hardware'); spi_mode=getattr(opts,'driver_spi','hardware')
    if (need_i2c and i2c_mode=='dma') or (need_spi and spi_mode=='dma'):
        raise ToolError('自动 DMA 端口尚未验证，拒绝伪装为可用。请选硬件阻塞/软件 I2C，或 generic DMA 框架 / '
                        'Automatic DMA binding is not validated; use blocking/software or explicit generic DMA')
    found.update(i2c_mode=i2c_mode,spi_mode=spi_mode,need_i2c=need_i2c,need_spi=need_spi)
    for kind,needed in (('i2c',need_i2c and i2c_mode!='software'),('spi',need_spi)):
        requested=getattr(opts,'driver_'+kind+'_instance',None)
        choices=found[kind]
        if needed:
            if not requested and len(choices)==1: requested=choices[0]
            if requested not in choices:
                raise ToolError(kind.upper()+' 实例不明确或未检测到初始化，请选择工程已有实例 / '
                                'Select an existing initialized instance: '+', '.join(choices))
        found[kind]=requested if needed else None
    found['cs']=_pin(getattr(opts,'driver_cs',None),'SPI CS') if need_spi else None
    found['scl']=_pin(getattr(opts,'driver_scl',None),'SCL') if need_i2c and i2c_mode=='software' else None
    found['sda']=_pin(getattr(opts,'driver_sda',None),'SDA') if need_i2c and i2c_mode=='software' else None
    pins=[found[p] for p in ('cs','scl','sda') if found[p]]
    if len(set(pins))!=len(pins): raise ToolError('GPIO 不能重复 / GPIO assignments overlap')
    if set(pins) & {'PA13','PA14'}:
        raise ToolError('自动端口不重配 SWD 调试引脚 PA13/PA14 / SWD pins are reserved')
    return found


COMMON = r'''/* SPDX-License-Identifier: MIT. Generated STM32F4 binding.
 * Initialize existing clocks and hardware buses FIRST, then call kps_stm32_init().
 * Selected CS/software-I2C pins are configured explicitly by that call.
 * No main()/ISR/SysTick replacement. No automatic storage writes.
 * Only task/main context, interrupts enabled. One owner across external bus users.
 * The generated lock rejects concurrent calls (KPS_EIO), never silently interleaves.
 * DWT delay is blocking; it does not change SysTick or disable scheduling.
 */
#include "kps_stm32_port.h"
@INCLUDES@
static kps_board_context context;
static volatile unsigned occupied;
static int initialized;
static uint32_t per_us;
static int elapsed(uint32_t start,uint32_t ms) {
    return (uint32_t)(DWT->CYCCNT-start)/per_us/1000u>=ms;
}
static int delay_us(void *p,uint32_t us) {
    uint32_t start; (void)p;
    if(!per_us || us>1000000u) return KPS_EINVAL;
    start=DWT->CYCCNT;
    while((uint32_t)(DWT->CYCCNT-start)<us*per_us) { __NOP(); }
    return KPS_OK;
}
static int board_delay(void *p,uint32_t ms) {
    int e;
    while(ms--) { e=delay_us(p,1000); if(e) return e; }
    return KPS_OK;
}
static int board_lock(void *p,uint32_t ms) {
    uint32_t saved; (void)p; (void)ms;
    if(!initialized || __get_IPSR() || __get_PRIMASK()) return KPS_EIO;
    saved=__get_PRIMASK(); __disable_irq();
    if(occupied) { __set_PRIMASK(saved); return KPS_EIO; }
    occupied=1; __set_PRIMASK(saved); return KPS_OK;
}
static void board_unlock(void *p) { (void)p; __DMB(); occupied=0; }
@TRANSPORTS@
int kps_stm32_init(void) {
    uint32_t first; unsigned i;
    if(initialized) return KPS_OK;
    if(__get_IPSR() || __get_PRIMASK()) return KPS_EIO;
    SystemCoreClockUpdate();
    if(SystemCoreClock<1000000u || SystemCoreClock>180000000u) return KPS_EINVAL;
    per_us=(SystemCoreClock+999999u)/1000000u;
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk; /* preserve existing cycle counter */
    first=DWT->CYCCNT; for(i=0;i<64;i++) { __NOP(); }
    if(DWT->CYCCNT==first) { per_us=0; return KPS_ENOSYS; }
    @INIT@
    initialized=1; return KPS_OK;
}
kps_bus kps_stm32_bus(void) { return kps_board_bus(&context); }
kps_bus kps_board_bus(kps_board_context *p) {
    kps_bus b;
    b.ctx=p; b.i2c=@I2C@; b.spi=@SPI@;
    b.delay_ms=board_delay; b.lock=board_lock; b.unlock=board_unlock;
    b.timeout_ms=100; return b;
}
'''

HAL_I2C = r'''
extern I2C_HandleTypeDef @BUS@;
static int board_i2c(void *p,uint8_t a,const uint8_t *t,size_t nt,uint8_t *r,size_t nr,uint32_t ms) {
    HAL_StatusTypeDef s; (void)p;
    if(ms>1000 || !ms || nt>65535 || nr>65535 || !@BUS@.Instance) return KPS_EINVAL;
    if(nt && nr) {
        uint16_t reg;
        if(nt!=1 && nt!=2) return KPS_EINVAL;
        reg=nt==2 ? (uint16_t)((uint16_t)t[0]<<8|t[1]) : t[0];
        /* Memory read preserves repeated START for all generated register readers. */
        s=HAL_I2C_Mem_Read(&@BUS@,(uint16_t)(a<<1),reg,
            nt==2 ? I2C_MEMADD_SIZE_16BIT : I2C_MEMADD_SIZE_8BIT,r,(uint16_t)nr,ms);
    } else if(nt) s=HAL_I2C_Master_Transmit(&@BUS@,(uint16_t)(a<<1),(uint8_t *)t,(uint16_t)nt,ms);
    else s=HAL_I2C_Master_Receive(&@BUS@,(uint16_t)(a<<1),r,(uint16_t)nr,ms);
    return s==HAL_OK ? KPS_OK : s==HAL_TIMEOUT ? KPS_ETIMEOUT : KPS_EIO;
}
'''

HAL_SPI = r'''
extern SPI_HandleTypeDef @BUS@;
static int board_spi(void *p,const uint8_t *cmd,size_t nc,const uint8_t *t,uint8_t *r,size_t n,uint32_t ms) {
    HAL_StatusTypeDef s; uint8_t dummy=0xff; size_t i; (void)p;
    if(!ms || ms>1000 || nc>4 || n>256 || @BUS@.Init.DataSize!=SPI_DATASIZE_8BIT ||
       @BUS@.Init.Mode!=SPI_MODE_MASTER || @BUS@.Init.Direction!=SPI_DIRECTION_2LINES ||
       @BUS@.Init.NSS!=SPI_NSS_SOFT || @BUS@.Init.FirstBit!=SPI_FIRSTBIT_MSB) return KPS_EINVAL;
    HAL_GPIO_WritePin(@PORT@,@PIN@,GPIO_PIN_RESET);
    s=HAL_SPI_Transmit(&@BUS@,(uint8_t *)cmd,(uint16_t)nc,ms);
    if(s==HAL_OK && t && n) s=HAL_SPI_Transmit(&@BUS@,(uint8_t *)t,(uint16_t)n,ms);
    /* Per-byte receive avoids stack-sized dummy arrays; choose DMA manually for throughput. */
    for(i=0;s==HAL_OK && r && i<n;i++) s=HAL_SPI_TransmitReceive(&@BUS@,&dummy,r+i,1,ms);
    HAL_GPIO_WritePin(@PORT@,@PIN@,GPIO_PIN_SET);
    return s==HAL_OK ? KPS_OK : s==HAL_TIMEOUT ? KPS_ETIMEOUT : KPS_EIO;
}
'''

SPL_SPI = r'''
static int spi_byte(uint8_t value,uint8_t *out,uint32_t start,uint32_t ms) {
    while(SPI_I2S_GetFlagStatus(@BUS@,SPI_I2S_FLAG_TXE)==RESET) {
        if(elapsed(start,ms)) return KPS_ETIMEOUT;
    }
    SPI_I2S_SendData(@BUS@,value);
    while(SPI_I2S_GetFlagStatus(@BUS@,SPI_I2S_FLAG_RXNE)==RESET) {
        if(elapsed(start,ms)) return KPS_ETIMEOUT;
    }
    *out=(uint8_t)SPI_I2S_ReceiveData(@BUS@); return KPS_OK;
}
static int board_spi(void *p,const uint8_t *cmd,size_t nc,const uint8_t *t,uint8_t *r,size_t n,uint32_t ms) {
    uint32_t start=DWT->CYCCNT; size_t i; uint8_t dummy; int e=0; (void)p;
    if(!ms || ms>1000 || nc>4 || n>256 || !(@BUS@->CR1 & SPI_CR1_SPE) ||
       !(@BUS@->CR1 & SPI_CR1_MSTR) || (@BUS@->CR1 & (SPI_CR1_DFF|SPI_CR1_LSBFIRST|SPI_CR1_BIDIMODE|SPI_CR1_RXONLY))) return KPS_EINVAL;
    GPIO_ResetBits(@PORT@,@PIN@);
    for(i=0;!e && i<nc;i++) e=spi_byte(cmd[i],&dummy,start,ms);
    for(i=0;!e && i<n;i++) { e=spi_byte(t?t[i]:0xff,&dummy,start,ms); if(!e && r) r[i]=dummy; }
    while(!e && SPI_I2S_GetFlagStatus(@BUS@,SPI_I2S_FLAG_BSY)!=RESET) {
        if(elapsed(start,ms)) e=KPS_ETIMEOUT;
    }
    GPIO_SetBits(@PORT@,@PIN@); return e;
}
'''

SPL_I2C = r'''
static int i2c_flag(uint32_t flag,int desired,uint32_t start,uint32_t ms) {
    while((I2C_GetFlagStatus(@BUS@,flag)!=RESET)!=desired) {
        if(@BUS@->SR1 & (I2C_SR1_AF|I2C_SR1_BERR|I2C_SR1_ARLO|I2C_SR1_OVR)) return KPS_EIO;
        if(elapsed(start,ms)) return KPS_ETIMEOUT;
    }
    return KPS_OK;
}
static void clear_addr(void) { volatile uint32_t s=@BUS@->SR1; s=@BUS@->SR2; (void)s; }
static int board_i2c(void *p,uint8_t a,const uint8_t *t,size_t nt,uint8_t *r,size_t nr,uint32_t ms) {
    uint32_t start=DWT->CYCCNT,saved; size_t i; int e=0; (void)p;
    if(!ms || ms>1000 || !(@BUS@->CR1 & I2C_CR1_PE)) return KPS_EINVAL;
    @BUS@->SR1 &= ~(I2C_SR1_AF|I2C_SR1_BERR|I2C_SR1_ARLO|I2C_SR1_OVR);
    e=i2c_flag(I2C_FLAG_BUSY,0,start,ms); if(e) return e;
    I2C_AcknowledgeConfig(@BUS@,ENABLE); I2C_NACKPositionConfig(@BUS@,I2C_NACKPosition_Current);
    if(nt) {
        I2C_GenerateSTART(@BUS@,ENABLE);
        e=i2c_flag(I2C_FLAG_SB,1,start,ms); if(e) goto done;
        I2C_Send7bitAddress(@BUS@,(uint8_t)(a<<1),I2C_Direction_Transmitter);
        e=i2c_flag(I2C_FLAG_ADDR,1,start,ms); if(e) goto done; clear_addr();
        for(i=0;i<nt;i++) {
            e=i2c_flag(I2C_FLAG_TXE,1,start,ms); if(e) goto done;
            I2C_SendData(@BUS@,t[i]);
        }
        e=i2c_flag(I2C_FLAG_BTF,1,start,ms); if(e) goto done;
    }
    if(nr) {
        I2C_GenerateSTART(@BUS@,ENABLE);
        e=i2c_flag(I2C_FLAG_SB,1,start,ms); if(e) goto done;
        I2C_Send7bitAddress(@BUS@,(uint8_t)(a<<1),I2C_Direction_Receiver);
        e=i2c_flag(I2C_FLAG_ADDR,1,start,ms); if(e) goto done;
        /* F4 legacy I2C 1/2/3-byte receive sequence. Only short register sequences
         * are atomic; never wait on a peripheral flag with interrupts disabled. */
        saved=__get_PRIMASK(); __disable_irq();
        if(nr<=2) I2C_AcknowledgeConfig(@BUS@,DISABLE);
        if(nr==2) I2C_NACKPositionConfig(@BUS@,I2C_NACKPosition_Next);
        clear_addr(); if(nr==1) I2C_GenerateSTOP(@BUS@,ENABLE);
        __set_PRIMASK(saved);
        while(nr) {
            if(nr==1) {
                e=i2c_flag(I2C_FLAG_RXNE,1,start,ms); if(e) goto done;
                *r++=I2C_ReceiveData(@BUS@); nr--;
            } else if(nr==2) {
                e=i2c_flag(I2C_FLAG_BTF,1,start,ms); if(e) goto done;
                saved=__get_PRIMASK(); __disable_irq();
                I2C_GenerateSTOP(@BUS@,ENABLE); *r++=I2C_ReceiveData(@BUS@); *r++=I2C_ReceiveData(@BUS@);
                __set_PRIMASK(saved); nr=0;
            } else if(nr==3) {
                e=i2c_flag(I2C_FLAG_BTF,1,start,ms); if(e) goto done;
                saved=__get_PRIMASK(); __disable_irq();
                I2C_AcknowledgeConfig(@BUS@,DISABLE); *r++=I2C_ReceiveData(@BUS@);
                __set_PRIMASK(saved); nr--;
            } else {
                e=i2c_flag(I2C_FLAG_RXNE,1,start,ms); if(e) goto done;
                *r++=I2C_ReceiveData(@BUS@); nr--;
            }
        }
    } else I2C_GenerateSTOP(@BUS@,ENABLE);
    e=i2c_flag(I2C_FLAG_BUSY,0,start,ms);
done:
    if(e && (@BUS@->SR2 & I2C_SR2_MSL)) I2C_GenerateSTOP(@BUS@,ENABLE);
    I2C_AcknowledgeConfig(@BUS@,ENABLE); I2C_NACKPositionConfig(@BUS@,I2C_NACKPosition_Current);
    @BUS@->SR1 &= ~(I2C_SR1_AF|I2C_SR1_BERR|I2C_SR1_ARLO|I2C_SR1_OVR);
    return e;
}
'''


def render_stm32(config):
    hal=config['profile']=='hal'
    includes=['#include "stm32f4xx_hal.h"'] if hal else [
        '#include "stm32f4xx_gpio.h"','#include "stm32f4xx_rcc.h"',
        '#include "stm32f4xx_i2c.h"','#include "stm32f4xx_spi.h"']
    parts=[]; init=[]
    def pin_parts(pin): return 'GPIO'+pin[1], ('GPIO_PIN_' if hal else 'GPIO_Pin_')+pin[2:]
    for role in ('cs','scl','sda'):
        pin=config.get(role)
        if not pin: continue
        port,bit=pin_parts(pin)
        if hal:
            init.append('{ GPIO_InitTypeDef g={0}; __HAL_RCC_GPIO'+pin[1]+'_CLK_ENABLE(); '
                'HAL_GPIO_WritePin('+port+','+bit+',GPIO_PIN_SET); g.Pin='+bit+'; '
                'g.Mode='+('GPIO_MODE_OUTPUT_PP' if role=='cs' else 'GPIO_MODE_OUTPUT_OD')+'; '
                'g.Pull=GPIO_NOPULL; g.Speed=GPIO_SPEED_FREQ_HIGH; HAL_GPIO_Init('+port+',&g); }')
        else:
            init.append('{ GPIO_InitTypeDef g; RCC_AHB1PeriphClockCmd(RCC_AHB1Periph_GPIO'+pin[1]+',ENABLE); '
                'GPIO_SetBits('+port+','+bit+'); GPIO_StructInit(&g); g.GPIO_Pin='+bit+'; '
                'g.GPIO_Mode=GPIO_Mode_OUT; g.GPIO_OType='+('GPIO_OType_PP' if role=='cs' else 'GPIO_OType_OD')+'; '
                'g.GPIO_PuPd=GPIO_PuPd_NOPULL; g.GPIO_Speed=GPIO_Speed_50MHz; GPIO_Init('+port+',&g); }')
        if role!='cs':
            write=('HAL_GPIO_WritePin('+port+','+bit+',v?GPIO_PIN_SET:GPIO_PIN_RESET);' if hal else
                   'GPIO_WriteBit('+port+','+bit+',v?Bit_SET:Bit_RESET);')
            read=('HAL_GPIO_ReadPin' if hal else 'GPIO_ReadInputDataBit')+'('+port+','+bit+')'
            parts.append('static int set_'+role+'(void *p,int v) { (void)p; '+write+' return 0; }\n'
                         'static int read_'+role+'(void *p) { (void)p; return '+read+'!=0; }\n')
    if config['need_i2c']:
        if config['i2c_mode']=='software':
            parts.append('static int board_i2c(void *p,uint8_t a,const uint8_t *t,size_t nt,uint8_t *r,size_t nr,uint32_t ms) {\n'
                         'return kps_soft_i2c_transfer(&((kps_board_context *)p)->i2c_soft,a,t,nt,r,nr,ms); }\n')
            init.append('context.i2c_soft.user=0; context.i2c_soft.scl=set_scl; context.i2c_soft.sda=set_sda; '
                        'context.i2c_soft.read_scl=read_scl; context.i2c_soft.read_sda=read_sda; '
                        'context.i2c_soft.delay_us=delay_us; context.i2c_soft.half_period_us=5;')
        else: parts.append((HAL_I2C if hal else SPL_I2C).replace('@BUS@',config['i2c']))
    if config['need_spi']:
        port,bit=pin_parts(config['cs'])
        parts.append((HAL_SPI if hal else SPL_SPI).replace('@BUS@',config['spi']).replace('@PORT@',port).replace('@PIN@',bit))
    code=COMMON.replace('@INCLUDES@','\n'.join(includes)).replace('@TRANSPORTS@','\n'.join(parts))
    code=code.replace('@INIT@','\n    '.join(init)).replace('@I2C@','board_i2c' if config['need_i2c'] else '0')
    code=code.replace('@SPI@','board_spi' if config['need_spi'] else '0')
    if hal or not (config['need_spi'] or (config['need_i2c'] and config['i2c_mode']=='hardware')):
        code=re.sub(r'static int elapsed\(.*?\n}\n','',code,count=1,flags=re.S)
    header='''#ifndef KPS_STM32_PORT_H
#define KPS_STM32_PORT_H
#include "kps_board_port.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Call once after system/bus initialization, before tasks start using this bus.
 * kps_stm32_init() configures ONLY selected CS / software-I2C pins and DWT.
 * Then: kps_bus bus=kps_stm32_bus(); check each device API's return value.
 */
int kps_stm32_init(void);
kps_bus kps_stm32_bus(void);
#ifdef __cplusplus
}
#endif
#endif
'''
    return {'kps_board_port.c':code,'kps_stm32_port.h':header}
