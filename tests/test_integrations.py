import json
import sys
from pathlib import Path

import pytest

from sentinel.integrations import configure_integration, hook_event, integration_status


@pytest.mark.parametrize('provider', ['zcode', 'cursor', 'windsurf'])
def test_install_upgrade_and_remove_preserve_user_configuration(tmp_path, provider):
    path = tmp_path/'配置 空格.json'
    other = {'type': 'process', 'command': 'original-tool', 'args': ['data']} if provider == 'zcode' else {'command': 'original-tool'}
    raw = {'model': 'user-model', 'permissions': {'allow': []}, 'hooks':
           {'enabled': False, 'events': {'Stop': [{'matcher': 'user', 'hooks': [other]}]}} if provider == 'zcode' else
           {'stop': [other]}}
    path.write_text(json.dumps(raw), encoding='utf-8')
    result = configure_integration(provider, path=path)
    assert result['enabled'] and result['changed']
    installed = path.read_bytes()
    assert not configure_integration(provider, path=path)['changed']
    assert path.read_bytes() == installed
    assert list(tmp_path.glob('*.sentinel-backup-*.json'))
    configure_integration(provider, path=path, remove=True)
    restored = json.loads(path.read_text(encoding='utf-8'))
    assert restored['model'] == 'user-model' and restored['permissions'] == {'allow': []}
    assert restored['hooks'] == raw['hooks']
    assert not integration_status(provider, path)['installed']


def test_bad_existing_hooks_are_never_overwritten(tmp_path):
    p = tmp_path/'config.json'
    original = '{"hooks":{"events":[]}}'
    p.write_text(original, encoding='utf-8')
    with pytest.raises(ValueError): configure_integration('zcode', path=p)
    assert p.read_text(encoding='utf-8') == original


def test_installer_uses_argv_for_zcode_and_quotes_paths(tmp_path, monkeypatch):
    from sentinel import integrations
    monkeypatch.setattr(integrations, 'command_args', lambda provider:
                        ['C:/路径 with space/sentinel.exe', 'hook', '--provider', provider])
    p = tmp_path/'config.json'
    configure_integration('zcode', path=p)
    h=json.loads(p.read_text(encoding='utf-8'))['hooks']['events']['PermissionRequest'][0]['hooks'][0]
    assert h['command'] == 'C:/路径 with space/sentinel.exe'
    assert h['args'] == ['hook', '--provider', 'zcode']
    assert 'permissionDecision' not in h


@pytest.mark.parametrize('kind,status', [('UserPromptSubmit','running'),('PermissionRequest','waiting'),
                                       ('PostToolUseFailure','running'),('Stop','unknown')])
def test_zcode_real_hook_semantics_do_not_guess_final_success(kind, status):
    event = hook_event({'session_id':'s','hook_event_name':kind,'prompt':'SECRET',
                       'tool_response':'SECRET','cwd':'E:/项目'},'zcode',now=100)
    assert event['id'] == 'zcode:s:current' and event['status'] == status
    assert 'SECRET' not in json.dumps(event)


@pytest.mark.parametrize('ending,status', [('completed','success'),('error','error'),('aborted','cancelled'),('new-status',None)])
def test_cursor_stop_distinguishes_cancel_error_and_completion(ending, status):
    event = hook_event({'conversation_id':'c','generation_id':'g','hook_event_name':'stop','status':ending},'cursor')
    assert (event['status'] if event else None) == status
    if event: assert event['id'] == 'cursor:c:g'


def test_before_permission_tool_is_not_a_permission_request():
    assert hook_event({'conversation_id':'c','hook_event_name':'preToolUse','tool_name':'Shell'},'cursor') is None
    assert hook_event({'session_id':'s','hook_event_name':'PostToolUse','agent_id':'child'},'zcode') is None


def test_windsurf_complete_never_saves_response_or_prompt():
    event = hook_event({'trajectory_id':'t','agent_action_name':'post_cascade_response',
                        'tool_info':{'response':'private response'}},'windsurf')
    assert event['status'] == 'success' and 'private response' not in json.dumps(event)


def test_hook_failure_keeps_original_client_running(tmp_path, monkeypatch, capsys):
    from sentinel.cli import main, Client
    monkeypatch.setenv('AI_SENTINEL_HOME', str(tmp_path))
    def unavailable(*args):raise ConnectionError('offline')
    monkeypatch.setattr(Client, 'request', unavailable)
    code=main(['hook','--provider','cursor',json.dumps({'hook_event_name':'beforeSubmitPrompt','conversation_id':'s'})])
    captured = capsys.readouterr()
    assert code == 0 and json.loads(captured.out) == {'continue': True}
    assert '暂不可用' in captured.err


def test_missing_session_is_not_combined_with_other_users():
    assert hook_event({'hook_event_name':'PermissionRequest'},'zcode') is None
