#利用url和PAT登录远程gitlab仓库, 提交MR请求，查询MR状态

import gitlab

def login_gitlab(url: str, pat: str):
    gl = gitlab.Gitlab(url, private_token=pat)
    gl.auth()
    return gl


def SubmitMR(gl: gitlab.Gitlab, project_id: int, source_branch: str, target_branch: str, title: str, description: str):
    project = gl.projects.get(project_id)
    mr = project.mergerequests.create({
        'source_branch': source_branch,
        'target_branch': target_branch,
        'title': title
    })
    return mr

def QueryMR(gl: gitlab.Gitlab, project_id: int, mr_id: int):
    project = gl.projects.get(project_id)
    mr = project.mergerequests.get(mr_id)
    return mr
