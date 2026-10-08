# -*- coding: utf-8 -*-
"""Quantify the value of a patch bundle: how big is a real change on a 43 MB campaign?"""
import os, shutil, sys, time, zipfile
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
import sc2repo, sc2mpq

SRC = 'testdata/samples/UmojanCT.SC2Mod'
ROOT = 'testdata/bundle_probe'
if os.path.exists(ROOT):
    shutil.rmtree(ROOT)
t0 = time.perf_counter()
repo = sc2repo.Repo.init(ROOT, SRC)
repo.commit('import')
print('repo built in %.1fs  (%d components, source %.1f MB)'
      % (time.perf_counter() - t0, len(repo.index_tree()), os.path.getsize(SRC) / 1e6))

# a realistic small edit: touch one XML component
import glob
cands = [n for n in repo.index_tree() if n.lower().endswith('.xml')]
target = sorted(cands)[0]
path = repo.workpath(target)
data = open(path, 'rb').read()
open(path, 'wb').write(data.replace(b'<Catalog>', b'<Catalog>\n  <!-- sc2diff probe -->', 1) if b'<Catalog>' in data else data + b'\n<!-- probe -->\n')
repo.add(all=True)
repo.commit('small edit')

# what would today's distribution cost vs a bundle?
full = os.path.getsize(SRC)
store = sum(os.path.getsize(p) for p in glob.glob(ROOT + '/.sc2diff/**/*', recursive=True) if os.path.isfile(p))
print('repository .sc2diff total: %.2f MB' % (store / 1e6))
print('would-be incremental payload (1 changed component): %d bytes' % len(open(path, "rb").read()))
print()
print('edited component:', target)
print('=> a patch carrying just this change is a few KB, versus re-shipping %.1f MB' % (full / 1e6))
