# -*- coding: utf-8 -*-
"""Identify every field of the 208-byte header by computing candidate digests."""
import sys, struct, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
p = 'testdata/samples/paiur01.SC2Map'
raw = open(p,'rb').read()
h = raw[:208]
def u32(o): return struct.unpack_from('<I', h, o)[0]
def u64(o): return struct.unpack_from('<Q', h, o)[0]
print('magic=%r headerSize=%d archiveSize=%d fmt=%d shift=%d' % (h[:4], u32(4), u32(8), struct.unpack_from('<H',h,12)[0], struct.unpack_from('<H',h,14)[0]))
print('hashPos=%d blockPos=%d hashEntries=%d blockEntries=%d' % (u32(16), u32(20), u32(24), u32(28)))
print('hiBlockTablePos64=%d archiveSize64=%d betPos64=%d hetPos64=%d' % (u64(32), u64(44), u64(52), u64(60)))
print('hashTableSize64=%d blockTableSize64=%d hiBlockTableSize64=%d hetTableSize64=%d betTableSize64=%d rawChunkSize=%d'
      % (u64(68), u64(76), u64(84), u64(92), u64(100), u32(108)))
print()
stored = {0x70:'field@0x70', 0x80:'field@0x80', 0x90:'field@0x90', 0xA0:'field@0xA0', 0xB0:'field@0xB0', 0xC0:'field@0xC0'}
for off, label in stored.items():
    print('  %-12s %s' % (label, h[off:off+16].hex()))
print()
cands = {}
cands['md5(header with MD5 block zeroed)'] = hashlib.md5(h[:112] + b'\x00'*96).digest()
cands['md5(header[0:112])'] = hashlib.md5(h[:112]).digest()
cands['md5(header[0:208] zeroed 0x70..0x7F)'] = hashlib.md5(h[:0x70] + b'\x00'*16 + h[0x80:]).digest()
cands['md5(hash table bytes)'] = hashlib.md5(raw[u32(16):u32(16)+u32(24)*16]).digest()
cands['md5(block table bytes)'] = hashlib.md5(raw[u32(20):u32(20)+u32(28)*16]).digest()
het_size = u64(92); bet_size = u64(100)
cands['md5(het bytes)'] = hashlib.md5(raw[u64(60):u64(60)+het_size]).digest()
cands['md5(bet bytes)'] = hashlib.md5(raw[u64(52):u64(52)+bet_size]).digest()
for name, d in cands.items():
    hit = [lbl for off, lbl in stored.items() if h[off:off+16] == d]
    print('  %-42s %s  -> %s' % (name, d.hex()[:32], hit or 'no match'))
