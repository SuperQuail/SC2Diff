# -*- coding: utf-8 -*-
import re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
EXE = r"D:\Game\BLZ\StarCraft II\Support64\SC2Editor_x64.exe"
data = open(EXE, 'rb').read()
def strings_with_off(data, minlen=4):
    out, cur, start = [], b'', 0
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if not cur: start = i
            cur += bytes([b])
        else:
            if len(cur) >= minlen: out.append((start, cur.decode('ascii')))
            cur = b''
    return out
S = strings_with_off(data)
idx = {}
for off, s in S:
    idx.setdefault(s, off)
for probe in ['CommandLineLoad', 'CommandLineHandle', 'DocsAutoReload', 'AutoReloadCheck', '.SC2Components', 'headlessNoRender']:
    off = idx.get(probe)
    print('=== %s @ 0x%X ===' % (probe, off or 0))
    if off is None: continue
    near = [(o, s) for (o, s) in S if abs(o - off) < 3000]
    near.sort()
    for o, s in near:
        print('   0x%08X %s' % (o, s[:130]))
    print()
