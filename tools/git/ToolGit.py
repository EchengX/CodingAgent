# 完成基本 git 操作: clone, pull, add, commit, push, branch...
# 所有命令必须在目标仓库的 cwd 下执行，禁止用 run 代替。

import os
import subprocess

from agents import function_tool


def _git_cwd(cwd: str) -> str:
    text = (cwd or "").strip()
    if not text:
        raise ValueError("cwd 必须是绝对路径：clone 时为父目录，其余操作为 git 仓库根。")
    text = os.path.expanduser(text)
    if not os.path.isabs(text):
        raise ValueError(f"cwd 必须是绝对路径，收到: {text}")
    if not os.path.isdir(text):
        raise FileNotFoundError(f"{text} 不是目录")
    return text


def _git_run(args: list[str], cwd: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (cwd={cwd}, code={result.returncode})\n"
            f"stdout: {out or '(empty)'}\n"
            f"stderr: {err or '(empty)'}"
        )
    return out or "ok"


def _create_branch_impl(branch: str, cwd: str) -> str:
    root = _git_cwd(cwd)
    _git_run(["checkout", "-b", branch], root)
    return f"create branch success: {branch} in {root}"


def _repo_name_from_url(url: str) -> str:
    name = os.path.basename((url or "").rstrip("/"))
    if name.endswith(".git"):
        name = name[:-4]
    if not name:
        raise ValueError("无法从 url 解析仓库目录名")
    return name


def _is_git_repo(path: str) -> bool:
    git_dir = os.path.join(path, ".git")
    return os.path.isdir(git_dir) or os.path.isfile(git_dir)


@function_tool
def git_clone(url: str, branch: str, cwd: str):
    """把远程仓 clone 到 cwd 下。cwd 是父目录的绝对路径，不是仓内路径。
    目标目录已存在且是 git 仓库时改为 pull，不报错、不覆盖。
    需要认证时由调用方把凭据放在 url 或环境里，不要用 run git clone，不要把 PAT 写进对话。"""
    parent = _git_cwd(cwd)
    dest = os.path.join(parent, _repo_name_from_url(url))
    if os.path.exists(dest):
        if not _is_git_repo(dest):
            raise ValueError(
                f"{dest} 已存在但不是 git 仓库，拒绝覆盖。请用户处理该目录，或换一个 cwd。"
            )
        out = _git_run(["pull"], dest)
        return (
            f"already exists, pulled: {dest}\n"
            f"branch requested: {branch}\n"
            f"{out}"
        )
    _git_run(["clone", "-b", branch, url], parent)
    return f"clone success: {url} -> {dest} (branch {branch})"


@function_tool
def create_branch(branch: str, cwd: str):
    """在 cwd 这个 git 仓库里从当前分支新建并切换到 branch。cwd 必须是仓库根绝对路径。
    有远程仓、开始写代码前调用。"""
    return _create_branch_impl(branch, cwd)


@function_tool
def delete_branch(branch: str, cwd: str):
    """删除 cwd 仓库的本地分支。不能删当前分支，不能删 main/master，除非用户明确要求。"""
    name = (branch or "").strip()
    if name in {"main", "master"}:
        raise ValueError("拒绝删除 main/master，除非用户改用其他方式明确要求")
    root = _git_cwd(cwd)
    _git_run(["branch", "-D", name], root)
    return f"delete branch success: {name}"


@function_tool
def git_checkout(branch: str, cwd: str):
    """切换到 cwd 仓库里已存在的分支。分支不存在时用 create_branch，不要 checkout -b。"""
    root = _git_cwd(cwd)
    _git_run(["checkout", branch], root)
    return f"checkout success: {branch}"


@function_tool
def git_pull(cwd: str):
    """在 cwd 仓库执行 git pull。冲突时把报错原文交给调用方，不要 force。"""
    root = _git_cwd(cwd)
    out = _git_run(["pull"], root)
    return f"pull success\n{out}"


@function_tool
def git_add(file: str, cwd: str):
    """把 file 加入暂存区。cwd 是仓库根；file 相对仓根，如 playground/Foo.php。
    只 add 任务交付文件。禁止 add observationdeck/、vendor/、.env、截图、下载。"""
    root = _git_cwd(cwd)
    target = (file or "").strip()
    if not target:
        raise ValueError("file is empty")
    lowered = target.replace("\\", "/").lower()
    blocked = ("observationdeck/", "vendor/", ".env", "codingagent-scratch")
    if any(part in lowered for part in blocked) or lowered.endswith(".png"):
        raise ValueError(f"拒绝 add {target}：不是交付文件。只 add playground 等任务产物。")
    _git_run(["add", "--", target], root)
    return f"add success: {target}"


@function_tool
def git_commit(message: str, cwd: str):
    """提交 cwd 仓库已暂存的改动。必须先 git_add。message 写原因，不要空。
    hook 失败或没有改动时返回错误，不要加 --no-verify。"""
    text = (message or "").strip()
    if not text:
        raise ValueError("commit message is empty")
    root = _git_cwd(cwd)
    out = _git_run(["commit", "-m", text], root)
    return f"commit success\n{out}"


@function_tool
def git_push(branch: str, cwd: str):
    """把 cwd 仓库的 branch 推到 origin（-u）。必须已经 commit。
    认证失败说明当前用户没有可用的 GitLab 凭据，不要改用 run，不要把 PAT 打进命令。"""
    root = _git_cwd(cwd)
    out = _git_run(["push", "-u", "origin", branch], root)
    return f"push success: {branch}\n{out}"
