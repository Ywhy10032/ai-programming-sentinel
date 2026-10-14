import pytest
from sentinel.diagnostics import summarize,is_build_command


@pytest.mark.parametrize("log,summary,location",[
    ("main.c:128:5: fatal error: missing.h: No such file or directory","缺少文件或头文件","main.c:128"),
    (r"C:\My Project\main.c(128,5): error C1083: Cannot open include file: 'x.h'","缺少文件或头文件","main.c:128"),
    ("main.c:20: undefined reference to 'hello'","函数或符号未定义","main.c:20"),
    ('Traceback (most recent call last):\n  File "C:\\src\\main.py", line 12, in run\nModuleNotFoundError: No module named x',"缺少 Python 依赖","main.py:12"),
    ("Error[Pe1696]: cannot open source file main.h","缺少文件或头文件",""),
    ("FAILED tests/test_math.py::test_math - AssertionError: x\n1 failed in 0.5s","测试未通过",""),
])
def test_real_tool_formats(log,summary,location):
    result=summarize(log,1)
    assert result.summary==summary
    assert result.location==location


def test_error_counts_not_invented():
    assert summarize("command failed",1).errors is None
    assert summarize("2 errors generated.",1).errors==2
    assert summarize("all passed",0).errors==0


def test_ansi_removed():
    d=summarize("\x1b[31mmain.c:12: error: x undeclared\x1b[0m",1)
    assert d.location=="main.c:12"
    assert d.summary=="变量或名称未声明"


@pytest.mark.parametrize("command,mode",[
    ("cmake --build build","build"),("python -m pytest -q","test"),
    ("cargo test","test"),("arm-none-eabi-gcc -c main.c","build"),
    ("IarBuild.exe project.ewp -build Debug","build"),("dotnet build","build"),
    ("pip install Pillow",None),("echo build",None),("echo cmake --build build",None),
    ('python -c "print(\'pytest\')"',None),
])
def test_command_classification(command,mode):
    assert is_build_command(command)==mode
