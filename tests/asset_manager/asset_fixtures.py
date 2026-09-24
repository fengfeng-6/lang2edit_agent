"""tests/asset_manager 共用 fixture：图片生成、本地库 manifest、假在线源。

文件名刻意唯一（asset_fixtures.py），避免与其他 tests 目录的 helper 撞
``sys.modules``。
"""

from __future__ import annotations

import json
import os
import wave
from typing import Any, Dict, List, Optional

import pytest


def make_png(
    path: str,
    size=(64, 64),
    opaque=False,
    shape="ellipse",
    color=(255, 120, 160, 255),
) -> str:
    """生成测试 PNG：默认带透明底的主体；opaque=True 输出无透明。

    shape: ellipse / rect / diamond / cross——感知哈希对同形换色会判重，
    需要多个不同候选时换 shape。
    """

    PIL = pytest.importorskip("PIL", reason="asset image tests need PIL")
    from PIL import Image, ImageDraw

    os.makedirs(os.path.dirname(path), exist_ok=True)
    if opaque:
        img = Image.new("RGB", size, (255, 255, 255))
    else:
        img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    w, h = size
    if shape == "ellipse":
        draw.ellipse([w * 0.2, h * 0.2, w * 0.8, h * 0.8], fill=color)
    elif shape == "rect":
        draw.rectangle([w * 0.1, h * 0.35, w * 0.9, h * 0.65], fill=color)
    elif shape == "diamond":
        draw.polygon(
            [(w * 0.5, h * 0.1), (w * 0.9, h * 0.5), (w * 0.5, h * 0.9), (w * 0.1, h * 0.5)],
            fill=color)
    elif shape == "cross":
        draw.rectangle([w * 0.4, h * 0.1, w * 0.6, h * 0.9], fill=color)
        draw.rectangle([w * 0.1, h * 0.4, w * 0.9, h * 0.6], fill=color)
    img.save(path, format="PNG")
    return path


def make_wav(path: str, seconds: float = 1.0, rate: int = 8000) -> str:
    """生成静音 WAV（stdlib wave，无需音频库）。"""

    os.makedirs(os.path.dirname(path), exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(b"\x00\x00" * frames)
    return path


def write_manifest(library_root: str, entries: List[Dict[str, Any]]) -> str:
    os.makedirs(library_root, exist_ok=True)
    manifest = os.path.join(library_root, "manifest.json")
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump({"assets": entries}, fh, ensure_ascii=False, indent=2)
    return manifest


def lib_entry(
    uid: str,
    rel_path: str,
    *,
    asset_type: str = "sticker",
    media_type: str = "image",
    obj: str = "",
    attributes: Optional[List[str]] = None,
    style: Optional[List[str]] = None,
    usage_tags: Optional[List[str]] = None,
    license_status: str = "cleared",
    technical: Optional[Dict[str, Any]] = None,
    style_family: str = "",
) -> Dict[str, Any]:
    return {
        "asset_uid": uid,
        "path": rel_path,
        "asset_type": asset_type,
        "media_type": media_type,
        "semantic_metadata": {
            "object": obj,
            "attributes": attributes or [],
            "style": style or [],
            "usage_tags": usage_tags or [],
        },
        "license_metadata": {"status": license_status},
        "technical_metadata": technical or {},
        "style_family": style_family,
    }


class FakeOnlineAdapter:
    """在线源假实现：search 返回预定行，download 复制本地文件。"""

    def __init__(self, rows=None, adapter_id="fake_online",
                 asset_types=("sticker", "image", "background"),
                 authorized_for_music=False, files=None):
        self.adapter_id = adapter_id
        self.asset_types = list(asset_types)
        self.authorized_for_music = authorized_for_music
        self._rows = list(rows or [])
        self._files = dict(files or {})  # original_url → 本地文件路径
        self.search_calls = 0
        self.download_calls = 0

    def search(self, text_query, negative_terms, limit):
        self.search_calls += 1
        return self._rows[:limit]

    def download(self, original_uri, dest_dir):
        self.download_calls += 1
        import shutil

        src = self._files[original_uri]
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(src))
        shutil.copyfile(src, dest)
        return dest


def online_row(
    row_id: str,
    url: str,
    *,
    title: str = "",
    tags=None,
    asset_type: str = "image",
    width: int = 0,
    height: int = 0,
    has_alpha=None,
    license_type: str = "cc0",
    duration=None,
    bpm=None,
) -> Dict[str, Any]:
    return {
        "id": row_id,
        "title": title,
        "tags": tags or [],
        "asset_type": asset_type,
        "original_url": url,
        "preview_url": url + ".preview",
        "width": width,
        "height": height,
        "has_alpha": has_alpha,
        "duration": duration,
        "bpm": bpm,
        "license": {"type": license_type},
    }


def request_dict(**over) -> Dict[str, Any]:
    base = {
        "request_uid": "asset_req_sticker_01",
        "asset_type": "sticker",
        "media_type": "image",
        "semantic_query": "可爱的粉色爱心",
        "technical_requirements": {"has_alpha": True},
        "usage_context": {},
        "style_context": {},
        "source_policy": "any",
        "constraint_level": "hard",
    }
    base.update(over)
    return base
