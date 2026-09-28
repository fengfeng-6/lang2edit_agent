"""Diff 层（§32-§34）：Desired vs Committed → GraphDiff。"""

from .engine import GraphDiff, ObjectDiff, diff_graphs
from .properties import RECREATE, PropertyChange, compute_property_diff

__all__ = [
    "GraphDiff",
    "ObjectDiff",
    "PropertyChange",
    "RECREATE",
    "compute_property_diff",
    "diff_graphs",
]
