# -*- coding: utf-8 -*-
"""Editor acceptance rig (v4) -- does the Galaxy Editor open this document, and does it survive?

Measured editor behaviours that shape this rig:
  * a document path on the command line makes the editor exit at once -> launch bare
  * the editor is single-instance and re-execs itself, so the launched PID is not reliable
    -> always re-resolve the live PID by image name
  * 文件/打开 is Blizzard's own dialog; the target must be listed in the dialog's current
    directory, which comes from Recent Directory - Open and Save Documents
  * a document whose dependency mods are not installed produces a modal
    "无法加载依赖项数据 / 仍然继续？" with buttons 是(6) / 否(7).  That is a content-level
    warning, not a container rejection, and the same prompt appears for the untouched
    original -- so the rig answers 是 and continues.
  * crashes are evidenced by a new "<stamp> <host> B97579 Error" folder under EditorLogs

Verdicts: LOADED | REJECTED | CRASH | TIMEOUT
"""
from __future__ import annotations
import argparse, ctypes, ctypes.wintypes as w, json, os, subprocess, sys, time, winreg

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

EDITOR = r"D:\Game\BLZ\StarCraft II\Support64\SC2Editor_x64.exe"
SC2KIT_DIR = r"D:\Code\Python\银河编辑器\archive\sc2editor-kit"
SC2KIT_PY = os.path.join(SC2KIT_DIR, '.venv', 'Scripts', 'python.exe')
LOGS = os.path.join(os.environ['USERPROFILE'], 'Documents', 'StarCraft II', 'EditorLogs')
REG_KEY = r"Software\Blizzard Entertainment\StarCraft II Editor\Recent Files"
REG_DIR = "Recent Directory - Open and Save Documents"
DEPENDENCY_HINT = "依赖"
CONTINUE_BUTTON = 6

u32 = ctypes.WinDLL('user32', use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


def window_titles(pid):
    out = []
    def cb(h, _):
        p = w.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(h):
            n = u32.GetWindowTextLengthW(h)
            b = ctypes.create_unicode_buffer(n + 2)
            u32.GetWindowTextW(h, b, n + 2)
            if b.value:
                out.append(b.value)
        return True
    u32.EnumWindows(EnumWindowsProc(cb), 0)
    return sorted(set(out))


def kit(*args, timeout=240):
    env = dict(os.environ, PYTHONPATH='src', PYTHONIOENCODING='utf-8')
    try:
        p = subprocess.run([SC2KIT_PY, '-m', 'sc2kit'] + [str(a) for a in args],
                           cwd=SC2KIT_DIR, env=env, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout or b'').decode('utf-8', 'replace'), (p.stderr or b'').decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout'


def editor_pids():
    p = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq SC2Editor_x64.exe', '/NH'], capture_output=True)
    out = []
    for line in (p.stdout or b'').decode('utf-8', 'replace').splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[0].lower().startswith('sc2editor'):
            try:
                out.append(int(parts[1]))
            except ValueError:
                pass
    return out


def kill_all():
    for exe in ('SC2Editor_x64.exe', 'SC2Editor.exe'):
        subprocess.run(['taskkill', '/F', '/IM', exe], capture_output=True)
    for _ in range(60):
        if not editor_pids():
            return True
        time.sleep(1)
    return False


def get_recent_dir():
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY)
        v, _ = winreg.QueryValueEx(k, REG_DIR)
        winreg.CloseKey(k)
        return v
    except OSError:
        return None


def set_recent_dir(value):
    k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(k, REG_DIR, 0, winreg.REG_SZ, value)
    winreg.CloseKey(k)


def log_names():
    return set(os.listdir(LOGS)) if os.path.isdir(LOGS) else set()


def boot(boot_timeout=200, verbose=True):
    subprocess.Popen([EDITOR], cwd=os.path.dirname(EDITOR))
    t0 = time.time()
    while time.time() - t0 < boot_timeout:
        time.sleep(5)
        pids = editor_pids()
        if not pids or time.time() - t0 < 25:
            continue
        pid = pids[-1]
        rc, out, err = kit('--pid', pid, 'doctor')
        if 'command target:' in out and 'responsive:' in out and 'True' in out:
            if verbose:
                print('  editor pid=%s responsive after %.0fs' % (pid, time.time() - t0), flush=True)
            return pid
    return None


