# -*- coding: utf-8 -*-
import sys, os
from pathlib import Path
try:
    import winreg
except ImportError:
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPNAME = "SplitBrainProto"

def _pythonw():
    # pythonw.exe を優先（コンソール出さない）
    exe = Path(sys.executable)
    if exe.name.lower().startswith("python"):
        cand = exe.parent / "pythonw.exe"
        if cand.exists(): return str(cand)
    return str(exe)

def enable_startup():
    if not winreg: return False, "winreg不可（Windowsのみ対応）"
    cmd = f'"{_pythonw()}" -m app.ui.main'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, APPNAME, 0, winreg.REG_SZ, cmd)
    return True, cmd

def disable_startup():
    if not winreg: return False, "winreg不可（Windowsのみ対応）"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APPNAME)
        return True, ""
    except FileNotFoundError:
        return True, ""
