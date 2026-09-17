"""人物与空间理解。

person / face 轨道承载于 ``DenseSpatialTracks``（§10 Dense Spatial
Tracks，artifact 化存储 §48）；空间原语 Region / Point / Direction /
Trajectory 与平滑轨迹（§9）；``snapshot.py`` 组装事件级空间快照、
Event Anchor（双手中点）与 Protected Region（§27-29）；``loaders.py``
JSON artifact 加载；``mediapipe_backend.py`` 可选 MediaPipe+PyAV
真实视频后端（懒加载，§11 按需手部 landmarks）。
"""
