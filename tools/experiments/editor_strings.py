# -*- coding: utf-8 -*-
"""Find the editor's command-line contract by scanning its binary strings."""
import re, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
EXE = r"D:\Game\BLZ\StarCraft II\Support64\SC2Editor_x64.exe"
data = open(EXE, 'rb').read()
print('size', len(data))
def strings(data, minlen=5):
    out, cur = [], b''
    for b in data:
        if 32 <= b < 127:
            cur += bytes([b])
        else:
            if len(cur) >= minlen: out.append(cur.decode('ascii'))
            cur = b''
    if len(cur) >= minlen: out.append(cur.decode('ascii'))
    return out
S = strings(data)
print('strings:', len(S))
pats = [r'CommandLine', r'^-', r'-[a-z]{3,}$', r'\.SC2Map', r'\.SC2Mod', r'\.SC2Component', r'SC2Components', r'headless', r'AutoReload', r'Reload', r'Autosave', r'OpenDocument', r'DocumentFolder']
for p in pats:
    rx = re.compile(p)
    hits = [s for s in S if rx.search(s)]
    print('\n### %s -> %d' % (p, len(hits)))
    for h in hits[:40]:
        print('   ', h[:160])
