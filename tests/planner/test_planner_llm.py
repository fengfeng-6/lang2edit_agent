"""Creative Planner LLM 接缝（§19/§61）：契约、白名单、回退、适配器。

LLM 是规划中唯一的非确定性环节，只写创作字段（animation /
emphasis / palette / duration_hint / relation_preference /
motion_language）。时间戳、event_uid、坐标、镜头运动强度、
无障碍策略永远由确定性代码决定——这些测试钉住这条边界，
并覆盖轮椅/低幅度画像下 LLM 输出与兜底的行为。
"""

from __future__ import annotations

import json

import pytest

from planner_fixtures import event_req, heart_intent, make_view
from gesture_intent.models import (
    ConstraintLevel,
    EditingIntent,
    GlobalIntent,
    ObjectType,
    SemanticValue,
)

from editing_planner import EditingPlanner
from editing_planner.llm.planner import (
    OpenAICompatibleCreativePlanner,
    RuleBasedCreativePlanner,
    default_creative_planner,
)
from editing_planner.models import (
    AccessibilityPlanningProfile,
    GlobalStrategy,
    PlanItemStatus,
    PlanOperation,
    StyleSpec,
)
from editing_planner.strategy.creative import (
    build_creative_context,
    merge_creative_output,
    requirement_digest,
)


class RecordingLLM:
    """记录上下文、返回固定输出的假 LLM。"""

    name = "llm"

    def __init__(self, output=None, error=None):
        self.output = output if output is not None else {
            "global_strategy": {"motion_language": ["glow"]},
            "item_directives": [],
        }
        self.error = error
        self.contexts = []

    def plan(self, context):
        self.contexts.append(context)
        if self.error is not None:
            raise self.error
        return self.output


def _plan(intent=None, planner=None, profile=None, view=None):
    payload = {
        "editing_intent": intent or heart_intent(),
        "semantic_view": view or make_view(),
    }
    if profile is not None:
        payload["accessibility_profile"] = profile
    return EditingPlanner(creative_planner=planner or RecordingLLM()).plan(payload)


# ---------------------------------------------------------------------------
# 上下文契约：LLM 看得到什么、看不到什么
# ---------------------------------------------------------------------------


def test_context_carries_accessibility_profile():
    """轮椅用户画像必须喂给 LLM——它才能做"低幅度→语义增强"类创作决策。"""
    profile = AccessibilityPlanningProfile(
        posture="seated", active_hands=["right"], motion_amplitude="low",
        tremor=True, preserve_mobility_device=True,
    )
    llm = RecordingLLM()
    _plan(planner=llm, profile=profile)
    ctx = llm.contexts[0]
    assert ctx["accessibility_profile"]["posture"] == "seated"
    assert ctx["accessibility_profile"]["active_hands"] == ["right"]
    assert ctx["accessibility_profile"]["motion_amplitude"] == "low"
    assert ctx["accessibility_profile"]["tremor"] is True
    # 需求摘要带 id 与约束等级——LLM 按 id 回指
    ids = {r["id"] for r in ctx["requirements"]}
    assert "event_req_01" in ids
    assert all("constraint_level" in r for r in ctx["requirements"])


def test_context_has_no_video_facts():
    """§61：LLM 拿不到事件 uid/坐标/时间戳——它不产生视频事实。"""
    llm = RecordingLLM()
    _plan(planner=llm)
    ctx = llm.contexts[0]
    blob = json.dumps(ctx, ensure_ascii=False)
    assert "relevant_events" not in ctx
    assert "spatial_summaries" not in ctx
    assert "event_uid" not in blob
    assert "evt_" not in blob  # fixture 事件 uid 都是 evt_ 前缀
    assert "peak_time" not in blob


# ---------------------------------------------------------------------------
# 白名单合并（merge_creative_output 单元行为）
# ---------------------------------------------------------------------------


def test_merge_rejects_non_dict_and_junk_lists():
    directives, _ = merge_creative_output("garbage", GlobalStrategy())
    assert directives == {}
    directives, _ = merge_creative_output(
        {"item_directives": "not_a_list"}, GlobalStrategy()
    )
    assert directives == {}
    directives, _ = merge_creative_output(
        {"item_directives": [{"animation": "pop"}]}, GlobalStrategy()
    )
    assert directives == {}  # 无 requirement_id 的指令丢弃


def test_merge_invalid_enums_fall_back_to_defaults():
    directives, _ = merge_creative_output(
        {"item_directives": [{
            "requirement_id": "r1",
            "animation": "explosion",        # 非白名单
            "emphasis": "maximum",           # 非法枚举
            "relation_preference": "behind", # 非法枚举
        }]},
        GlobalStrategy(),
    )
    spec = directives["r1"]
    assert spec.animation == "pop"
    assert spec.emphasis == "medium"
    assert spec.relation_preference is None


