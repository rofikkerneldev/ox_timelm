#!/usr/bin/env python3
"""Run actual0091 snapshot code with lifetime/lock mocks and header decoder cases."""
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def snapshot_code():
    if os.environ.get('XNU_SOURCE'):
        text = (Path(os.environ['XNU_SOURCE']) / 'osfmk/kern/exception.c').read_text()
    else:
        patch = (ROOT / 'xnu/patches/0091-timelm-bounded-gui-snapshots.patch').read_text()
        section = patch.split('+++ b/osfmk/kern/exception.c\n', 1)[1].split('\n--- ', 1)[0]
        text = '\n'.join(line[1:] for line in section.splitlines() if line.startswith(('+', ' ')))
    text = text[text.index('#define SWEET_SAMPLE_MAX_THREADS'):]
    return text[:text.index('#endif /* ARM64_BOARD_CONFIG_SWEET */')]


class GUITests(unittest.TestCase):
    def test_snapshot_reference_lifetime_and_lock_release(self):
        code = snapshot_code()
        for call in ('vm_map_read_user(', 'copyin(', 'find_user_regs(', 'get_task_map('):
            self.assertNotIn(call, code)
        harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <string>
#include <vector>
struct th { int refs, id; th *next; };
struct task { th *threads; uint64_t mach_header_vm_address; };
using thread_t=th*; using task_t=task*;
using kern_return_t=int; using mach_msg_type_number_t=unsigned int;
using thread_info_t=int*; using thread_state_t=unsigned int*;
struct thread_basic_info { int run_state,suspend_count,flags; };
struct arm_thread_state64_t { uint64_t pc,lr,sp; };
struct region { uint64_t sr_slide; };
using vm_shared_region_t=region*;
#define THREAD_BASIC_INFO_COUNT 3
#define THREAD_BASIC_INFO 1
#define ARM_THREAD_STATE64_COUNT 6
#define ARM_THREAD_STATE64 6
#define KERN_SUCCESS 0
#define arm_thread_state64_get_pc(t) ((t).pc)
#define arm_thread_state64_get_lr(t) ((t).lr)
#define arm_thread_state64_get_sp(t) ((t).sp)
#define queue_iterate(q,t,type,link) for ((t)=*(q);(t);(t)=(t)->next)
static int locked, calls, infos, released;
static std::vector<std::string> output;
static region sr{0x12340000};
static vm_shared_region_t vm_shared_region_get(task_t) { return &sr; }
static void vm_shared_region_deallocate(vm_shared_region_t) {}
static void task_lock(task_t) { assert(!locked);locked=1; }
static void task_unlock(task_t) { assert(locked);locked=0; }
static void thread_reference(thread_t t) { assert(locked);++t->refs; }
static void thread_deallocate(thread_t t) { assert(!locked && t->refs==1);--t->refs;++released; }
static uint64_t thread_tid(thread_t t) { assert(!locked && t->refs);return t->id; }
static uint64_t mach_absolute_time() { return 12345678; }
static int emit(const char *fmt,...) {
 assert(!locked);char b[512];va_list ap;va_start(ap,fmt);int n=vsnprintf(b,sizeof(b),fmt,ap);va_end(ap);
 output.emplace_back(b);return n;
}
static kern_return_t thread_info(thread_t t,int,thread_info_t out,mach_msg_type_number_t *) {
 assert(!locked && t->refs==1);++infos;auto *b=(thread_basic_info*)out;
 if(t->id==1)return 14;b->run_state=3;return 0;
}
static kern_return_t thread_get_state(thread_t t,int,thread_state_t out,mach_msg_type_number_t *n) {
 assert(!locked && t->refs==1);++calls;
 if(t->id==1)return 14; // task/thread termination race
 if(t->id==2){*n=0;return 0;} // successful but unexpected count
 auto *s=(arm_thread_state64_t*)out;s->pc=0x12345678;s->lr=0x23456789;s->sp=0x34567890;return 0;
}
#define printf emit
''' + code + r'''
int main() {
 for(int size: {0,1,3,40}) {
  std::vector<th> v(size);for(int i=0;i<size;i++){v[i].id=i;v[i].next=i+1<size?&v[i+1]:nullptr;}
  task t{size?&v[0]:nullptr,0x100000000};calls=infos=released=locked=0;output.clear();
  sweet_sample_task("SpringBoard",26,&t);
  const int expected=size>32?32:size;
  assert(!locked && calls==expected && infos==expected && released==expected);
  for(auto &x:v)assert(x.refs==0);
  int regs=0;bool truncated=false;
  for(auto &line:output){if(line.find(" pc ")!=std::string::npos)++regs;
   if(line.find("truncated 1")!=std::string::npos)truncated=true;}
  assert(regs==expected-(expected>1)-(expected>2));assert(truncated==(size>32));
 }
}
'''
        with tempfile.TemporaryDirectory(prefix='timelm91-', dir=os.environ.get('TMPDIR')) as td:
            src = Path(td) / 'snapshot.cpp'
            binary = Path(td) / 'snapshot'
            src.write_text(harness)
            subprocess.run(['clang++', '-std=c++17', '-fsanitize=address,undefined',
                            '-fno-omit-frame-pointer', str(src), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_decoder_new_old_and_unknown_extension(self):
        fields = ('vsyncs', 'vsync_abs', 'submit_abs', 'present_abs', 'copy_abs',
                  'power_off', 'power_abs', 'sample_cycle', 'sample_abs',
                  'sample_targets', 'sample_phase')
        with tempfile.TemporaryDirectory(prefix='timelm91-', dir=os.environ.get('TMPDIR')) as td:
            p = Path(td) / 'tail.bin'
            for version in (None, 1, 99):
                raw = bytearray(8192)
                struct.pack_into('<8sIIQQ', raw, 0, b'SWEETLOG', 1, 4096, 0, 1)
                if version is not None:
                    struct.pack_into('<8sII11Q', raw, 640, b'TIMELM91', version, 104, *range(11))
                p.write_bytes(raw)
                run = subprocess.run(['python3', str(ROOT / 'tools/sweet-log.py'), str(p)],
                                     check=True, capture_output=True, text=True)
                self.assertEqual(run.stdout, '')
                if version == 1:
                    line = next(x for x in run.stderr.splitlines() if x.startswith('# TIMELM91 '))
                    self.assertEqual(json.loads(line[len('# TIMELM91 '):]), dict(zip(fields, range(11))))
                else:
                    self.assertNotIn('# TIMELM91 {', run.stderr)


if __name__ == '__main__':
    unittest.main()
