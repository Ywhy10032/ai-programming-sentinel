# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
root=Path(SPECPATH)
a=Analysis([str(root/'main.py')],pathex=[str(root)],binaries=[],
           datas=[(str(root/'web'),'web'),(str(root/'assets'),'assets'),
                  (str(root/'tmp/desktop-observer/observer.exe'),'.')],hiddenimports=['pystray._win32'],
           hookspath=[],hooksconfig={},runtime_hooks=[],
           excludes=['tkinter','pytest','PyInstaller'],noarchive=False)
pyz=PYZ(a.pure)
gui=EXE(pyz,a.scripts,[],exclude_binaries=True,name='AI编程哨兵',
        debug=False,bootloader_ignore_signals=False,strip=False,upx=False,
        console=False,icon=str(root/'assets/sentinel.ico'))
cli=EXE(pyz,a.scripts,[],exclude_binaries=True,name='sentinel',
        debug=False,bootloader_ignore_signals=False,strip=False,upx=False,
        console=True,icon=str(root/'assets/sentinel.ico'))
coll=COLLECT(gui,cli,a.binaries,a.datas,strip=False,upx=False,name='AI编程哨兵')
