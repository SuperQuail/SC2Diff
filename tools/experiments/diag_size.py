# -*- coding: utf-8 -*-
"""Where does the rebuilt archive differ in size from the original?"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive
for pair in [('testdata/samples/paiur01.SC2Map', 'testdata/rebuilt/paiur01.SC2Map')]:
    a = MPQArchive(pair[0]); b = MPQArchive(pair[1])
    print('%-46s %10s %10s %8s' % ('component', 'orig cmp', 'new cmp', 'delta'))
    rows = []
    for i in range(a.block_count):
        if not a.block_table[i].exists: continue
        n = a.block_name(i) or ''
        j = b.find_block(n)
        if j < 0: continue
        rows.append((b.block_table[j].cmp_size - a.block_table[i].cmp_size, a.block_table[i].cmp_size,
                     b.block_table[j].cmp_size, n, a.block_table[i].flags, b.block_table[j].flags, a.block_table[i].file_size))
    rows.sort(reverse=True)
    for d, o, nw, name, fa, fb, fs_ in rows[:14]:
        print('%-46s %10d %10d %8d  flags %08x->%08x  raw=%d' % (name[:46], o, nw, d, fa, fb, fs_))
    print('  top-14 delta sum = %d ; base cmp total=%d new=%d' %
          (sum(r[0] for r in rows[:14]),
           sum(a.block_table[i].cmp_size for i in range(a.block_count) if a.block_table[i].exists),
           sum(b.block_table[i].cmp_size for i in range(b.block_count) if b.block_table[i].exists)))
