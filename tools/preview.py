"""导出第一版界面演示，不发送事件、不连接 USB。"""
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from PIL import Image
from sentinel.render import Renderer


def main():
    output=ROOT/'output/previews'
    output.mkdir(parents=True,exist_ok=True)
    renderer=Renderer()
    start=time.time()
    sequence=['running','waiting','success','error','idle']
    labels={'running':'正在生成','waiting':'需要操作','success':'任务完成','error':'编译失败'}
    frames=[]
    stills=[]
    for index in range(120):
        elapsed=index/8
        status=sequence[min(4,int(elapsed//3))]
        task=None if status=='idle' else {
            'title':'演示 · 修改程序','status':status,'label':labels[status],
            'mode':'build' if status=='error' else 'ai','elapsed':92+elapsed,
            'detail':'请在原工具中确认','location':'main.c:128' if status=='error' else '',
            'errors':1 if status=='error' else None,'files':3 if status=='success' else None,
            'finished_at':start+6 if status=='success' else None}
        image=renderer.render({'task':task,'device':{'connected':True}}, {},start+elapsed)
        large=image.resize((640,320),Image.Resampling.NEAREST)
        if index in [8,32,60,80]:stills.append(large)
        frames.append(large.convert('P',palette=Image.Palette.ADAPTIVE,colors=128))
    frames[0].save(output/'屏幕状态.gif',save_all=True,append_images=frames[1:],duration=125,loop=0,optimize=True,disposal=2)
    grid=Image.new('RGB',(1280,640),(7,11,20))
    for index,image in enumerate(stills):grid.paste(image,(index%2*640,index//2*320))
    grid.save(output/'四种状态.png')
    print(output)


if __name__=='__main__':main()
