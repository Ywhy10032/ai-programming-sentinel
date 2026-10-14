import json

import pytest

from sentinel.antigravity import (EVENTS, GROUP, configure_integration, hook_event,
                                 hook_output, integration_status, other_stop_hooks)


def payload(**extra):
    return {'conversationId':'conversation-1','workspacePaths':['E:/工作区'],
            'transcriptPath':'PRIVATE LOG','modelName':'PRIVATE MODEL',**extra}


@pytest.mark.parametrize('kind,status', [('PreInvocation','running'),('PreToolUse','running'),('PostToolUse','running')])
def test_activity_is_not_completion(kind,status):
    event=hook_event(payload(toolCall={'name':'run_command','args':{'CommandLine':'PRIVATE COMMAND'}}),kind)
    assert event['status']==status and event['id']=='antigravity:conversation-1:current'
    assert 'PRIVATE' not in json.dumps(event)


@pytest.mark.parametrize('tool', ['ask_question','ask_permission'])
def test_real_interaction_tools_need_operation(tool):
    event=hook_event(payload(toolCall={'name':tool,'args':{'questions':'PRIVATE'}}),'PreToolUse')
    assert event['status']=='waiting' and 'PRIVATE' not in json.dumps(event)


def test_post_tool_failure_does_not_mark_whole_turn_failed():
    event=hook_event(payload(error='PRIVATE ERROR'),'PostToolUse')
    assert event['status']=='running' and 'PRIVATE' not in json.dumps(event)


@pytest.mark.parametrize('idle,status', [(True,'success'),(False,'running'),(None,'unknown'),('true','unknown')])
def test_completion_requires_explicit_fully_idle(idle,status):
    assert hook_event(payload(fullyIdle=idle,terminationReason='model_stop'),'Stop')['status']==status


@pytest.mark.parametrize('reason,status', [('error','error'),('max_steps_exceeded','error'),('cancelled','cancelled'),
                                       ('interrupted','cancelled'),('future-reason','unknown')])
def test_stop_reason_does_not_fabricate_success(reason,status):
    assert hook_event(payload(fullyIdle=True,terminationReason=reason),'Stop')['status']==status


def test_existing_continuation_hook_prevents_false_completion():
    assert hook_event(payload(fullyIdle=True,terminationReason='model_stop'),'Stop',may_continue=True)['status']=='unknown'


def test_no_session_or_child_agent_is_ignored():
    assert hook_event({'fullyIdle':True},'Stop') is None
    assert hook_event(payload(parentConversationId='parent'),'Stop') is None


def test_protocol_outputs_delegate_permissions_without_auto_approval():
    assert hook_output('PreToolUse')=={'decision':'allow'}
    assert hook_output('Stop')=={'decision':'stop'}
    assert hook_output('PreInvocation')=={}
    assert 'auto_approve' not in json.dumps([hook_output(k) for k in EVENTS])


def test_installer_preserves_user_hooks_and_is_reversible(tmp_path):
    path=tmp_path/'hooks.json'
    original={'existing-policy':{'enabled':False,'PreToolUse':[{'matcher':'run_command',
               'hooks':[{'command':'user-check'}]}]},'existing-stop':{'Stop':[{'command':'user-stop'}]}}
    path.write_text(json.dumps(original),encoding='utf-8')
    assert configure_integration(path=path)['enabled']
    raw=json.loads(path.read_text(encoding='utf-8'))
    assert raw['existing-policy']==original['existing-policy'] and raw['existing-stop']==original['existing-stop']
    assert raw[GROUP]['PreInvocation'][0]['command'].endswith('--event PreInvocation')
    assert raw[GROUP]['PreToolUse'][0]['hooks'][0]['type']=='command'
    assert not configure_integration(path=path)['changed']
    assert other_stop_hooks({},path=path)
    configure_integration(remove=True,path=path)
    assert json.loads(path.read_text(encoding='utf-8'))==original
    assert list(tmp_path.glob('hooks.sentinel-backup-*.json'))


def test_only_our_stop_is_not_a_continuation_hook_and_disable_is_reported(tmp_path):
    path=tmp_path/'hooks.json';configure_integration(path=path)
    assert not other_stop_hooks({},path=path)
    raw=json.loads(path.read_text(encoding='utf-8'));raw[GROUP]['enabled']=False
    path.write_text(json.dumps(raw),encoding='utf-8')
    assert integration_status(path)['installed'] and not integration_status(path)['enabled']


def test_workspace_stop_hook_is_seen(tmp_path):
    global_path=tmp_path/'global/hooks.json';configure_integration(path=global_path)
    workspace=tmp_path/'workspace';(workspace/'.agents').mkdir(parents=True)
    (workspace/'.agents/hooks.json').write_text('{"user":{"Stop":[{"command":"check"}]}}',encoding='utf-8')
    assert other_stop_hooks({'workspacePaths':[str(workspace)]},path=global_path)


def test_malformed_existing_settings_are_not_overwritten(tmp_path):
    path=tmp_path/'hooks.json';path.write_text('[1,2]',encoding='utf-8')
    with pytest.raises(ValueError):configure_integration(path=path)
    assert path.read_text(encoding='utf-8')=='[1,2]'


def test_install_does_not_enable_paused_foreign_handler_in_same_group(tmp_path):
    path=tmp_path/'hooks.json'
    original=json.dumps({GROUP:{'enabled':False,'Stop':[{'command':'user-check'}]}})
    path.write_text(original,encoding='utf-8')
    with pytest.raises(ValueError):configure_integration(path=path)
    assert path.read_text(encoding='utf-8')==original


@pytest.mark.parametrize('kind,expected', [('PreToolUse',{'decision':'allow'}),('Stop',{'decision':'stop'})])
def test_offline_sentinel_returns_neutral_hook_protocol(tmp_path,monkeypatch,capsys,kind,expected):
    from sentinel.cli import main,Client
    monkeypatch.setenv('AI_SENTINEL_HOME',str(tmp_path))
    def offline(*args):raise ConnectionError('offline')
    monkeypatch.setattr(Client,'request',offline)
    assert main(['hook','--provider','antigravity','--event',kind,json.dumps(payload())])==0
    assert json.loads(capsys.readouterr().out)==expected
