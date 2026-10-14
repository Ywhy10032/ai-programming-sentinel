"""本地规则提取编译信息；不声称调用了 AI，不上传日志。"""
from __future__ import annotations

import ntpath
import re
from dataclasses import dataclass

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
LOCATION = re.compile(r"(?P<file>(?:[A-Za-z]:)?[^\s\"<>|]*?\.(?:c|cc|cpp|cxx|h|hpp|py|rs|cs|ts|tsx|js|java))(?::(?P<line>\d+)(?::\d+)?|\((?P<msline>\d+)(?:,\d+)?\))", re.I)
ERROR_LINE = re.compile(r"(?:\bfatal error\b|\berror(?:\s+[A-Z]\d+|\[[A-Z]+\d+\]|\s*#\d+)?\s*:|\bundefined reference\b|\bFAILED\b|\bAssertionError\b|\bModuleNotFoundError\b|\bSyntaxError\b|\bTraceback\b|\bpanic\b)", re.I)


@dataclass
class Diagnostic:
    errors: int | None
    summary: str
    location: str
    first_error: str


def summarize(output: str, exit_code: int) -> Diagnostic:
    lines = ANSI.sub("", output).splitlines()
    candidates = [line.strip() for line in lines if ERROR_LINE.search(line)]
    primary = next((x for x in candidates if "Traceback" not in x and not re.match(r"\s*\d+ failed", x)), candidates[0] if candidates else "")
    errors = None
    # 编译器明确报告总数时使用总数，否则计数具体诊断行，避免伪造 0。
    count = re.search(r"\b(\d+)\s+(?:errors? generated|errors?\b|failed\b)", "\n".join(lines[-60:]), re.I)
    if count:
        errors = int(count.group(1))
    elif candidates:
        concrete = [x for x in candidates if re.search(r"\berror(?:\s+[A-Z]\d+)?\s*:|undefined reference|AssertionError|ModuleNotFoundError|SyntaxError", x, re.I)]
        errors = len(concrete) or None
    location = ""
    for line in candidates + lines:
        match = LOCATION.search(line)
        if match:
            location = ntpath.basename(match["file"]) + ":" + (match["line"] or match["msline"])
            break
    if not location:
        match=re.search(r'File "([^"\n]+\.py)", line (\d+)',"\n".join(lines))
        if match:
            location=ntpath.basename(match[1])+":"+match[2]
    text = "\n".join(candidates).lower()
    rules = [
        (r"no such file|cannot open (?:include|source)|file not found", "缺少文件或头文件"),
        (r"undefined reference|unresolved external|lnk2019|lnk2001", "函数或符号未定义"),
        (r"undeclared|not declared|identifier.*undefined", "变量或名称未声明"),
        (r"modulenotfounderror|no module named", "缺少 Python 依赖"),
        (r"assertionerror|assertion.*failed|\bfailed\b", "测试未通过"),
        (r"syntaxerror|expected.*before|expected.*;", "语法错误"),
        (r"overflowed|will not fit|region.*overflow", "存储空间不足"),
        (r"permission denied|access is denied", "文件访问被拒绝"),
    ]
    summary = next((label for pattern,label in rules if re.search(pattern,text)), "")
    if exit_code == 0:
        return Diagnostic(0, "全部检查通过", "", "")
    if not summary:
        summary = "请查看完整日志" if not primary else primary[:70]
    return Diagnostic(errors, summary, location, primary[:500])


def is_build_command(command: str) -> str | None:
    import shlex
    result=None
    in_here=False
    for line in command.splitlines():
        line=line.strip()
        if line.startswith(("@'",'@"')):
            in_here=True
            continue
        if in_here:
            if line.startswith(("'@",'"@')):in_here=False
            continue
        try:parts=shlex.split(line,posix=False)
        except ValueError:continue
        if parts and parts[0]=="&":parts=parts[1:]
        if not parts:continue
        executable=ntpath.basename(parts[0].strip("\"'")).lower().removesuffix(".exe")
        args=[p.strip("\"'").lower() for p in parts[1:]]
        if executable in ["pytest","ctest"] or executable in ["python","python3","py"] and any(args[i:i+2] in [["-m","pytest"],["-m","unittest"]] for i in range(len(args))):
            result="test"
        elif executable in ["cargo","dotnet","npm"] and (args[:1]==["test"] or args[:2]==["run","test"]):result="test"
        elif executable=="cmake" and "--build" in args:result="build"
        elif executable in ["ninja","make","mingw32-make","msbuild","iarbuild"]:result="build"
        elif re.fullmatch(r"(?:[\w-]+-)?(?:gcc|g\+\+|clang|clang\+\+)",executable):result="build"
        elif executable in ["cargo","dotnet"] and args[:1] in [["build"],["publish"]]:result="build"
        elif executable=="npm" and args[:2]==["run","build"]:result="build"
    return result
    return None
