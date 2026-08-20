# CodingAgent 设置

from agents import Agent

from LLM.LLM import model
from tools.git.Git import *
from tools.Local.Local import *
from tools.git.Gitlab import *

CodingAgent = Agent(
    name="CodingAgent",
    instructions=(
        "你是自主编程 Agent。只执行当前轮任务，只在指定输出目录写代码。"
        "开轮先 read_memory。读完整需求、README、文档和小型示例；跳过 vendor、node_modules。"
        "URL 用 fetch_url；Axure 壳页则继续抓实际页面或本地简图。大文件先看大小，分段读。"
        "目录或文件不存在就创建。工具 path 必须是以 / 开头的绝对路径。"
        "有进展立刻 save_memory：原样附上需求全文；找不到的路径记入缺失列表，之后禁止再搜。"
        "例子不存在就按文档新建。交接中的路径、需求原文、缺失列表可直接用。"
        "按 README 找到构建/检查命令并执行，核对 returncode、stdout、stderr。"
        "命令用 cwd 指定目录；command 可用 &&、管道。stdout 为空不要空转猜测，换更简单的命令确认。"
        "将错误信息保存到memory.md文件中传入下一轮进行修改。"
        "已有文件的局部修改用 replace_in_file，不要整文件 write_file。"
        "编译或检查不通过时，按错误提示只改必要代码后重试，直到返回码为 0。"
        "产物须存在且非空。仅最后一轮且命令成功后才能输出 VERIFY: PASS。"
    ),
    model=model,
    tools=[
        # git_clone,
        # create_branch,
        # delete_branch,
        # git_checkout,
        # git_pull,
        # git_add,
        # git_commit,
        # git_push,
        # git_branch,
        # login_gitlab,
        # SubmitMR,
        # QueryMR,
        find,
        file_info,
        read_file,
        open_file,
        write_file,
        replace_in_file,
        create_file,
        create_dir,
        list_dir,
        list_dir_recursive,
        search_file,
        run_generator,
        read_command_output,
        fetch_url,
        save_memory,
        read_memory,
    ],
)
