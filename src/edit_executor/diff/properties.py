"""Property-Level Diff（§33-§34）。

发现 UPDATE 后不再 delete+create，而分析具体属性：
    media_ref/asset_uid           → replace_media（素材替换 uid 不变 §34）
    project_time                  → set_time_range
    transform.{position,scale,rotation} → set_transform（只带变化键）
    animation                     → set_animation
    keyframes                     → set_keyframes
    mask_ref                      → set_mask
    parameters.{content,font,size,alignment,style} → set_text
    parameters.{volume_db,mute_ranges}            → set_volume
    parameters.source_time/freeze_duration        → freeze_frame
    tracking/object_type/role/track_uid/origin    → recreate 兜底
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from gesture_intent.models import model_dump

from ..dsl.models import EditOpType
from ..models import TimelineObject

RECREATE = "recreate"

_TEXT_KEYS = ("content", "font", "size", "alignment", "style")
_VOLUME_KEYS = ("volume_db", "mute_ranges")
_FREEZE_KEYS = ("source_time", "freeze_duration")


@dataclass
class PropertyChange:
    property: str
    op_type: str  # EditOpType value 或 RECREATE
    arguments: Dict[str, Any] = field(default_factory=dict)


def compute_property_diff(
    old: TimelineObject, new: TimelineObject
) -> List[PropertyChange]:
    # recreate 兜底：身份级属性变化无法用 DSL 表达，重建（uid 保持不变）
    for prop in ("object_type", "role", "track_uid", "origin"):
        if getattr(old, prop) != getattr(new, prop):
            return [
                PropertyChange(property=prop, op_type=RECREATE, arguments={})
            ]
    if old.tracking != new.tracking:
        return [
            PropertyChange(property="tracking", op_type=RECREATE, arguments={})
        ]

    changes: List[PropertyChange] = []
    if old.media_ref != new.media_ref or old.asset_uid != new.asset_uid:
        changes.append(
            PropertyChange(
                property="media_ref",
                op_type=EditOpType.replace_media.value,
                arguments={"media_ref": new.media_ref},
            )
        )
    if old.project_time != new.project_time:
        changes.append(
            PropertyChange(
                property="project_time",
                op_type=EditOpType.set_time_range.value,
                arguments={
                    "start": new.project_time.start,
                    "end": new.project_time.end,
                },
            )
        )
    transform_args = _transform_diff(old, new)
    if transform_args:
        changes.append(
            PropertyChange(
                property="transform",
                op_type=EditOpType.set_transform.value,
                arguments=transform_args,
            )
        )
    if old.animation != new.animation:
        changes.append(
            PropertyChange(
                property="animation",
                op_type=EditOpType.set_animation.value,
                arguments=(
                    model_dump(new.animation) if new.animation is not None else {}
                ),
            )
        )
    if old.keyframes != new.keyframes:
        changes.append(
            PropertyChange(
                property="keyframes",
                op_type=EditOpType.set_keyframes.value,
                arguments={
                    "keyframes": [
                        model_dump(kf) for kf in new.keyframes
                    ]
                },
            )
        )
    if old.mask_ref != new.mask_ref:
        changes.append(
            PropertyChange(
                property="mask_ref",
                op_type=EditOpType.set_mask.value,
                arguments={"artifact_uid": new.mask_ref},
            )
        )
    text_args = {
        key: new.parameters[key]
        for key in _TEXT_KEYS
        if old.parameters.get(key) != new.parameters.get(key)
    }
    if text_args:
        changes.append(
            PropertyChange(
                property="parameters.text",
                op_type=EditOpType.set_text.value,
                arguments=text_args,
            )
        )
    volume_args = {
        key: new.parameters[key]
        for key in _VOLUME_KEYS
        if old.parameters.get(key) != new.parameters.get(key)
    }
    if volume_args:
        changes.append(
            PropertyChange(
                property="parameters.volume",
                op_type=EditOpType.set_volume.value,
                arguments=volume_args,
            )
        )
    freeze_args = {
        key: new.parameters.get(key)
        for key in _FREEZE_KEYS
        if old.parameters.get(key) != new.parameters.get(key)
    }
    if freeze_args and new.object_type == "freeze":
        changes.append(
            PropertyChange(
                property="parameters.freeze",
                op_type=EditOpType.freeze_frame.value,
                arguments={
                    "source_time": new.parameters.get("source_time"),
                    "duration": new.parameters.get("freeze_duration"),
                },
            )
        )
    # parameters 其余键变化无法映射 DSL 时保守重建
    handled = set(_TEXT_KEYS) | set(_VOLUME_KEYS) | set(_FREEZE_KEYS)
    residual = {
        k
        for k in set(old.parameters) | set(new.parameters)
        if k not in handled and old.parameters.get(k) != new.parameters.get(k)
    }
    if residual:
        return [
            PropertyChange(
                property=f"parameters.{sorted(residual)[0]}",
                op_type=RECREATE,
                arguments={},
            )
        ]
    return changes


def _transform_diff(old: TimelineObject, new: TimelineObject) -> Dict[str, Any]:
    if old.transform is None and new.transform is None:
        return {}
    if old.transform is None or new.transform is None:
        # transform 出现/消失：整组下发
        return model_dump(new.transform) if new.transform is not None else {
            "position": None,
            "scale": None,
            "rotation": None,
        }
    args: Dict[str, Any] = {}
    if old.transform.position != new.transform.position:
        args["position"] = list(new.transform.position)
    if old.transform.scale != new.transform.scale:
        args["scale"] = new.transform.scale
    if old.transform.rotation != new.transform.rotation:
        args["rotation"] = new.transform.rotation
    return args
