from __future__ import annotations

import datetime as dt
import math
import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .brands import brand_icon, provider_for

PALETTE = {
    "running": (91, 173, 255), "waiting": (255, 199, 83),
    "success": (98, 220, 160), "error": (255, 112, 112),
    "cancelled": (155, 167, 191), "unknown": (159, 174, 205), "idle": (178, 189, 214),
}
BG = (7, 11, 20)
WHITE = (235, 241, 253)
MUTED = (130, 146, 174)
SCALE = 3


@lru_cache(maxsize=48)
def font(size: int, bold=False, numeric=False):
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    names = (["bahnschrift.ttf", "consola.ttf"] if numeric else
             ["msyhbd.ttc" if bold else "msyh.ttc", "simhei.ttf"])
    for name in names:
        if (windows/name).exists():
            return ImageFont.truetype(str(windows/name), size*SCALE)
    return ImageFont.load_default(size=size*SCALE)


def elapsed_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}"
    return f"{seconds//60:02}:{seconds%60:02}"


class Renderer:
    def render(self, snapshot: dict, settings: dict, now: float) -> Image.Image:
        self.image = Image.new("RGB", (160*SCALE, 80*SCALE), BG)
        self.d = ImageDraw.Draw(self.image)
        task = snapshot.get("task")
        status = task["status"] if task else "idle"
        accent = PALETTE[status]
        reduced = settings.get("reduced_motion", False)
        phase = 0 if reduced else now
        # 低亮度星点和右侧细线为信息留出空间。
        for i, (x, y) in enumerate([(147, 10), (150, 65), (140, 51), (127, 13)]):
            brightness = 0.25 + 0.06*math.sin(i)
            self.dot(x, y, 0.45, self.color(accent, brightness))
        self.line((7, 22, 153, 22), (30, 41, 61))
        self.line((7, 60, 153, 60), (30, 41, 61))
        self.line((0, 0, 0, 80), accent, 2)
        if task:
            provider = provider_for(task)
            heading = ("Codex · " if provider == "codex" else "Claude · " if provider == "claude" else "") + task["title"]
            self.text(8, 5, self.fit(heading, 132, 10), 10, MUTED)
            self.dot(149, 11, 2, accent)
            self.icon(status, 21, 41, accent, phase, reduced, task, now)
            self.text(39, 29, task["label"], 20, WHITE, bold=True)
            mode = task.get("mode", "ai")
            if status == "running":
                bottom = f"已用时 {elapsed_text(task.get('elapsed', 0))}"
            elif status == "error":
                bottom = task.get("location") or task.get("detail") or "完整日志见电脑端"
                if task.get("errors") is not None:
                    bottom = f"{task['errors']} 错误 · {bottom}"
            elif status == "success":
                bottom = f"修改 {task['files']} 个文件" if task.get("files") is not None else f"耗时 {elapsed_text(task.get('elapsed',0))}"
            elif status == "waiting":
                bottom = task.get("detail") or "请在原工具中处理"
            else:
                bottom = task.get("detail") or "持续监听真实事件"
            self.scrolling_text(8, 64, bottom, 145, 10, MUTED, phase, reduced)
            if status == "running" and not reduced:
                # 往返的短光带仅表达正在运行，并非百分比进度。
                x = 8 + (1+math.sin(phase*1.65))*63
                self.line((x, 59.5, x+18, 59.5), self.color(accent, 0.7), 1)
            if status == "success" and not reduced:
                age = now - (task.get("finished_at") or now)
                if age < 1.4:
                    glow = max(0, 1 - age/1.4) * (0.35+0.15*math.sin(age*10))
                    self.d.rounded_rectangle((2*SCALE, 2*SCALE, 157*SCALE, 77*SCALE), radius=4*SCALE,
                                             outline=self.color(accent, glow), width=SCALE)
        else:
            moment = dt.datetime.fromtimestamp(now)
            self.text(8, 5, "AI 编程哨兵", 10, MUTED)
            self.dot(149, 11, 2, (98, 220, 160) if snapshot.get("device", {}).get("connected") else MUTED)
            clock = moment.strftime("%H:%M:%S" if settings.get("clock_24h", True) else "%I:%M:%S")
            size = 30
            width = font(size, numeric=True).getlength(clock) / SCALE
            self.text((160-width)/2, 27, clock, size, WHITE, numeric=True)
            if not settings.get("clock_24h", True):
                self.text(119, 5, "上午" if moment.hour < 12 else "下午", 10, MUTED)
            weekday = "一二三四五六日"[moment.weekday()]
            self.text(8, 64, f"{moment:%m/%d} 周{weekday}", 10, MUTED)
            self.text(104, 64, "随时待命", 10, (139, 169, 184))
        result = self.image.resize((160, 80), Image.Resampling.LANCZOS)
        if settings.get("rotation", 0) == 180:
            result = result.transpose(Image.Transpose.ROTATE_180)
        return result

    @staticmethod
    def color(c, factor):
        return tuple(max(0, min(255, int(v*factor))) for v in c)

    def text(self, x, y, text, size, color, bold=False, numeric=False):
        self.d.text((int(x*SCALE), int(y*SCALE)), text, font=font(size, bold, numeric), fill=color,
                    stroke_width=0, anchor="lt")

    def fit(self, text, width, size):
        f = font(size)
        if f.getlength(text) <= width*SCALE:
            return text
        while text and f.getlength(text+"…") > width*SCALE:
            text = text[:-1]
        return text+"…"

    def scrolling_text(self, x, y, text, width, size, color, phase, reduced):
        length = font(size).getlength(text)/SCALE
        if length <= width or reduced:
            self.text(x,y,self.fit(text,width,size),size,color)
            return
        strip = Image.new("RGB", (math.ceil(length*SCALE)+4, 14*SCALE), BG)
        ImageDraw.Draw(strip).text((0,0),text,font=font(size),fill=color,anchor="lt")
        distance = length-width
        duration = distance/16
        cycle = (phase % (2*duration+4))
        if cycle < 2:
            offset = 0
        elif cycle < duration+2:
            offset = (cycle-2)*16
        elif cycle < duration+4:
            offset = distance
        else:
            offset = max(0, distance-(cycle-duration-4)*16)
        self.image.paste(strip.crop((int(offset*SCALE),0,int((offset+width)*SCALE),14*SCALE)),
                         (x*SCALE,y*SCALE))

    def line(self, coords, color, width=1):
        self.d.line(tuple(round(v*SCALE) for v in coords),fill=color,width=max(1,round(width*SCALE)))

    def dot(self, x, y, radius, color):
        self.d.ellipse(tuple(round(v*SCALE) for v in (x-radius,y-radius,x+radius,y+radius)),fill=color)

    def icon(self, status, x, y, color, phase, reduced, task, now):
        if status == "running":
            provider = provider_for(task)
            if provider:
                # 图标保持稳定；仅外围光点表达运行，避免旋转标志影响识别。
                logo = brand_icon(provider, 20*SCALE)
                self.image.paste(logo, (int((x-10)*SCALE), int((y-10)*SCALE)), logo)
                for i in range(16):
                    angle = i*math.pi/8
                    pulse = 0.15+0.7*((i/16-phase*.4)%1)**3 if not reduced else .4
                    self.dot(x+14*math.cos(angle), y+14*math.sin(angle), .65, self.color(color, pulse))
                return
            for i in range(12):
                angle = i*math.pi/6
                pulse = 0.2+0.8*((i/12-phase*0.55)%1)**3 if not reduced else 0.75
                self.dot(x+10*math.cos(angle),y+10*math.sin(angle),1.55,self.color(color,pulse))
            self.line((x-3,y,x,y-4,x+3,y,x,y+4,x-3,y),color,1)
        elif status == "success":
            age = now - (task.get("finished_at") or now)
            ratio = 1 if reduced else min(1,max(0,age/0.4))
            self.d.ellipse(tuple(int(v*SCALE) for v in (x-12,y-12,x+12,y+12)),outline=self.color(color,0.3),width=SCALE)
            points = [(x-6,y),(x-1,y+5),(x+7,y-5)]
            if ratio < .4:
                end = (x-6+5*ratio/.4,y+5*ratio/.4)
                self.line((*points[0],*end),color,2)
            else:
                self.line((*points[0],*points[1]),color,2)
                k=(ratio-.4)/.6
                self.line((*points[1],x-1+8*k,y+5-10*k),color,2)
        elif status == "waiting":
            lift = 0 if reduced else math.sin(phase*2.3)*0.8
            self.d.rounded_rectangle(tuple(int(v*SCALE) for v in (x-10,y-11+lift,x+10,y+11+lift)),radius=3*SCALE,
                                     outline=self.color(color,0.45),width=SCALE)
            self.line((x-3,y-5+lift,x-3,y+5+lift),color,2)
            self.line((x+3,y-5+lift,x+3,y+5+lift),color,2)
        elif status == "error":
            self.line((x,y-12,x+12,y+10,x-12,y+10,x,y-12),color,1.7)
            self.line((x,y-4,x,y+2),color,2)
            self.dot(x,y+6,1,color)
        else:
            self.d.ellipse(tuple(int(v*SCALE) for v in (x-10,y-10,x+10,y+10)),outline=color,width=SCALE)
            self.line((x-4,y,x+4,y),color,2)
