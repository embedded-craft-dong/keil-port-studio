"""Selectable software, synchronous hardware and DMA-completion adapters."""

SOFT_H = r'''#ifndef KPS_SOFT_I2C_H
#define KPS_SOFT_I2C_H
#include "kps_bus.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct {
    void *user;
    /* TODO: 1 = release open-drain line, NEVER drive push-pull HIGH.
     * GPIO setters return KPS_OK/error; readers return 0/1, negative on error.
     * Both lines need pull-ups to a voltage safe for ALL connected devices.
     */
    int (*scl)(void *, int);
    int (*sda)(void *, int);
    int (*read_scl)(void *);
    int (*read_sda)(void *);
    int (*delay_us)(void *, uint32_t); /* calibrated minimum delay; not an empty loop */
    unsigned half_period_us; /* 5..1000: <=100kHz; OS scheduling may slow it down */
} kps_soft_i2c;
int kps_soft_i2c_transfer(kps_soft_i2c *, uint8_t, const uint8_t *, size_t,
                          uint8_t *, size_t, uint32_t);
#ifdef __cplusplus
}
#endif
#endif
'''
SOFT_C = r'''/* SPDX-License-Identifier: MIT. Single master only. No automatic bus recovery pulses. */
#include "kps_soft_i2c.h"
typedef struct { kps_soft_i2c *p; uint32_t budget; } wire;
static int pause_wire(wire *w) {
    unsigned us=w->p->half_period_us;
    if(w->budget<us) return KPS_ETIMEOUT;
    w->budget-=us; return w->p->delay_us(w->p->user,us);
}
static int clk(wire *w,int high) {
    int e=w->p->scl(w->p->user,high); if(e) return e;
    if(high) {
        for(;;) {
            e=w->p->read_scl(w->p->user); if(e<0) return e;
            if(e) break;
            e=pause_wire(w); if(e) return e;
        }
    }
    return pause_wire(w);
}
static int dat(wire *w,int high) { return w->p->sda(w->p->user,high); }
static int start(wire *w) {
    int e=dat(w,1); if(e) return e;
    e=pause_wire(w); if(e) return e;
    e=clk(w,1); if(e) return e;
    e=w->p->read_sda(w->p->user); if(e<0) return e;
    if(!e) return KPS_EIO; /* busy/stuck bus; do not guess recovery */
    e=dat(w,0); if(e) return e;
    e=pause_wire(w); if(e) return e;
    return clk(w,0);
}
static int stop(wire *w) {
    int e=dat(w,0); if(e) return e;
    e=clk(w,1); if(e) return e;
    e=dat(w,1); if(e) return e;
    return pause_wire(w);
}
static int put(wire *w,uint8_t value) {
    unsigned i; int e,ack;
    for(i=0;i<8;i++) {
        e=dat(w,!!(value&0x80)); if(e) return e;
        e=pause_wire(w); if(e) return e;
        e=clk(w,1); if(e) return e;
        e=clk(w,0); if(e) return e;
        value=(uint8_t)(value<<1);
    }
    e=dat(w,1); if(e) return e;
    e=pause_wire(w); if(e) return e;
    e=clk(w,1); if(e) return e;
    ack=w->p->read_sda(w->p->user);
    e=clk(w,0); if(e) return e;
    return ack<0 ? ack : (ack ? KPS_EIO : KPS_OK);
}
static int get(wire *w,uint8_t *value,int last) {
    unsigned i; uint8_t v=0; int e;
    e=dat(w,1); if(e) return e;
    for(i=0;i<8;i++) {
        e=clk(w,1); if(e) return e;
        e=w->p->read_sda(w->p->user); if(e<0) return e;
        v=(uint8_t)((v<<1)|(e?1:0));
        e=clk(w,0); if(e) return e;
    }
    e=dat(w,last); if(e) return e; /* ACK except final byte: NACK */
    e=pause_wire(w); if(e) return e;
    e=clk(w,1); if(e) return e;
    e=clk(w,0); if(e) return e;
    e=dat(w,1); if(e) return e;
    *value=v; return KPS_OK;
}
int kps_soft_i2c_transfer(kps_soft_i2c *p,uint8_t a,const uint8_t *t,size_t nt,uint8_t *r,size_t nr,uint32_t ms) {
    wire w; size_t i; int e=KPS_OK,cleanup;
    if(!p || !p->scl || !p->sda || !p->read_scl || !p->read_sda || !p->delay_us ||
       p->half_period_us<5 || p->half_period_us>1000 || !ms || ms>60000 ||
       a>0x7f || (!nt && !nr) || (nt && !t) || (nr && !r)) return KPS_EINVAL;
    w.p=p; w.budget=ms*1000u;
    e=start(&w); if(e) goto done;
    if(nt) {
        e=put(&w,(uint8_t)(a<<1)); if(e) goto done;
        for(i=0;i<nt;i++) { e=put(&w,t[i]); if(e) goto done; }
    }
    if(nr) {
        if(nt) { e=start(&w); if(e) goto done; }
        e=put(&w,(uint8_t)((a<<1)|1)); if(e) goto done;
        for(i=0;i<nr;i++) { e=get(&w,r+i,i==nr-1); if(e) goto done; }
    }
done:
    /* Always attempt STOP with a separate small bounded cleanup budget. */
    w.budget=2000; cleanup=stop(&w); if(!e) e=cleanup;
    cleanup=p->sda(p->user,1); if(!e) e=cleanup;
    cleanup=p->scl(p->user,1); if(!e) e=cleanup;
    return e;
}
'''

