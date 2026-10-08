# -*- coding: utf-8 -*-
"""Baseline on the largest sample (43 MB packed / 301 components)."""
import os, sys, time, glob, statistics
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
import sc2repo

SRC = 'testdata/samples/UmojanCT.SC2Mod'
root = 'testdata/bench_umojan'
t0 = time.perf_counter()
if not os.path.exists(os.path.join(root, '.sc2diff')):
    repo = sc2repo.Repo.init(root, SRC)
    repo.commit('import')
print('init+first commit: %.1fs' % (time.perf_counter() - t0))

repo = sc2repo.Repo(root)
total = sum(os.path.getsize(p) for p in glob.glob(root + '/**/*', recursive=True) if os.path.isfile(p))
nfiles = sum(1 for p in glob.glob(root + '/**/*', recursive=True) if os.path.isfile(p))
print('working tree: %d files, %.1f MB' % (nfiles, total / 1e6))

def timeit(fn, n=3):
    ts = []
    for _ in range(n):
        t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
    return min(ts), statistics.median(ts)

for label, fn in (('scan_tree', lambda: repo.scan_tree()),
                  ('diff_working', lambda: repo.diff_working()),
                  ('commit', lambda: repo.commit('bench'))):
    mn, md = timeit(fn, n=3 if label != 'commit' else 2)
    print('  %-14s min=%7.3fs median=%7.3fs' % (label, mn, md))
