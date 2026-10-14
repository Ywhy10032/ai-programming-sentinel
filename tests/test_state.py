import json
import pytest
from sentinel.state import StateStore
from sentinel.config import Config


@pytest.fixture
def store(tmp_path):
    now=[1000.0]
    s=StateStore(tmp_path,clock=lambda:now[0])
    s.now=now
    return s


def event(store,status,id="a",**kw):
    return store.event({"id":id,"status":status,**kw})


def test_elapsed_stops_at_completion(store):
    event(store,"running")
    store.now[0]+=92
    event(store,"success")
    store.now[0]+=10
    assert store.snapshot()["task"]["elapsed"]==92


def test_error_stays_until_ack_and_running_survives(store):
    event(store,"running","ai")
    event(store,"error","build",mode="build",location="main.c:128")
    store.now[0]+=10000
    assert store.snapshot()["task"]["id"]=="build"
    store.acknowledge()
    assert store.snapshot()["task"]["id"]=="ai"


def test_waiting_only_from_explicit_event(store):
    event(store,"running")
    store.now[0]+=10000
    assert store.snapshot()["task"]["status"]=="running"
    event(store,"waiting")
    assert store.snapshot()["task"]["status"]=="waiting"


def test_new_success_temporarily_visible_then_running(store):
    event(store,"running","ai")
    event(store,"success","build")
    assert store.snapshot()["task"]["id"]=="build"
    store.now[0]+=7
    assert store.snapshot()["task"]["id"]=="ai"


def test_completion_expires_and_repeated_start_resets_time(store):
    event(store,"running")
    store.now[0]+=3
    event(store,"success")
    store.now[0]+=20
    assert store.snapshot()["task"] is None
    event(store,"running")
    assert store.snapshot()["task"]["elapsed"]==0


def test_dedup_notification_and_unknown_file_count(store):
    notifications=[]
    store.on_notify=lambda task:notifications.append(task.status)
    event(store,"success",event_id="one")
    assert event(store,"success",event_id="one") is None
    assert notifications==["success"]
    assert store.snapshot()["task"]["files"] is None


@pytest.mark.parametrize("payload",[
    {"id":"a","status":"made-up"},{"status":"running"},
    {"id":"a","status":"running","errors":-1},
    {"id":"a","status":"running","files":"3"},
    {"id":"a","status":"running","started_at":"invalid"},
    {"id":"a","status":"running","started_at":100000},
])
def test_invalid_events_rejected(store,payload):
    with pytest.raises(ValueError):store.event(payload)
    assert not store.tasks


def test_utf8_history_and_no_stale_running_restore(store,tmp_path):
    event(store,"running",title="中文项目",detail="缺少头文件")
    text=(tmp_path/"history.json").read_text(encoding="utf-8")
    assert "中文项目" in text
    restored=StateStore(tmp_path,clock=lambda:1000)
    assert restored.snapshot()["task"] is None
    assert restored.snapshot()["history"][0]["title"]=="中文项目"


def test_config_invalid_update_atomic_and_no_token_exposure(tmp_path):
    c=Config(tmp_path)
    token=c.get("token")
    with pytest.raises(ValueError):c.update({"sound":False,"rotation":90})
    assert c.get("sound") is True
    assert "token" not in c.public()
    assert Config(tmp_path).get("token")==token


def test_bad_config_preserved(tmp_path):
    (tmp_path/"config.json").write_text("{bad",encoding="utf-8")
    c=Config(tmp_path)
    assert (tmp_path/"config.invalid.json").exists()
    assert c.get("port")=="auto"
