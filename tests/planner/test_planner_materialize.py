"""Plan Materializer（§50-51/§56）：绑定 → 工程时间 + 坐标。"""

from __future__ import annotations

from planner_fixtures import freeze_intent, heart_intent, make_view

from editing_planner import EditingPlanner
from editing_planner.models import PlanItemStatus


def _plan(intent=None, view=None):
    return EditingPlanner().plan({
        "editing_intent": intent or heart_intent(),
        "semantic_view": view or make_view(),
    })


def _bindings(uid="asset_req_sticker_01", asset="asset_heart_17"):
    return [{
        "asset_request_uid": uid,
        "asset_uid": asset,
        "uri": "s3://x/heart.png",
        "media_type": "image",
        "metadata": {"has_alpha": True, "width": 1024, "height": 1024},
        "match_score": 0.91,
    }]


def test_scenario56_end_to_end():
    """§56：每次比心出现粉色爱心不挡脸 —— plan→materialize 全链路。"""
    view = make_view()
    plan = _plan(view=view)
    items = [i for i in plan.plan_items if i.operation.value == "add_overlay"]
    assert len(items) == 2  # 两次比心各一项
    assert len(plan.asset_requests) == 1  # 同款爱心去重（§36）

    mat = EditingPlanner().materialize(plan, bindings=_bindings(), semantic_view=view)
    assert len(mat.resolved_items) == 2
    for ri in mat.resolved_items:
        assert ri.asset_uid == "asset_heart_17"  # 共享素材
        assert ri.status in (PlanItemStatus.planned, PlanItemStatus.degraded)
        assert ri.transform is not None
        # 位置在锚点上方附近，且不与脸 bbox 重叠
        x, y = ri.transform.position
        assert 0 <= x <= 1 and 0 <= y <= 1
        half = ri.transform.scale / 2
        face = (0.38, 0.06, 0.62, 0.26)  # fixture 保护区
        ix = min(x + half, face[2]) - max(x - half, face[0])
        iy = min(y + half, face[3]) - max(y - half, face[1])
        assert ix <= 0 or iy <= 0 or (ix * iy) / ((2 * half) ** 2) < 0.05
    # 时间落在事件峰附近（peak ± beat_snap/offset 容差）
    starts = sorted(i.project_time.start for i in mat.resolved_items)
    assert abs(starts[0] - 4.35) < 0.35
    assert abs(starts[1] - 10.35) < 0.35


def test_freeze_shifts_project_time():
    """freeze insert_duration：其后的源时间整体平移。"""
    plan = _plan(intent=freeze_intent())
    freeze = next(i for i in plan.plan_items if i.operation.value == "freeze")
    view = make_view()
    mat = EditingPlanner().materialize(plan, bindings=[], semantic_view=view)
    assert mat.timeline_mapping.total_duration == 13.0  # 12 + 1s 定格
    assert mat.timeline_mapping.shifts, "应有移位表"
    shift = mat.timeline_mapping.shifts[0]
    assert shift["delta"] == 1.0


def test_missing_binding_soft_skips_hard_unfulfilled():
    plan = _plan()  # hard 需求
    mat = EditingPlanner().materialize(plan, bindings=[], semantic_view=make_view())
    assert all(i.status == PlanItemStatus.unfulfilled
               for i in mat.resolved_items)


def test_follow_trajectory_ref_without_tracks():
    """无 spatial_tracks 输入 → follow 保留 trajectory_ref（非阻塞）。"""
    from planner_fixtures import follow_intent
    plan = _plan(intent=follow_intent())
    follow_item = next(i for i in plan.plan_items
                       if i.spatial_spec and i.spatial_spec.follow.enabled)
    mat = EditingPlanner().materialize(plan, bindings=[
        {"asset_request_uid": follow_item.asset_request_ref,
         "asset_uid": "asset_crown", "uri": "", "media_type": "image"},
    ], semantic_view=make_view(), spatial_tracks=None)
    ri = next(i for i in mat.resolved_items
              if i.plan_item_uid == follow_item.plan_item_uid)
    assert ri.follow is not None
    assert "trajectory_ref" in ri.follow


def test_follow_keyframes_with_tracks():
    from planner_fixtures import follow_intent
    plan = _plan(intent=follow_intent())
    follow_item = next(i for i in plan.plan_items
                       if i.spatial_spec and i.spatial_spec.follow.enabled)
    track = {
        "target": "head",
        "points": [{"t": t * 0.1, "x": 0.5 + 0.01 * t, "y": 0.2} for t in range(60)],
        "smoothed": True,
    }
    mat = EditingPlanner().materialize(
        plan,
        bindings=[{"asset_request_uid": follow_item.asset_request_ref,
                   "asset_uid": "asset_crown", "uri": "", "media_type": "image"}],
        semantic_view=make_view(),
        spatial_tracks={"head": track},
    )
    ri = next(i for i in mat.resolved_items
              if i.plan_item_uid == follow_item.plan_item_uid)
    assert ri.follow["mode"] == "keyframes"
    assert ri.follow["keyframes"], "应抽稀出关键帧"
    assert all(0 <= k["x"] <= 1 for k in ri.follow["keyframes"])
