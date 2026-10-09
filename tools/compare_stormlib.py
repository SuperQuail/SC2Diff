# -*- coding: utf-8 -*-
"""Compare StormLib's extraction of an archive against our own reader."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import Document

def compare(archive, extracted_dir, label):
    doc = Document.open(archive)
    mine = {n: d for n, d in doc.entries}
    theirs = {}
    for dirpath, dirnames, filenames in os.walk(extracted_dir):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, extracted_dir).replace('/', '\\')
            theirs[rel] = open(full, 'rb').read()
    missing = sorted(set(mine) - set(theirs))
    extra = sorted(set(theirs) - set(mine))
    differ = sorted(n for n in mine if n in theirs and mine[n] != theirs[n])
    print('%-52s components ours=%d storm=%d' % (label, len(mine), len(theirs)))
    print('   missing in StormLib extract : %s' % (missing or 'none'))
    print('   extra                       : %s' % (extra or 'none'))
    print('   byte-differing              : %s' % (differ or 'none'))
    return not missing and not extra and not differ

ok1 = compare('testdata/samples/paiur01.SC2Map', 'testdata/stormlib/orig', 'ORIGINAL packed by Blizzard')
ok2 = compare('testdata/rebuilt/paiur01.SC2Map', 'testdata/stormlib/repacked', 'REPACKED by sc2mpq from scratch')
print()
print('StormLib independently agrees with our reader: original=%s repacked=%s' % (ok1, ok2))
