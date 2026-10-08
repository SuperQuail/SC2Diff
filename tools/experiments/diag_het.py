# -*- coding: utf-8 -*-
"""Focused diagnosis of the HET table and the BET flag endianness."""
import sys, os, struct
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive, decrypt, hash_string, HASH_FILE_KEY, bits_get, jenkins_name_hash
import sc2mpq as M

for path in ['testdata/samples/paiur01.SC2Map', 'testdata/samples/HTXL.SC2Mod']:
    a = MPQArchive(path)
    print('=' * 100)
    print(os.path.basename(path))
    het_raw = a._ext_table(a.het_pos, a.het_size)
    het_new = a.het.to_bytes()
    print('  HET stored len=%d  regenerated len=%d  dataSize=%d' %
          (len(het_raw), len(het_new), struct.unpack_from('<I', het_raw, 8)[0]))
    n = min(len(het_raw), len(het_new))
    diffs = [i for i in range(n) if het_raw[i] != het_new[i]]
    print('  HET differing bytes: %d  first at %s' % (len(diffs), diffs[:8]))
    print('  HET fields: tableSize=%d entryCount=%d totalCount=%d nameHashBitSize=%d indexSizeTotal=%d indexSizeExtra=%d indexSize=%d indexTableSize=%d'
          % (a.het.table_size, a.het.entry_count, a.het.total_count, a.het.name_hash_bit_size,
             a.het.index_size_total, a.het.index_size_extra, a.het.index_size, a.het.index_table_size))
    print('  expected indexTableSize = ((%d*%d)+7)//8 = %d' %
          (a.het.total_count, a.het.index_size_total, (a.het.total_count * a.het.index_size_total + 7) // 8))
    if diffs:
        i = diffs[0]
        print('  around first diff (index %d): stored=%s new=%s' % (i, het_raw[max(0,i-4):i+8].hex(), het_new[max(0,i-4):i+8].hex()))
    # BET flag bytes
    if a.bet is not None:
        body = decrypt(a.raw[a.bet_pos + 12:a.bet_pos + 12 + struct.unpack_from('<I', a.raw, a.bet_pos + 8)[0]],
                       hash_string('(block table)', HASH_FILE_KEY))
        rawflags = body[76:76 + 4 * a.bet.flag_count]
        print('  BET flagCount=%d raw bytes=%s  as-LE=%s  as-BE=%s' %
              (a.bet.flag_count, rawflags.hex(),
               [hex(v) for v in struct.unpack('<%dI' % a.bet.flag_count, rawflags)],
               [hex(v) for v in struct.unpack('>%dI' % a.bet.flag_count, rawflags)]))
        print('  classic flags present: %s' % sorted({hex(b.flags) for b in a.block_table if b.exists}))
        print('  BET unknown08=%d (0x%X)' % (a.bet.unknown08, a.bet.unknown08))
    # HET lookup vs classic for the mismatching names
    names = a.names()
    bad = [n for n in names if a.find_block(n) != a.het.get_file_index(n)]
    print('  HET/classic mismatches: %d %s' % (len(bad), bad[:6]))
    for n in bad[:4]:
        fh = jenkins_name_hash(n, a.het.name_hash_bit_size)
        nh1 = (fh >> (a.het.name_hash_bit_size - 8)) & 0xFF
        idx = fh % a.het.total_count
        print('    %-46s classic=%s het=%s  jenkins=%016x hash1=%02x startIdx=%d storedHashAtStart=%02x'
              % (n, a.find_block(n), a.het.get_file_index(n), fh, nh1, idx, a.het.name_hashes[idx]))
    print('  name->index map from classic: count=%d, distinct blocks=%d'
          % (len(names), len({a.find_block(n) for n in names})))
