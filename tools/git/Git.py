#完成基本git操作:clone, pull, add, commit, push, branch...

import subprocess
from agents import function_tool

@function_tool
def git_clone(url: str, branch: str):
    subprocess.run(["git", "clone", "-b", branch, url], check=True)
    print("clone success")
    return "clone success"

@function_tool
def create_branch(branch: str):
    subprocess.run(["git", "checkout", "-b", branch], check=True)
    return "create branch success"

@function_tool
def delete_branch(branch: str):
    subprocess.run(["git", "branch", "-D", branch], check=True)
    return "delete branch success"

@function_tool
def git_checkout(branch: str):
    subprocess.run(["git", "checkout", branch], check=True)
    return "checkout success"

@function_tool
def git_pull():
    subprocess.run(["git", "pull"], check=True)
    return "pull success"

@function_tool
def git_add(file: str):
    subprocess.run(["git", "add", file], check=True)
    return "add success"

@function_tool
def git_commit(message: str):
    subprocess.run(["git", "commit", "-m", message], check=True)
    return "commit success"

@function_tool
def git_push(branch: str):
    subprocess.run(["git", "push", "-u", "origin", branch], check=True)
    return "push success"

@function_tool
def git_branch(branch: str):
    subprocess.run(["git", "checkout", "-b", branch], check=True)
    return "branch success"

# def main():
#     url = "https://github.com/EchengX/CodingAgent.git"
#     branch = "main"
#     new_branch = "xmy"
#     # git_clone(url, branch)
#     git_checkout(new_branch)
#     # git_pull()
#     git_add(".")
#     git_commit("update")
#     git_push(new_branch)


# if __name__ == "__main__":
#     main()
