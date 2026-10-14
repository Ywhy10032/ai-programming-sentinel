from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__
from .config import Config, resource_path
from .runner import run_command
from .providers import PROVIDERS


class Client:
    def __init__(self, config=None):
        self.config = config or Config()
        self.base = f"http://127.0.0.1:{self.config.get('http_port')}"

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload,ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base+path,data=data,
            headers={"X-Sentinel-Token":self.config.get("token"),"Content-Type":"application/json"})
        with urllib.request.urlopen(request,timeout=4) as response:
            return json.load(response)

    def running(self):
        try:
            return self.request("/api/status")
        except (OSError,ValueError):
            return None

    def ensure(self):
        if self.running():
            return
        if getattr(sys,"frozen",False):
            executable = Path(sys.executable).with_name("AI编程哨兵.exe")
            if not executable.exists():
                executable = Path(sys.executable)
            command = [str(executable),"serve","--no-browser"]
        else:
            executable = Path(sys.executable)
            if os.name=="nt" and executable.with_name("pythonw.exe").exists():
                executable = executable.with_name("pythonw.exe")
            command = [str(executable),str(resource_path("main.py")),"serve","--no-browser"]
        subprocess.Popen(command,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0,
                         stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         cwd=str(resource_path(".")),start_new_session=os.name!="nt")
        deadline = time.monotonic()+12
        while time.monotonic()<deadline:
            if self.running():
                return
            time.sleep(.2)
        raise ConnectionError("哨兵后台未启动，请双击 AI编程哨兵.exe 查看连接状态")

    def emit(self,event):
        self.ensure()
        return self.request("/api/event",event)


def print_json(value):
    if sys.stdout:
        print(json.dumps(value,ensure_ascii=False,indent=2))


def hook_event(payload: dict, provider="codex"):
    if provider == "claude":
        from .claude import hook_event as claude_event
        return claude_event(payload)
    if provider != 'codex':
        from .integrations import hook_event as client_event
        return client_event(payload, provider)
    kind = payload.get("hook_event_name",payload.get("type", ""))
    mapping = {"agent-turn-complete":"success","UserPromptSubmit":"running","Stop":"success",
               "Interrupt":"cancelled","approval-requested":"waiting","request-user-input":"waiting",
               "task-started":"running","task-failed":"error"}
    if kind not in mapping:
        return None
    session = payload.get("session_id",payload.get("thread-id",payload.get("thread_id","external")))
    turn = payload.get("turn_id",payload.get("turn-id", "current"))
    task_id = f"codex:{session}:{turn}"
    cwd = payload.get("cwd", "")
    return {"id":task_id,"status":mapping[kind],"title":Path(cwd).name or "AI 任务",
            "source":"Codex 生命周期事件","provider":"codex","mode":"ai",
            "detail":"请在原工具中处理" if mapping[kind]=="waiting" else "本轮任务已结束" if mapping[kind]=="success" else "",
            "event_id":f"{task_id}:task_complete" if mapping[kind]=="success" else f"{task_id}:{kind}"}


