# -*- coding: utf-8 -*-
import sys, os, glob
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, 'tools')
from sc2mpq import MPQArchive
print('%-28s %-10s %-10s %-10s %-10s %-10s %-10s' % ('archive', 'hdrMD5', 'blockMD5', 'hashMD5', 'hetMD5', 'betMD5', 'attrs'))
for p in sorted(glob.glob('testdata/samples/*.SC2Map') + glob.glob('testdata/samples/*.SC2Mod') + ['testdata/rebuilt/paiur01.SC2Map']):
    v = MPQArchive(p).verify(deep=False)
    print('%-28s %-10s %-10s %-10s %-10s %-10s %-10s' % (
        os.path.basename(p)[:28], v.get('header_md5_valid'), v.get('block_table_md5_valid'),
        v.get('hash_table_md5_valid'), v.get('het_md5_valid'), v.get('bet_md5_valid'), v.get('attributes')))
