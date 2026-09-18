"""视频预处理。

metadata 提取（duration / fps / resolution / codec / rotation /
audio presence，§6）：``metadata.py`` 提供调用方注入与 ffprobe 两级
降级；秒级时间归一由 ``VideoMetadata.time_of_frame`` 承载（t_i = i/f）。
"""
