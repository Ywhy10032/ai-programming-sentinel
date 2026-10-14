import datetime as dt
import json
import threading
import time

from sentinel.config import Config
from sentinel.state import StateStore
from sentinel.watch import CodexWatcher,Cursor,timestamp


def setup(tmp_path):
    config=Config(tmp_path/"data")
    store=StateStore(config.directory)
    watcher=CodexWatcher(store,config,threading.Event(),tmp_path/"sessions")
    watcher.root.mkdir()
    return store,watcher


def obj(kind,**extra):
    return {"type":"event_msg","timestamp":dt.datetime.now(dt.timezone.utc).isoformat(),
            "payload":{"type":kind,**extra}}


def test_bootstrap_open_windows_log_ignores_stale_mtime(tmp_path):
    store,watcher=setup(tmp_path)
    path=watcher.root/"test.jsonl"
    started=time.time()-92
    data=[{"type":"session_meta","payload":{"id":"session","cwd":"E:/中文项目"}},
          obj("task_started",turn_id="turn",started_at=int(started)),obj("token_count")]
    path.write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in data)+"\n",encoding="utf-8")
    import os
    os.utime(path,(time.time()-7200,time.time()-7200))
    watcher.bootstrap(path)
    task=store.snapshot()["task"]
    assert task["status"]=="running"
    assert 91<=task["elapsed"]<=94
    assert not store.history


