# -*- coding: utf-8 -*-
"""Where does the sector offset table start for SECTOR_CRC blocks?"""
import sys, struct
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive
a = MPQArchive('testdata/samples/paiur01.SC2Map')
for i in range(a.block_count):
    b = a.block_table[i]
    if not b.exists or not (b.flags & 0x01000000):
        n = a.block_name(i) or ''
        if b.exists and (b.flags & 0x04000000):
            ns = (b.file_size + a.block_size - 1)//a.block_size
            offs = struct.unpack_from('<%dI' % (ns+1), a.raw, b.file_pos)
            print('%-24s flags=%08x raw=%d cmp=%d sectors=%d offsets[0]=%d  4*(n+1)=%d  offsets[-1]=%d'
                  % (n, b.flags, b.file_size, b.cmp_size, ns, offs[0], 4*(ns+1), offs[-1]))
