"""Google Antigravity 官方生命周期回调。保留默认权限策略与既有 Hook。"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import threading
import uuid
from pathlib import Path

from .state import clean_text

EVENTS = ('PreInvocation', 'PreToolUse', 'PostToolUse', 'Stop')
GROUP = 'ai-programming-sentinel'
LOCK = threading.RLock()


def settings_path():
    return Path.home()/'.gemini/config/hooks.json'


def read_settings(path):
    raw = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if not isinstance(raw, dict):
        raise ValueError('Antigravity 回调配置需要 JSON 对象，已保留原文件')
    return raw


def handler_command(kind):
    from .integrations import command_args
    return subprocess.list2cmdline(command_args('antigravity') + ['--event', kind])


def owned_handler(handler, kind):
    if not isinstance(handler, dict) or handler.get('type', 'command') != 'command':
        return False
    command = handler.get('command', '')
    if not isinstance(command, str):
        return False
    return command == handler_command(kind) or bool(re.search(
        r'(?:[/\\]|^)sentinel\.exe"? hook --provider antigravity --event '+re.escape(kind)+r'$', command, re.I))


def integration_status(path=None):
    path = path or settings_path()
    try:
        group = read_settings(path).get(GROUP, {})
        if not isinstance(group, dict):
            raise ValueError('同名回调组格式不正确')
        installed = set()
        for kind in EVENTS:
            entries = group.get(kind, [])
            for entry in entries if isinstance(entries, list) else []:
                handlers = entry.get('hooks', []) if kind in ('PreToolUse', 'PostToolUse') and isinstance(entry, dict) else [entry]
                if any(owned_handler(h, kind) for h in handlers):
                    installed.add(kind)
        ready = installed == set(EVENTS)
        enabled = ready and group.get('enabled', True) is not False
        return {'installed': ready, 'enabled': enabled, 'message':
                '全局回调已接入 · 新会话生效' if enabled else 'Antigravity 回调已暂停' if ready else 'Antigravity 尚未接入'}
    except (OSError, ValueError, TypeError):
        return {'installed': False, 'enabled': False, 'message': 'Antigravity 配置暂不可读，原文件保持不变'}


def configure_integration(*, remove=False, path=None):
    path = path or settings_path()
    with LOCK:
        original = path.read_bytes() if path.exists() else None
        if remove and original is None:
            return {'ok': True, 'changed': False, **integration_status(path)}
        raw = read_settings(path)
        previous = raw.get(GROUP, {})
        if not isinstance(previous, dict):
            raise ValueError('同名回调组不是哨兵配置，已保留原文件')
        # 不删除用户加在同一组里的其他事件和处理器。
        group = dict(previous)
        for kind in EVENTS:
            entries = group.get(kind, [])
            if not isinstance(entries, list):
                raise ValueError('Antigravity 回调格式不正确，已保留原文件')
            kept = []
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError('Antigravity 回调条目格式不正确')
                if kind in ('PreToolUse', 'PostToolUse'):
                    handlers = entry.get('hooks', [])
                    if not isinstance(handlers, list):
                        raise ValueError('Antigravity 工具回调格式不正确')
                    remaining = [h for h in handlers if not owned_handler(h, kind)]
                    if remaining or not handlers:
                        kept.append(dict(entry, hooks=remaining))
                elif not owned_handler(entry, kind):
                    kept.append(entry)
            if kept:
                group[kind] = kept
            else:
                group.pop(kind, None)
        if not remove:
            if previous.get('enabled') is False and set(group) - {'enabled'}:
                raise ValueError('同名回调组含已暂停的其他处理器，请先为该组改名，原文件保持不变')
            group['enabled'] = True
            for kind in EVENTS:
                handler = {'type': 'command', 'command': handler_command(kind), 'timeout': 5}
                entry = {'matcher': '*', 'hooks': [handler]} if kind in ('PreToolUse', 'PostToolUse') else handler
                group.setdefault(kind, []).append(entry)
        if set(group) <= {'enabled'}:
            raw.pop(GROUP, None)
        else:
            raw[GROUP] = group
        if original is not None and json.loads(original.decode('utf-8-sig')) == raw:
            return {'ok': True, 'changed': False, **integration_status(path)}
        path.parent.mkdir(parents=True, exist_ok=True)
        if original is not None:
            stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
            path.with_name('hooks.sentinel-backup-'+stamp+'-'+uuid.uuid4().hex[:6]+'.json').write_bytes(original)
        temporary = path.with_name('.sentinel-'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            if (path.read_bytes() if path.exists() else None) != original:
                raise ValueError('Antigravity 配置刚被其他程序更新，请重试')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return {'ok': True, 'changed': True, **integration_status(path)}


def other_stop_hooks(payload, path=None):
    paths = [path or settings_path()]
    roots = payload.get('workspacePaths', [])
    for root in roots if isinstance(roots, list) else []:
        if not isinstance(root, str) or root.startswith(('\\\\', '//')):
            continue
        candidate = Path(root)
        if candidate.is_absolute():
            paths.append(candidate/'.agents/hooks.json')
    try:
        for candidate in paths:
            for name, group in read_settings(candidate).items():
                if not isinstance(group, dict) or group.get('enabled', True) is False:
                    continue
                for handler in group.get('Stop', []):
                    if name != GROUP or not owned_handler(handler, 'Stop'):
                        return True
        return False
    except (OSError, TypeError, ValueError):
        # 无法核对是否还有续跑 Hook 时，不宣称最终成功。
        return True


def hook_event(payload, kind=None, *, may_continue=False):
    kind = kind or payload.get('hook_event_name', payload.get('hookEventName'))
    session = clean_text(payload.get('conversationId'), 80)
    if not session or kind not in EVENTS or payload.get('parentConversationId') or payload.get('isSubagent'):
        return None
    status = 'running'
    detail = 'Antigravity 正在处理任务'
    if kind == 'PreToolUse':
        call = payload.get('toolCall', {})
        if isinstance(call, dict) and call.get('name') in ('ask_question', 'ask_permission'):
            status = 'waiting'; detail = '请在 Antigravity 中回答或处理权限请求'
    elif kind == 'PostToolUse' and payload.get('error'):
        detail = '工具报告失败，Antigravity 正在处理'
    elif kind == 'Stop':
        reason = payload.get('terminationReason')
        if payload.get('error') or reason in ('error', 'max_steps_exceeded'):
            status = 'error'; detail = 'Antigravity 报告本轮失败或达到步数限制'
        elif reason in ('cancelled', 'canceled', 'aborted', 'interrupted', 'user_cancelled'):
            status = 'cancelled'; detail = 'Antigravity 报告本轮中断'
        elif reason == 'model_stop' and payload.get('fullyIdle') is True and not may_continue:
            status = 'success'; detail = '本轮正常结束，后台任务已全部结束'
        elif payload.get('fullyIdle') is False:
            detail = '仍有后台任务运行，等待全部结束'
        else:
            status = 'unknown'; detail = '准备结束，尚未确认最终状态或其他回调可能继续执行'
    roots = payload.get('workspacePaths', [])
    root = roots[0] if isinstance(roots, list) and roots and isinstance(roots[0], str) else ''
    title = root.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
    return {'id': f'antigravity:{session}:current', 'provider': 'antigravity', 'status': status,
            'title': clean_text(title, 80) or 'Antigravity 会话', 'source': 'Antigravity 官方回调', 'detail': detail}


def hook_output(kind):
    # allow 仅委托给后续 Hook / 默认权限规则；绝不使用 auto_approve 或 permissionOverrides。
    return {'decision': 'allow'} if kind == 'PreToolUse' else {'decision': 'stop'} if kind == 'Stop' else {}
