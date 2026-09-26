"""Compile and EXECUTE generated C against protocol/fault models (not hardware HIL)."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import keil_port_tool as m
from kps_core.driver_generator import render_pack
from kps_core.device_drivers import CATALOG
from test_spl_adapter import project

HARNESS = r'''
#include "kps_devices.h"
#include "kps_board_port.h"
#include "kps_dma.h"
#include "kps_soft_i2c.h"
#include <assert.h>
#include <string.h>
#include <stdio.h>
static int mode, locked, locks, unlocks, fail, writes, wp, busy, badid;
static unsigned page, ab, sizes[100], delay_total, calls;
static uint8_t memory[32768], flash[8192];
static int lock(void *p,uint32_t ms) { (void)p; assert(ms); assert(!locked); locked=1; locks++; return 0; }
static void unlock(void *p) { (void)p; assert(locked); locked=0; unlocks++; }
static int delay(void *p,uint32_t ms) { (void)p; assert(locked); delay_total+=ms; return 0; }
static int i2c(void *p,uint8_t a,const uint8_t *t,size_t nt,uint8_t *r,size_t nr,uint32_t ms) {
    unsigned off,i; (void)p; assert(locked && ms); calls++;
    if(fail) return KPS_EIO;
    if(mode==1) {
        assert(a==0x50 && nt>=ab); off=ab==2 ? ((unsigned)t[0]<<8)|t[1] : t[0];
        if(nr) { assert(nt==ab); memcpy(r,memory+off,nr); }
        else { assert(nt>ab && off/page==(off+nt-ab-1)/page);
            sizes[writes++]=(unsigned)(nt-ab); if(!wp) memcpy(memory+off,t+ab,nt-ab); }
    } else if(mode==2) {
        assert(a==0x44);
        if(nt) { assert(nt==2 && t[0]==0x24 && t[1]==0); }
        else { uint8_t data[]={0xbe,0xef,0x92,0xbe,0xef,0x92}; assert(nr==6); memcpy(r,data,6); if(wp) r[5]^=1; }
    } else if(mode==3) {
        static const uint8_t commands[]={1,0x42,0x65,0x20}; assert(a==0x23);
        if(nt) { assert(nt==1 && *t==commands[writes++]); }
        else { assert(nr==2); r[0]=0; r[1]=120; }
    } else if(mode==4) {
        assert(a==0x48);
        if(nt==3) { assert(t[0]==1 && t[1]==0xc3 && t[2]==0x83); writes++; }
        else { assert(nt==1 && nr==2);
            if(*t==1) { r[0]=(uint8_t)(busy?0:0x80); r[1]=0; }
            else { assert(*t==0); r[0]=0xff; r[1]=0xff; }
        }
    } else if(mode==5) {
        assert(a==0x3c && !nr);
        if(nt==26) { assert(t[0]==0 && t[5]==(wp?31:63) && t[16]==(wp?2:0x12)); }
        else if(nt==7) { assert(t[1]==0x21 && t[6]==(wp?3:7)); }
        else { assert(nt==129 && t[0]==0x40); for(i=1;i<129;i++) assert(t[i]==(uint8_t)(writes*128+i-1)); writes++; }
    } else assert(0);
    return 0;
}
static int wel;
static int spi(void *p,const uint8_t *c,size_t nc,const uint8_t *t,uint8_t *r,size_t n,uint32_t ms) {
    uint32_t a=0; (void)p; assert(locked && ms); calls++;
    if(fail) return KPS_EIO;
    if(nc==4) a=((uint32_t)c[1]<<16)|((uint32_t)c[2]<<8)|c[3];
    switch(c[0]) {
    case 0x9f: assert(nc==1 && n==3); r[0]=(uint8_t)(badid?0:0xef); r[1]=0x40; r[2]=0x18; break;
    case 5: assert(n==1); r[0]=(uint8_t)((busy?1:0)|(wel?2:0)); break;
    case 6: assert(n==0); wel=1; break;
    case 3: assert(a+n<=sizeof(flash)); memcpy(r,flash+a,n); break;
    case 2: assert(wel && a+n<=sizeof(flash) && a/256==(a+n-1)/256);
        sizes[writes++]=(unsigned)n; if(!wp) memcpy(flash+a,t,n); wel=0; break;
    case 0x20: assert(wel && a%4096==0 && !n); if(!wp) memset(flash+a,0xff,4096); writes++; wel=0; break;
    default: assert(0);
    }
    return 0;
}
static int starts, waits, aborts, wait_error, abort_error, start_error;
static const uint8_t *dma_seen;
static int dma_start(void *p,int s,uint8_t a,const uint8_t *t,size_t nt,uint8_t *r,size_t nr) {
    (void)p; (void)s; (void)a; starts++; dma_seen=t;
    assert(nt>0); if(nr) memset(r,0x5a,nr); return start_error;
}
static int dma_wait(void *p,uint32_t ms) { (void)p; assert(ms); waits++; return wait_error; }
static int dma_abort(void *p,uint32_t ms) { (void)p; assert(ms); aborts++; return abort_error; }
static int scl_value=1,sda_value=1,stuck,nack,readpos;
static uint8_t sequence[100];
static int set_scl(void *p,int v) { (void)p; scl_value=v; return 0; }
static int set_sda(void *p,int v) { (void)p; sda_value=v; return 0; }
static int read_scl(void *p) { (void)p; return stuck?0:scl_value; }
static int read_sda(void *p) { (void)p; if(nack) return 1; return sequence[readpos++]; }
static int delay_us(void *p,uint32_t us) { (void)p; assert(us>=5); return 0; }
int main(void) {
    kps_bus b={0,i2c,spi,delay,lock,unlock,100};
    uint8_t data[1024],out[1024]; unsigned i; int32_t temp,uv; uint32_t hum,lux;
    kps_board_context board={0}; kps_bus unfinished=kps_board_bus(&board);
    uint8_t dt[288],dr[288]; kps_dma dma={0,dt,dr,288,288,dma_start,dma_wait,dma_abort,0};
    kps_soft_i2c soft={0,set_scl,set_sda,read_scl,read_sda,delay_us,5};
    assert(kps_w25q_probe(&unfinished)==KPS_ENOSYS);
    for(i=0;i<sizeof(data);i++) data[i]=(uint8_t)i;
    mode=1; ab=2; page=64; writes=0;
    assert(kps_eeprom_write(&b,256,0x50,63,data,70)==0);
    assert(writes==3 && sizes[0]==1 && sizes[1]==64 && sizes[2]==5);
    assert(kps_eeprom_read(&b,256,0x50,63,out,70)==0 && !memcmp(out,data,70));
    ab=1; page=8; writes=0;
    assert(kps_eeprom_write(&b,2,0x50,7,data,20)==0 && writes==4);
    assert(sizes[0]==1 && sizes[1]==8 && sizes[2]==8 && sizes[3]==3);
    assert(kps_eeprom_read(&b,2,0x50,7,out,20)==0 && !memcmp(out,data,20));
    i=calls; assert(kps_eeprom_write(&b,2,0x50,255,data,2)==KPS_EINVAL && i==calls);
    assert(kps_eeprom_read(&b,16,0x50,0,out,2)==KPS_EDEVICE);
    wp=1; data[0]=99; assert(kps_eeprom_write(&b,2,0x50,7,data,1)==KPS_EVERIFY); wp=0;
    fail=1; assert(kps_eeprom_read(&b,2,0x50,0,out,2)==KPS_EIO); fail=0;
    mode=2; delay_total=0; assert(kps_sht3x_read(&b,0x44,&temp,&hum)==0);
    assert(temp==(int32_t)(175000ull*0xbeef/65535)-45000 && hum==100000ull*0xbeef/65535 && delay_total==16);
    temp=123; hum=456; wp=1; assert(kps_sht3x_read(&b,0x44,&temp,&hum)==KPS_ECRC && temp==123 && hum==456); wp=0;
    assert(kps_sht3x_read(&b,0x46,&temp,&hum)==KPS_EINVAL);
    mode=3; writes=0; delay_total=0; assert(kps_bh1750_read(&b,0x23,&lux)==0 && lux==100000 && delay_total==180);
    mode=4; assert(kps_ads1115_read(&b,0x48,0,&uv)==0 && uv==-125);
    busy=1; uv=42; assert(kps_ads1115_read(&b,0x48,0,&uv)==KPS_ETIMEOUT && uv==42); busy=0;
    assert(kps_ads1115_read(&b,0x48,4,&uv)==KPS_EINVAL);
    mode=5; data[0]=0; writes=0; assert(kps_ssd1306_init(&b,0x3c,64)==0);
    assert(kps_ssd1306_flush(&b,0x3c,64,data,1024)==0 && writes==8);
    wp=1; writes=0; assert(kps_ssd1306_init(&b,0x3c,32)==0);
    assert(kps_ssd1306_flush(&b,0x3c,32,data,512)==0 && writes==4); wp=0;
    assert(kps_ssd1306_flush(&b,0x3c,64,data,512)==KPS_EINVAL);
    memset(flash,0xff,sizeof(flash)); writes=0;
    assert(kps_w25q_probe(&b)==0);
    assert(kps_w25q_program(&b,250,data,20)==0 && writes==2 && sizes[0]==6 && sizes[1]==14);
    assert(kps_w25q_read(&b,250,out,20)==0 && !memcmp(out,data,20));
    data[0]=0xff; i=(unsigned)writes; assert(kps_w25q_program(&b,250,data,1)==KPS_EVERIFY && i==(unsigned)writes);
    assert(kps_w25q_erase4k(&b,1)==KPS_EINVAL);
    assert(kps_w25q_erase4k(&b,0)==0 && flash[250]==0xff);
    wp=1; flash[0]=0; assert(kps_w25q_erase4k(&b,0)==KPS_EVERIFY); wp=0;
    assert(kps_w25q_read(&b,0xffffff,out,2)==KPS_EINVAL);
    badid=1; i=(unsigned)writes; assert(kps_w25q_program(&b,0,data,1)==KPS_EDEVICE && i==(unsigned)writes); badid=0;
    busy=1; assert(kps_w25q_probe(&b)==0); assert(kps_w25q_read(&b,0,out,1)==KPS_ETIMEOUT); busy=0;
    assert(locks==unlocks && !locked);
    assert(kps_dma_i2c(&dma,0x44,data,2,out,6,100)==0 && out[0]==0x5a && dma_seen!=data);
    wait_error=KPS_ETIMEOUT; out[0]=0x33;
    assert(kps_dma_i2c(&dma,0x44,data,2,out,6,100)==KPS_ETIMEOUT && out[0]==0x33 && aborts==1);
    abort_error=KPS_EIO;
    assert(kps_dma_i2c(&dma,0x44,data,2,out,6,100)==KPS_ETIMEOUT && dma.quarantined);
    i=(unsigned)starts; assert(kps_dma_i2c(&dma,0x44,data,2,out,6,100)==KPS_EIO && i==(unsigned)starts);
    dma.quarantined=0; wait_error=0; abort_error=0; start_error=KPS_EIO;
    i=(unsigned)aborts; assert(kps_dma_i2c(&dma,0x44,data,2,out,6,100)==KPS_EIO && aborts==(int)i+1);
    start_error=0; assert(kps_dma_spi(&dma,data,4,data,0,256,100)==0);
    assert(kps_dma_spi(&dma,data,4,0,out,257,100)==KPS_EINVAL);
    /* Write pointer + repeated START + one byte 0xA5. */
    sequence[0]=1; sequence[1]=0; sequence[2]=0; sequence[3]=1; sequence[4]=0;
    for(i=0;i<8;i++) sequence[5+i]=(uint8_t)((0xa5>>(7-i))&1);
    assert(kps_soft_i2c_transfer(&soft,0x50,data,1,out,1,100)==0 && out[0]==0xa5 && readpos==13);
    assert(scl_value==1 && sda_value==1);
    nack=1; assert(kps_soft_i2c_transfer(&soft,0x50,data,1,out,1,100)==KPS_EIO); nack=0;
    stuck=1; assert(kps_soft_i2c_transfer(&soft,0x50,data,1,out,1,1)==KPS_ETIMEOUT);
    assert(scl_value==1 && sda_value==1);
    puts("protocols, boundaries, CRC, protection, timeouts, locking, DMA quarantine and software-I2C PASS");
    return 0;
}
'''


def emit(directory, files):
    for name, text in files.items():
        (directory/name).write_text(text, encoding='utf-8')


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.gcc = shutil.which('gcc') or ('C:/MinGW/bin/gcc.exe' if Path('C:/MinGW/bin/gcc.exe').exists() else None)
        if not self.gcc:
            self.skipTest('GCC unavailable: generated C execution not verified')

    def test_generated_c_protocols(self):
        with tempfile.TemporaryDirectory(prefix='kps-driver-') as td:
            root=Path(td)
            emit(root, render_pack(CATALOG, 'software', 'dma'))
            (root/'test.c').write_text(HARNESS, encoding='utf-8')
            result=subprocess.run([self.gcc,'-std=c99','-Wall','-Wextra','-Werror','-O2']+
                [str(p) for p in root.glob('*.c')]+['-o',str(root/'test.exe')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(root/'test.exe')],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            print(result.stdout)

    def test_all_transport_profiles_and_each_driver_compile(self):
        combinations=[([key],'hardware','hardware') for key in CATALOG]
        combinations += [(CATALOG,i,s) for i in ('hardware','software','dma') for s in ('hardware','dma')]
        for selected,i2c,spi in combinations:
            with self.subTest(selected=selected,i2c=i2c,spi=spi), tempfile.TemporaryDirectory() as td:
                root=Path(td); emit(root,render_pack(selected,i2c,spi))
                result=subprocess.run([self.gcc,'-std=c99','-Wall','-Wextra','-Werror','-c']+
                    [str(p) for p in root.glob('*.c')],cwd=root,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)

    def test_plan_dry_run_install_preserve_collision_and_rollback(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            p=project(Path(td)); before=p.path.read_bytes()
            opts=SimpleNamespace(drivers=['24lc256','sht3x'],driver_i2c='dma',driver_spi='hardware',yes=True,dry_run=True)
            self.assertFalse(m.run_tasks(p,['device_drivers'],opts))
            self.assertEqual(p.path.read_bytes(),before)
            self.assertFalse((Path(td)/'KPS').exists())
            opts.dry_run=False; p=m.KeilProject(p.path)
            self.assertTrue(m.run_tasks(p,['device_drivers'],opts))
            port=Path(td)/'KPS/DeviceDrivers/kps_board_port.c'
            port.write_text(port.read_text(encoding='utf-8')+'\n/* user */\n',encoding='utf-8')
            changed=port.read_bytes()
            self.assertFalse(m.run_tasks(m.KeilProject(p.path),['device_drivers'],opts))
            self.assertEqual(port.read_bytes(),changed)
            opts.driver_i2c='software'; p=m.KeilProject(p.path)
            self.assertFalse(m.run_tasks(p,['device_drivers'],opts)); self.assertTrue(p._planning_failed)
            self.assertEqual(port.read_bytes(),changed)
            self.assertTrue(m.rollback_last_transaction(m.KeilProject(p.path),yes=True))
            self.assertEqual(p.path.read_bytes(),before)
            self.assertFalse(port.exists())
            # Empty shells left by rollback must not block a new profile.
            self.assertTrue(m.run_tasks(m.KeilProject(p.path),['device_drivers'],opts))
            self.assertIn('kps_soft_i2c_transfer',port.read_text(encoding='utf-8'))

    def test_keil_ac5_compiles_all_profiles(self):
        compiler=os.environ.get('ARMCC','D:/Keil_v5/ARM/ARMCC/Bin/armcc.exe')
        if not Path(compiler).is_file(): self.skipTest('AC5 unavailable; not a Keil compile pass')
        for i2c in ('hardware','software','dma'):
            for spi in ('hardware','dma'):
                with self.subTest(i2c=i2c,spi=spi), tempfile.TemporaryDirectory() as td:
                    root=Path(td); emit(root,render_pack(CATALOG,i2c,spi))
                    for source in root.glob('*.c'):
                        result=subprocess.run([compiler,'--cpu','Cortex-M4','--c99','-O2','-c',str(source),
                            '-o',str(source.with_suffix('.o'))],cwd=root,capture_output=True,text=True,errors='replace')
                        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                        self.assertNotIn('warning:',result.stderr.lower(),source.name+result.stderr)

    def test_preview_concurrency_and_shared_target_uninstall(self):
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root=Path(td); p=project(root)
            opts=SimpleNamespace(drivers=['sht3x'],yes=True,dry_run=False)
            def external_edit(_preview):
                p.path.write_bytes(p.path.read_bytes()+b'\n<!-- external edit -->\n')
                return True
            opts.preview_callback=external_edit
            with self.assertRaises(m.ToolError): m.run_tasks(p,['device_drivers'],opts)
            self.assertIn(b'external edit',p.path.read_bytes())
            self.assertFalse((root/'KPS').exists())
            p=m.KeilProject(p.path)
            def new_port(_preview):
                port=root/'KPS/DeviceDrivers/kps_board_port.c'
                port.parent.mkdir(parents=True); port.write_text('/* external port */')
                return True
            opts.preview_callback=new_port
            with self.assertRaises(m.ToolError): m.run_tasks(p,['device_drivers'],opts)
            self.assertEqual((root/'KPS/DeviceDrivers/kps_board_port.c').read_text(),'/* external port */')
            self.assertFalse((root/'KPS/DeviceDrivers/driver-pack.json').exists())
        with tempfile.TemporaryDirectory() as td, contextlib.redirect_stdout(io.StringIO()):
            root=Path(td); p=project(root)
            extra=copy.deepcopy(p.targets[0]); extra.find('TargetName').text='Release'
            p.root.find('Targets').append(extra)
            p.path.write_bytes(m.ET.tostring(p.root,encoding='utf-8'))
            p=m.KeilProject(p.path)
            opts=SimpleNamespace(drivers=['sht3x'],yes=True,dry_run=False)
            self.assertTrue(m.run_tasks(p,['device_drivers'],opts))
            saved=p.path.read_bytes(); port=root/'KPS/DeviceDrivers/kps_board_port.c'
            p=m.KeilProject(p.path); p.select_targets(['T'])
            with self.assertRaises(m.ToolError): m.uninstall_component(p,'device_drivers',yes=True)
            self.assertEqual(saved,p.path.read_bytes()); self.assertTrue(port.is_file())
            self.assertTrue(m.uninstall_component(m.KeilProject(p.path),'device_drivers',yes=True))
            self.assertFalse(port.exists())

    def test_cli_rejects_ambiguous_driver_options(self):
        for args in (['--driver-i2c','dma'], ['--driver','sht3x','--rollback']):
            result=subprocess.run([sys.executable,str(Path(m.__file__))]+args,
                                  capture_output=True,text=True,encoding='utf-8',errors='replace')
            self.assertNotEqual(result.returncode,0)


if __name__=='__main__':
    unittest.main()
