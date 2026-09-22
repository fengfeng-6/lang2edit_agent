"""Accessibility Validator（§44）：第一阶段必须实现的独立检查项。

在 ConstraintValidator 之后运行，检查项（§44 十条）：

1. 关键手势区域是否被遮挡        6. 裁剪是否破坏动作表达
2. 脸部是否被遮挡                7. 轨迹跟随是否过度抖动
3. 活跃手部是否被遮挡            8. 是否错误依赖站姿动作
4. 自动布局是否忽略坐姿构图      9. 低幅度动作是否被当作无效
5. 背景替换是否错误移除轮椅     10. 是否使用不必要大幅镜头运动
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..accessibility.policies import (
    DEFAULT_AVOID_IOU_THRESHOLD,
    SEATED_LOWER_THIRD_Y,
    STANDING_ONLY_EVENTS,
)
from ..models import (
    AccessibilityPlanningProfile,
    LogicalEditingPlan,
    PlanItem,
    PlanItemStatus,
    PlanOperation,
    SpatialRelation,
    ValidationIssue,
)
from ..spatial.placement import bbox_around, collect_avoid_regions, iou

BBox = Tuple[float, float, float, float]

_SPATIAL_OPS = {
    PlanOperation.add_overlay,
    PlanOperation.add_text,
    PlanOperation.add_effect,
    PlanOperation.track_overlay,
}


def validate_accessibility(
    plan: LogicalEditingPlan,
    profile: AccessibilityPlanningProfile,
    event_index: Any,  # expansion.EventIndex（避免循环依赖用 Any + duck typing）
    item_positions: Dict[str, Tuple[Tuple[float, float], float]],
    hard_avoid_face: bool = False,
) -> List[ValidationIssue]:
    """返回问题列表；``item_positions`` 是 plan 阶段已算出的
    uid → (position, scale) 映射；``hard_avoid_face`` 表示存在
    hard 级 avoid_overlap(face) 用户约束。"""
    issues: List[ValidationIssue] = []
    tau = DEFAULT_AVOID_IOU_THRESHOLD

    for item in plan.plan_items:
        if item.operation not in _SPATIAL_OPS:
            continue
        if item.status in (PlanItemStatus.blocked, PlanItemStatus.skipped,
                           PlanItemStatus.unfulfilled, PlanItemStatus.unsupported):
            continue
        spec = item.spatial_spec
        if spec is None:
            continue
        brief = _item_brief(item, event_index)
        position, scale = item_positions.get(item.plan_item_uid, (None, spec.scale_policy.get("value", 0.16)))
        if position is None:
            continue
        box = bbox_around(position, scale / 2.0)

        # 1-3. 遮挡检查：face / active_gesture / active_hands
        avoid = collect_avoid_regions(
            brief, ["face", "active_gesture", "active_hands"], profile, asset_half=scale / 2.0
        )
        for name, priority, region in avoid:
            overlap = iou(box, region)
            if overlap <= 0:
                continue
            if priority == 0:
                severity = "hard" if hard_avoid_face else "warning"
                issues.append(ValidationIssue(
                    code="face_occlusion",
                    severity=severity,
                    message=f"{item.plan_item_uid} 与人脸区域重叠 (IoU={overlap:.2f})",
                    item_uids=[item.plan_item_uid],
                    requirement_ids=item.source_requirement_ids,
                ))
            elif priority == 1 and overlap >= tau:
                issues.append(ValidationIssue(
                    code="gesture_region_occlusion" if name == "active_gesture" else "active_hands_occlusion",
                    severity="warning",
                    message=f"{item.plan_item_uid} 遮挡{name} (IoU={overlap:.2f})",
                    item_uids=[item.plan_item_uid],
                    requirement_ids=item.source_requirement_ids,
                ))

        # 4. 坐姿构图：素材被放进画面下三分之一"站姿假设"槽位
        if profile.posture == "seated" and position[1] > SEATED_LOWER_THIRD_Y:
            issues.append(ValidationIssue(
                code="seated_composition",
                severity="warning",
                message=(
                    f"{item.plan_item_uid} 落在画面下三分之一（坐姿主体上半身构图"
                    "之外），请确认是否有意为之"
                ),
                item_uids=[item.plan_item_uid],
            ))

    # 5. 背景替换保护轮椅（§24/§59）：preserve_mobility_device 时
    #    该项不得是 planned——必须由依赖请求挂起
    if profile.preserve_mobility_device:
        for item in plan.plan_items:
            if item.operation == PlanOperation.replace_background and item.status == PlanItemStatus.planned:
                issues.append(ValidationIssue(
                    code="mobility_device_risk",
                    severity="hard",
                    message=(
                        "背景替换在 preserve_mobility_device 下未挂起依赖——"
                        "不能静默生成可能缺失轮椅的抠像结果"
                    ),
                    item_uids=[item.plan_item_uid],
                    requirement_ids=item.source_requirement_ids,
                ))

    # 6. 裁剪安全：main_video 上的 scale/position_adjust 不得把 P0/P1 区移出画面
    for item in plan.plan_items:
        if item.operation in (PlanOperation.scale_adjust, PlanOperation.position_adjust):
            if item.target.get("type") in ("video", "person"):
                issues.append(ValidationIssue(
                    code="crop_safety",
                    severity="hard",
                    message=f"{item.plan_item_uid} 对主体的缩放/移动可能破坏动作表达（§55 宁可少裁）",
                    item_uids=[item.plan_item_uid],
                    requirement_ids=item.source_requirement_ids,
                ))

    # 7. 跟随抖动：tremor 主体的 follow 必须是强平滑参数
    if profile.tremor:
        for item in plan.plan_items:
            spec = item.spatial_spec
            if spec is None or not spec.follow.enabled:
                continue
            if spec.follow.smoothing == "none" or spec.follow.keyframe_density > 0.5:
                issues.append(ValidationIssue(
                    code="follow_jitter",
                    severity="hard",
                    message=(
                        f"{item.plan_item_uid} 跟随参数未按震颤主体稳定化"
                        "（需 strong 平滑 + 低关键帧密度）"
                    ),
                    item_uids=[item.plan_item_uid],
                ))

    # 8. 站姿动作依赖：坐姿主体不得引用 jump/squat/stand_up
    if profile.posture == "seated":
        for item in plan.plan_items:
            brief = _item_brief(item, event_index)
            canonical = (brief or {}).get("canonical") or ""
            if canonical in STANDING_ONLY_EVENTS:
                issues.append(ValidationIssue(
                    code="standing_event_reliance",
                    severity="hard",
                    message=f"{item.plan_item_uid} 依赖站姿动作 '{canonical}'，坐姿主体不适用",
                    item_uids=[item.plan_item_uid],
                ))

    # 9. 低幅度动作被当无效：任何需求以 low_amplitude/no_motion 为由跳过 → 违规
    for unfulfilled in plan.unresolved:
        if unfulfilled.reason in ("low_amplitude", "no_motion"):
            issues.append(ValidationIssue(
                code="low_amplitude_dismissed",
                severity="hard",
                message=(
                    f"{unfulfilled.requirement_id} 因动作幅度低被跳过——"
                    "视觉表达强度不等于物理位移大小（§3）"
                ),
                requirement_ids=[unfulfilled.requirement_id],
            ))

    # 10. 不必要镜头运动：低幅度/震颤主体 + 无明确用户镜头运动请求
    strategy = plan.global_strategy.accessibility_strategy
    if profile.motion_amplitude in ("low", "very_low") or profile.tremor:
        if strategy.camera_motion_intensity in ("medium", "high"):
            issues.append(ValidationIssue(
                code="unnecessary_camera_motion",
                severity="warning",
                message=(
                    "低幅度/震颤主体采用中高镜头运动强度——"
                    "应优先语义视觉增强而非大幅镜头运动（§20/§57）"
                ),
            ))

    return issues


def _item_brief(item: PlanItem, event_index: Any) -> Optional[Dict[str, Any]]:
    """取 PlanItem 绑定事件的摘要 dict。"""
    event_uid = None
    if item.spatial_spec and item.spatial_spec.anchor:
        event_uid = item.spatial_spec.anchor.event_uid
    if event_uid is None and item.temporal_spec and item.temporal_spec.start_anchor:
        event_uid = item.temporal_spec.start_anchor.event_uid
    if event_uid is None:
        target = item.target or {}
        if target.get("type") == "event":
            event_uid = target.get("value")
    if event_uid is None or event_index is None:
        return None
    brief = event_index.brief(event_uid)
    spatial = event_index.spatial_brief(brief)
    merged = dict(brief or {})
    if spatial:
        merged["_spatial"] = spatial
        merged.update({k: v for k, v in spatial.items() if k not in merged})
    return merged
