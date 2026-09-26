"""Original, bus-independent C driver generator. No downloads or board guessing."""
from .errors import ToolError

CATALOG = {
    'w25q128jv': ('W25Q128JV', 'SPI NOR, 16 MiB / 256 B page / 4 KiB sector',
        'https://www.winbond.com/resource-files/w25q128jv%20revg%2004082019%20plus.pdf'),
    '24lc02b': ('24LC02B', 'I2C EEPROM, 256 B / 8 B page / 1-byte address',
        'https://ww1.microchip.com/downloads/en/DeviceDoc/21709c.pdf'),
    '24lc256': ('24LC256', 'I2C EEPROM, 32 KiB / 64 B page / 2-byte address',
        'https://ww1.microchip.com/downloads/en/devicedoc/21203m.pdf'),
    'sht3x': ('SHT30/31/35-DIS', 'I2C temperature / humidity, CRC8, single shot',
        'https://sensirion.com/media/documents/213E6A3B/63A5A569/Datasheet_SHT3x_DIS.pdf'),
    'bh1750': ('BH1750FVI', 'I2C light, one-shot H resolution, MTreg=69',
        'https://www.mouser.com/datasheet/2/348/bh1750fvi-e-186247.pdf'),
    'ads1115': ('ADS1115', 'I2C ADC, single-ended AIN0..3, 128 SPS, +/-4.096 V',
        'https://www.ti.com/lit/ds/symlink/ads1115.pdf'),
    'ssd1306': ('SSD1306', 'I2C OLED 128x64 / 128x32, internal charge pump',
        'https://cdn.sparkfun.com/assets/d/a/a/b/c/SSD1306_datasheet.pdf'),
}
I2C_MODES = ('hardware', 'software', 'dma')
SPI_MODES = ('hardware', 'dma')

BUS_H = r'''/* SPDX-License-Identifier: MIT
 * KPS original drivers. Read DRIVER_GUIDE.md before connecting hardware.
 * Fill board callbacks, not this protocol layer. No HAL/SPL/RTOS dependency.
 */
#ifndef KPS_BUS_H
#define KPS_BUS_H
#include <stdint.h>
#include <stddef.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { KPS_OK=0, KPS_EINVAL=-1, KPS_EIO=-2, KPS_ETIMEOUT=-3,
       KPS_ECRC=-4, KPS_EDEVICE=-5, KPS_ENOSYS=-6, KPS_EVERIFY=-7 };
typedef struct {
    void *ctx;
    /* 7-bit address (NOT shifted). tx+rx: repeated START, no intermediate STOP.
     * tx only / rx only: STOP at end. All bytes complete before return.
     * Implement finite timeout; map HAL/SPL status to KPS_* (0 only on success).
     */
    int (*i2c)(void *, uint8_t, const uint8_t *, size_t, uint8_t *, size_t, uint32_t);
    /* CS low across command AND data, MSB first; rx clocks use dummy 0xFF.
     * tx/rx data are mutually exclusive. CS high on ALL exits incl. errors.
     * Standard SPI only; mode 0 or 3. Never leave DMA accessing caller buffers.
     */
    int (*spi)(void *, const uint8_t *, size_t, const uint8_t *, uint8_t *, size_t, uint32_t);
    int (*delay_ms)(void *, uint32_t); /* delay >= requested; RTOS sleep allowed */
    int (*lock)(void *, uint32_t);    /* entire device operation, finite timeout */
    void (*unlock)(void *);
    uint32_t timeout_ms;             /* per bus transfer/lock, 1..60000 */
} kps_bus;
/* Internal helpers are public only to share across generated translation units. */
int kps_begin(kps_bus *b);
int kps_end(kps_bus *b, int result);
int kps_i2c(kps_bus *b, uint8_t a, const uint8_t *t, size_t nt, uint8_t *r, size_t nr);
int kps_spi(kps_bus *b, const uint8_t *c, size_t nc, const uint8_t *t, uint8_t *r, size_t n);
int kps_delay(kps_bus *b, uint32_t ms);
#ifdef __cplusplus
}
#endif
#endif
'''
BUS_C = r'''#include "kps_bus.h"
int kps_begin(kps_bus *b) {
    if (!b || !b->lock || !b->unlock || !b->delay_ms ||
        !b->timeout_ms || b->timeout_ms > 60000u) return KPS_EINVAL;
    return b->lock(b->ctx, b->timeout_ms);
}
int kps_end(kps_bus *b, int result) { b->unlock(b->ctx); return result; }
int kps_i2c(kps_bus *b, uint8_t a, const uint8_t *t, size_t nt, uint8_t *r, size_t nr) {
    if (!b->i2c) return KPS_ENOSYS;
    if (a > 0x7fu || (nt && !t) || (nr && !r)) return KPS_EINVAL;
    return b->i2c(b->ctx, a, t, nt, r, nr, b->timeout_ms);
}
int kps_spi(kps_bus *b, const uint8_t *c, size_t nc, const uint8_t *t, uint8_t *r, size_t n) {
    if (!b->spi) return KPS_ENOSYS;
    return b->spi(b->ctx, c, nc, t, r, n, b->timeout_ms);
}
int kps_delay(kps_bus *b, uint32_t ms) { return b->delay_ms(b->ctx, ms); }
'''

