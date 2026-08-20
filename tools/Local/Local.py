# 按照用户给出的路径（绝对或相对）查找、阅读、修改、创建、删除本地文件和文件夹
# 未提供具体路径则默认/Users/xxxx/work/CodingAgent/Workspace
import os
import subprocess
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from agents import function_tool

WORKSPACE = "/Users/xxxx/work/CodingAgent/Workspace"
MEMORY_PATH = os.path.join(WORKSPACE, "memory.md")
MEMORY_EMPTY = "尚无交接"
MEMORY_MAX_CHARS = 12000
SKIP_DIR_NAMES = {
    "vendor",
    "node_modules",
    ".git",
    ".idea",
    ".svn",
    "__pycache__",
}

def _abs(path: str) -> str:
    text = (path or "").strip().strip("'\"")
    if not text:
        raise ValueError("path is empty")

    text = text.replace("\\", "/")
    if text.startswith("file://"):
        text = text[7:]
    text = os.path.expandvars(text)
    text = os.path.expanduser(text)
    text = text.strip()

    while text.startswith("./"):
        text = text[2:].lstrip()

    # "Users/xxxx/..." 或被 cwd 拼进去后的 ".../Users/xxxx/..."
    nested_home = text.find(" /Users/")
    if nested_home >= 0:
        text = text[nested_home + 1 :]
    first_users = text.find("/Users/")
    second_users = text.find("/Users/", first_users + 1) if first_users >= 0 else -1
    if second_users > first_users:
        text = text[second_users:]
    elif text.startswith("Users/"):
        text = "/" + text

    text = text.strip()
    if not os.path.isabs(text):
        text = os.path.abspath(text)
    return os.path.normpath(text)


def _prune_walk_dirs(dirs):
    dirs[:] = [name for name in dirs if name not in SKIP_DIR_NAMES]


def read_memory_text() -> str:
    if not os.path.isfile(MEMORY_PATH):
        return MEMORY_EMPTY
    with open(MEMORY_PATH, "r", encoding="utf-8") as f:
        text = f.read().strip()
    return text if text else MEMORY_EMPTY


def clear_memory_file() -> None:
    if os.path.isfile(MEMORY_PATH):
        os.remove(MEMORY_PATH)


