"""meta 命令拦截（必须在 parser 之前）。

规则抽取器对"换一个/再来一个"产出空 patch（无新描述可抽）——不拦截会
落成 needs_clarification；"海星换成椰子树"（有新描述）不拦截，走正常
parse 管线。undo/redo 同理——这不是剪辑语义而是会话命令。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import List, Optional, Tuple

from gesture_intent.models import SemanticProjectObject
from gesture_intent.resolver import _candidate_objects, _ordinal


class MetaCommand(str, Enum):
    undo = "undo"
    redo = "redo"
    switch = "switch"


_UNDO_RE = re.compile(r"(撤销|撤回|undo|回退一步|退回到)", re.IGNORECASE)
_REDO_RE = re.compile(r"(重做|redo|恢复刚才|恢复撤销)", re.IGNORECASE)
#: 换资源动词出现在句尾（可带语气词/标点）才算"换一个"——后接新描述
#: （"换一个更欢快的"）是带内容的替换需求，交给正常 parse。
_SWITCH_TAIL = re.compile(
    r"(换一个|换个|换一换|再来一个|再换一个|换一个吧|换一个试试|重新换一个|换掉|换一首|换一条)"
    r"[吧啊呢啦]*[。!！~]*\s*$"
)
_REPLACE_WITH_DESC = re.compile(r"(换成|换为|替换成|替换为|改成|改为|变为)")


def classify(utterance: str) -> Optional[MetaCommand]:
    text = utterance.strip()
    if _UNDO_RE.search(text):
        return MetaCommand.undo
    if _REDO_RE.search(text):
        return MetaCommand.redo
    if _REPLACE_WITH_DESC.search(text):
        return None
    if _SWITCH_TAIL.search(text):
        return MetaCommand.switch
    return None


def switch_target_phrase(utterance: str) -> str:
    """剥掉句尾换资源动词 + 把/将/帮我把 等助词，得到目标短语。"""
    text = _SWITCH_TAIL.sub("", utterance.strip())
    text = re.sub(r"^[把将帮]?[我]?[把将]?", "", text).strip()
    return text


def resolve_switch_target(
    utterance: str,
    objects: List[SemanticProjectObject],
) -> Tuple[Optional[SemanticProjectObject], List[SemanticProjectObject]]:
    """复用模块一的候选匹配 + 序号规则定位要切换素材的对象。

    返回 (命中对象, 候选列表)。裸"换一个"且工程里只有一个可切换对象时
    直接命中；多候选返回 None + candidates 交上层澄清。
    """
    phrase = switch_target_phrase(utterance)
    candidates = _candidate_objects(phrase, objects) if phrase else list(objects)
    if not candidates:
        return None, []
    ordinal = _ordinal(phrase) if phrase else None
    ordered = sorted(
        candidates,
        key=lambda item: (item.order if item.order is not None else 10**9, item.id),
    )
    if ordinal == -1:
        return ordered[-1], candidates
    if ordinal is not None:
        if ordinal <= len(ordered):
            return ordered[ordinal - 1], candidates
        return None, candidates
    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates
