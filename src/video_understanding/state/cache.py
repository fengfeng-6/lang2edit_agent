"""Query Cache 与 Query Coverage（§45-46）。

缓存基于归一化查询而非原始字符串。覆盖关系 Q_a ⊇ Q_b 表示
已有查询 a 的分析结果足以回答 b：

- 检测目标键（type/event/condition/reference）相同；
- a 的 required_occurrence 覆盖 b 的：``all`` 覆盖一切；
  ``first``/``index(1)`` 互为覆盖；``range`` 覆盖其中的 index。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gesture_intent.models import OccurrenceType

from ..events.router import normalize_query, query_key
from ..models import SemanticEvent


def _occurrence(query: Dict[str, Any]) -> Dict[str, Any]:
    return query.get("required_occurrence") or {"type": "all"}


def occurrence_covers(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """a 的 occurrence 范围是否覆盖 b 的。"""
    ta, tb = a.get("type", "all"), b.get("type", "all")
    if ta == tb and a == b:
        return True
    if ta == "all":
        return True
    # first ≡ index(1)
    if {ta, tb} == {"first", "index"}:
        return (b if ta == "first" else a).get("value") == 1
    if ta == "index" and tb == "index":
        return a.get("value") == b.get("value")
    if ta == "range" and tb == "index":
        v = b.get("value")
        return v is not None and a.get("start", 1) <= v <= (a.get("end") or v)
    if ta == "range" and tb == "range":
        return a.get("start", 1) <= b.get("start", 1) and (a.get("end") or 10**9) >= (b.get("end") or 0)
    return False


def query_covers(existing: Dict[str, Any], new: Dict[str, Any]) -> bool:
    """Q_existing ⊇ Q_new：检测目标相同且 occurrence 覆盖。"""
    if query_key(existing) != query_key(new):
        return False
    return occurrence_covers(_occurrence(normalize_query(existing)),
                             _occurrence(normalize_query(new)))


def select_by_occurrence(events: List[SemanticEvent], occurrence: Optional[Dict[str, Any]]) -> List[SemanticEvent]:
    """按 required_occurrence 从事件列表（已按时间排序、带 occurrence_index）过滤。"""
    occ = occurrence or {"type": "all"}
    otype = occ.get("type", "all")
    if otype == OccurrenceType.all.value or otype == "all":
        return list(events)
    if otype == "first":
        return events[:1]
    if otype == "last":
        return events[-1:] if events else []
    if otype == "index":
        idx = occ.get("value")
        if idx is None:
            return list(events)
        picked = [e for e in events if e.occurrence_index == idx]
        return picked or (events[idx - 1:idx] if 0 < idx <= len(events) else [])
    if otype == "range":
        start, end = occ.get("start") or 1, occ.get("end") or len(events)
        return [e for e in events if e.occurrence_index is not None and start <= e.occurrence_index <= end]
    return list(events)
