import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from sentinel.app import Application
from sentinel.config import Config


@pytest.fixture
def server(tmp_path):
    config=Config(tmp_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    config.update({"http_port":port,"sound":False,"codex_watch":False,"desktop_watch":False,"deepseek_watch":False})
    app=Application(config,preview_only=True)
    thread=threading.Thread(target=lambda:app.serve(browser=False,tray=False))
    thread.start()
    deadline=time.monotonic()+3
    while app.server is None and time.monotonic()<deadline:time.sleep(.02)
    yield app
    app.shutdown()
    thread.join(timeout=5)
    assert not thread.is_alive()


def request(app,path,body=None,headers=None):
    req=urllib.request.Request(app.url.rstrip('/')+path,data=json.dumps(body).encode() if body is not None else None,
        headers=headers or {})
    return urllib.request.urlopen(req,timeout=3)


def token(app):return {"X-Sentinel-Token":app.config.get("token")}


def test_panel_and_real_preview_available(server):
    with request(server,'/') as r:assert 'AI 编程哨兵' in r.read().decode('utf-8')
    deadline=time.monotonic()+2
    while not server.preview and time.monotonic()<deadline:time.sleep(.02)
    with request(server,'/api/preview.png') as r:assert r.read().startswith(b'\x89PNG')


def test_status_needs_token_and_does_not_expose_token(server):
    with pytest.raises(urllib.error.HTTPError) as e:request(server,'/api/status')
    assert e.value.code==403
    with request(server,'/api/status',headers=token(server)) as r:status=json.load(r)
    assert 'token' not in status['settings']
    assert status['device']['connected'] is False


def test_csrf_and_dns_rebinding_rejected(server):
    headers=token(server)|{'Origin':'https://malicious.example'}
    with pytest.raises(urllib.error.HTTPError) as e:request(server,'/api/demo',{},headers)
    assert e.value.code==403
    headers=token(server)|{'Host':'malicious.example'}
    with pytest.raises(urllib.error.HTTPError) as e:request(server,'/api/status',headers=headers)
    assert e.value.code==403


def test_invalid_event_and_setting_cannot_mutate(server):
    for path,payload in [('/api/event',{'id':'a','status':'madeup'}),('/api/settings',{'fps':100})]:
        with pytest.raises(urllib.error.HTTPError) as e:request(server,path,payload,token(server))
        assert e.value.code==400
    assert not server.store.tasks and server.config.get('fps')==8


def test_demo_is_separate_and_real_event_stops_demo(server):
    with request(server,'/api/demo',{},token(server)):pass
    assert server.display_snapshot(time.time())['demo'] is True
    assert not server.store.tasks
    with request(server,'/api/event',{'id':'actual','status':'running','title':'真实任务'},token(server)):pass
    assert server.demo_started is None
    assert server.status()['task']['title']=='真实任务'


def test_api_ack_keeps_running_tasks(server):
    for task in [{'id':'running','status':'running'},{'id':'error','status':'error'}]:
        with request(server,'/api/event',task,token(server)):pass
    assert server.status()['task']['status']=='error'
    with request(server,'/api/ack',{},token(server)):pass
    assert server.status()['task']['id']=='running'


def test_api_current_origin_accepted(server):
    with request(server,'/api/settings',{'quiet':True},token(server)|{'Origin':server.url.rstrip('/')}):pass
    assert server.config.get('quiet') is True


def test_real_watcher_event_also_ends_demo(server):
    server.demo_started=time.time()
    server.store.event({'id':'codex:real','status':'running','source':'Codex 自动监听'})
    assert server.demo_started is None


def test_claude_hook_resumes_waiting_and_never_returns_decisions(server):
    for kind in ('UserPromptSubmit', 'PermissionRequest', 'PostToolUse', 'Stop'):
        body = {'session_id': 'real', 'cwd': 'E:/项目', 'hook_event_name': kind}
        with request(server, '/api/hooks/claude', body, token(server)) as response:
            assert json.load(response) == {}
        assert server.status()['task']['provider'] == 'claude'
    assert server.status()['task']['status'] == 'success'
    with request(server, '/api/hooks/claude', dict(body, hook_event_name='SessionEnd'), token(server)):pass
    assert server.status()['task']['status'] == 'success'


def test_claude_api_failure_remains_error_after_session_exit(server):
    base = {'session_id': 'fail', 'hook_event_name': 'StopFailure', 'error': 'rate_limit'}
    with request(server, '/api/hooks/claude', base, token(server)):pass
    with request(server, '/api/hooks/claude', dict(base, hook_event_name='SessionEnd'), token(server)):pass
    assert server.status()['task']['status'] == 'error'


def test_claude_hook_large_tool_output_is_not_saved(server):
    body = {'session_id': 'large', 'hook_event_name': 'PostToolUse', 'tool_response': 'PRIVATE' * 20000}
    with request(server, '/api/hooks/claude', body, token(server)) as response:
        assert json.load(response) == {}
    assert 'PRIVATE' not in json.dumps(server.status())


def test_disable_claude_removes_active_task_and_ignores_future_hooks(server):
    body = {'session_id': 'pause', 'hook_event_name': 'UserPromptSubmit'}
    with request(server, '/api/hooks/claude', body, token(server)):pass
    with request(server, '/api/settings', {'claude_watch': False}, token(server)):pass
    with request(server, '/api/hooks/claude', body, token(server)):pass
    assert server.status()['active_count'] == 0


def test_claude_integration_api_is_reversible_and_preserves_other_hooks(server, tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude'))
    with request(server, '/api/integrations/claude', {}, token(server)) as response:
        assert json.load(response)['installed']
    assert server.status()['claude']['installed']
    with request(server, '/api/integrations/claude', {'remove': True}, token(server)):pass
    assert not server.status()['claude']['installed']


def test_brand_icon_routes_are_available_without_arbitrary_file_access(server):
    for provider in ('codex', 'claude', 'deepseek', 'zcode', 'cherry', 'chatgpt'):
        with request(server, f'/api/icons/{provider}.png') as response:
            assert response.read().startswith(b'\x89PNG')
    with pytest.raises(urllib.error.HTTPError):
        request(server, '/api/icons/config.json')


def test_desktop_directory_is_honest_and_does_not_expose_private_controls(server):
    with request(server,'/api/status',headers=token(server)) as response:
        state=json.load(response)
    assert not state['desktop']['enabled']
    from sentinel.providers import PROVIDERS
    assert len(state['desktop']['clients']) == len(PROVIDERS)
    assert all(not c['detected'] for c in state['desktop']['clients'])
    assert all('window_title' not in c and 'controls' not in c for c in state['desktop']['clients'])


def test_new_hook_endpoint_uses_token_and_does_not_return_permission_decision(server):
    payload={'hook_event_name':'PermissionRequest','session_id':'s','prompt':'private'}
    with pytest.raises(urllib.error.HTTPError):request(server,'/api/hooks/zcode',payload)
    with request(server,'/api/hooks/zcode',payload,token(server)) as response:
        assert json.load(response)=={}
    assert server.store.tasks['zcode:s:current'].status=='waiting'
    assert 'private' not in json.dumps(server.store.snapshot())


def test_second_backend_cannot_take_over_occupied_port(server, tmp_path):
    other_config = Config(tmp_path / 'other')
    other_config.update({'http_port': server.config.get('http_port'), 'sound': False, 'codex_watch': False})
    other = Application(other_config, preview_only=True)
    try:
        with pytest.raises(OSError):
            other.serve(browser=False, tray=False)
        with request(server, '/api/status', headers=token(server)) as response:
            from sentinel import __version__
            assert json.load(response)['version'] == __version__
    finally:
        import logging
        logging.getLogger().removeHandler(other.log_handler)
        other.log_handler.close()


def test_startup_api_exposes_result_and_rejects_wrong_type(server, monkeypatch):
    calls = []
    def set_startup(enabled):
        calls.append(enabled)
        return {'enabled': enabled, 'supported': True, 'registered': enabled,
                'needs_repair': False, 'message': '状态已更新'}
    monkeypatch.setattr(server, 'set_startup', set_startup)
    with request(server, '/api/startup', {'enabled': True}, token(server)) as response:
        assert json.load(response)['startup']['enabled']
    with request(server, '/api/startup', {'enabled': False}, token(server)) as response:
        assert not json.load(response)['startup']['enabled']
    with pytest.raises(urllib.error.HTTPError) as error:
        request(server, '/api/startup', {'enabled': 'yes'}, token(server))
    assert error.value.code == 400 and calls == [True, False]