def test_merge_clamps_duration_and_truncates_lists():
    directives, strategy = merge_creative_output(
        {
            "global_strategy": {"motion_language": [f"m{i}" for i in range(20)]},
            "item_directives": [
                {"requirement_id": "r_low", "duration_hint": -5.0},
                {"requirement_id": "r_high", "duration_hint": 99.0},
                {"requirement_id": "r_str", "duration_hint": "abc"},
                {"requirement_id": "r_pal",
                 "palette": ["pink", 3, None, "gold", {}, "x", "y", "z", "w"]},
            ],
        },
        GlobalStrategy(),
    )
    assert len(strategy.motion_language) == 8  # 上限截断
    assert directives["r_low"].duration_hint == 0.2
    assert directives["r_high"].duration_hint == 5.0
    assert directives["r_str"].duration_hint is None
    palette = directives["r_pal"].palette
    assert len(palette) <= 6
    assert all(isinstance(c, str) for c in palette)


def test_merge_does_not_touch_deterministic_strategy_fields():
    """LLM 的 global_strategy 只补措辞——不能改 camera_motion/layout。"""
    raw = {"global_strategy": {
        "motion_language": ["camera_shake", "aggressive_zoom"],
        "layout_strategy": "full_body_aware",        # 越权字段
        "camera_motion_intensity": "high",           # 越权字段
    }}
    _, strategy = merge_creative_output(raw, GlobalStrategy())
    assert strategy.motion_language == ["camera_shake", "aggressive_zoom"]
    assert strategy.layout_strategy == "upper_body_aware"  # 未被改写
    assert strategy.accessibility_strategy.camera_motion_intensity == "low"


# ---------------------------------------------------------------------------
# 端到端：LLM 指令落到 StyleSpec，事实字段不动摇
# ---------------------------------------------------------------------------


def test_llm_directive_lands_on_style_only():
    llm = RecordingLLM(output={
        "global_strategy": {"motion_language": ["flash"]},
        "item_directives": [{
            "requirement_id": "event_req_01",
            "animation": "flash", "emphasis": "high",
            "palette": ["pink"], "duration_hint": 4.9,
            "relation_preference": "below",
            "event_uid": "evt_forged",          # 越权
            "start_time": 0.0,                  # 越权
        }],
    })
    plan = _plan(planner=llm)
    assert plan.provenance.planner_mode == "llm"
    item = plan.plan_items[0]
    assert item.style_spec.animation == "flash"
    assert item.style_spec.emphasis == "high"
    assert item.style_spec.duration_hint == 4.9
    # duration_hint 同步进 preferred 时长
    assert item.temporal_spec.duration.value == 4.9
    # 伪造事实字段不生效：锚点仍指向真实事件
    assert item.spatial_spec.anchor.event_uid in ("evt_h1", "evt_h2")
    assert item.target["value"] in ("evt_h1", "evt_h2")


def test_llm_forged_requirement_id_ignored():
    """LLM 回指不存在的 requirement_id → 指令被丢弃，项保持默认样式。"""
    llm = RecordingLLM(output={
        "item_directives": [{
            "requirement_id": "req_does_not_exist",
            "animation": "flash", "emphasis": "high",
        }],
    })
    plan = _plan(planner=llm)
    for item in plan.plan_items:
        assert item.style_spec.animation == "pop"   # StyleSpec 默认
        assert item.style_spec.emphasis == "medium"


def test_llm_partial_coverage_others_default():
    """LLM 只给部分需求指令 → 其余需求保持默认而非报错。"""
    intent = EditingIntent(event_bound_requirements=[
        event_req("r_heart", "heart_gesture", description="爱心", canonical_desc="heart"),
        event_req("r_point", "point_right", description="箭头", canonical_desc="arrow"),
    ])
    llm = RecordingLLM(output={"item_directives": [
        {"requirement_id": "r_heart", "animation": "particle", "emphasis": "high"},
    ]})
    plan = _plan(intent=intent, planner=llm)
    heart = next(i for i in plan.plan_items if "r_heart" in i.source_requirement_ids)
    point = next(i for i in plan.plan_items if "r_point" in i.source_requirement_ids)
    assert heart.style_spec.animation == "particle"
    assert point.style_spec.animation == "pop"


# ---------------------------------------------------------------------------
# LLM 不能绕过无障碍确定性约束（轮椅场景）
# ---------------------------------------------------------------------------


