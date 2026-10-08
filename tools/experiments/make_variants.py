# -*- coding: utf-8 -*-
"""Build bisection variants between the archive that loads and the one that crashes."""
import sys, os, struct, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
import sc2mpq
from sc2mpq import Document, MPQArchive, FLAG_EXISTS, FLAG_COMPRESS, FLAG_SINGLE_UNIT

os.makedirs('testdata/abtest', exist_ok=True)
doc = Document.open('testdata/samples/paiur01.SC2Map')
entries = list(doc.entries)

# ---- V4: everything single-unit AND the COMPRESS flag always set, exactly like Blizzard's
#          own vocabulary (Blizzard never emits a stored block without COMPRESS).
import sc2mpq as M
orig_pack = M._pack_entries
def pack_always_compress(entries_, block_size, hints=None):
    body = bytearray()
    blocks = []
    for name, data in entries_:
        if not data:
            blocks.append(M.BlockEntry(M.HEADER_SIZE_V3 + len(body), 0, 0, FLAG_EXISTS | FLAG_SINGLE_UNIT))
            continue
        payload, compressed = M._compress_single(data)
        # Blizzard keeps COMPRESS set even when it stores the data verbatim (cmp == size)
        flags = FLAG_EXISTS | FLAG_SINGLE_UNIT | FLAG_COMPRESS
        blocks.append(M.BlockEntry(M.HEADER_SIZE_V3 + len(body), len(payload), len(data), flags))
        body += payload
    return body, blocks

M._pack_entries = pack_always_compress
info = M.build_document(entries, 'testdata/abtest/dd_compressflag.SC2Map',
                        hash_count=doc.hash_count, block_shift=doc.block_shift,
                        unknown08=doc.het_unknown08, attr_flags=doc.attr_flags,
                        listfile=doc.original_listfile)
M._pack_entries = orig_pack
v = MPQArchive('testdata/abtest/dd_compressflag.SC2Map').verify(deep=False)
print('V4 dd_compressflag  %s  flags=%s' % ({k: info[k] for k in ('bytes','blocks')}, v.get('header_md5_valid')))

# ---- V5: our rebuild with HET/BET removed entirely (force the reader down the classic path)
src = open('testdata/abtest/bb_repacked.SC2Map', 'rb').read()
raw = bytearray(src)
struct.pack_into('<Q', raw, 52, 0)      # betTablePos64
struct.pack_into('<Q', raw, 60, 0)      # hetTablePos64
struct.pack_into('<Q', raw, 92, 0)      # hetTableSize64
struct.pack_into('<Q', raw, 100, 0)     # betTableSize64
raw[0xA0:0xB0] = b'\x00' * 16           # bit-table MD5 slots now empty
raw[0xB0:0xC0] = b'\x00' * 16
raw[0xC0:0xD0] = hashlib.md5(bytes(raw[:0xC0])).digest()   # header self-hash must follow
open('testdata/abtest/ee_nohet.SC2Map', 'wb').write(bytes(raw))
v2 = MPQArchive('testdata/abtest/ee_nohet.SC2Map').verify(deep=False)
print('V5 ee_nohet         bytes=%d  header_md5=%s het=%s bet=%s' % (len(raw), v2.get('header_md5_valid'), v2['het'], v2['bet']))

# sanity: both must still be readable by us
for p in ('testdata/abtest/dd_compressflag.SC2Map', 'testdata/abtest/ee_nohet.SC2Map'):
    a = MPQArchive(p)
    d = Document(a)
    orig = dict(entries)
    got = {n: d2 for n, d2 in d.entries}
    ok = all(orig[k] == got.get(k) for k in orig) and len(orig) == len(got)
    print('   %-42s components identical to original: %s (%d)' % (p, ok, len(got)))
