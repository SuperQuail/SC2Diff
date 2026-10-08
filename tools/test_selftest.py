# -*- coding: utf-8 -*-
"""Self-contained acceptance run: container, repository, and patch exchange on a fixture.

Needs no copyrighted sample and no installed game, so it is what CI runs.
"""
import os, re, shutil, subprocess, sys, tempfile
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fixture, sc2mpq, sc2repo

FAIL = []
def check(label, cond, extra=''):
    print('   %s %s %s' % ('PASS' if cond else 'FAIL', label, extra))
    if not cond:
        FAIL.append(label)

work = tempfile.mkdtemp(prefix='sc2diff-selftest-')
DOC = os.path.join(work, 'fixture.SC2Map')
fixture.build(DOC)
print('fixture: %s (%d bytes)' % (DOC, os.path.getsize(DOC)))

print('\n1  container round-trip')
a = sc2mpq.MPQArchive(DOC)
v = a.verify(deep=True)
check('header digest block valid', bool(v.get('header_md5_valid')))
check('HET regenerates byte-exact', bool(v.get('het_roundtrip_exact')))
check('BET regenerates byte-exact', bool(v.get('bet_roundtrip_exact')))
check('HET agrees with the classic hash table', not v.get('het_vs_classic_mismatch'))
check('BET agrees with the classic block table', not v.get('bet_vs_blocktable_mismatch'))
check('attributes verify apart from the two bookkeeping entries',
      v.get('md5_bad') == 2 and v.get('crc_bad') == 1)
check('rawChunkSize is 0 (the editor-acceptance field)',
      __import__('struct').unpack_from('<I', a.raw, 108)[0] == 0)

print('\n2  rebuild from scratch and compare every component')
doc = sc2mpq.Document.open(DOC)
OUT = os.path.join(work, 'rebuilt.SC2Map')
doc.save(OUT)
b = sc2mpq.Document.open(OUT)
orig = dict(doc.entries)
same = [n for n in orig if b.get(n) == orig[n]]
check('all components byte-identical after repack', len(same) == len(orig), '%d/%d' % (len(same), len(orig)))
vb = sc2mpq.MPQArchive(OUT).verify(deep=True)
check('rebuilt container self-consistent',
      bool(vb.get('header_md5_valid')) and bool(vb.get('het_md5_valid')) and bool(vb.get('bet_md5_valid')))

print('\n3  repository workflow')
REPO = os.path.join(work, 'repo')
sh = lambda *args, expect=0: subprocess.run(
    [sys.executable, os.path.join(HERE, 'sc2diff.py'), '-C', REPO] + list(args),
    capture_output=True, text=True, encoding='utf-8', errors='replace')
r = sh('init', DOC); check('init from a packed document', r.returncode == 0, (r.stderr or '')[:80])
sh('add', '-A'); sh('commit', '-m', 'import')
r = sh('status'); check('clean after the import commit', 'nothing to commit' in (r.stdout or ''))
obj = os.path.join(REPO, 'Objects')
txt = open(obj, encoding='utf-8').read()
open(obj, 'w', encoding='utf-8').write(txt.replace('UnitType="Medivac"', 'UnitType="Viking"', 1))
r = sh('diff')
check('semantic diff names the changed entity', 'ObjectUnit:2' in (r.stdout or ''), (r.stdout or '')[:80])
sh('add', '-A'); sh('commit', '-m', 'change unit type')
r = sh('log', '--oneline')
check('two commits', len((r.stdout or '').strip().splitlines()) == 2)
sh('branch', 'feature'); sh('switch', 'feature')
r = sh('status'); check('switched to feature', 'On branch feature' in (r.stdout or ''))

print('\n4  patch bundle exchange')
B = os.path.join(work, 'other')
sh2 = lambda *args: subprocess.run(
    [sys.executable, os.path.join(HERE, 'sc2diff.py'), '-C', B] + list(args),
    capture_output=True, text=True, encoding='utf-8', errors='replace')
BUNDLE = os.path.join(work, 'full.sc2bundle')
r = sh('checkout', 'main'); sh('bundle', BUNDLE)
check('bundle created', os.path.exists(BUNDLE))
sh2('init')
r = sh2('apply', BUNDLE)
check('applied into an empty repository', 'imported' in (r.stdout or ''), (r.stderr or '')[:80])
check('files materialised after apply', os.path.exists(os.path.join(B, 'Objects')))
check('content matches the author', open(os.path.join(B, 'Objects'), encoding='utf-8').read() ==
      open(os.path.join(REPO, 'Objects'), encoding='utf-8').read())
PACKED = os.path.join(work, 'from_bundle.SC2Map')
r = sh2('pack', PACKED)
check('receiver can repack a container', os.path.exists(PACKED) and r.returncode == 0)
pdoc = sc2mpq.Document.open(PACKED)
check('packed container holds the same components',
      all(pdoc.get(n) == doc.get(n) for n in doc.component_names() if n != 'Objects'))

print('\nFAILURES: %s' % (FAIL or 'none'))
print('RESULT: %s' % ('PASS' if not FAIL else 'FAIL'))
shutil.rmtree(work, ignore_errors=True)
sys.exit(0 if not FAIL else 1)
