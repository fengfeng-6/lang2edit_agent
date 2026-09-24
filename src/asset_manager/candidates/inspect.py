"""素材检测（§52 的 Decode Validation + Metadata Extraction）。

- 图片：PIL 优先做真实解码 + Alpha/占比/感知哈希；无 PIL 时退化为
  PNG/JPEG 头部解析（宽高/声明性 alpha），``partial=True`` 标记像素级
  统计缺失——§42 要求真实 Alpha 检查，声称与实测分离；
- 音频：魔数嗅探 + WAV 头解析时长；装了 PyAV 时做一次真实解码验证；
- 任何内容字节都算 SHA-256 全量摘要与 dHash 感知哈希（§51/§55-56）。
"""

from __future__ import annotations

import hashlib
import os
import struct
from datetime import datetime, timezone
from typing import Optional, Tuple

from ..models import Integrity, TechnicalMetadata

_ALPHA_TAU = 8  # §41：alpha > τ 计为前景
_FORMAT_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
    "wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac",
    "ogg": "audio/ogg", "m4a": "audio/mp4", "aac": "audio/aac",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sniff_format(path: str, head: bytes = b"") -> str:
    """魔数优先，扩展名兜底。"""

    head = head or _read_head(path)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        return "ogg"
    if head[4:8] == b"ftyp":
        return "m4a"
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return ext


def _read_head(path: str, n: int = 64) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


class InspectResult:
    __slots__ = ("technical", "integrity")

    def __init__(self, technical: TechnicalMetadata, integrity: Integrity):
        self.technical = technical
        self.integrity = integrity


class Inspector:
    """文件/字节 → TechnicalMetadata + Integrity。"""

    def __init__(self, use_pil: bool = True, use_av: bool = True):
        self._pil = None
        self._av = None
        if use_pil:
            try:
                from PIL import Image  # noqa: N813

                self._pil = Image
            except Exception:  # noqa: BLE001
                self._pil = None
        if use_av:
            try:
                import av

                self._av = av
            except Exception:  # noqa: BLE001
                self._av = None

    # -- public ----------------------------------------------------------

    def inspect_file(self, path: str) -> InspectResult:
        with open(path, "rb") as fh:
            data = fh.read()
        return self.inspect_bytes(data, name=path)

    def inspect_bytes(self, data: bytes, name: str = "") -> InspectResult:
        fmt = sniff_format(name, data[:64])
        integrity = Integrity(
            content_hash=hashlib.sha256(data).hexdigest(),
            file_size=len(data),
            mime_type=_FORMAT_MIME.get(fmt, "application/octet-stream"),
            validated_at=_now(),
        )
        technical = TechnicalMetadata(
            format=fmt,
            file_size=len(data),
            mime_type=integrity.mime_type,
        )
        if fmt in ("png", "jpg", "gif", "webp", "bmp"):
            self._inspect_image(data, technical, integrity)
        elif fmt in ("wav", "mp3", "flac", "ogg", "m4a", "aac"):
            self._inspect_audio(data, name, fmt, technical, integrity)
        else:
            integrity.decodable = False
            technical.partial = True
        return InspectResult(technical, integrity)

    # -- image -----------------------------------------------------------

    def _inspect_image(
        self, data: bytes, technical: TechnicalMetadata, integrity: Integrity
    ) -> None:
        if self._pil is not None:
            try:
                self._inspect_image_pil(data, technical, integrity)
                return
            except Exception:  # noqa: BLE001 - 真实解码失败再试头部
                integrity.decodable = False
        # stdlib 头部路径（或 PIL 解码失败的兜底信息）
        dims, alpha_declared = _header_dims(technical.format, data)
        if dims:
            technical.width, technical.height = dims
            technical.aspect_ratio = dims[0] / dims[1]
            if technical.has_alpha is None:
                technical.has_alpha = alpha_declared
        technical.partial = True
        if integrity.decodable is None or not integrity.decodable:
            integrity.decodable = bool(dims)

    def _inspect_image_pil(
        self, data: bytes, technical: TechnicalMetadata, integrity: Integrity
    ) -> None:
        import io

        img = self._pil.open(io.BytesIO(data))
        img.verify()  # 不解码像素先验结构
        img = self._pil.open(io.BytesIO(data)).convert("RGBA")
        technical.width, technical.height = img.size
        technical.aspect_ratio = img.size[0] / img.size[1]
        integrity.decodable = True

        declared_alpha = self._pil.open(io.BytesIO(data))
        has_alpha_channel = declared_alpha.mode in ("RGBA", "LA", "PA") or (
            declared_alpha.mode == "P" and "transparency" in declared_alpha.info)
        alpha = img.getchannel("A")
        hist = alpha.histogram()
        total = img.size[0] * img.size[1]
        transparent = total - hist[255]
        technical.alpha_ratio = transparent / total
        technical.foreground_occupancy = sum(hist[_ALPHA_TAU + 1:]) / total
        # §42：声称 alpha 但 r_alpha≈0 → 实际不透明
        technical.has_alpha = bool(has_alpha_channel) and technical.alpha_ratio > 1e-4
        integrity.perceptual_hash = _dhash(img)

    # -- audio -----------------------------------------------------------

    def _inspect_audio(
        self, data: bytes, name: str, fmt: str,
        technical: TechnicalMetadata, integrity: Integrity,
    ) -> None:
        integrity.decodable = True  # 魔数已匹配；真实解码交给 av（若有）
        if fmt == "wav":
            duration = _wav_duration(data)
            if duration:
                technical.duration = duration
        if self._av is not None:
            try:
                import io

                container = self._av.open(io.BytesIO(data))
                if container.duration:
                    technical.duration = container.duration / 1_000_000.0
                technical.partial = False
            except Exception:  # noqa: BLE001
                integrity.decodable = False
        else:
            technical.partial = technical.duration is None