@function_tool
def save_memory(content: str) -> str:
    """覆盖写入本轮交接。必须含需求原文和缺失文件列表，以及路径/要点。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("memory content is empty")
    if len(text) > MEMORY_MAX_CHARS:
        text = text[:MEMORY_MAX_CHARS] + "\n... [truncated]"
    parent = os.path.dirname(MEMORY_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(MEMORY_PATH, "w", encoding="utf-8") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")
    return "save memory success: " + MEMORY_PATH


@function_tool
def read_memory() -> str:
    """读取轮次交接备忘录。文件不存在或为空时返回「尚无交接」。"""
    return read_memory_text()

@function_tool
def find(path: str):
    path = _abs(path)
    if not os.path.exists(path):
        return "not found: " + path
    if os.path.isdir(path):
        return os.listdir(path)
    return path

@function_tool
def file_info(path: str) -> str:
    # 查看文件或目录的类型和大小
    path = _abs(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    kind = "directory" if os.path.isdir(path) else "file"
    size = os.path.getsize(path)
    return f"path: {path}\ntype: {kind}\nsize: {size} bytes"


def _read_file_impl(path: str, start_line: int = 1, max_lines: int = 200) -> str:
    path = _abs(path)
    if start_line < 1:
        raise ValueError("start_line must be at least 1")
    max_lines = max(1, min(max_lines, 500))

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    start = start_line - 1
    selected = lines[start:start + max_lines]
    end_line = start + len(selected)
    content = "".join(selected)
    if len(content) > 20_000:
        content = content[:20_000] + "\n[content truncated at 20000 characters]"
    return (
        f"path: {path}\n"
        f"lines: {start_line}-{end_line} of {len(lines)}\n"
        f"{content}"
    )


@function_tool
def read_file(path: str, start_line: int = 1, max_lines: int = 200) -> str:
    """分段读取文本文件，行号从 1 开始，每次最多 500 行。"""
    return _read_file_impl(path, start_line, max_lines)


@function_tool
def open_file(path: str, start_line: int = 1, max_lines: int = 200) -> str:
    """打开并读取文本文件，与 read_file 相同。"""
    return _read_file_impl(path, start_line, max_lines)

@function_tool
def write_file(path: str, content: str):
    path = _abs(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return "write success: " + path


@function_tool
def replace_in_file(path: str, old: str, new: str) -> str:
    """只替换文件中第一次出现的 old 为 new，返回改动处前后若干行。"""
    path = _abs(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    if not old:
        raise ValueError("old is empty")

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    index = text.find(old)
    if index < 0:
        raise ValueError("old not found in " + path)

    updated = text[:index] + new + text[index + len(old) :]
    with open(path, "w", encoding="utf-8") as f:
        f.write(updated)

    start_line = updated[:index].count("\n") + 1
    end_line = updated[: index + len(new)].count("\n") + 1
    lines = updated.splitlines(keepends=True)
    ctx_start = max(1, start_line - 3)
    ctx_end = min(len(lines), end_line + 3)
    snippet = "".join(lines[ctx_start - 1 : ctx_end])
    return (
        f"replace success: {path}\n"
        f"replaced first match at lines {start_line}-{end_line}\n"
        f"context lines {ctx_start}-{ctx_end} of {len(lines)}\n"
        f"{snippet}"
    )


@function_tool
def create_file(path: str, content: str = ""):
    path = _abs(path)
    if os.path.exists(path):
        raise FileExistsError(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:
        f.write(content)
    return "create file success: " + path

@function_tool
def create_dir(path: str):
    path = _abs(path)
    os.makedirs(path, exist_ok=False)
    return "create dir success: " + path

# 查看当前目录
@function_tool
def list_dir(path: str):
    path = _abs(path)
    if not os.path.isdir(path):
        raise FileNotFoundError(path)
    return os.listdir(path)


# 递归查看目录
@function_tool
def list_dir_recursive(path: str):
    path = _abs(path)
    if not os.path.isdir(path):
        raise FileNotFoundError(path)
    items = []
    for root, dirs, files in os.walk(path):
        _prune_walk_dirs(dirs)
        for name in dirs:
            items.append(os.path.relpath(os.path.join(root, name), path) + "/")
        for name in files:
            items.append(os.path.relpath(os.path.join(root, name), path))
    return items

@function_tool
def search_file(path: str, keyword: str, max_results: int = 50):
    # 递归搜索文本文件内容，返回文件、行号和匹配行。
    path = _abs(path)
    if not os.path.isdir(path):
        raise FileNotFoundError(path)
    if not keyword:
        raise ValueError("keyword is empty")

    max_results = max(1, min(max_results, 200))
    results = []
    for root, dirs, files in os.walk(path):
        _prune_walk_dirs(dirs)
        for name in files:
            file_path = os.path.join(root, name)
            if os.path.getsize(file_path) > 1_000_000:
                continue
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line_number, line in enumerate(f, 1):
                        if keyword.lower() in line.lower():
                            relative = os.path.relpath(file_path, path)
                            results.append(
                                f"{relative}:{line_number}: {line.rstrip()}"
                            )
                            if len(results) >= max_results:
                                return results
            except (UnicodeDecodeError, PermissionError):
                continue
    return results

def _run_command(command: str, cwd: str) -> str:
    text = (command or "").strip()
    if not text:
        raise ValueError("command is empty")
    cwd = _abs(cwd)
    result = subprocess.run(
        text,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
        executable="/bin/zsh",
    )
    return (
        f"returncode: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@function_tool
def run_generator(command: str, cwd: str = WORKSPACE) -> str:
    """在 cwd 下用 shell 运行命令。支持 &&、管道和重定向。返回 returncode、stdout、stderr。"""
    return _run_command(command, cwd)

@function_tool
def fetch_url(url: str, save_path: str = "") -> str:
    """读取 http/https URL。文本返回正文（截断）；图片等二进制保存到本地并返回路径。"""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("url must be http or https")

    request = Request(url, headers={"User-Agent": "CodingAgent/1.0"})
    with urlopen(request, timeout=20) as response:
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        body = response.read(2_000_000)
        final_url = response.geturl()

    text_types = (
        "text/",
        "application/json",
        "application/javascript",
        "application/xml",
        "application/xhtml+xml",
    )
    is_text = (not content_type) or any(content_type.startswith(t) or content_type == t.rstrip("/") for t in text_types)

    if is_text and not content_type.startswith("image/"):
        text = body.decode("utf-8", errors="replace")
        if len(text) > 20_000:
            text = text[:20_000] + "\n[content truncated at 20000 characters]"
        return (
            f"url: {final_url}\n"
            f"content-type: {content_type or 'unknown'}\n"
            f"size: {len(body)} bytes\n"
            f"{text}"
        )

    dest = save_path.strip()
    if not dest:
        name = os.path.basename(parsed.path) or "download.bin"
        dest = os.path.join(WORKSPACE, "downloads", name)
    dest = _abs(dest)
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(body)
    return (
        f"url: {final_url}\n"
        f"content-type: {content_type or 'unknown'}\n"
        f"saved: {dest}\n"
        f"size: {len(body)} bytes\n"
        "binary content was saved to disk; this model cannot view images."
    )


@function_tool
def read_command_output(command: str, cwd: str = WORKSPACE) -> str:
    """运行只读检查命令。支持 &&、管道和重定向。返回 returncode、stdout、stderr。"""
    return _run_command(command, cwd)