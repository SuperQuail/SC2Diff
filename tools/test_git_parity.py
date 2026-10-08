# -*- coding: utf-8 -*-
"""Exercise the git-shaped CLI end to end, the way an agent would drive it."""
import json, os, re, shutil, subprocess, sys, xml.etree.ElementTree as ET
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import fixture
_argv = sys.argv[1:]
_src_arg = _argv[_argv.index('--source') + 1] if '--source' in _argv else None
REPO = os.path.abspath('testdata/parity_repo')
SRC = fixture.ensure_source(_src_arg)
OUT = os.path.abspath('testdata/parity_packed.SC2Map')
FAIL = []
STEP = [0]

def run(*args, expect=0, quiet=False, show=True):
    p = subprocess.run([sys.executable, 'tools/sc2diff.py', '-C', REPO] + list(args),
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    if show and not quiet:
        print('$ sc2diff %s' % ' '.join(args))
        out = (p.stdout or '').rstrip()
        print(out if len(out) < 1500 else out[:800] + '\n   ...[%d more lines]...' % (out.count('\n') - 12))
        if p.stderr.strip():
            print(p.stderr.rstrip())
    if p.returncode != expect:
        FAIL.append('exit %d (want %d) for: sc2diff %s%s' %
                    (p.returncode, expect, ' '.join(args), (' :: ' + (p.stderr or '').strip()[:200]) if p.stderr.strip() else ''))
    return p.stdout or ''

def check(label, cond, extra=''):
    print('   %s %s %s' % ('PASS' if cond else 'FAIL', label, extra))
    if not cond:
        FAIL.append(label)

def head(title):
    STEP[0] += 1
    print('=' * 90); print('%d  %s' % (STEP[0], title))

def edit_objects(drop_one=False, add_marine=False):
    path = os.path.join(REPO, 'Objects')
    tree = ET.parse(path); root = tree.getroot()
    if drop_one:
        us = root.findall('ObjectUnit')
        if us: root.remove(us[0])
    if add_marine:
        e = ET.SubElement(root, 'ObjectUnit')
        e.set('Id', '4242424242'); e.set('Position', '64.0,64.0,0'); e.set('Rotation', '0')
        e.set('Scale', '1,1,1'); e.set('UnitType', 'Marine'); e.set('Player', '1')
    tree.write(path, encoding='utf-8', xml_declaration=True)

print('### setup'); 
if os.path.exists(REPO): shutil.rmtree(REPO)
run('init', SRC, quiet=True)
print('   initialised from %s' % os.path.basename(SRC))

head('baseline commit of the imported document')
run('add', '-A', quiet=True)
run('commit', '-m', 'import from paiur01.SC2Map')
out = run('status'); check('working tree clean after the import commit', 'nothing to commit' in out)
out = run('status', '-s'); check('short status is empty', out.strip() == '')

head('edit a component -> status shows it unstaged')
edit_objects(drop_one=True)
out = run('status')
check('unstaged section names Objects', 'not staged' in out and 'Objects' in out)
out = run('status', '-s'); check('short shows a leading-space M', re.search(r'^ M\s+Objects$', out, re.M) is not None, repr(out.strip()[:60]))

head('semantic diff of the working tree')
out = run('diff')
check('names the removed entity', 'ObjectUnit:' in out and 'xml(PlacedObjects)' in out)
check('does not report the whole document', 'component(s) changed' in out and '1 component(s) changed' in out)

head('add -A stages it')
run('add', '-A', quiet=True)
out = run('status', '-s'); check('short shows M staged', re.search(r'^M\s+Objects$', out, re.M) is not None, repr(out.strip()[:60]))

head('diff --cached compares the index against HEAD')
out = run('diff', '--cached')
check('cached diff shows Objects only', '1 component(s) changed' in out and 'Objects' in out)

head('commit the index')
c1 = re.search(r'\[([^\s]+) ([0-9a-f]+)\]', run('commit', '-m', 'drop one placed unit'))
sha1 = c1.group(2) if c1 else ''
check('commit hash captured', bool(sha1), sha1)
out = run('status', '-s'); check('tree clean after commit', out.strip() == '')

head('log --oneline and show')
out = run('log', '--oneline')
check('two commits', len(out.strip().splitlines()) == 2, repr(out.strip()[:120]))
out = run('show')
check('show renders the diff', 'ObjectUnit:' in out or 'xml(PlacedObjects)' in out)

head('branch and switch')
run('branch', 'feature', quiet=True)
out = run('branch'); check('lists main and feature', '* main' in out and 'feature' in out)
run('switch', 'feature', quiet=True)
out = run('status'); check('now on feature', 'On branch feature' in out)
edit_objects(add_marine=True)
run('add', '-A', quiet=True)
run('commit', '-m', 'place a Marine at map centre', quiet=True)
out = run('log', '--oneline', '-n', '1'); check('feature tip is the Marine commit', 'Marine' in out)

head('checkout main then feature: working tree follows')
run('checkout', 'main', quiet=True)
check('main has no Marine', '4242424242' not in open(os.path.join(REPO, 'Objects'), encoding='utf-8').read())
run('checkout', 'feature', quiet=True)
check('feature has the Marine', '4242424242' in open(os.path.join(REPO, 'Objects'), encoding='utf-8').read())
run('checkout', 'main', quiet=True)

head('tags and revision resolution')
run('tag', 'v1', quiet=True)
out = run('tag'); check('tag listed', 'v1' in out)
out = run('diff', 'v1', 'feature'); check('diff by tag', '1 component(s) changed' in out)
out = run('diff', sha1, 'feature'); check('diff by abbreviated sha', '1 component(s) changed' in out)
out = run('diff', 'HEAD~1', 'HEAD'); check('diff by HEAD~1', '1 component(s) changed' in out)

head('restore --staged unstages a real change')
edit_objects(drop_one=True)
run('add', '-A', quiet=True)
out = run('status', '-s'); check('staged before restore', re.search(r'^M\s+Objects$', out, re.M) is not None)
run('restore', '--staged', 'Objects', quiet=True)
out = run('status', '-s'); check('unstaged after restore --staged', re.search(r'^ M\s+Objects$', out, re.M) is not None, repr(out.strip()[:60]))
run('checkout', '-f', 'main', quiet=True)

head('branch -d refuses unmerged work; -D forces')
run('branch', '-d', 'feature', expect=128, quiet=True)
run('branch', '-D', 'feature', quiet=True)
out = run('branch'); check('feature gone', 'feature' not in out)
run('tag', '-d', 'v1', quiet=True)

head('pack the working tree back into a container')
run('pack', OUT)
import sc2mpq
a = sc2mpq.MPQArchive(OUT); v = a.verify(deep=True)
check('container self-consistent', bool(v.get('header_md5_valid')) and not v.get('het_vs_classic_mismatch'))
check('digest fields valid', bool(v.get('het_md5_valid')) and bool(v.get('bet_md5_valid')))
check('attributes verify (2 bookkeeping exemptions)', v.get('md5_bad') == 2 and v.get('crc_bad') == 1)
doc = sc2mpq.Document.open(OUT); src = sc2mpq.Document.open(SRC)
names = [n for n in doc.component_names()]
diff = [n for n in names if doc.get(n) != src.get(n)]
check('only the components we edited differ', diff == ['Objects'] or diff == [], str(diff))

print('=' * 90)
print('FAILURES: %s' % (FAIL or 'none'))
print('RESULT: %s' % ('PASS' if not FAIL else 'FAIL'))
sys.exit(0 if not FAIL else 1)