# Public wrappers acquire/release the same bus mutex for the whole operation,
# including command/conversion/read and EEPROM page splitting.
API = {
    'w25q128jv': [
        ('kps_w25q_probe', ''),
        ('kps_w25q_read', 'uint32_t offset, uint8_t *data, size_t length'),
        ('kps_w25q_program', 'uint32_t offset, const uint8_t *data, size_t length'),
        ('kps_w25q_erase4k', 'uint32_t offset')],
    'eeprom': [
        ('kps_eeprom_read', 'unsigned model, uint8_t addr7, uint32_t offset, uint8_t *data, size_t length'),
        ('kps_eeprom_write', 'unsigned model, uint8_t addr7, uint32_t offset, const uint8_t *data, size_t length')],
    'sht3x': [('kps_sht3x_read', 'uint8_t addr7, int32_t *temperature_mC, uint32_t *humidity_mpercent')],
    'bh1750': [('kps_bh1750_read', 'uint8_t addr7, uint32_t *millilux')],
    'ads1115': [('kps_ads1115_read', 'uint8_t addr7, unsigned channel, int32_t *microvolts')],
    'ssd1306': [('kps_ssd1306_init', 'uint8_t addr7, unsigned height'),
                ('kps_ssd1306_flush', 'uint8_t addr7, unsigned height, const uint8_t *pixels, size_t length')],
}

