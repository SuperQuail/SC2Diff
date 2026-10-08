# -*- coding: utf-8 -*-
"""Last details needed by the writer: compression masks used, bookkeeping-entry attributes,
   and whether the remaining HET/classic 'mismatches' are just absent names."""
import sys, os, collections, zlib, hashlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive, jenkins_name_hash

for path in ['testdata/samples/paiur01.SC2Map', 'testdata/samples/HTXL.SC2Mod', 'testdata/samples/UmojanCT.SC2Mod']:
    a = MPQArchive(path)
    masks = collections.Counter()
    flagsets = collections.Counter()
    for i in range(a.block_count):
        b = a.block_table[i]
        if not b.exists:
            continue
        flagsets[hex(b.flags)] += 1
        if not (b.flags & 0x00000200):
            masks['stored'] += 1
            continue
        if b.flags & 0x01000000:
            chunk = a.raw[b.file_pos:b.file_pos + b.cmp_size]
            masks['single:0x%02X' % chunk[0]] += 1
        else:
            masks['sector'] += 1
    print('=' * 96)
    print(os.path.basename(path))
    print('  flag sets        : %s' % dict(flagsets))
    print('  compression masks: %s' % dict(masks))
    if a.attributes:
        li = a.find_block('(listfile)'); ab = a.find_block('(attributes)')
        print('  (listfile)  block %s md5=%s crc=%08x' % (li, a.attributes.md5s[li].hex(), a.attributes.crcs[li]))
        print('  (attributes) block %s md5=%s crc=%08x' % (ab, a.attributes.md5s[ab].hex(), a.attributes.crcs[ab]))
        others = [i for i in range(a.block_count) if i not in (li, ab) and a.block_table[i].exists]
        print('  sample normal md5=%s crc=%08x' % (a.attributes.md5s[others[0]].hex(), a.attributes.crcs[others[0]]))
    # are the HET/classic mismatches simply names that are not in the archive?
    names = a.names()
    for n in names:
        c = a.find_block(n); h = a.het.get_file_index(n)
        if c != h:
            print('  mismatch %-40s classic=%s het=%s  (classic<0 means name absent)' % (n, c, h))
