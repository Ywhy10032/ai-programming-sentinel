import threading

import pytest

from sentinel.config import Config
from sentinel.desktop import DesktopWatcher
from sentinel.state import StateStore
from sentinel.providers import PROVIDERS
from sentinel.render import Renderer


@pytest.fixture
def observer(tmp_path):
    now=[100.0]
    store=StateStore(tmp_path/'state', clock=lambda:now[0])
    w=DesktopWatcher(store,Config(tmp_path/'config'),threading.Event(),clock=lambda:now[0])
    return w,store,now


def sample(**extra):
    return dict(provider='cherry',window='42',scope='document-1',visible=True,readable=True,**extra)


def settle(w, row):
    w.consume([row]);w.consume([row])


def test_opening_client_is_not_a_running_task(observer):
    w,s,_=observer
    settle(w,sample(ready=True))
    assert not s.tasks and w.snapshot()['clients'][8]['detected']


def test_generation_needs_stable_evidence_and_clock_is_observation_time(observer):
    w,s,now=observer
    w.consume([sample(running=True)])
    assert not s.tasks
    now[0]=102
    w.consume([sample(running=True)])
    t=s.snapshot()['task']
    assert t['status']=='running' and t['provider']=='cherry' and t['started_at']==102


def test_disappearing_stop_or_restored_send_does_not_mean_success(observer):
    w,s,now=observer
    settle(w,sample(running=True));now[0]+=5
    settle(w,sample(ready=True))
    assert s.snapshot()['task']['status']=='unknown'
    now[0]+=31;w.consume([sample(ready=True)])
    assert s.snapshot()['task'] is None


def test_clear_permission_controls_and_resume(observer):
    w,s,_=observer
    settle(w,sample(waiting=True))
    assert s.snapshot()['task']['status']=='waiting'
    settle(w,sample(running=True))
    assert s.snapshot()['task']['status']=='running'


def test_scope_switch_and_window_close_never_complete_other_thread(observer):
    w,s,_=observer
    settle(w,sample(running=True))
    w.consume([dict(sample(ready=True),scope='another-document')])
    assert s.snapshot()['task'] is None
    settle(w,sample(running=True));w.consume([])
    assert s.snapshot()['task'] is None
    assert not any(t.status=='success' for t in s.tasks.values())


def test_minimized_or_unreadable_window_degrades_instead_of_freezing_timer(observer):
    w,s,_=observer
    settle(w,sample(running=True))
    settle(w,dict(sample(),visible=False,readable=False))
    assert s.snapshot()['task']['label']=='状态不可见'


def test_native_event_has_priority_and_no_duplicate_task(observer):
    w,s,_=observer
    s.event({'id':'cherry:real','provider':'cherry','status':'running'})
    settle(w,sample(running=True))
    assert len(s.tasks)==1 and s.snapshot()['active_count']==1


def test_success_requires_explicit_completion_and_prior_activity(observer):
    w,s,_=observer
    settle(w,sample(complete=True))
    assert not s.tasks
    settle(w,sample(running=True));settle(w,sample(complete=True))
    assert s.snapshot()['task']['status']=='success'


@pytest.mark.parametrize('provider',list(PROVIDERS))
def test_each_client_has_a_renderable_identity(tmp_path,provider):
    s=StateStore(tmp_path)
    s.event({'id':'real:'+provider,'provider':provider,'status':'running','title':'项目'})
    image=Renderer().render(s.snapshot(), {'reduced_motion':True}, 1000)
    assert image.size==(160,80)