SOURCES = {}
SOURCES['w25q128jv'] = r'''
static int status(kps_bus *b, uint8_t *s) {
    const uint8_t cmd=0x05; return kps_spi(b, &cmd, 1, 0, s, 1);
}
static int ready(kps_bus *b, unsigned attempts) {
    uint8_t s; int e;
    while (attempts--) {
        e=status(b,&s); if (e) return e;
        if (!(s&1)) return KPS_OK;
        e=kps_delay(b,1); if(e) return e;
    }
    return KPS_ETIMEOUT;
}
static int kps_w25q_probe_impl(kps_bus *b) {
    const uint8_t cmd=0x9f; uint8_t id[3]; int e=kps_spi(b,&cmd,1,0,id,3);
    if(e) return e;
    return (id[0]==0xef && id[1]==0x40 && id[2]==0x18) ? KPS_OK : KPS_EDEVICE;
}
static int range(uint32_t a, size_t n) {
    return a <= 0x1000000u && n <= 0x1000000u-a;
}
static int addressed(kps_bus *b, uint8_t op, uint32_t a, const uint8_t *t, uint8_t *r, size_t n) {
    uint8_t c[4]; c[0]=op; c[1]=(uint8_t)(a>>16); c[2]=(uint8_t)(a>>8); c[3]=(uint8_t)a;
    return kps_spi(b,c,4,t,r,n);
}
static int enable_write(kps_bus *b) {
    uint8_t s; const uint8_t cmd=6; int e=kps_spi(b,&cmd,1,0,0,0);
    if(e) return e;
    e=status(b,&s); if(e) return e;
    return (s&2) ? KPS_OK : KPS_EVERIFY;
}
static int kps_w25q_read_impl(kps_bus *b, uint32_t offset, uint8_t *data, size_t length) {
    size_t n; int e;
    if(!range(offset,length) || (length && !data)) return KPS_EINVAL;
    e=kps_w25q_probe_impl(b); if(e) return e;
    e=ready(b,500); if(e) return e;
    while(length) {
        n=length>256 ? 256 : length;
        e=addressed(b,3,offset,0,data,n); if(e) return e;
        data+=n; offset+=(uint32_t)n; length-=n;
    }
    return KPS_OK;
}
/* Explicit program ONLY. Caller owns erase policy and data-loss authorization.
 * Reject 0->1 transitions; verify each written page. Earlier pages can remain
 * written if a later page fails: this is not a power-fail atomic transaction.
 */
static int kps_w25q_program_impl(kps_bus *b, uint32_t offset, const uint8_t *data, size_t length) {
    uint8_t verify[256]; size_t n,i; int e;
    if(!range(offset,length) || (length && !data)) return KPS_EINVAL;
    e=kps_w25q_probe_impl(b); if(e) return e;
    e=ready(b,500); if(e) return e;
    while(length) {
        n=256u-(offset%256u); if(n>length) n=length;
        e=addressed(b,3,offset,0,verify,n); if(e) return e;
        for(i=0;i<n;i++) if((verify[i]&data[i])!=data[i]) return KPS_EVERIFY;
        e=enable_write(b); if(e) return e;
        e=addressed(b,2,offset,data,0,n); if(e) return e;
        e=ready(b,10); if(e) return e;
        e=addressed(b,3,offset,0,verify,n); if(e) return e;
        for(i=0;i<n;i++) if(verify[i]!=data[i]) return KPS_EVERIFY;
        data+=n; offset+=(uint32_t)n; length-=n;
    }
    return KPS_OK;
}
static int kps_w25q_erase4k_impl(kps_bus *b, uint32_t offset) {
    uint8_t verify[256]; unsigned chunk,i; int e;
    if((offset%4096u) || !range(offset,4096)) return KPS_EINVAL;
    e=kps_w25q_probe_impl(b); if(e) return e;
    e=ready(b,500); if(e) return e;
    e=enable_write(b); if(e) return e;
    e=addressed(b,0x20,offset,0,0,0); if(e) return e;
    e=ready(b,500); if(e) return e;
    for(chunk=0;chunk<16;chunk++) {
        e=addressed(b,3,offset+chunk*256u,0,verify,256); if(e) return e;
        for(i=0;i<256;i++) if(verify[i]!=0xff) return KPS_EVERIFY;
    }
    return KPS_OK;
}
'''
SOURCES['eeprom'] = r'''
static int geometry(unsigned model, uint8_t a, uint32_t offset, size_t length,
                    unsigned *page, unsigned *bytes) {
    uint32_t capacity;
    if(model==2) { capacity=256; *page=8; *bytes=1; }
    else if(model==256) { capacity=32768; *page=64; *bytes=2; }
    else return KPS_EDEVICE; /* Do NOT guess 24C04/08/16 bank-select addressing. */
    if(a<0x50 || a>0x57 || offset>capacity || length>capacity-offset) return KPS_EINVAL;
    return KPS_OK;
}
static void address_bytes(uint8_t *q, unsigned bytes, uint32_t offset) {
    if(bytes==2) q[0]=(uint8_t)(offset>>8);
    q[bytes-1]=(uint8_t)offset;
}
static int kps_eeprom_read_impl(kps_bus *b, unsigned model, uint8_t addr7, uint32_t offset, uint8_t *data, size_t length) {
    uint8_t q[2]; unsigned page,bytes; size_t n; int e;
    e=geometry(model,addr7,offset,length,&page,&bytes); if(e) return e;
    if(length && !data) return KPS_EINVAL;
    while(length) {
        n=length>256 ? 256 : length; address_bytes(q,bytes,offset);
        e=kps_i2c(b,addr7,q,bytes,data,n); if(e) return e;
        offset+=(uint32_t)n; data+=n; length-=n;
    }
    return KPS_OK;
}
static int kps_eeprom_write_impl(kps_bus *b, unsigned model, uint8_t addr7, uint32_t offset, const uint8_t *data, size_t length) {
    uint8_t q[66],verify[64]; unsigned page,bytes; size_t n,i; int e;
    e=geometry(model,addr7,offset,length,&page,&bytes); if(e) return e;
    if(length && !data) return KPS_EINVAL;
    while(length) {
        n=page-offset%page; if(n>length) n=length; address_bytes(q,bytes,offset);
        for(i=0;i<n;i++) q[bytes+i]=data[i];
        e=kps_i2c(b,addr7,q,bytes+n,0,0); if(e) return e;
        /* Datasheet tWR max 5ms, use 6ms. WP can ACK but suppress writes: verify. */
        e=kps_delay(b,6); if(e) return e;
        e=kps_i2c(b,addr7,q,bytes,verify,n); if(e) return e;
        for(i=0;i<n;i++) if(verify[i]!=data[i]) return KPS_EVERIFY;
        offset+=(uint32_t)n; data+=n; length-=n;
    }
    return KPS_OK;
}
'''
SOURCES['sht3x'] = r'''
static uint8_t crc(const uint8_t *p) {
    unsigned i,j; uint8_t c=0xff;
    for(i=0;i<2;i++) { c^=p[i]; for(j=0;j<8;j++) c=(uint8_t)((c&0x80)?(c<<1)^0x31:c<<1); }
    return c;
}
static int kps_sht3x_read_impl(kps_bus *b, uint8_t addr7, int32_t *temperature_mC, uint32_t *humidity_mpercent) {
    const uint8_t q[2]={0x24,0x00}; uint8_t r[6]; uint32_t t,h; int e;
    if((addr7!=0x44 && addr7!=0x45) || !temperature_mC || !humidity_mpercent) return KPS_EINVAL;
    e=kps_i2c(b,addr7,q,2,0,0); if(e) return e;
    e=kps_delay(b,16); if(e) return e; /* high repeatability, no clock stretch */
    e=kps_i2c(b,addr7,0,0,r,6); if(e) return e;
    if(crc(r)!=r[2] || crc(r+3)!=r[5]) return KPS_ECRC;
    t=((uint32_t)r[0]<<8)|r[1]; h=((uint32_t)r[3]<<8)|r[4];
    *temperature_mC=(int32_t)((175000ull*t)/65535u)-45000;
    *humidity_mpercent=(uint32_t)((100000ull*h)/65535u);
    return KPS_OK;
}
'''
SOURCES['bh1750'] = r'''
static int kps_bh1750_read_impl(kps_bus *b, uint8_t addr7, uint32_t *millilux) {
    const uint8_t cmds[]={1,0x42,0x65,0x20}; uint8_t r[2]; unsigned i; int e;
    if((addr7!=0x23 && addr7!=0x5c) || !millilux) return KPS_EINVAL;
    /* Explicitly restore MTreg=69; a prior application may have changed it. */
    for(i=0;i<sizeof(cmds);i++) { e=kps_i2c(b,addr7,cmds+i,1,0,0); if(e) return e; }
    e=kps_delay(b,180); if(e) return e;
    e=kps_i2c(b,addr7,0,0,r,2); if(e) return e;
    *millilux=(((uint32_t)r[0]<<8)|r[1])*2500u/3u;
    return KPS_OK;
}
'''
SOURCES['ads1115'] = r'''
static int kps_ads1115_read_impl(kps_bus *b, uint8_t addr7, unsigned channel, int32_t *microvolts) {
    uint8_t q[3],r[2],reg=1; uint16_t cfg; unsigned i; int32_t raw; int e;
    if(addr7<0x48 || addr7>0x4b || channel>3 || !microvolts) return KPS_EINVAL;
    cfg=(uint16_t)(0x8383u|((4u+channel)<<12)); /* OS, +/-4.096V, single shot, 128SPS, comparator off */
    q[0]=1; q[1]=(uint8_t)(cfg>>8); q[2]=(uint8_t)cfg;
    e=kps_i2c(b,addr7,q,3,0,0); if(e) return e;
    /* First wait also prevents reading stale OS immediately after a start. */
    e=kps_delay(b,9); if(e) return e;
    for(i=0;i<20;i++) {
        e=kps_i2c(b,addr7,&reg,1,r,2); if(e) return e;
        if(r[0]&0x80) break;
        e=kps_delay(b,1); if(e) return e;
    }
    if(i==20) return KPS_ETIMEOUT;
    reg=0; e=kps_i2c(b,addr7,&reg,1,r,2); if(e) return e;
    raw=((int32_t)r[0]<<8)|r[1]; if(raw&0x8000) raw-=65536;
    *microvolts=raw*125; /* Input still MUST stay inside device supply rails. */
    return KPS_OK;
}
'''
SOURCES['ssd1306'] = r'''
static int valid(uint8_t a, unsigned h) { return (a==0x3c || a==0x3d) && (h==32 || h==64); }
static int kps_ssd1306_init_impl(kps_bus *b, uint8_t addr7, unsigned height) {
    /* Board must perform the module's power/reset sequence FIRST.
     * Only internal-charge-pump modules. SH1106/SSD1315 are NOT interchangeable.
     * Screen RAM is not initialized here; call flush with a complete framebuffer.
     */
    uint8_t q[]={0,0xae,0xd5,0x80,0xa8,0x3f,0xd3,0,0x40,0x8d,0x14,
        0x20,0,0xa1,0xc8,0xda,0x12,0x81,0x7f,0xd9,0xf1,0xdb,0x40,0xa4,0xa6,0xaf};
    if(!valid(addr7,height)) return KPS_EINVAL;
    q[5]=(uint8_t)(height-1); q[16]=(uint8_t)(height==64 ? 0x12 : 0x02);
    return kps_i2c(b,addr7,q,sizeof(q),0,0);
}
static int kps_ssd1306_flush_impl(kps_bus *b, uint8_t addr7, unsigned height, const uint8_t *pixels, size_t length) {
    uint8_t cmd[]={0,0x21,0,127,0x22,0,7},q[129]; unsigned page,i; int e;
    if(!valid(addr7,height) || !pixels || length!=128u*(height/8u)) return KPS_EINVAL;
    cmd[6]=(uint8_t)(height/8u-1u);
    e=kps_i2c(b,addr7,cmd,sizeof(cmd),0,0); if(e) return e;
    q[0]=0x40;
    for(page=0;page<height/8u;page++) {
        for(i=0;i<128;i++) q[i+1]=pixels[page*128u+i];
        e=kps_i2c(b,addr7,q,sizeof(q),0,0); if(e) return e;
    }
    return KPS_OK;
}
'''


