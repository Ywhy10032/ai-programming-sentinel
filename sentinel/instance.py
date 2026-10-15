"""按当前数据目录互斥启动，覆盖登录自启与手动启动的并发情况。"""
from __future__ import annotations

import hashlib
import os


class InstanceGuard:
    def __init__(self, directory):
        self.directory = directory.resolve()
        self.acquired = False
        self.handle = None

    def __enter__(self):
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            self.kernel.CreateMutexW.restype = wintypes.HANDLE
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            identity = hashlib.sha256(str(self.directory).casefold().encode('utf-8')).hexdigest()
            self.handle = self.kernel.CreateMutexW(None, True, 'Local\\AIProgrammingSentinel-'+identity)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self.acquired = ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
        else:
            import fcntl
            self.directory.mkdir(parents=True, exist_ok=True)
            self.handle = (self.directory/'instance.lock').open('a+b')
            try:
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
            except BlockingIOError:
                pass
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            if os.name == 'nt':
                if self.acquired:
                    self.kernel.ReleaseMutex(self.handle)
                self.kernel.CloseHandle(self.handle)
            else:
                self.handle.close()
            self.handle = None
        self.acquired = False