def test_llm_cannot_enable_follow():
    """relation_preference="follow" 只是位置措辞——不会开启轨迹跟随。

    follow 由需求文本触发（"跟着/跟随"）+ 能力链决定，LLM 措辞
    不能让素材开始追踪人脸（隐私/能力边界）。
    """
    llm = RecordingLLM(output={"item_directives": [
        {"requirement_id": "event_req_01", "relation_preference": "follow"},
    ]})
    plan = _plan(planner=llm)
    item = plan.plan_items[0]
    assert item.operation == PlanOperation.add_overlay  # 未变 track_overlay
    assert item.spatial_spec.follow.enabled is False
    assert item.spatial_spec.anchor.type.value == "event_anchor"  # 未变 spatial_track


def test_llm_cannot_raise_camera_motion():
    """低幅度画像下 LLM 写再激进的措辞，镜头运动强度仍是确定性 low。"""
    llm = RecordingLLM(output={
        "global_strategy": {"motion_language": ["camera_shake", "whip_pan"]},
        "item_directives": [{"requirement_id": "event_req_01", "animation": "flash"}],
    })
    plan = _plan(planner=llm)  # fixture 默认 seated + low amplitude
    assert plan.provenance.planner_mode == "llm"
    strategy = plan.global_strategy.accessibility_strategy
    assert strategy.camera_motion_intensity == "low"
    # 措辞被记录为 motion_language，但不构成 accessibility 违规
    assert "camera_shake" in plan.global_strategy.motion_language
    assert not any(
        i.code == "unnecessary_camera_motion"
        for i in plan.validation.accessibility.issues
    )


def test_llm_preference_not_exempt_from_accessibility_validation():
    """LLM 偏好 below → 坐姿下落入下三分之一 → seated_composition 仍报警。"""
    llm = RecordingLLM(output={"item_directives": [
        {"requirement_id": "event_req_01", "relation_preference": "below"},
    ]})
    plan = _plan(planner=llm)
    codes = [i.code for i in plan.validation.accessibility.issues]
    assert "seated_composition" in codes


def test_llm_duration_hint_clamped_end_to_end():
    """LLM 给 99s 时长 → 钳到 5.0 后才写入 temporal_spec。"""
    llm = RecordingLLM(output={"item_directives": [
        {"requirement_id": "event_req_01", "duration_hint": 99.0},
    ]})
    plan = _plan(planner=llm)
    assert all(i.temporal_spec.duration.value <= 5.0 for i in plan.plan_items)


# ---------------------------------------------------------------------------
# 回退路径：LLM 挂了，轮椅适配不丢
# ---------------------------------------------------------------------------


def test_llm_failure_falls_back_to_rules():
    """LLM 抛错 → rules_fallback + fallback_reason + 规则指令仍按低幅度出 soft_pop。"""
    llm = RecordingLLM(error=RuntimeError("api down"))
    plan = _plan(planner=llm)
    assert plan.provenance.planner_mode == "rules_fallback"
    assert "api down" in plan.provenance.fallback_reason
    # fixture 画像 low amplitude → 规则兜底仍走 §20 白名单
    assert plan.plan_items[0].style_spec.animation == "soft_pop"
    assert plan.plan_items[0].style_spec.emphasis == "high"


def test_llm_non_dict_output_merges_empty_safely():
    """LLM 返回非 dict（异常但不抛错）→ 空指令集，计划照常产出。"""
    llm = RecordingLLM(output="not_a_dict")
    plan = _plan(planner=llm)
    assert plan.provenance.planner_mode == "llm"
    assert plan.plan_items
    assert all(i.style_spec.animation == "pop" for i in plan.plan_items)


def test_custom_planner_mode_recorded():
    class Custom:
        name = "inhouse_v1"

        def plan(self, context):
            return {"item_directives": []}

    plan = _plan(planner=Custom())
    assert plan.provenance.planner_mode == "custom"


def test_llm_style_jitter_absorbed_by_replan():
    """§52：LLM 重跑出不同 style → replan 补丁为空（style 不算结构变化）。"""
    base = EditingPlanner()
    plan = base.plan({
        "editing_intent": heart_intent(), "semantic_view": make_view(),
    })
    jittery = RecordingLLM(output={"item_directives": [
        {"requirement_id": "event_req_01", "animation": "glow",
         "emphasis": "low", "palette": ["blue"]},
    ]})
    planner2 = EditingPlanner(creative_planner=jittery)
    patch = planner2.replan(plan, heart_intent(), semantic_view=make_view())
    assert not patch.add_plan_items
    assert not patch.update_plan_items
    assert not patch.remove_plan_item_uids


# ---------------------------------------------------------------------------
# OpenAI-compatible 适配器（mock urlopen，不发真实请求）
# ---------------------------------------------------------------------------


def _capture_urlopen(monkeypatch, response_body):
    """替换 urllib.request.urlopen，捕获请求并返回固定响应。"""
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(response_body).encode("utf-8")

    def fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["data"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake)
    return captured


