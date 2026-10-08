# -*- coding: utf-8 -*-
"""What EXACTLY differs between the archive that loads and the one that crashes?"""
import sys, collections, struct
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive

A = MPQArchive('testdata/abtest/aa_original.SC2Map')     # LOADS
B = MPQArchive('testdata/abtest/bb_repacked.SC2Map')     # CRASHES

def flagsets(m):
    c = collections.Counter()
    for i in range(m.block_count):
        if m.block_table[i].exists: c[hex(m.block_table[i].flags)] += 1
    return dict(c)

print('== block flag vocabulary ==')
print('  original :', flagsets(A))
print('  repacked :', flagsets(B))
print()
print('== HET ==')
for lbl, t in (('original', A.het), ('repacked', B.het)):
    print('  %-9s tableSize=%d entryCount=%d totalCount=%d nameHashBitSize=%d indexSizeTotal=%d indexSizeExtra=%d indexSize=%d indexTableSize=%d'
          % (lbl, t.table_size, t.entry_count, t.total_count, t.name_hash_bit_size,
             t.index_size_total, t.index_size_extra, t.index_size, t.index_table_size))
print('  nameHashes identical :', A.het.name_hashes == B.het.name_hashes)
print('  indexBits  identical :', A.het.index_bits == B.het.index_bits)
print()
print('== BET ==')
fields = ['table_size','entry_count','unknown08','table_entry_size','bit_index_file_pos','bit_index_file_size',
          'bit_index_cmp_size','bit_index_flag_index','bit_index_unknown','bit_count_file_pos','bit_count_file_size',
          'bit_count_cmp_size','bit_count_flag_index','bit_count_unknown','bit_total_name_hash2','bit_extra_name_hash2',
          'bit_count_name_hash2','name_hash_array_size','flag_count']
for f in fields:
    va, vb = getattr(A.bet, f), getattr(B.bet, f)
    if va != vb:
        print('  DIFF %-22s original=%-8s repacked=%s' % (f, va, vb))
print('  original flags   :', [hex(x) for x in A.bet.flags])
print('  repacked flags   :', [hex(x) for x in B.bet.flags])
print('  fileBits identical:', A.bet.file_bits == B.bet.file_bits)
print('  nameHashBits identical:', A.bet.name_hash_bits == B.bet.name_hash_bits)
print()
print('== per-block flag table (original -> repacked) ==')
diff = [i for i in range(A.block_count) if A.block_table[i].flags != B.block_table[i].flags]
print('  blocks whose flags differ: %s' % diff)
for i in diff:
    print('    blk %2d %-46s orig=%08x new=%08x' % (i, (A.block_name(i) or '')[:46],
          A.block_table[i].flags, B.block_table[i].flags))
print()
print('== attributes ==')
print('  original version=%d flags=0x%X crc_len=%d md5_len=%d' % (A.attributes.version, A.attributes.flags, len(A.attributes.crcs), len(A.attributes.md5s)))
print('  repacked version=%d flags=0x%X crc_len=%d md5_len=%d' % (B.attributes.version, B.attributes.flags, len(B.attributes.crcs), len(B.attributes.md5s)))
print('  arrays identical:', A.attributes.crcs == B.attributes.crcs, A.attributes.md5s == B.attributes.md5s)
print()
print('== header ==')
ha, hb = A.raw[:208], B.raw[:208]
for off, name in ((0x04,'headerSize'),(0x08,'archiveSize'),(0x0C,'fmtVersion'),(0x0E,'blockShift'),
                  (0x18,'hashEntries'),(0x1C,'blockEntries'),(0x6C,'rawChunkSize')):
    fmt = '<H' if name in ('fmtVersion','blockShift') else '<I'
    print('  %-12s orig=%-10s repacked=%s' % (name, struct.unpack_from(fmt, ha, off)[0], struct.unpack_from(fmt, hb, off)[0]))
