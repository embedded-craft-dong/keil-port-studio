"""Read-only discovery of open Keil projects, without UI automation or dependencies.

The current window caption is authoritative. Process command lines and MRU keys
can refer to a project which was closed/switched, so they are deliberately NOT
used as fallbacks. Unknown captions remain selectable but require manual input.
"""
from dataclasses import dataclass
import ntpath
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class KeilWindow:
    hwnd: int
    pid: int
    title: str
    project: object = None
    reason: str = ''


def project_from_title(title):
    """Extract only an unambiguous absolute Windows .uvproj[x] caption path."""
    caption = re.sub(r'\s+-\s+[µμu]Vision(?:\d+)?(?:\s+[\d.]+)?$', '',
                     title.strip(), count=1, flags=re.IGNORECASE).strip()
    caption = caption.rstrip('*').strip().strip('"')
    drive, _ = ntpath.splitdrive(caption)
    if not drive or not ntpath.isabs(caption):
        return None
    if ntpath.splitext(caption)[1].lower() not in ('.uvproj', '.uvprojx'):
        return None
    return Path(caption)


def _native_windows():
    """Visible, unowned UV4/UV3 top-level windows, including minimized ones."""
    import ctypes
    from ctypes import wintypes as w

    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes = [callback_type, w.LPARAM]
    user.EnumWindows.restype = w.BOOL
    user.IsWindowVisible.argtypes = [w.HWND]
    user.IsWindowVisible.restype = w.BOOL
    user.GetWindow.argtypes = [w.HWND, w.UINT]
    user.GetWindow.restype = w.HWND
    user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    user.GetWindowThreadProcessId.restype = w.DWORD
    user.GetWindowTextLengthW.argtypes = [w.HWND]
    user.GetWindowTextLengthW.restype = ctypes.c_int
    user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    user.GetWindowTextW.restype = ctypes.c_int
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR,
                                               ctypes.POINTER(w.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    executables, windows, errors = {}, [], []

    def visit(hwnd, _):
        try:
            if not user.IsWindowVisible(hwnd) or user.GetWindow(hwnd, 4):  # GW_OWNER
                return True
            pid = w.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in executables:
                name = ''
                handle = kernel.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
                if handle:
                    try:
                        size = w.DWORD(32768)
                        buf = ctypes.create_unicode_buffer(size.value)
                        if kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                            name = ntpath.basename(buf.value).lower()
                    finally:
                        kernel.CloseHandle(handle)
                executables[pid.value] = name
            if executables[pid.value] not in ('uv4.exe', 'uv3.exe'):
                return True
            length = user.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user.GetWindowTextW(hwnd, title, len(title))
            windows.append((int(hwnd), pid.value, title.value))
        except Exception as error:
            errors.append(error)  # Never let ctypes swallow callback exceptions.
        return True

    callback = callback_type(visit)
    if not user.EnumWindows(callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    if errors:
        raise errors[0]
    return windows


def discover_keil_windows():
    if os.name != 'nt':
        return []
    result = []
    for hwnd, pid, title in _native_windows():
        project = project_from_title(title)
        reason = '' if project else 'unresolved'
        if project is not None:
            try:
                present = project.is_file()
            except OSError:
                present = False
            if not present:
                project, reason = None, 'missing'
        result.append(KeilWindow(hwnd, pid, title, project, reason))
    return result
