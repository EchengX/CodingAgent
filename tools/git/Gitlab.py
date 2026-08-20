#利用url和PAT登录远程gitlab仓库, 提交MR请求，查询MR状态

import gitlab
from agents import function_tool
_gl = None
@function_tool
def login_gitlab(url: str, pat: str):
    global _gl
    _gl = gitlab.Gitlab(url, private_token=pat)
    _gl.auth()
    return "login success"

@function_tool
def SubmitMR(project_id: int, source_branch: str, target_branch: str, title: str, description: str):
    project = _gl.projects.get(project_id)
    mr = project.mergerequests.create({
        'source_branch': source_branch,
        'target_branch': target_branch,
        'title': title
    })
    return mr.web_url

@function_tool
def QueryMR(project_id: int, mr_id: int):
    project = _gl.projects.get(project_id)
    mr = project.mergerequests.get(mr_id)
    return mr.web_url
