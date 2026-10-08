# -*- coding: utf-8 -*-
"""Editor acceptance harness (v2).

Launches the Galaxy Editor on a document and decides, without screenshots:
  LOADED        - a top-level window title names the document
  ERROR_DIALOG  - a modal dialog owned by the editor is up
  EXITED(n)     - the editor process died (crash)
  TIMEOUT       - nothing conclusive
Also reports fresh crash artefacts written under Documents\StarCraft II\EditorLogs.
"""
from __future__ import annotations
import argparse, ctypes, ctypes.wintypes as w, os, subprocess, sys, time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

EDITOR = r"D:\Game\BLZ\StarCraft II\Support64\SC2Editor_x64.exe"
LOGS = os.path.join(os.environ['USERPROFILE'], 'Documents', 'StarCraft II', 'EditorLogs')

u32 = ctypes.WinDLL('user32', use_last_error=True)
EnumWindows = u32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
GetWindowTextW = u32.GetWindowTextW
GetWindowTextLengthW = u32.GetWindowTextLengthW
GetClassNameW = u32.GetClassNameW
GetWindowThreadProcessId = u32.GetWindowThreadProcessId
IsWindowVisible = u32.IsWindowVisible
GetMenu = u32.GetMenu
GetWindow = u32.GetWindow
GW_OWNER, GW_CHILD, GW_HWNDNEXT = 4, 5, 2

def win_text(hwnd):
    n = GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    GetWindowTextW(hwnd, buf, n + 2)
    return buf.value

def win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, buf, 256)
    return buf.value

def windows_of(pid):
    found = []
    def cb(hwnd, _):
        p = w.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid:
            found.append(dict(hwnd=hwnd, text=win_text(hwnd), cls=win_class(hwnd),
                              visible=bool(IsWindowVisible(hwnd)),
                              has_menu=bool(GetMenu(hwnd)), owner=GetWindow(hwnd, GW_OWNER)))
        return True
    EnumWindows(EnumWindowsProc(cb), 0)
    return found

def descendants(hwnd, depth=0, out=None):
    if out is None: out = []
    if depth > 5: return out
    c = GetWindow(hwnd, GW_CHILD)
    while c:
        out.append((c, win_text(c), win_class(c), depth))
        descendants(c, depth + 1, out)
        c = GetWindow(c, GW_HWNDNEXT)
    return out

def log_snapshot():
    return set(os.listdir(LOGS)) if os.path.isdir(LOGS) else set()

def kill_editors():
    for exe in ('SC2Editor_x64.exe', 'SC2Editor.exe'):
        subprocess.run(['taskkill', '/F', '/IM', exe], capture_output=True)
    time.sleep(1.0)

def probe(doc, timeout=120, keep=False, quiet=False):
    doc = os.path.abspath(doc)
    base = os.path.basename(doc)
    print('DOC       %s  (%s)' % (doc, ('%d bytes' % os.path.getsize(doc)) if os.path.isfile(doc) else 'directory'))
    kill_editors()
    before = log_snapshot()
    proc = subprocess.Popen([EDITOR, doc], cwd=os.path.dirname(EDITOR))
    pid = proc.pid
    print('PID       %d' % pid)
    t0 = time.time()
    verdict, last_titles, dialogs = None, [], []
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            verdict = 'EXITED(rc=%s)' % proc.returncode
            break
        ws = windows_of(pid)
        last_titles = sorted({x['text'] for x in ws if x['visible'] and x['text']})
        dialogs = []
        for x in ws:
            if not x['visible'] or x['cls'] != '#32770':
                continue
            if x['has_menu'] or x['owner'] == 0:
                continue  # main frame or unowned top-level, not a modal dialog
            kids = [t for (_h, t, c, _d) in descendants(x['hwnd']) if t.strip() and c in ('Static', 'Button', 'Edit', 'RichEdit20W', 'SysListView32')]
            dialogs.append((x['text'], kids[:6]))
        if any(base.lower() in t.lower() for t in last_titles):
            verdict = 'LOADED'
            break
        if dialogs:
            verdict = 'ERROR_DIALOG'
            break
        time.sleep(2.0)
    print('ELAPSED   %.1fs' % (time.time() - t0))
    print('VERDICT   %s' % (verdict or 'TIMEOUT'))
    for t in last_titles:
        print('  TITLE   %r' % t)
    for d in dialogs:
        print('  DIALOG  %r  children=%s' % d)
    fresh = sorted(log_snapshot() - before)
    print('NEW LOGS  %s' % (fresh or 'none'))
    crashes = [f for f in fresh if 'Error' in f]
    if crashes:
        print('CRASH     %s' % crashes)
    if not keep:
        kill_editors()
    return dict(verdict=verdict, titles=last_titles, dialogs=dialogs, crashes=crashes)

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--doc', required=True)
    ap.add_argument('--timeout', type=float, default=120)
    ap.add_argument('--keep', action='store_true')
    a = ap.parse_args()
    probe(a.doc, a.timeout, a.keep)
