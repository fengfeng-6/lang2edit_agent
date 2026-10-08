"""session_feedback — 模块六：反馈与持续修改（§4.6/§9）。

会话编排层，把"用户反馈 → IntentPatch → Partial Replanning →
素材重解析 → Executor"串成闭环：

    FeedbackSession.start(video, utterance)   首轮：分析→parse→plan→apply
    FeedbackSession.reply(utterance)          反馈轮：parse(patch)→归一化
                                              →replan→增量素材→apply
    FeedbackSession.undo() / redo()           快照回滚（git-revert 式前进）
    FeedbackSession.get_view() / get_state()  EditView / 会话摘要
"""

from .models import (
    FeedbackResult,
    FeedbackStatus,
    PatchNote,
    SessionState,
    TurnRecord,
)
from .session import FeedbackSession

__all__ = [
    "FeedbackResult",
    "FeedbackSession",
    "FeedbackStatus",
    "PatchNote",
    "SessionState",
    "TurnRecord",
]
