#!/usr/bin/env python3
"""下载模块二所需的 MediaPipe .task 模型文件到 data/models/。

用法：
    python scripts/download_models.py            # 下载全部默认模型
    python scripts/download_models.py --list     # 只列出需要的模型与状态

模型源为 storage.googleapis.com（本机已验证可达）。若下载失败（网络变动），
按报错里的 URL 手动下载后放到 --dir（默认 data/models/）即可。
目标目录也可用环境变量 VU_MODEL_DIR 指定。
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request
from pathlib import Path

# (文件名, googleapis URL, 用途)
MODELS = [
    (
        "pose_landmarker_full.task",
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task",
        "人体 33 关键点（PoseLandmarker）",
    ),
    (
        "hand_landmarker.task",
        "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
        "双手 21 关键点（HandLandmarker）",
    ),
    (
        "blaze_face_short_range.tflite",
        "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite",
        "人脸框检测（FaceDetector）",
    ),
]


def model_dir() -> Path:
    return Path(os.environ.get("VU_MODEL_DIR", "data/models"))


def download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r    {done * 100 // total}%", end="", flush=True)
    tmp.replace(dest)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=None, help="模型目录（默认 VU_MODEL_DIR 或 data/models/）")
    parser.add_argument("--list", action="store_true", help="列出模型与本地状态后退出")
    args = parser.parse_args()

    target = args.dir or model_dir()
    ok = True
    for name, url, purpose in MODELS:
        dest = target / name
        status = "已存在" if dest.exists() else "缺失"
        if args.list:
            print(f"{status}  {name}  ({purpose}) -> {dest}")
            continue
        print(f"[{name}] {purpose}")
        if dest.exists():
            print("    已存在，跳过")
            continue
        target.mkdir(parents=True, exist_ok=True)
        try:
            print(f"    下载 {url}")
            download(url, dest)
        except Exception as exc:  # noqa: BLE001 - 任何网络错误都走手动指引
            ok = False
            print(f"    下载失败：{exc}")
            print(f"    请手动下载该 URL 并放到 {dest}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