def render_devices(selected):
    selected = sorted(set(selected))
    if not selected or any(k not in CATALOG for k in selected):
        raise ToolError('请选择有效的驱动 / Select valid drivers')
    families = sorted({'eeprom' if k.startswith('24lc') else k for k in selected})
    declarations, files = [], {'kps_bus.h': BUS_H, 'kps_bus.c': BUS_C}
    for family in families:
        wrappers = []
        for name, params in API[family]:
            signature = 'kps_bus *b' + (', ' + params if params else '')
            args = ', '.join(p.strip().split()[-1].lstrip('*') for p in params.split(',')) if params else ''
            declarations.append('int %s(%s);' % (name, signature))
            wrappers.append('int %s(%s) {\n    int e=kps_begin(b); if(e) return e;\n'
                            '    return kps_end(b,%s_impl(b%s));\n}\n' %
                            (name, signature, name, ', '+args if args else ''))
        source = SOURCES[family]
        if family == 'eeprom':
            # Do not accidentally enable the other geometry when not selected.
            if '24lc02b' not in selected:
                source = source.replace('if(model==2)', 'if(0)')
            if '24lc256' not in selected:
                source = source.replace('else if(model==256)', 'else if(0)')
        files['kps_'+family+'.c'] = ('/* SPDX-License-Identifier: MIT; generated by Keil Port Studio. */\n'
                                   '#include "kps_devices.h"\n' + source + '\n'.join(wrappers))
    files['kps_devices.h'] = ('#ifndef KPS_DEVICES_H\n#define KPS_DEVICES_H\n#include "kps_bus.h"\n'
        '#ifdef __cplusplus\nextern "C" {\n#endif\n'
        '/* EEPROM model: 2=24LC02B, 256=24LC256. Only selected models enabled.\n'
        ' * Functions are blocking, task-context only. Output scalars unchanged on error.\n'
        ' * Buffers can be partially transferred on bus errors. Check EVERY return code.\n'
        ' * Do not call from an ISR or while already holding this bus lock. */\n'
        + '\n'.join(declarations) + '\n#ifdef __cplusplus\n}\n#endif\n#endif\n')
    return files
