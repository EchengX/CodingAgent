# DeepSeek 官方 API（OpenAI 兼容）

import json
import os
from typing import Any

import httpx
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel, set_tracing_disabled

set_tracing_disabled(True)

BASE_URL = "https://api.deepseek.com"
MODEL_NAME = "deepseek-chat"
# 优先环境变量 DEEPSEEK_API_KEY；没有则把 sk- 密钥填到下一行
_FALLBACK_KEY = ""
API_KEY = os.environ.get("DEEPSEEK_API_KEY") or _FALLBACK_KEY
if not API_KEY:
    raise RuntimeError(
        "未设置 DeepSeek 密钥。先 export DEEPSEEK_API_KEY=sk-你的密钥，"
        "或把密钥写到 LLM.py 的 _FALLBACK_KEY。"
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _parse_arguments(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return _extract_json_object(text)


def _dump_arguments(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, separators=(",", ":"))


def _guess_tool_name(args: dict[str, Any]) -> str:
    keys = set(args)
    if "url" in keys:
        return "fetch_url"
    if "keyword" in keys:
        return "search_file"
    if "command" in keys:
        return "run_generator"
    if "start_line" in keys or "max_lines" in keys:
        return "open_file"
    if "content" in keys and "path" in keys:
        return "create_file"
    if "content" in keys:
        return "save_memory"
    path = str(args.get("path") or "")
    if path.endswith((".md", ".txt", ".php", ".sui", ".py", ".json", ".mjs")):
        return "open_file"
    if "path" in keys:
        return "list_dir"
    return "find"


def _tool_name_and_args(obj: dict[str, Any]) -> tuple[str, str]:
    if "name" in obj and "arguments" in obj:
        name = str(obj.get("name") or "").strip() or "find"
        parsed = _parse_arguments(obj.get("arguments"))
        if parsed is None and isinstance(obj.get("arguments"), dict):
            parsed = obj["arguments"]
        if parsed is None:
            parsed = {k: v for k, v in obj.items() if k not in ("name", "arguments")}
        return name, _dump_arguments(parsed or {})
    return _guess_tool_name(obj), _dump_arguments(obj)


def _extract_raw_from_error(message: str) -> str:
    marker = "raw='"
    start = message.find(marker)
    quote = "'"
    if start < 0:
        marker = 'raw="'
        start = message.find(marker)
        quote = '"'
    if start < 0:
        return message
    start += len(marker)
    end_token = quote + ", err="
    end = message.find(end_token, start)
    if end < 0:
        end = message.rfind(quote)
    raw = message[start:end] if end > start else message[start:]
    return raw.replace("\\'", "'").replace('\\"', '"')


def _error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload)
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or payload)
    if isinstance(error, str):
        return error
    return str(payload.get("message") or payload)


def _is_parse_error(status_code: int, payload: Any) -> bool:
    if status_code < 400:
        return False
    message = _error_message(payload).lower()
    return "error parsing tool call" in message or "looking for beginning of value" in message


def _completion_payload(
    name: str,
    arguments: str,
    model_name: str,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-tool-fix",
        "object": "chat.completion",
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_tool_fix",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": arguments,
                            },
                        }
                    ],
                },
            }
        ],
    }


def _as_sse(payload: dict[str, Any]) -> bytes:
    message = payload["choices"][0]["message"]
    chunk = {
        "id": payload.get("id", "chatcmpl-tool-fix"),
        "object": "chat.completion.chunk",
        "model": payload.get("model", MODEL_NAME),
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": message.get("tool_calls") or [],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    return (
        "data: "
        + json.dumps(chunk, ensure_ascii=False)
        + "\n\ndata: [DONE]\n\n"
    ).encode("utf-8")


def _fix_tool_calls(message: dict[str, Any]) -> bool:
    changed = False
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            parsed = _parse_arguments(function.get("arguments"))
            if parsed is None:
                continue
            dumped = _dump_arguments(parsed)
            if dumped != function.get("arguments"):
                function["arguments"] = dumped
                changed = True
        if tool_calls:
            return changed

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        return False
    obj = _extract_json_object(content)
    if not obj:
        return False
    if "name" not in obj and "arguments" not in obj:
        return False
    name, arguments = _tool_name_and_args(obj)
    message["content"] = None
    message["tool_calls"] = [
        {
            "id": "call_tool_fix",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }
    ]
    return True


def _fix_completion_json(payload: dict[str, Any]) -> bool:
    changed = False
    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict) and _fix_tool_calls(message):
            if message.get("tool_calls"):
                choice["finish_reason"] = "tool_calls"
            changed = True
    return changed


def _recover_from_parse_error(message: str, model_name: str) -> dict[str, Any] | None:
    raw = _extract_raw_from_error(message)
    obj = _extract_json_object(raw) or _extract_json_object(message)
    if not obj:
        return None
    name, arguments = _tool_name_and_args(obj)
    print(f"[tool_call_fix] recovered {name}", flush=True)
    return _completion_payload(name, arguments, model_name)


def _wants_stream(request: httpx.Request) -> bool:
    try:
        body = json.loads(request.content.decode("utf-8") or "{}")
    except Exception:
        return "text/event-stream" in request.headers.get("accept", "")
    return bool(body.get("stream"))


def _request_model(request: httpx.Request) -> str:
    try:
        body = json.loads(request.content.decode("utf-8") or "{}")
        return str(body.get("model") or MODEL_NAME)
    except Exception:
        return MODEL_NAME


class ToolCallFixTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport):
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if "/chat/completions" not in str(request.url):
            return response

        raw = await response.aread()
        await response.aclose()
        want_stream = _wants_stream(request)
        model_name = _request_model(request)
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip()

        payload: Any = None
        if raw and content_type != "text/event-stream":
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                payload = None

        recovered = None
        if payload is not None and _is_parse_error(response.status_code, payload):
            recovered = _recover_from_parse_error(_error_message(payload), model_name)
        elif response.status_code >= 400:
            text = raw.decode("utf-8", errors="replace")
            if "error parsing tool call" in text.lower():
                recovered = _recover_from_parse_error(text, model_name)

        if recovered is not None:
            if want_stream:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=_as_sse(recovered),
                    request=request,
                )
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=json.dumps(recovered, ensure_ascii=False).encode("utf-8"),
                request=request,
            )

        if (
            response.status_code == 200
            and isinstance(payload, dict)
            and _fix_completion_json(payload)
        ):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                request=request,
            )

        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=raw,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


client = AsyncOpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    http_client=httpx.AsyncClient(
        trust_env=False,
        transport=ToolCallFixTransport(httpx.AsyncHTTPTransport()),
    ),
)

model = OpenAIChatCompletionsModel(
    model=MODEL_NAME,
    openai_client=client,
)
