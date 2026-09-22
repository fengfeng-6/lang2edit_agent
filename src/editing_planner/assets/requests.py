"""Asset Requirement Builder（§34-36）：素材需求生成 + 去重。

Planner 只产生语义需求——"每次比心出现同样爱心"对应
``reuse_same_asset``：同 canonical+类型+风格标签的需求共享一个
AssetRequest，而不是为每个事件搜一次（§36）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gesture_intent.models import ObjectType, SemanticValue

from ..models import AssetRequest, ReusePolicy

#: object_type → (asset_type, media_type)；text 不是外部素材（§35）
_ASSET_TYPE: Dict[ObjectType, Optional[tuple]] = {
    ObjectType.background: ("background", "image"),
    ObjectType.sticker: ("sticker", "image"),
    ObjectType.image: ("image", "image"),
    ObjectType.overlay: ("sticker", "image"),
    ObjectType.music: ("music", "audio"),
    ObjectType.sound_effect: ("sound_effect", "audio"),
    ObjectType.effect: (None, None),  # 特效由执行端合成，不进素材库
    ObjectType.text: (None, None),
}


def semantic_query_of(value: Optional[SemanticValue], fallback: str) -> str:
    """SemanticValue → 素材检索语义串：raw 保留原文，canonical/tags 补充。"""
    if value is None:
        return fallback
    parts = [value.raw]
    if value.canonical and value.canonical not in value.raw:
        parts.append(value.canonical)
    parts.extend(t for t in value.tags if t not in parts)
    return " ".join(p for p in parts if p)


def dedup_key_for(
    asset_type: str, value: Optional[SemanticValue], style_tags: List[str]
) -> str:
    """去重键：asset_type + canonical/raw + 全局风格标签（§36）。"""
    descriptor = ""
    if value is not None:
        descriptor = value.canonical or value.raw
    return "|".join([asset_type, descriptor, ",".join(sorted(style_tags))])


class AssetRequestBuilder:
    """按 dedup_key 去重，request_uid 按出现顺序确定性编号。"""

    def __init__(self, style_context: Optional[Dict[str, Any]] = None):
        self.style_context = dict(style_context or {})
        self._by_key: Dict[str, AssetRequest] = {}
        self._seq: Dict[str, int] = {}

    def request_for(
        self,
        object_type: ObjectType,
        description: Optional[SemanticValue],
        *,
        constraint_level,
        usage: Optional[Dict[str, Any]] = None,
        technical: Optional[Dict[str, Any]] = None,
    ) -> Optional[AssetRequest]:
        """同需求复用同一 AssetRequest；text/effect 返回 None。"""
        pair = _ASSET_TYPE.get(object_type)
        if not pair or pair[0] is None:
            return None
        asset_type, media_type = pair
        style_tags = list(self.style_context.get("visual_language", []))
        key = dedup_key_for(asset_type, description, style_tags)
        existing = self._by_key.get(key)
        if existing is not None:
            bound = existing.usage_context.get("bound_items", 0)
            existing.usage_context["bound_items"] = bound + 1
            return existing

        self._seq[asset_type] = self._seq.get(asset_type, 0) + 1
        request = AssetRequest(
            request_uid=f"asset_req_{asset_type}_{self._seq[asset_type]:02d}",
            asset_type=asset_type,
            media_type=media_type,
            semantic_query=semantic_query_of(description, fallback=object_type.value),
            style_context=dict(self.style_context),
            technical_requirements=dict(technical or _default_technical(object_type)),
            usage_context={"bound_items": 1, **dict(usage or {})},
            reuse_policy=ReusePolicy.reuse_same_asset,
            constraint_level=constraint_level,
            dedup_key=key,
        )
        self._by_key[key] = request
        return request

    def requests(self) -> List[AssetRequest]:
        return list(self._by_key.values())


def _default_technical(object_type: ObjectType) -> Dict[str, Any]:
    if object_type in (ObjectType.sticker, ObjectType.overlay, ObjectType.image):
        return {"has_alpha": True}
    return {}
