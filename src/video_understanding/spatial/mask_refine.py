"""前景主体蒙版精炼（坐姿/轮椅可达性场景）。

背景替换链路的两处实测缺陷:

- **轮椅被遮蔽**: MediaPipe ``selfie_segmenter`` 只分割人体,轮椅被判为
  背景,换背景后被覆盖。这里允许多个分割置信图联合(如叠加
  DeepLab-V3 的 person 通道与 chair/bicycle/motorbike 等辅助类),
  并在「椅带」(座位线以下区域)内用低阈值扩展召回轮子等弱响应区。
- **边缘模糊**: 低分辨率蒙版直接 BILINEAR 放大再高斯羽化,产生宽晕边。
  ``guided_upsample`` 以原图灰度为引导做引导滤波上采样,把 alpha 边缘
  对齐到真实色彩边缘,再经窄带 remap + 轻羽化得到干净软边。

纯 numpy 实现(无 scipy/cv2 依赖),选择/形态学/连通域/洞填补统一在
``work_scale`` 缩减分辨率上进行(默认 1/4),阈值参数按工作分辨率像素计;
选中区上采样回原尺寸后只做 alpha 抬升,细节由原置信与引导滤波决定。
"""

from __future__ import annotations

from typing import Iterable, Optional, Tuple

import numpy as np

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


