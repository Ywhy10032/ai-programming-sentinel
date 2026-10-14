"""编译只读桌面观察器；用户安装应用及系统设置保持不变。"""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(destination=None):
    if os.name != 'nt':
        raise OSError('桌面观察器需要 Windows')
    runtime = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319'
    destination = destination or ROOT/'tmp/desktop-observer/observer.exe'
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [str(runtime/'csc.exe'), '/nologo', '/target:exe', '/optimize+', '/platform:x64',
               '/out:'+str(destination), '/reference:'+str(runtime/'WPF/UIAutomationClient.dll'),
               '/reference:'+str(runtime/'WPF/UIAutomationTypes.dll'), '/reference:'+str(runtime/'WPF/WindowsBase.dll'),
               '/reference:'+str(runtime/'System.Web.Extensions.dll'), '/reference:'+str(runtime/'System.Drawing.dll'),
               str(ROOT/'sentinel/desktop_observer.cs')]
    subprocess.run(command, check=True, capture_output=True)
    return destination


if __name__ == '__main__':
    print(build())
