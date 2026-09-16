from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from gesture_intent import IntentParser, IntentParserInput, IntentStateManager
from gesture_intent.extractors import OpenAICompatibleExtractor
from gesture_intent.models import (
    Constraint,
    EditingIntent,
    EventBoundRequirement,
    EventReference,
    EventRequirement,
    EventTrigger,
    ExplicitOperation,
    IntentPatch,
    ObjectAction,
    ObjectRequirement,
    ObjectType,
    OperationType,
    RequestType,
    SemanticProjectObject,
    SemanticProjectView,
    SemanticValue,
    TargetReference,
    model_dump,
)
from gesture_intent.checks import conflicts
from gesture_intent.store import IntentStore


def parse(text: str, **kwargs):
    return IntentParser().parse(IntentParserInput(user_utterance=text, **kwargs))


def test_initial_request_separates_global_event_operation_and_constraint():
    output = parse("把视频做成可爱的夏日海边风格，每次比心的时候出现粉色爱心，最后一个动作定格一秒，音乐欢快一点，爱心不要挡脸。")

    assert output.request_type.value == "initial_edit"
    assert output.editing_intent is not None
    intent = output.editing_intent
    assert "summer" in intent.global_intent.theme.tags
    assert "beach" in intent.global_intent.theme.tags
    assert intent.global_intent.style.tags == ["cute"]
    assert any(item.object_type == ObjectType.music for item in intent.object_requirements)
    assert intent.event_bound_requirements[0].trigger.event.canonical == "heart_gesture"
    assert intent.event_bound_requirements[0].trigger.occurrence.type.value == "all"
    assert intent.event_bound_requirements[0].requirement.semantic_description.raw == "粉色爱心"
    assert intent.explicit_operations[0].operation.value == "freeze"
    assert intent.explicit_operations[0].parameters["duration"]["value"] == 1.0
    assert intent.constraints[0].type == "avoid_overlap"
    assert any(query.type == "person_face_tracking" for query in output.required_video_queries)
    assert all(item.source_text for item in intent.object_requirements + intent.event_bound_requirements + intent.explicit_operations + intent.constraints)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("第一次比心时出现爱心", "first"),
        ("最后一次比心时出现爱心", "last"),
        ("每次比心时出现爱心", "all"),
        ("第二次比心时出现爱心", "index"),
        ("第二次到第四次比心时出现爱心", "range"),
    ],
)
def test_occurrence_forms(text: str, expected: str):
    output = parse(text)
    occurrence = output.editing_intent.event_bound_requirements[0].trigger.occurrence
    assert occurrence.type.value == expected
    if expected == "index":
        assert occurrence.value == 2
    if expected == "range":
        assert (occurrence.start, occurrence.end) == (2, 4)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("比心之前出现爱心", "before_event"),
        ("比心的时候出现爱心", "at_event"),
        ("转身过程中添加旋转效果", "during_event"),
        ("比心之后出现星星", "after_event"),
    ],
)
def test_temporal_relations(text: str, expected: str):
    output = parse(text)
    assert output.editing_intent.event_bound_requirements[0].trigger.temporal_relation.value == expected


def test_pose_and_audio_video_queries_are_targeted():
    output = parse("手举到头顶时出现皇冠，音乐重拍的时候闪一下。")
    query_types = {query.type for query in output.required_video_queries}
    assert "pose_condition_detection" in query_types
    assert "audio_event_detection" in query_types
    assert all(query.type != "detect_all_gestures" for query in output.required_video_queries)


def project_view() -> SemanticProjectView:
    return SemanticProjectView(
        objects=[
            SemanticProjectObject(id="heart_01", object_type=ObjectType.sticker, description="粉色爱心", event_ref="heart_gesture", order=1),
            SemanticProjectObject(id="heart_02", object_type=ObjectType.sticker, description="粉色爱心", event_ref="heart_gesture", order=2),
            SemanticProjectObject(id="text_ending_01", object_type=ObjectType.text, description="Summer!", order=3),
            SemanticProjectObject(id="music_01", object_type=ObjectType.music, description="summer pop", order=4),
        ]
    )


