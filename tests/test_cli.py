import pytest
from sentinel.cli import hook_event


def test_official_completion_hook_matches_watcher_task_id():
    event=hook_event({'type':'agent-turn-complete','thread-id':'s','turn-id':'t','cwd':'E:/工程'})
    assert event['id']=='codex:s:t'
    assert event['event_id']=='codex:s:t:task_complete'
    assert event['status']=='success'
    assert event['title']=='工程'


@pytest.mark.parametrize('kind,status',[('UserPromptSubmit','running'),('Stop','success'),('Interrupt','cancelled'),('approval-requested','waiting')])
def test_supported_hook_events(kind,status):
    assert hook_event({'hook_event_name':kind,'session_id':'s','turn_id':'t'})['status']==status


def test_unknown_hook_does_not_invent_state():
    assert hook_event({'type':'something-else'}) is None
