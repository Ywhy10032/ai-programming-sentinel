import json

from sentinel.config import Config


def test_existing_instance_refreshes_rotated_token_without_overwriting_preferences(tmp_path):
    first=Config(tmp_path)
    disk=json.loads(first.path.read_text(encoding='utf-8'))
    disk.update(token='b'*43,sound=False)
    first.path.write_text(json.dumps(disk),encoding='utf-8')
    assert first.refresh_token()
    assert first.get('token')=='b'*43
    assert first.get('sound') is True
    assert json.loads(first.path.read_text(encoding='utf-8'))['sound'] is False
    assert not first.refresh_token()


def test_invalid_refresh_does_not_rewrite_configuration_or_destroy_loaded_token(tmp_path):
    config=Config(tmp_path);before=config.get('token')
    for body in ['broken','[]','{"token":"短"}','{"token":"'+('你'*40)+'"}']:
        config.path.write_text(body,encoding='utf-8')
        assert not config.refresh_token()
        assert config.get('token')==before
        assert config.path.read_text(encoding='utf-8')==body


def test_save_preserves_new_disk_token_and_uses_no_shared_temporary_file(tmp_path):
    first=Config(tmp_path)
    disk=json.loads(first.path.read_text(encoding='utf-8'));disk['token']='c'*43
    first.path.write_text(json.dumps(disk),encoding='utf-8')
    first.update({'sound':False})
    assert json.loads(first.path.read_text(encoding='utf-8'))['token']=='c'*43
    assert not list(tmp_path.glob('*.tmp'))
