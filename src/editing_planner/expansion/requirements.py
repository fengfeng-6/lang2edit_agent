"""Requirement Expander（§9/§14-17）：把事件绑定需求展开到具体 event_uid。

确定性代码——occurrence（first/last/all/index/range）到事件的映射语义
与模块二 ``state/cache.py::select_by_occurrence`` 对齐，只是作用在
SemanticView 的事件摘要 dict 上。缺失事件不猜、不替代：

- 已被模块二分析但无目标（query status=not_found）→ 按约束等级处理；
- 从未被查询过（无 query 记录）→ PlannerDependencyRequest 交给
  Agent Controller 补分析（§46），Planner 自己不调模块二；
- 全部 uncertain + hard 需求 → dependency（重分析/用户确认）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from gesture_intent.models import (
    ConstraintLevel,
    EventBoundRequirement,
    Occurrence,
    OccurrenceType,
    model_dump,
)
from video_understanding.events.router import normalize_query, query_id_for

from ..models import DependencyType, PlannerDependencyRequest, UnfulfilledRequirement

# 事件摘要中视为"可用"的置信状态（§17：uncertain 单独处理）
_USABLE_STATUS = {"confirmed", "candidate"}
_UNCERTAIN_STATUS = "uncertain"


@dataclass
class EventBinding:
    """一次事件实例绑定：一个 event_uid（或 between_events 的一对）。"""

    event_uid: Optional[str] = None
    brief: Optional[Dict[str, Any]] = None
    pair: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None  # between_events
    uncertain: bool = False


@dataclass
class ExpandedRequirement:
    """Requirement Expander 的输出：需求 + 事件实例绑定列表。"""

    requirement: EventBoundRequirement
    bindings: List[EventBinding] = field(default_factory=list)
    missing_reason: Optional[str] = None  # not_found / not_analyzed / occurrence_out_of_range / all_uncertain
    skipped_uncertain: int = 0
    note: str = ""
    query: Optional[Dict[str, Any]] = None  # 回查/依赖请求用的归一化查询

    @property
    def constraint_level(self) -> ConstraintLevel:
        return self.requirement.constraint_level

    @property
    def has_items(self) -> bool:
        return bool(self.bindings)


class EventIndex:
    """SemanticView 事件索引：canonical → 按时间排序的摘要列表。"""

    def __init__(self, semantic_view: Any):
        events = _get(semantic_view, "relevant_events") or []
        self.by_uid: Dict[str, Dict[str, Any]] = {}
        self.by_canonical: Dict[str, List[Dict[str, Any]]] = {}
        for brief in events:
            if brief.get("invalidated"):
                continue
            uid = brief.get("event_uid")
            if uid:
                self.by_uid[uid] = brief
            canonical = brief.get("canonical")
            if canonical:
                self.by_canonical.setdefault(canonical, []).append(brief)
        for group in self.by_canonical.values():
            group.sort(key=lambda b: b.get("start_time", 0.0))
        self.spatial = _get(semantic_view, "spatial_summaries") or {}
        self.structural = _get(semantic_view, "structural") or {}
        self.query_statuses = _get(semantic_view, "query_statuses") or {}

    def events_for(self, canonical: str) -> List[Dict[str, Any]]:
        return self.by_canonical.get(canonical, [])

    def brief(self, event_uid: str) -> Optional[Dict[str, Any]]:
        return self.by_uid.get(event_uid)

    def spatial_brief(self, brief: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """spatial_summaries 以 display_id（无则 event_uid）为键（view.py:130）。"""
        if not brief:
            return None
        return self.spatial.get(brief.get("display_id") or brief.get("event_uid") or "")

    def query_status(self, query: Dict[str, Any]) -> Optional[str]:
        return self.query_statuses.get(query_id_for(query))


def _get(view: Any, name: str) -> Any:
    if isinstance(view, dict):
        return view.get(name)
    return getattr(view, name, None)


def required_query_for(requirement: EventBoundRequirement) -> Dict[str, Any]:
    """重建模块一会发给模块二的查询（checks.py:49-87 同形），用于回查状态
    与生成 PlannerDependencyRequest。"""
    event = requirement.trigger.event
    occurrence = model_dump(requirement.trigger.occurrence)
    if event.type.value == "pose_condition":
        return normalize_query(
            {"type": "pose_condition_detection", "condition": event.condition}
        )
    if event.canonical is None:
        return normalize_query({"type": "event_detection", "event": event.raw})
    if event.type.value == "audio_event":
        return normalize_query(
            {
                "type": "audio_event_detection",
                "event": event.canonical,
                "required_occurrence": occurrence,
            }
        )
    if event.type.value == "video_structure":
        return normalize_query(
            {
                "type": "video_structure_detection",
                "event": event.canonical,
                "required_occurrence": occurrence,
            }
        )
    return normalize_query(
        {
            "type": "event_detection",
            "event": event.canonical,
            "required_occurrence": occurrence,
        }
    )


def select_briefs(
    events: List[Dict[str, Any]], occurrence: Optional[Occurrence]
) -> List[Dict[str, Any]]:
    """occurrence → 事件子集（镜像 select_by_occurrence 语义）。"""
    occ = occurrence or Occurrence(type=OccurrenceType.all)
    otype = occ.type.value if isinstance(occ.type, OccurrenceType) else str(occ.type)
    if otype == OccurrenceType.all.value:
        return list(events)
    if otype == OccurrenceType.first.value:
        return events[:1]
    if otype == OccurrenceType.last.value:
        return events[-1:] if events else []
    if otype == OccurrenceType.index.value:
        idx = occ.value
        if idx is None:
            return list(events)
        picked = [e for e in events if e.get("occurrence_index") == idx]
        return picked or (events[idx - 1 : idx] if 0 < idx <= len(events) else [])
    if otype == OccurrenceType.range.value:
        start = occ.start or 1
        end = occ.end if occ.end is not None else len(events)
        return [
            e
            for e in events
            if e.get("occurrence_index") is not None and start <= e["occurrence_index"] <= end
        ]
    return list(events)


def _gap_pairs(
    events: List[Dict[str, Any]], occurrence: Optional[Occurrence]
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """between_events：选中事件与其后继事件之间的间隙。

    - index=i → event[i].end … event[i+1].start
    - range{start,end} → event[start].end … event[end].start
    - all/first/last → 全部相邻间隙
    """
    occ = occurrence or Occurrence(type=OccurrenceType.all)
    otype = occ.type.value if isinstance(occ.type, OccurrenceType) else str(occ.type)
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    if otype == OccurrenceType.index.value and occ.value:
        i = occ.value
        if 0 < i < len(events):
            pairs.append((events[i - 1], events[i]))
        return pairs
    if otype == OccurrenceType.range.value:
        start = occ.start or 1
        end = occ.end if occ.end is not None else len(events)
        if 0 < start <= len(events) and 0 < end <= len(events) and start < end:
            pairs.append((events[start - 1], events[end - 1]))
        return pairs
    for i in range(len(events) - 1):
        pairs.append((events[i], events[i + 1]))
    return pairs


def expand_event_requirement(
    requirement: EventBoundRequirement, index: EventIndex
) -> ExpandedRequirement:
    """展开单个事件绑定需求。"""
    trigger = requirement.trigger
    event = trigger.event
    canonical = event.canonical or event.raw
    out = ExpandedRequirement(requirement=requirement)

    # 结构事件（video_start/end/first_action/last_action）不是
    # relevant_events 里的实例——走 structural 锚点，不产生绑定。
    if event.type.value == "video_structure" or canonical in (
        "video_start",
        "video_end",
        "first_action",
        "last_action",
    ):
        out.bindings = [EventBinding(event_uid=None, brief=None)]
        return out

    query = required_query_for(requirement)
    out.query = query
    events = index.events_for(canonical)

    if not events:
        status = index.query_status(query)
        if status == "not_found":
            out.missing_reason = "not_found"
        elif status in ("failed", "invalidated"):
            out.missing_reason = "analysis_failed"
        elif status is None:
            out.missing_reason = "not_analyzed"
        else:
            out.missing_reason = "not_found"  # completed/low_confidence 但无事件
        return out

    if trigger.temporal_relation.value == "between_events":
        pairs = _gap_pairs(events, trigger.occurrence)
        if not pairs:
            out.missing_reason = "occurrence_out_of_range"
            return out
        out.bindings = [EventBinding(pair=p) for p in pairs]
        return out

    selected = select_briefs(events, trigger.occurrence)
    if not selected:
        out.missing_reason = "occurrence_out_of_range"
        return out

    usable = [b for b in selected if b.get("status") in _USABLE_STATUS]
    uncertain = [b for b in selected if b.get("status") == _UNCERTAIN_STATUS]

    level = requirement.constraint_level
    if level == ConstraintLevel.hard:
        # hard：confirmed/candidate 正常使用；全 uncertain → 依赖请求
        chosen = usable or selected  # 全 uncertain 时先绑定再标记依赖
        out.bindings = [
            EventBinding(
                event_uid=b.get("event_uid"),
                brief=b,
                uncertain=b.get("status") == _UNCERTAIN_STATUS,
            )
            for b in chosen
        ]
        if not usable:
            out.missing_reason = "all_uncertain"
    elif level == ConstraintLevel.soft:
        # soft：只用 confirmed/candidate，uncertain 跳过 + warning
        out.skipped_uncertain = len(uncertain)
        out.bindings = [
            EventBinding(event_uid=b.get("event_uid"), brief=b) for b in usable
        ]
        if not usable:
            out.missing_reason = "all_uncertain"
    else:  # open：全部可用，带注记
        out.bindings = [
            EventBinding(
                event_uid=b.get("event_uid"),
                brief=b,
                uncertain=b.get("status") == _UNCERTAIN_STATUS,
            )
            for b in selected
        ]
        if uncertain:
            out.note = "包含 uncertain 事件（open 需求，允许创作自由度）"
    return out


def expand_requirements(
    intent_requirements: List[EventBoundRequirement], index: EventIndex
) -> List[ExpandedRequirement]:
    return [expand_event_requirement(r, index) for r in intent_requirements]


def missing_to_dependency(
    expanded: ExpandedRequirement, dep_seq: int
) -> Tuple[Optional[PlannerDependencyRequest], Optional[UnfulfilledRequirement], Optional[str]]:
    """把缺失/全部不确定的展开结果翻译成依赖请求 / 未满足需求 / 警告。

    返回 (dependency, unfulfilled, warning)。按 §16-17 与约束等级：

    - not_found + hard → blocked + unfulfilled（第一阶段不自动替代）
    - not_found + soft → warning + skip
    - not_found + open → warning + skip + creative_substitution_allowed 注记
    - not_analyzed / analysis_failed → video_analysis dependency；
      hard 时 blocking（计划 needs_dependency），soft/open 非阻塞 + skip
    - all_uncertain + hard → video_analysis + user_confirmation 依赖（§17）；
      soft → skip + warning；open → 不视为缺失（bindings 已在）
    - occurrence_out_of_range → 同 not_found
    """
    req = expanded.requirement
    level = req.constraint_level
    reason = expanded.missing_reason

    if reason in ("not_found", "occurrence_out_of_range"):
        if level == ConstraintLevel.hard:
            return (
                None,
                UnfulfilledRequirement(
                    requirement_id=req.id,
                    reason=reason,
                    severity="hard",
                    status="blocked",
                ),
                None,
            )
        note = f"{req.id}: 事件未满足（{reason}），soft/open 需求跳过"
        if level == ConstraintLevel.open:
            note += "（creative_substitution_allowed）"
        return None, None, note

    if reason in ("not_analyzed", "analysis_failed"):
        dep = PlannerDependencyRequest(
            request_uid=f"dep_{dep_seq:02d}",
            type=DependencyType.video_analysis,
            required_queries=[expanded.query] if expanded.query else [],
            reason=(
                f"{req.id} 需要的事件 '{req.trigger.event.canonical or req.trigger.event.raw}' "
                f"{'尚未被分析' if reason == 'not_analyzed' else '上次分析失败/已失效'}"
            ),
            blocking=level == ConstraintLevel.hard,
            requirement_ids=[req.id],
        )
        unfulfilled = UnfulfilledRequirement(
            requirement_id=req.id,
            reason=reason,
            severity=level.value,
            status="pending_dependency",
        )
        return dep, unfulfilled, None

    if reason == "all_uncertain":
        if level == ConstraintLevel.hard:
            dep = PlannerDependencyRequest(
                request_uid=f"dep_{dep_seq:02d}",
                type=DependencyType.video_analysis,
                required_queries=[expanded.query] if expanded.query else [],
                reason=(
                    f"{req.id} 选中的事件全部为 uncertain——请重新分析、"
                    "用更强模型验证，或转为请求用户确认（§17）"
                ),
                blocking=True,
                requirement_ids=[req.id],
            )
            return (
                dep,
                UnfulfilledRequirement(
                    requirement_id=req.id,
                    reason="all_uncertain",
                    severity="hard",
                    status="pending_dependency",
                ),
                None,
            )
        # soft：uncertain 已跳过
        return (
            None,
            None,
            f"{req.id}: 全部候选为 uncertain，soft 需求跳过（已跳过 {expanded.skipped_uncertain} 个）",
        )
    return None, None, None
