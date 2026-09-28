"""Fingerprint（§31）：对象与图的确定性内容指纹。

只包含影响最终结果的属性：asset / media / time / transform /
animation / keyframes / tracking / mask / parameters。
不包含：uid、object_key、origin、provenance、backend_object_ref、运行时状态。
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict

from gesture_intent.models import model_dump

from ..models import DesiredProjectGraph, TimelineObject, canonical_json

_FINGERPRINT_FIELDS = (
    "object_type",
    "role",
    "track_uid",
    "media_ref",
    "asset_uid",
    "project_time",
    "transform",
    "animation",
    "keyframes",
    "tracking",
    "mask_ref",
    "parameters",
)


def object_fingerprint(obj: TimelineObject) -> str:
    """F_o = H(CanonicalSerialize(o))（§31）。"""
    payload = model_dump(obj)
    body: Dict[str, Any] = {
        key: payload.get(key) for key in _FINGERPRINT_FIELDS
    }
    return hashlib.sha1(canonical_json(body).encode("utf-8")).hexdigest()


def graph_fingerprint(graph: DesiredProjectGraph) -> str:
    """整图指纹：项目规格 + 轨道 + 对象指纹 + 媒体引用（§31 扩展）。"""
    body = {
        "project_spec": model_dump(graph.project_spec),
        "tracks": {
            uid: model_dump(t) for uid, t in sorted(graph.tracks.items())
        },
        "objects": {
            uid: (obj.fingerprint or object_fingerprint(obj))
            for uid, obj in sorted(graph.objects.items())
        },
        "media_refs": {
            uid: model_dump(m) for uid, m in sorted(graph.media_refs.items())
        },
        "total_duration": graph.total_duration,
    }
    return hashlib.sha1(canonical_json(body).encode("utf-8")).hexdigest()
