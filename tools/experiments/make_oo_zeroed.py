# -*- coding: utf-8 -*-
"""oo: change one file's content and ZERO its digest entries in (attributes).

  * if this LOADS  -> the editor skips zeroed entries, so its digest source IS (attributes),
                      and our earlier "fixed" variant must have been updating something else
  * if this CRASHES -> the editor gets its expected digest from somewhere other than (attributes)
"""
import sys, struct
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive

SRC = 'testdata/abtest/aa_original.SC2Map'
a = MPQArchive(SRC)
raw = bytearray(open(SRC, 'rb').read())

bi = a.find_block('DocumentInfo.version')
blk = a.block_table[bi]
raw[blk.file_pos + 32] ^= 0x01                      # change the content
ai = a.find_block('(attributes)')
ab_blk = a.block_table[ai]
ab = bytearray(raw[ab_blk.file_pos:ab_blk.file_pos + ab_blk.file_size])
n = a.block_count
crc_off = 8
md5_off = 8 + 4 * n
struct.pack_into('<I', ab, crc_off + 4 * bi, 0)     # zero the CRC entry
ab[md5_off + 16 * bi: md5_off + 16 * bi + 16] = b'\x00' * 16   # zero the MD5 entry
raw[ab_blk.file_pos:ab_blk.file_pos + ab_blk.file_size] = ab
open('testdata/abtest/oo_zeroed.SC2Map', 'wb').write(bytes(raw))
m = MPQArchive('testdata/abtest/oo_zeroed.SC2Map')
print('content changed: %s' % (m.read('DocumentInfo.version') != a.read('DocumentInfo.version')))
print('entry now: crc=%s md5=%s' % (hex(m.attributes.crcs[bi]), m.attributes.md5s[bi].hex()))
print('other files still verify:', m.verify(deep=True).get('md5_bad'))