def test_revision_resolves_object_references_and_reports_affected_objects():
    current = parse("背景换成海边，每次比心时出现粉色爱心")
    output = IntentParser().parse(
        IntentParserInput(
            user_utterance="第二个爱心小一点，最后那个文字改成 Hello Summer，音乐再小一点",
            current_effective_intent=current.editing_intent,
            semantic_project_view=project_view(),
        )
    )

    assert output.request_type.value == "revision"
    assert output.intent_patch is not None
    assert {item.target_id for item in output.resolved_references} == {"heart_02", "text_ending_01", "music_01"}
    assert set(output.affected_objects) == {"heart_02", "text_ending_01", "music_01"}
    operations = output.intent_patch.add_operations
    assert {item.operation.value for item in operations} == {"scale_adjust", "replace_text", "volume_adjust"}
    text_op = next(item for item in operations if item.operation.value == "replace_text")
    assert text_op.parameters["value"] == "Hello Summer"
    assert not output.unresolved


def test_ambiguous_reference_is_unresolved():
    current = parse("每次比心时出现爱心")
    output = IntentParser().parse(
        IntentParserInput(
            user_utterance="那个爱心小一点",
            current_effective_intent=current.editing_intent,
            semantic_project_view=project_view(),
        )
    )
    assert len(output.unresolved) == 1
    assert output.unresolved[0].candidates == ["heart_01", "heart_02"]
    assert not output.affected_objects


def test_patch_preserves_old_intent():
    first = parse("背景换成海边")
    second = parse("音乐换得欢快一点", current_effective_intent=first.editing_intent, semantic_project_view=SemanticProjectView(objects=[]))
    assert second.intent_patch is not None
    updated = IntentStateManager().apply_patch(first.editing_intent, second.intent_patch)
    assert any(item.object_type == ObjectType.background for item in updated.object_requirements)
    assert any(item.object_type == ObjectType.music for item in updated.object_requirements)


def test_addition_event_patch_and_removal_patch_apply_to_state():
    current = parse("每次比心时出现爱心")
    addition = IntentParser().parse(
        IntentParserInput(
            user_utterance="转身过程中添加旋转效果",
            current_effective_intent=current.editing_intent,
            semantic_project_view=SemanticProjectView(objects=[]),
        )
    )
    assert addition.intent_patch is not None
    assert addition.intent_patch.add_event_bound_requirements
    updated = IntentStateManager().apply_patch(current.editing_intent, addition.intent_patch)
    assert any(item.trigger.event.canonical == "turn_body" for item in updated.event_bound_requirements)

    removal = IntentParser().parse(
        IntentParserInput(
            user_utterance="最后那个爱心删掉",
            current_effective_intent=updated,
            semantic_project_view=project_view(),
        )
    )
    assert removal.intent_patch is not None
    assert removal.intent_patch.remove_object_requirement_ids == ["heart_02"]


def test_global_revision_is_a_patch():
    current = parse("背景换成海边")
    output = parse("整体做成可爱风格", current_effective_intent=current.editing_intent)
    assert output.intent_patch is not None
    assert "style" in output.intent_patch.global_updates
    updated = IntentStateManager().apply_patch(current.editing_intent, output.intent_patch)
    assert updated.global_intent.style.tags == ["cute"]


def test_conflict_detection():
    output = parse("保持原视频长度，最后一个动作定格两秒，不要修改原来的音乐，音乐换成欢快音乐")
    conflict_types = {item.type for item in output.conflicts}
    assert "duration_conflict" in conflict_types
    assert "original_music_conflict" in conflict_types


def test_invalid_llm_result_falls_back_to_rules():
    class BadExtractor:
        def extract(self, input, request_type):
            return {"editing_intent": {"confidence": 2}}

    output = IntentParser(extractor=BadExtractor()).parse(IntentParserInput(user_utterance="每次比心时出现爱心"))
    assert output.editing_intent is not None
    assert output.parser_mode == "rules_fallback"
    assert output.fallback_reason.startswith("ValidationError")
    assert output.editing_intent.event_bound_requirements[0].trigger.event.canonical == "heart_gesture"


