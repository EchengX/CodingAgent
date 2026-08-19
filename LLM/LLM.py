# 本地开源大模型设置（Ollama）

import httpx
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel, set_tracing_disabled

set_tracing_disabled(True)

BASE_URL = "http://127.0.0.1:11434/v1"
API_KEY = "ollama"
MODEL_NAME = "coding-agent-model"

client = AsyncOpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    http_client=httpx.AsyncClient(trust_env=False),
)

model = OpenAIChatCompletionsModel(
    model=MODEL_NAME,
    openai_client=client,
)
