# -*- coding: utf-8 -*-
"""Open the working original in the editor and let the EDITOR save it back.
Whatever it changes is exactly what its own verifier expects."""
import os, shutil, subprocess, sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, 'tools')
import editor_accept as EA

SRC = 'testdata/abtest/aa_original.SC2Map'
DOC = 'testdata/abtest/resaved.SC2Map'
os.makedirs('testdata/abtest', exist_ok=True)
SRC = os.path.abspath(SRC)
DOC = os.path.abspath(DOC)
shutil.copy2(SRC, DOC)
print('copied %s -> %s (%d bytes)' % (SRC, DOC, os.path.getsize(DOC)))

EA.set_recent_dir(os.path.dirname(DOC) + os.sep)
EA.kill_all()
before_logs = EA.log_names()
pid = EA.boot(verbose=True)
if pid is None:
    print('BOOT FAILED'); sys.exit(1)
for _ in range(8):
    rc, out, err = EA.kit('--pid', pid, 'modal', timeout=90)
    if 'modal:' not in ((out or '') + (err or '')):
        break
    EA.kit('--pid', pid, 'modal', '--dismiss', timeout=90)
    time.sleep(1)

rc, out, err = EA.kit('--pid', pid, 'open', DOC)
print('open rc=%d %s' % (rc, (out or err).strip()[:200]))
state, seen = EA.pump(pid, os.path.basename(DOC), time.time() + 300, [], verbose=True)
print('load state=%s titles=%s' % (state, seen))

mtime_before = os.path.getmtime(DOC)
size_before = os.path.getsize(DOC)
rc, out, err = EA.kit('--pid', pid, 'save', timeout=600)
print('save rc=%d' % rc)
print((out or err).strip()[:800])
time.sleep(3)
after_size = os.path.getsize(DOC)
print('size before=%d after=%d  changed=%s' % (size_before, after_size, after_size != size_before))
EA.kill_all()