DMA_H = r'''#ifndef KPS_DMA_H
#define KPS_DMA_H
#include "kps_bus.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Explicitly owned, persistent DMA-safe bounce memory supplied by the board.
 * tx/rx: separate >=288-byte buffers, correctly aligned for your DMA/cache,
 * valid for the ENTIRE lifetime of this link, including timeout/quarantine.
 * Never use automatic/stack arrays. On F4 avoid CCM (DMA cannot access it).
 * On cached MCUs provide non-cacheable RAM or correct clean/invalidate hooks.
 */
typedef struct {
    void *user;
    uint8_t *tx, *rx;
    size_t tx_capacity, rx_capacity;
    /* spi=0: I2C address in address, tx followed by repeated START+rx.
     * spi=1: tx contains command+write data; rx is a subsequent read phase
     * clocked with dummy 0xFF. Hold CS across phases; wait/abort releases it.
     * start must return promptly. May start hardware even when returning error.
     */
    int (*start)(void *, int spi, uint8_t address, const uint8_t *, size_t, uint8_t *, size_t);
    /* TODO: wait for ALL bus phases and DMA/IRQ completion, not just TX complete.
     * No unbounded spin. RTOS event/semaphore or bounded bare-metal polling.
     * On success: peripheral idle, RX cache coherent, CS released / STOP sent.
     */
    int (*wait)(void *, uint32_t);
    /* TODO: synchronously abort DMA/peripheral and quiesce ALL callbacks/IRQs;
     * return 0 only once buffers are unused. On failure link is quarantined.
     * Never free/reuse its buffers while quarantined. Reset hardware first.
     */
    int (*abort_quiesce)(void *, uint32_t);
    int quarantined;
} kps_dma;
int kps_dma_i2c(kps_dma *,uint8_t,const uint8_t *,size_t,uint8_t *,size_t,uint32_t);
int kps_dma_spi(kps_dma *,const uint8_t *,size_t,const uint8_t *,uint8_t *,size_t,uint32_t);
#ifdef __cplusplus
}
#endif
#endif
'''
DMA_C = r'''/* SPDX-License-Identifier: MIT. DMA completion adapter, NOT fire-and-forget. */
#include "kps_dma.h"
#include <string.h>
static int transfer(kps_dma *p,int spi,uint8_t a,const uint8_t *cmd,size_t nc,
                    const uint8_t *t,size_t nt,uint8_t *r,size_t nr,uint32_t ms) {
    int e;
    if(!p || !p->start || !p->wait || !p->abort_quiesce || !p->tx || !p->rx ||
       p->tx==p->rx || !ms || ms>60000 || nc>4 || nt>272 || nr>256 ||
       nc+nt>p->tx_capacity || nr>p->rx_capacity || (nc && !cmd) || (nt && !t) || (nr && !r)) return KPS_EINVAL;
    if((uintptr_t)p->tx < (uintptr_t)p->rx ?
       (uintptr_t)p->rx-(uintptr_t)p->tx < p->tx_capacity :
       (uintptr_t)p->tx-(uintptr_t)p->rx < p->rx_capacity) return KPS_EINVAL;
    if(p->quarantined) return KPS_EIO;
    if(nc) memcpy(p->tx,cmd,nc);
    if(nt) memcpy(p->tx+nc,t,nt);
    e=p->start(p->user,spi,a,p->tx,nc+nt,p->rx,nr);
    if(!e) e=p->wait(p->user,ms);
    if(e) {
        if(p->abort_quiesce(p->user,ms)) p->quarantined=1;
        return e; /* Never expose stale/partial RX as a successful response. */
    }
    if(nr) memcpy(r,p->rx,nr);
    return KPS_OK;
}
int kps_dma_i2c(kps_dma *p,uint8_t a,const uint8_t *t,size_t nt,uint8_t *r,size_t nr,uint32_t ms) {
    if(a>0x7f || (!nt && !nr)) return KPS_EINVAL;
    return transfer(p,0,a,0,0,t,nt,r,nr,ms);
}
int kps_dma_spi(kps_dma *p,const uint8_t *c,size_t nc,const uint8_t *t,uint8_t *r,size_t n,uint32_t ms) {
    if(!nc || (n && ((!t && !r) || (t && r)))) return KPS_EINVAL;
    return transfer(p,1,0,c,nc,t,t?n:0,r,r?n:0,ms);
}
'''