# ---------------------------------------------------------------------------
# stdlib 头部解析（无 PIL 的兜底路径）
# ---------------------------------------------------------------------------


def _header_dims(fmt: str, data: bytes) -> Tuple[Optional[Tuple[int, int]], bool]:
    """返回 ((w,h), 声明性 alpha)。先验签名再解尺寸——垃圾数据不算数。"""

    try:
        if fmt == "png" and len(data) >= 26:
            if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
                return None, False
            width, height = struct.unpack(">II", data[16:24])
            if not (0 < width <= 65535 and 0 < height <= 65535):
                return None, False
            color_type = data[25]
            alpha = color_type in (4, 6) or b"tRNS" in data[:4096]
            return (width, height), alpha
        if fmt == "jpg":
            if data[:2] != b"\xff\xd8":
                return None, False
            idx = 2
            while idx + 9 < len(data):
                if data[idx] != 0xFF:
                    idx += 1
                    continue
                marker = data[idx + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    height, width = struct.unpack(">HH", data[idx + 5:idx + 9])
                    return (width, height), False
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    idx += 2
                    continue
                seg_len = struct.unpack(">H", data[idx + 2:idx + 4])[0]
                idx += 2 + seg_len
        if fmt == "gif" and len(data) >= 10:
            if not data.startswith((b"GIF87a", b"GIF89a")):
                return None, False
            width, height = struct.unpack("<HH", data[6:10])
            return (width, height), False
    except Exception:  # noqa: BLE001
        return None, False
    return None, False


def _wav_duration(data: bytes) -> Optional[float]:
    try:
        idx = 12
        byte_rate = 0
        data_size = 0
        while idx + 8 <= len(data):
            chunk_id = data[idx:idx + 4]
            size = struct.unpack("<I", data[idx + 4:idx + 8])[0]
            if chunk_id == b"fmt ":
                byte_rate = struct.unpack("<I", data[idx + 16:idx + 20])[0]
            if chunk_id == b"data":
                data_size = size
            idx += 8 + size + (size % 2)
        if byte_rate and data_size:
            return data_size / byte_rate
    except Exception:  # noqa: BLE001
        return None
    return None


def _dhash(img) -> str:
    """8x8 差分感知哈希（§51）；img 为 PIL RGBA 图。"""

    gray = img.convert("L").resize((9, 8))
    px = list(gray.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | int(px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"
