"""AssetBinding 管理（§63-64/§50/§85）。

- ``BindingRecord`` 是模块四内部的富绑定；同一 request 多版本并存，
  只有最新 ``active=True``（§64）；
- ``alternatives`` 存候选池里的 candidate_uid——切换时拿原候选的
  source_ref 再走 Import，不用重新联网搜（§85）；
- 对外映射模块三简版 ``AssetBinding``（uri/media_type/match_score +
  metadata 捎带 alternatives 等富字段）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from editing_planner.models import AssetBinding, AssetRequest
from gesture_intent.models import model_dump

from ..models import AssetRecord, BindingRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BindingManager:
    def __init__(self, bindings: Dict[str, List[BindingRecord]]):
        self._bindings = bindings  # request_uid → [版本链]
        self._seq = sum(len(v) for v in bindings.values())

    def active_for(self, request_uid: str) -> Optional[BindingRecord]:
        for binding in self._bindings.get(request_uid, []):
            if binding.active:
                return binding
        return None

    def create(
        self,
        request: AssetRequest,
        record: AssetRecord,
        score: float,
        breakdown: Dict[str, float],
        alternatives: List[str],
        warnings: Optional[List[str]] = None,
        fallback_used: bool = False,
    ) -> BindingRecord:
        """新版本绑定：同 request 旧绑定全部下架为历史。"""

        for old in self._bindings.get(request.request_uid, []):
            old.active = False
        self._seq += 1
        binding = BindingRecord(
            binding_uid=f"bind_{self._seq:04d}",
            asset_request_uid=request.request_uid,
            request_version=request.version,
            asset_uid=record.asset_uid,
            selected_origin=record.origins[-1] if record.origins else None,
            selection_score=round(score, 6),
            score_breakdown={k: round(v, 6) for k, v in breakdown.items()},
            alternatives=list(alternatives),
            fallback_used=fallback_used,
            active=True,
            warnings=list(warnings or []),
            created_at=_now(),
        )
        self._bindings.setdefault(request.request_uid, []).append(binding)
        return binding

    def to_planner(
        self, binding: BindingRecord, record: Optional[AssetRecord]
    ) -> AssetBinding:
        """模块四富绑定 → 模块三契约（§73）。"""

        return AssetBinding(
            asset_request_uid=binding.asset_request_uid,
            asset_uid=binding.asset_uid,
            uri=record.local_uri if record else "",
            media_type=record.media_type if record else "",
            metadata={
                "binding_uid": binding.binding_uid,
                "request_version": binding.request_version,
                "alternatives": list(binding.alternatives),
                "score_breakdown": dict(binding.score_breakdown),
                "fallback_used": binding.fallback_used,
                "warnings": list(binding.warnings),
                "selected_origin": (
                    model_dump(binding.selected_origin)
                    if binding.selected_origin is not None else {}
                ),
            },
            match_score=binding.selection_score,
        )