def render_port(i2c_mode, spi_mode):
    files = {}
    if i2c_mode == 'software':
        files.update({'kps_soft_i2c.h': SOFT_H, 'kps_soft_i2c.c': SOFT_C})
    if 'dma' in (i2c_mode, spi_mode):
        files.update({'kps_dma.h': DMA_H, 'kps_dma.c': DMA_C})
    includes = ('#include "kps_soft_i2c.h"\n' if i2c_mode == 'software' else '')
    includes += ('#include "kps_dma.h"\n' if 'dma' in (i2c_mode, spi_mode) else '')
    fields = ('    kps_soft_i2c i2c_soft;\n' if i2c_mode == 'software' else '')
    fields += ('    kps_dma i2c_dma;\n' if i2c_mode == 'dma' else '')
    fields += ('    kps_dma spi_dma;\n' if spi_mode == 'dma' else '')
    files['kps_board_port.h'] = ('#ifndef KPS_BOARD_PORT_H\n#define KPS_BOARD_PORT_H\n#include "kps_bus.h"\n'
        + includes + '#ifdef __cplusplus\nextern "C" {\n#endif\n'
        'typedef struct { void *user;\n' + fields + '} kps_board_context;\n'
        '/* Zero-initialize a persistent context; configure selected transport hooks. */\n'
        'kps_bus kps_board_bus(kps_board_context *context);\n'
        '#ifdef __cplusplus\n}\n#endif\n#endif\n')
    i2c_body = {
        'software': 'return kps_soft_i2c_transfer(&p->i2c_soft,a,t,nt,r,nr,ms);',
        'dma': 'return kps_dma_i2c(&p->i2c_dma,a,t,nt,r,nr,ms);',
        'hardware': '/* TODO I2C_HW: map your blocking HAL/SPL/other library; address is 7-bit.\n'
                    '     * HAL APIs may require (a << 1); shift only HERE, exactly once.\n'
                    '     * tx+rx MUST use repeated START; no STOP between pointer and read. */\n'
                    '    (void)p; (void)a; (void)t; (void)nt; (void)r; (void)nr; (void)ms;\n'
                    '    return KPS_ENOSYS;'
    }[i2c_mode]
    spi_body = ('return kps_dma_spi(&p->spi_dma,c,nc,t,r,n,ms);' if spi_mode == 'dma' else
                '/* TODO SPI_HW: CS low -> command -> data -> wait idle -> CS high.\n'
                '     * Release CS even on error. Full-duplex receive clocks dummy 0xFF. */\n'
                '    (void)p; (void)c; (void)nc; (void)t; (void)r; (void)n; (void)ms;\n'
                '    return KPS_ENOSYS;')
    files['kps_board_port.c'] = r'''/* SPDX-License-Identifier: MIT
 * USER EDIT AREA / 用户填写区域
 * 1. Read DRIVER_GUIDE.md. Init clocks/GPIO/bus separately before using drivers.
 * 2. Fill the selected callbacks below; unfinished callbacks fail, not pretend success.
 * 3. No automatic init/read/write is injected into main.c, ISR or scheduler.
 * 4. 所有 TODO 完成后先做只读测试；写入/擦除由用户明确调用。
 */
#include "kps_board_port.h"
static int board_i2c(void *ctx,uint8_t a,const uint8_t *t,size_t nt,uint8_t *r,size_t nr,uint32_t ms) {
    kps_board_context *p=(kps_board_context *)ctx;
    if(!p) return KPS_EINVAL;
    @I2C@
}
static int board_spi(void *ctx,const uint8_t *c,size_t nc,const uint8_t *t,uint8_t *r,size_t n,uint32_t ms) {
    kps_board_context *p=(kps_board_context *)ctx;
    if(!p) return KPS_EINVAL;
    @SPI@
}
static int board_delay(void *ctx,uint32_t ms) {
    /* TODO DELAY_MS: bare metal calibrated delay or RTOS delay rounded UP.
     * 不可空实现。RTOS tick换算向上取整；调度前不可调用任务睡眠。 */
    (void)ctx; (void)ms; return KPS_ENOSYS;
}
static int board_lock(void *ctx,uint32_t ms) {
    /* TODO LOCK: shared mutex for ALL users of the same physical bus.
     * 单线程裸机可明确改成 return KPS_OK；RTOS应实现有限时互斥锁。
     * Never disable interrupts for the whole operation (BH1750 waits 180ms).
     * Do not lock again inside transfer hooks; these calls already hold it. */
    (void)ctx; (void)ms; return KPS_ENOSYS;
}
static void board_unlock(void *ctx) {
    /* TODO UNLOCK: release the same mutex. Single-thread bare metal: no-op. */
    (void)ctx;
}
kps_bus kps_board_bus(kps_board_context *context) {
    kps_bus b;
    b.ctx=context; b.i2c=board_i2c; b.spi=board_spi;
    b.delay_ms=board_delay; b.lock=board_lock; b.unlock=board_unlock;
    b.timeout_ms=100; return b;
}
'''.replace('@I2C@', i2c_body).replace('@SPI@', spi_body)
    return files
