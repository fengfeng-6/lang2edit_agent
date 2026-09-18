"""音频与节奏理解。

BPM、beat、downbeat、onset 分析；Audio Event 与 Visual Event 使用统一
SemanticEvent 协议；原视频音频与外部音乐素材复用同一分析器（§33-35）。

实现：``analyzer.py`` 的 ``AudioAnalyzer`` 协议 + 三级降级
``ProvidedAudioAnalyzer``（注入节奏数据）/ ``LibrosaAudioAnalyzer``
（懒加载 librosa + 可选 beat_this downbeat）/ ``StdlibWavAnalyzer``
（无 librosa 时的纯标准库 .wav 兜底）。
"""

from .analyzer import (
    AudioAnalyzer,
    LibrosaAudioAnalyzer,
    ProvidedAudioAnalyzer,
    StdlibWavAnalyzer,
    default_audio_analyzer,
)

__all__ = [
    "AudioAnalyzer",
    "LibrosaAudioAnalyzer",
    "ProvidedAudioAnalyzer",
    "StdlibWavAnalyzer",
    "default_audio_analyzer",
]
