"""Pose / 手部表征与规则。

body keypoints、head center、左右腕、基础手位承载于
``models.FrameObservation``（§12）；Pose / Motion Rule Engine 与
Condition Compiler（``conditions.py``，姿态条件编译为可执行谓词，
人物宽度归一化距离 §21）；运动工具（``motion.py``：位移/速度/
运动能量，§22）。
"""
