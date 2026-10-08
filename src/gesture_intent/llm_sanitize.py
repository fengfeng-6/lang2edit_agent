"""LLM 输出的引导提示词与白名单清洗。

OpenAI 兼容端点在 ``json_object`` 模式下不保证输出形状（实测 sjtu qwen
会自造 ``{raw, canonical, tags}`` 之类结构），而 EditingIntent/IntentPatch
是 ``StrictModel(extra="forbid")``——多一个键就校验失败、整条回落规则
抽取器。本模块给默认抽取器两层保护：

1. ``GUIDED_SYSTEM_PROMPT``：把允许键、枚举值、句式约定写进系统提示，
   约束模型只产出我们认得的形状；
2. ``sanitize_intent_payload`` / ``sanitize_patch_payload``：对返回 JSON
   做白名单过滤 + 类型/枚举纠偏（多余键丢弃、缺省补齐、非法枚举归位）。
   完全异形的 payload 抛 ``ValueError``，由 parser 决定是否回落。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

GUIDED_SYSTEM_PROMPT = """你是视频剪辑需求理解器。把用户输入转成严格 JSON，顶层只含 request_type 和
editing_intent（首轮）或 intent_patch（修改轮）。只提取用户明确表达的意图。

editing_intent 允许的键（多余键会导致解析失败，不许发明）：
- global_intent: {"theme","mood","style","pacing","color_preference",
  "platform_style","autonomy"}，每项 {"raw","canonical","tags"} 或 null
- object_requirements: [{"id":"req_01" 等唯一 id,
  "object_type":"background|music|image|sticker|text|effect|sound_effect|overlay",
  "action":"add|replace|update|remove",
  "description":{"raw":"原话","canonical":"english_snake","tags":["en"]}或null,
  "content":文本或null,"target_ref":null,"source_text":"对应原话",
  "constraint_level":"hard|soft|open","confidence":0.9}]
