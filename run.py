# Agent启动入口

import asyncio
import json
import sys
from typing import Any

from agents import (
    ItemHelpers,
    RawResponsesStreamEvent,
    Runner,
    RunItemStreamEvent,
)
from agents.exceptions import MaxTurnsExceeded
from agents.items import MessageOutputItem, ReasoningItem, ToolCallItem, ToolCallOutputItem

from CodingAgent import CodingAgent
from tools.Local.Local import clear_memory_file, clear_scratch_dir

MAX_TURNS = 200

TOOL_ARG_LIMIT = 300
TOOL_OUTPUT_LINES = 5
TOOL_OUTPUT_LIMIT = 500
RECENT_TOOL_OUTPUTS = 10


def _out(text: str = "", end: str = "\n") -> None:
    print(text, end=end, flush=True)


def _clip(text: object, limit: int = TOOL_ARG_LIMIT) -> str:
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... [truncated {len(value) - limit} chars]"


def _brief(text: object) -> str:
    """工具结果只在终端展示前几行摘要，完整内容仍会进模型上下文。"""
    value = "" if text is None else str(text)
    lines = value.splitlines()
    head = "\n".join(lines[:TOOL_OUTPUT_LINES])
    head = _clip(head, TOOL_OUTPUT_LIMIT)
    omitted = len(lines) - TOOL_OUTPUT_LINES
    if omitted > 0:
        head += f"\n... [省略 {omitted} 行，共 {len(value)} 字符]"
    return head or "(无输出)"


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


def _tool_output_summary(
    name: str,
    arguments: object,
    output: object,
) -> str:
    arg_text = str(arguments or "").strip()
    try:
        parsed = json.loads(arg_text)
        arg_text = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        pass
    if len(arg_text) > 300:
        arg_text = arg_text[:300] + "..."

    output_text = str(output or "").strip()
    lines = [line.strip() for line in output_text.splitlines() if line.strip()]
    if name == "read":
        useful = lines[:2]
    elif name == "run":
        useful = [line for line in lines if line.startswith("returncode:")][:1]
        useful += lines[1:2]
    else:
        useful = lines[:1]
    result = "; ".join(useful) if useful else "无文本输出"
    return (
        f"[旧工具结果摘要] {name or 'unknown'}({arg_text})"
        f" -> {result}；原结果 {len(output_text)} 字符"
    )


def _compact_history(
    history: list[Any],
    keep_recent: int = RECENT_TOOL_OUTPUTS,
) -> list[Any]:
    """最近 10 条工具结果保留全文，更早的压成一行。需求细节以 read/read_image 结果为准。"""
    compacted = list(history)
    call_meta: dict[str, tuple[str, object]] = {}
    output_indexes: list[int] = []

    for index, item in enumerate(compacted):
        item_type = _item_attr(item, "type")
        if item_type == "function_call":
            call_id = str(_item_attr(item, "call_id") or "")
            call_meta[call_id] = (
                str(_item_attr(item, "name") or "unknown"),
                _item_attr(item, "arguments") or "",
            )
        elif item_type == "function_call_output":
            output_indexes.append(index)

    old_indexes = output_indexes[:-keep_recent] if keep_recent > 0 else output_indexes
    for index in old_indexes:
        item = compacted[index]
        call_id = str(_item_attr(item, "call_id") or "")
        name, arguments = call_meta.get(call_id, ("unknown", ""))
        summary = _tool_output_summary(
            name,
            arguments,
            _item_attr(item, "output"),
        )
        if isinstance(item, dict):
            updated = dict(item)
            updated["output"] = summary
            compacted[index] = updated
        elif hasattr(item, "model_copy"):
            compacted[index] = item.model_copy(update={"output": summary})

    return compacted


def _task_message(user_input: str) -> str:
    return f"原始任务：\n{user_input.strip()}"


async def run_round(input_items: str | list[Any]) -> tuple[str, list[Any]]:
    result = Runner.run_streamed(CodingAgent, input_items, max_turns=MAX_TURNS)
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
            _out(_brief(event.item.output))

    if streamed_text or streamed_reason:
        _out()
    history = result.to_input_list(mode="normalized")
    return result.final_output or "", _compact_history(history)


async def run_workflow(user_input: str):
    clear_memory_file()
    clear_scratch_dir()
    history: list[Any] = [{"role": "user", "content": _task_message(user_input)}]
    final_output = ""
    _out("\n===== 开始 =====")
    try:
        final_output, history = await run_round(history)
    except MaxTurnsExceeded as exc:
        final_output = str(exc)
        _out(f"\n中断: {exc}")
    _out("\n===== 结束 =====")
    if final_output:
        _out(final_output)
    if "VERIFY: PASS" not in (final_output or ""):
        _out("\n最终验收未明确通过，请检查输出中是否包含 VERIFY: PASS。")


async def main():
    while True:
        try:
            user_input = input("请输入任务（输入 exit 退出）: ").strip()
            if user_input.lower() == "exit":
                break

            await run_workflow(user_input)
        except Exception as e:
            _out(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    asyncio.run(main())
