# -*- coding: utf-8 -*-
"""kk: change a DIFFERENT 44-byte sidecar (GameData.version) and fix its attributes.
If the editor still blames DocumentInfo.version, the failure does not track the edit."""
import sys, struct, hashlib, zlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive

SRC = 'testdata/abtest/aa_original.SC2Map'
a = MPQArchive(SRC)
raw = bytearray(open(SRC, 'rb').read())
for target in ('GameData.version',):
    bi = a.find_block(target)
    blk = a.block_table[bi]
    print('%s -> block %d pos=%d cmp=%d size=%d flags=%08x' % (target, bi, blk.file_pos, blk.cmp_size, blk.file_size, blk.flags))
    raw[blk.file_pos + 32] ^= 0x01
    new = bytes(raw[blk.file_pos:blk.file_pos + blk.file_size])
    # refresh this block's entries inside (attributes) in place
    ai = a.find_block('(attributes)')
    ab_blk = a.block_table[ai]
    ab = bytearray(raw[ab_blk.file_pos:ab_blk.file_pos + ab_blk.file_size])
    n = a.block_count
    crc_off, p = 8, 8 + 4 * n
    md5_off = p
    struct.pack_into('<I', ab, crc_off + 4 * bi, zlib.crc32(new) & 0xFFFFFFFF)
    ab[md5_off + 16 * bi: md5_off + 16 * bi + 16] = hashlib.md5(new).digest()
    raw[ab_blk.file_pos:ab_blk.file_pos + ab_blk.file_size] = ab
    print('  changed content of %s; attributes entry refreshed' % target)
open('testdata/abtest/kk_otherfile.SC2Map', 'wb').write(bytes(raw))
m = MPQArchive('testdata/abtest/kk_otherfile.SC2Map')
d = m.read('GameData.version')
print('  readback: len=%d md5_matches_attributes=%s  header_md5=%s' %
      (len(d), hashlib.md5(d).digest() == m.attributes.md5s[bi], m.verify(deep=False).get('header_md5_valid')))