- event_bound_requirements（"当/每次X时加Y"句式）:[{"id":"event_req_01",
  "source_text":"原话",
  "trigger":{"event":{"type":"gesture|body_action|pose_condition|
    video_structure|audio_event","raw":"原词","canonical":"heart_gesture|
    wave_hand|open_both_hands|hand_raise|ending_pose|beat|video_start|
    video_end|last_action 等 snake_case"},
  "occurrence":{"type":"all|first|last|index|range","value":null,
    "start":null,"end":null},
  "temporal_relation":"at_event|during_event|before_event|after_event|
    from_event|until_event|between_events"},
  "requirement":{"object_type":"sticker 等","action":"add",
    "semantic_description":{"raw":"原话","canonical":"en","tags":["en"]},
    "content":null,"operation":null},
  "constraint_level":"hard","confidence":0.9}]
- explicit_operations: [{"id":"op_01","operation":"freeze 等",
  "target":{"type":"object|requirement|event","value":"id"},
  "parameters":{},"source_text":"原话","constraint_level":"hard",
  "confidence":0.9}]
- constraints（保留/不要/必须类）:[{"id":"constraint_01",
  "scope":{"target":"background_music|person|audio"},"type":"keep|
  no_add|visual_style 等","preference":{"raw":"..."},"raw":"原话",
  "constraint_level":"hard","confidence":0.9}]
- unresolved: [{"type":"object_reference 等","raw":"原话","candidates":[],
  "confidence":0.0,"reason":"..."}]

要点：背景分段每段一条 object_requirements(object_type=background,action=add)；
时间段（如0-8秒）写进 description.raw；手势 canonical 用英文蛇形；
canonical/tags 一律英文小写；"保留/不要"类放 constraints 不要放 object_requirements。
intent_patch 顶层键：add_object_requirements / update_object_requirements /
remove_object_requirement_ids / add_event_bound_requirements /
update_event_bound_requirements / remove_event_bound_requirement_ids /
add_operations / update_operations / remove_operation_ids / add_constraints /
update_constraints / remove_constraint_ids / global_updates / affected_objects，
元素结构同上，只输出本轮增量。"""

_SV_KEYS = {"raw", "canonical", "tags", "confidence"}
_OBJ_TYPES = {"background", "music", "image", "sticker", "text", "effect",
              "sound_effect", "overlay"}
_ACTIONS = {"add", "replace", "update", "remove"}
_LEVELS = {"hard", "soft", "open"}
_RELATIONS = {"before_event", "at_event", "during_event", "after_event",
              "from_event", "until_event", "between_events"}
_EVENT_TYPES = {"gesture", "body_action", "pose_condition",
                "video_structure", "audio_event"}
_OCC_TYPES = {"all", "first", "last", "index", "range"}
_OPERATIONS = {"freeze", "trim", "split", "remove", "scale_adjust",
               "position_adjust", "volume_adjust", "speed_adjust",
               "replace_text", "replace_asset"}

_INTENT_KEYS = {"global_intent", "object_requirements",
                "event_bound_requirements", "explicit_operations",
                "constraints", "unresolved"}
_PATCH_KEYS = {
    "add_object_requirements", "update_object_requirements",
    "remove_object_requirement_ids", "add_event_bound_requirements",
    "update_event_bound_requirements", "remove_event_bound_requirement_ids",
    "add_operations", "update_operations", "remove_operation_ids",
    "add_constraints", "update_constraints", "remove_constraint_ids",
    "global_updates", "affected_objects",
}
_PATCH_LIST_KINDS = {
    "add_object_requirements": "object_req",
    "update_object_requirements": "object_req",
    "add_event_bound_requirements": "event_req",
    "update_event_bound_requirements": "event_req",
    "add_operations": "op",
    "update_operations": "op",
    "add_constraints": "constraint",
    "update_constraints": "constraint",
}
_REMOVE_ID_KEYS = ("remove_object_requirement_ids",
                   "remove_event_bound_requirement_ids",
                   "remove_operation_ids", "remove_constraint_ids")

_GLOBAL_INTENT_KEYS = {"theme", "mood", "style", "pacing",
                       "color_preference", "platform_style", "autonomy"}
_AUTONOMY_KEYS = {"default", "protected_requirements"}
_OBJECT_REQ_KEYS = {"id", "object_type", "action", "description", "content",
                    "target_ref", "source_text", "constraint_level",
                    "confidence", "field_confidence"}
_EVENT_REQ_KEYS = {"id", "source_text", "trigger", "requirement",
                   "constraint_level", "confidence", "field_confidence"}
_TRIGGER_KEYS = {"event", "occurrence", "temporal_relation"}
_EVENT_KEYS = {"type", "raw", "canonical", "condition", "confidence"}
_OCCURRENCE_KEYS = {"type", "value", "start", "end"}
_INNER_REQ_KEYS = {"object_type", "action", "semantic_description", "content",
                   "operation"}
_OP_KEYS = {"id", "operation", "target", "parameters", "source_text",
            "constraint_level", "confidence"}
_TARGET_KEYS = {"type", "value"}
_CONSTRAINT_KEYS = {"id", "scope", "type", "reference", "preference", "raw",
                    "constraint_level", "confidence"}
_UNRESOLVED_KEYS = {"type", "raw", "candidates", "confidence", "reason"}
_FIELD_CONFIDENCE_KEYS = None  # 任意 dict 原样保留


def sanitize_intent_payload(raw: Any) -> Dict[str, Any]:
    """清洗 LLM 的 editing_intent 负载到 EditingIntent 可校验的形状。"""
    picked = _recognized(raw, _INTENT_KEYS, "intent")
    out: Dict[str, Any] = {}
    out["global_intent"] = _clean_global_intent(picked.get("global_intent"))
    out["object_requirements"] = _clean_list(
        picked.get("object_requirements"), _clean_object_req)
    out["event_bound_requirements"] = _clean_list(
        picked.get("event_bound_requirements"), _clean_event_req)
    out["explicit_operations"] = _clean_list(
        picked.get("explicit_operations"), _clean_op)
    out["constraints"] = _clean_list(
        picked.get("constraints"), _clean_constraint)
    out["unresolved"] = _clean_list(
        picked.get("unresolved"), _clean_unresolved)
    return out


def sanitize_patch_payload(raw: Any) -> Dict[str, Any]:
    """清洗 LLM 的 intent_patch 负载到 IntentPatch 可校验的形状。"""
    picked = _recognized(raw, _PATCH_KEYS, "patch")
    out: Dict[str, Any] = {}
    cleaners = {"object_req": _clean_object_req, "event_req": _clean_event_req,
                "op": _clean_op, "constraint": _clean_constraint}
    for key, kind in _PATCH_LIST_KINDS.items():
        out[key] = _clean_list(picked.get(key), cleaners[kind])
    for key in _REMOVE_ID_KEYS:
        out[key] = [str(x) for x in _as_list(picked.get(key))]
    out["global_updates"] = (
        picked.get("global_updates")
        if isinstance(picked.get("global_updates"), dict) else {}
    )
    out["affected_objects"] = [
        str(x) for x in _as_list(picked.get("affected_objects"))]
    return out


def _recognized(raw: Any, keys: set, label: str) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(
            f"LLM {label} payload must be a dict, got {type(raw).__name__}")
    picked = _pick(raw, keys)
    if raw and not picked:
        raise ValueError(f"LLM {label} payload has no recognized keys")
    return picked


def _pick(d: Any, keys: set) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if k in keys} if isinstance(d, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _clean_list(value: Any, cleaner) -> List[Dict[str, Any]]:
    out = []
    for i, item in enumerate(_as_list(value)):
        if not isinstance(item, dict):
            continue
        cleaned = cleaner(item, i + 1)
        if cleaned is not None:
            out.append(cleaned)
    return out


def _sv(v: Any) -> Optional[Dict[str, Any]]:
    if v is None:
        return None
    if isinstance(v, str):
        return {"raw": v}
    picked = _pick(v, _SV_KEYS)
    if isinstance(picked.get("raw"), (int, float)):
        picked["raw"] = str(picked["raw"])
    if "raw" not in picked or not isinstance(picked["raw"], str):
        return None
    if not isinstance(picked.get("canonical"), str):
        picked.pop("canonical", None)
    tags = picked.get("tags")
    picked["tags"] = [str(t) for t in tags] if isinstance(tags, list) else []
    if "confidence" in picked:
        picked["confidence"] = _num(picked["confidence"], None)
    return picked


def _num(v: Any, default: Optional[float] = 0.9) -> Optional[float]:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default


def _int_or_none(v: Any) -> Optional[int]:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _str_or_none(v: Any) -> Optional[str]:
    return v if isinstance(v, str) else None


def _clean_global_intent(value: Any) -> Dict[str, Any]:
    gi = _pick(value, _GLOBAL_INTENT_KEYS)
    out: Dict[str, Any] = {}
    for key, v in gi.items():
        if key == "platform_style":
            if isinstance(v, dict):
                v = v.get("raw")
            out[key] = _str_or_none(v)
        elif key == "autonomy":
            au = _pick(v, _AUTONOMY_KEYS) if isinstance(v, dict) else {}
            if au.get("default") not in _LEVELS:
                au["default"] = "soft"
            au["protected_requirements"] = [
                str(x) for x in _as_list(au.get("protected_requirements"))]
            out[key] = au
        else:
            out[key] = _sv(v)
    return out


def _clean_object_req(d: Dict[str, Any], i: int) -> Dict[str, Any]:
    d = _pick(d, _OBJECT_REQ_KEYS)
    d["id"] = str(d.get("id") or f"req_{i:02d}")
    if d.get("object_type") not in _OBJ_TYPES:
        d["object_type"] = "sticker"
    if d.get("action") not in _ACTIONS:
        d["action"] = "add"
    d["description"] = _sv(d.get("description"))
    d["content"] = _str_or_none(d.get("content"))
    d["target_ref"] = _str_or_none(d.get("target_ref"))
    d["source_text"] = str(d.get("source_text") or d["id"])
    if d.get("constraint_level") not in _LEVELS:
        d["constraint_level"] = "hard"
    d["confidence"] = _num(d.get("confidence"))
    if isinstance(d.get("field_confidence"), dict):
        d["field_confidence"] = {
            str(k): _num(v) for k, v in d["field_confidence"].items()}
    return d


def _clean_event_req(d: Dict[str, Any], i: int) -> Dict[str, Any]:
    d = _pick(d, _EVENT_REQ_KEYS)
    d["id"] = str(d.get("id") or f"event_req_{i:02d}")
    d["source_text"] = str(d.get("source_text") or d["id"])

    tr = _pick(d.get("trigger"), _TRIGGER_KEYS)
    ev = _pick(tr.get("event"), _EVENT_KEYS)
    if ev.get("type") not in _EVENT_TYPES:
        ev["type"] = "gesture"
    ev["raw"] = str(ev.get("raw") or "")
    ev["canonical"] = _str_or_none(ev.get("canonical"))
    ev["condition"] = (
        ev.get("condition") if isinstance(ev.get("condition"), dict) else None)
    ev["confidence"] = _num(ev.get("confidence"), 0.95)
    tr["event"] = ev
    occ = _pick(tr.get("occurrence"), _OCCURRENCE_KEYS)
    if occ.get("type") not in _OCC_TYPES:
        occ["type"] = "all"
    occ["value"] = _int_or_none(occ.get("value"))
    occ["start"] = _int_or_none(occ.get("start"))
    occ["end"] = _int_or_none(occ.get("end"))
    tr["occurrence"] = occ
    if tr.get("temporal_relation") not in _RELATIONS:
        tr["temporal_relation"] = "at_event"
    d["trigger"] = tr

    rq = _pick(d.get("requirement"), _INNER_REQ_KEYS)
    if rq.get("object_type") not in _OBJ_TYPES:
        rq["object_type"] = "sticker"
    if rq.get("action") not in _ACTIONS:
        rq["action"] = "add"
    rq["semantic_description"] = _sv(rq.get("semantic_description"))
    rq["content"] = _str_or_none(rq.get("content"))
    if rq.get("operation") not in _OPERATIONS:
        rq["operation"] = None
    d["requirement"] = rq

    if d.get("constraint_level") not in _LEVELS:
        d["constraint_level"] = "hard"
    d["confidence"] = _num(d.get("confidence"))
    return d


def _clean_constraint(d: Dict[str, Any], i: int) -> Dict[str, Any]:
    d = _pick(d, _CONSTRAINT_KEYS)
    d["id"] = str(d.get("id") or f"constraint_{i:02d}")
    d["scope"] = d.get("scope") if isinstance(d.get("scope"), dict) else {}
    d["type"] = str(d.get("type") or "preference")
    d["reference"] = _str_or_none(d.get("reference"))
    d["preference"] = _sv(d.get("preference"))
    d["raw"] = str(d.get("raw") or d["id"])
    if d.get("constraint_level") not in _LEVELS:
        d["constraint_level"] = "hard"
    d["confidence"] = _num(d.get("confidence"))
    return d


def _clean_op(d: Dict[str, Any], i: int) -> Optional[Dict[str, Any]]:
    """operation 非法或 target 不可用的 op 整条丢弃（而不是让整批校验失败）。"""
    d = _pick(d, _OP_KEYS)
    if d.get("operation") not in _OPERATIONS:
        return None
    target = _pick(d.get("target"), _TARGET_KEYS)
    target["type"] = str(target.get("type") or "object_reference")
    target["value"] = str(target.get("value") or "")
    if not target["value"]:
        return None
    d["id"] = str(d.get("id") or f"op_{i:02d}")
    d["target"] = target
    d["parameters"] = (
        d.get("parameters") if isinstance(d.get("parameters"), dict) else {})
    d["source_text"] = str(d.get("source_text") or d["id"])
    if d.get("constraint_level") not in _LEVELS:
        d["constraint_level"] = "hard"
    d["confidence"] = _num(d.get("confidence"))
    return d


def _clean_unresolved(d: Dict[str, Any], i: int) -> Dict[str, Any]:
    d = _pick(d, _UNRESOLVED_KEYS)
    d["type"] = str(d.get("type") or "object_reference")
    d["raw"] = str(d.get("raw") or "")
    d["candidates"] = [str(x) for x in _as_list(d.get("candidates"))]
    d["confidence"] = _num(d.get("confidence"), 0.0)
    d["reason"] = _str_or_none(d.get("reason"))
    return d
