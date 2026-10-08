
import ctypes, ctypes.wintypes as w, subprocess, sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
u32 = ctypes.WinDLL('user32', use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
p = subprocess.run(['tasklist','/FI','IMAGENAME eq SC2Editor_x64.exe','/NH'], capture_output=True)
pids=[int(l.split()[1]) for l in p.stdout.decode('utf-8','replace').splitlines() if l.split() and l.split()[0].lower().startswith('sc2editor')]
print('editor pids', pids)
def cb(h,_):
    pid=w.DWORD(); u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    if pid.value in pids and u32.IsWindowVisible(h):
        n=u32.GetWindowTextLengthW(h); b=ctypes.create_unicode_buffer(n+2); u32.GetWindowTextW(h,b,n+2)
        if b.value:
            cls=ctypes.create_unicode_buffer(128); u32.GetClassNameW(h,cls,128)
            print('  %-14s %r' % (cls.value, b.value))
    return True
u32.EnumWindows(EnumWindowsProc(cb), 0)
print()
print('--- registry open-document session state ---')
import winreg
k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Blizzard Entertainment\StarCraft II Editor\Recent Files')
i=0
while True:
    try: n,v,t = winreg.EnumValue(k,i)
    except OSError: break
    if n.startswith(('Open Document','Recent Directory')): print('  %-46s %s' % (n,v))
    i+=1
