# -*- coding: utf-8 -*-
import sys, os, bz2, zlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive
a = MPQArchive('testdata/samples/paiur01.SC2Map')
rows = []
for i in range(a.block_count):
    b = a.block_table[i]
    if not b.exists: continue
    n = a.block_name(i) or ''
    if b.file_size < 20000: continue
    data = a.read_block(i, n)
    if data is None: continue
    mine_bz = len(bz2.compress(data, 9)) + 1
    mine_zl = len(zlib.compress(data, 9)) + 1
    rows.append((b.file_size, b.cmp_size, mine_bz, mine_zl, n))
rows.sort(reverse=True)
print('%-46s %10s %10s %10s %10s' % ('component', 'raw', 'blizzard', 'my bz2', 'my zlib'))
for size, orig, bz, zl, n in rows[:14]:
    print('%-46s %10d %10d %10d %10d' % (n[:46], size, orig, bz, zl))
tot_o = sum(r[1] for r in rows); tot_bz = sum(r[2] for r in rows); tot_zl = sum(min(r[2],r[3]) for r in rows)
print('totals for these blocks: blizzard=%d  mine-bz2=%d  mine-best=%d  (%.2fx)' % (tot_o, tot_bz, tot_zl, tot_zl/tot_o))
