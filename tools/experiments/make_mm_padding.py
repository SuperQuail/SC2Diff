# -*- coding: utf-8 -*-
"""mm: flip ONE byte in the archive's inter-table padding.

That region is covered by nothing: not by any file's CRC32/MD5 in (attributes), not by the
header's table digests (which cover the hash/block/HET/BET tables and the header itself).
If this loads, any content change really is being verified; if it crashes, the editor is
rejecting the archive for a reason unrelated to the bytes we changed.
"""
import sys, struct, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive

SRC = 'testdata/abtest/aa_original.SC2Map'
a = MPQArchive(SRC)
# widest uncovered gap: between the end of the last data block and the HET table
ends = []
names = a.names() + ['(listfile)', '(attributes)']
for n in names:
    bi = a.find_block(n)
    if bi is not None:
        b = a.block_table[bi]
        if b.exists:
            ends.append(b.file_pos + b.cmp_size)
last_data_end = max(ends)
print('last data block ends at %d ; HET at %d ; gap = %d bytes' % (last_data_end, a.het_pos, a.het_pos - last_data_end))
gap_start, gap_end = last_data_end, a.het_pos
assert gap_end > gap_start, 'no gap to probe'
raw = bytearray(open(SRC, 'rb').read())
pos = gap_start + (gap_end - gap_start) // 2
print('flipping byte at 0x%X (padding, covered by nothing)' % pos)
raw[pos] ^= 0xFF
open('testdata/abtest/mm_padding.SC2Map', 'wb').write(bytes(raw))
m = MPQArchive('testdata/abtest/mm_padding.SC2Map')
v = m.verify(deep=False)
print('after flip: header_md5=%s block_md5=%s hash_md5=%s het_md5=%s bet_md5=%s  size=%d' %
      (v.get('header_md5_valid'), v.get('block_table_md5_valid'), v.get('hash_table_md5_valid'),
       v.get('het_md5_valid'), v.get('bet_md5_valid'), len(m.raw)))
d = __import__('sc2mpq').Document(m)
orig = __import__('sc2mpq').Document(MPQArchive(SRC))
same = all(orig.get(n) == d.get(n) for n in orig.component_names())
print('all component contents unchanged:', same)
