"""Runs the injected PVRTC hook in an ARM64 emulator and compares it with an independent decoder.
pip install unicorn numpy texture2ddecoder
"""
import struct, numpy as np, texture2ddecoder as t
from unicorn import *
from unicorn.arm64_const import *
import sys
if len(sys.argv)<2: sys.exit('usage: test_hook_emulated.py patched_image.raw   (create it with: tools/patch_ipa.py <ipa> --keep-raw patched_image.raw)')
raw=open(sys.argv[1],'rb').read(); f=struct.unpack_from('<18I',raw,4); cs=f[4]; code=raw[cs:cs+f[16]]
BASE=0x10000000                       # emulated image base
def run(fn_off, args, stack_args=(), setup=None, hooks=None):
    mu=Uc(UC_ARCH_ARM64,UC_MODE_ARM)
    mu.mem_map(BASE,0x700000+0x10000); mu.mem_write(BASE,code)
    mu.mem_map(0x70000000,0x400000); sp=0x70000000+0x3f0000
    for i,a in enumerate(args): mu.reg_write(UC_ARM64_REG_X0+i,a)
    sp-=16*len(stack_args); 
    for i,a in enumerate(stack_args): mu.mem_write(sp+8*i,struct.pack('<Q',a))
    mu.reg_write(UC_ARM64_REG_SP,sp); LR=BASE+0x6f0000; mu.reg_write(UC_ARM64_REG_LR,LR)
    calls=[]
    def hk(uc,addr,size,ud):
        name=hooks[addr-BASE]
        regs=[uc.reg_read(UC_ARM64_REG_X0+i) for i in range(8)]
        sp_=uc.reg_read(UC_ARM64_REG_SP); extra=struct.unpack('<Q',uc.mem_read(sp_,8))[0]
        ret=hooks_ret.get(name,0)
        calls.append((name,regs,extra,uc))
        if name=='TexSubImage2D':
            w,nrows=regs[4],regs[5]; calls[-1]=(name,regs,extra,bytes(uc.mem_read(extra,w*nrows*4)))
        else: calls[-1]=(name,regs,extra,None)
        uc.reg_write(UC_ARM64_REG_X0,ret); uc.reg_write(UC_ARM64_REG_PC,uc.reg_read(UC_ARM64_REG_LR))
    hooks_ret={}
    if setup: hooks_ret=setup(mu) or {}
    for off in hooks: mu.hook_add(UC_HOOK_CODE,hk,None,BASE+off,BASE+off)
    mu.emu_start(BASE+fn_off,LR)
    return calls,mu
# slot / stub layout inside emulated image: image offsets are used directly (BASE+offset)
def test_decode(w,h,fmt,seed):
    np.random.seed(seed); bw=max(w//(8 if fmt in(0x8c01,0x8c03) else 4),2); bh=max(h//4,2)
    d=np.random.bytes(bw*bh*8)
    def setup(mu):
        mu.mem_write(BASE+0x600000,d)
    calls,mu=run(0x4c3180,[0x0de1,0,fmt,w,h,0,len(d),BASE+0x600000],setup=setup,hooks={0x5dc:'TexImage2D',0xd3c:'TexSubImage2D'})
    assert calls[0][0]=='TexImage2D', calls[0][0]
    img=np.zeros((h,w,4),np.uint8); y=0
    for name,regs,extra,buf in calls[1:]:
        x,yo,ww,rows=regs[2],regs[3],regs[4],regs[5]
        img[yo:yo+rows,:ww,:]=np.frombuffer(buf,np.uint8).reshape(rows,ww,4)
    if fmt in(0x8c00,0x8c02):
        ref=np.frombuffer(t.decode_pvrtc(d,bw*4,bh*4,False),np.uint8).reshape(bh*4,bw*4,4)[:h,:w,[2,1,0,3]]
        df=np.abs(img.astype(int)-ref.astype(int)); a=df[...,3] if fmt==0x8c02 else 0
        print(f'{w}x{h} fmt {fmt:#x}: calls {len(calls)}  rgb max diff {df[...,:3].max()}  alpha max {np.max(a)}  (alpha channel sample {img[0,0,3]})')
    else: print(f'{w}x{h} fmt {fmt:#x} (2bpp approx): calls {len(calls)} ok, mean rgb {img[...,:3].mean():.1f}')
for w,h,fmt in((64,64,0x8c02),(128,32,0x8c02),(8,8,0x8c00),(4,4,0x8c00),(512,256,0x8c02),(1024,1024,0x8c00),(2,2,0x8c00),(64,64,0x8c03)): test_decode(w,h,fmt,w+h)

print('--- glGetString / passthrough tests')
EXT=b"GL_OES_blend_subtract GL_AMD_compressed_ATC_texture GL_OES_compressed_ETC1_RGB8_texture GL_OES_depth24 GL_EXT_texture_compression_s3tc GL_OES_packed_depth_stencil \0"
def setup_gs(mu):
    mu.mem_write(BASE+0x600000,EXT)
    mu.mem_write(BASE+0x5273b0,struct.pack('<Q',BASE+0x680000))      # SLOT_GetString -> fake function
    return {'GetString':BASE+0x600000}
calls,mu=run(0x4c39ac,[0x1F03],setup=setup_gs,hooks={0x680000:'GetString'})
ret=mu.reg_read(UC_ARM64_REG_X0); s=bytes(mu.mem_read(ret,200)).split(b'\0')[0]
print('returned @',hex(ret-BASE),'(EXTBUF=0x5e5df0):',s.decode())
calls,mu=run(0x4c39ac,[0x1F00],setup=lambda mu:(mu.mem_write(BASE+0x600000,b'Adreno\0'),mu.mem_write(BASE+0x5273b0,struct.pack('<Q',BASE+0x680000)),{'GetString':BASE+0x600000})[2],hooks={0x680000:'GetString'})
print('GL_VENDOR passthrough ->',bytes(mu.mem_read(mu.reg_read(UC_ARM64_REG_X0),8)).split(b'\0')[0])
def setup_pt(mu): mu.mem_write(BASE+0x527388,struct.pack('<Q',BASE+0x680000))
calls,mu=run(0x4c3180,[0x0de1,1,0x8D64,16,16,0,128,BASE+0x600000],setup=setup_pt,hooks={0x680000:'RealCompTexImage2D'})
print('ETC1 passthrough ->',calls[0][0],'args',[hex(x) for x in calls[0][1][:4]])
