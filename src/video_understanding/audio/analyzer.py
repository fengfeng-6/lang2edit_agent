"""音频与节奏理解（§33-35）。

``analyze_audio(asset)`` 对原视频音频与外部音乐素材用同一协议。
实现两级降级：
- ``ProvidedAudioAnalyzer``：调用方直接给 bpm/beats/downbeats（测试与离线管线）；
- ``LibrosaAudioAnalyzer``：懒加载 librosa 做 onset / beat tracking，
  downbeat 用每 4 拍启发式（可选 beat_this 时升级，见 pyproject [audio]）。

输出统一为 ``AudioAnalysis``；audio 事件（beat/downbeat/music_onset）
由 state 层按查询物化为 SemanticEvent（§34 统一协议）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Protocol, Union

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

        y, sr = librosa.load(path, sr=None, mono=True)
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
