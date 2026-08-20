# Agent启动入口

import asyncio
import sys

from agents import (
    ItemHelpers,
    RawResponsesStreamEvent,
    Runner,
    RunItemStreamEvent,
)
from agents.exceptions import MaxTurnsExceeded
from agents.items import MessageOutputItem, ReasoningItem, ToolCallItem, ToolCallOutputItem

from CodingAgent import CodingAgent
from tools.Local.Local import clear_memory_file, read_memory_text

ROUNDS = [
    "定位需求文档、背景目录和目标工作目录；完整阅读需求，整理必须实现、禁止出现和完成标准。需求里的 URL 用 fetch_url 读取（或读任务旁的本地简图）。如果有背景目录，阅读 README、规范和相关示例，不得虚构 API。跳过 vendor、node_modules。",
    "根据需求和背景资料按需求创建文件/文件夹，编写代码到目标工作目录。没有具体错误时不要无理由重写已有代码。",
    "运行项目实际的解析、编译或构建命令；不通过则按错误提示修改代码并重试，直到返回码为 0。产物存在且非空后，最后一行输出 VERIFY: PASS。",
]

TOOL_OUTPUT_LIMIT = 4000


def _out(text: str = "", end: str = "\n") -> None:
    print(text, end=end, flush=True)


def _clip(text: object, limit: int = TOOL_OUTPUT_LIMIT) -> str:
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... [truncated {len(value) - limit} chars]"


def _item_attr(item: object, name: str) -> object:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _reasoning_text(item: ReasoningItem) -> str:
    raw = item.raw_item
    parts: list[str] = []
    content = _item_attr(raw, "content") or []
    for part in content:
        text = _item_attr(part, "text")
        if text:
            parts.append(str(text))
    summary = _item_attr(raw, "summary") or []
    for part in summary:
        text = _item_attr(part, "text")
        if text:
            parts.append(str(text))
    return "\n".join(parts)


async def run_round(prompt: str) -> str:
    result = Runner.run_streamed(CodingAgent, prompt, max_turns=30)
    streamed_text = False
    streamed_reason = False

    async for event in result.stream_events():
        if isinstance(event, RawResponsesStreamEvent):
            data = event.data
            event_type = getattr(data, "type", "")
            delta = getattr(data, "delta", None) or ""
            if event_type == "response.created":
                streamed_text = False
                streamed_reason = False
            elif event_type in (
                "response.reasoning_text.delta",
                "response.reasoning_summary_text.delta",
            ):
                if not streamed_reason:
                    _out("\n----- 模型思考 -----")
                    streamed_reason = True
                _out(delta, end="")
            elif event_type == "response.output_text.delta":
                if not streamed_text:
                    _out("\n----- 模型输出 -----")
                    streamed_text = True
                _out(delta, end="")
            continue

        if not isinstance(event, RunItemStreamEvent):
            continue

        if event.name == "reasoning_item_created" and not streamed_reason:
            text = _reasoning_text(event.item) if isinstance(event.item, ReasoningItem) else ""
            if text:
                _out("\n----- 模型思考 -----")
                _out(text)

        elif event.name == "message_output_created" and not streamed_text:
            if isinstance(event.item, MessageOutputItem):
                text = ItemHelpers.text_message_output(event.item)
                if text:
                    _out("\n----- 模型输出 -----")
                    _out(text)

        elif event.name == "tool_called" and isinstance(event.item, ToolCallItem):
            raw = event.item.raw_item
            name = event.item.tool_name or _item_attr(raw, "name") or "unknown"
            arguments = _item_attr(raw, "arguments") or ""
            _out(f"\n----- 调用工具 {name} -----")
            if arguments:
                _out(_clip(arguments))

        elif event.name == "tool_output" and isinstance(event.item, ToolCallOutputItem):
            _out("\n----- 工具结果 -----")
            _out(_clip(event.item.output))

    if streamed_text or streamed_reason:
        _out()
    return result.final_output or ""


async def run_workflow(user_input: str):
    clear_memory_file()
    final_output = ""

    for index, round_task in enumerate(ROUNDS, 1):
        memory = read_memory_text()
        prompt = f"""
原始任务：
{user_input}

上一轮交接（需求原文、缺失列表、路径可直接用；编译对错仍以命令为准）：
{memory}

当前是第 {index}/{len(ROUNDS)} 轮：
{round_task}

严格只做当前轮要求的工作，不要提前执行后续轮次。
每轮开始先 read_memory。
本轮有进展就 save_memory：必须原封不动附上需求文档全文；找不到的文件记入缺失列表，之后禁止再搜。
目录不存在就 create_dir，不要反复 list_dir。
交接中的路径、需求原文和缺失列表可以直接使用。
编译是否通过必须重新跑命令，不能只信交接。
没有具体错误或可验证的改进理由时，禁止重写已有代码。
需要修改时只修改必要部分，禁止无理由推倒重写。
必须实际使用工具，不要只说明计划或打印工具调用参数。
工具 path 必须是以 / 开头的绝对路径。
"""
        _out(f"\n===== 第 {index}/{len(ROUNDS)} 轮开始 =====")
        try:
            final_output = await run_round(prompt)
        except MaxTurnsExceeded as exc:
            final_output = str(exc)
            _out(f"\n本轮中断: {exc}")
        _out(f"\n===== 第 {index}/{len(ROUNDS)} 轮结束 =====")
        if final_output:
            _out(final_output)

    if "VERIFY: PASS" not in final_output:
        _out(f"\n最终验收未明确通过，请检查第 {len(ROUNDS)} 轮输出。")


async def main():
    while True:
        try:
            user_input = input("请输入任务（输入 exit 退出）: ").strip()
            if user_input.lower() == "exit":
                break

            await run_workflow(user_input)
        except Exception as e:
            _out(str(e))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    asyncio.run(main())
