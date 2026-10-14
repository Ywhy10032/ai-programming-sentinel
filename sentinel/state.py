from __future__ import annotations

import dataclasses
import datetime as dt
import json
import threading
import time
from collections import deque
from pathlib import Path

from .brands import provider_for

STATUSES = {"running", "waiting", "success", "error", "cancelled", "unknown"}
LABELS = {"running": "正在生成", "waiting": "需要操作", "success": "任务完成",
          "error": "任务出错", "cancelled": "已停止", "unknown": "等待事件", "idle": "随时待命"}


def clean_text(value, limit: int = 120) -> str:
    import re
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value or ""))
    return " ".join("".join(c for c in text if c >= " " or c in "\n\t").split())[:limit]


@dataclasses.dataclass
class Task:
    id: str
    status: str
    title: str
    source: str
    mode: str
    detail: str
    started_at: float
    updated_at: float
    finished_at: float | None = None
    errors: int | None = None
    files: int | None = None
    location: str = ""
    acknowledged: bool = False
    log_path: str = ""
    provider: str = ""
    notification_only: bool = False

    def public(self, now: float) -> dict:
        data = dataclasses.asdict(self)
        data["elapsed"] = max(0, (self.finished_at or now) - self.started_at)
        data["label"] = LABELS[self.status]
        if self.notification_only and self.status == "success":
            data["label"] = "已处理"
        if self.mode in ["build", "test"]:
            subject = "测试" if self.mode == "test" else "编译"
            data["label"] = {"running": f"正在{subject}", "success": f"{subject}通过",
                             "error": f"{subject}失败"}.get(self.status, data["label"])
        return data