def test_partial_utf8_log_and_completion(tmp_path):
    store,watcher=setup(tmp_path)
    path=watcher.root/"test.jsonl"
    path.write_text(json.dumps({"type":"session_meta","payload":{"id":"session","cwd":"E:/中文项目"}},ensure_ascii=False)+"\n",encoding="utf-8")
    watcher.bootstrap(path)
    data=(json.dumps(obj("task_started",turn_id="turn",started_at=time.time()),ensure_ascii=False)+"\n").encode("utf-8")
    with path.open("ab") as f:f.write(data[:len(data)//2])
    watcher.poll()
    assert not store.tasks
    with path.open("ab") as f:f.write(data[len(data)//2:])
    watcher.poll()
    assert store.snapshot()["task"]["status"]=="running"
    with path.open("ab") as f:f.write((json.dumps(obj("task_complete",turn_id="turn"))+"\n").encode())
    watcher.poll()
    assert store.snapshot()["task"]["status"]=="success"


def test_bootstrap_does_not_replay_old_success(tmp_path):
    store,watcher=setup(tmp_path)
    path=watcher.root/"test.jsonl"
    data=[{"type":"session_meta","payload":{"id":"s","cwd":"E:/test"}},
          obj("task_started",turn_id="t",started_at=time.time()-20),obj("task_complete",turn_id="t")]
    path.write_text("\n".join(json.dumps(x) for x in data)+"\n",encoding="utf-8")
    watcher.bootstrap(path)
    assert not store.tasks


def test_unrelated_subagent_completion_does_not_complete_parent(tmp_path):
    store,w=setup(tmp_path)
    c=Cursor(tmp_path/"x",session_id="s",cwd="E:/test",title="test")
    w.consume(c,obj("task_started",turn_id="parent",started_at=time.time()))
    w.consume(c,obj("task_complete",turn_id="child"))
    assert store.snapshot()["task"]["status"]=="running"


def test_completed_build_error_has_actual_exit_and_log(tmp_path):
    store,w=setup(tmp_path)
    c=Cursor(tmp_path/"x",session_id="s",title="工程")
    w.consume(c,obj("task_started",turn_id="t",started_at=time.time()))
    w.consume(c,obj("item_completed",item={"type":"CommandExecution","id":"build-1","command":"cmake --build build",
        "exit_code":1,"aggregated_output":"main.c:128: error: foo undeclared", "duration":{"secs":2,"nanos":0}}))
    task=store.snapshot()["task"]
    assert task["status"]=="error" and task["location"]=="main.c:128"
    assert task["errors"]==1
    assert task["log_path"]


def test_undelivered_async_question_does_not_fake_waiting(tmp_path):
    store,w=setup(tmp_path)
    c=Cursor(tmp_path/"x",session_id="s")
    w.consume(c,obj("task_started",turn_id="t",started_at=time.time()))
    w.consume(c,{"type":"response_item","payload":{"type":"function_call","name":"request_user_input_async"}})
    assert store.snapshot()["task"]["status"]=="running"
    w.consume(c,obj("approval_requested"))
    assert store.snapshot()["task"]["status"]=="waiting"


def async_call(call_id='ask-1', count=1):
    return {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'request_user_input_async',
            'call_id': call_id, 'arguments': json.dumps({'questions': [{'title': '请确认发布位置'}] * count})}}


def accepted(call_id='ask-1', value=True):
    return {'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': call_id,
            'output': json.dumps({'accepted': value})}}


def user_reply(call_id='ask-1', index=0, role='user'):
    text = '<send_user_message_question_reply>\n' + json.dumps([
        {'questionItemId': json.dumps(['request_user_input_async', call_id, index]), 'answer': '已处理'}
    ]) + '\n</send_user_message_question_reply>'
    return {'type': 'response_item', 'payload': {'type': 'message', 'role': role,
            'content': [{'type': 'input_text', 'text': text}]}}


def test_delivered_async_question_alerts_without_pausing_parent_and_reply_restores_parent(tmp_path):
    store, watcher = setup(tmp_path)
    cursor = Cursor(tmp_path / 'x', session_id='s', title='项目')
    watcher.consume(cursor, obj('task_started', turn_id='t', started_at=time.time()))
    parent = cursor.task_id
    watcher.consume(cursor, async_call())
    assert store.snapshot()['task']['status'] == 'running'
    watcher.consume(cursor, accepted())
    alert = store.snapshot()['task']
    assert alert['status'] == 'waiting' and alert['notification_only']
    assert store.tasks[parent].status == 'running'
    assert store.snapshot()['active_count'] == 1
    watcher.consume(cursor, obj('item_completed', item={'type': 'AgentMessage', 'text': '正在继续'}))
    assert store.snapshot()['task']['status'] == 'waiting'
    watcher.consume(cursor, user_reply())
    assert store.snapshot()['task']['id'] == parent
    assert store.snapshot()['task']['status'] == 'running'
    assert not watcher.pending_inputs
    assert store.tasks[alert['id']].public(time.time())['label'] == '已处理'


def test_failed_or_duplicate_delivery_does_not_create_phantom_or_duplicate_alerts(tmp_path):
    store, watcher = setup(tmp_path)
    cursor = Cursor(tmp_path / 'x', session_id='s')
    watcher.consume(cursor, obj('task_started', turn_id='t', started_at=time.time()))
    watcher.consume(cursor, async_call('failed'))
    watcher.consume(cursor, accepted('failed', False))
    assert store.snapshot()['task']['status'] == 'running'
    watcher.consume(cursor, async_call())
    watcher.consume(cursor, accepted())
    length = len(store.history)
    watcher.consume(cursor, accepted())
    assert len(store.history) == length


def test_multiple_questions_remain_pending_until_all_corresponding_answers_arrive(tmp_path):
    store, watcher = setup(tmp_path)
    cursor = Cursor(tmp_path / 'x', session_id='s')
    watcher.consume(cursor, obj('task_started', turn_id='t', started_at=time.time()))
    watcher.consume(cursor, async_call(count=2))
    watcher.consume(cursor, accepted())
    watcher.consume(cursor, user_reply('other-call'))
    watcher.consume(cursor, user_reply(role='assistant'))
    assert store.snapshot()['task']['status'] == 'waiting'
    watcher.consume(cursor, user_reply(index=0))
    assert store.snapshot()['task']['status'] == 'waiting'
    watcher.consume(cursor, user_reply(index=1))
    assert store.snapshot()['task']['status'] == 'running'


def test_bootstrap_recovers_only_unanswered_current_questions_without_sound_or_history(tmp_path):
    store, watcher = setup(tmp_path)
    path = watcher.root / 'questions.jsonl'
    data = [{'type': 'session_meta', 'payload': {'id': 's', 'cwd': 'E:/项目'}},
            obj('task_started', turn_id='t', started_at=time.time()), async_call('answered'), accepted('answered'),
            user_reply('answered'), async_call('pending'), accepted('pending')]
    path.write_text('\n'.join(json.dumps(row) for row in data) + '\n', encoding='utf-8')
    watcher.bootstrap(path)
    task = store.snapshot()['task']
    assert task['status'] == 'waiting' and task['id'].endswith(':input:pending')
    assert not store.history
    assert set(watcher.pending_inputs) == {'pending'}


def test_parent_abort_closes_question_alert(tmp_path):
    store, watcher = setup(tmp_path)
    cursor = Cursor(tmp_path / 'x', session_id='s')
    watcher.consume(cursor, obj('task_started', turn_id='t', started_at=time.time()))
    watcher.consume(cursor, async_call())
    watcher.consume(cursor, accepted())
    watcher.consume(cursor, obj('turn_aborted', turn_id='t'))
    assert store.snapshot()['task']['status'] == 'cancelled'
    assert not watcher.pending_inputs


def test_manual_request_acknowledgement_does_not_approve_or_end_ai_task(tmp_path):
    store, watcher = setup(tmp_path)
    cursor = Cursor(tmp_path / 'x', session_id='s')
    watcher.consume(cursor, obj('task_started', turn_id='t', started_at=time.time()))
    watcher.consume(cursor, async_call())
    watcher.consume(cursor, accepted())
    store.acknowledge(store.snapshot()['task']['id'])
    assert store.snapshot()['task']['id'] == cursor.task_id
    assert store.snapshot()['task']['status'] == 'running'


def test_numeric_and_iso_timestamps():
    assert timestamp(1000)==1000
    assert timestamp("1970-01-01T00:16:40Z")==1000
