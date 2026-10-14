"""只读 DeepSeek Harness 的现行 JSONL / 分帧 Zstandard 会话日志。"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import zstandard

from .state import clean_text

LOG = logging.getLogger(__name__)
MAX_LINE = 2*1024*1024
MAX_FRAME = 16*1024*1024


@dataclass
class SessionCursor:
    path: Path
    offset: int = 0
    pending: bytes = b''
    frame: bytes = b''
    decoder: object = None
    session: str = ''
    version: int = 0
    seq: int = -1
    title: str = 'DeepSeek Harness 任务'
    turn: int = 0
    started: float = 0
    active: bool = False
    ignored: bool = False
    initial: bool = True
    signature: tuple = ()
    last_event: dict | None = None


class DeepSeekWatcher:
    def __init__(self, store, config, stop, roots=None, clock=time.time):
        self.store, self.config, self.stop = store, config, stop
        home = Path(os.environ.get('DSH_HOME', str(Path.home()/'.dsh')))
        self.roots = roots or [home/'sessions', home/'sessions-desktop']
        self.clock = clock
        self.cursors = {}
        self.last_scan = 0
        self._roots_key = None
        self.message = '尚未发现 DeepSeek Harness 会话目录'

    def roots_now(self):
        custom = self.config.get('deepseek_root')
        return [Path(custom).expanduser()] if custom else self.roots

    def discover(self):
        paths = []
        for root in self.roots_now():
            if not root.is_dir() or root.is_symlink():
                continue
            # 官方布局最多两级子目录，不扫描账户目录、凭据或完整工作区。
            for pattern in ('*/*/session*.jsonl', '*/*/session*.jsonl.zstd'):
                paths.extend(root.glob(pattern))
        highest = {}
        for path in paths[:10000]:
            if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
                continue
            match = re.fullmatch(r'session(?:\.v(\d+))?\.jsonl(?:\.zstd)?', path.name)
            if not match:
                continue
            version = int(match[1] or 0)
            old = highest.get(path.parent)
            if old is None or version > old[0]:
                highest[path.parent] = (version, path)
        recent = []
        for _, path in highest.values():
            try:
                if self.clock()-path.stat().st_mtime < 3600:
                    recent.append(path)
            except OSError:
                pass
        for path in sorted(recent, key=lambda p:p.stat().st_mtime, reverse=True)[:30]:
            self.cursors.setdefault(path, SessionCursor(path))
        self.message = f'只读监听 {len(self.cursors)} 个近期会话' if paths else '尚未发现 DeepSeek Harness 会话日志'

    def event(self, cursor, record, *, notify=True):
        if not isinstance(record, dict):
            return None
        # 现行物理 header 的 type 由版本目录决定；不从普通聊天消息猜身份。
        if not cursor.session and record.get('type') == 'session' and type(record.get('version')) is int and isinstance(record.get('id'), str):
            cursor.version = record['version']
            cursor.ignored = cursor.version not in (2, 3, 4) or record.get('origin') == 'subagent'
            cursor.session = clean_text(record['id'], 80)
            cwd = str(record.get('cwd', '')).replace('\\', '/').rstrip('/')
            cursor.title = clean_text(cwd.rsplit('/', 1)[-1], 80) or cursor.title
            return None
        if cursor.ignored or not cursor.session:
            return None
        seq = record.get('seq')
        if type(seq) is not int or seq < 0 or seq <= cursor.seq:
            return None
        if seq != cursor.seq+1:
            cursor.ignored = True
            raise ValueError('会话日志事件序号不连续')
        cursor.seq = seq
        kind, data = record.get('type'), record.get('data', {})
        if not isinstance(data, dict):
            return None
        status = None
        try:
            observed = float(record.get('time', self.clock()*1000))/1000
        except (TypeError, ValueError):
            observed = self.clock()
        if not 0 < observed <= self.clock()+5:
            observed = self.clock()
        if kind == 'turn/start' and type(data.get('turn')) is int:
            cursor.turn = data['turn']; cursor.started = observed; cursor.active = True
            status = 'running'
        elif kind == 'turn/end' and data.get('turn') == cursor.turn:
            reason = data.get('reason', {})
            if not isinstance(reason, dict):
                return None
            status = {'completed': 'success', 'aborted': 'cancelled', 'interrupted': 'cancelled',
                      'error': 'error', 'blocked': 'waiting', 'max-tokens': 'error', 'forked': 'cancelled'}.get(reason.get('kind'))
            cursor.active = False
        elif kind == 'step/start' and cursor.active:
            status = 'running'
        # approval/asked 是整个决策链的审计，可能自动批准，不能据此猜“需要用户操作”。
        if not status:
            return None
        detail = {'running': '会话事件确认任务正在执行', 'success': '本轮任务正常结束',
                  'waiting': '任务被阻挡，请在 DeepSeek Harness 中检查',
                  'error': '会话事件报告失败或输出限制', 'cancelled': '会话事件报告中断'}[status]
        event = {'id': f'deepseek:{cursor.session}:{cursor.turn}', 'provider': 'deepseek',
                 'status': status, 'title': cursor.title, 'source': 'DeepSeek Harness 会话事件',
                 'detail': detail, 'started_at': cursor.started or observed,
                 'event_id': f'deepseek:{cursor.session}:{seq}'}
        if not cursor.initial:
            self.store.event(event, notify=notify)
        return event

    def lines(self, cursor, body):
        cursor.pending += body
        while b'\n' in cursor.pending:
            line, cursor.pending = cursor.pending.split(b'\n', 1)
            if len(line) > MAX_LINE:
                # 大型 tool/message 内容忽略会破坏连续序号，明确停止识别该日志。
                raise ValueError('会话行过大，停止监听该文件')
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                raise ValueError('会话日志格式无效') from None
            event = self.event(cursor, record)
            if event:
                cursor.last_event = event
        if len(cursor.pending) > MAX_LINE:
            raise ValueError('未完成会话行过大')

    def poll(self, cursor):
        stat = cursor.path.stat()
        signature = (stat.st_dev, stat.st_ino)
        if cursor.signature and (cursor.signature != signature or stat.st_size < cursor.offset):
            self.cursors[cursor.path] = cursor = SessionCursor(cursor.path)
        cursor.signature = signature
        if stat.st_size == cursor.offset or cursor.ignored:
            return
        with cursor.path.open('rb') as stream:
            stream.seek(cursor.offset)
            budget = 32*1024*1024
            while budget > 0:
                chunk = stream.read(min(8192, budget))
                if not chunk:
                    break
                cursor.offset += len(chunk); budget -= len(chunk)
                if cursor.path.suffix != '.zstd':
                    self.lines(cursor, chunk)
                    continue
                remaining = chunk
                while remaining:
                    if cursor.decoder is None:
                        cursor.decoder = zstandard.ZstdDecompressor(max_window_size=64*1024).decompressobj()
                    cursor.frame += cursor.decoder.decompress(remaining)
                    if len(cursor.frame) > MAX_FRAME:
                        raise ValueError('会话压缩帧过大')
                    remaining = cursor.decoder.unused_data
                    if cursor.decoder.eof:
                        # 仅在校验完整帧后处理，其中的半行保留到下一帧。
                        self.lines(cursor, cursor.frame)
                        cursor.decoder = None; cursor.frame = b''
                    elif remaining:
                        raise ValueError('会话压缩帧状态无效')
        if cursor.initial and budget > 0 and cursor.decoder is None and not cursor.pending:
            cursor.initial = False
            # 启动只恢复近期仍在运行的任务，绝不重播历史完成、错误或声音。
            if cursor.last_event and cursor.last_event['status'] == 'running' and self.clock()-stat.st_mtime < 120:
                self.store.event(cursor.last_event, notify=False, record_history=False)

    def run(self):
        while not self.stop.is_set():
            roots_key = tuple(str(p) for p in self.roots_now())
            if self._roots_key is not None and self._roots_key != roots_key:
                self.cursors.clear()
                self.last_scan = 0
                with self.store.lock:
                    for key in list(self.store.tasks):
                        if key.startswith('deepseek:') and not self.store.tasks[key].finished_at:
                            del self.store.tasks[key]
                    self.store.revision += 1
            self._roots_key = roots_key
            if not self.config.get('deepseek_watch'):
                self.message = 'DeepSeek Harness 监听已暂停'
                with self.store.lock:
                    for key in list(self.store.tasks):
                        if key.startswith('deepseek:') and not self.store.tasks[key].finished_at:
                            del self.store.tasks[key]
                self.cursors.clear(); self.stop.wait(1)
                continue
            try:
                if self.clock()-self.last_scan > 5:
                    self.discover(); self.last_scan = self.clock()
                for cursor in list(self.cursors.values()):
                    try:
                        self.poll(cursor)
                    except (OSError, ValueError, zstandard.ZstdError):
                        cursor.ignored = True
                        self.message = '部分日志版本或格式不可读，其余接入继续工作'
                        with self.store.lock:
                            for task in self.store.tasks.values():
                                if task.id.startswith('deepseek:'+cursor.session+':') and not task.finished_at:
                                    self.store.event({'id':task.id, 'provider':'deepseek', 'status':'unknown',
                                                      'title':task.title, 'source':task.source,
                                                      'detail':'日志暂不可读，请检查原客户端'}, notify=False)
            except OSError:
                LOG.debug('DeepSeek 会话目录暂不可读')
            self.stop.wait(.6)
