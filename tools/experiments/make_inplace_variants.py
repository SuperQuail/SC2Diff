# -*- coding: utf-8 -*-
"""Minimal-write experiments on the ORIGINAL archive: change one component's payload in place.

Structure is untouched: same block position, same compressed size, same file size, same flags,
so the block table / hash table / HET / BET and the header digest block all stay valid.

  ff_inplace_stale.SC2Map : payload changed, (attributes) NOT updated
  gg_inplace_fixed.SC2Map : payload changed, (attributes) entry updated in place
"""
import sys, struct, hashlib, zlib, shutil
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive, parse_attributes

SRC = 'testdata/abtest/aa_original.SC2Map'
TARGET = 'DocumentInfo.version'

a = MPQArchive(SRC)
bi = a.find_block(TARGET)
blk = a.block_table[bi]
print('target block %d: pos=%d cmp=%d size=%d flags=%08x' % (bi, blk.file_pos, blk.cmp_size, blk.file_size, blk.flags))
assert blk.cmp_size == blk.file_size, 'expected a stored block'
assert not (blk.flags & 0x00010000), 'expected unencrypted'

raw = bytearray(open(SRC, 'rb').read())
before = bytes(raw[blk.file_pos:blk.file_pos + blk.file_size])
# flip the low byte of the timestamp field at offset 32 (semantically harmless)
raw[blk.file_pos + 32] ^= 0x01
after = bytes(raw[blk.file_pos:blk.file_pos + blk.file_size])
print('payload changed at one byte; size preserved:', len(before) == len(after))

open('testdata/abtest/ff_inplace_stale.SC2Map', 'wb').write(bytes(raw))

# --- gg: also refresh this block's CRC32 and MD5 inside (attributes), in place -----------
attrs_blk_i = a.find_block('(attributes)')
attrs_blk = a.block_table[attrs_blk_i]
assert attrs_blk.cmp_size == attrs_blk.file_size
ab = bytearray(raw[attrs_blk.file_pos:attrs_blk.file_pos + attrs_blk.file_size])
version, flags = struct.unpack_from('<II', ab, 0)
n = a.block_count
p = 8
assert flags & 1, 'expected CRC32 array'
crc_off = p; p += 4 * n
assert not (flags & 2), 'unexpected FILETIME array'
md5_off = p; p += 16 * n
print('attributes version=%d flags=0x%X crc_off=%d md5_off=%d' % (version, flags, crc_off, md5_off))
struct.pack_into('<I', ab, crc_off + 4 * bi, zlib.crc32(after) & 0xFFFFFFFF)
ab[md5_off + 16 * bi: md5_off + 16 * bi + 16] = hashlib.md5(after).digest()
raw2 = bytearray(open('testdata/abtest/ff_inplace_stale.SC2Map', 'rb').read())
raw2[attrs_blk.file_pos:attrs_blk.file_pos + attrs_blk.file_size] = ab
open('testdata/abtest/gg_inplace_fixed.SC2Map', 'wb').write(bytes(raw2))
print('wrote ff_inplace_stale and gg_inplace_fixed')

for name, path in (('original', SRC), ('ff_stale', 'testdata/abtest/ff_inplace_stale.SC2Map'),
                   ('gg_fixed', 'testdata/abtest/gg_inplace_fixed.SC2Map')):
    m = MPQArchive(path)
    d = m.read(TARGET)
    v = m.verify(deep=False)
    ok_crc = (zlib.crc32(d) & 0xFFFFFFFF) == m.attributes.crcs[bi]
    ok_md5 = hashlib.md5(d).digest() == m.attributes.md5s[bi]
    print('  %-9s %s content-changed=%s  attributes crc_ok=%s md5_ok=%s  header_md5=%s  bytes=%d'
          % (name, TARGET, d != before, ok_crc, ok_md5, v.get('header_md5_valid'), len(m.raw)))
