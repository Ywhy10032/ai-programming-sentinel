"""当前用户的 Windows 登录自启；不需要管理员权限。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AIProgrammingSentinel"
GUI_NAME = "AI编程哨兵.exe"


class StartupManager:
    def __init__(self, executable=None, registry=None, *, windows=None, packaged=None):
        self.windows = os.name == "nt" if windows is None else windows
        self.packaged = bool(getattr(sys, "frozen", False)) if packaged is None else packaged
        candidate = Path(executable or sys.executable).resolve()
        self.executable = candidate if candidate.name == GUI_NAME else candidate.with_name(GUI_NAME)
        self._registry = registry

    @property
    def registry(self):
        if self._registry is None:
            import winreg
            self._registry = winreg
        return self._registry

    def command(self):
        if not self.windows or not self.packaged:
            raise ValueError("开机启动请使用 Windows 便携版程序")
        if not self.executable.is_file():
            raise ValueError("图形入口不存在，请完整解压便携包后再设置自启")
        return f'"{self.executable}" serve --no-browser'

    def registered_command(self):
        if not self.windows:
            return None
        reg = self.registry
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, kind = reg.QueryValueEx(key, VALUE_NAME)
            return value if kind == reg.REG_SZ and isinstance(value, str) and value.strip() else None
        except OSError:
            return None

    def state(self):
        supported = self.windows and self.packaged and self.executable.is_file()
        registered = self.registered_command()
        expected = self.command() if supported else None
        enabled = bool(registered and registered == expected)
        repair = bool(registered and registered != expected)
        message = ("登录 Windows 后在托盘运行，控制面板保持关闭" if enabled else
                   "自启路径需更新，打开开关可修复" if repair and supported else
                   "请使用 Windows 便携版设置开机自启" if not supported else
                   "开机自启当前关闭")
        return {"enabled": enabled, "supported": supported, "registered": bool(registered),
                "needs_repair": repair, "message": message}

    def set(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("开机自启需要布尔值")
        if not self.windows or not self.packaged:
            raise ValueError("开机启动请使用 Windows 便携版程序")
        command = self.command() if enabled else None
        reg = self.registry
        with reg.CreateKey(reg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                reg.SetValueEx(key, VALUE_NAME, 0, reg.REG_SZ, command)
            else:
                try:
                    reg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return self.state()

    def repair_after_upgrade(self):
        state = self.state()
        if state["supported"] and state["registered"] and state["needs_repair"]:
            return self.set(True)
        return state
