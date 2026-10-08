# -*- coding: utf-8 -*-
"""Experiment: can the editor be made to open a document without GUI automation?

The editor keeps session state in
  HKCU\Software\Blizzard Entertainment\StarCraft II Editor\Recent Files
with keys "Open Document Count" / "Open Document NN" / "Open Document Active".
If it restores that session on start, we get a fully automatable acceptance test.
"""
import ctypes, ctypes.wintypes as w, os, subprocess, sys, time, winreg

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
EDITOR = r"D:\Game\BLZ\StarCraft II\Support64\SC2Editor_x64.exe"
KEY = r"Software\Blizzard Entertainment\StarCraft II Editor\Recent Files"
LOGS = os.path.join(os.environ['USERPROFILE'], 'Documents', 'StarCraft II', 'EditorLogs')

u32 = ctypes.WinDLL('user32', use_last_error=True)
GetWindowTextW, GetWindowTextLengthW = u32.GetWindowTextW, u32.GetWindowTextLengthW
GetWindowThreadProcessId, IsWindowVisible = u32.GetWindowThreadProcessId, u32.IsWindowVisible
EnumWindowsProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

def titles(pid):
    out = []
    def cb(h, _):
        p = w.DWORD(); GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and IsWindowVisible(h):
            n = GetWindowTextLengthW(h); b = ctypes.create_unicode_buffer(n + 2)
            GetWindowTextW(h, b, n + 2)
            if b.value: out.append(b.value)
        return True
    u32.EnumWindows(EnumWindowsProc(cb), 0)
    return sorted(set(out))

def snapshot():
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY)
    vals = {}
    i = 0
    while True:
        try: n, v, t = winreg.EnumValue(k, i)
        except OSError: break
        if n.startswith('Open Document'): vals[n] = v
        i += 1
    winreg.CloseKey(k)
    return vals

def restore(vals):
    k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE)
    for n, v in vals.items():
        winreg.SetValueEx(k, n, 0, winreg.REG_DWORD if isinstance(v, int) else winreg.REG_SZ, v)
    winreg.CloseKey(k)

def set_session(doc, active):
    k = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(k, 'Open Document Count', 0, winreg.REG_DWORD, 1)
    winreg.SetValueEx(k, 'Open Document 01', 0, winreg.REG_SZ, doc)
    winreg.SetValueEx(k, 'Open Document Active', 0, winreg.REG_DWORD, active)
    winreg.CloseKey(k)

def kill():
    for exe in ('SC2Editor_x64.exe', 'SC2Editor.exe'):
        subprocess.run(['taskkill', '/F', '/IM', exe], capture_output=True)
    time.sleep(1)

def trial(doc, active, args, timeout=120, label=''):
    set_session(doc, active)
    kill()
    before = set(os.listdir(LOGS)) if os.path.isdir(LOGS) else set()
    p = subprocess.Popen([EDITOR] + args, cwd=os.path.dirname(EDITOR))
    t0, seen, verdict = time.time(), [], None
    while time.time() - t0 < timeout:
        if p.poll() is not None:
            verdict = 'EXITED(rc=%s)' % p.returncode; break
        seen = titles(p.pid)
        if any(os.path.basename(doc).lower() in t.lower() for t in seen):
            verdict = 'LOADED'; break
        time.sleep(2)
    fresh = sorted(set(os.listdir(LOGS)) - before) if os.path.isdir(LOGS) else []
    print('%-28s active=%d args=%-14s -> %-18s %.0fs  titles=%s  newlogs=%s'
          % (label, active, ' '.join(args) or '(none)', verdict or 'TIMEOUT', time.time() - t0, seen, fresh))
    kill()
    return verdict

if __name__ == '__main__':
    doc = os.path.abspath(sys.argv[1])
    saved = snapshot()
    try:
        for active in (0, 1):
            for args in ([], [doc]):
                trial(doc, active, args, timeout=100, label='session-restore')
    finally:
        restore(saved)
        print('registry restored:', saved)
