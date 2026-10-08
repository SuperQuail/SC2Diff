# -*- coding: utf-8 -*-
"""Repack fidelity: unpack a real document, rebuild it from scratch, read it back, compare."""
import sys, os, glob, hashlib, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import Document, MPQArchive

OUT = 'testdata/rebuilt'
os.makedirs(OUT, exist_ok=True)

paths = sys.argv[1:] or sorted(glob.glob('testdata/samples/*.SC2Map') + glob.glob('testdata/samples/*.SC2Mod'))
if not paths:
    print('SKIP: no sample documents under testdata/samples (they are copyrighted and not '
          'shipped). Run tools/test_selftest.py for the self-contained equivalent.')
    sys.exit(0)
summary = []
for p in paths:
    base = os.path.basename(p)
    doc = Document.open(p)
    dest = os.path.join(OUT, base)
    info = doc.save(dest)
    src = doc.archive
    two = MPQArchive(dest)
    v = two.verify(deep=True)

    orig = dict(doc.entries)
    got = {}
    for i in range(two.block_count):
        if not two.block_table[i].exists:
            continue
        n = two.block_name(i)
        if n is None:
            continue
        got[n] = two.read_block(i, n)

    missing = [n for n in orig if n not in got]
    extra = [n for n in got if n not in orig]
    differing = [n for n in orig if n in got and orig[n] != got[n]]

    ok = not missing and not extra and not differing and v.get('het_roundtrip_exact') and v.get('bet_roundtrip_exact') \
         and not v.get('het_vs_classic_mismatch') and not v.get('bet_vs_blocktable_mismatch') \
         and not v.get('bet_flag_mismatch') and v.get('md5_bad') == 2 and v.get('crc_bad') == 1

    print('=' * 96)
    print('%-30s %10d -> %10d bytes   components=%d' % (base, os.path.getsize(p), os.path.getsize(dest), len(orig)))
    print('  entries: original=%d rebuilt=%d  missing=%s extra=%s differing=%s'
          % (len(orig), len(got), missing or 'none', extra or 'none', differing or 'none'))
    print('  rebuilt HET byte-exact=%s  BET byte-exact=%s  HET/classic mismatches=%s  BET/block mismatches=%s  BET flags=%s'
          % (v.get('het_roundtrip_exact'), v.get('bet_roundtrip_exact'),
             v.get('het_vs_classic_mismatch') or 'none', v.get('bet_vs_blocktable_mismatch') or 'none',
             v.get('bet_flag_mismatch') or 'none'))
    print('  attributes: md5 ok=%s bad=%s (expect 2 skipped: listfile/attributes)  crc ok=%s bad=%s (expect 1)'
          % (v.get('md5_ok'), v.get('md5_bad'), v.get('crc_ok'), v.get('crc_bad')))
    print('  rebuilt blocks=%d hash=%d  block_size=%d' % (info['blocks'], info['hash_entries'], info['block_size']))
    print('  RESULT: %s' % ('PASS' if ok else 'FAIL'))
    summary.append(dict(file=base, ok=ok, src_size=os.path.getsize(p), dst_size=os.path.getsize(dest),
                        entries=len(orig), missing=missing, extra=extra, differing=differing, verify=v))

print()
print('passed %d / %d' % (sum(1 for s in summary if s['ok']), len(summary)))
