"""Detection Strategy Router（§14）：为每个 required_video_query 选分析策略。

    Query
      ↓
    Is standard event? ──Yes──→ Dedicated Detector（registry.supported）
      ↓ No
    Can it be represented by pose/motion geometry? ──Yes──→ Pose / Motion Rule
      ↓ No
    Open Semantic Detector

查询归一化为确定性的 query_key / query_id，是缓存覆盖（§45-46）的基础。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from gesture_intent.models import RequiredVideoQuery, model_dump, model_validate

from . import registry


def normalize_query(query: Any) -> Dict[str, Any]:
    """RequiredVideoQuery / dict → 归一化 dict（canonical 名归一 + 键稳定）。"""
    if isinstance(query, dict):
        query = model_validate(RequiredVideoQuery, query)
    data = model_dump(query)
    if data.get("event"):
        data["event"] = registry.resolve_canonical(data["event"])
    return data


def query_key(query: Any) -> str:
    """查询语义键：忽略 occurrence 的检测目标键（occurrence 只是事后过滤）。"""
    data = normalize_query(query)
    return json.dumps(
        {
            "type": data.get("type"),
            "event": data.get("event"),
            "condition": data.get("condition"),
            "reference": data.get("reference"),
        },
        sort_keys=True, ensure_ascii=False,
    )


def query_id_for(query: Any) -> str:
    """确定性 query_id：同一归一化查询（含 occurrence）稳定同 ID。"""
    data = normalize_query(query)
    digest = hashlib.sha1(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:10]
    return f"query_{digest}"


@dataclass
class RoutedPlan:
    query_id: str
    query: Dict[str, Any]
    strategy: str
    canonical: Optional[str] = None
    entry: Optional[registry.EventRegistryEntry] = None
    condition: Optional[Dict[str, Any]] = None
    description: str = ""  # open_semantic 的原始语义描述
    error: Optional[str] = None  # 非 None 时 query 直接判 failed


def route(query: Any) -> RoutedPlan:
    data = normalize_query(query)
    qtype = data.get("type", "")
    canonical = registry.resolve_canonical(data["event"]) if data.get("event") else None
    plan = RoutedPlan(query_id=query_id_for(data), query=data, strategy="", canonical=canonical)

    if qtype == "event_detection":
        if not canonical:
            plan.strategy = "open_semantic"
            plan.description = data.get("event") or ""
            plan.error = "event_detection query missing event name"
            return plan
        entry = registry.lookup(canonical)
        if entry is None:
            # 模块一对 first/last_action、音频事件也发 event_detection
            # （来源是 BODY/AUDIO 同义词表），按对应策略分发而非落入
            # 开放语义——无 verifier 时会白拿一个 failed。
            if registry.is_structural(canonical):
                plan.strategy = "structural"
                return plan
            if registry.is_audio(canonical):
                plan.strategy = "audio_analyzer"
                return plan
            plan.strategy = "open_semantic"
            plan.description = canonical
            return plan
        plan.entry = entry
        if not entry.supported or not entry.detector:
            plan.strategy = "dedicated_detector"
            plan.error = f"event '{canonical}' registered but has no detector yet"
            return plan
        plan.strategy = "dedicated_detector"
        return plan

    if qtype == "pose_condition_detection":
        plan.strategy = "pose_motion_rule"
        plan.condition = data.get("condition") or {}
        if not plan.condition:
            plan.error = "pose_condition_detection query missing condition"
        return plan

    if qtype == "audio_event_detection":
        plan.strategy = "audio_analyzer"
        if not canonical:
            plan.error = "audio_event_detection query missing event name"
        elif not registry.is_audio(canonical):
            plan.error = f"audio event '{canonical}' not supported in MVP"
        return plan

    if qtype == "video_structure_detection":
        if canonical and registry.is_structural(canonical):
            plan.strategy = "structural"
            return plan
        # ending_pose 等虽是结构查询也可走 dedicated detector
        entry = registry.lookup(canonical) if canonical else None
        if entry and entry.supported and entry.detector:
            plan.entry = entry
            plan.strategy = "dedicated_detector"
            return plan
        plan.strategy = "structural"
        plan.error = f"video structure event '{canonical or data.get('event')}' not supported"
        return plan

    if qtype in ("person_tracking", "person_face_tracking"):
        plan.strategy = "base_spatial"
        return plan

    plan.strategy = "open_semantic"
    plan.description = canonical or data.get("event") or qtype
    plan.error = f"unknown query type '{qtype}'"
    return plan
