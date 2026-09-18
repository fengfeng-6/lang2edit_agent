"""音频与节奏理解（§33-35）。

``analyze_audio(asset)`` 对原视频音频与外部音乐素材用同一协议。
实现三级降级：
- ``ProvidedAudioAnalyzer``：调用方直接给 bpm/beats/downbeats（测试与离线管线）；
- ``LibrosaAudioAnalyzer``：懒加载 librosa 做 onset / beat tracking，
  downbeat 用每 4 拍启发式（可选 beat_this 时升级，见 pyproject [audio]）；
- ``StdlibWavAnalyzer``：纯标准库兜底（无 librosa 时接管可解码 .wav），
  RMS 能量包络 → energy-flux onset → 自相关估 BPM → beat 网格 → 每 4 拍 downbeat。

输出统一为 ``AudioAnalysis``；audio 事件（beat/downbeat/music_onset）
由 state 层按查询物化为 SemanticEvent（§34 统一协议）。
"""

from __future__ import annotations

import math
import wave
from array import array
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union

from ..models import AudioAnalysis

AudioSource = Union[str, Path, Dict[str, Any], AudioAnalysis]


class AudioAnalyzer(Protocol):
    name: str

    def available(self) -> bool: ...

    def analyze(self, asset: AudioSource) -> AudioAnalysis: ...


class ProvidedAudioAnalyzer:
    """直接采用调用方提供的节奏数据（asset dict 里的 bpm/beats/…）。"""

    name = "provided_v1"

    def __init__(self, payload: Union[dict, AudioAnalysis]):
        self.payload = payload

    def available(self) -> bool:
        return True

    def analyze(self, asset: AudioSource) -> AudioAnalysis:
        if isinstance(self.payload, AudioAnalysis):
            return self.payload
        data = dict(self.payload)
        if isinstance(asset, dict) and asset.get("asset_id"):
            data.setdefault("asset_id", asset["asset_id"])
        data.setdefault("analyzer", self.name)
        return _validate_audio(data)


def _validate_audio(data: dict) -> AudioAnalysis:
    from gesture_intent.models import model_validate

    return model_validate(AudioAnalysis, data)


def _load_pcm(librosa, path: str):
    """加载为 mono PCM：先走 librosa（wav/flac 经 soundfile，mp3 经
    audioread），失败时退化 PyAV 解码——mp4/m4a 等容器在无 ffmpeg CLI
    的环境下只有后者可靠。"""
    try:
        return librosa.load(path, sr=None, mono=True)
    except Exception:
        import av  # 同属 [video] extras，装了 librosa 的语义环境一般也有 av

        chunks = []
        with av.open(path) as container:
            if not container.streams.audio:
                raise RuntimeError(f"no audio stream in {path}")
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="flt", layout="mono", rate=22050)
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    chunks.append(out.to_ndarray()[0])
            for out in resampler.resample(None):  # flush
                chunks.append(out.to_ndarray()[0])
        if not chunks:
            raise RuntimeError(f"empty audio stream in {path}")
        import numpy as np

        return np.concatenate(chunks).astype(np.float32), 22050


class LibrosaAudioAnalyzer:
    """librosa 节奏分析（可选依赖；未安装时 available()=False）。"""

    name = "librosa_v1"

    def available(self) -> bool:
        import importlib.util

        return importlib.util.find_spec("librosa") is not None

    def analyze(self, asset: AudioSource) -> AudioAnalysis:
        try:
            import librosa
        except ImportError as exc:
            raise RuntimeError(
                f"librosa missing ({exc}); install via pip install -e \".[video]\""
            ) from exc

        if isinstance(asset, dict):
            path = asset.get("path")
            asset_id = asset.get("asset_id") or (Path(path).stem if path else "audio")
        elif isinstance(asset, AudioAnalysis):
            return asset
        else:
            path = str(asset)
            asset_id = Path(path).stem
        if not path:
            raise RuntimeError("audio analysis requires a file path or provided data")

        y, sr = _load_pcm(librosa, path)
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
        beats = [float(t) for t in librosa.frames_to_time(beat_frames, sr=sr)]
        onsets = [float(t) for t in librosa.frames_to_time(
            librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr), sr=sr)]
        bpm = float(tempo[0] if hasattr(tempo, "__len__") else tempo)

        # downbeat：优先 beat_this（若安装），否则每 4 拍启发式 + 强 onset 对齐。
        downbeats = self._downbeats_beattracking(path)
        if not downbeats:
            downbeats = beats[::4]
            onset_set = set(onsets)
            downbeats = [min(onset_set, key=lambda o: abs(o - b)) if onset_set else b
                         for b in downbeats]

        return AudioAnalysis(
            asset_id=asset_id, bpm=round(bpm, 2), beats=beats,
            downbeats=sorted(set(round(d, 4) for d in downbeats)),
            onsets=[round(o, 4) for o in onsets],
            analyzer=self.name,
        )

    @staticmethod
    def _downbeats_beattracking(path: str) -> List[float]:
        try:
            from beat_this.inference import File2Beats  # type: ignore
        except ImportError:
            return []
        result = File2Beats()(path)
        return [float(t) for t in getattr(result, "downbeats", [])]


