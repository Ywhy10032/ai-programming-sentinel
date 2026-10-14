import json

import pytest

from sentinel.claude import EVENTS, configure_integration, hook_event, integration_status
from sentinel.config import Config
from sentinel.state import StateStore


def payload(kind, **extra):
    return {'session_id': 'session-1', 'cwd': 'E:\\工程\\项目', 'hook_event_name': kind, **extra}


@pytest.mark.parametrize('kind,status', [
    ('UserPromptSubmit', 'running'), ('PreToolUse', 'running'), ('PostToolUse', 'running'),
    ('PostToolUseFailure', 'running'), ('PermissionRequest', 'waiting'), ('Stop', 'success'),
    ('StopFailure', 'error'), ('SessionEnd', 'cancelled'), ('Elicitation', 'waiting'),
    ('ElicitationResult', 'running'),
])
def test_official_lifecycle(kind, status):
    event = hook_event(payload(kind))
    assert event['id'] == 'claude:session-1:current'
    assert event['provider'] == 'claude' and event['title'] == '项目'
    assert event['status'] == status


def test_wait_resume_and_next_prompt_keep_correct_duration(tmp_path):
    now = [1000.]
    store = StateStore(tmp_path, clock=lambda: now[0])
    store.event(hook_event(payload('UserPromptSubmit'), now[0]))
    now[0] += 20
    store.event(hook_event(payload('PermissionRequest')))
    now[0] += 5
    store.event(hook_event(payload('PostToolUse')))
    assert store.snapshot()['task']['elapsed'] == 25
    store.event(hook_event(payload('Stop')))
    now[0] += 10
    store.event(hook_event(payload('UserPromptSubmit'), now[0]))
    assert store.snapshot()['task']['elapsed'] == 0
    now[0] += 8
    store.event(hook_event(payload('Stop')))
    assert store.snapshot()['task']['elapsed'] == 8


@pytest.mark.parametrize('kind', ['idle_prompt', 'auth_success', 'unknown'])
def test_idle_or_unknown_notifications_never_create_waiting_task(kind):
    assert hook_event(payload('Notification', notification_type=kind)) is None


def test_question_tool_and_permission_notification_wait():
    assert hook_event(payload('PreToolUse', tool_name='AskUserQuestion'))['status'] == 'waiting'
    assert hook_event(payload('Notification', notification_type='permission_prompt'))['status'] == 'waiting'
    assert hook_event(payload('PostToolUse', tool_name='AskUserQuestion'))['status'] == 'running'


def test_ignore_subagent_completion_and_unrelated_hooks():
    assert hook_event(payload('Stop', agent_id='child-1')) is None
    assert hook_event(payload('SubagentStop')) is None
    assert hook_event({'hook_event_name': 'Stop'}) is None


def test_private_prompt_and_tool_result_are_discarded():
    event = hook_event(payload('PostToolUse', prompt='PRIVATE_PROMPT', tool_input={'secret': 'SECRET'},
                              tool_response={'content': 'PRIVATE_RESULT'}))
    text = json.dumps(event)
    assert all(word not in text for word in ('PRIVATE_PROMPT', 'SECRET', 'PRIVATE_RESULT'))


def test_install_preserves_existing_hooks_and_settings_and_is_idempotent(tmp_path):
    config = Config(tmp_path / 'sentinel')
    path = tmp_path / '.claude' / 'settings.json'
    path.parent.mkdir()
    previous = {'permissions': {'deny': ['Read(secrets)']}, 'effortLevel': 'high',
                'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'existing-notification'}]}]}}
    original = json.dumps(previous, ensure_ascii=False).encode('utf-8')
    path.write_bytes(original)
    result = configure_integration(config, path=path)
    assert result['installed'] and result['changed']
    assert __import__('pathlib').Path(result['backup']).read_bytes() == original
    changed = json.loads(path.read_text(encoding='utf-8'))
    assert changed['permissions'] == previous['permissions']
    assert changed['hooks']['Stop'][0] == previous['hooks']['Stop'][0]
    assert set(EVENTS) <= changed['hooks'].keys()
    assert configure_integration(config, path=path)['changed'] is False
    assert len(list(path.parent.glob('settings.sentinel-backup-*.json'))) == 1
    status = integration_status(config, path)
    assert config.get('token') not in json.dumps(status)
    configure_integration(config, remove=True, path=path)
    assert json.loads(path.read_text(encoding='utf-8')) == previous


def test_bad_settings_are_preserved_and_disable_all_hooks_is_respected(tmp_path):
    config = Config(tmp_path / 'sentinel')
    path = tmp_path / 'settings.json'
    path.write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError):
        configure_integration(config, path=path)
    assert path.read_text(encoding='utf-8') == '{broken'
    path.write_text('{"disableAllHooks": true}', encoding='utf-8')
    configure_integration(config, path=path)
    assert integration_status(config, path)['installed']
    assert not integration_status(config, path)['enabled']


def test_reinstall_after_port_change_replaces_only_sentinel_callbacks(tmp_path):
    config = Config(tmp_path / 'sentinel')
    path = tmp_path / 'settings.json'
    configure_integration(config, path=path)
    config.update({'http_port': 19876})
    assert not integration_status(config, path)['installed']
    configure_integration(config, path=path)
    data = json.loads(path.read_text(encoding='utf-8'))
    assert all(len(entries) == 1 for entries in data['hooks'].values())
    assert integration_status(config, path)['enabled']


def test_ctrl_c_is_detected_without_replaying_history_or_guessing_from_tool_error(tmp_path, monkeypatch):
    import datetime
    import threading
    from sentinel.claude import ClaudeInterruptWatcher
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude'))
    config = Config(tmp_path / 'sentinel')
    store = StateStore(config.directory)
    watcher = ClaudeInterruptWatcher(store, config, threading.Event())
    transcript = tmp_path / 'claude/projects/project/session-1.jsonl'
    transcript.parent.mkdir(parents=True)
    record = {'type': 'user', 'message': {'content': [{'type': 'text', 'text': '[Request interrupted by user]'}]},
              'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    transcript.write_text(json.dumps(record) + '\n', encoding='utf-8')
    event = hook_event(payload('UserPromptSubmit'))
    store.event(event)
    watcher.register({'transcript_path': str(transcript)}, event)
    watcher.poll()
    assert store.snapshot()['task']['status'] == 'running'
    with transcript.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(dict(record, type='assistant')) + '\n')
    watcher.poll()
    assert store.snapshot()['task']['status'] == 'running'
    record['timestamp'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    data = (json.dumps(record) + '\n').encode()
    with transcript.open('ab') as stream:stream.write(data[:len(data)//2])
    watcher.poll()
    assert store.snapshot()['task']['status'] == 'running'
    with transcript.open('ab') as stream:stream.write(data[len(data)//2:])
    watcher.poll()
    assert store.snapshot()['task']['status'] == 'cancelled'


def test_interrupt_watcher_rejects_paths_outside_claude_projects(tmp_path, monkeypatch):
    import threading
    from sentinel.claude import ClaudeInterruptWatcher
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude'))
    config = Config(tmp_path / 'sentinel')
    watcher = ClaudeInterruptWatcher(StateStore(config.directory), config, threading.Event())
    watcher.register({'transcript_path': str(tmp_path / 'private.jsonl')}, hook_event(payload('UserPromptSubmit')))
    assert not watcher.cursors
