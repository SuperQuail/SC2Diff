# -*- coding: utf-8 -*-
"""After-optimisation benchmark + git-parity smoke test."""
import os, shutil, sys, time, glob, statistics
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
import sc2repo

CASES = [('paiur01.SC2Map', 'testdata/samples/paiur01.SC2Map'),
         ('HTXL.SC2Mod', 'testdata/samples/HTXL.SC2Mod'),
         ('UmojanCT.SC2Mod', 'testdata/samples/UmojanCT.SC2Mod')]

def timeit(fn, n=3):
    ts = []
    for _ in range(n):
        t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
    return min(ts), statistics.median(ts)

for label, src in CASES:
    root = 'testdata/bench2_' + label.replace('.', '_')
    if os.path.exists(root):
        shutil.rmtree(root)
    t0 = time.perf_counter()
    repo = sc2repo.Repo.init(root, src)
    init_s = time.perf_counter() - t0
    total = sum(os.path.getsize(p) for p in glob.glob(root + '/**/*', recursive=True) if os.path.isfile(p))
    nfiles = sum(1 for p in glob.glob(root + '/**/*', recursive=True) if os.path.isfile(p))
    t0 = time.perf_counter(); repo.commit('import'); first_s = time.perf_counter() - t0
    print('=== %-16s %3d files  %7.1f MB   init=%.2fs  first-commit=%.2fs' % (label, nfiles, total / 1e6, init_s, first_s))
    for name, fn in (('scan (cached)', lambda: repo.scan()),
                     ('status', lambda: repo.status()),
                     ('diff (clean)', lambda: repo.diff_working()),
                     ('add -A + commit', lambda: (repo.add(all=True), repo.commit('noop', allow_empty=True)))):
        mn, md = timeit(fn, n=3)
        print('      %-16s min=%7.4fs  median=%7.4fs' % (name, mn, md))
