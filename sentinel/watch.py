from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
import sqlite3
import re
from dataclasses import dataclass, field
from pathlib import Path

from .diagnostics import is_build_command, summarize
from .state import clean_text

LOG = logging.getLogger(__name__)


def timestamp(value, fallback=None):
    try:
        if isinstance(value,(int,float)):
            return float(value)
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return fallback if fallback is not None else time.time()


@dataclass
class Cursor:
    path: Path
    offset: int = 0
    pending: bytes = b""
    session_id: str = ""
    cwd: str = ""
    turn: str = ""
    started: float = 0
    active: bool = False
    title: str = "Codex 任务"
    files: set = field(default_factory=set)
    signature: tuple = ()
    ignored: bool = False
    last_activity: float = 0

    @property
    def task_id(self):
        return f"codex:{self.session_id}:{self.turn}"


class CodexWatcher:
    """只读本机 JSONL。日志格式为内部格式，官方 notify 可作稳定完成入口。"""
    def __init__(self, store, config, stop: threading.Event, root: Path | None = None):
        self.store, self.config, self.stop = store, config, stop
        home = Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))
        self.root = root or home/"sessions"
        self.cursors: dict[Path, Cursor] = {}
        self.last_scan = 0
        self.last_scope = None
        self.thread_titles={}
        self.pending_inputs = {}

    def run(self):
        while not self.stop.is_set():
            try:
                enabled = self.config.get("codex_watch")
                scope = self.config.get("codex_scope")
                if self.last_scope != scope or not enabled:
                    if self.cursors:
                        ids = {c.task_id for c in self.cursors.values()}
                        with self.store.lock:
                            for task_id in ids:
                                self.store.tasks.pop(task_id,None)
                            for task_id, task in list(self.store.tasks.items()):
                                if task.source == 'Codex 操作请求':
                                    self.store.tasks.pop(task_id, None)
                            self.store.revision += 1
                            self.store._save_pending()
                    self.pending_inputs.clear()
                    self.cursors.clear()
                    self.last_scan = 0
                    self.last_scope = scope
                if enabled:
                    self.poll()
                with self.store.lock:
                    self.store.watcher.update(enabled=enabled, sessions=sum(not c.ignored for c in self.cursors.values()),
                        message="监听本机真实任务事件" if enabled else "自动监听已关闭")
            except (OSError, ValueError) as exc:
                LOG.warning("Codex 监听暂不可用: %s", exc)
                with self.store.lock:
                    self.store.watcher["message"] = "日志暂不可用，将自动重试"
            self.stop.wait(.6)

    def discover(self):
        now = time.time()
        self.last_scan = now
        if not self.root.exists():
            return
        # 日期目录范围受限，避免遍历整个历史库。兼容扁平测试目录。
        dated = [self.root/d.strftime("%Y/%m/%d") for d in
                 [dt.datetime.now()-dt.timedelta(days=i) for i in range(4)]]
        paths = []
        for directory in dated:
            if directory.exists():
                paths.extend(directory.glob("*.jsonl"))
        paths.extend(self.root.glob("*.jsonl"))
        # 已存在的老会话恢复执行时，仍可能写入旧日期目录。
        # 桌面版的只读索引提供近期会话路径；仅查询路径和标题元数据。
        databases=list(self.root.parent.glob("state_*.sqlite"))
        databases.sort(key=lambda p:int(p.stem.split('_')[-1]) if p.stem.split('_')[-1].isdigit() else -1,reverse=True)
        if databases:
            try:
                with sqlite3.connect(databases[0].as_uri()+"?mode=ro",uri=True,timeout=.15) as db:
                    columns={r[1] for r in db.execute("PRAGMA table_info(threads)")}
                    if {"rollout_path","title","updated_at"}<=columns:
                        order="recency_at_ms" if "recency_at_ms" in columns else "updated_at"
                        for rollout,title in db.execute(f"SELECT rollout_path,title FROM threads ORDER BY {order} DESC LIMIT 24"):
                            p=Path(rollout)
                            if p.exists() and p.suffix==".jsonl":
                                paths.append(p)
                                self.thread_titles[p]=clean_text(title,80)
            except (sqlite3.Error,OSError,ValueError):
                LOG.debug("桌面会话索引不可用，继续使用日志目录")
        paths=list(dict.fromkeys(paths))
        index=self.root.parent/'session_index.jsonl'
        if index.exists():
            try:
                # 索引只包含会话 id、标题和更新时间；保留最后一次命名记录。
                with index.open('rb') as stream:
                    stream.seek(max(0,index.stat().st_size-512*1024))
                    if stream.tell():stream.readline()
                    for line in stream:
                        try:entry=json.loads(line)
                        except (ValueError,UnicodeError):continue
                        thread_id=str(entry.get('id',''))
                        if thread_id:
                            for path in paths:
                                if thread_id in path.name:
                                    self.thread_titles[path]=clean_text(entry.get('thread_name',''),80)
            except OSError:
                pass
        recent = sorted(paths, key=lambda p:p.stat().st_mtime, reverse=True)[:48]
        for path in recent:
            if path not in self.cursors:
                self.bootstrap(path)
        for path in list(self.cursors):
            if path not in recent:
                cursor = self.cursors.pop(path)
                if cursor.active and not cursor.ignored:
                    self._event(cursor,"unknown",detail="日志不再活跃，状态待核实", notify=False)

    def bootstrap(self, path: Path):
        cursor = Cursor(path)
        self.cursors[path] = cursor
        with path.open("rb") as stream:
            first = stream.readline(65536)
            try:
                obj = json.loads(first)
                payload = obj.get("payload", {})
                if obj.get("type") == "session_meta":
                    cursor.session_id = clean_text(payload.get("id", path.stem), 80)
                    cursor.cwd = str(payload.get("cwd", ""))
                    source = payload.get("source", "")
                    cursor.ignored = isinstance(source, dict) and any("subagent" in str(k).lower() for k in source)
            except (ValueError, UnicodeError):
                pass
            cursor.session_id = cursor.session_id or path.stem
            cursor.title = self.thread_titles.get(path) or Path(cursor.cwd).name or "Codex 任务"
            scope = self.config.get("codex_scope").strip()
            if scope:
                try:
                    cursor.ignored |= not Path(cursor.cwd).resolve().is_relative_to(Path(scope).resolve())
                except (OSError, ValueError):
                    cursor.ignored = True
            size = path.stat().st_size
            # 启动时流式扫描生命周期，长任务的开始记录可能远离日志尾部。
            # 只解析生命周期及时间戳，不保存历史对话或推理内容。
            for line in stream:
                if not line.endswith(b"\n"):
                    cursor.pending=line
                    break
                if not any(marker in line for marker in [b'"task_started"',b'"task_complete"',b'"turn_aborted"',b'"event_msg"',
                             b'request_user_input_async',b'send_user_message_question_reply',b'"function_call_output"']):
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                cursor.last_activity=max(cursor.last_activity,timestamp(obj.get("timestamp"),0))
                if obj.get("type") != "event_msg" or obj.get("payload",{}).get("type") in ["task_started","task_complete","turn_aborted"]:
                    self.consume(cursor,obj,bootstrap=True)
            cursor.offset=stream.tell()
        if cursor.active and not cursor.ignored:
            # Windows 对打开中的文件可能延迟更新 mtime，实际事件时间才可靠。
            fresh = time.time()-cursor.last_activity < 600
            self._event(cursor,"running" if fresh else "unknown", detail="正在处理任务" if fresh else "日志较旧，状态待核实",notify=False,record_history=False)
        for call_id, request in list(self.pending_inputs.items()):
            if request['parent'] == cursor.task_id and request['confirmed'] and cursor.active:
                self._show_input(call_id, notify=False, record_history=False)

    def poll(self):
        if time.time()-self.last_scan > 3:
            self.discover()
        for cursor in list(self.cursors.values()):
            if cursor.ignored or not cursor.path.exists():
                continue
            size = cursor.path.stat().st_size
            if size < cursor.offset:
                self.bootstrap(cursor.path)
                continue
            if size == cursor.offset:
                if cursor.active and time.time()-cursor.last_activity > 1800:
                    old = self.store.tasks.get(cursor.task_id)
                    if old and old.status == "running":
                        self._event(cursor,"unknown",detail="长时间无新事件，状态待核实",notify=False)
                continue
            with cursor.path.open("rb") as stream:
                stream.seek(cursor.offset)
                block = stream.read(4*1024*1024)
                cursor.offset = stream.tell()
            data = cursor.pending+block
            parts = data.split(b"\n")
            cursor.pending = parts.pop()
            # 不让损坏日志中永不换行的记录无限占用内存。
            if len(cursor.pending) > 8*1024*1024:
                cursor.pending = b""
                LOG.warning("忽略超过大小限制的日志记录")
            for line in parts:
                try:
                    self.consume(cursor,json.loads(line))
                except (ValueError, UnicodeError, TypeError):
                    LOG.debug("忽略格式不匹配的日志记录")

    def _event(self, c: Cursor, status, detail="", notify=True, record_history=True, **extra):
        event = {"id": c.task_id,"status":status,"title":c.title,"source":"Codex 自动监听","mode":"ai",
                 "started_at":c.started or time.time(),"detail":detail,**extra}
        self.store.event(event,notify=notify,record_history=record_history)

    def consume(self, c: Cursor, obj: dict, bootstrap=False):
        if c.ignored:
            return
        top = obj.get("type")
        p = obj.get("payload", {})
        if not isinstance(p,dict):
            return
        kind = p.get("type")
        c.last_activity=max(c.last_activity,timestamp(obj.get("timestamp"),0))
        if top == "response_item":
            if kind == "function_call" and str(p.get("name", "")).split(".")[-1] == "request_user_input_async":
                self._queue_input(c, p)
            elif kind == "function_call_output" and p.get("call_id") in self.pending_inputs:
                try:
                    output = json.loads(p.get("output", "")) if isinstance(p.get("output"), str) else p.get("output")
                except (ValueError, TypeError):
                    output = None
                call_id = p.get("call_id")
                if isinstance(output, dict) and output.get("accepted") is True:
                    self.pending_inputs[call_id]['confirmed'] = True
                    if not bootstrap:
                        self._show_input(call_id)
                else:
                    self.pending_inputs.pop(call_id, None)
            elif kind == "message" and p.get("role") == "user":
                self._input_replies(p, bootstrap)
        if top == "event_msg" and kind == "task_started":
            attribution = p.get("turn_attribution", {})
            if isinstance(attribution,dict) and attribution.get("kind") in ["subagent","review"]:
                return
            c.turn = str(p.get("turn_id",p.get("root_turn_id", "turn")))
            c.started = timestamp(p.get("started_at",obj.get("timestamp")))
            c.active = True
            c.files.clear()
            if not bootstrap:
                self._event(c,"running",detail="正在处理任务",event_id=f"{c.task_id}:start")
        elif top == "event_msg" and kind in ["task_complete","turn_aborted"]:
            turn = str(p.get("turn_id",p.get("root_turn_id",c.turn)))
            if turn != c.turn:
                return
            c.active = False
            if kind == "turn_aborted":
                for call_id, request in list(self.pending_inputs.items()):
                    if request['parent'] == c.task_id:
                        self._finish_input(call_id, bootstrap, "对应任务已中断")
            if not bootstrap:
                options = {"files":len(c.files)} if c.files else {}
                self._event(c,"success" if kind == "task_complete" else "cancelled",
                            detail="本轮任务已结束" if kind == "task_complete" else "任务已中断",
                            event_id=f"{c.task_id}:{kind}",**options)
        elif not bootstrap and top == "event_msg" and kind in ["approval_requested","request_user_input"] and c.active:
            self._event(c,"waiting",detail="请在 Codex 中处理请求")
        elif not bootstrap and top == "event_msg" and kind == "error" and c.active:
            self._event(c,"error",detail=clean_text(p.get("message", "AI 工具报告错误")))
        elif not bootstrap and top == "event_msg" and kind == "item_completed" and c.active:
            item = p.get("item", {})
            self._item(c,item)
        elif not bootstrap and top == "response_item" and kind == "function_call" and c.active:
            if str(p.get("name", "")).split(".")[-1] == "request_user_input":
                self._event(c,"waiting",detail="Codex 正等待你的回答")
        if not bootstrap:
            with self.store.lock:
                self.store.watcher["last_event"] = time.time()
            old=self.store.tasks.get(c.task_id)
            if c.active and old and old.status=="unknown":
                self._event(c,"running",detail="收到新的任务活动",notify=False)

    def _queue_input(self, c, payload):
        if not c.active:
            return
        call_id = payload.get('call_id')
        try:
            args = payload.get('arguments', {})
            args = json.loads(args) if isinstance(args, str) else args
        except (ValueError, TypeError):
            return
        questions = args.get('questions') if isinstance(args, dict) else None
        if not isinstance(call_id, str) or not isinstance(questions, list) or not questions or call_id in self.pending_inputs:
            return
        first = questions[0] if isinstance(questions[0], dict) else {}
        self.pending_inputs[call_id] = {'id': f'{c.task_id}:input:{call_id}', 'parent': c.task_id,
            'title': c.title, 'detail': clean_text(first.get('title') or first.get('question') or '请在 Codex 中回答问题'),
            'confirmed': False, 'remaining': set(range(len(questions)))}
        if len(self.pending_inputs) > 1024:
            self.pending_inputs.pop(next(iter(self.pending_inputs)))

    def _show_input(self, call_id, *, notify=True, record_history=True):
        request = self.pending_inputs[call_id]
        old = self.store.tasks.get(request['id'])
        if old is not None:
            return
        self.store.event({'id': request['id'], 'status': 'waiting', 'title': request['title'],
            'source': 'Codex 操作请求', 'provider': 'codex', 'mode': 'ai', 'detail': request['detail'],
            'notification_only': True}, notify=notify, record_history=record_history)

    def _input_replies(self, payload, bootstrap):
        content = payload.get('content', [])
        if not isinstance(content, list):
            return
        for entry in content:
            text = entry.get('text', '') if isinstance(entry, dict) else ''
            if not isinstance(text, str) or not text.startswith('<send_user_message_question_reply>'):
                continue
            match = re.fullmatch(r'<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>\s*', text, re.S)
            if not match:
                continue
            try:
                replies = json.loads(match[1])
            except ValueError:
                continue
            for reply in replies if isinstance(replies, list) else []:
                if not isinstance(reply, dict) or 'answer' not in reply:
                    continue
                try:
                    key = reply.get('questionItemId')
                    key = json.loads(key) if isinstance(key, str) else key
                    if not isinstance(key, list) or len(key) != 3 or key[0] != 'request_user_input_async':
                        continue
                    call_id, index = key[1], key[2]
                    request = self.pending_inputs.get(call_id)
                    if not request or type(index) is not int:
                        continue
                    request['remaining'].discard(index)
                    if not request['remaining']:
                        self._finish_input(call_id, bootstrap)
                except (ValueError, TypeError):
                    continue

    def _finish_input(self, call_id, bootstrap, detail='已收到你的回答'):
        request = self.pending_inputs.pop(call_id, None)
        if request and request['id'] in self.store.tasks:
            self.store.event({'id': request['id'], 'status': 'success', 'title': request['title'],
                'source': 'Codex 操作请求', 'provider': 'codex', 'mode': 'ai', 'detail': detail,
                'notification_only': True}, notify=False, record_history=not bootstrap)

    def _item(self, c: Cursor, item: dict):
        if not isinstance(item, dict):
            return
        kind = item.get("type", "")
        if kind in ["FileChange","fileChange"]:
            changes = item.get("changes", {})
            if isinstance(changes,dict):
                c.files.update(changes)
            elif isinstance(changes,list):
                c.files.update(str(x.get("path")) for x in changes if isinstance(x,dict) and x.get("path"))
        elif kind in ["CommandExecution","commandExecution"]:
            command = str(item.get("command", ""))
            mode = is_build_command(command)
            exit_code = item.get("exit_code", item.get("exitCode"))
            if mode and type(exit_code) is int:
                output = str(item.get("aggregated_output",item.get("aggregatedOutput", "")))
                diagnostic = summarize(output,exit_code)
                duration = item.get("duration", {})
                seconds = duration.get("secs",0)+duration.get("nanos",0)/1e9 if isinstance(duration,dict) else 0
                build_id = f"{c.task_id}:command:{item.get('id','result')}"
                log_path = self.store.directory / "logs" / f"{abs(hash(build_id))}.log"
                log_path.parent.mkdir(parents=True,exist_ok=True)
                log_path.write_text(output,encoding="utf-8")
                ev = {"id":build_id,"status":"success" if exit_code==0 else "error","mode":mode,
                      "title":c.title,"source":"Codex 命令结果","detail":diagnostic.summary,
                      "location":diagnostic.location,"log_path":str(log_path),
                      "started_at":time.time()-max(0,seconds),"event_id":build_id}
                if diagnostic.errors is not None:
                    ev["errors"] = diagnostic.errors
                self.store.event(ev)
        elif kind in ["AgentMessage","agentMessage"]:
            # 等待被新的真实执行活动解除；异步提问本身不视为暂停。
            old = self.store.tasks.get(c.task_id)
            if old and old.status in ["waiting","unknown"]:
                self._event(c,"running",detail="任务已继续",notify=False)
