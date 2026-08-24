# 按照用户给出的路径（绝对或相对）查找、阅读、修改、创建、删除本地文件和文件夹
# glob/grep 未指定 path 时从仓库根搜
import fnmatch
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from agents import function_tool

from LLM.vision import compare_ui_images as compare_ui_images_raw
from LLM.vision import describe_ui_image

REPO_ROOT = "/Users/xxxx/work/CodingAgent"
WORKSPACE = os.path.join(REPO_ROOT, "Workspace")
# 需求图、预览截图都是验收用的临时产物，不能落到交付目录里
SCRATCH_DIR = os.path.join(tempfile.gettempdir(), "codingagent-scratch")
MEMORY_PATH = os.path.join(WORKSPACE, "memory.md")
MEMORY_EMPTY = "尚无交接"
MEMORY_MAX_CHARS = 3000
READ_DEFAULT_LINES = 100
READ_MAX_LINES = 200
TOOL_TEXT_LIMIT = 8000
COMMAND_STREAM_LIMIT = 2000
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


def _skipped(path: Path) -> bool:
    return bool(set(path.parts) & SKIP_DIR_NAMES)


def _search_root(path: str) -> Path:
    root = Path(_abs(path)) if (path or "").strip() else Path(REPO_ROOT)
    if not root.exists():
        raise FileNotFoundError(f"{root} 不存在。不要再猜这条路径，改用 glob 或 grep。")
    return root


def _glob_pattern(pattern: str) -> str:
    text = (pattern or "").strip().lstrip("./")
    if not text:
        raise ValueError("pattern is empty")
    if "**" not in text and "/" not in text:
        return "**/" + text
    return text


def _truncate_text(text: str, limit: int, hint: str) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n... [截断 {omitted} 字符；{hint}]"


