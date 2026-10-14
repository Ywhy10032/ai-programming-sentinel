from sentinel.state import StateStore


def test_pending_operation_alert_survives_restart_and_acknowledgement(tmp_path):
    first = StateStore(tmp_path, clock=lambda: 1000)
    first.event({'id': 'codex:s:t:input:ask', 'status': 'waiting', 'notification_only': True})
    restored = StateStore(tmp_path, clock=lambda: 2000)
    assert restored.snapshot()['task']['status'] == 'waiting'
    restored.acknowledge('codex:s:t:input:ask')
    assert restored.snapshot()['task'] is None
    assert StateStore(tmp_path, clock=lambda: 3000).snapshot()['task'] is None


def test_error_survives_restart_but_running_does_not(tmp_path):
    first=StateStore(tmp_path,clock=lambda:1000)
    first.event({'id':'build','status':'error','title':'构建失败','location':'main.c:128'})
    first.event({'id':'ai','status':'running','title':'AI 任务'})
    restored=StateStore(tmp_path,clock=lambda:2000)
    assert restored.snapshot()['task']['id']=='build'
    assert restored.snapshot()['task']['location']=='main.c:128'
    assert 'ai' not in restored.tasks


def test_acknowledged_error_does_not_return_after_restart(tmp_path):
    first=StateStore(tmp_path,clock=lambda:1000)
    first.event({'id':'build','status':'error'})
    first.acknowledge()
    assert StateStore(tmp_path,clock=lambda:2000).snapshot()['task'] is None


def test_retry_same_task_replaces_pending_failure(tmp_path):
    first=StateStore(tmp_path,clock=lambda:1000)
    first.event({'id':'build','status':'error'})
    first.event({'id':'build','status':'running'})
    assert StateStore(tmp_path,clock=lambda:2000).snapshot()['task'] is None
