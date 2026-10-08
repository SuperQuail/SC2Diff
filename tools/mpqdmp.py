# -*- coding: utf-8 -*-
"""Recon: MPQ container + integrity metadata forensics for SC2 documents."""
import struct, sys, zlib, bz2, os, hashlib, lzma

def crypt_table():
    seed = 0x00100001; t = [0]*0x500
    for i1 in range(0x100):
        i2 = i1
        for _ in range(5):
            seed = (seed*125+3) % 0x2AAAAB; t1 = (seed & 0xFFFF) << 16
            seed = (seed*125+3) % 0x2AAAAB; t2 = seed & 0xFFFF
            t[i2] = (t1 | t2) & 0xFFFFFFFF; i2 += 0x100
    return t
CRYPT = crypt_table()

def hash_string(s, ht):
    s1, s2 = 0x7FED7FED, 0xEEEEEEEE
    for b in s.upper().encode('latin-1','replace'):
        s1 = (CRYPT[(ht<<8)+b] ^ ((s1+s2) & 0xFFFFFFFF)) & 0xFFFFFFFF
        s2 = (b + s1 + s2 + (s2<<5) + 3) & 0xFFFFFFFF
    return s1

def decrypt(data, key):
    s1, s2 = key & 0xFFFFFFFF, 0xEEEEEEEE
    out = bytearray()
    for i in range(0, len(data) & ~3, 4):
        dw = struct.unpack_from('<I', data, i)[0]
        s2 = (s2 + CRYPT[0x400 + (s1 & 0xFF)]) & 0xFFFFFFFF
        dw = (dw ^ ((s1+s2) & 0xFFFFFFFF)) & 0xFFFFFFFF
        s1 = ((((~s1 & 0xFFFFFFFF) << 0x15) + 0x11111111) | (s1 >> 0x0B)) & 0xFFFFFFFF
        s2 = (dw + s2 + (s2<<5) + 3) & 0xFFFFFFFF
        out += struct.pack('<I', dw)
    return bytes(out)

MASK_NAMES = {0x01:'huff',0x02:'zlib',0x08:'pkware',0x10:'bzip2',0x12:'lzma',0x20:'sparse',0x40:'adpcm-m',0x80:'adpcm-s'}

def decompress(chunk, expected):
    if len(chunk) == expected: return chunk
    if not chunk: return b''
    mask = chunk[0]; payload = chunk[1:]
    if mask == 0: return payload
    if mask & 0x02: return zlib.decompress(payload)
    if mask & 0x10: return bz2.decompress(payload)
    if mask == 0x12: return lzma.decompress(payload)
    if mask & 0x20: return payload   # sparse: not needed for recon
    if mask & 0x01: return payload
    raise NotImplementedError('mask 0x%02X (%s)' % (mask, MASK_NAMES.get(mask,'?')))

