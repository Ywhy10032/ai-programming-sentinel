"""Claude Code 官方生命周期回调与可撤销的本机接入。"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path

from .state import clean_text

HOOK_PATH = "/api/hooks/claude"
EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
          "PermissionRequest", "Notification", "Stop", "StopFailure", "SessionEnd",
          "Elicitation", "ElicitationResult")
SETTINGS_LOCK = threading.RLock()


def settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "settings.json"


def our_hook(hook: dict) -> bool:
    if not isinstance(hook, dict) or hook.get("type") != "http":
        return False
    try:
        url = urllib.parse.urlsplit(hook.get("url", ""))
        return (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost")
                and url.path == HOOK_PATH and "X-Sentinel-Token" in hook.get("headers", {}))
    except (ValueError, TypeError):
        return False


def read_settings(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(raw, dict) or not isinstance(raw.get("hooks", {}), dict):
        raise ValueError("Claude 设置格式不正确，已保留原文件")
    return raw


def integration_status(config, path: Path | None = None) -> dict:
    path = path or settings_path()
    try:
        raw = read_settings(path)
        expected_url = f"http://127.0.0.1:{config.get('http_port')}{HOOK_PATH}"
        installed = set()
        for event, entries in raw.get("hooks", {}).items():
            for entry in entries if isinstance(entries, list) else []:
                for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
                    if (our_hook(hook) and hook.get("url") == expected_url
                            and hook.get("headers", {}).get("X-Sentinel-Token") == config.get("token")):
                        installed.add(event)
        ready = set(EVENTS) <= installed
        disabled = raw.get("disableAllHooks", False) or not config.get("claude_watch")
        return {"installed": ready, "enabled": ready and not disabled,
                "message": "Claude 回调已暂停" if ready and disabled else
                           "Claude 已接入 · 新会话生效" if ready else "Claude 尚未接入"}
    except (OSError, ValueError, TypeError):
        return {"installed": False, "enabled": False, "message": "Claude 设置暂不可读"}


def configure_integration(config, *, remove=False, path: Path | None = None) -> dict:
    """只增删本工具的 HTTP 回调；保留权限、模型、其他通知和回调。"""
    path = path or settings_path()
    with SETTINGS_LOCK:
        original = path.read_bytes() if path.exists() else None
        raw = read_settings(path)
        hooks = raw.setdefault("hooks", {})
        for event in list(hooks):
            if not isinstance(hooks[event], list):
                raise ValueError("Claude 回调格式不正确，已保留原文件")
            remaining = []
            for entry in hooks[event]:
                if not isinstance(entry, dict) or not isinstance(entry.get("hooks", []), list):
                    raise ValueError("Claude 回调格式不正确，已保留原文件")
                kept = [h for h in entry.get("hooks", []) if not our_hook(h)]
                if kept or not entry.get("hooks"):
                    remaining.append(dict(entry, hooks=kept) if entry.get("hooks") else entry)
            if remaining:
                hooks[event] = remaining
            else:
                del hooks[event]
        if not remove:
            handler = {"type": "http", "url": f"http://127.0.0.1:{config.get('http_port')}{HOOK_PATH}",
                       "timeout": 2, "headers": {"X-Sentinel-Token": config.get("token")}}
            for event in EVENTS:
                entry = {"hooks": [handler]}
                if event == "Notification":
                    entry["matcher"] = "permission_prompt|elicitation_dialog|elicitation_url_dialog"
                hooks.setdefault(event, []).append(entry)
        if original is not None:
            previous = json.loads(original.decode("utf-8-sig"))
            if previous == raw:
                return {"ok": True, "changed": False, **integration_status(config, path)}
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if original is not None:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = path.with_name(f"settings.sentinel-backup-{stamp}-{uuid.uuid4().hex[:6]}.json")
            backup.write_bytes(original)
        temp = path.with_name(f".settings.sentinel-{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            # 防止读写间有其他工具更新设置。
            current = path.read_bytes() if path.exists() else None
            if current != original:
                raise ValueError("Claude 设置刚被其他程序更新，请重试接入")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        return {"ok": True, "changed": True, "backup": str(backup) if backup else None,
                **integration_status(config, path)}


def hook_event(payload: dict, now: float | None = None) -> dict | None:
    kind = payload.get("hook_event_name")
    # 子代理的停止不能冒充主会话结束。
    if kind not in EVENTS or payload.get("agent_id"):
        return None
    session = clean_text(payload.get("session_id"), 100)
    if not session:
        return None
    tool = payload.get("tool_name", "")
    mapping = {"UserPromptSubmit": "running", "PreToolUse": "running", "PostToolUse": "running",
               "PostToolUseFailure": "running", "PermissionRequest": "waiting", "Stop": "success",
               "StopFailure": "error", "SessionEnd": "cancelled", "Elicitation": "waiting",
               "ElicitationResult": "running"}
    status = mapping.get(kind)
    if kind == "Notification":
        if payload.get("notification_type") not in ("permission_prompt", "elicitation_dialog", "elicitation_url_dialog"):
            return None
        status = "waiting"
    if kind == "PreToolUse" and tool in ("AskUserQuestion", "ExitPlanMode"):
        status = "waiting"
    detail = {"running": "Claude 正在处理任务", "waiting": "请在 Claude 中处理",
              "success": "Claude 本轮任务已结束", "error": "Claude 请求出错", "cancelled": "Claude 会话已结束"}[status]
    if kind == "PostToolUseFailure":
        detail = "工具执行失败，Claude 正在处理"
    if kind == "StopFailure":
        detail = {"rate_limit": "Claude 额度或速率受限", "authentication_failed": "Claude 身份验证失败",
                  "billing_error": "Claude 账户额度不足", "api_error": "Claude 服务请求失败"}.get(payload.get("error"), detail)
    # 仅保留项目名、状态和耗时，不保留 prompt / tool_input / tool_response。
    cwd = str(payload.get("cwd", "")).replace("\\", "/").rstrip("/")
    event = {"id": f"claude:{session}:current", "provider": "claude", "status": status,
             "title": clean_text(cwd.rsplit("/", 1)[-1], 80) or "Claude 任务",
             "source": "Claude Code 官方事件", "mode": "ai", "detail": detail}
    if kind == "UserPromptSubmit":
        event["started_at"] = now if now is not None else time.time()
    return event


@dataclass
class TranscriptCursor:
    path: Path
    task_id: str
    offset: int = 0
    pending: bytes = b""


class ClaudeInterruptWatcher:
    """Stop 不报告 Ctrl+C；只识别本机会话日志的明确中断记录，不猜测完成。"""
    def __init__(self, store, config, stop):
        self.store, self.config, self.stop = store, config, stop
        self.cursors = {}
        self.lock = threading.RLock()

    def register(self, payload, event):
        raw_path = payload.get("transcript_path")
        if not raw_path:
            return
        try:
            path = Path(raw_path).resolve()
            root = (settings_path().parent / "projects").resolve()
            if path.suffix != ".jsonl" or not path.is_relative_to(root):
                return
            with self.lock:
                if path not in self.cursors:
                    self.cursors[path] = TranscriptCursor(path, event["id"], path.stat().st_size if path.exists() else 0)
                if len(self.cursors) > 64:
                    self.cursors.pop(next(iter(self.cursors)))
        except (OSError, ValueError):
            return

    def poll(self):
        if not self.config.get("claude_watch"):
            with self.lock:
                self.cursors.clear()
            return
        with self.lock:
            cursors = list(self.cursors.values())
        for cursor in cursors:
            try:
                if not cursor.path.exists():
                    continue
                size = cursor.path.stat().st_size
                if size < cursor.offset:
                    cursor.offset, cursor.pending = size, b""
                    continue
                with cursor.path.open("rb") as stream:
                    stream.seek(cursor.offset)
                    chunk = stream.read(256*1024)
                    cursor.offset = stream.tell()
                lines = (cursor.pending + chunk).split(b"\n")
                cursor.pending = lines.pop()
                if len(cursor.pending) > 2*1024*1024:
                    cursor.pending = b""
                for line in lines:
                    try:
                        obj = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(obj, dict) or obj.get("type") != "user" or obj.get("isSidechain"):
                        continue
                    message = obj.get("message", {})
                    content = message.get("content", []) if isinstance(message, dict) else []
                    texts = [content] if isinstance(content, str) else [
                        item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"] if isinstance(content, list) else []
                    if not any(text.strip() in ("[Request interrupted by user]", "[Request interrupted by user for tool use]")
                               for text in texts if isinstance(text, str)):
                        continue
                    with self.store.lock:
                        old = self.store.tasks.get(cursor.task_id)
                        if old is None or old.finished_at is not None:
                            continue
                        try:
                            when = dt.datetime.fromisoformat(str(obj.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
                        except (TypeError, ValueError):
                            continue
                        if when < old.started_at:
                            continue
                        self.store.event({"id": old.id, "provider": "claude", "status": "cancelled",
                                          "title": old.title, "source": "Claude Code 中断记录", "mode": "ai",
                                          "detail": "Claude 任务已被中断"}, notify=False)
            except OSError:
                continue

    def run(self):
        while not self.stop.is_set():
            self.poll()
            self.stop.wait(.6)