def pump(pid, base, deadline, transcript, verbose=True):
    """Watch the editor: answer the dependency prompt, detect load, detect death."""
    last_pid = pid
    while time.time() < deadline:
        pids = editor_pids()
        if not pids:
            return 'gone', None
        pid = pids[-1]
        seen = window_titles(pid)
        if any(base.lower() in t.lower() for t in seen):
            return 'loaded', seen
        rc, out, err = kit('--pid', pid, 'modal', timeout=90)
        txt = (out or '') + (err or '')
        if 'modal:' in txt:
            title = txt.splitlines()[0]
            transcript.append(title.strip() + ' | ' + ' / '.join(
                l.strip() for l in txt.splitlines()[1:6]))
            if verbose:
                print('    modal: %s' % transcript[-1][:220], flush=True)
            if DEPENDENCY_HINT in txt or '仍然继续' in txt:
                kit('--pid', pid, 'modal', '--confirm', CONTINUE_BUTTON, timeout=90)
            elif '打开文档' in txt:
                pass
            else:
                kit('--pid', pid, 'modal', '--dismiss', timeout=90)
        time.sleep(3)
    return 'timeout', window_titles(pid) if editor_pids() else None


def accept(doc, verbose=True):
    doc = os.path.abspath(doc)
    base = os.path.basename(doc)
    saved = get_recent_dir()
    res = {"doc": doc, "attempts": [], "modals": []}
    try:
        set_recent_dir(os.path.dirname(doc) + os.sep)
        kill_all()
        before = log_names()
        pid = boot(verbose=verbose)
        if pid is None:
            res.update(verdict="CRASH", detail="editor never became responsive")
            return res
        # the editor opens with a tip / welcome modal; sc2kit refuses to stack the open
        # dialog on top of it, so clear the deck first (dismiss uses the safe button)
        for _ in range(8):
            rc0, out0, err0 = kit('--pid', pid, 'modal', timeout=90)
            if 'modal:' not in ((out0 or '') + (err0 or '')):
                break
            res["modals"].append('cleared before open: ' + ((out0 or err0).strip().splitlines() or [''])[0][:120])
            kit('--pid', pid, 'modal', '--dismiss', timeout=90)
            time.sleep(1)
        pids = editor_pids()
        if pids:
            pid = pids[-1]
        rc, out, err = kit('--pid', pid, 'open', doc)
        res["attempts"].append({"rc": rc, "msg": (out or err).strip().replace('\n', ' ')[:300]})
        if verbose:
            print('  open rc=%d %s' % (rc, (out or err).strip()[:200]), flush=True)
        state, seen = pump(pid, base, time.time() + 240, res["modals"], verbose)
        if verbose:
            print('  state=%s titles=%s' % (state, seen), flush=True)
        fresh = sorted(log_names() - before)
        crashes = [f for f in fresh if 'Error' in f]
        res["new_logs"] = fresh
        res["titles"] = seen or []
        if state == 'loaded':
            res["verdict"] = "LOADED"
        elif crashes:
            res.update(verdict="CRASH", detail="new crash artefacts: %s" % crashes)
        elif state == 'gone':
            res.update(verdict="CRASH", detail="editor process disappeared; no crash folder written")
        else:
            res["verdict"] = "REJECTED"
        return res
    finally:
        if saved is not None:
            set_recent_dir(saved)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('docs', nargs='+')
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args()
    verdicts = {}
    for d in a.docs:
        print('=' * 90, flush=True)
        print('DOC %s (%d bytes)' % (d, os.path.getsize(d)), flush=True)
        r = accept(d)
        verdicts[d] = r.get('verdict')
        print('  VERDICT: %s' % r.get('verdict'), flush=True)
        for m in r.get('modals', []):
            print('    MODAL %s' % m[:260])
        for t in r.get('titles', []):
            print('    TITLE %r' % t)
        if r.get('detail'):
            print('    detail: %s' % r['detail'])
        if not a.keep:
            kill_all()
    print(json.dumps(verdicts, ensure_ascii=False, indent=1))
