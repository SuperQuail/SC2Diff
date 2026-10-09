# -*- coding: utf-8 -*-
"""Patch-bundle exchange: the workflow that replaces a remote, with no server involved."""
import os, shutil, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
import sc2mpq, sc2repo

import fixture
_argv = sys.argv[1:]
_src_arg = _argv[_argv.index('--source') + 1] if '--source' in _argv else None
ROOT = os.path.abspath('testdata')
SRC = fixture.ensure_source(_src_arg)
A, B = os.path.join(ROOT, 'bundle_a'), os.path.join(ROOT, 'bundle_b')
FULL = os.path.join(ROOT, 'a_full.sc2bundle')
THIN = os.path.join(ROOT, 'a_thin.sc2bundle')
FAIL = []

def sh(*args, cwd=A, expect=0, quiet=True):
    p = subprocess.run([sys.executable, os.path.join(ROOT, '..', 'tools', 'sc2diff.py'), '-C', cwd] + list(args),
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if not quiet or p.returncode != expect:
        print('$ sc2diff %s   -> rc=%d' % (' '.join(args), p.returncode))
        print((p.stdout or '').rstrip())
        if p.stderr.strip():
            print((p.stderr or '').rstrip())
    if p.returncode != expect:
        FAIL.append('rc=%d (want %d): sc2diff %s :: %s' % (p.returncode, expect, ' '.join(args), (p.stderr or '').strip()[:160]))
    return p.stdout or ''

def check(label, cond, extra=''):
    print('   %s %s %s' % ('PASS' if cond else 'FAIL', label, extra))
    if not cond:
        FAIL.append(label)

for d in (A, B):
    if os.path.exists(d): shutil.rmtree(d)
for f in (FULL, THIN):
    if os.path.exists(f): os.remove(f)

print('# side A: the author')
sh('init', SRC)
sh('commit', '-m', 'import')
base = sh('log', '--oneline', '-n', '1').split()[0]
# realistic edit
obj = os.path.join(A, 'Objects')
data = open(obj, encoding='utf-8').read()
open(obj, 'w', encoding='utf-8').write(data.replace('<PlacedObjects Version="27">',
                                                    '<PlacedObjects Version="27">\n    <!-- authored by side A -->', 1))
sh('add', '-A'); sh('commit', '-m', 'author edit')
sh('tag', 'v1')
print('   A: 2 commits, tag v1')

print('# side B: an empty repository (no document, no server)')
sh('init', cwd=B, quiet=False)

print('# B applies the FULL bundle  (this is the clone case)')
sh('bundle', FULL)
out = sh('apply', FULL, cwd=B)
check('bundle applied', 'imported' in out)
out = sh('log', '--oneline', cwd=B)
check('B now has the same 2 commits', len(out.strip().splitlines()) == 2, repr(out.strip()[:80]))
a_objs = open(os.path.join(A, 'Objects'), encoding='utf-8').read()
b_objs = open(os.path.join(B, 'Objects'), encoding='utf-8').read()
check('B working tree matches A', a_objs == b_objs)

print('# B can repack a loadable container straight after applying')
sh('pack', os.path.join(ROOT, 'b_packed.SC2Map'), cwd=B)
v = sc2mpq.MPQArchive(os.path.join(ROOT, 'b_packed.SC2Map')).verify(deep=False)
check('packed container is self-consistent', bool(v.get('header_md5_valid')) and bool(v.get('het_md5_valid')))

print('# side A: a second edit, sent as a THIN bundle (--basis v1)')
trg = os.path.join(A, 'MapScript.galaxy')
src = open(trg, 'rb').read()
open(trg, 'wb').write(src + b'\n// authored by side A, round 2\n')
sh('add', '-A'); sh('commit', '-m', 'second edit')
sh('bundle', THIN, '--basis', 'v1')
full_kb = os.path.getsize(FULL) / 1024.0
thin_kb = os.path.getsize(THIN) / 1024.0
print('   full bundle %.1f KB   thin bundle %.1f KB' % (full_kb, thin_kb))
check('thin bundle is much smaller', thin_kb < full_kb * 0.5, '%.1f vs %.1f KB' % (thin_kb, full_kb))

print('# a repository missing the basis must refuse the thin bundle')
C = os.path.join(ROOT, 'bundle_c')
if os.path.exists(C): shutil.rmtree(C)
sh('init', cwd=C)
p = subprocess.run([sys.executable, os.path.join(ROOT, '..', 'tools', 'sc2diff.py'), '-C', C, 'apply', THIN],
                   capture_output=True, text=True, encoding='utf-8', errors='replace')
check('refused with a clear reason', p.returncode == 1 and 'does not have' in (p.stderr or ''), (p.stderr or '').strip()[:100])

print('# B applies the thin bundle -> fast-forward, no merge needed')
sh('tag', 'v1', cwd=B)
out = sh('apply', THIN, cwd=B)
check('fast-forward reported', 'fast-forward' in out, out.strip()[:120])
check('B has A content again', open(os.path.join(B, 'MapScript.galaxy'), 'rb').read() == open(trg, 'rb').read())

print('# what a real patch costs vs re-shipping the campaign')
BIG = os.path.join(ROOT, 'bundle_big')
if os.path.exists(BIG): shutil.rmtree(BIG)
big_src = os.path.join(ROOT, 'samples', 'UmojanCT.SC2Mod')
if not os.path.exists(big_src):
    print('   (skipped: the 43 MB community sample is not shipped in this repository)')
    print()
    print('FAILURES: %s' % (FAIL or 'none'))
    print('RESULT: %s' % ('PASS' if not FAIL else 'FAIL'))
    sys.exit(0 if not FAIL else 1)
repo = sc2repo.Repo.init(BIG, big_src); repo.commit('import')
xml = [n for n in repo.index_tree() if n.lower().endswith('.xml')][0]
fp = repo.workpath(xml)
open(fp, 'ab').write(b'\n<!-- one line changed -->\n')
repo.add(all=True); repo.commit('one component edited')
big_full = os.path.join(ROOT, 'big_full.sc2bundle')
big_thin = os.path.join(ROOT, 'big_thin.sc2bundle')
repo.bundle(big_full)
repo.bundle(big_thin, basis=[repo.log(limit=2)[1]['id']])
print('   campaign archive          : %8.1f MB' % (os.path.getsize(big_src) / 1e6))
print('   full bundle (whole history): %8.2f MB' % (os.path.getsize(big_full) / 1e6))
print('   one-component patch        : %8.2f MB  (%.1f%% of the archive)'
      % (os.path.getsize(big_thin) / 1e6, 100.0 * os.path.getsize(big_thin) / os.path.getsize(big_src)))
check('patch is a small fraction of the archive',
      os.path.getsize(big_thin) < os.path.getsize(big_src) * 0.25)

print()
print('FAILURES: %s' % (FAIL or 'none'))
print('RESULT: %s' % ('PASS' if not FAIL else 'FAIL'))
sys.exit(0 if not FAIL else 1)
