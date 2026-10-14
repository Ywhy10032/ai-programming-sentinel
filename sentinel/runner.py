from __future__ import annotations

import codecs
import locale
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .diagnostics import summarize,ERROR_LINE,LOCATION


def decode_output(data: bytes) -> str:
    for encoding in ["utf-8", locale.getpreferredencoding(False), "gb18030"]:
        try:
            return data.decode(encoding)
        except (UnicodeError, LookupError):
            pass
    return data.decode("utf-8", "replace")


def run_command(argv: list[str], emit, directory: Path, title="编译任务", mode="build", cwd=None) -> int:
    if not argv:
        raise ValueError("需要执行的命令")
    directory.mkdir(parents=True, exist_ok=True)
    task_id = f"run:{uuid.uuid4().hex}"
    start = time.time()
    log = directory / f"{time.strftime('%Y%m%d-%H%M%S')}-{task_id[4:12]}.log"
    base = {"id":task_id,"title":title,"mode":mode,"source":"编译命令包装器","started_at":start,"log_path":str(log)}
    emit({**base,"status":"running","detail":"正在执行命令"})
    process = None
    chunks = bytearray()
    diagnostic_lines=[]
    pending_line=b""
    truncated=False
    interrupted = False
    try:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(argv,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                   creationflags=flags,start_new_session=os.name != "nt")
        # 读取固定大小字节块，避免无换行输出阻塞或占用无限内存。
        with log.open("w",encoding="utf-8") as stream:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                chunk = process.stdout.read1(4096)
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks)>4*1024*1024:
                    del chunks[:-4*1024*1024]
                    truncated=True
                parts=(pending_line+chunk).split(b"\n")
                pending_line=parts.pop()
                if len(pending_line)>65536:
                    parts.append(pending_line[:65536])
                    pending_line=b""
                for line in parts:
                    text=decode_output(line)
                    if len(diagnostic_lines)<64 and (ERROR_LINE.search(text) or LOCATION.search(text) or text.lstrip().startswith('File "')):
                        diagnostic_lines.append(line[:4096])
                # 终端保留子程序原始字节；文件统一写入 UTF-8。
                if sys.stdout and hasattr(sys.stdout,"buffer"):
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                stream.write(decoder.decode(chunk))
                stream.flush()
            stream.write(decoder.decode(b"",final=True))
        code = process.wait()
    except KeyboardInterrupt:
        interrupted = True
        code = 130
        if process and process.poll() is None:
            if os.name=="nt":
                subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            else:
                import signal
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
    except OSError as exc:
        code = 127
        chunks.extend(str(exc).encode("utf-8"))
        log.write_text(str(exc),encoding="utf-8")
    finally:
        if process and process.stdout:
            process.stdout.close()
    output = decode_output(bytes(chunks))
    # 对 GBK 工具的输出重新以 UTF-8 保存，保留解析后文本。
    if "�" in log.read_text(encoding="utf-8") and len(chunks)<4*1024*1024:
        log.write_text(output,encoding="utf-8")
    if truncated:
        first=decode_output(b"\n".join(line for line in diagnostic_lines if line not in chunks))
        diagnostic=summarize(first+"\n"+output,code)
        # 节选不足以确定总错误数，不从节选中编造完整数量。
        import re
        if not re.search(r"\b\d+\s+(?:errors? generated|errors?\b|failed\b)",output[-65536:],re.I):
            diagnostic.errors=None
    else:
        diagnostic = summarize(output,code)
    event = {**base,"status":"cancelled" if interrupted else "success" if code==0 else "error",
             "detail":"用户中断了命令" if interrupted else diagnostic.summary,"location":diagnostic.location}
    if diagnostic.errors is not None:
        event["errors"] = diagnostic.errors
    emit(event)
    return code
