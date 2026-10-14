from pathlib import Path

import pytest

from sentinel.startup import GUI_NAME, RUN_KEY, VALUE_NAME, StartupManager


class FakeKey:
    def __enter__(self):return self
    def __exit__(self, *args):pass


class FakeRegistry:
    HKEY_CURRENT_USER = 'current-user'
    REG_SZ = 1
    def __init__(self):self.values = {'OtherProgram': ('keep-existing-command', 1)}
    def OpenKey(self, root, path):
        assert root == self.HKEY_CURRENT_USER and path == RUN_KEY
        return FakeKey()
    CreateKey = OpenKey
    def QueryValueEx(self, key, name):
        if name not in self.values:raise FileNotFoundError(name)
        return self.values[name]
    def SetValueEx(self, key, name, reserved, kind, value):self.values[name] = (value, kind)
    def DeleteValue(self, key, name):
        if name not in self.values:raise FileNotFoundError(name)
        del self.values[name]


def manager(tmp_path):
    directory = tmp_path / '带空格的 app 目录'
    directory.mkdir()
    gui = directory / GUI_NAME
    gui.write_bytes(b'GUI test fixture')
    registry = FakeRegistry()
    return StartupManager(directory / 'sentinel.exe', registry, windows=True, packaged=True), registry, gui


def test_enable_registers_quoted_gui_and_background_arguments_and_disable_preserves_others(tmp_path):
    startup, registry, gui = manager(tmp_path)
    assert not startup.state()['enabled']
    assert startup.set(True)['enabled']
    assert registry.values[VALUE_NAME] == (f'"{gui}" serve --no-browser', registry.REG_SZ)
    assert startup.set(False)['enabled'] is False
    assert VALUE_NAME not in registry.values
    assert registry.values['OtherProgram'][0] == 'keep-existing-command'
    startup.set(False)


def test_upgrade_repairs_only_existing_opt_in_and_does_not_enable_by_default(tmp_path):
    startup, registry, gui = manager(tmp_path)
    startup.repair_after_upgrade()
    assert VALUE_NAME not in registry.values
    registry.values[VALUE_NAME] = ('"C:/old/app/AI编程哨兵.exe" serve --no-browser', 1)
    assert startup.state()['needs_repair'] and not startup.state()['enabled']
    assert startup.repair_after_upgrade()['enabled']
    assert str(gui) in registry.values[VALUE_NAME][0]
    assert registry.values['OtherProgram'][0] == 'keep-existing-command'


def test_missing_gui_cannot_enable_but_can_remove_old_registration(tmp_path):
    startup, registry, gui = manager(tmp_path)
    gui.unlink()
    assert not startup.state()['supported']
    with pytest.raises(ValueError, match='完整解压'):
        startup.set(True)
    registry.values[VALUE_NAME] = ('old', 1)
    startup.set(False)
    assert VALUE_NAME not in registry.values


@pytest.mark.parametrize('windows,packaged', [(False, True), (True, False), (False, False)])
def test_unsupported_platform_or_source_cannot_write_startup(tmp_path, windows, packaged):
    registry = FakeRegistry()
    startup = StartupManager(tmp_path / GUI_NAME, registry, windows=windows, packaged=packaged)
    assert not startup.state()['supported']
    with pytest.raises(ValueError, match='便携版'):
        startup.set(True)
    assert VALUE_NAME not in registry.values


def test_invalid_boolean_is_rejected(tmp_path):
    startup, registry, gui = manager(tmp_path)
    with pytest.raises(ValueError):startup.set('on')
    assert VALUE_NAME not in registry.values