# ---------------------------------------------------------------------------
# Stdlib WAV fallback（无 librosa 时的最后一级，仅接 .wav）
# ---------------------------------------------------------------------------


def _decode_wav_mono(path: str) -> Tuple[List[float], int]:
    """PCM WAV → (mono samples in [-1, 1], sample_rate)，仅标准库。"""
    try:
        with wave.open(path, "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError, OSError) as exc:
        raise RuntimeError(f"stdlib analyzer cannot decode {path}: {exc}") from exc
    if width == 2:
        data = array("h")
        data.frombytes(raw)
        scale = 32768.0
    elif width == 1:
        unsigned = array("B")
        unsigned.frombytes(raw)
        data = [b - 128 for b in unsigned]  # 8-bit WAV is unsigned
        scale = 128.0
    elif width == 4:
        data = array("i")
        data.frombytes(raw)
        scale = 2147483648.0
    else:
        raise RuntimeError(f"unsupported WAV sample width: {width} bytes")
    if channels > 1:
        data = [
            sum(data[i : i + channels]) / channels
            for i in range(0, len(data), channels)
        ]
    return [s / scale for s in data], rate


def _energy_flux(samples: List[float], rate: int, hop: int = 512, win: int = 1024) -> Tuple[List[float], float]:
    env = [
        math.sqrt(sum(s * s for s in samples[i : i + win]) / win)
        for i in range(0, max(1, len(samples) - win), hop)
    ]
    flux = [max(0.0, env[i] - env[i - 1]) for i in range(1, len(env))]
    return flux, hop / rate


def _onsets_from_flux(flux: List[float], hop_seconds: float, min_gap: float = 0.18) -> List[float]:
    if not flux:
        return []
    peak, floor = max(flux), median(flux)
    threshold = floor + (peak - floor) * 0.3
    times, last = [], -1e9
    for i in range(1, len(flux) - 1):
        if flux[i] >= threshold and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]:
            t = i * hop_seconds
            if t - last >= min_gap:
                times.append(round(t, 4))
                last = t
    return times


def _tempo_lag(flux: List[float], hop_seconds: float) -> Tuple[Optional[float], float]:
    """60–200 BPM 区间内自相关峰对应的 lag（亚帧精度），附归一化强度。"""
    if len(flux) < 8:
        return None, 0.0
    lo = int(round(60.0 / 200.0 / hop_seconds))
    hi = min(int(round(60.0 / 60.0 / hop_seconds)), len(flux) - 1)
    if lo < 2 or hi <= lo:
        return None, 0.0
    mean = sum(flux) / len(flux)
    centered = [f - mean for f in flux]
    energy = sum(c * c for c in centered) or 1.0
    # ±1 帧膨胀包络做相关：真实周期常落在两个整帧之间（如 0.5s=21.53 帧），
    # 整数 lag 在窄峰上错半帧、基频得分反而低于对齐良好的 2T——
    # 不做容差会把 120 BPM 误报成 60 BPM。
    dilated = [
        max(centered[max(0, j - 1)], centered[j], centered[min(len(centered) - 1, j + 1)])
        for j in range(len(centered))
    ]
    scores = {
        lag: sum(centered[i] * dilated[i - lag] for i in range(lag, len(centered))) / energy
        for lag in range(lo, hi + 1)
    }
    best = max(scores.values())
    if best <= 0:
        return None, 0.0
    # 周期 T 在 2T/3T 处也会出现相关峰——取达到峰值 85% 的最小连续 lag 簇
    # （基频而非倍频），簇内按得分加权质心得到亚帧周期（BPM 不致被
    # 整帧量化成 117/123）。
    cands = [lag for lag in range(lo, hi + 1) if scores[lag] >= best * 0.85]
    cluster = [cands[0]]
    for lag in cands[1:]:
        if lag == cluster[-1] + 1:
            cluster.append(lag)
        else:
            break
    refined = sum(lag * scores[lag] for lag in cluster) / sum(scores[lag] for lag in cluster)
    return refined, best


