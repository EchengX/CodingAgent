"""界面截图识图：用飞书同一套百炼 Qwen 多模态，产出纯文字清单。"""
from __future__ import annotations

import base64
import os
from pathlib import Path

from openai import OpenAI

REPO_ROOT = Path("/Users/xxxx/work/CodingAgent")
FEISHU_ENV = REPO_ROOT / "feishu" / "feishu-req-intake" / ".env"
DEFAULT_BASE_URL = (
    "https://llm-n512stl9lp4z2bc8.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)
DEFAULT_MODEL = "qwen3.7-plus"

IMAGE_UI_PROMPT = """你是界面还原助手。只分析这一张图里的一个页面或一个弹窗，不要把多个页面合并成总表。
如果图片包含多个独立页面/弹窗，先明确写「需要拆图」，然后只分析画面中最主要的一个视图。
只依据可见像素，不推测未画出的功能，不编造；看不清的内容标记「无法辨认」。

按以下结构用中文输出详细的纯文字规格：
1. 视图类型与标题
   - 页面/弹窗/抽屉；主标题、副标题、面包屑原文
2. 整体布局
   - 从上到下、从左到右描述区域；筛选区与表格的位置关系；每行列数；对齐、宽窄和明显间距
3. 筛选项（严格按视觉顺序）
   - 序号、标签原文、控件类型（输入框/下拉/日期/单选等）、占位或默认值、可见选项、是否必填
4. 按钮与操作（严格按视觉顺序）
   - 文案、位置、主/次/危险样式、图标、是否禁用
5. 表格
   - 列按从左到右编号；列名原文、宽窄、对齐、示例值、格式；行内操作顺序；分页文案
6. 表单或弹窗字段（严格按视觉顺序）
   - 标签、控件类型、默认值/占位、可见选项、是否必填、帮助/校验文案
7. 视觉样式
   - 背景、卡片/分区、颜色、字号层级、边框、圆角、标签状态；只能写可见特征
8. 界面内文案（要实现）
   - 只抄录长在控件上的文字：标题、标签、占位、按钮、列名、空状态、分页
9. 需求标注（不要实现）
   - 原型稿的说明性文字：页面下方或侧边的编号说明、权限说明、交互说明、
     跳转说明、字段口径、开发备注、批注气泡、红字标注
   - 判定标准：它描述「谁可见、点了会怎样、怎么算」，而不是界面上的一个控件
   - 原样抄录，并统一标注「仅作实现依据，禁止渲染成页面文字」
10. 不确定项
   - 遮挡、模糊、截图外内容；没有写「无」

第 8 项和第 9 项必须分开，不确定归属时放进第 9 项。
"""

IMAGE_DIFF_PROMPT = """你是 UI 验收助手。下面依次给出【需求原图】和【代码预览图】。
只比较可见内容，不猜业务逻辑，不评价截图工具或浏览器外框。

先判定视图类型，再比内容：
- 原图是弹窗时，预览图里必须有打开的弹窗。预览图只有列表页或弹窗没弹出，
  直接判 FAIL，第 2 项写「弹窗未打开或未截到」，不要拿列表页内容凑数。
- 两张图视图类型不一致（一个是页面一个是弹窗）时同样判 FAIL。

原型稿里的说明性文字（页面下方或侧边的编号说明、权限说明、交互说明、
跳转说明、开发备注、红字批注）只是需求依据，不是界面内容：
- 预览图里出现这类说明文字，算第 3 项多余项，且定级 P0，必须删掉。
- 预览图里没有这类文字，不算缺失项。

按以下结构输出中文差异清单：
1. 结论：PASS 或 FAIL；并写明本次比对的视图类型（页面/弹窗）
2. 缺失项：原图有、预览图没有；按标题/筛选/列/按钮/弹窗字段/文案分类
3. 多余项：预览图有、原图没有；把渲染出来的需求标注文字单列出来
4. 错误项：文案、控件类型、默认值、必填、列顺序、按钮顺序不一致
5. 布局差异：左右/上下关系、区域顺序、对齐、宽窄和明显间距
6. 样式差异：主次按钮、颜色、字号层级、边框、状态样式
7. 修改优先级：仅列可执行修改，P0=内容/结构错误，P1=布局错误，P2=样式误差

完全一致或只有无法由组件库控制的微小像素差异时才给 PASS。
"""


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def vision_config() -> dict[str, str]:
    env_file = _parse_dotenv(FEISHU_ENV)
    api_key = (
        os.environ.get("QWEN_API_KEY", "").strip()
        or env_file.get("QWEN_API_KEY", "").strip()
    )
    base_url = (
        os.environ.get("QWEN_BASE_URL", "").strip()
        or env_file.get("QWEN_BASE_URL", "").strip()
        or DEFAULT_BASE_URL
    )
    model = (
        os.environ.get("QWEN_MODEL", "").strip()
        or env_file.get("QWEN_MODEL", "").strip()
        or DEFAULT_MODEL
    )
    return {"api_key": api_key, "base_url": base_url, "model": model}


def guess_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"RIFF") and b"WEBP" in data[:16]:
        return "image/webp"
    return "image/png"


def _image_content(image_bytes: bytes) -> dict:
    mime = guess_image_mime(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }


def _vision_completion(content: list[dict]) -> str:
    cfg = vision_config()
    if not cfg["api_key"]:
        raise RuntimeError(
            "未找到 QWEN_API_KEY。可设环境变量，或保证 "
            f"{FEISHU_ENV} 里已填写飞书那套百炼 key。"
        )
    client = OpenAI(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        timeout=120.0,
    )
    resp = client.chat.completions.create(
        model=cfg["model"],
        messages=[
            {
                "role": "user",
                "content": content,
            }
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("vision model returned empty content")
    return text


def describe_ui_image(image_bytes: bytes) -> str:
    if not image_bytes:
        raise ValueError("image is empty")
    return _vision_completion(
        [
            {"type": "text", "text": IMAGE_UI_PROMPT},
            _image_content(image_bytes),
        ]
    )


def compare_ui_images(reference_bytes: bytes, preview_bytes: bytes) -> str:
    if not reference_bytes:
        raise ValueError("reference image is empty")
    if not preview_bytes:
        raise ValueError("preview image is empty")
    return _vision_completion(
        [
            {"type": "text", "text": IMAGE_DIFF_PROMPT},
            {"type": "text", "text": "【需求原图】"},
            _image_content(reference_bytes),
            {"type": "text", "text": "【代码预览图】"},
            _image_content(preview_bytes),
        ]
    )
