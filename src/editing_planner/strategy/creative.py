"""Creative Planner 合并层（§19/§61）：创作决策 → StyleSpec。

``llm/planner.py`` 的输出在这里被白名单校验后合并：

- animation 只接受白名单枚举值，未知值丢弃回默认；
- duration_hint / relation_preference 越界钳制；
- global_strategy 只补 LLM 可写字段（motion_language 等措辞）；
- 校验失败的整条指令忽略，回退规则输出——"LLM 产生设计决策，
  不产生视频事实"由这层强制。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..models import (
    AccessibilityPlanningProfile,
    EditingPlannerInput,
    GlobalStrategy,
    PlannerMode,
    SpatialRelation,
    StyleSpec,
)

_ANIMATION_WHITELIST = {
    "pop",
    "soft_pop",
    "fade",
    "glow",
    "flash",
    "particle",
    "float",
    "none",
}

_EMPHASIS_WHITELIST = {"low", "medium", "high"}

_DURATION_BOUNDS = (0.2, 5.0)


def build_creative_context(
    input: EditingPlannerInput,
    profile: AccessibilityPlanningProfile,
    requirement_digests: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """组装 Creative Planner 的输入上下文（只含创作相关信息）。"""
    intent = input.editing_intent
    g = intent.global_intent
    from gesture_intent.models import model_dump

    return {
        "global_intent": model_dump(g),
        "visual_language": _visual_language(g),
        "accessibility_profile": model_dump(profile),
        "requirements": requirement_digests,
        "video_summary": dict(input.semantic_view.video or {}),
    }


def _visual_language(g: Any) -> List[str]:
    out: List[str] = []
    for name in ("theme", "mood", "style", "color_preference"):
        value = getattr(g, name, None)
        if value is None:
            continue
        if value.canonical and value.canonical not in out:
            out.append(value.canonical)
        for tag in value.tags:
            if tag not in out:
                out.append(tag)
    return out


def requirement_digest(requirement_id: str, object_type: str, description: Any,
                       constraint_level: str) -> Dict[str, Any]:
    """喂给 Creative Planner 的单个需求摘要（不含任何视频事实）。"""
    return {
        "id": requirement_id,
        "object_type": object_type,
        "description_raw": getattr(description, "raw", "") if description else "",
        "canonical": getattr(description, "canonical", None) if description else None,
        "tags": list(getattr(description, "tags", []) or []) if description else [],
        "constraint_level": constraint_level,
    }


def merge_creative_output(
    raw: Dict[str, Any],
    strategy: GlobalStrategy,
) -> Tuple[Dict[str, StyleSpec], GlobalStrategy]:
    """校验并合并 creative planner 输出 → (requirement_id → StyleSpec, strategy)。

    所有白名单之外的字段直接丢弃；非法枚举/越界数值回默认。
    """
    directives: Dict[str, StyleSpec] = {}
    if not isinstance(raw, dict):
        return directives, strategy

    gs = raw.get("global_strategy")
    if isinstance(gs, dict):
        motion = gs.get("motion_language")
        if isinstance(motion, list):
            strategy.motion_language = [
                str(m) for m in motion if isinstance(m, (str, int, float))
            ][:8]

    for directive in raw.get("item_directives") or []:
        if not isinstance(directive, dict):
            continue
        req_id = directive.get("requirement_id")
        if not isinstance(req_id, str) or not req_id:
            continue
        animation = directive.get("animation")
        if animation not in _ANIMATION_WHITELIST:
            animation = None
        emphasis = directive.get("emphasis")
        if emphasis not in _EMPHASIS_WHITELIST:
            emphasis = "medium"
        palette = [
            str(c) for c in directive.get("palette") or []
            if isinstance(c, (str, int, float))
        ][:6]
        duration_hint = directive.get("duration_hint")
        if isinstance(duration_hint, (int, float)):
            duration_hint = float(
                min(max(duration_hint, _DURATION_BOUNDS[0]), _DURATION_BOUNDS[1])
            )
        else:
            duration_hint = None
        relation = directive.get("relation_preference")
        if relation not in SpatialRelation._value2member_map_:
            relation = None
        directives[req_id] = StyleSpec(
            animation=animation or "pop",
            emphasis=emphasis,
            palette=palette,
            duration_hint=duration_hint,
            relation_preference=relation,
        )
    return directives, strategy


def plan_creative(
    planner: Any,
    fallback: Any,
    context: Dict[str, Any],
    strategy: GlobalStrategy,
) -> Tuple[Dict[str, StyleSpec], GlobalStrategy, str, Optional[str]]:
    """跑 creative planner，失败回退规则。返回 (directives, strategy, mode, reason)。"""
    try:
        raw = planner.plan(context)
        directives, strategy = merge_creative_output(raw, strategy)
        mode = "llm" if getattr(planner, "name", "") == "llm" else (
            "custom" if getattr(planner, "name", "rules") != "rules" else "rules"
        )
        return directives, strategy, mode, None
    except Exception as exc:  # noqa: BLE001 —— 回退是设计的一部分
        raw = fallback.plan(context)
        directives, strategy = merge_creative_output(raw, strategy)
        return directives, strategy, PlannerMode.rules_fallback.value, str(exc)
