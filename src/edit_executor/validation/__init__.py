"""Validation 层（§73, §85-§87）：Preflight + Core Verification。"""

from .preflight import PreflightResult, run_preflight
from .verifier import verify_desired_graph

__all__ = [
    "PreflightResult",
    "run_preflight",
    "verify_desired_graph",
]
