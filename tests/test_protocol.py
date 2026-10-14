import pytest
from PIL import Image
from sentinel.device import Screen,rgb565,frame_packets


def decode(packets):
    ram=bytearray(256)
    output=bytearray()
    for offset in range(0,len(packets),6):
        cmd=packets[offset:offset+6]
        assert len(cmd)==6
        if cmd[:2]==b"\x02\x04":ram[:]=cmd[2:]*64
        elif cmd[0]==4:ram[cmd[1]*4:cmd[1]*4+4]=cmd[2:]
        elif cmd[:3]==b"\x02\x03\x08":output.extend(ram[:cmd[3]*256+cmd[4]])
        else:pytest.fail("出现显示路径以外的命令")
    return bytes(output)


def test_rgb565_primary_colors():
    image=Image.new("RGB",(4,1))
    image.putdata([(255,0,0),(0,255,0),(0,0,255),(255,255,255)])
    assert rgb565(image)==bytes.fromhex("f800 07e0 001f ffff")


def test_compressed_frame_roundtrip_and_no_flash_commands():
    image=Image.new("RGB",(160,80),(7,11,20))
    for i in range(80):image.putpixel((i,i),(255,i,10))
    raw=rgb565(image)
    packet=frame_packets(raw)
    assert decode(packet)==raw
    assert len(packet)<39000/4


def test_solid_frame_golden_packets():
    packet=frame_packets(bytes.fromhex("f800")*12800)
    assert packet==(bytes.fromhex("02 04 f8 00 f8 00 02 03 08 01 00 00")*100)


def test_bad_frame_rejected():
    with pytest.raises(ValueError):frame_packets(b"x"*25599)


class FakeSerial:
    def __init__(self,*a,**kw):
        self.pending=bytearray(b"\0MSN01")
        self.written=[]
        self.closed=False
        self.chunk=1
    @property
    def in_waiting(self):return len(self.pending)
    def read(self,n):
        result=bytes(self.pending[:self.chunk]);del self.pending[:self.chunk]
        return result
    def reset_input_buffer(self):self.pending.clear()
    def write(self,data):
        self.written.append(data)
        if data==b"\0MSNCN" or data[:2]==b"\x02\x03":self.pending.extend(data)
        elif data[0]==8:self.pending.extend(data[:4]+b'\x08\x00')
        return len(data)
    def flush(self):pass
    def close(self):self.closed=True


def test_fragmented_handshake_and_full_ack(monkeypatch):
    monkeypatch.setattr(Screen,"available",staticmethod(lambda:[{"port":"COM8","supported":True}]))
    screen=Screen(serial_factory=FakeSerial)
    screen.connect()
    assert screen.identity=="MSN01"
    screen.draw(Image.new("RGB",(160,80)))
    assert screen.acks==3
    fake=screen.serial
    screen.close()
    assert fake.closed


def test_wrong_port_never_receives_commands(monkeypatch):
    class Wrong(FakeSerial):
        def __init__(self,*a,**kw):super().__init__();self.pending.clear()
    monkeypatch.setattr(Screen,"available",staticmethod(lambda:[]))
    fake=Wrong()
    screen=Screen("COM17",serial_factory=lambda *a,**kw:fake)
    with pytest.raises(ConnectionError):screen.connect()
    assert not fake.written


def test_silent_verified_device_can_resume_without_boot_banner(monkeypatch):
    class Active(FakeSerial):
        def __init__(self,*a,**kw):super().__init__();self.pending.clear()
        def write(self,data):
            self.written.append(data)
            if data[:2]==b"\x02\x03":self.pending.extend(data)
            return len(data)
    monkeypatch.setattr(Screen,"available",staticmethod(lambda:[{"port":"COM8","supported":True}]))
    screen=Screen(serial_factory=Active)
    screen.connect()
    assert screen.acks==1
    screen.close()


def test_partial_region_rgb565_roundtrip():
    raw=bytes(range(70))
    assert decode(frame_packets(raw,7,5))==raw


def test_updates_only_changed_rectangle_without_reinitializing(monkeypatch):
    monkeypatch.setattr(Screen,'available',staticmethod(lambda:[{'port':'COM8','supported':True}]))
    screen=Screen(serial_factory=FakeSerial)
    screen.connect()
    image=Image.new('RGB',(160,80))
    screen.draw(image)
    before=len(screen.serial.written)
    image.putpixel((80,40),(255,0,0))
    screen.draw(image)
    commands=screen.serial.written[before:]
    assert screen.last_rect==(80,40,1,1)
    assert commands[0]==bytes((2,0,0,80,0,40))
    assert commands[1]==bytes((2,1,0,1,0,1))
    assert b'\x02\x03\x0a\x00\x00\x00' not in commands
    assert commands[-1]==bytes((8,9,0,0,0,0))
    assert screen.draw(image) is False
    screen.close()
