"""Creative Planner 的 LLM 接缝（§19/§61）。

LLM 唯一可参与的阶段：全局策略措辞与逐项创作指令
（动画 / 强调程度 / 调色板 / 时长偏好 / 相对位置偏好）。

契约边界由 ``strategy/creative.py`` 强制——LLM 输出先经白名单校验
再合并进 StyleSpec；时间戳、event_uid、坐标、素材 URL 一律不接受
（"LLM 产生设计决策，不产生视频事实"）。

适配器形态与模块一 ``extractors.py::OpenAICompatibleExtractor`` 一致：
stdlib urllib → ``{base_url}/chat/completions``，``PLANNER_LLM_*``
环境变量优先、``OPENAI_*`` 兜底。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Protocol
from urllib import error, request


class StructuredCreativePlanner(Protocol):
    """创作决策器的可插拔协议：上下文 dict → 指令 dict。"""

    name: str

    def plan(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """返回 {"global_strategy": {...}, "item_directives": [{...}]}。"""
        ...


# ---------------------------------------------------------------------------
# 确定性默认实现
# ---------------------------------------------------------------------------

#: 语义标签 → (animation, palette)
_TAG_STYLE = {
    "cute": ("soft_pop", ["pink", "pastel"]),
    "pink": ("soft_pop", ["pink"]),
    "heart": ("pop", ["pink"]),
    "summer": ("float", ["warm", "bright"]),
    "energetic": ("flash", ["vivid"]),
    "cool": ("glow", ["cyan", "purple"]),
    "dream": ("float", ["pastel", "lavender"]),
}

#: object_type → 默认动画 / 相对位置偏好
_TYPE_DEFAULT = {
    "sticker": ("pop", "above"),
    "overlay": ("pop", "above"),
    "image": ("fade", "above"),
    "text": ("fade", "below"),
    "effect": ("glow", "centered_on"),
    "sound_effect": ("none", "centered_on"),
    "music": ("none", "centered_on"),
    "background": ("none", "centered_on"),
}

#: 低幅度/震颤场景的视觉增强（§20 白名单 → 动画语义）
_LOW_AMP_ANIMATION = {
    "sticker": "soft_pop",
    "overlay": "soft_pop",
    "image": "soft_pop",
    "text": "fade",
    "effect": "glow",
}


class RuleBasedCreativePlanner:
    """确定性创意默认：标签→风格表 + 类型默认 + 低幅度白名单。"""

    name = "rules"

    def plan(self, context: Dict[str, Any]) -> Dict[str, Any]:
        profile = context.get("accessibility_profile") or {}
        low_amp = profile.get("motion_amplitude") in ("low", "very_low")

        # 全局风格：汇总全部需求的标签 + global_intent 视觉语言
        visual_tags: List[str] = list(context.get("visual_language") or [])
        for req in context.get("requirements") or []:
            for tag in req.get("tags") or []:
                if tag not in visual_tags:
                    visual_tags.append(tag)

        palette: List[str] = []
        motion_language: List[str] = []
        for tag in visual_tags:
            entry = _TAG_STYLE.get(tag)
            if not entry:
                continue
            anim, colors = entry
            if anim not in motion_language:
                motion_language.append(anim)
            for c in colors:
                if c not in palette:
                    palette.append(c)
        if low_amp and "soft_pop" not in motion_language:
            motion_language.append("soft_pop")
            if "light_glow" not in motion_language:
                motion_language.append("light_glow")

        directives: List[Dict[str, Any]] = []
        for req in context.get("requirements") or []:
            object_type = req.get("object_type", "sticker")
            animation, relation = _TYPE_DEFAULT.get(object_type, ("pop", "above"))
            if low_amp and object_type in _LOW_AMP_ANIMATION:
                animation = _LOW_AMP_ANIMATION[object_type]
            emphasis = "high" if low_amp else "medium"
            if req.get("constraint_level") == "open":
                emphasis = "low"
            directive: Dict[str, Any] = {
                "requirement_id": req.get("id", ""),
                "animation": animation,
                "emphasis": emphasis,
                "palette": palette,
                "relation_preference": relation,
            }
            if object_type in ("sticker", "overlay", "effect"):
                directive["duration_hint"] = 0.8
            directives.append(directive)

        return {
            "global_strategy": {
                "motion_language": motion_language,
            },
            "item_directives": directives,
        }


# ---------------------------------------------------------------------------
# OpenAI-compatible 适配器
# ---------------------------------------------------------------------------


class OpenAICompatibleCreativePlanner:
    """OpenAI-compatible Chat Completions 创意规划适配器（stdlib HTTP）。"""

    DEFAULT_BASE_URL = "https://models.sjtu.edu.cn/api/v1"
    DEFAULT_MODEL = "deepseek-reasoner"
    name = "llm"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_environment(cls) -> Optional["OpenAICompatibleCreativePlanner"]:
        api_key = os.getenv("PLANNER_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        base_url = (
            os.getenv("PLANNER_LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or cls.DEFAULT_BASE_URL
        )
        model = os.getenv("PLANNER_LLM_MODEL") or os.getenv("OPENAI_MODEL") or cls.DEFAULT_MODEL
        try:
            timeout = float(os.getenv("PLANNER_LLM_TIMEOUT", "60"))
        except ValueError:
            timeout = 60.0
        return cls(base_url, api_key, model, timeout=timeout)

    def plan(self, context: Dict[str, Any]) -> Dict[str, Any]:
        system = (
            "你是视频剪辑创意规划器。只产生创作决策：视觉风格、动画类型、"
            "强调程度、大概持续时间、素材相对位置偏好。"
            "禁止输出时间戳、event_uid、坐标、素材文件或 URL——"
            "那些由确定性代码和上游模块决定。必须返回 JSON，顶层包含 "
            "global_strategy 和 item_directives 两个键。"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        if "reasoner" not in self.model.lower() and "reasoning" not in self.model.lower():
            payload["temperature"] = 0
        endpoint = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else f"{self.base_url}/chat/completions"
        )
        req = request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"creative planner request failed: {exc}") from exc
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        content = content.strip()
        if content.startswith("```"):
            content = content.removeprefix("```").removeprefix("json").strip()
            if content.endswith("```"):
                content = content[:-3].strip()
        return json.loads(content)


def default_creative_planner() -> StructuredCreativePlanner:
    return OpenAICompatibleCreativePlanner.from_environment() or RuleBasedCreativePlanner()
