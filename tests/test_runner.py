import sys
from sentinel.runner import run_command,decode_output


def test_success_exit_and_real_elapsed(tmp_path):
    events=[]
    code=run_command([sys.executable,"-c","print('完成')"],events.append,tmp_path,title="中文项目")
    assert code==0
    assert [x["status"] for x in events]==["running","success"]
    assert next(tmp_path.glob("*.log")).read_text(encoding="utf-8").strip()=="完成"


def test_failure_preserves_exit_code_and_error_location(tmp_path):
    events=[]
    code=run_command([sys.executable,"-c","import sys;print('main.c:128: fatal error: missing.h: No such file or directory');sys.exit(7)"],events.append,tmp_path)
    assert code==7
    assert events[-1]["status"]=="error"
    assert events[-1]["location"]=="main.c:128"
    assert events[-1]["detail"]=="缺少文件或头文件"


def test_missing_executable_emits_error(tmp_path):
    events=[]
    code=run_command([str(tmp_path/"不存在的程序")],events.append,tmp_path)
    assert code==127
    assert events[-1]["status"]=="error"


def test_legacy_chinese_output(tmp_path):
    events=[]
    code=run_command([sys.executable,"-c","import sys;sys.stdout.buffer.write('中文错误'.encode('gbk'));sys.exit(2)"],events.append,tmp_path)
    assert code==2
    assert next(tmp_path.glob("*.log")).read_text(encoding="utf-8")=="中文错误"


def test_decoder_gbk():
    assert decode_output("中文".encode("gbk"))=="中文"


def test_chinese_decoder_is_independent_of_english_windows_locale(monkeypatch):
    monkeypatch.setattr('sentinel.runner.locale.getpreferredencoding', lambda _=False: 'cp1252')
    assert decode_output('中文错误'.encode('gbk')) == '中文错误'
    assert decode_output(b'caf\xe9') == 'café'


def test_python_child_can_print_unicode_without_inheriting_legacy_console_encoding(tmp_path, monkeypatch):
    monkeypatch.delenv('PYTHONIOENCODING', raising=False)
    monkeypatch.delenv('PYTHONUTF8', raising=False)
    events = []
    code = run_command([sys.executable, '-c', "print('🟢 完成')"], events.append, tmp_path)
    assert code == 0
    assert next(tmp_path.glob('*.log')).read_text(encoding='utf-8').strip() == '🟢 完成'


def test_long_output_preserves_first_error_without_inventing_total(tmp_path):
    events=[]
    script="import sys;print('main.c:128: error: value undeclared');sys.stdout.write('normal output\\n'*400000);sys.exit(1)"
    code=run_command([sys.executable,'-c',script],events.append,tmp_path)
    assert code==1
    assert events[-1]['location']=='main.c:128'
    assert events[-1]['detail']=='变量或名称未声明'
    assert 'errors' not in events[-1]
