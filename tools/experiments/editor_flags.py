# -*- coding: utf-8 -*-
import re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
EXE = r"D:\Game\BLZ\StarCraft II\Support64\SC2Editor_x64.exe"
data = open(EXE, 'rb').read()
def strings(data, minlen=3):
    out, cur, start = [], b'', 0
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if not cur: start = i
            cur += bytes([b])
        else:
            if len(cur) >= minlen: out.append(cur.decode('ascii'))
            cur = b''
    return out
S = set(strings(data))
flags = sorted(s for s in S if re.fullmatch(r'-[A-Za-z][A-Za-z0-9_]{2,20}', s))
print('dash flags (%d):' % len(flags))
print('  ' + '  '.join(flags))
print()
for kw in ['Command Line', 'usage', 'Usage', 'params', 'Params', 'PARAMS', 'argc', 'argv']:
    hits = sorted(s for s in S if kw in s and len(s) < 120)
    if hits:
        print('### %s (%d)' % (kw, len(hits)))
        for h in hits[:25]: print('   ', h)
