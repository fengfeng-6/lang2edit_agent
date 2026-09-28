"""State 层（§56-§83）：execution/ 目录、revision、recovery、索引。"""

from .manager import ExecutionStateManager
from .recovery import RecoveryReport, recover
from .revision import (
    commit_candidate,
    current_revision,
    load_committed_graph,
    load_committed_state,
    write_candidate,
)
from .store import ExecutionStore

__all__ = [
    "ExecutionStateManager",
    "ExecutionStore",
    "RecoveryReport",
    "commit_candidate",
    "current_revision",
    "load_committed_graph",
    "load_committed_state",
    "recover",
    "write_candidate",
]
