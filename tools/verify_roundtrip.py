# -*- coding: utf-8 -*-
"""Round-trip oracle: parse a real SC2 document, regenerate its tables, compare bytes."""
import sys, glob, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive

paths = sys.argv[1:] or sorted(glob.glob('testdata/samples/*.SC2Map') + glob.glob('testdata/samples/*.SC2Mod'))
if not paths:
    print('SKIP: no sample documents under testdata/samples (they are copyrighted and not '
          'shipped). Run tools/test_selftest.py for the self-contained equivalent.')
    sys.exit(0)
for p in paths:
    try:
        a = MPQArchive(p)
    except Exception as e:
        print('%-34s OPEN FAILED: %s' % (os.path.basename(p), e)); continue
    r = a.verify(deep=True)
    print('=' * 96)
    print('%-30s %10d bytes  blocks=%-3d hash=%-4d fmt=%d hdr=%d' %
          (os.path.basename(p), os.path.getsize(p), r['blocks'], r['hash_entries'], r['format_version'], r['header_size']))
    print('  HET=%s BET=%s attributes=%s names=%d' % (r['het'], r['bet'], r['attributes'], r['names']))
    print('  HET regenerate byte-exact : %s' % r.get('het_roundtrip_exact'))
    print('  BET regenerate byte-exact : %s' % r.get('bet_roundtrip_exact'))
    print('  HET vs classic-hash lookup mismatches : %s' % (r.get('het_vs_classic_mismatch') or 'none'))
    print('  BET vs classic block-table mismatches : %s' % (r.get('bet_vs_blocktable_mismatch') or 'none'))
    print('  BET flag mismatches                   : %s' % (r.get('bet_flag_mismatch') or 'none'))
    print('  MD5 ok=%s bad=%s | CRC ok=%s bad=%s' % (r.get('md5_ok'), r.get('md5_bad'), r.get('crc_ok'), r.get('crc_bad')))
    if r.get('md5_exceptions'):
        print('  MD5 exceptions (= expected for the bookkeeping files): %s' % r['md5_exceptions'])
