from __future__ import annotations

import io
import json
import logging
import logging.handlers
import math
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw

from . import __version__
from .config import Config, resource_path
from .device import Screen
from .render import PALETTE, Renderer
from .state import StateStore
from .startup import StartupManager
from .watch import CodexWatcher
from .claude import (HOOK_PATH, ClaudeInterruptWatcher, configure_integration,
                     hook_event as claude_hook_event, integration_status)

LOG = logging.getLogger(__name__)


class Application:
    def __init__(self, config=None, *, preview_only=False):
        self.config = config or Config()
        log_path = self.config.directory / "sentinel.log"
        handler = logging.handlers.RotatingFileHandler(log_path,maxBytes=1_000_000,backupCount=3,encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger().addHandler(handler)
        self.log_handler=handler
        logging.getLogger().setLevel(logging.INFO)
        self.stop = threading.Event()
        self.store = StateStore(self.config.directory,self.notify,on_event=self.real_event_received)
        self.claude_interrupts = ClaudeInterruptWatcher(self.store, self.config, self.stop)
        self.preview_only = preview_only
        self.preview = b""
        self.preview_lock = threading.Lock()
        self.threads = []
        self.server = None
        self.tray = None
        self.demo_started = None
        self.sound_gate = 0
        self.version = __version__
        self.muted_until = 0
        self.ports_cache=[]
        self.ports_checked=0
        self.startup = StartupManager()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.config.get('http_port')}/"

    def real_event_received(self, task):
        self.demo_started=None

    def notify(self, task):
        if not self.config.get("sound") or self.config.get("quiet") or time.time() < self.muted_until:
            return
        if time.monotonic()-self.sound_gate < 1:
            return
        self.sound_gate = time.monotonic()
        def play():
            try:
                if os.name != "nt":
                    return
                import winsound
                # 一次短提示。等待、失败、成功使用不同音程，不循环鸣响。
                tones = {"success":[(660,90),(880,130)],"error":[(440,120),(330,160)],"waiting":[(660,180)]}
                for frequency, duration in tones.get(task.status,[]):
                    if self.stop.is_set():
                        return
                    winsound.Beep(frequency,duration)
                    time.sleep(.05)
            except (RuntimeError,OSError):
                LOG.debug("音频设备不可用")
        threading.Thread(target=play,daemon=True,name="提示音").start()

    def display_snapshot(self, now):
        snapshot = self.store.snapshot(self.config.get("completion_hold"))
        if self.demo_started is not None:
            age = now-self.demo_started
            steps = ["running","waiting","success","error","idle"]
            if age >= 15:
                self.demo_started = None
            else:
                index = min(4,int(age//3))
                status = steps[index]
                snapshot["demo"] = True
                snapshot["task"] = None if status=="idle" else {
                    "status":status,"title":"演示 · AI 编程哨兵","label":{"running":"正在生成","waiting":"需要操作","success":"任务完成","error":"编译失败"}[status],
                    "mode":"build" if status=="error" else "ai","elapsed":92+age,"detail":"请在原工具中确认",
                    "errors":1 if status=="error" else None,"location":"main.c:128" if status=="error" else "",
                    "files":3 if status=="success" else None,"finished_at":self.demo_started+6 if status=="success" else None}
        return snapshot

    def display_loop(self):
        screen = Screen(self.config.get("port"))
        renderer = Renderer()
        connected = False
        retry_at = 0
        previous = None
        sent_times = []
        last_probe=0
        while not self.stop.is_set():
            start = time.monotonic()
            now = time.time()
            settings = self.config.public()
            if screen.requested_port != settings["port"]:
                screen.close()
                screen.requested_port = settings["port"]
                connected = False
                retry_at = 0
            if not connected and not self.preview_only and start>=retry_at:
                try:
                    screen.connect()
                    connected = True
                    previous = None
                    with self.store.lock:
                        self.store.health.update(connected=True,port=screen.port,device=screen.identity,
                            message="屏幕已连接 · LCD 写入已确认")
                except (OSError,ConnectionError) as exc:
                    retry_at = time.monotonic()+3
                    with self.store.lock:
                        self.store.health.update(connected=False,port="",fps=0,message=str(exc))
                    LOG.info("屏幕连接等待: %s", exc)
            if self.preview_only:
                with self.store.lock:
                    self.store.health["message"] = "仅预览模式 · 未占用 USB"
            snapshot = self.display_snapshot(now)
            image = renderer.render(snapshot,settings,now)
            buffer = io.BytesIO()
            image.save(buffer,format="PNG")
            with self.preview_lock:
                self.preview = buffer.getvalue()
            if connected:
                try:
                    pixels = image.tobytes()
                    if pixels != previous:
                        screen.draw(image)
                        last_probe=time.monotonic()
                        previous = pixels
                        sent_times.append(time.monotonic())
                        sent_times = [t for t in sent_times if time.monotonic()-t < 3]
                        with self.store.lock:
                            self.store.health["frames"] += 1
                            self.store.health.update(last_ack=screen.last_ack,fps=round(len(sent_times)/3,1))
                    elif time.monotonic()-last_probe>=2:
                        # 静态错误画面也保持只读心跳，及时发现 USB 被拔下。
                        screen.read_adc()
                        last_probe=time.monotonic()
                    sent_times=[t for t in sent_times if time.monotonic()-t<3]
                    with self.store.lock:
                        self.store.health.update(last_ack=screen.last_ack,fps=round(len(sent_times)/3,1))
                        if snapshot.get("task"):
                            self.store.health["message"] = "屏幕已连接 · LCD 写入已确认"
                except (OSError,ConnectionError) as exc:
                    connected = False
                    screen.close()
                    retry_at = time.monotonic()+2
                    with self.store.lock:
                        self.store.health.update(connected=False,port="",fps=0,message=f"USB 已断开，将自动重连：{exc}")
                    LOG.warning("USB 断开: %s",exc)
            if self.tray:
                status = snapshot["task"]["status"] if snapshot.get("task") else "idle"
                try:
                    self.tray.title = "AI 编程哨兵 · "+(snapshot["task"]["label"] if snapshot.get("task") else "随时待命")
                    if getattr(self,"_tray_status",None) != status:
                        self._tray_status = status
                        self.tray.icon = self.tray_image(status)
                except Exception:
                    LOG.debug("托盘状态更新失败",exc_info=True)
            target_fps=settings["fps"] if snapshot.get("task") or snapshot.get("demo") else 1
            self.stop.wait(max(.005,1/target_fps-(time.monotonic()-start)))
        if connected:
            try:
                farewell = {"task":{"status":"cancelled","title":"AI 编程哨兵","label":"已休息","detail":"下次启动自动连接"}}
                screen.draw(renderer.render(farewell,settings,time.time()))
            except (OSError,ConnectionError):
                pass
        screen.close()

    def status(self):
        data = self.store.snapshot(self.config.get("completion_hold"))
        if time.monotonic()-self.ports_checked>10:
            try:self.ports_cache=Screen.available()
            except OSError:self.ports_cache=[]
            self.ports_checked=time.monotonic()
        data.update(settings=self.config.public(),version=self.version,demo=self.demo_started is not None,
                    data_dir=str(self.config.directory),startup=self.startup_enabled(),muted_until=self.muted_until,
                    ports=self.ports_cache, claude=integration_status(self.config), startup_state=self.startup.state())
        return data

    def claude_event(self, payload):
        if not self.config.get("claude_watch"):
            return {}
        event = claude_hook_event(payload)
        if event:
            self.claude_interrupts.register(payload, event)
            with self.store.lock:
                old = self.store.tasks.get(event["id"])
                if payload.get("hook_event_name") == "SessionEnd" and (
                        old is None or old.finished_at is not None):
                    return {}
                # 未知版本通知不读取消息文本猜状态；真实后续执行解除等待。
                self.store.event(event)
                self.store.watcher["last_event"] = time.time()
        # 永远不返回权限决策、额外提示或 Stop 阻止决定。
        return {}

    def event(self, payload):
        self.demo_started = None
        task = self.store.event(payload)
        return {"ok":True,"task":task.public(time.time()) if task else None}

    def open_panel(self):
        webbrowser.open(self.url)

    @staticmethod
    def tray_image(status="idle"):
        image = Image.new("RGBA",(64,64),(0,0,0,0))
        d = ImageDraw.Draw(image)
        color = PALETTE.get(status,PALETTE["idle"])
        d.rounded_rectangle((6,6,58,58),radius=15,fill=(13,20,32),outline=color,width=3)
        d.line((20,31,29,40,45,23),fill=color,width=5)
        return image

    def start_tray(self):
        try:
            import pystray
            def quiet(icon,item):
                self.config.update({"quiet":not self.config.get("quiet")})
            def mute(icon,item):
                self.muted_until=time.time()+3600
            menu = pystray.Menu(
                pystray.MenuItem("打开控制面板",lambda icon,item:self.open_panel(),default=True),
                pystray.MenuItem("确认已结束提醒",lambda icon,item:self.store.acknowledge()),
                pystray.MenuItem("静音一小时",mute),
                pystray.MenuItem("持续静音",quiet,checked=lambda item:self.config.get("quiet")),
                pystray.MenuItem("开机自动启动",lambda icon,item:self.set_startup(not self.startup_enabled()),
                                checked=lambda item:self.startup_enabled(),
                                enabled=lambda item:self.startup.state()["supported"]),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出哨兵",lambda icon,item:self.shutdown()))
            self.tray = pystray.Icon("ai-programming-sentinel",self.tray_image(),"AI 编程哨兵",menu)
            self.tray.run_detached()
        except Exception:
            LOG.warning("托盘不可用，可通过控制面板退出",exc_info=True)

    def startup_enabled(self):
        return self.startup.state()["enabled"]

    def set_startup(self, enabled):
        return self.startup.set(enabled)

    def shutdown(self):
        self.stop.set()
        if self.tray:
            self.tray.stop()
        if self.server:
            threading.Thread(target=self.server.shutdown,daemon=True).start()

    def serve(self, *, browser=True, tray=True):
        try:
            self.startup.repair_after_upgrade()
        except OSError:
            LOG.warning("开机启动路径更新失败，可在控制面板重试")
        app = self
        class Handler(BaseHTTPRequestHandler):
            server_version = "Sentinel"
            def log_message(self,*args):
                pass

            def allowed_host(self):
                host = self.headers.get("Host", "")
                return host in [f"127.0.0.1:{app.config.get('http_port')}",f"localhost:{app.config.get('http_port')}"]

            def authenticated(self):
                token = self.headers.get("X-Sentinel-Token", "")
                return secrets.compare_digest(token,app.config.get("token"))

            def send(self, data, content="application/json; charset=utf-8", code=200):
                if not isinstance(data,bytes):
                    data = json.dumps(data,ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type",content)
                self.send_header("Content-Length",str(len(data)))
                self.send_header("Cache-Control","no-store")
                self.send_header("X-Content-Type-Options","nosniff")
                self.send_header("X-Frame-Options","DENY")
                self.send_header("Referrer-Policy","no-referrer")
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError):
                    pass

            def do_GET(self):
                if not self.allowed_host():
                    return self.send({"error":"Host 不允许"},code=403)
                path = urllib.parse.urlsplit(self.path).path
                if path == "/":
                    html = resource_path("web/index.html").read_text(encoding="utf-8")
                    html = html.replace("__TOKEN__",app.config.get("token"))
                    return self.send(html.encode("utf-8"),"text/html; charset=utf-8")
                if path == "/api/status":
                    if not self.authenticated():
                        return self.send({"error":"需要本机访问令牌"},code=403)
                    return self.send(app.status())
                if path == "/api/preview.png":
                    with app.preview_lock:
                        data = app.preview
                    return self.send(data,"image/png")
                if path in ("/api/icons/codex.png", "/api/icons/claude.png"):
                    return self.send(resource_path("assets/" + path.rsplit("/", 1)[-1]).read_bytes(), "image/png")
                return self.send({"error":"不存在"},code=404)

            def do_POST(self):
                if not self.allowed_host() or not self.authenticated():
                    return self.send({"error":"访问被拒绝"},code=403)
                origin = self.headers.get("Origin")
                if origin and origin not in [app.url.rstrip('/'),app.url.replace('127.0.0.1','localhost').rstrip('/')]:
                    return self.send({"error":"来源不允许"},code=403)
                try:
                    path = urllib.parse.urlsplit(self.path).path
                    length = int(self.headers.get("Content-Length","0"))
                    limit = 2*1024*1024 if path == HOOK_PATH else 65536
                    if not 0<=length<=limit:
                        return self.send({"error":"请求过大"},code=413)
                    self.connection.settimeout(3)
                    body = self.rfile.read(length)
                    if len(body)!=length:
                        return self.send({"error":"请求不完整"},code=400)
                    payload = json.loads(body) if body else {}
                    if not isinstance(payload,dict):
                        raise ValueError("请求需要 JSON 对象")
                    if path == HOOK_PATH:
                        return self.send(app.claude_event(payload))
                    if path == "/api/integrations/claude":
                        if not isinstance(payload.get("remove", False), bool):
                            raise ValueError("移除选项需要布尔值")
                        return self.send(configure_integration(app.config, remove=payload.get("remove", False)))
                    if path == "/api/event":
                        return self.send(app.event(payload))
                    if path == "/api/settings":
                        if "http_port" in payload:
                            raise ValueError("服务端口请退出程序后在配置文件修改")
                        settings = app.config.update(payload)
                        if payload.get("claude_watch") is False:
                            with app.store.lock:
                                for task_id in list(app.store.tasks):
                                    if task_id.startswith("claude:") and app.store.tasks[task_id].finished_at is None:
                                        del app.store.tasks[task_id]
                                app.store.revision += 1
                        return self.send(settings)
                    if path == "/api/ack":
                        app.store.acknowledge(payload.get("id"))
                    elif path == "/api/demo":
                        app.demo_started = None if payload.get("stop") else time.time()
                    elif path == "/api/mute":
                        app.muted_until = time.time()+3600 if not payload.get("clear") else 0
                    elif path == "/api/startup":
                        if not isinstance(payload.get("enabled"),bool):
                            raise ValueError("需要 enabled 布尔值")
                        return self.send({"ok": True, "startup": app.set_startup(payload["enabled"])})
                    elif path == "/api/shutdown":
                        self.send({"ok":True})
                        app.shutdown()
                        return
                    else:
                        return self.send({"error":"不存在"},code=404)
                    return self.send({"ok":True})
                except (ValueError,TypeError,KeyError) as exc:
                    self.send({"error":str(exc)},code=400)
                except (OSError,TimeoutError):
                    self.send({"error":"本机操作未完成，请检查日志"},code=500)
                    LOG.exception("本机 API 操作失败")

        class Server(ThreadingHTTPServer):
            daemon_threads=True
            # Windows 的 SO_REUSEADDR 会允许两个监听器占用同一端口，导致请求随机到达旧实例。
            allow_reuse_address=os.name != "nt"
            def server_bind(self):
                if os.name == "nt":
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                super().server_bind()
            def get_request(self):
                connection,address=super().get_request()
                connection.settimeout(5)
                return connection,address
        self.server = Server(("127.0.0.1",self.config.get("http_port")),Handler)
        for target,name in [(self.display_loop,"屏幕渲染"),(CodexWatcher(self.store,self.config,self.stop).run,"Codex 监听"),
                            (self.claude_interrupts.run, "Claude 中断监听")]:
            thread = threading.Thread(target=target,name=name,daemon=True)
            thread.start()
            self.threads.append(thread)
        if tray:
            self.start_tray()
        if browser:
            self.open_panel()
        LOG.info("哨兵启动 %s",self.url)
        try:
            self.server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:
            self.shutdown()
        finally:
            self.stop.set()
            if self.tray:
                self.tray.stop()
            self.server.server_close()
            for thread in self.threads:
                thread.join(timeout=4)
            LOG.info("哨兵已退出，USB 串口已释放")
            logging.getLogger().removeHandler(self.log_handler)
            self.log_handler.close()
