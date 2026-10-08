# -*- coding: utf-8 -*-
"""mask_refine 单测：椅带低阈扩展 / 连通域过滤 / 洞门控 / 引导滤波上采样。

测试用 100x80 合成图 + work_scale=4(工作分辨率 25x20)。
"""
import numpy as np
import pytest

from video_understanding.spatial.mask_refine import (
    guided_upsample,
    subject_alpha,
)


def _conf(shape=(100, 80)):
    return np.zeros(shape, dtype=np.float32)


class TestSubjectAlpha:
    def test_core_passthrough(self):
        conf = _conf()
        conf[10:40, 20:60] = 0.9  # 上半身核心块
        a = subject_alpha(conf)
        assert a.shape == conf.shape
        assert a[25, 40] == pytest.approx(0.9)
        assert a[90, 10] == 0.0

    def test_band_low_threshold_recovers_wheel(self):
        conf = _conf()
        conf[10:58, 30:50] = 0.9          # 人体核心
        conf[58:99, 30:50] = 0.3          # 腿(椅带内弱响应,连 core)
        conf[60:70, 10:70] = 0.3          # 座面横梁(连通腿与轮)
        conf[70:99, 10:25] = 0.25         # 左轮
        conf[70:99, 65:79] = 0.25         # 右轮
        conf[10:30, 65:79] = 0.25         # 带外弱响应(背景杂物)
        conf[60:70, 0:2] = 0.3            # 椅带内孤立岛(不相连,应丢弃)
        a = subject_alpha(conf, seat_frac=0.58, core_thresh=0.45,
                          band_thresh=0.15, band_boost=0.92)
        assert a[80, 15] >= 0.9           # 左轮召回
        assert a[80, 70] >= 0.9           # 右轮召回
        assert a[20, 70] < 0.5            # 带外低置信不抬升
        assert a[20, 70] == pytest.approx(0.25)
        assert a[65, 1] < 0.5             # 孤立岛被连通域过滤丢弃

    def test_aux_channels_merged_with_gain(self):
        conf = _conf()
        conf[10:58, 30:50] = 0.9
        aux = _conf()
        aux[60:99, 10:70] = 0.4           # chair 类弱响应遍布椅带
        a = subject_alpha(conf, [aux], aux_gain=0.8, band_thresh=0.15)
        assert a[80, 40] >= 0.9           # 0.32 > 0.15 → 椅带内召回

    def test_band_enclosed_gap_filled(self):
        conf = _conf((100, 100))
        conf[60:99, 20:80] = 0.8          # 椅带大块
        conf[75, 50] = 0.0                # 内部小洞
        a = subject_alpha(conf, seat_frac=0.58)
        assert a[75, 50] >= 0.9           # 封闭小洞被填

    def test_big_pocket_kept(self):
        # U 形椅区围出大口袋(类比手臂-轮间空隙,实测 ~2% 帧)
        # work res(25x20):四壁 sel,内部 4x10 空腔
        conf = _conf((100, 100))
        conf[10:56, 30:50] = 0.9          # 人体 core(连到椅带顶壁)
        conf[56:64, 8:76] = 0.9           # 顶壁
        conf[56:96, 8:20] = 0.9           # 左壁
        conf[56:96, 64:76] = 0.9          # 右壁
        conf[88:96, 8:76] = 0.9           # 底壁
        a = subject_alpha(conf, seat_frac=0.58, band_hole_frac=0.012)
        assert a[76, 40] < 0.5            # 大口袋中心保留为背景
        assert a[60, 40] >= 0.9           # 顶壁照常入选

    def test_small_hole_outside_band_filled(self):
        conf = _conf((100, 100))
        conf[10:50, 20:60] = 0.8          # 核心块(带外)
        conf[24:32, 38:46] = 0.0          # 8x8 洞(work res 2x2)
        a = subject_alpha(conf, seat_frac=0.58, hole_area_frac=0.004)
        assert a[28, 42] >= 0.9           # 带外小洞被填

    def test_seat_row_explicit(self):
        conf = _conf()
        conf[80:99, 10:70] = 0.3
        a = subject_alpha(conf, seat_row=85, band_thresh=0.15)
        assert a[90, 40] >= 0.9
        assert a[82, 40] < 0.5

    def test_shape_mismatch_raises(self):
        with pytest.raises(ValueError):
            subject_alpha(_conf(), [_conf((10, 10))])


class TestGuidedUpsample:
    def test_output_shape_and_range(self):
        g = np.random.RandomState(0).rand(200, 100).astype(np.float32)
        a = np.random.RandomState(1).rand(50, 25).astype(np.float32)
        out = guided_upsample(g, a, blur_sigma=0)
        assert out.shape == (200, 100)
        assert 0.0 <= out.min() and out.max() <= 1.0

    def test_edge_sharpens_near_guide_edge(self):
        # 软 alpha 边缘与 guide 边缘重合:输出过渡带应显著变窄
        H, W = 100, 100
        g = np.zeros((H, W), np.float32)
        g[:, :50] = 0.9
        a = np.zeros((H, W), np.float32)
        xs = np.arange(W)
        a[:] = np.clip((60 - xs) / 30.0, 0, 1)[None, :]  # x∈[30,60] 缓坡
        out = guided_upsample(g, a, radius=6, eps=0.005,
                            lo=0.4, hi=0.6, blur_sigma=0)
        row = out[50]
        hi_idx = np.where(row > 0.9)[0]
        lo_idx = np.where(row < 0.1)[0]
        width = lo_idx[lo_idx > hi_idx.max()][0] - hi_idx.max()
        assert width <= 15                       # 缓坡 30px → 锐化后 ≤15px
        edge = np.argmax(row < 0.5)
        assert 42 <= edge <= 60                  # 边缘吸附在 guide 边附近

    def test_flat_region_passes(self):
        g = np.full((60, 60), 0.5, np.float32)
        a = np.ones((15, 15), np.float32)
        out = guided_upsample(g, a, (60, 60), blur_sigma=0)
        assert out.min() > 0.9

    def test_rgb_guide_and_u8_alpha(self):
        g = np.random.RandomState(2).randint(0, 255, (80, 80, 3)).astype(np.uint8)
        a = np.random.RandomState(3).randint(0, 255, (20, 20)).astype(np.uint8)
        out = guided_upsample(g, a, (80, 80))
        assert out.shape == (80, 80)
        assert out.dtype == np.float32

    def test_hard_threshold_when_no_band(self):
        g = np.zeros((40, 40), np.float32)
        a = np.full((10, 10), 0.5, np.float32)
        out = guided_upsample(g, a, (40, 40), lo=0.5, hi=0.5, blur_sigma=0)
        assert set(np.unique(out)) <= {0.0, 1.0}
