# 利用 GitLab API 提交/查询 MR。凭据应来自运行上下文，不要让模型先 login。

from __future__ import annotations

import os
import subprocess
from urllib.parse import unquote, urlparse

import gitlab
from agents import function_tool

_gl = None


def _client():
    if _gl is None:
        raise RuntimeError(
            "GitLab 未登录。不要向用户再要 PAT、不要把 token 写入仓库。"
            "请确认本次请求的 context 已注入凭据，或由入口在启动时登录。"
        )
    return _gl


def _is_git_repo(path: str) -> bool:
    git_dir = os.path.join(path, ".git")
    return os.path.isdir(git_dir) or os.path.isfile(git_dir)


def _origin_url(cwd: str) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    url = (result.stdout or "").strip()
    if result.returncode != 0 or not url:
        err = (result.stderr or "").strip()
        raise RuntimeError(
            f"无法从 {cwd} 读取 origin。"
            f" stderr: {err or '(empty)'}。"
            "请传 clone URL，例如 https://gitlab.example.com/sean/carryonobservation.git"
        )
    return url


def _project_path(repo: str) -> str:
    """把 clone URL、SSH、网页地址或 namespace/project 收成 GitLab 项目路径。"""
    text = (repo or "").strip()
    if not text:
        raise ValueError(
            "repo 不能为空。传 clone URL 或 namespace/project，"
            "例如 https://gitlab.example.com/sean/carryonobservation.git"
            " 或 sean/carryonobservation。"
        )

    expanded = os.path.expanduser(text)
    if os.path.isdir(expanded) and _is_git_repo(expanded):
        text = _origin_url(expanded)

    if text.startswith("git@"):
        _, _, rest = text.partition(":")
        path = rest
    elif "://" in text:
        parsed = urlparse(text)
        path = parsed.path or ""
    else:
        path = text

    path = unquote(path).strip()
    path = path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if "/-/" in path:
        path = path.split("/-/", 1)[0]
    path = path.strip("/")
    parts = [p for p in path.split("/") if p and p not in (".", "..")]
    if len(parts) < 2:
        raise ValueError(
            f"无法从 repo 解析 GitLab 项目路径: {repo!r}。"
            "需要 clone URL 或 namespace/project，例如 sean/carryonobservation。"
        )
    return "/".join(parts)


def _project(repo: str):
    return _client().projects.get(_project_path(repo))


@function_tool
def login_gitlab(url: str, pat: str):
    """仅当本次用户消息里明确给了 GitLab URL 和 PAT、且尚未登录时调用。
    不要在提交 MR 前习惯性调用。不要把 PAT 回显、不要写入文件。多用户场景应改由入口注入凭据。"""
    global _gl
    _gl = gitlab.Gitlab(url, private_token=pat)
    _gl.auth()
    return "login success"


@function_tool
def SubmitMR(
    repo: str,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str = "",
):
    """创建 Merge Request，返回 web_url。必须已经 git_push 成功。
    repo 用 clone URL 即可（https / git@ 都行），或 namespace/project，或本地仓库绝对路径（会读 origin）。
    不要向用户要数字 project_id。source_branch 是刚推的分支，target_branch 一般为 master 或 main。
    未登录时会报错，不要先瞎调 login_gitlab。只为本次交付文件开 MR。"""
    project = _project(repo)
    payload = {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "title": title,
    }
    if (description or "").strip():
        payload["description"] = description.strip()
    mr = project.mergerequests.create(payload)
    return mr.web_url


@function_tool
def QueryMR(repo: str, mr_id: int):
    """查询已有 MR，返回 web_url。只查询不创建。
    repo 同 SubmitMR：clone URL、namespace/project 或本地仓库路径。不要传数字 project_id。"""
    project = _project(repo)
    mr = project.mergerequests.get(mr_id)
    return mr.web_url
