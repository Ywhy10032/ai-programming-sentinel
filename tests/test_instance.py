import subprocess
import sys

from sentinel.instance import InstanceGuard


def test_excludes_second_process_and_releases_after_exit(tmp_path):
    program = ('from pathlib import Path; from sentinel.instance import InstanceGuard; '
               'import sys; '
               '\nwith InstanceGuard(Path(sys.argv[1])) as guard: print(int(guard.acquired))')
    def child():
        return subprocess.check_output([sys.executable,'-c',program,str(tmp_path)],text=True).strip()
    with InstanceGuard(tmp_path) as guard:
        assert guard.acquired
        assert child() == '0'
        with InstanceGuard(tmp_path/'other') as other:
            assert other.acquired
    assert child() == '1'


def test_releases_after_exception(tmp_path):
    try:
        with InstanceGuard(tmp_path) as guard:
            assert guard.acquired
            raise RuntimeError('expected')
    except RuntimeError:
        pass
    with InstanceGuard(tmp_path) as guard:
        assert guard.acquired
