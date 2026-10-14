"""整理公开发布资产，只收录明确的源码和文档，不包含用户数据。"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sentinel import __version__

ROOT_FILES = ('README.md', 'CHANGELOG.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'main.py',
              'requirements.txt', 'requirements-dev.txt', 'requirements-lock.txt', 'pytest.ini',
              '.gitignore', '.gitattributes', 'sentinel.spec', 'sentinel.cmd', '启动哨兵.cmd')
DIRECTORIES = ('sentinel', 'tests', 'tools', 'web', 'assets', 'docs', '.github')
TEXT_SUFFIXES = ('.py', '.md', '.txt', '.html', '.js', '.json', '.yml', '.yaml', '.ini', '.spec', '.cmd')
SECRET_PATTERN = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|'
                            r'sk-(?:proj-)?[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def source_files(root=ROOT):
    files = [root / name for name in ROOT_FILES if (root / name).is_file()]
    for directory in DIRECTORIES:
        files.extend(path for path in (root / directory).rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts and path.suffix not in ('.pyc', '.log'))
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'不能打包工作目录外的链接: {path.name}')
        if path.name in ('config.json', 'history.json', 'pending.json', '.credentials.json', '.env'):
            raise ValueError(f'发布目录中发现用户数据文件: {path.name}')
        if path.suffix in TEXT_SUFFIXES or path.name in ('LICENSE', '.gitignore', '.gitattributes'):
            text = path.read_text(encoding='utf-8')
            if SECRET_PATTERN.search(text):
                raise ValueError(f'文件疑似包含访问凭据，停止发布: {path.name}')
    return sorted(set(files))


def main():
    out = ROOT / 'output'
    package = out / f'AI编程哨兵-{__version__}'
    manifest = json.loads((package / 'SHA256.json').read_text(encoding='utf-8'))
    for name, expected in manifest.items():
        if hashlib.sha256((package / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'便携版文件校验失败: {name}')
    release = out / f'release-{__version__}'
    release.mkdir(parents=True, exist_ok=True)
    portable = release / f'AI-Programming-Sentinel-{__version__}-Windows-x64.zip'
    shutil.copy2(out / f'AI编程哨兵-{__version__}.zip', portable)
    with zipfile.ZipFile(portable) as archive:
        if archive.testzip() is not None:
            raise ValueError('便携版压缩包损坏')
        for name, expected in manifest.items():
            zipped = archive.read(package.name + '/' + name.replace('\\', '/'))
            if hashlib.sha256(zipped).hexdigest() != expected:
                raise ValueError(f'压缩包内容与便携版不一致: {name}')
    source = release / f'AI-Programming-Sentinel-{__version__}-Source.zip'
    files = source_files()
    with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f'ai-programming-sentinel-{__version__}/' + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(source) as archive:
        if archive.testzip() is not None:
            raise ValueError('源码压缩包损坏')
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (portable, source)}
    (release / 'SHA256SUMS.txt').write_text(''.join(f'{value}  {name}\n' for name, value in hashes.items()), encoding='utf-8')
    report = {'version': __version__, 'portable_files_verified': len(manifest), 'public_source_files': len(files),
              'credentials_scan_passed': True, 'archives': hashes}
    (out / f'打包核验-{__version__}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if sys.stdout:sys.stdout.reconfigure(encoding='utf-8')
    main()
