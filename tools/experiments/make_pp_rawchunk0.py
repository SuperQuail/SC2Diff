# -*- coding: utf-8 -*-
"""pp: change one file's content with (attributes) fixed AND dwRawChunkSize set to 0.

Blizzard's System_Mopaq has an MD5VerifyData subsystem whose chunking is driven by the
header's dwRawChunkSize (StormLib's AllocateRawMD5s keys off the same field). If that is
what is failing, disabling it should let the archive load -- and it would also be the fix
for our writer.
"""
import sys, struct, hashlib, zlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive

SRC = 'testdata/abtest/aa_original.SC2Map'
a = MPQArchive(SRC)
raw = bytearray(open(SRC, 'rb').read())

bi = a.find_block('DocumentInfo.version')
blk = a.block_table[bi]
raw[blk.file_pos + 32] ^= 0x01
new = bytes(raw[blk.file_pos:blk.file_pos + blk.file_size])

ai = a.find_block('(attributes)')
ab_blk = a.block_table[ai]
ab = bytearray(raw[ab_blk.file_pos:ab_blk.file_pos + ab_blk.file_size])
n = a.block_count
struct.pack_into('<I', ab, 8 + 4 * bi, zlib.crc32(new) & 0xFFFFFFFF)
md5_off = 8 + 4 * n
ab[md5_off + 16 * bi: md5_off + 16 * bi + 16] = hashlib.md5(new).digest()
raw[ab_blk.file_pos:ab_blk.file_pos + ab_blk.file_size] = ab

print('dwRawChunkSize was %d' % struct.unpack_from('<I', raw, 108)[0])
struct.pack_into('<I', raw, 108, 0)                      # disable raw-chunk MD5
raw[0xC0:0xD0] = hashlib.md5(bytes(raw[:0xC0])).digest()  # header self-hash must follow
open('testdata/abtest/pp_rawchunk0.SC2Map', 'wb').write(bytes(raw))

m = MPQArchive('testdata/abtest/pp_rawchunk0.SC2Map')
v = m.verify(deep=False)
print('readback: rawChunkSize=%d header_md5=%s md5_bad=%s content_changed=%s size=%d' % (
    struct.unpack_from('<I', m.raw, 108)[0], v.get('header_md5_valid'), v.get('md5_bad'),
    m.read('DocumentInfo.version') != a.read('DocumentInfo.version'), len(m.raw)))