def _grow(cores: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """从核出发在 mask 内迭代膨胀至收敛(连通域提取,无标签实现)。"""
    grown = cores & mask
    while True:
        new = _dilate(grown, 1, 1) & mask
        if (new == grown).all():
            return grown
        grown = new


def _keep_connected(sel: np.ndarray, seed: np.ndarray) -> np.ndarray:
    """保留与 seed 连通的成分(从 seed 洪泛);隔离岛丢弃。

    椅带内低阈扩展会带进不相关的弱响应孤岛(如画面边缘的柜体),
    而轮椅响应与人体 core 经座位区连续相连——洪泛过滤正好去芜存菁。
    """
    if not seed.any():
        return sel
    return _grow(seed, sel)


def _core_seed(core: np.ndarray, peer_frac: float = 0.2,
               min_px: int = 64) -> np.ndarray:
    """连通种子 = 最大核心 CC + 其余 ≥ max(min_px, peer_frac·最大) 的 CC。

    直接拿整个 core 做种子会让口袋里的高置信渗色岛(实测 conf 0.5~0.9,
    本身也是 ≥core_thresh 的 core)反向洪泛保活;限到大 CC 后孤岛被弃。
    保留"并列大块"兜底躯干被阈值切成两段的情形(如抬起的手臂单独成团)。
    """
    rest = core.copy()
    ccs = []
    while rest.any():
        seed = np.zeros_like(core)
        seed.flat[np.argmax(rest)] = True
        cc = _grow(seed, rest)
        ccs.append(cc)
        rest &= ~cc
    if not ccs:
        return core
    biggest = max(int(c.sum()) for c in ccs)
    thresh = max(min_px, peer_frac * biggest)
    keep = np.zeros_like(core)
    for cc in ccs:
        if int(cc.sum()) >= thresh:
            keep |= cc
    return keep


def _ccs(m: np.ndarray) -> Iterable[np.ndarray]:
    """逐块产出 bool mask 的 4-连通成分(无标签实现,工作分辨率下足够快)。"""
    rest = m.copy()
    while rest.any():
        seed = np.zeros_like(m)
        seed.flat[np.argmax(rest)] = True
        cc = _grow(seed, rest)
        yield cc
        rest &= ~cc


def _enclosed(sel: np.ndarray) -> np.ndarray:
    """四向均被前景包围的像素(行列双向 accumulate 的交集)。

    腋窝、腿缝、手臂-轮间口袋这类与外边连通或开口的结构不会被判为
    洞;轮辐间隙、被前景四向封闭的空隙会。
    """
    up = np.maximum.accumulate(sel, axis=0)
    down = np.maximum.accumulate(sel[::-1], axis=0)[::-1]
    left = np.maximum.accumulate(sel, axis=1)
    right = np.maximum.accumulate(sel[:, ::-1], axis=1)[:, ::-1]
    return up & down & left & right


def _maxpool(a: np.ndarray, s: int) -> np.ndarray:
    """s×s 最大池化下采样(保留细的弱响应,利于椅带召回)。"""
    if s <= 1:
        return a.astype(np.float32)
    H, W = a.shape
    h, w = H // s, W // s
    return (a[: h * s, : w * s].reshape(h, s, w, s)
            .max(axis=(1, 3))).astype(np.float32)


def _nn_upsample(m: np.ndarray, h: int, w: int) -> np.ndarray:
    """最近邻放大 bool 图到 (h,w)(下采样裁剪区保持 False)。"""
    ys = np.minimum((np.arange(h) * m.shape[0]) // h, m.shape[0] - 1)
    xs = np.minimum((np.arange(w) * m.shape[1]) // w, m.shape[1] - 1)
    return m[ys][:, xs]


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
    pocket_frac: float = 0.008,
    pocket_wall: float = 0.75,
    stray_cap: float = 0.30,
    close_px: int = 1,
    band_close_px: Optional[int] = None,
    work_scale: int = 4,
) -> np.ndarray:
    """置信图 → 前景 alpha(与输入同尺寸,float32 in [0,1])。

    - ``fg = max(person_conf, aux_gain * max(aux_confs))``:deeplab 的
      person 通道在轮椅区域已有 0.2~0.6 弱响应,chair/bicycle 等类
      折扣后并入召回。
    - 核心阈 ``core_thresh`` 选出确定前景;椅带(``seat_row`` 以下,
      默认 0.58H——实测轮椅轮顶约 0.6H、髋关节约 0.8H)内阈值降到
      ``band_thresh`` 召回轮子/脚踏;带外保持高阈防抓背景。
    - 连通域过滤:仅保留与大核心块(``_core_seed``,最大 CC + 并列大块)
      连通的选择——椅带弱响应带进画面边缘的孤立岛、口袋里的高置信
      渗色岛都被丢弃;轮椅则与人体经座位连续相连得以保留。
    - 形态学:小闭运算 + 椅带横向大闭运算补轮辐间隙。
    - **口袋抑制**:``_enclosed`` 判出的四向封闭区中,面积
      ≥``pocket_frac`` 帧面积的洞视为"口袋"内部(手臂-轮之间
      的空隙,实测 ~2% 帧)。口袋域 = 洞向封闭区内非强响应
      (<``pocket_wall``)内壁洪泛扩张;域内选区全部撤销——deeplab
      置信会渗入口袋内的柜体/墙面形成鬼影。强响应墙(手臂/椅把)
      不属于口袋,选区自然保留。
    - 洞填补:封闭小洞(面积 < ``hole_area_frac``)填补,
      衣服漏检斑、轮辐小空隙被抹平;中等空隙与口袋保持开放
      (轮间缝隙透出新背景是预期效果)。
    - **sel 权威化**:最终 alpha 在选区内为 ``max(fg, band_boost)``
      (低置信召回抬升),选区外裁剪到 ``stray_cap``——任何未入选的
      高置信残岛/渗色一律压成背景,而不是透传 raw conf。
    - 核心像素保留原置信(头发等软边不被压平)。

    形态学像素参数(``close_px``/``band_close_px``)与洞半径按
    ``work_scale`` 缩减分辨率计(默认 1/4)。
    """
    fg_full = np.asarray(person_conf, dtype=np.float32)
    if fg_full.ndim != 2:
        raise ValueError(f"person_conf 应为 (H,W),got {fg_full.shape}")
    fg_full = fg_full.copy()
    for a in aux_confs or ():
        a = np.asarray(a, dtype=np.float32)
        if a.shape != fg_full.shape:
            raise ValueError(
                f"aux_conf 尺寸 {a.shape} 与 person_conf {fg_full.shape} 不一致")
        np.maximum(fg_full, a * aux_gain, out=fg_full)

    H, W = fg_full.shape
    s = max(1, int(work_scale))
    fg = _maxpool(fg_full, s) if s > 1 else fg_full
    h, w = fg.shape

    seat_full = (seat_row if seat_row is not None
                 else int(round(H * seat_frac)))
    seat = max(0, min(int(round(seat_full / s)), h))
    band = np.zeros((h, w), dtype=bool)
    band[seat:, :] = True

    core = fg >= core_thresh
    seed = _core_seed(core)                 # 大核心块,排除高置信渗色孤岛
    sel = core | ((fg >= band_thresh) & band)
    sel = _keep_connected(sel, seed)

    if close_px > 0:
        sel = _close(sel, close_px, close_px)
    bw = band_close_px if band_close_px is not None else max(2, w // 40)
    if bw > 0 and seat < h:
        sel = sel | _close(sel & band, 1, bw)

    enc = _enclosed(sel)
    holes = enc & ~sel
    if holes.any():
        pocket_min = pocket_frac * h * w     # 面积门控(实测口袋洞 ~2% 帧)
        fill_max = hole_area_frac * h * w
        pocket = np.zeros_like(sel)
        fill = np.zeros_like(sel)
        wall = fg >= pocket_wall
        for cc in _ccs(holes):
            sz = int(cc.sum())
            if sz >= pocket_min:
                # 大洞 → 口袋域:洞 + 封闭区内非强响应内壁
                pocket |= _grow(cc, enc & ~wall)
            elif sz < fill_max:
                fill |= cc                    # 小洞照填
            # 中间尺寸洞保持开放(轮间空隙透出新背景是预期效果)
        sel &= ~pocket
        sel |= fill
        sel = _keep_connected(sel, seed)      # 撤销可能切出新孤岛

    sel_full = _nn_upsample(sel, H, W)
    # 选区内:低置信召回抬到 band_boost,原置信 ≥core_thresh 保留原值;
    # 选区外:压到 stray_cap,杜绝未入选渗色/残岛透出
    alpha_in = np.maximum(
        fg_full, (sel_full & (fg_full < core_thresh)) * band_boost)
    return np.where(sel_full, alpha_in,
                    np.minimum(fg_full, stray_cap))


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