def main(argv=None):
    if sys.stdout and hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    if sys.stderr and hasattr(sys.stderr,"reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8",errors="replace")
    if sys.stdin and hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description="AI 编程哨兵 · USB 小屏任务提醒")
    parser.add_argument("--version",action="version",version=__version__)
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve",help="启动后台和控制面板")
    serve.add_argument("--no-browser",action="store_true")
    serve.add_argument("--no-tray",action="store_true")
    serve.add_argument("--preview-only",action="store_true")
    sub.add_parser("panel",help="打开控制面板")
    sub.add_parser("status",help="查询当前状态")
    sub.add_parser("stop",help="退出后台并释放 USB")
    ack = sub.add_parser("ack",help="确认已结束提醒")
    ack.add_argument("--id")
    sub.add_parser("demo",help="15 秒演示，演示不写入真实任务记录")
    emit = sub.add_parser("emit",help="发送真实任务事件")
    emit.add_argument("status",choices=["running","waiting","success","error","cancelled","unknown"])
    emit.add_argument("--id",default="manual:default")
    emit.add_argument("--title",default="AI 任务")
    emit.add_argument("--mode",choices=["ai","build","test"],default="ai")
    emit.add_argument("--detail",default="")
    emit.add_argument("--location",default="")
    emit.add_argument("--errors",type=int)
    emit.add_argument("--files",type=int)
    emit.add_argument("--provider",choices=list(PROVIDERS),default="")
    run = sub.add_parser("run",help="执行编译/测试命令，保留原始退出码")
    run.add_argument("--title",default="编译任务")
    run.add_argument("--mode",choices=["build","test","ai"],default="build")
    run.add_argument("--cwd")
    run.add_argument("args",nargs=argparse.REMAINDER)
    hook = sub.add_parser("hook",help="接收 Codex / 外部工具 JSON 生命周期事件")
    hook.add_argument("payload",nargs="?")
    hook.add_argument("--provider",choices=list(PROVIDERS),default="codex")
    integrate = sub.add_parser("integrate",help="接入 Claude Code 官方回调，保留原有配置")
    integrate.add_argument("tool", choices=['claude', 'zcode', 'cursor', 'windsurf'])
    integrate.add_argument("--remove",action="store_true",help="仅移除哨兵回调")
    startup = sub.add_parser("startup",help="设置或查询当前用户的开机自启")
    startup.add_argument("action",choices=["on", "off", "status"],nargs="?",default="status")
    doctor = sub.add_parser("doctor",help="检查设备和后台")
    doctor.add_argument("--probe",action="store_true")
    options = parser.parse_args(argv)
    client = Client()
    try:
        if options.command in [None,"serve"]:
            if client.running():
                if not getattr(options,"no_browser",False):
                    webbrowser.open(client.base)
                return 0
            from .app import Application
            app = Application(client.config,preview_only=getattr(options,"preview_only",False))
            app.serve(browser=not getattr(options,"no_browser",False),tray=not getattr(options,"no_tray",False))
        elif options.command=="panel":
            client.ensure()
            webbrowser.open(client.base)
        elif options.command=="status":
            state = client.running()
            if state is None:
                print_json({"running":False,"message":"哨兵未启动"})
                return 1
            print_json(state)
        elif options.command=="stop":
            if client.running():
                print_json(client.request("/api/shutdown",{}))
        elif options.command=="emit":
            event = {"id":options.id,"status":options.status,"title":options.title,"mode":options.mode,
                     "detail":options.detail,"location":options.location,"source":"命令行事件","provider":options.provider}
            for key in ["errors","files"]:
                if getattr(options,key) is not None:
                    event[key]=getattr(options,key)
            print_json(client.emit(event))
        elif options.command=="run":
            args = options.args[1:] if options.args[:1]==["--"] else options.args
            if not args:
                parser.error("run 后需要 -- 和要执行的命令")
            # 提醒服务故障不阻止真实编译，不覆盖原命令退出码。
            def emit_safely(event):
                try:
                    client.emit(event)
                except (OSError,ValueError) as exc:
                    if sys.stderr:
                        print(f"哨兵提醒暂不可用：{exc}",file=sys.stderr)
            return run_command(args,emit_safely,client.config.directory/"logs",options.title,options.mode,options.cwd)
        elif options.command=="hook":
            payload = {}
            try:
                payload = json.loads(options.payload if options.payload is not None else sys.stdin.read(2*1024*1024))
                if not isinstance(payload,dict):
                    raise ValueError('回调需要 JSON 对象')
                event = hook_event(payload,options.provider)
                if event:
                    # 回调不启动第二个后台；提醒故障不能阻挡原客户端。
                    client.request('/api/event', event)
            except (OSError, ValueError, TypeError):
                if sys.stderr:
                    print('哨兵提醒暂不可用，原任务继续。', file=sys.stderr)
            # 只确认用户提交继续；不订阅 Cursor 工具前权限 Hook，不批准任何命令。
            print_json({'continue': True} if options.provider == 'cursor' and
                       isinstance(payload, dict) and payload.get('hook_event_name') == 'beforeSubmitPrompt' else {})
        elif options.command=="integrate":
            if options.tool == 'claude':
                from .claude import configure_integration
                print_json(configure_integration(client.config,remove=options.remove))
            else:
                from .integrations import configure_integration
                print_json(configure_integration(options.tool,remove=options.remove))
        elif options.command=="startup":
            client.ensure()
            if options.action == "status":
                print_json(client.request("/api/status")["startup_state"])
            else:
                print_json(client.request("/api/startup", {"enabled": options.action == "on"}))
        elif options.command=="ack":
            print_json(client.request("/api/ack",{"id":options.id}))
        elif options.command=="demo":
            client.ensure()
            print_json(client.request("/api/demo",{}))
        elif options.command=="doctor":
            from .device import Screen
            state = client.running()
            result = {"ports":Screen.available(),"backend_running":state is not None,
                      "device":state.get("device") if state else None,"data_dir":str(client.config.directory)}
            if options.probe:
                if state is not None:
                    result["probe"] = "后台已拥有 USB，请先执行 stop 后再独立探测"
                else:
                    screen = Screen(client.config.get("port"))
                    try:
                        screen.connect()
                        result["probe"]={"port":screen.port,"identity":screen.identity,"acks":screen.acks}
                    finally:
                        screen.close()
            print_json(result)
        return 0
    except (OSError,ValueError,urllib.error.HTTPError) as exc:
        if sys.stderr:
            print(f"哨兵：{exc}",file=sys.stderr)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(exc),"AI 编程哨兵",0x10)
        return 1
