#!/usr/bin/env bash
# Rebuilds hook.bin / hook.json from hook.c (optional: a prebuilt hook.bin is already in the repo).
# Needs:  pip install ziglang pyelftools
set -euo pipefail
cd "$(dirname "$0")"
FLAGS="-Oz -fno-pic -ffreestanding -fno-builtin -fno-stack-protector -fno-jump-tables -mgeneral-regs-only -mbranch-protection=none -fno-unwind-tables -fno-asynchronous-unwind-tables"
python3 -m ziglang cc -target aarch64-freestanding $FLAGS -c hook.c -o hook.o
# Link at the image offset where the blob is injected (unused tail of the code section).
# The defsyms are image offsets inside the iOS 1.3.5 arm64 build:
#   STUB_*  = the game's own import stubs for glTexImage2D / glTexSubImage2D
#   SLOT_*  = the data slots the loader fills with the real GL function pointers
#   STUB_File*, SLOT_ConfigGet* = the game's file / config import stubs and data slots
#   EXTBUF / CFG = the two 0x2000 halves of the 0x4000 bytes of extra BSS added by patch_ipa.py
python3 -m ziglang ld.lld -m aarch64elf -static -N --no-gc-sections -Ttext=0x4c3180 -e hook_GetString \
  --defsym=STUB_TexImage2D=0x5dc --defsym=STUB_TexSubImage2D=0xd3c \
  --defsym=SLOT_CompTexImage2D=0x527388 --defsym=SLOT_CompTexSubImage2D=0x527790 --defsym=SLOT_GetString=0x5273b0 \
  --defsym=EXTBUF=0x5e5df0 --defsym=CFG=0x5e7df0 \
  --defsym=STUB_FileOpen=0x57c --defsym=STUB_FileRead=0x9bc --defsym=STUB_FileClose=0x51c \
  --defsym=SLOT_ConfigGetString=0x527778 --defsym=SLOT_ConfigGetInt=0x5273b8 hook.o -o hook.elf
python3 -m ziglang objcopy -O binary hook.elf hook.bin
python3 - <<'PY'
import json
from elftools.elf.elffile import ELFFile
e = ELFFile(open('hook.elf', 'rb'))
want = ('hook_CompTexImage2D', 'hook_CompTexSubImage2D', 'hook_GetString', 'hook_ConfigGetString', 'hook_ConfigGetInt')
sym = {s.name: hex(s['st_value']) for s in e.get_section_by_name('.symtab').iter_symbols() if s.name in want}
json.dump({'link_address': '0x4c3180', 'symbols': sym}, open('hook.json', 'w'), indent=2)
print(sym)
PY
rm -f hook.o hook.elf
ls -l hook.bin
