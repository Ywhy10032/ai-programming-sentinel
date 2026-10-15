from __future__ import annotations

import json
import os
import secrets
import re
import sys
import threading
import uuid
from pathlib import Path


def data_directory() -> Path:
    override = os.environ.get("AI_SENTINEL_HOME")
    if override:
        return Path(override).resolve()
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AI编程哨兵"


def resource_path(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / name


DEFAULTS = {
    "port": "auto", "http_port": 18765, "fps": 8,
    "rotation": 0, "sound": True, "quiet": False, "reduced_motion": False,
    "codex_watch": True, "codex_scope": "", "completion_hold": 18,
    "clock_24h": True, "claude_watch": True,
    "desktop_watch": True, "deepseek_watch": True, "deepseek_root": "",
}


def valid_token(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{32,256}', value) is not None


class Config:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or data_directory()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "config.json"
        self.lock = threading.RLock()
        self.values = dict(DEFAULTS)
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.values.update(self.validate(raw))
                    if valid_token(raw.get("token")):
                        self.values["token"] = raw["token"]
            except (OSError, ValueError):
                self.path.replace(self.directory / "config.invalid.json")
        if "token" not in self.values:
            self.values["token"] = secrets.token_urlsafe(32)
            self.save()

    @staticmethod
    def validate(raw: dict) -> dict:
        result = {}
        for key, value in raw.items():
            if key not in DEFAULTS:
                continue
            if key in ["sound", "quiet", "reduced_motion", "codex_watch", "claude_watch", "clock_24h", "desktop_watch", "deepseek_watch"]:
                if not isinstance(value, bool):
                    raise ValueError(f"{key} 必须是布尔值")
            elif key == "rotation":
                if value not in [0, 180]:
                    raise ValueError("旋转角度只能是 0 或 180")
            elif key == "fps":
                if type(value) is not int or not 1 <= value <= 15:
                    raise ValueError("刷新率范围为 1 到 15")
            elif key == "http_port":
                if type(value) is not int or not 1024 <= value <= 65535:
                    raise ValueError("端口范围为 1024 到 65535")
            elif key == "completion_hold":
                if type(value) is not int or not 5 <= value <= 300:
                    raise ValueError("完成保留时间范围为 5 到 300 秒")
            elif not isinstance(value, str) or len(value) > 1024:
                raise ValueError(f"{key} 格式不正确")
            elif key == "port" and value != "auto":
                import re
                if not re.fullmatch(r"COM\d+", value, re.I):
                    raise ValueError("端口应为 auto 或 COM 加数字")
                value = value.upper()
            result[key] = value
        return result

    def get(self, key: str):
        with self.lock:
            return self.values[key]

    def public(self) -> dict:
        with self.lock:
            return {k: v for k, v in self.values.items() if k != "token"}

    def refresh_token(self):
        # 配置可能被另一个入口或备份恢复更新；只同步有效令牌，不重写文件或改变偏好。
        with self.lock:
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError, UnicodeError):
                return False
            if not isinstance(raw, dict) or not valid_token(raw.get('token')):
                return False
            changed = raw['token'] != self.values['token']
            self.values['token'] = raw['token']
            return changed

    def update(self, raw: dict) -> dict:
        values = self.validate(raw)
        with self.lock:
            self.values.update(values)
            self.save()
            return self.public()

    def save(self):
        with self.lock:
            self.refresh_token()
            temporary = self.path.with_name('.config-'+uuid.uuid4().hex+'.tmp')
            try:
                temporary.write_text(json.dumps(self.values, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