def _clip_stream(text: str, limit: int = COMMAND_STREAM_LIMIT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - limit
    return (
        text[:half]
        + f"\n... [中间截断 {omitted} 字符] ...\n"
        + text[-half:]
    )


def read_memory_text() -> str:
    if not os.path.isfile(MEMORY_PATH):
        return MEMORY_EMPTY
    with open(MEMORY_PATH, "r", encoding="utf-8") as f:
        text = f.read().strip()
    return text if text else MEMORY_EMPTY


def clear_memory_file() -> None:
    if os.path.isfile(MEMORY_PATH):
        os.remove(MEMORY_PATH)


def clear_scratch_dir() -> None:
    shutil.rmtree(SCRATCH_DIR, ignore_errors=True)


def _scratch_path(prefix: str, suffix: str) -> str:
    os.makedirs(SCRATCH_DIR, exist_ok=True)
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", prefix).strip("-")[:40] or "shot"
    name = f"{slug}-{int(time.time() * 1000)}{suffix}"
    return os.path.join(SCRATCH_DIR, name)


@function_tool
def save_memory(content: str) -> str:
    """保存短任务索引，不得复制需求原文。必须严格包含以下四个标题：
    ## 输出目录
    ## 已读文件
    ## 缺失路径
    ## 最近命令
    未知或没有的项目写「无」。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("memory content is empty")
    required = ("## 输出目录", "## 已读文件", "## 缺失路径", "## 最近命令")
    missing = [heading for heading in required if heading not in text]
    if missing:
        raise ValueError(
            "memory 缺少固定标题: "
            + ", ".join(missing)
            + "。不要写需求原文，只保存路径和命令索引。"
        )
    if len(text) > MEMORY_MAX_CHARS:
        raise ValueError(
            f"memory 超过 {MEMORY_MAX_CHARS} 字符。请删除需求原文和解释，只保留索引。"
        )
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
        raise FileNotFoundError(f"{path} 不存在。不要再猜这条路径，改用 glob 或 grep。")
    if os.path.isdir(path):
        return os.listdir(path)
    return path

@function_tool
def file_info(path: str) -> str:
    # 查看文件或目录的类型和大小
    path = _abs(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path} 不存在。不要再猜这条路径，改用 glob 或 grep。")
    kind = "directory" if os.path.isdir(path) else "file"
    size = os.path.getsize(path)
    return f"path: {path}\ntype: {kind}\nsize: {size} bytes"


@function_tool
def glob(pattern: str, path: str = "", max_results: int = 50) -> str:
    """按文件名查找，返回绝对路径（新修改的在前）。
    path 必须是任务目录的绝对路径；禁止空 path 扫整个仓库（会扫到 vendor/feishu）。
    pattern 要具体，如 *.php、playground/*.php；禁止 * 或 **/*。
    任务已点名文件时直接 read，不要先 glob。结果被截断就缩小 path/pattern。"""
    if not (path or "").strip():
        raise ValueError("path 不能为空。传入任务输出目录的绝对路径，不要从仓库根搜。")
    root = _search_root(path)
    if not root.is_dir():
        raise FileNotFoundError(f"{root} 不是目录。不要再猜这条路径，改用 glob。")
    query = _glob_pattern(pattern)
    max_results = max(1, min(max_results, 100))
    hits = [
        p for p in root.glob(query)
        if p.is_file() and not _skipped(p)
    ]
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if not hits:
        return f"no files matching {query} under {root}"
    total = len(hits)
    shown = hits[:max_results]
    result = "\n".join(str(p) for p in shown)
    if total > len(shown):
        result += (
            f"\n... [仅显示 {len(shown)}/{total} 个结果；"
            "请缩小 path 或 pattern]"
        )
    return _truncate_text(result, TOOL_TEXT_LIMIT, "请缩小 path 或 pattern")


@function_tool
def grep(
    pattern: str,
    path: str = "",
    glob: str = "",
    files_only: bool = False,
    context: int = 0,
    max_results: int = 50,
) -> str:
    """按内容搜索。pattern 是正则（编译失败则当普通字符串）。
    path 必须是绝对目录或单个文件；禁止空 path 搜仓库根。
    用 glob 限定类型，如 *.php。查跳转搜 redirect( 或 ->redirect，不要搜「跳转」。
    files_only 只返回路径。context 默认 0，需要周围代码再加。"""
    if not (path or "").strip():
        raise ValueError("path 不能为空。传入任务目录或单个文件的绝对路径。")
    text = (pattern or "").strip()
    if not text:
        raise ValueError("pattern is empty")
    root = _search_root(path)
    max_results = max(1, min(max_results, 100))
    context = max(0, min(context, 5))
    name_filter = (glob or "").strip()
    try:
        matcher = re.compile(text, re.IGNORECASE)
    except re.error:
        matcher = re.compile(re.escape(text), re.IGNORECASE)

    files: list[Path] = [root] if root.is_file() else []
    if root.is_dir():
        for dirpath, dirs, filenames in os.walk(root):
            _prune_walk_dirs(dirs)
            for name in filenames:
                if name_filter and not fnmatch.fnmatch(name, name_filter):
                    continue
                files.append(Path(dirpath) / name)

    lines_out: list[str] = []
    seen_files: list[str] = []
    for file_path in files:
        if _skipped(file_path):
            continue
        try:
            if file_path.stat().st_size > 1_000_000:
                continue
            body = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, PermissionError):
            continue
        file_lines = body.splitlines()
        matched_idx = [
            i for i, line in enumerate(file_lines) if matcher.search(line)
        ]
        if not matched_idx:
            continue
        shown = str(file_path)
        if files_only:
            seen_files.append(shown)
            if len(seen_files) >= max_results:
                break
            continue
        for i in matched_idx:
            if len(lines_out) >= max_results:
                break
            lo = max(0, i - context)
            hi = min(len(file_lines), i + context + 1)
            for j in range(lo, hi):
                mark = ":" if j == i else "-"
                lines_out.append(f"{shown}:{j + 1}{mark} {file_lines[j]}")
            if context and i != matched_idx[-1]:
                lines_out.append("--")
        if len(lines_out) >= max_results:
            break

    if files_only:
        result = "\n".join(seen_files) if seen_files else f"no matches for {text} under {root}"
    else:
        result = "\n".join(lines_out) if lines_out else f"no matches for {text} under {root}"
    return _truncate_text(
        result,
        TOOL_TEXT_LIMIT,
        "请缩小 path、glob 或 max_results",
    )


@function_tool
def read(
    path: str,
    start_line: int = 1,
    max_lines: int = READ_DEFAULT_LINES,
) -> str:
    """读取文本文件，每行带行号。path 必须是绝对路径。默认 100 行、最多 200 行。
    任务已点名的文件直接 read，不要先 glob。图片用 read_image，不要 read 二进制。
    未读完时按返回里的 start_line 续读，不要从头再读，也不要用 run cat。"""
    path = _abs(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{path} 不存在。不要再猜这条路径，改用 glob 或 grep。")
    if start_line < 1:
        raise ValueError("start_line must be at least 1")
    max_lines = max(1, min(max_lines, READ_MAX_LINES))

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    start = start_line - 1
    selected = lines[start:start + max_lines]
    end_line = start + len(selected)
    numbered = "".join(
        f"{start_line + i:>6}|{line}"
        for i, line in enumerate(selected)
    )
    numbered = _truncate_text(
        numbered,
        TOOL_TEXT_LIMIT,
        f"请用 start_line={end_line + 1} 继续读取",
    )
    header = f"path: {path}\nlines: {start_line}-{end_line} of {len(lines)}\n"
    if end_line < len(lines):
        header += (
            f"文件共 {len(lines)} 行，本次未读完。"
            f"继续请设 start_line={end_line + 1}（offset），不要从头再读。\n"
        )
    return header + numbered

@function_tool
def write_file(path: str, content: str):
    """新建文件。不存在则创建（含父目录）；已存在必须改用 replace_in_file，禁止整文件覆盖。
    只写任务输出目录下的交付文件，如 playground/*.php。
    禁止写入 vendor/、observationdeck/、备份、改名前旧文件、截图和下载。"""
    path = _abs(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    existed = os.path.isfile(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return ("overwrite success: " if existed else "create success: ") + path


@function_tool
def replace_in_file(path: str, old: str, new: str) -> str:
    """把文件中唯一一次出现的 old 换成 new，返回改动处前后若干行。
    改已有文件、改文案、修报错都用这个，不要 write_file 整文件覆盖，不要新建无关文件。
    old 必须是文件里的原文且只出现一次；出现多次就扩大 old 的上下文再调，不要省略号。
    一次只改一处。成功后看返回的行号确认，不必立刻再 read 整文件。"""
    path = _abs(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{path} 不存在。不要再猜这条路径，改用 glob 或 grep。")
    if not old:
        raise ValueError("old is empty")

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    count = text.count(old)
    if count == 0:
        raise ValueError("old not found in " + path)
    if count > 1:
        raise ValueError(
            f"old 在 {path} 中出现 {count} 次，拒绝替换。"
            "请扩大 old 的上下文，使匹配在文件中唯一。"
        )

    index = text.find(old)

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
        f"replaced unique match at lines {start_line}-{end_line}\n"
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


# 查看当前目录
@function_tool
def list_dir(path: str):
    path = _abs(path)
    if not os.path.isdir(path):
        raise FileNotFoundError(f"{path} 不存在。不要再猜这条路径，改用 glob 或 grep。")
    return os.listdir(path)


# 递归查看目录
@function_tool
def list_dir_recursive(path: str):
    path = _abs(path)
    if not os.path.isdir(path):
        raise FileNotFoundError(f"{path} 不存在。不要再猜这条路径，改用 glob 或 grep。")
    items = []
    for root, dirs, files in os.walk(path):
        _prune_walk_dirs(dirs)
        for name in dirs:
            items.append(os.path.relpath(os.path.join(root, name), path) + "/")
        for name in files:
            items.append(os.path.relpath(os.path.join(root, name), path))
    return items

def _forbidden_run_command(command: str) -> str | None:
    """拦截会删 vendor、走 git/SSH 卡死的 shell。返回原因；允许则返回 None。"""
    text = (command or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if re.search(r"\bgit\b", lowered):
        return (
            "禁止用 run 执行 git。clone/pull/commit/push 用 git_* 工具；"
            "禁止 git submodule。vendor 未就绪时停止修复，不要自行拉子模块。"
        )
    if re.search(r"\brm\b", lowered) and re.search(
        r"vendor|observationdeck|\.git\b", lowered
    ):
        return (
            "禁止用 run 删除 vendor/、observationdeck/ 或 .git。"
            "vendor 未就绪请用户从完整观测台拷贝，不要 rm -rf。"
        )
    if "submodule" in lowered:
        return "禁止 git submodule。不要初始化或更新 vendor 子模块。"
    return None


def _run_command(command: str, cwd: str) -> str:
    text = (command or "").strip()
    if not text:
        raise ValueError("command is empty")
    blocked = _forbidden_run_command(text)
    if blocked:
        raise ValueError(blocked)
    cwd = _abs(cwd)
    if not os.path.isdir(cwd):
        raise FileNotFoundError(f"{cwd} 不存在。不要再猜这条路径，改用 glob 或 grep。")
    result = subprocess.run(
        text,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
        executable="/bin/zsh",
    )
    stdout = _clip_stream(result.stdout)
    stderr = _clip_stream(result.stderr)
    return (
        f"returncode: {result.returncode}\n"
        f"stdout:\n{stdout}\n"
        f"stderr:\n{stderr}"
    )


@function_tool
def run(command: str, cwd: str = WORKSPACE) -> str:
    """在 cwd 下用 shell 跑检查/预览等命令。cwd 必须是绝对目录，不要用默认 Workspace 除非任务就在那。
    检查用项目 README 里的命令，以 returncode 为准，非 0 只改报错处再跑。
    禁止用 run 代替专用工具：读文件用 read，搜索用 glob/grep，改文件用 replace_in_file，
    git 用 git_*，下载用 fetch_url，截图用 capture_screenshot。不要后台挂起 serve 当检查。
    禁止 git / submodule / rm vendor；vendor 缺失时不要自行修复环境。"""
    return _run_command(command, cwd)


@function_tool
def capture_screenshot(
    url: str,
    width: int = 1280,
    height: int = 800,
    click_text: str = "",
) -> str:
    """截取代码预览页，保存到临时目录并返回路径。不要指定交付路径，不要把 png 拷进 playground。
    截页面：不传 click_text。截弹窗：click_text 必须是按钮可见原文，看返回是否 dialog: 已打开。
    若 dialog: 未检测到弹窗，这张图不能拿去 compare_ui_images。每个弹窗按钮单独截一次。
    url 用 README 里的预览/verify 地址，不要截需求文档网站。写完并检查通过后再截。"""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", "file"):
        raise ValueError("url must be http, https or file")
    click_text = click_text.strip()
    dest = _scratch_path(click_text or os.path.basename(parsed.path) or "page", ".png")
    width = max(320, min(int(width), 3840))
    height = max(240, min(int(height), 2160))

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "未安装 Playwright。先 pip install playwright && playwright install chromium"
        ) from exc

    with sync_playwright() as playwright:
        chrome_path = os.environ.get("PLAYWRIGHT_CHROME_PATH", "").strip()
        if not chrome_path and os.path.isfile(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        ):
            chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        try:
            launch_options = {"headless": True}
            if chrome_path:
                launch_options["executable_path"] = chrome_path
            browser = playwright.chromium.launch(**launch_options)
        except PlaywrightError as exc:
            raise RuntimeError(
                "没有可用的 Chromium。请执行 playwright install chromium，"
                "或设置 PLAYWRIGHT_CHROME_PATH。"
            ) from exc
        dialog_note = ""
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1500)
            if click_text:
                page.get_by_text(click_text, exact=True).first.click(timeout=10_000)
                page.wait_for_timeout(800)
                dialog_note = _dialog_state(page)
            page.screenshot(path=dest, full_page=True)
        finally:
            browser.close()
    lines = [
        f"screenshot success: {dest}",
        f"url: {url}",
        f"viewport: {width}x{height}",
        f"clicked: {click_text or '无'}",
        "临时文件，验收用完即弃，禁止拷进交付目录。",
    ]
    if dialog_note:
        lines.insert(4, dialog_note)
    return "\n".join(lines)


DIALOG_SELECTOR = (
    "dialog, [role=dialog], .modal, .el-dialog, .ant-modal, "
    ".layui-layer, .layer-dialog, .weui-dialog"
)


def _dialog_state(page) -> str:
    """点开按钮后确认弹窗真的出现了，避免拿着列表页截图冒充弹窗验收。"""
    try:
        visible = page.locator(DIALOG_SELECTOR).filter(visible=True).count()
    except Exception:
        return "dialog: 无法判定，请人工确认截图里是否有弹窗"
    if visible:
        return f"dialog: 已打开（可见弹窗容器 {visible} 个）"
    return (
        "dialog: 未检测到弹窗。这张图不能当弹窗验收，"
        "先确认按钮文案和触发方式是否正确。"
    )


def _download_url_bytes(url: str, limit: int = 2_000_000) -> tuple[bytes, str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("url must be http or https")
    request = Request(url, headers={"User-Agent": "CodingAgent/1.0"})
    with urlopen(request, timeout=20) as response:
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        body = response.read(limit)
        final_url = response.geturl()
    return body, content_type, final_url


def _looks_like_image(body: bytes) -> bool:
    return body.startswith((b"\x89PNG", b"\xff\xd8", b"GIF8", b"RIFF"))


def _read_image_source(path: str, url: str, label: str) -> tuple[bytes, str]:
    path = (path or "").strip()
    url = (url or "").strip()
    if not path and not url:
        raise ValueError(f"{label} needs path or url")
    if path and url:
        raise ValueError(f"{label} 只能传 path 或 url 其中一个")

    if url:
        body, content_type, final_url = _download_url_bytes(url)
        if content_type and not content_type.startswith("image/") and not _looks_like_image(body):
            raise ValueError(
                f"{label} url is not an image (content-type={content_type or 'unknown'}). "
                "先用 fetch_url 找到实际 png/jpg。"
            )
        return body, final_url

    dest = _abs(path)
    if not os.path.isfile(dest):
        raise FileNotFoundError(
            f"{dest} 不存在。不要猜路径，先用 glob/grep 找图，"
            "或用 fetch_url 下载后再识图。"
        )
    with open(dest, "rb") as f:
        body = f.read(2_000_000)
    if not _looks_like_image(body):
        raise ValueError(f"{dest} 不是支持的 png/jpg/gif/webp 图片")
    return body, dest


@function_tool
def fetch_url(url: str) -> str:
    """读取 http/https。文本返回正文；图片等二进制只存临时目录，返回 saved 路径，禁止拷进交付目录。
    需求是网页时先调这个。正文里若有多张图，把全部图片 URL 找出来，每张再 fetch_url 或 read_image，
    禁止只处理第一张。本模型看不见像素，二进制必须再调 read_image。"""
    url = (url or "").strip()
    parsed = urlparse(url)
    body, content_type, final_url = _download_url_bytes(url)

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

    name = os.path.basename(parsed.path) or "download"
    stem, ext = os.path.splitext(name)
    dest = _scratch_path(stem, ext or ".bin")
    with open(dest, "wb") as f:
        f.write(body)
    return (
        f"url: {final_url}\n"
        f"content-type: {content_type or 'unknown'}\n"
        f"saved: {dest}\n"
        f"size: {len(body)} bytes\n"
        "临时文件，禁止拷进交付目录。this coding model cannot see pixels; "
        f"call read_image with path={dest} to get a text inventory."
    )


@function_tool
def read_image(path: str = "", url: str = "") -> str:
    """分析一张图里的一个页面或一个弹窗，返回布局、控件、文案和需求标注。path 与 url 只能传一个。
    每个页面、每个弹窗各调一次，禁止把多张图合并成总表。
    返回「需要拆图」说明图里有多个视图：把每个视图登记进清单，去文档找该视图的单独图或正文补规格，禁止只做主视图。
    「需求标注」只指导实现（权限、跳转、口径），禁止写进页面 Hint/summary/脚注；界面内文案才渲染。"""
    body, source = _read_image_source(path, url, "read_image")
    inventory = describe_ui_image(body)
    if len(inventory) > TOOL_TEXT_LIMIT:
        inventory = inventory[:TOOL_TEXT_LIMIT] + f"\n[truncated at {TOOL_TEXT_LIMIT} characters]"
    return f"source: {source}\n{inventory}"


@function_tool
def compare_ui_images(
    reference_path: str = "",
    preview_path: str = "",
    reference_url: str = "",
    preview_url: str = "",
) -> str:
    """比较一张需求原图和一张代码预览图。reference 是需求图，preview 是 capture_screenshot 的结果；每次一对。
    原图是弹窗时 preview 必须是点开后的截图；返回弹窗未打开则不要用列表页凑数。
    只修 P0/P1：改代码后重新截再比。预览里出现需求标注/说明文字是 P0 多余项，要删掉而不是补上。"""
    reference, reference_source = _read_image_source(
        reference_path, reference_url, "reference"
    )
    preview, preview_source = _read_image_source(
        preview_path, preview_url, "preview"
    )
    diff = compare_ui_images_raw(reference, preview)
    if len(diff) > TOOL_TEXT_LIMIT:
        diff = diff[:TOOL_TEXT_LIMIT] + f"\n[truncated at {TOOL_TEXT_LIMIT} characters]"
    return (
        f"reference: {reference_source}\n"
        f"preview: {preview_source}\n"
        f"{diff}"
    )

