import json
import os
import threading

import pytest
import zstandard

from sentinel.config import Config
from sentinel.deepseek import DeepSeekWatcher, SessionCursor
from sentinel.state import StateStore


def header(**extra):
    return dict(type='session',version=4,id='s',cwd='E:/项目',isSeeded=False,createdAt=100000,**extra)


def event(kind,seq,**data):
    return {'type':kind,'seq':seq,'time':100000+seq*1000,'data':data}


def line(record):return (json.dumps(record,ensure_ascii=False)+'\n').encode('utf-8')


@pytest.fixture
def harness(tmp_path):
    store=StateStore(tmp_path/'state',clock=lambda:150)
    watcher=DeepSeekWatcher(store,Config(tmp_path/'config'),threading.Event(),roots=[tmp_path/'logs'],clock=lambda:150)
    return watcher,store,tmp_path/'logs/p/s/session.v4.jsonl'


@pytest.mark.parametrize('compressed',[False,True])
def test_raw_and_multiple_checked_frames_track_only_new_real_events(harness,compressed):
    w,s,path=harness
    if compressed:path=path.with_name(path.name+'.zstd')
    path.parent.mkdir(parents=True)
    pack=lambda b:zstandard.ZstdCompressor(write_checksum=True).compress(b) if compressed else b
    path.write_bytes(pack(line(header()))+pack(line(event('turn/start',0,turn=1))+line(event('turn/end',1,turn=1,reason={'kind':'completed'}))))
    cursor=SessionCursor(path);w.poll(cursor)
    assert not s.tasks and not s.history
    with path.open('ab') as f:f.write(pack(line(event('turn/start',2,turn=2))))
    w.poll(cursor)
    assert s.snapshot()['task']['status']=='running'
    with path.open('ab') as f:f.write(pack(line(event('turn/end',3,turn=2,reason={'kind':'error','error':{'message':'SECRET'}}))))
    w.poll(cursor)
    assert s.snapshot()['task']['status']=='error'
    assert 'SECRET' not in json.dumps(s.snapshot())


def test_torn_zstd_frame_is_not_an_event_until_checksum_completes(harness):
    w,s,path=harness;path=path.with_name(path.name+'.zstd');path.parent.mkdir(parents=True)
    compressor=zstandard.ZstdCompressor(write_checksum=True)
    path.write_bytes(compressor.compress(line(header())))
    c=SessionCursor(path);w.poll(c)
    frame=compressor.compress(line(event('turn/start',0,turn=1)))
    with path.open('ab') as f:f.write(frame[:-3])
    w.poll(c);assert not s.tasks
    with path.open('ab') as f:f.write(frame[-3:])
    w.poll(c);assert s.snapshot()['task']['status']=='running'


def test_append_without_newline_waits_and_does_not_replay(harness):
    w,s,path=harness;path.parent.mkdir(parents=True)
    path.write_bytes(line(header()))
    c=SessionCursor(path);w.poll(c)
    raw=line(event('turn/start',0,turn=1))
    with path.open('ab') as f:f.write(raw[:-1])
    w.poll(c);assert not s.tasks
    with path.open('ab') as f:f.write(b'\n')
    w.poll(c);assert s.snapshot()['task']['status']=='running'
    n=len(s.history);w.poll(c);assert len(s.history)==n


@pytest.mark.parametrize('reason,status',[('completed','success'),('aborted','cancelled'),('interrupted','cancelled'),
                                       ('error','error'),('blocked','waiting'),('max-tokens','error'),('future',None)])
def test_turn_end_mapping_distinguishes_success_and_failure(harness,reason,status):
    w,s,path=harness;c=SessionCursor(path,initial=False)
    w.event(c,header());w.event(c,event('turn/start',0,turn=1))
    e=w.event(c,event('turn/end',1,turn=1,reason={'kind':reason}))
    assert (e['status'] if e else None)==status


def test_automatic_approval_audit_is_not_a_user_question(harness):
    w,s,path=harness;c=SessionCursor(path,initial=False)
    w.event(c,header());w.event(c,event('turn/start',0,turn=1))
    assert w.event(c,event('approval/asked',1,id='approval')) is None
    assert s.snapshot()['task']['status']=='running'


def test_future_header_and_subagent_are_not_misclassified(harness):
    w,s,path=harness
    for h in [dict(header(),version=99),header(origin='subagent')]:
        c=SessionCursor(path,initial=False)
        w.event(c,h);w.event(c,event('turn/start',0,turn=1))
    assert not s.tasks


def test_sequence_gap_refuses_corrupt_log(harness):
    w,s,path=harness;c=SessionCursor(path,initial=False)
    w.event(c,header())
    with pytest.raises(ValueError):w.event(c,event('turn/start',3,turn=1))
    assert c.ignored


def test_discovery_selects_latest_generation_only(harness):
    w,s,path=harness;path.parent.mkdir(parents=True)
    path.write_bytes(line(header()))
    path.with_name('session.v2.jsonl').write_bytes(line(dict(header(),version=2)))
    os.utime(path,(150,150));os.utime(path.with_name('session.v2.jsonl'),(150,150))
    w.discover()
    assert list(w.cursors)==[path]
