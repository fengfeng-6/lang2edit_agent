"""Usage Index（§62）：asset_uid → plan_item_uid[]。

删一个 PlanItem 不误删仍被其他 PlanItem 引用的素材。
"""

from __future__ import annotations

from typing import Dict, List


def record_usage(index: Dict[str, List[str]], asset_uid: str, plan_item_uid: str) -> None:
    items = index.setdefault(asset_uid, [])
    if plan_item_uid and plan_item_uid not in items:
        items.append(plan_item_uid)


def items_for(index: Dict[str, List[str]], asset_uid: str) -> List[str]:
    return list(index.get(asset_uid, []))


def assets_for(index: Dict[str, List[str]], plan_item_uid: str) -> List[str]:
    return [uid for uid, items in index.items() if plan_item_uid in items]


def remove_item(index: Dict[str, List[str]], plan_item_uid: str) -> List[str]:
    """PlanItem 删除后清索引，返回已不再被引用的 asset_uid。"""

    orphaned = []
    for uid in list(index):
        if plan_item_uid in index[uid]:
            index[uid].remove(plan_item_uid)
            if not index[uid]:
                orphaned.append(uid)
    return orphaned
