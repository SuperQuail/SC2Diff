# -*- coding: utf-8 -*-
"""What are the components actually made of?"""
import sys, os, re, collections
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import Document

for path in ['testdata/samples/paiur01.SC2Map', 'testdata/samples/SCORE-OtherC.SC2Mod']:
    doc = Document.open(path)
    print('=' * 100)
    print(os.path.basename(path), len(doc.component_names()), 'components')
    kinds = collections.Counter()
    for name, data in doc.entries:
        if name.startswith('('):
            continue
        head = data[:200]
        if data[:5] == b'<?xml':
            kind = 'xml'
        elif data[:4] == b'cdes' or (len(data) == 44 and data[:4] == b'cdes'):
            kind = 'version-sidecar'
        elif re.match(rb'^[\x09\x0a\x0d\x20-\x7e]{8,}$', head) and b'\x00' not in head:
            kind = 'text'
        else:
            kind = 'binary'
        kinds[kind] += 1
    print('  kinds:', dict(kinds))
    xmls = [n for n, d in doc.entries if d[:5] == b'<?xml']
    print('  XML components (%d):' % len(xmls))
    for n in xmls[:22]:
        print('     %s' % n)
    # show a couple of representative heads
    for probe in ['Objects', 'Triggers', 'Base.SC2Data\\GameData\\UnitData.xml', 'DocumentInfo', 'GameData.version']:
        d = doc.get(probe)
        if d is not None:
            print('  ---- %s (%d bytes) ----' % (probe, len(d)))
            print('     ' + d[:400].decode('utf-8', 'replace').replace('\n', '\n     '))
