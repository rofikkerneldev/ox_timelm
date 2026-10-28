#!/usr/bin/env python3
"""Exercise actual timelm RAM and UEFI payload parsers with sanitizers."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class PortTests(unittest.TestCase):
    def test_actual_memory_and_payload_bounds(self):
        code = r'''
#include <stdint.h>
#include <assert.h>
#include <string.h>
#include "xnuloader/timelm.h"
#include "uefi/timelm/TimelmPayload.h"
int main(void) {
 uint64_t base=0,size=0;
 assert(timelm_ram_window(0xc0000000ULL,0x180000000ULL,&base,&size));
 assert(base==0x100000000ULL && size==0x80000000ULL);
 assert(!timelm_ram_window(0x180000000ULL,0x200000000ULL,&base,&size));
 assert(!timelm_ram_window(0x200000000ULL,0x400000000ULL,&base,&size));
 assert(!timelm_ram_window(0x17fffffffULL,0x180000000ULL,&base,&size));
 assert(!timelm_ram_window(UINT64_MAX-1,UINT64_MAX,&base,&size));
 assert(!timelm_ram_window(0x100000000ULL,0,&base,&size));
 unsigned char p[72]={0x58,0x4e,0x55,0x50,64};
 memcpy(p+8,"loader.efi",11);p[24]=4;p[32]='M';p[33]='Z';
 const unsigned char *image=0;unsigned int length=0;
 assert(TimelmFindLoader(p,sizeof(p),&image,&length));assert(image==p+32 && length==4);
 for(unsigned int n=0;n<36;n++)assert(!TimelmFindLoader(p,n,&image,&length));
 p[24]=255;assert(!TimelmFindLoader(p,sizeof(p),&image,&length));
 p[24]=4;p[4]=255;assert(!TimelmFindLoader(p,sizeof(p),&image,&length));
 for(unsigned int n=0;n<sizeof(p);n++) {
  memset(p,0xff,sizeof(p));assert(!TimelmFindLoader(p,n,&image,&length));
 }
}
'''
        with tempfile.TemporaryDirectory(prefix='timelm-port-',dir=os.environ.get('TMPDIR')) as d:
            source=Path(d)/'test.c'; binary=Path(d)/'test'
            source.write_text(code)
            subprocess.run(['clang','-std=c11','-fsanitize=address,undefined','-I'+str(ROOT),
                            str(source),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)

    def test_profile_and_missing_firmware(self):
        run=subprocess.run(['bash','-c','. common/devices/timelm.conf; printf "%s\\n%s\\n" "$BOOTARGS" "$AUTOBOOT_ARGS"'],cwd=ROOT,capture_output=True,text=True,check=True)
        args,auto=run.stdout.splitlines()
        self.assertIn('cpus=1',args)
        for option in ('ufs=0','gpu=0','wifi=0','touchscreen=0'):
            self.assertIn(option,args)
        self.assertEqual(auto,'')
        env=os.environ.copy();env.update(UEFI_TARGET='timelm',UEFI_FD='/nonexistent/timelm.fd')
        run=subprocess.run(['python3','mkxnuimg.py'],cwd=ROOT,env=env,capture_output=True,text=True)
        self.assertNotEqual(run.returncode,0)
        self.assertIn('no prebuilt fallback',run.stderr)


if __name__=='__main__':unittest.main()
