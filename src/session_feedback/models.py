"""模块六数据模型：会话状态 / 轮次记录 / 反馈结果（§4.6/§9）。

与上游一致：StrictModel + model_dump/model_validate 双版本兼容。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from gesture_intent.models import StrictModel, UnresolvedReference
from pydantic import Field

from edit_executor.models import ExecutionResult, ProjectEditView


class FeedbackStatus(str, Enum):
    applied = "applied"                      # 反馈已并入并落盘
    preview = "preview"                      # dry_run 预览，无副作用
    needs_clarification = "needs_clarification"  # 有未解析引用，等待用户澄清
    no_change = "no_change"                  # patch 为空 / 执行 noop
    switched = "switched"                    # "换一个" fast-path 候选切换成功
    undone = "undone"
    redone = "redone"
    nothing_to_undo = "nothing_to_undo"
    nothing_to_redo = "nothing_to_redo"
    failed = "failed"


class SessionState(StrictModel):
    """session_state.json：跨进程可恢复的会话指针。"""

    project_id: str
    turn: int = 0
    video_id: str = ""
    source_uri: str = ""
    #: 下发过模块二的归一化查询（原始 RequiredVideoQuery dict）——
    #: build_semantic_view(queries=…) 必须携带，否则已分析但无结果的
    #: 事件会被 replan 误判 not_analyzed → 假 dependency
    queries_seen: List[Dict[str, Any]] = Field(default_factory=list)
    #: 可撤销快照名（snap_NNNN），栈顶 = 最近一次成功 apply 之前的状态
    undo_stack: List[str] = Field(default_factory=list)
    redo_stack: List[str] = Field(default_factory=list)
    next_snap: int = 1
    last_revision: int = 0


class TurnRecord(StrictModel):
    """turns.jsonl 追加行：一轮反馈的审计摘要。"""

    turn: int
    utterance: str
    status: str
    request_type: str = ""
    parser_mode: str = ""
    base_revision: int = 0
    revision: int = 0
    unresolved: List[Dict[str, Any]] = Field(default_factory=list)
    snapshot: str = ""


class PatchNote(StrictModel):
    """patch_flow 归一化留下的审计轨迹。"""

    kind: str          # replace_to_update / collapse / instance_remove / drop_unresolved
    detail: str


class FeedbackResult(StrictModel):
    """session.start / reply / undo / redo 的统一返回。"""

    status: FeedbackStatus
    turn: int = 0
    utterance: str = ""
    message: str = ""
    request_type: str = ""
    parser_mode: str = ""
    unresolved: List[UnresolvedReference] = Field(default_factory=list)
    #: PlanPatch 摘要（计数），完整补丁可从 plan 文件追溯
    plan_patch: Dict[str, int] = Field(default_factory=dict)
    asset_status: str = ""
    execution: Optional[ExecutionResult] = None
    edit_view: Optional[ProjectEditView] = None
    revision: int = 0
    dependencies: List[Dict[str, Any]] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
