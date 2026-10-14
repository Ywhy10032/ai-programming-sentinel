"""生成 Windows 便携版：图形入口和命令行入口共用运行库。"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from sentinel.app import Application
from sentinel import __version__


def main():
    from build_observer import build
    build()
    assets=ROOT/'assets'
    assets.mkdir(exist_ok=True)
    Application.tray_image('success').save(assets/'sentinel.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64)])
    spec=ROOT/'sentinel.spec'
    spec.write_text('''# -*- mode: python ; coding: utf-8 -*-
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
''',encoding='utf-8')
    # 独立构建目录避免 Windows 对旧运行库的占用及只读目录属性影响重建。
    staging=ROOT/'tmp/build-dist'/uuid.uuid4().hex[:12]
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--distpath',str(staging),str(spec)],cwd=ROOT,check=True)
    output=ROOT/'output'
    package=output/f'AI编程哨兵-{__version__}'
    package.mkdir(parents=True,exist_ok=True)
    shutil.copytree(staging/'AI编程哨兵',package,dirs_exist_ok=True)
    shutil.copy2(ROOT/'README.md',package/'使用说明.txt')
    shutil.copy2(ROOT/'README.md',package/'README.md')
    if (ROOT/'docs').exists():shutil.copytree(ROOT/'docs',package/'docs',dirs_exist_ok=True)
    shutil.copy2(ROOT/'THIRD_PARTY_NOTICES.md',package/'第三方说明.txt')
    shutil.copy2(ROOT/'LICENSE',package/'LICENSE')
    shutil.copy2(ROOT/'CHANGELOG.md',package/'更新记录.txt')
    licenses=package/'licenses'
    licenses.mkdir(exist_ok=True)
    for name in ['Pillow','pyserial','pystray','six','PyInstaller','zstandard']:
        distribution=importlib.metadata.distribution(name)
        for file in distribution.files or []:
            if file.name.upper().startswith(('LICENSE','COPYING','COPYRIGHT')):
                source=Path(distribution.locate_file(file))
                if source.is_file():shutil.copy2(source,licenses/(name+'-'+source.name))
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists():shutil.copy2(python_license,licenses/'Python-LICENSE.txt')
    manifest={str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(package.rglob('*')) if p.is_file() and p.name!='SHA256.json'}
    (package/'SHA256.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    zip_path=shutil.make_archive(str(output/f'AI编程哨兵-{__version__}'),'zip',root_dir=output,base_dir=package.name)
    print('PACKAGE',package)
    print('ZIP',zip_path)
    print('SHA256',hashlib.sha256(Path(zip_path).read_bytes()).hexdigest())


if __name__=='__main__':main()