class MPQ:
    def __init__(self, path):
        self.path = path; self.data = open(path, 'rb').read(); d = self.data
        (self.header_size, self.archive_size, self.fmt_ver, self.block_shift,
         self.hash_pos, self.block_pos, self.hash_entries, self.block_entries) = struct.unpack_from('<IIHHIIII', d, 4)
        self.block_size = 512 << self.block_shift
        self.archive_size64 = struct.unpack_from('<Q', d, 44)[0]
        self.bet_pos64 = struct.unpack_from('<Q', d, 52)[0]
        self.het_pos64 = struct.unpack_from('<Q', d, 60)[0]
        self.het_size64 = struct.unpack_from('<Q', d, 92)[0]
        self.bet_size64 = struct.unpack_from('<Q', d, 100)[0]
        self.hash_table = self._tbl(d[self.hash_pos:self.hash_pos+self.hash_entries*16], '(hash table)', '<IIHHI')
        self.block_table = self._tbl(d[self.block_pos:self.block_pos+self.block_entries*16], '(block table)', '<IIII')
        self.attrs = None

    def _tbl(self, blob, keyname, fmt):
        dec = decrypt(blob, hash_string(keyname, 3))
        cand = [struct.unpack_from(fmt, dec, i*16) for i in range(len(dec)//16)]
        if fmt == '<IIHHI':
            if all((e[4] == 0xFFFFFFFF) or (e[4] < self.block_entries) for e in cand) and any(e[4] == 0xFFFFFFFF for e in cand):
                return cand
        else:
            if all((not (e[3] & 0x80000000)) or (e[0]+e[1] <= len(self.data)) for e in cand):
                return cand
        return [struct.unpack_from(fmt, blob, i*16) for i in range(len(blob)//16)]

    def find(self, name):
        idx = hash_string(name, 0) & (len(self.hash_table)-1)
        for _ in range(len(self.hash_table)):
            ha, hb, loc, plat, bi = self.hash_table[idx]
            if bi == 0xFFFFFFFF: return None
            if ha == hash_string(name,1) and hb == hash_string(name,2) and bi < len(self.block_table):
                return bi
            idx = (idx+1) & (len(self.hash_table)-1)
        return None

    def stored(self, bi):
        pos, csize, fsize, flags = self.block_table[bi]
        return self.data[pos:pos+csize]

    def read_block(self, bi, name):
        pos, csize, fsize, flags = self.block_table[bi]
        if not (flags & 0x80000000): return None
        if fsize == 0: return b''
        raw = self.data[pos:pos+csize]
        if flags & 0x00010000:
            key = hash_string(name.replace('/', '\\').split('\\')[-1], 3)
            if flags & 0x00020000: key = (key + pos) & 0xFFFFFFFF
            raw = decrypt(raw, key)
        return self._decode(raw, fsize, flags)

    def read(self, name):
        bi = self.find(name)
        return None if bi is None else self.read_block(bi, name)

    def _decode(self, raw, fsize, flags):
        if flags & 0x00000100: return decompress(raw, fsize)
        if flags & 0x01000000:
            return raw if len(raw) == fsize else decompress(raw, fsize)
        nsec = (fsize + self.block_size - 1)//self.block_size
        offs = struct.unpack_from('<%dI' % (nsec+1), raw, 0)
        out = bytearray()
        for i in range(nsec):
            exp = min(self.block_size, fsize - len(out))
            out += decompress(raw[offs[i]:offs[i+1]], exp)
        return bytes(out)

def name_map(m, names):
    d = {}
    for n in names: 
        bi = m.find(n)
        if bi is not None: d[bi] = n
    for s in ('(listfile)','(attributes)','(signature)'):
        bi = m.find(s)
        if bi is not None: d[bi] = s
    return d

def dump(path, deep=True):
    print('='*100); print('FILE', path, os.path.getsize(path))
    m = MPQ(path)
    print('  headerSize=%d fmtVer=%d blockShift=%d (blockSize=%d) hashPos=%d blockPos=%d hash=%d block=%d'
          % (m.header_size, m.fmt_ver, m.block_shift, m.block_size, m.hash_pos, m.block_pos, m.hash_entries, m.block_entries))
    print('  HET pos=%d size=%d | BET pos=%d size=%d' % (m.het_pos64, m.het_size64, m.bet_pos64, m.bet_size64))
    if m.het_pos64:
        het = m.data[m.het_pos64:m.het_pos64+16]
        hdr = struct.unpack_from('<IIIIIIII', decrypt(m.data[m.het_pos64+12:m.het_pos64+44], hash_string('(hash table)',3)), 0)
        print('  HET sig=%r ver=%d dataSize=%d' % (het[0:4], struct.unpack_from('<I',het,4)[0], struct.unpack_from('<I',het,8)[0]))
        print('      tableSize=%d maxFileCount=%d hashTableSize=%d hashEntrySize=%d totalIndexSize=%d indexSizeExtra=%d indexSize=%d blockTableSize=%d' % hdr)
        bet = m.data[m.bet_pos64:m.bet_pos64+16]
        bh = struct.unpack_from('<IIIIIIIIII', decrypt(m.data[m.bet_pos64+12:m.bet_pos64+52], hash_string('(block table)',3)), 0)
        print('  BET sig=%r ver=%d dataSize=%d' % (bet[0:4], struct.unpack_from('<I',bet,4)[0], struct.unpack_from('<I',bet,8)[0]))
        print('      tableSize=%d entryCount=%d fileCount=%d fileTableSize=%d fileTableBits=%d fileTableIndexSize=%d flagCount=%d flagTableSize=%d flagTableBits=%d flagTableIndexSize=%d' % bh)
    listing = m.read('(listfile)')
    names = [l.strip() for l in listing.decode('utf-8','replace').splitlines() if l.strip()] if listing else []
    nm = name_map(m, names)
    print('  (listfile)=%s bytes, %d names' % (len(listing) if listing else None, len(names)))
    ab = m.read('(attributes)')
    live = [(i,)+tuple(m.block_table[i]) for i in range(m.block_entries) if m.block_table[i][3] & 0x80000000]
    print('  live blocks=%d' % len(live))
    if ab:
        ver, flags = struct.unpack_from('<II', ab, 0)
        n = m.block_entries; p = 8
        crcs = fts = md5s = None
        if flags & 1: crcs = struct.unpack_from('<%dI' % n, ab, p); p += 4*n
        if flags & 2: fts = struct.unpack_from('<%dQ' % n, ab, p); p += 8*n
        if flags & 4: md5s = [ab[p+16*i:p+16*i+16] for i in range(n)]; p += 16*n
        print('  (attributes) size=%d version=%d flags=0x%X parsed=%d/%d crc=%s ft=%s md5=%s'
              % (len(ab), ver, flags, p, len(ab), crcs is not None, fts is not None, md5s is not None))
        if deep and md5s:
            ok_d = bad_d = ok_s = bad_s = 0
            for i in range(n):
                if not (m.block_table[i][3] & 0x80000000): continue
                label = nm.get(i, '?')
                st = m.stored(i)
                try: dc = m.read_block(i, label if label != '?' else '(listfile)')
                except Exception as e: dc = None
                if dc is not None and hashlib.md5(dc).digest() == md5s[i]: ok_d += 1
                elif dc is not None: bad_d += 1
                if hashlib.md5(st).digest() == md5s[i]: ok_s += 1
                else: bad_s += 1
            print('  MD5 verify: decompressed ok=%d bad=%d | stored-bytes ok=%d bad=%d' % (ok_d, bad_d, ok_s, bad_s))
        if deep and crcs:
            okc = badc = 0
            for i in range(n):
                if not (m.block_table[i][3] & 0x80000000): continue
                if i == m.find('(attributes)'): continue
                label = nm.get(i)
                try: dc = m.read_block(i, label) if label else None
                except Exception: dc = None
                if dc is None: continue
                if (zlib.crc32(dc) & 0xFFFFFFFF) == crcs[i]: okc += 1
                else: badc += 1
            print('  CRC32 verify: ok=%d bad=%d' % (okc, badc))
    print('  --- files ---')
    for i in range(m.block_entries):
        pos, csize, fsize, flags = m.block_table[i]
        if not (flags & 0x80000000): continue
        print('    blk %2d %-52s pos=%8d cmp=%8d size=%8d flags=0x%08X' % (i, nm.get(i,'?')[:52], pos, csize, fsize, flags))
    return m

if __name__ == '__main__':
    for p in sys.argv[1:]:
        try: dump(p)
        except Exception as e:
            import traceback; traceback.print_exc()