class StdlibWavAnalyzer:
    """无 librosa 时的纯标准库兜底，仅接可解码 ``.wav`` 路径。

    energy-flux onset → 自相关估 BPM → beat 网格相位对齐 onset 能量 →
    downbeat 每 4 拍、相位取 onset 能量最强者。
    """

    name = "stdlib_wav_v1"

    def available(self) -> bool:
        return True  # 无第三方依赖；asset 适用性在 analyze 内判定

    def analyze(self, asset: AudioSource) -> AudioAnalysis:
        if isinstance(asset, dict):
            path = asset.get("path")
            asset_id = asset.get("asset_id") or (Path(path).stem if path else "audio")
        elif isinstance(asset, AudioAnalysis):
            return asset
        else:
            path = str(asset)
            asset_id = Path(path).stem
        if not path or not str(path).lower().endswith(".wav"):
            raise RuntimeError(
                f"stdlib analyzer only handles .wav, got {path!r}; "
                'install librosa via pip install -e ".[video]" for compressed audio'
            )
        samples, rate = _decode_wav_mono(path)
        duration = len(samples) / rate
        flux, hop_seconds = _energy_flux(samples, rate)
        onsets = _onsets_from_flux(flux, hop_seconds)
        lag, periodicity = _tempo_lag(flux, hop_seconds)

        bpm: Optional[float] = None
        beats: List[float] = []
        downbeats: List[float] = []
        if lag:
            bpm = round(60.0 / (lag * hop_seconds), 2)
            # 相位扫描用分数步进（lag 是亚帧值），再就近吸附到 ±2 帧内
            # 的 flux 峰——网格不随帧量化累积漂移，beat 落在真实 onset 上。
            best_phase, best_energy = 0, -1.0
            for phase in range(int(math.ceil(lag))):
                e, pos = 0.0, float(phase)
                while pos < len(flux):
                    e += flux[min(len(flux) - 1, int(round(pos)))]
                    pos += lag
                if e > best_energy:
                    best_phase, best_energy = phase, e
            floor = median(flux)
            grid, pos = [], float(best_phase)
            while pos < len(flux):
                center = int(round(pos))
                w_lo, w_hi = max(0, center - 2), min(len(flux), center + 3)
                peak = max(range(w_lo, w_hi), key=lambda j: flux[j])
                grid.append(peak if flux[peak] > floor else center)
                pos += lag
            beats = sorted({round(i * hop_seconds, 4) for i in grid
                            if i * hop_seconds <= duration})
            if len(beats) >= 4:
                best_off, best_e = 0, -1.0
                for off in range(4):
                    e = sum(
                        flux[int(t / hop_seconds)]
                        for t in beats[off::4]
                        if int(t / hop_seconds) < len(flux)
                    )
                    if e > best_e:
                        best_off, best_e = off, e
                downbeats = beats[best_off::4]

        confidence = min(1.0, periodicity * (1.5 if onsets else 1.0))
        return AudioAnalysis(
            asset_id=asset_id,
            bpm=bpm,
            beats=beats,
            downbeats=downbeats,
            onsets=onsets,
            analyzer=self.name,
            confidence=round(confidence, 4),
        )


def default_audio_analyzer() -> AudioAnalyzer:
    """librosa 可用则用之，否则退回纯标准库 WAV 分析器。"""
    librosa_analyzer = LibrosaAudioAnalyzer()
    return librosa_analyzer if librosa_analyzer.available() else StdlibWavAnalyzer()
