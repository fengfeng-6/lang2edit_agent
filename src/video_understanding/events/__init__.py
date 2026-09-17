"""语义事件检测。

Detection Strategy Router（``router.py``，§14）；Dedicated Event Detector
姿态启发式打分器（``detectors.py``，§15-16）；Temporal Event Aggregator
帧级结果聚合为独立语义事件、区分多次发生（``aggregator.py``，§19-20）；
Pose / Motion Rule 由 pose 包条件编译承载（§21-23）；Open Semantic
Detector 两阶段提案+可插拔验证（``open_semantic.py``，§24）；结构化
事件 first/last_action 等（``structural.py``，§39）；Semantic Event
Registry 能力注册表（``registry.py``，§26）。
"""
