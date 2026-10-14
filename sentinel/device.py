"""MSU2 MINI 原厂协议。仅写 LCD 易失画面，不提供擦除/烧录闪存命令。

协议参考 Blackwood416/MSU_Mini_UsbScreen (MIT)，并核对本地官方演示程序。
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter

import serial
from serial.tools import list_ports
from PIL import ImageChops

LOG = logging.getLogger(__name__)
WIDTH, HEIGHT = 160, 80


def rgb565(image) -> bytes:
    pixels = image.convert("RGB").tobytes()
    data = bytearray(len(pixels) // 3 * 2)
    for p, d in zip(range(0, len(pixels), 3), range(0, len(data), 2)):
        r, g, b = pixels[p:p+3]
        data[d] = (r & 0xF8) | (g >> 5)
        data[d+1] = ((g & 0x1C) << 3) | (b >> 3)
    return bytes(data)


def frame_packets(data: bytes, width=WIDTH, height=HEIGHT) -> bytes:
    if not 1<=width<=WIDTH or not 1<=height<=HEIGHT or len(data)!=width*height*2:
        raise ValueError("RGB565 数据与显示区域尺寸不匹配")
    packets = bytearray()
    for start in range(0, len(data), 256):
        chunk = data[start:start+256]
        valid = len(chunk)
        chunk = chunk.ljust(256, b"\xff")
        # 原厂 fast 路径：先填充最常见的两个像素，再更新不同的四字节组。
        # 深色状态界面可大幅减少 USB 流量；不使用旧固件兼容性未知的 0x09。
        groups = [chunk[i:i+4] for i in range(0,256,4)]
        common = Counter(groups).most_common(1)[0][0]
        packets.extend(b"\x02\x04"+common)
        for i,group in enumerate(groups):
            if group != common:
                packets.extend(bytes((4,i))+group)
        packets.extend(bytes((2, 3, 8, valid >> 8, valid & 255, 0)))
    return bytes(packets)


class Screen:
    def __init__(self, port="auto", serial_factory=serial.Serial):
        self.requested_port = port
        self.serial_factory = serial_factory
        self.serial = None
        self.port = ""
        self.identity = ""
        self.last_ack = None
        self.acks = 0
        self.last_image=None
        self.last_rect=None

    @staticmethod
    def available() -> list[dict]:
        return [{"port": p.device, "name": p.description,
                 "supported": p.vid == 0x1A86 and p.pid == 0xFE0C}
                for p in list_ports.comports()]

    def connect(self):
        self.close()
        if self.requested_port == "auto":
            candidates = [p["port"] for p in self.available() if p["supported"]]
            if not candidates:
                raise ConnectionError("未找到 MSU2 屏幕，请检查 USB 连接")
        else:
            candidates = [self.requested_port]
        problems = []
        for port in candidates:
            try:
                self.serial = self.serial_factory(port, 19200, timeout=0.03, write_timeout=1.5,
                                                  dsrdtr=False, rtscts=False)
                greeting = self._read_until(b"MSN", timeout=0.7)
                match = re.search(rb"\x00MSN(\d\d)", greeting)
                supported = any(p["port"]==port and p["supported"] for p in self.available())
                if not match and not supported:
                    raise ConnectionError("设备未返回 MSN 标识，已停止发送")
                # 固件激活后不会在每次重开串口时再次发送启动标识。
                # 仅对 USB VID/PID 已核验的设备尝试无副作用的原厂握手。
                self.identity = "MSN" + match[1].decode("ascii") if match else "MSN（握手确认）"
                self.serial.reset_input_buffer()
                self._write(b"\x00MSNCN")
                response = self._read_until(b"MSNCN", timeout=1)
                if b"MSNCN" not in response and match:
                    raise ConnectionError("屏幕握手未确认，请重新插拔 USB 屏幕")
                self.port = port
                # LCD_State(0)：原厂横向显示模式，反向由电脑端旋转画面实现。
                try:
                    self.command(b"\x02\x03\x0a\x00\x00\x00")
                except ConnectionError:
                    raise ConnectionError("屏幕未返回协议响应，请重新插拔 USB 屏幕") from None
                LOG.info("连接屏幕 %s %s", port, self.identity)
                return
            except (OSError, serial.SerialException, ConnectionError) as exc:
                problems.append(f"{port}: {exc}")
                self.close()
        raise ConnectionError("；".join(problems))

    def _write(self, data: bytes):
        if self.serial is None:
            raise ConnectionError("屏幕未连接")
        written = self.serial.write(data)
        if written != len(data):
            raise ConnectionError("USB 数据未完整发送")

    def _read_until(self, needle: bytes, timeout=1) -> bytes:
        deadline = time.monotonic() + timeout
        received = bytearray()
        while time.monotonic() < deadline:
            part = self.serial.read(max(1, min(self.serial.in_waiting, 4096)))
            received.extend(part)
            index = received.find(needle)
            if index >= 0 and len(received) >= index + len(needle):
                # 标识可能被 USB 分包，允许剩余字节到达。
                if needle == b"MSN" and len(received) < index + 5:
                    continue
                return bytes(received)
            if len(received) > 8192:
                del received[:-256]
        return bytes(received)

    def command(self, data: bytes):
        if len(data) != 6:
            raise ValueError("命令长度必须为 6")
        # 完整六字节回显匹配，排除上一帧残留的同类响应。
        self.serial.reset_input_buffer()
        self._write(data)
        response = self._read_until(data, timeout=1)
        if data not in response:
            raise ConnectionError(f"屏幕未确认命令 {data.hex(' ')}")
        self.last_ack = time.time()
        self.acks += 1

    def read_adc(self, channel=9):
        """只读 ADC 回应作为处理屏障，避免重复 LCD_State 导致闪屏。"""
        request=bytes((8,channel,0,0,0,0))
        self.serial.reset_input_buffer()
        self._write(request)
        deadline=time.monotonic()+1
        received=bytearray()
        while time.monotonic()<deadline:
            received.extend(self.serial.read(max(1,min(self.serial.in_waiting,4096))))
            index=received.find(request[:4])
            if index>=0 and len(received)>=index+6:
                self.last_ack=time.time()
                self.acks+=1
                return received[index+4]*256+received[index+5]
            if len(received)>8192:del received[:-256]
        raise ConnectionError("屏幕未返回帧处理后的只读响应")

    def draw(self, image):
        if image.size != (WIDTH, HEIGHT):
            raise ValueError("画面尺寸必须为 160×80")
        box=ImageChops.difference(image,self.last_image).getbbox() if self.last_image is not None else (0,0,WIDTH,HEIGHT)
        if box is None:return False
        x,y,right,bottom=box
        width,height=right-x,bottom-y
        region=image.crop(box)
        self._write(bytes((2,0,x>>8,x&255,y>>8,y&255)))
        self._write(bytes((2,1,width>>8,width&255,height>>8,height&255)))
        self.command(b"\x02\x03\x07\x00\x00\x00")
        packets=frame_packets(rgb565(region),width,height)
        # 每次写入最多 60 字节，保持 USB 短包中的六字节命令边界完整。
        # 减少旧固件接收缓冲溢出风险，USB 背压仍由系统处理。
        for start in range(0,len(packets),60):
            self._write(packets[start:start+60])
        self.serial.flush()
        # LCD_State 只在连接时执行；每帧使用只读 ADC 回显验证处理完成。
        self.read_adc()
        self.last_image=image.copy()
        self.last_rect=(x,y,width,height)
        return True

    def close(self):
        if self.serial is not None:
            try:
                self.serial.close()
            except OSError:
                pass
        self.serial = None
        self.port = ""
        self.last_image=None
