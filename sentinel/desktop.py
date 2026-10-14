"""只读桌面控件状态。界面消失不是成功；看不到状态时明确降级。"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import resource_path
from .brands import brand_icon
from .integrations import HOOK_EVENTS, integration_status
from .providers import PROVIDERS, provider_name

LOG = logging.getLogger(__name__)


@dataclass
class Observed:
    provider: str
    window: str
    scope: str
    status: str = ''
    sample: str = ''
    repeats: int = 0
    started: float = 0
    updated: float = 0


class DesktopWatcher:
    def __init__(self, store, config, stop, scanner=None, clock=time.time):
        self.store, self.config, self.stop = store, config, stop
        self.scanner = scanner or self.scan
        self.clock = clock
        self.observed = {}
        self.rows = {}
        self.message = '正在检查桌面客户端'
        self.lock = threading.RLock()
        self.process = None

    def scan(self):
        if os.name != 'nt':
            raise OSError('桌面监听仅适用于 Windows')
        helper = resource_path('observer.exe') if getattr(sys, 'frozen', False) else resource_path('tmp/desktop-observer/observer.exe')
        if not helper.exists():
            raise OSError('桌面观察器未构建，请运行 tools/build_observer.py')
        with self.lock:
            self.process = subprocess.Popen([str(helper), str(self.config.directory/'icons')],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            process = self.process
        try:
            output, _ = process.communicate(timeout=7)
            if process.returncode != 0:
                raise OSError('桌面观察器未返回有效结果')
            if len(output) > 256*1024:
                raise ValueError('桌面状态结果超限')
            return [json.loads(line) for line in output.decode('utf-8-sig').splitlines() if line.strip()]
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=2)
            with self.lock:
                self.process = None

    def close(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.kill()

    def _native_active(self, provider):
        with self.store.lock:
            # 同一客户端已有真实生命周期任务时，界面观察不得覆盖或重复计数。
            return any(t.provider == provider and not t.id.startswith('desktop:') and
                       t.status in ('running', 'waiting') for t in self.store.tasks.values()) or (
                       provider == 'chatgpt' and any(t.provider == 'codex' and t.status in ('running', 'waiting')
                                                   for t in self.store.tasks.values()))

    def _forget(self, window):
        self.observed.pop(window, None)
        with self.store.lock:
            task = self.store.tasks.get('desktop:'+window)
            if task and task.status in ('running', 'waiting', 'unknown'):
                del self.store.tasks[task.id]
                self.store.revision += 1

    def consume(self, samples):
        now = self.clock()
        seen = set()
        rows = {}
        for row in samples:
            if not isinstance(row, dict) or row.get('provider') not in PROVIDERS:
                continue
            provider = row['provider']; window = str(row.get('window', ''))
            if not window:
                continue
            seen.add(window)
            rows[provider] = {'detected': True, 'readable': row.get('readable') is True,
                              'message': '界面控件可读' if row.get('readable') else '已发现应用，当前界面状态不可读'}
            if self._native_active(provider):
                self._forget(window)
                continue
            scope = str(row.get('scope', ''))
            old = self.observed.get(window)
            if old and old.scope != scope and scope:
                self._forget(window)
                old = None
            if old is None:
                old = self.observed[window] = Observed(provider, window, scope, updated=now)
            if not row.get('visible') or not row.get('readable'):
                status = 'unknown' if old.status in ('running', 'waiting') else ''
            elif row.get('waiting'):
                status = 'waiting'
            elif row.get('error') and old.status:
                status = 'error'
            elif row.get('running'):
                status = 'running'
            elif row.get('complete') and old.status in ('running', 'waiting', 'unknown'):
                status = 'success'
            elif old.status in ('running', 'waiting'):
                status = 'unknown'
            else:
                status = ''
            # 连续两次同状态才切换，减少控件刷新导致的假提醒。
            old.repeats = old.repeats+1 if old.sample == status else 1
            old.sample = status
            if status and status != old.status and old.repeats >= 2:
                if status == 'running' and old.status not in ('running', 'waiting', 'unknown'):
                    old.started = now
                old.started = old.started or now
                old.status = status; old.updated = now
                detail = {'running': '界面显示正在生成 · 从首次观察开始计时',
                          'waiting': '界面有允许与拒绝操作，请到原客户端处理',
                          'error': '界面明确报告请求失败', 'success': '界面明确报告回答完成',
                          'unknown': '当前状态不可见；停止按钮消失不代表成功'}[status]
                self.store.event({'id': 'desktop:'+window, 'provider': provider, 'status': status,
                                  'title': '当前会话', 'source': provider_name(provider)+' 界面监听',
                                  'detail': detail, 'started_at': old.started})
            if old.status == 'unknown' and now-old.updated >= 30:
                self._forget(window)
        for window in list(self.observed):
            if window not in seen:
                self._forget(window)
        with self.lock:
            self.rows = rows

    def snapshot(self):
        with self.lock:
            rows = dict(self.rows)
            message = self.message
        result = []
        for provider, (name, _, _, method) in PROVIDERS.items():
            row = {'provider': provider, 'name': name, 'method': method,
                   'detected': False, 'readable': False, 'message': '尚未发现桌面窗口', **rows.get(provider, {})}
            if provider in HOOK_EVENTS:
                row['hook'] = integration_status(provider)
            result.append(row)
        return {'enabled': self.config.get('desktop_watch'), 'message': message, 'clients': result}

    def run(self):
        try:
            while not self.stop.is_set():
                if not self.config.get('desktop_watch'):
                    for window in list(self.observed):
                        self._forget(window)
                    self.message = '桌面监听已暂停'
                    self.stop.wait(1)
                    continue
                try:
                    self.consume(self.scanner())
                    brand_icon.cache_clear()
                    self.message = '只读监听 · 仅当前可读会话 · 不操作客户端'
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    # 超时或异常时将已有观察任务降级，不能留着“正在生成”。
                    unavailable = [{'provider': o.provider, 'window': o.window, 'visible': False,
                                    'readable': False, 'scope': o.scope} for o in self.observed.values()]
                    self.consume(unavailable)
                    self.message = '界面监听暂不可用，真实任务回调继续工作'
                    LOG.debug('桌面观察未返回结果')
                self.stop.wait(2)
        finally:
            self.close()
