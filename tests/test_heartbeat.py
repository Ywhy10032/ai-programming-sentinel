import logging
import threading
import time

from sentinel.app import Application
from sentinel.config import Config
import sentinel.app as module


def test_static_failure_still_detects_usb_disconnect(tmp_path,monkeypatch):
    class FakeScreen:
        def __init__(self,port):
            self.requested_port=port
            self.port='COM8'
            self.identity='MSN01'
            self.last_ack=None
        def connect(self):pass
        def draw(self,image):self.last_ack=time.time()
        def read_adc(self):raise ConnectionError('模拟拔下 USB')
        def close(self):pass
    monkeypatch.setattr(module,'Screen',FakeScreen)
    config=Config(tmp_path)
    config.update({'sound':False,'codex_watch':False})
    app=Application(config)
    app.store.event({'id':'failure','status':'error','title':'静态失败记录','detail':'错误需确认'})
    thread=threading.Thread(target=app.display_loop)
    thread.start()
    deadline=time.monotonic()+4
    try:
        while time.monotonic()<deadline:
            if 'USB 已断开' in app.store.health['message']:break
            time.sleep(.04)
        assert app.store.health['connected'] is False
        assert 'USB 已断开' in app.store.health['message']
    finally:
        app.stop.set()
        thread.join(3)
        logging.getLogger().removeHandler(app.log_handler)
        app.log_handler.close()
