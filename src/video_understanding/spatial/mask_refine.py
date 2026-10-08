"""前景主体蒙版精炼（坐姿/轮椅可达性场景）。

背景替换链路的两处实测缺陷:

- **轮椅被遮蔽**: MediaPipe ``selfie_segmenter`` 只分割人体,轮椅被判为
  背景,换背景后被覆盖。这里允许多个分割置信图联合(如叠加
  DeepLab-V3 的 person 通道与 chair/bicycle/motorbike 等辅助类),
  并在「椅带」(座位线以下区域)内用低阈值扩展召回轮子等弱响应区。
- **边缘模糊**: 低分辨率蒙版直接 BILINEAR 放大再高斯羽化,产生宽晕边。
  ``guided_upsample`` 以原图灰度为引导做引导滤波上采样,把 alpha 边缘
  对齐到真实色彩边缘,再经窄带 remap + 轻羽化得到干净软边。

仅依赖 numpy(PIL 用于可选的原图引导灰度化);``scipy.ndimage`` 若可用
则顺带做带外小洞按面积填补,不可用自动跳过——集群与 CI 均有 scipy。
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

import numpy as np

try:  # 可选:仅用于带外小洞的面积门控
    from scipy import ndimage as _ndi
except Exception:  # pragma: no cover - 本地精简环境
    _ndi = None

# DeepLab-V3(Pascal VOC 21 类)中与轮椅部件相关的辅助类下标。
DEEPLAB_VOC_CLASSES = {
    "bicycle": 2,
    "chair": 9,
    "motorbike": 14,
    "person": 15,
}
DEEPLAB_AUX_CLASSES = ("chair", "bicycle", "motorbike")


# ---------------- 纯 numpy 基础算子 ----------------

def _box_mean(x: np.ndarray, k: int) -> np.ndarray:
    """k×k 均值滤波(edge 填充),积分图实现,O(HW)。"""
    r = k // 2
    xp = np.pad(x.astype(np.float64), r, mode="edge")
    P = np.pad(xp.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    s = P[k:, k:] - P[:-k, k:] - P[k:, :-k] + P[:-k, :-k]
    return s / float(k * k)


def _dilate_axis(m: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """沿单轴 ±radius 的二值膨胀(矩形结构元的可分离分解)。"""
    if radius <= 0:
        return m
    k = 2 * radius + 1
    pad = [(0, 0), (0, 0)]
    pad[axis] = (radius, radius)
    mp = np.pad(m, pad, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(mp, k, axis=axis)
    return win.any(axis=-1)


def _dilate(m: np.ndarray, ry: int, rx: int) -> np.ndarray:
    return _dilate_axis(_dilate_axis(m, rx, 1), ry, 0)


def _erode_axis(m: np.ndarray, radius: int, axis: int) -> np.ndarray:
    if radius <= 0:
        return m
    k = 2 * radius + 1
    pad = [(0, 0), (0, 0)]
    pad[axis] = (radius, radius)
    mp = np.pad(m, pad, mode="constant")  # 边界外视为 False
    win = np.lib.stride_tricks.sliding_window_view(mp, k, axis=axis)
    return win.all(axis=-1)


def _erode(m: np.ndarray, ry: int, rx: int) -> np.ndarray:
    return _erode_axis(_erode_axis(m, rx, 1), ry, 0)


def _close(m: np.ndarray, ry: int, rx: int) -> np.ndarray:
    return _erode(_dilate(m, ry, rx), ry, rx)


def _enclosed(sel: np.ndarray) -> np.ndarray:
    """四向均被前景包围的像素(行列双向 accumulate 的交集)。

    腋窝、腿缝这类与外边连通的空隙不会被判为洞;轮辐间隙、被前景
    封闭的口袋会。
    """
    up = np.maximum.accumulate(sel, axis=0)
    down = np.maximum.accumulate(sel[::-1], axis=0)[::-1]
    left = np.maximum.accumulate(sel, axis=1)
    right = np.maximum.accumulate(sel[:, ::-1], axis=1)[:, ::-1]
    return up & down & left & right


def _resize_bilinear(a: np.ndarray, h: int, w: int) -> np.ndarray:
    """float 图双线性缩放到 (h,w)(align_corners=False 约定)。"""
    H, W = a.shape
    if (H, W) == (h, w):
        return a.astype(np.float32)
    ys = np.clip((np.arange(h) + 0.5) * H / h - 0.5, 0, H - 1)
    xs = np.clip((np.arange(w) + 0.5) * W / w - 0.5, 0, W - 1)
    y0 = np.floor(ys).astype(np.int64)
    x0 = np.floor(xs).astype(np.int64)
    y1 = np.minimum(y0 + 1, H - 1)
    x1 = np.minimum(x0 + 1, W - 1)
    wy = (ys - y0)[:, None]
    wx = (xs - x0)[None, :]
    top = a[y0][:, x0] * (1 - wx) + a[y0][:, x1] * wx
    bot = a[y1][:, x0] * (1 - wx) + a[y1][:, x1] * wx
    return (top * (1 - wy) + bot * wy).astype(np.float32)


# ---------------- 蒙版合成 ----------------

def subject_alpha(
    person_conf: np.ndarray,
    aux_confs: Optional[Iterable[np.ndarray]] = None,
    *,
    aux_gain: float = 0.8,
    seat_row: Optional[int] = None,
    seat_frac: float = 0.58,
    core_thresh: float = 0.45,
    band_thresh: float = 0.15,
    band_boost: float = 0.92,
    hole_area_frac: float = 0.004,
    close_px: int = 3,
    band_close_px: Optional[int] = None,
) -> np.ndarray:
    """置信图 → 前景 alpha(与输入同尺寸,float32 in [0,1])。

    - ``fg = max(person_conf, aux_gain * max(aux_confs))``:deeplab 的
      person 通道在轮椅区域已有 0.2~0.6 的弱响应,chair/bicycle 等类
      折扣后并入召回。
    - 核心阈 ``core_thresh`` 选出确定前景;椅带(``seat_row`` 以下,
      默认 0.58H——实测轮椅轮顶约在 0.6H、髋关节约 0.8H)内把阈值降到
      ``band_thresh`` 召回轮子/脚踏等弱响应;带外保持高阈防止抓取
      背景杂物。
    - 形态学:基础闭运算 + 椅带内横向大闭运算补轮辐间隙;椅带内
      四向封闭区域(轮间空隙)直接填;带外小洞(< ``hole_area_frac``
      帧面积)在 scipy 可用时按连通域填补,否则跳过。
    - 非核心的入选像素抬到 ``band_boost``,核心像素保留原置信
      (头发等软边不被压平)。
    """
    fg = np.asarray(person_conf, dtype=np.float32)
    if fg.ndim != 2:
        raise ValueError(f"person_conf 应为 (H,W),got {fg.shape}")
    fg = fg.copy()
    for a in aux_confs or ():
        a = np.asarray(a, dtype=np.float32)
        if a.shape != fg.shape:
            raise ValueError(
                f"aux_conf 尺寸 {a.shape} 与 person_conf {fg.shape} 不一致")
        np.maximum(fg, a * aux_gain, out=fg)

    H, W = fg.shape
    seat = seat_row if seat_row is not None else int(round(H * seat_frac))
    seat = max(0, min(seat, H))
    band = np.zeros((H, W), dtype=bool)
    band[seat:, :] = True

    core = fg >= core_thresh
    sel = core | ((fg >= band_thresh) & band)

    if close_px > 0:
        r = max(1, close_px // 2)
        sel = _close(sel, r, r)
    bw = band_close_px if band_close_px is not None else max(8, W // 40)
    if bw > 0 and seat < H:
        sel = sel | _close(sel & band, 1, bw)

    enc = _enclosed(sel)
    sel |= enc & band & ~sel          # 椅带内封闭空隙全填
    holes = enc & ~band & ~sel        # 带外洞:scipy 时按面积门控
    if holes.any() and _ndi is not None:
        lab, n = _ndi.label(holes)
        if n:
            sizes = np.bincount(lab.ravel(), minlength=n + 1)
            small = sizes <= max(1, int(hole_area_frac * H * W))
            small[0] = False
            sel |= small[lab]

    boost = (sel & ~core).astype(np.float32) * band_boost
    return np.maximum(fg, boost)


# ---------------- 边缘对齐上采样 ----------------

def guided_upsample(
    guide: np.ndarray,
    alpha: np.ndarray,
    out_size: Optional[Tuple[int, int]] = None,
    *,
    radius: Optional[int] = None,
    eps: float = 0.02,
    lo: float = 0.35,
    hi: float = 0.65,
    blur_sigma: float = 1.0,
) -> np.ndarray:
    """引导滤波上采样 alpha 并对齐到 guide 的色彩边缘。

    ``guide``: (H,W) 灰度或 (H,W,3) RGB(float [0,1] 或 uint8/float
    [0,255] 自动归一),通常为合成分辨率的原帧。``alpha``: (h,w)
    低分辨率前景 alpha。``out_size=(W,H)`` 默认取 guide 尺寸。

    流程:alpha 双线性放大 → 引导滤波(edge-aware)→ ``[lo,hi]`` 窄带
    remap 压缩过渡带 → 三次盒滤波近似 ``blur_sigma`` 高斯,出 1-2px
    抗锯齿软边。
    """
    g = np.asarray(guide, dtype=np.float32)
    if g.ndim == 3:
        g = (g[..., 0] * 0.299 + g[..., 1] * 0.587 + g[..., 2] * 0.114)
    if g.max() > 1.5:
        g = g / 255.0
    a = np.asarray(alpha, dtype=np.float32)
    if a.max() > 1.5:
        a = a / 255.0

    W, H = out_size if out_size else (g.shape[1], g.shape[0])
    if a.shape != (H, W):
        a = _resize_bilinear(a, H, W)
    if g.shape != (H, W):
        g = _resize_bilinear(g, H, W)

    r = radius if radius is not None else max(4, round(min(H, W) / 160))
    k = 2 * r + 1
    mean_g = _box_mean(g, k)
    mean_a = _box_mean(a, k)
    corr_g = _box_mean(g * g, k)
    corr_ga = _box_mean(g * a, k)
    var_g = corr_g - mean_g * mean_g
    cov_ga = corr_ga - mean_a * mean_a
    A = cov_ga / (var_g + eps)
    b = mean_a - A * mean_g
    q = _box_mean(A, k) * g + _box_mean(b, k)

    if hi > lo:
        q = np.clip((q - lo) / (hi - lo), 0.0, 1.0)
    else:
        q = (q >= lo).astype(np.float64)
    if blur_sigma > 0:  # 三次盒滤波 ≈ 高斯
        kk = max(3, int(round(blur_sigma * 3)) | 1)
        q = _box_mean(_box_mean(_box_mean(q, kk), kk), kk)
    return np.clip(q, 0.0, 1.0).astype(np.float32)