class StateStore:
    """优先保留待确认错误；成功结果短暂显示；绝不从时间推算进度。"""
    def __init__(self, directory: Path, on_notify=None, clock=time.time, on_event=None):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = threading.RLock()
        self.tasks: dict[str, Task] = {}
        self.history = deque(maxlen=100)
        self.seen = deque(maxlen=2048)
        self.on_notify = on_notify
        self.on_event=on_event
        self.revision = 0
        self.health = {"connected": False, "port": "", "device": "", "frames": 0,
                       "fps": 0, "message": "正在寻找 USB 屏幕", "last_ack": None}
        self.watcher = {"enabled": False, "sessions": 0, "message": "正在启动", "last_event": None}
        path = directory / "history.json"
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(previous, list):
                    self.history.extend(previous[-100:])
            except (OSError, ValueError):
                pass
        pending=directory/'pending.json'
        if pending.exists():
            try:
                saved=json.loads(pending.read_text(encoding='utf-8'))
                fields={f.name for f in dataclasses.fields(Task)}
                for raw in saved if isinstance(saved,list) else []:
                    if not isinstance(raw,dict) or raw.get('acknowledged'):
                        continue
                    if raw.get('status') != 'error' and not (raw.get('status') == 'waiting' and raw.get('notification_only') is True):
                        continue
                    try:
                        task=Task(**{k:v for k,v in raw.items() if k in fields})
                        task.public(self.clock())
                        self.tasks[task.id]=task
                    except (TypeError,ValueError,KeyError):
                        continue
            except (OSError,ValueError):
                pass

    def event(self, raw: dict, *, notify=True, record_history=True) -> Task | None:
        if not isinstance(raw, dict):
            raise ValueError("事件必须是 JSON 对象")
        status = raw.get("status")
        if status not in STATUSES:
            raise ValueError("不支持的任务状态")
        task_id = clean_text(raw.get("id"), 160)
        if not task_id:
            raise ValueError("事件需要任务 id")
        mode = raw.get("mode", "ai")
        if mode not in ["ai", "build", "test"]:
            raise ValueError("模式应为 ai、build 或 test")
        for key in ["errors", "files"]:
            if key in raw and (type(raw[key]) is not int or not 0 <= raw[key] <= 100000):
                raise ValueError(f"{key} 必须是非负整数")
        if raw.get("provider", "") not in ("", "codex", "claude"):
            raise ValueError("工具来源应为 codex 或 claude")
        if type(raw.get("notification_only", False)) is not bool:
            raise ValueError("独立提醒标记需要布尔值")
        now = self.clock()
        event_key = clean_text(raw.get("event_id"), 240)
        with self.lock:
            if event_key and event_key in self.seen:
                return None
            old = self.tasks.get(task_id)
            start = now
            if old and not (status == "running" and old.finished_at is not None):
                start = old.started_at
            if "started_at" in raw:
                try:
                    start = float(raw["started_at"])
                    if not 0 < start <= now + 5:
                        raise ValueError()
                except (TypeError, ValueError):
                    raise ValueError("开始时间无效") from None
            finished = now if status in ["success", "error", "cancelled"] else None
            title = clean_text(raw.get("title", old.title if old else "AI 任务"), 80)
            task = Task(task_id, status, title or "AI 任务", clean_text(raw.get("source", "手动事件"), 40),
                        mode, clean_text(raw.get("detail", "")), start, now, finished,
                        raw.get("errors"), raw.get("files"), clean_text(raw.get("location", ""), 80),
                        False, clean_text(raw.get("log_path", ""), 1024),
                        provider_for(raw) or (old.provider if old else ""),
                        raw.get("notification_only", old.notification_only if old else False))
            self.tasks[task_id] = task
            if event_key:
                self.seen.append(event_key)
            self.revision += 1
            item = task.public(now)
            item["time"] = dt.datetime.fromtimestamp(now).strftime("%H:%M:%S")
            if record_history:
                self.history.appendleft(item)
            # 有界存储：优先移除已结束任务，不丢弃正在执行的任务。
            if len(self.tasks) > 150:
                ended = sorted((t for t in self.tasks.values() if t.finished_at), key=lambda t: t.updated_at)
                for t in ended[:len(self.tasks) - 150]:
                    self.tasks.pop(t.id, None)
            if record_history:
                self._save_history()
            self._save_pending()
        if self.on_event:
            self.on_event(task)
        if notify and self.on_notify and status in ["waiting", "success", "error"]:
            if old is None or old.status != status:
                self.on_notify(task)
        return task

    def acknowledge(self, task_id: str | None = None):
        with self.lock:
            for task in self.tasks.values():
                if task_id is None or task.id == task_id:
                    if task.notification_only and task.status == "waiting":
                        task.status = "success"
                        task.finished_at = self.clock()
                        task.detail = "已在控制面板确认提醒"
                    if task.finished_at is not None:
                        task.acknowledged = True
            self.revision += 1
            self._save_pending()

    def snapshot(self, hold=18) -> dict:
        now = self.clock()
        with self.lock:
            visible = [t for t in self.tasks.values() if not t.acknowledged and (
                t.status in ["running", "waiting", "unknown", "error"] or
                t.finished_at is not None and now - t.finished_at < hold)
                and not (t.notification_only and t.finished_at is not None)]
            priority = {"waiting": 6, "error": 5, "running": 3, "unknown": 2, "success": 1, "cancelled": 0}
            # 新完成任务显示几秒，再回到后台其他运行任务。
            def score(t):
                p = priority[t.status]
                if t.status == "success" and now - (t.finished_at or now) < 6:
                    p = 4
                if t.status == "running" and t.mode in ["build", "test"]:
                    p = 3.5
                return p, t.updated_at
            selected = max(visible, key=score) if visible else None
            return {"task": selected.public(now) if selected else None,
                    "tasks": [t.public(now) for t in sorted(self.tasks.values(), key=lambda t:t.updated_at, reverse=True)][:40],
                    "active_count": sum(t.status in ["running", "waiting"] and not t.notification_only for t in self.tasks.values()),
                    "history": list(self.history)[:40], "device": dict(self.health),
                    "watcher": dict(self.watcher), "revision": self.revision, "now": now}

    def _save_history(self):
        path = self.directory / "history.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(list(self.history), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _save_pending(self):
        pending=[dataclasses.asdict(t) for t in self.tasks.values() if not t.acknowledged and
                 (t.status=='error' or t.notification_only and t.status=='waiting')]
        path=self.directory/'pending.json'
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(pending,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(path)
