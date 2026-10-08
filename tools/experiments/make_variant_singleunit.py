# -*- coding: utf-8 -*-
"""Variant: rebuild with no sector-based blocks at all (removes the SECTOR_CRC difference)."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
import sc2mpq

doc = sc2mpq.Document.open('testdata/samples/paiur01.SC2Map')
entries = [(n, d) for n, d in doc.entries]
out = 'testdata/abtest/cc_singleunit.SC2Map'
info = sc2mpq.build_document(entries, out, hash_count=doc.hash_count, block_shift=doc.block_shift,
                             unknown08=doc.het_unknown08, attr_flags=doc.attr_flags,
                             listfile=doc.original_listfile, hints={})   # hints={} -> everything single-unit
print('built', out, info)
a = sc2mpq.MPQArchive(out)
v = a.verify(deep=False)
print('header_md5=%s het=%s bet=%s attrs=%s blocks=%d' % (
    v.get('header_md5_valid'), v.get('het_md5_valid'), v.get('bet_md5_valid'), v.get('attributes'), v['blocks']))
# sector blocks remaining?
sect = [i for i in range(a.block_count) if a.block_table[i].exists and not (a.block_table[i].flags & 0x01000000)]
print('sector-based blocks remaining:', sect)
# component fidelity vs original
orig = dict(entries)
got = {}
for i in range(a.block_count):
    if not a.block_table[i].exists: continue
    n = a.block_name(i)
    if n: got[n] = a.read_block(i, n)
print('components identical to original:', all(orig[k] == got.get(k) for k in orig), '(%d)' % len(orig))