def test_openai_compatible_adapter_uses_standard_environment_and_json_mode(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": json.dumps({"event_bound_requirements": []})}}]}).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["authorization"] = req.get_header("Authorization")
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "unit-test-model")
    monkeypatch.delenv("INTENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("INTENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("INTENT_LLM_MODEL", raising=False)
    monkeypatch.delenv("INTENT_LLM_RESPONSE_FORMAT", raising=False)
    monkeypatch.setattr("gesture_intent.extractors.request.urlopen", fake_urlopen)

    adapter = OpenAICompatibleExtractor.from_environment()
    assert adapter is not None
    result = adapter.extract(IntentParserInput(user_utterance="测试"), RequestType.initial_edit)

    assert result == {"event_bound_requirements": []}
    assert captured["url"] == "https://models.sjtu.edu.cn/api/v1/chat/completions"
    assert captured["authorization"] == "Bearer unit-test-key"
    assert captured["payload"]["model"] == "unit-test-model"
    assert captured["payload"]["response_format"] == {"type": "json_object"}


def test_openai_compatible_adapter_can_request_json_schema(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"{\\"event_bound_requirements\\":[]}"}}]}'

    def fake_urlopen(req, timeout):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("INTENT_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("INTENT_LLM_RESPONSE_FORMAT", "json_schema")
    monkeypatch.setattr("gesture_intent.extractors.request.urlopen", fake_urlopen)
    adapter = OpenAICompatibleExtractor.from_environment()
    assert adapter is not None
    adapter.extract(IntentParserInput(user_utterance="测试"), RequestType.initial_edit)
    response_format = captured["payload"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"]["title"] == "EditingIntent"


def test_deepseek_reasoner_payload_omits_temperature_and_accepts_fenced_json(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            content = "```json\n{\"event_bound_requirements\": []}\n```"
            return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setenv("INTENT_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("INTENT_LLM_BASE_URL", "https://models.sjtu.edu.cn/api/v1")
    monkeypatch.setenv("INTENT_LLM_MODEL", "deepseek-reasoner")
    monkeypatch.delenv("INTENT_LLM_RESPONSE_FORMAT", raising=False)
    monkeypatch.setattr("gesture_intent.extractors.request.urlopen", fake_urlopen)

    adapter = OpenAICompatibleExtractor.from_environment()
    assert adapter is not None
    result = adapter.extract(IntentParserInput(user_utterance="测试"), RequestType.initial_edit)
    assert result == {"event_bound_requirements": []}
    assert "temperature" not in captured["payload"]


def test_schema_rejects_invalid_confidence():
    with pytest.raises(ValidationError):
        IntentParserInput(
            user_utterance="测试",
            current_effective_intent=EditingIntent(
                object_requirements=[
                    ObjectRequirement(
                        id="bad",
                        object_type=ObjectType.sticker,
                        action=ObjectAction.add,
                        source_text="测试",
                        confidence=2,
                    )
                ]
            ),
        )


def test_json_store_round_trip(tmp_path):
    output = parse("背景换成海边")
    store = IntentStore(tmp_path)
    store.save_state(output.editing_intent, utterances=["背景换成海边"], version=2)
    store.append_history("背景换成海边", output)
    restored = store.load_current_intent()
    assert model_dump(restored) == model_dump(output.editing_intent)
    assert len(store.load_history()) == 1
    state = store.load_state()
    assert state["version"] == 2
    assert state["requirement_ids"]


def test_cli_json_round_trip(tmp_path):
    input_path = tmp_path / "request.json"
    input_path.write_text(json.dumps({"user_utterance": "每次比心时出现粉色爱心"}, ensure_ascii=False), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "gesture_intent", "parse", "--input", str(input_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, "PYTHONPATH": "src"},
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["request_type"] == "initial_edit"
    assert result["editing_intent"]["event_bound_requirements"][0]["trigger"]["event"]["canonical"] == "heart_gesture"


@pytest.mark.parametrize(
    ("text", "canonical", "occurrence"),
    [
        ("每次比心的时候，出现粉色爱心", "heart_gesture", "all"),
        ("每次比心时，出现粉色爱心", "heart_gesture", "all"),
        ("每次比心的时候。出现粉色爱心", "heart_gesture", "all"),
        ("每次比心的时候，屏幕上出现粉色爱心", "heart_gesture", "all"),
        ("第二次比心，出现爱心", "heart_gesture", "index"),
        ("每次挥手，加一个爱心", "wave_hand", "all"),
        ("每次点赞的时候，弹出星星", "thumbs_up", "all"),
        ("手举到头顶的时候，出现皇冠", None, "all"),
        ("音乐重拍的时候，闪一下", "downbeat", "all"),
        ("音乐开始的时候，出现爱心", "music_onset", "all"),
    ],
)
def test_trigger_and_requirement_split_by_punctuation(text, canonical, occurrence):
    """A comma/period between trigger and requirement must not drop the binding."""
    output = parse(text)
    requirements = output.editing_intent.event_bound_requirements
    assert len(requirements) == 1
    trigger = requirements[0].trigger
    assert trigger.event.canonical == canonical
    assert trigger.occurrence.type.value == occurrence
    assert requirements[0].requirement.semantic_description.raw


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("比心之前，出现星星", "before_event"),
        ("转身过程中，添加旋转效果", "during_event"),
        ("比心之后，出现星星", "after_event"),
        ("每次比心的时候，出现爱心", "at_event"),
    ],
)
def test_temporal_relation_across_punctuation(text, expected):
    output = parse(text)
    assert output.editing_intent.event_bound_requirements[0].trigger.temporal_relation.value == expected


def test_constraint_clause_between_trigger_and_requirement_still_binds():
    output = parse("每次比心的时候，不要挡脸，出现爱心")
    intent = output.editing_intent
    assert intent.event_bound_requirements[0].trigger.event.canonical == "heart_gesture"
    assert intent.event_bound_requirements[0].requirement.semantic_description.raw == "爱心"
    assert any(item.type == "avoid_overlap" for item in intent.constraints)


def test_following_independent_requirement_is_not_absorbed():
    output = parse("每次比心的时候，出现粉色爱心，音乐欢快一点")
    intent = output.editing_intent
    assert intent.event_bound_requirements[0].trigger.event.canonical == "heart_gesture"
    assert intent.event_bound_requirements[0].requirement.semantic_description.raw == "粉色爱心"
    assert any(item.object_type == ObjectType.music for item in intent.object_requirements)


def test_complete_following_event_clause_keeps_its_own_trigger():
    output = parse("每次挥手的时候，每次比心时出现爱心")
    requirements = output.editing_intent.event_bound_requirements
    assert len(requirements) == 1
    assert requirements[0].trigger.event.canonical == "heart_gesture"


def test_event_word_as_object_does_not_absorb_next_clause():
    output = parse("想做爱心，加一个星星")
    intent = output.editing_intent
    assert not intent.event_bound_requirements
    assert any(
        item.object_type == ObjectType.sticker and item.description.raw == "星星"
        for item in intent.object_requirements
    )


def test_lone_trigger_without_requirement_produces_nothing():
    output = parse("每次比心的时候")
    assert not output.editing_intent.event_bound_requirements


@pytest.mark.parametrize(
    "text",
    [
        '最后加一段文字"你好，世界"',
        "最后加一段文字“你好，世界”",
        "加一段文字：'Hello, World'",
        "加一段文字「你好，世界」",
    ],
)
def test_quoted_text_keeps_inner_punctuation(text):
    output = parse(text)
    text_requirements = [
        item for item in output.editing_intent.object_requirements if item.object_type == ObjectType.text
    ]
    assert len(text_requirements) == 1
    assert "，" in text_requirements[0].content or "," in text_requirements[0].content


def test_patch_path_merges_trigger_clauses_too():
    current = parse("背景换成海边")
    output = IntentParser().parse(
        IntentParserInput(
            user_utterance="每次挥手的时候，加一个星星",
            current_effective_intent=current.editing_intent,
        )
    )
    assert output.intent_patch is not None
    additions = output.intent_patch.add_event_bound_requirements
    assert len(additions) == 1
    assert additions[0].trigger.event.canonical == "wave_hand"


def test_event_bound_audio_and_pose_queries_across_comma():
    output = parse("手举到头顶的时候，出现皇冠，音乐重拍的时候，闪一下。")
    query_types = {query.type for query in output.required_video_queries}
    assert "pose_condition_detection" in query_types
    assert "audio_event_detection" in query_types


def test_event_bound_quoted_text_keeps_inner_punctuation():
    output = parse('每次比心的时候，出现文字"你好，世界"')
    requirement = output.editing_intent.event_bound_requirements[0]
    assert requirement.trigger.event.canonical == "heart_gesture"
    assert requirement.requirement.object_type == ObjectType.text
    assert requirement.requirement.content == "你好，世界"


def test_revision_delete_explicit_operation_applied():
    current = EditingIntent()
    current.explicit_operations.append(
        ExplicitOperation(
            id="operation_01",
            operation=OperationType.scale_adjust,
            target=TargetReference(type="object_reference", value="爱心"),
            parameters={"direction": "larger"},
            source_text="把爱心放大",
        )
    )
    output = parse("把那个爱心效果删掉", current_effective_intent=current)
    patch = output.intent_patch
    assert patch is not None
    assert "operation_01" in patch.remove_operation_ids
    updated = IntentStateManager().apply_patch(current, patch)
    assert all(op.id != "operation_01" for op in updated.explicit_operations)


def test_revision_delete_event_bound_requirement_applied():
    current = EditingIntent()
    current.event_bound_requirements.append(
        EventBoundRequirement(
            id="event_req_01",
            source_text="比心出现爱心",
            trigger=EventTrigger(event=EventReference(type="gesture", raw="比心", canonical="heart_gesture")),
            requirement=EventRequirement(object_type=ObjectType.sticker, action=ObjectAction.add, semantic_description=SemanticValue(raw="爱心")),
        )
    )
    output = parse("删掉那个比心特效", current_effective_intent=current)
    patch = output.intent_patch
    assert patch is not None
    assert "event_req_01" in patch.remove_event_bound_requirement_ids
    updated = IntentStateManager().apply_patch(current, patch)
    assert all(req.id != "event_req_01" for req in updated.event_bound_requirements)


def test_volume_adjust_does_not_trigger_original_music_conflict():
    output = parse("音乐调低一点，不要修改原来的音乐", current_effective_intent=EditingIntent())
    assert "original_music_conflict" not in [c.type for c in output.conflicts]


def test_music_replace_still_conflicts_after_resolution_to_object_id():
    current = EditingIntent()
    current.object_requirements.append(
        ObjectRequirement(
            id="req_music_01",
            object_type=ObjectType.music,
            action=ObjectAction.replace,
            description=SemanticValue(raw="欢快音乐", tags=["cheerful"]),
            source_text="换成欢快音乐",
        )
    )
    patch = IntentPatch(
        add_operations=[
            ExplicitOperation(
                id="op_01",
                operation=OperationType.replace_asset,
                target=TargetReference(type="object_id", value="req_music_01"),
                parameters={},
                source_text="换个音乐",
            )
        ],
        add_constraints=[Constraint(id="c_01", scope={}, type="preserve_original_music", raw="保留原曲")],
    )
    assert any(c.type == "original_music_conflict" for c in conflicts(patch=patch, intent=current))


def test_remove_event_bound_object_via_project_view_id():
    """把爱心删掉 with a project view: removes both the project object and the
    backing event binding, so apply_patch actually drops it."""
    current = parse("每次比心时出现粉色爱心")
    event_req_id = current.editing_intent.event_bound_requirements[0].id
    view = SemanticProjectView(
        objects=[
            SemanticProjectObject(id="heart_02", object_type=ObjectType.sticker, description="粉色爱心", event_ref="heart_gesture", order=2),
        ]
    )
    output = IntentParser().parse(
        IntentParserInput(
            user_utterance="把爱心删掉",
            current_effective_intent=current.editing_intent,
            semantic_project_view=view,
        )
    )
    patch = output.intent_patch
    assert patch is not None
    assert "heart_02" in patch.remove_object_requirement_ids
    assert event_req_id in patch.remove_event_bound_requirement_ids
    updated = IntentStateManager().apply_patch(current.editing_intent, patch)
    assert not updated.event_bound_requirements


def test_remove_ordinal_instance_keeps_the_binding():
    """把第二个爱心删掉 removes that instance only — the binding stays."""
    current = parse("每次比心时出现粉色爱心")
    event_req_id = current.editing_intent.event_bound_requirements[0].id
    view = SemanticProjectView(
        objects=[
            SemanticProjectObject(id="heart_01", object_type=ObjectType.sticker, description="粉色爱心", event_ref="heart_gesture", order=1),
            SemanticProjectObject(id="heart_02", object_type=ObjectType.sticker, description="粉色爱心", event_ref="heart_gesture", order=2),
        ]
    )
    output = IntentParser().parse(
        IntentParserInput(
            user_utterance="把第二个爱心删掉",
            current_effective_intent=current.editing_intent,
            semantic_project_view=view,
        )
    )
    patch = output.intent_patch
    assert patch.remove_object_requirement_ids == ["heart_02"]
    assert event_req_id not in patch.remove_event_bound_requirement_ids
    updated = IntentStateManager().apply_patch(current.editing_intent, patch)
    assert updated.event_bound_requirements  # binding survives


def test_remove_ambiguous_event_binding_is_unresolved():
    """Two heart-producing bindings and no ordinal: report candidates instead
    of silently picking one."""
    current = parse("每次比心时出现爱心，每次挥手时出现爱心")
    assert len(current.editing_intent.event_bound_requirements) == 2
    output = parse("把爱心删掉", current_effective_intent=current.editing_intent)
    patch = output.intent_patch
    expected = {req.id for req in current.editing_intent.event_bound_requirements}
    assert not patch.remove_event_bound_requirement_ids
    assert not patch.remove_object_requirement_ids
    assert len(output.unresolved) == 1
    assert set(output.unresolved[0].candidates) == expected


def test_remove_resolved_requirement_id_routes_directly():
    """No project view: an event-bound requirement is itself addressable and
    its id routes to remove_event_bound_requirement_ids."""
    current = parse("每次比心时出现粉色爱心")
    event_req_id = current.editing_intent.event_bound_requirements[0].id
    output = parse("把那个爱心删掉", current_effective_intent=current.editing_intent)
    patch = output.intent_patch
    assert event_req_id in patch.remove_event_bound_requirement_ids
    updated = IntentStateManager().apply_patch(current.editing_intent, patch)
    assert not updated.event_bound_requirements


def test_remove_music_conflict_after_resolution_to_view_object_id():
    """preserve_original_music + 把音乐删掉 resolving to a view id still reports
    the conflict."""
    current = parse("背景换成海边")
    view = SemanticProjectView(
        objects=[SemanticProjectObject(id="music_01", object_type=ObjectType.music, description="summer pop", order=1)]
    )
    output = IntentParser().parse(
        IntentParserInput(
            user_utterance="不要修改原来的音乐，把音乐删掉",
            current_effective_intent=current.editing_intent,
            semantic_project_view=view,
        )
    )
    assert "original_music_conflict" in {item.type for item in output.conflicts}


def test_cross_turn_constraint_conflict():
    """A constraint from turn 1 conflicts with an operation in turn 2."""
    current = parse("不要修改原来的音乐")
    output = parse("把音乐删掉", current_effective_intent=current.editing_intent)
    assert "original_music_conflict" in {item.type for item in output.conflicts}


def test_object_attribute_does_not_leak_into_global_intent():
    output = parse("每次比心时出现蓝色爱心")
    assert output.editing_intent.global_intent.color_preference is None

    output = parse("背景换成海边")
    intent = output.editing_intent
    assert intent.global_intent.theme is None  # 海边修饰背景而非全局主题
    assert any(item.object_type == ObjectType.background for item in intent.object_requirements)


def test_global_intent_still_reads_unconsumed_words():
    output = parse("每次比心时出现蓝色爱心，整体要粉色一点")
    intent = output.editing_intent
    assert intent.global_intent.color_preference is not None
    assert intent.global_intent.color_preference.tags == ["pink"]


def test_bare_jia_is_an_event_action_word():
    output = parse("每次挥手时加爱心")
    requirement = output.editing_intent.event_bound_requirements[0]
    assert requirement.trigger.event.canonical == "wave_hand"
    assert requirement.requirement.semantic_description.raw == "爱心"

    output = parse("每次挥手时，加爱心")
    requirement = output.editing_intent.event_bound_requirements[0]
    assert requirement.trigger.event.canonical == "wave_hand"
    assert requirement.requirement.semantic_description.raw == "爱心"


def test_zeroth_occurrence_falls_back_to_all_without_crashing():
    output = parse("第0次比心时出现爱心")
    occurrence = output.editing_intent.event_bound_requirements[0].trigger.occurrence
    assert occurrence.type.value == "all"


def test_flash_effect_is_an_add_not_replace_asset():
    output = parse("音乐重拍的时候闪一下")
    requirement = output.editing_intent.event_bound_requirements[0]
    assert requirement.requirement.object_type == ObjectType.effect
    assert requirement.requirement.operation is None


def test_custom_extractor_reports_custom_mode():
    class CustomExtractor:
        def extract(self, input, request_type):
            return {"editing_intent": {}}

    output = IntentParser(extractor=CustomExtractor()).parse(IntentParserInput(user_utterance="比心出现爱心"))
    assert output.parser_mode == "custom"


def test_cli_accepts_utf8_bom_file(tmp_path):
    input_path = tmp_path / "bom.json"
    input_path.write_bytes(b'\xef\xbb\xbf' + json.dumps({"user_utterance": "每次比心时出现爱心"}, ensure_ascii=False).encode("utf-8"))
    completed = subprocess.run(
        [sys.executable, "-m", "gesture_intent", "parse", "--input", str(input_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, "PYTHONPATH": "src"},
        check=True,
    )
    assert json.loads(completed.stdout)["request_type"] == "initial_edit"


def test_history_tolerates_a_torn_trailing_line(tmp_path):
    output = parse("背景换成海边")
    store = IntentStore(tmp_path)
    store.append_history("背景换成海边", output)
    with store.history_path.open("a", encoding="utf-8") as handle:
        handle.write('{"utterance": "半截写入')
    assert len(store.load_history()) == 1
