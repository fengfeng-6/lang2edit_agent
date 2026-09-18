"""MediaPipe + PyAV 空间分析后端（可选依赖，懒加载）。

真实视频路径：PyAV 解码抽帧 → MediaPipe PoseLandmarker / FaceDetector /
（可选）HandLandmarker VIDEO 模式 → FrameObservation 稠密轨道。

依赖通过 ``pip install -e ".[video]"`` 安装，模型文件由
``scripts/download_models.py`` 下载到 ``VU_MODEL_DIR``（默认 data/models/）。
未安装依赖或缺模型时 ``available()`` 返回 False，analyze() 抛
RuntimeError —— 上层据此把 query 标记为 ``failed`` 而非 ``not_found``（§42）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ..models import (
    DenseSpatialTracks,
    FrameObservation,
    HandObservation,
    VideoMetadata,
)
from .loaders import VideoSource

# MediaPipe PoseLandmarker 33 点中我们维护的子集（与 BODY_KEYPOINTS 对齐）。
_POSE_INDEX = {
    "nose": 0,
    "left_eye": 2,
    "right_eye": 5,
    "left_ear": 7,
    "right_ear": 8,
    "left_shoulder": 11,
    "right_shoulder": 12,
    "left_elbow": 13,
    "right_elbow": 14,
    "left_wrist": 15,
    "right_wrist": 16,
    "left_hip": 23,
    "right_hip": 24,
    "left_knee": 25,
    "right_knee": 26,
    "left_ankle": 27,
    "right_ankle": 28,
}

# HandLandmarker 21 关键点全部保留（按需空间分析 §11）。
_HAND_LANDMARKS = (
    "wrist",
    "thumb_cmc", "thumb_mcp", "thumb_ip", "thumb_tip",
    "index_mcp", "index_pip", "index_dip", "index_tip",
    "middle_mcp", "middle_pip", "middle_dip", "middle_tip",
    "ring_mcp", "ring_pip", "ring_dip", "ring_tip",
    "pinky_mcp", "pinky_pip", "pinky_dip", "pinky_tip",
)


def _default_model_dir() -> Path:
    return Path(os.environ.get("VU_MODEL_DIR", "data/models"))


class MediaPipeSpatialAnalyzer:
    """Base Spatial Analysis 的默认真实实现（§8/§11）。

    ``with_hands=True`` 时同时跑 HandLandmarker，输出 21 点 landmarks
    （on-demand 精细手部分析）。
    """

    name = "mediapipe_v1"

    def __init__(self, model_dir: Optional[Path] = None, *, with_hands: bool = False,
                 sample_fps: float = 15.0):
        self.model_dir = Path(model_dir) if model_dir else _default_model_dir()
        self.with_hands = with_hands
        self.sample_fps = sample_fps  # 抽帧分析帧率（§6.2 视频抽帧）

    def available(self) -> bool:
        import importlib.util

        if importlib.util.find_spec("av") is None or importlib.util.find_spec("mediapipe") is None:
            return False
        return (self.model_dir / "pose_landmarker_full.task").exists()

    def analyze(self, video: VideoSource) -> DenseSpatialTracks:
        if isinstance(video, VideoMetadata):
            path = video.source_uri
            video_id = video.video_id
            fps = video.fps
        elif isinstance(video, dict):
            path = video.get("path") or video.get("source_uri")
            video_id = video.get("video_id", "video")
            fps = float(video.get("metadata", {}).get("fps", 30.0))
        else:
            path = str(video)
            video_id = Path(path).stem
            fps = 30.0
        if not path:
            raise RuntimeError("mediapipe analyzer requires a video file path")

        try:
            import av
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision
        except ImportError as exc:
            raise RuntimeError(
                f"video analysis dependencies missing ({exc}); "
                'install them via pip install -e ".[video]"'
            ) from exc

        pose_path = self.model_dir / "pose_landmarker_full.task"
        face_path = self.model_dir / "blaze_face_short_range.tflite"
        hand_path = self.model_dir / "hand_landmarker.task"
        if not pose_path.exists():
            raise RuntimeError(f"model missing: {pose_path}（运行 scripts/download_models.py）")

        pose = mp_vision.PoseLandmarker.create_from_options(
            mp_vision.PoseLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(pose_path)),
                running_mode=mp_vision.RunningMode.VIDEO,
                num_poses=1,
            )
        )
        face = None
        if face_path.exists():
            face = mp_vision.FaceDetector.create_from_options(
                mp_vision.FaceDetectorOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=str(face_path)),
                    running_mode=mp_vision.RunningMode.VIDEO,
                )
            )
        hands = None
        if self.with_hands and hand_path.exists():
            hands = mp_vision.HandLandmarker.create_from_options(
                mp_vision.HandLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=str(hand_path)),
                    running_mode=mp_vision.RunningMode.VIDEO,
                    num_hands=2,
                )
            )

        frames = []
        step = max(1, round(fps / self.sample_fps))
        try:
            container = av.open(path)
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            index = -1
            for av_frame in container.decode(stream):
                index += 1
                if index % step:
                    continue
                timestamp = float(av_frame.pts * av_frame.time_base) if av_frame.pts is not None else index / fps
                image = av_frame.to_ndarray(format="rgb24")
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image)
                ts_ms = int(timestamp * 1000)

                pose_result = pose.detect_for_video(mp_image, ts_ms)
                keypoints = {}
                person_bbox = None
                if pose_result.pose_landmarks:
                    lm = pose_result.pose_landmarks[0]
                    for name, idx in _POSE_INDEX.items():
                        keypoints[name] = (float(lm[idx].x), float(lm[idx].y))
                    xs = [p[0] for p in keypoints.values()]
                    ys = [p[1] for p in keypoints.values()]
                    person_bbox = (min(xs), min(ys), max(xs), max(ys))

                face_bbox = None
                if face is not None:
                    face_result = face.detect_for_video(mp_image, ts_ms)
                    if face_result.detections:
                        h, w = image.shape[:2]
                        bb = face_result.detections[0].bounding_box
                        face_bbox = (bb.origin_x / w, bb.origin_y / h,
                                     (bb.origin_x + bb.width) / w, (bb.origin_y + bb.height) / h)

                hand_obs = {}
                if hands is not None:
                    hand_result = hands.detect_for_video(mp_image, ts_ms)
                    for lm, handed in zip(hand_result.hand_landmarks or [],
                                          hand_result.handedness or []):
                        side = handed[0].category_name.lower()  # "left"/"right"（人物自身）
                        pts = {name: (float(p.x), float(p.y))
                               for name, p in zip(_HAND_LANDMARKS, lm)}
                        xs = [p[0] for p in pts.values()]
                        ys = [p[1] for p in pts.values()]
                        hand_obs[side] = HandObservation(
                            center=pts["wrist"],
                            bbox=(min(xs), min(ys), max(xs), max(ys)),
                            landmarks=pts,
                            confidence=float(handed[0].score),
                        )

                frames.append(FrameObservation(
                    timestamp=timestamp,
                    person_bbox=person_bbox,
                    face_bbox=face_bbox,
                    keypoints=keypoints,
                    hands=hand_obs,
                ))
        finally:
            pose.close()
            if face is not None:
                face.close()
            if hands is not None:
                hands.close()

        return DenseSpatialTracks(
            video_id=video_id, fps=fps, frames=frames,
            producer=self.name,
        )
