"""官方桌面编程工具回调：只提醒，不返回权限决定或续跑指令。"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .config import resource_path
from .providers import PROVIDERS, provider_name
from .state import clean_text

LOCK = threading.RLock()
HOOK_EVENTS = {
    'zcode': ('UserPromptSubmit', 'PreToolUse', 'PermissionRequest', 'PostToolUse', 'PostToolUseFailure', 'Stop'),
    # Cursor 的工具前置事件要求权限结果，旁观者不订阅这些事件。
    'cursor': ('beforeSubmitPrompt', 'postToolUse', 'postToolUseFailure', 'stop'),
    'windsurf': ('pre_user_prompt', 'post_cascade_response'),
}


def integration_path(provider):
    return {'zcode': Path.home()/'.zcode/cli/config.json',
            'cursor': Path.home()/'.cursor/hooks.json',
            'windsurf': Path.home()/'.codeium/windsurf/hooks.json'}[provider]


def command_args(provider):
    if getattr(sys, 'frozen', False):
        cli = Path(sys.executable).with_name('sentinel.exe')
        if not cli.exists():
            raise ValueError('请保留便携包中的 sentinel.exe')
        return [str(cli), 'hook', '--provider', provider]
    return [sys.executable, str(resource_path('main.py')), 'hook', '--provider', provider]


def read_config(path):
    raw = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if not isinstance(raw, dict) or not isinstance(raw.get('hooks', {}), dict):
        raise ValueError('已有回调设置格式不正确，已保留原文件')
    return raw


def our_hook(hook, provider):
    if not isinstance(hook, dict):
        return False
    if provider == 'zcode':
        args = hook.get('args', [])
        executable = str(hook.get('command', '')).replace('\\', '/').rsplit('/', 1)[-1].lower()
        owned = executable == 'sentinel.exe' or (isinstance(args, list) and args and
                                                args[0] == str(resource_path('main.py')))
        return owned and hook.get('type') == 'process' and isinstance(args, list) and args[-3:] == ['hook', '--provider', provider]
    command = hook.get('command', '')
    if not isinstance(command, str):
        return False
    return command == subprocess.list2cmdline(command_args(provider)) or bool(
        re.search(r'(?:[/\\]|^)sentinel\.exe"? hook --provider ' + re.escape(provider) + r'$', command, re.I))


def integration_status(provider, path=None):
    path = path or integration_path(provider)
    try:
        raw = read_config(path)
        group = raw.get('hooks', {})
        events = group.get('events', {}) if provider == 'zcode' else group
        installed = set()
        for kind, entries in events.items():
            for entry in entries if isinstance(entries, list) else []:
                hooks = entry.get('hooks', []) if provider == 'zcode' and isinstance(entry, dict) else [entry]
                if any(our_hook(h, provider) and h.get('enabled', True) for h in hooks):
                    installed.add(kind)
        ready = set(HOOK_EVENTS[provider]) <= installed
        enabled = ready and (provider != 'zcode' or group.get('enabled') is True)
        return {'installed': ready, 'enabled': enabled, 'message':
                '回调已接入 · 新会话生效' if enabled else '回调已暂停' if ready else '尚未接入回调'}
    except (OSError, ValueError, TypeError, AttributeError):
        return {'installed': False, 'enabled': False, 'message': '设置暂不可读，原文件保持不变'}


def configure_integration(provider, *, remove=False, path=None):
    if provider not in HOOK_EVENTS:
        raise ValueError('此客户端不提供自动回调安装')
    path = path or integration_path(provider)
    with LOCK:
        original = path.read_bytes() if path.exists() else None
        if remove and original is None:
            return {'ok': True, 'changed': False, **integration_status(provider, path)}
        state_path = path.with_name(path.stem + '.sentinel-integration-state.json')
        saved_state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        if not isinstance(saved_state, dict):
            raise ValueError('哨兵接入记录无效，已保留原设置')
        raw = read_config(path)
        group = raw.setdefault('hooks', {})
        events = group.setdefault('events', {}) if provider == 'zcode' else group
        if not isinstance(events, dict):
            raise ValueError('已有事件设置格式不正确，已保留原文件')
        for kind in list(events):
            entries = events[kind]
            if not isinstance(entries, list):
                raise ValueError('已有事件设置格式不正确，已保留原文件')
            remaining = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError('已有事件设置格式不正确，已保留原文件')
                if provider == 'zcode':
                    hooks = entry.get('hooks', [])
                    if not isinstance(hooks, list):
                        raise ValueError('已有回调设置格式不正确，已保留原文件')
                    kept = [h for h in hooks if not our_hook(h, provider)]
                    if kept or not hooks:
                        remaining.append(dict(entry, hooks=kept))
                elif not our_hook(entry, provider):
                    remaining.append(entry)
            if remaining:
                events[kind] = remaining
            else:
                del events[kind]
        if not remove:
            args = command_args(provider)
            if provider == 'zcode':
                # 启用状态可撤销；记录原值，避免移除时关掉用户原有回调。
                saved_state.setdefault('previous_enabled', group.get('enabled'))
                group['enabled'] = True
                hook = {'type': 'process', 'command': args[0], 'args': args[1:],
                        'timeoutMs': 5000, 'enabled': True, 'statusMessage': 'AI 编程哨兵提醒'}
            else:
                hook = {'command': subprocess.list2cmdline(args)}
                if provider == 'windsurf':
                    # powershell 入口优先使用 Windows 正确的 & 调用语法。
                    hook['powershell'] = '& ' + ' '.join("'" + a.replace("'", "''") + "'" for a in args)
                    hook['show_output'] = False
                else:
                    raw.setdefault('version', 1)
            for kind in HOOK_EVENTS[provider]:
                entry = {'hooks': [dict(hook)]} if provider == 'zcode' else dict(hook)
                events.setdefault(kind, []).append(entry)
        elif provider == 'zcode' and 'previous_enabled' in saved_state:
            previous = saved_state.pop('previous_enabled')
            if previous is None:
                group.pop('enabled', None)
            else:
                group['enabled'] = previous
        if original is not None and json.loads(original.decode('utf-8-sig')) == raw:
            return {'ok': True, 'changed': False, **integration_status(provider, path)}
        path.parent.mkdir(parents=True, exist_ok=True)
        if original is not None:
            backup = path.with_name(path.stem + '.sentinel-backup-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.json')
            backup.write_bytes(original)
        temporary = path.with_name('.sentinel-' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            if (path.read_bytes() if path.exists() else None) != original:
                raise ValueError('设置被其他程序更新，请重试')
            temporary.replace(path)
            if provider == 'zcode':
                state_temp = state_path.with_suffix('.tmp')
                state_temp.write_text(json.dumps(saved_state, ensure_ascii=False), encoding='utf-8')
                state_temp.replace(state_path)
        finally:
            temporary.unlink(missing_ok=True)
        return {'ok': True, 'changed': True, **integration_status(provider, path)}


def hook_event(payload, provider, now=None):
    if provider not in PROVIDERS or not isinstance(payload, dict):
        raise ValueError('未知客户端或事件格式无效')
    kind = payload.get('hook_event_name', payload.get('agent_action_name', payload.get('type')))
    if payload.get('agent_id') or payload.get('is_subagent') or payload.get('subagent_id'):
        return None
    session = clean_text(payload.get('session_id', payload.get('conversation_id', payload.get('trajectory_id'))), 80)
    if not session:
        return None
    mapping = {'UserPromptSubmit': 'running', 'beforeSubmitPrompt': 'running', 'pre_user_prompt': 'running',
               'PreToolUse': 'running', 'PostToolUse': 'running', 'PostToolUseFailure': 'running',
               'postToolUse': 'running', 'postToolUseFailure': 'running', 'PermissionRequest': 'waiting',
               'task-started': 'running', 'approval-requested': 'waiting', 'request-user-input': 'waiting',
               'task-completed': 'success', 'task-failed': 'error', 'task-cancelled': 'cancelled'}
    # ZCode Stop 是“准备结束”，可能被后面的 Hook 续跑，不伪装成确认完成。
    if kind == 'Stop' and provider == 'zcode':
        status = 'unknown'
    elif kind == 'stop' and provider == 'cursor':
        status = {'completed': 'success', 'aborted': 'cancelled', 'error': 'error'}.get(payload.get('status'))
    elif kind == 'post_cascade_response' and provider == 'windsurf':
        status = 'success'
    else:
        status = mapping.get(kind)
    if not status:
        return None
    if kind == 'PreToolUse' and payload.get('tool_name') in ('AskUserQuestion', 'ExitPlanMode'):
        status = 'waiting'
    turn = clean_text(payload.get('generation_id', payload.get('turn_id', 'current')), 60)
    name = provider_name(provider)
    roots = payload.get('workspace_roots', [])
    cwd = payload.get('cwd') or (roots[0] if isinstance(roots, list) and roots else '')
    title = str(cwd).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
    detail = {'running': '正在处理任务', 'waiting': '请在原客户端中处理', 'success': '本轮任务已结束',
              'error': '客户端报告任务失败', 'cancelled': '客户端报告任务中断',
              'unknown': '模型准备结束，等待最终状态；可能继续执行'}[status]
    event = {'id': f'{provider}:{session}:{turn}', 'provider': provider, 'status': status,
             'title': clean_text(title, 80) or name+' 任务', 'source': name+' 官方回调', 'detail': detail}
    if kind in ('UserPromptSubmit', 'beforeSubmitPrompt', 'pre_user_prompt', 'task-started'):
        event['started_at'] = now if now is not None else time.time()
    return event
