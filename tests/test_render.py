from sentinel.render import Renderer,elapsed_text
from PIL import ImageChops
import pytest


@pytest.mark.parametrize("status,label",[("running","正在生成"),("waiting","需要操作"),("success","任务完成"),("error","编译失败"),("cancelled","已停止"),("unknown","等待事件")])
def test_all_states_are_renderable(status,label):
    task={"status":status,"title":"非常长的中文任务名称需要正确截断","label":label,"elapsed":92,
          "detail":"一段超过一行的错误说明不会覆盖大字状态","finished_at":999 if status=="success" else None}
    image=Renderer().render({"task":task},{},1000)
    assert image.size==(160,80) and image.mode=="RGB"
    assert len(set(image.get_flattened_data()))>20


def test_rotation_and_reduced_motion():
    task={"status":"running","title":"项目","label":"正在生成","elapsed":0}
    a=Renderer().render({"task":task},{"reduced_motion":True},1000)
    b=Renderer().render({"task":task},{"reduced_motion":True},1001)
    assert ImageChops.difference(a,b).getbbox() is None
    c=Renderer().render({"task":task},{"reduced_motion":True,"rotation":180},1000)
    assert ImageChops.difference(a.rotate(180),c).getbbox() is None


def test_clock_and_no_fake_progress():
    image=Renderer().render({"task":None,"device":{"connected":True}},{},1000)
    assert image.size==(160,80)
    assert elapsed_text(92)=="01:32" and elapsed_text(3602)=="01:00:02"


def test_idle_seconds_are_as_large_as_hours_and_minutes(monkeypatch):
    import datetime
    from sentinel.render import font, SCALE
    calls = []
    real_text = Renderer.text
    def capture(self, x, y, text, size, color, **kwargs):
        calls.append((x, text, size, kwargs))
        return real_text(self, x, y, text, size, color, **kwargs)
    monkeypatch.setattr(Renderer, 'text', capture)
    Renderer().render({'task': None}, {}, datetime.datetime(2026, 10, 14, 23, 59, 58).timestamp())
    x, text, size, options = next(c for c in calls if c[3].get('numeric'))
    assert text == '23:59:58' and size == 30
    assert x >= 5 and x + font(size, numeric=True).getlength(text) / SCALE <= 155


def test_tool_icons_are_distinct_and_do_not_depend_on_title():
    task = {'status': 'running', 'title': '同一个项目', 'label': '正在生成', 'elapsed': 92}
    images = [Renderer().render({'task': dict(task, provider=provider)}, {'reduced_motion': True}, 1000)
              for provider in ('codex', 'claude')]
    assert ImageChops.difference(images[0].crop((10, 30, 32, 52)), images[1].crop((10, 30, 32, 52))).getbbox()