def _ok_body(directives):
    return {"choices": [{"message": {"content": json.dumps(directives)}}]}


def test_adapter_sends_contract_and_auth(monkeypatch):
    captured = _capture_urlopen(monkeypatch, _ok_body(
        {"global_strategy": {"motion_language": []}, "item_directives": []}
    ))
    planner = OpenAICompatibleCreativePlanner(
        "https://example.com/v1", "sk-test", "some-chat-model"
    )
    out = planner.plan({"requirements": []})
    assert captured["url"] == "https://example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    data = captured["data"]
    assert data["response_format"] == {"type": "json_object"}
    assert data["temperature"] == 0  # 非 reasoner 模型 → 显式 temperature
    assert data["messages"][0]["role"] == "system"
    assert "item_directives" in data["messages"][0]["content"]
    assert out["item_directives"] == []


def test_adapter_omits_temperature_for_reasoner(monkeypatch):
    captured = _capture_urlopen(monkeypatch, _ok_body({"item_directives": []}))
    planner = OpenAICompatibleCreativePlanner(
        "https://example.com", "sk-test", "deepseek-reasoner"
    )
    planner.plan({})
    assert "temperature" not in captured["data"]
    assert captured["url"] == "https://example.com/chat/completions"


def test_adapter_json_schema_response_format(monkeypatch):
    monkeypatch.setenv("PLANNER_LLM_RESPONSE_FORMAT", "json_schema")
    captured = _capture_urlopen(monkeypatch, _ok_body({"item_directives": []}))
    planner = OpenAICompatibleCreativePlanner("https://example.com", "k", "m")
    planner.plan({})
    fmt = captured["data"]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "creative_directives"


def test_adapter_strips_markdown_fence_and_joins_content(monkeypatch):
    inner = {"global_strategy": {"motion_language": ["pop"]}, "item_directives": []}
    body = {"choices": [{"message": {"content": [
        {"type": "text", "text": "```json\n" + json.dumps(inner)[:20]},
        {"type": "text", "text": json.dumps(inner)[20:] + "\n```"},
    ]}}]}
    _capture_urlopen(monkeypatch, body)
    planner = OpenAICompatibleCreativePlanner("https://example.com", "k", "m")
    out = planner.plan({})
    assert out["global_strategy"]["motion_language"] == ["pop"]


def test_adapter_http_error_raises_runtimeerror(monkeypatch):
    from urllib import error as urlerror

    def boom(req, timeout=None):
        raise urlerror.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    planner = OpenAICompatibleCreativePlanner("https://example.com", "k", "m")
    with pytest.raises(RuntimeError, match="creative planner request failed"):
        planner.plan({})


def test_adapter_error_falls_back_end_to_end(monkeypatch):
    """真实适配器 HTTP 失败 → plan() 层接住 → rules_fallback。"""
    from urllib import error as urlerror

    def boom(req, timeout=None):
        raise urlerror.URLError("timeout")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    adapter = OpenAICompatibleCreativePlanner("https://example.com", "k", "m")
    plan = EditingPlanner(creative_planner=adapter).plan({
        "editing_intent": heart_intent(), "semantic_view": make_view(),
    })
    assert plan.provenance.planner_mode == "rules_fallback"
    assert plan.provenance.fallback_reason


def test_from_environment_key_and_overrides(monkeypatch):
    for var in ("PLANNER_LLM_API_KEY", "PLANNER_LLM_BASE_URL", "PLANNER_LLM_MODEL",
                "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert OpenAICompatibleCreativePlanner.from_environment() is None

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    planner = OpenAICompatibleCreativePlanner.from_environment()
    assert planner.api_key == "sk-openai"
    assert planner.base_url == OpenAICompatibleCreativePlanner.DEFAULT_BASE_URL

    monkeypatch.setenv("PLANNER_LLM_API_KEY", "sk-planner")
    monkeypatch.setenv("PLANNER_LLM_BASE_URL", "https://planner.example.com/")
    monkeypatch.setenv("PLANNER_LLM_MODEL", "planner-model")
    planner = OpenAICompatibleCreativePlanner.from_environment()
    assert planner.api_key == "sk-planner"  # PLANNER_* 优先
    assert planner.base_url == "https://planner.example.com"  # 尾斜杠去掉
    assert planner.model == "planner-model"


def test_default_planner_switches_on_env(monkeypatch):
    for var in ("PLANNER_LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert isinstance(default_creative_planner(), RuleBasedCreativePlanner)
    monkeypatch.setenv("PLANNER_LLM_API_KEY", "sk-x")
    assert isinstance(default_creative_planner(), OpenAICompatibleCreativePlanner)
