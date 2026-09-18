"""StdlibWavAnalyzer：纯标准库 WAV 节奏分析兜底（§33-35）。

合成已知 BPM 的打点 WAV：能量包络 → onset → 自相关 BPM → beat 网格 →
downbeat，全程不依赖 librosa。
"""

from __future__ import annotations

import math
import struct
import wave

import pytest

from video_understanding.audio.analyzer import StdlibWavAnalyzer, default_audio_analyzer


def _write_click_wav(path, bpm=120.0, seconds=6.0, rate=22050):
    """每隔一个拍点写 5ms 衰减脉冲；每 4 拍加重（downbeat 更响）。"""
    interval = 60.0 / bpm
    n = int(seconds * rate)
    samples = [0] * n
    beat_idx = 0
    while True:
        t = beat_idx * interval
        i0 = int(t * rate)
        if i0 >= n:
            break
        amp = 30000 if beat_idx % 4 == 0 else 16000
        for j in range(int(0.005 * rate)):  # 5 ms 脉冲
            if i0 + j < n:
                samples[i0 + j] += int(amp * math.exp(-j / 40.0))
        beat_idx += 1
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(
            struct.pack("<h", max(-32768, min(32767, s))) for s in samples
        ))


def test_stdlib_wav_bpm_beats_downbeats(tmp_path):
    wav = tmp_path / "click_120.wav"
    _write_click_wav(wav, bpm=120.0, seconds=6.0)
    analysis = StdlibWavAnalyzer().analyze(str(wav))

    assert analysis.analyzer == "stdlib_wav_v1"
    assert analysis.bpm is not None and abs(analysis.bpm - 120.0) < 3.0
    # 6s @ 120BPM ≈ 12 beats；网格相位对齐 onset
    assert 10 <= len(analysis.beats) <= 13
    intervals = [b - a for a, b in zip(analysis.beats, analysis.beats[1:])]
    assert all(abs(dt - 0.5) < 0.06 for dt in intervals)
    # downbeat 每 4 拍
    assert analysis.downbeats and all(d in analysis.beats for d in analysis.downbeats)
    dintervals = [b - a for a, b in zip(analysis.downbeats, analysis.downbeats[1:])]
    assert all(abs(dt - 2.0) < 0.12 for dt in dintervals)
    assert analysis.onsets  # onset 检出非空


def test_stdlib_wav_rejects_non_wav(tmp_path):
    bogus = tmp_path / "track.mp3"
    bogus.write_bytes(b"not a wav")
    with pytest.raises(RuntimeError, match="wav|decode"):
        StdlibWavAnalyzer().analyze(str(bogus))


def test_default_analyzer_fallback():
    """无 librosa 环境下默认分析器应退到 StdlibWavAnalyzer。"""
    import importlib.util

    analyzer = default_audio_analyzer()
    if importlib.util.find_spec("librosa") is None:
        assert isinstance(analyzer, StdlibWavAnalyzer)
    else:
        assert analyzer.name == "librosa_v1"
