"""模块二对外接口（占位，待实现）。

设计文档 §52 定义的第一阶段接口：

  analyze_video(video)                       基础视频分析：Metadata + Base Spatial State
  resolve_queries(required_video_queries)    需求驱动的定向分析（消费模块一输出，§53）
  get_semantic_events(query)                 读取已有语义事件
  get_spatial_snapshot(event_uid)            事件级空间快照
  get_spatial_track(target, time_range)      空间轨迹（跟头 / 跟手 / 跟人物）
  analyze_audio(asset_id)                    音频分析（原视频音频与外部音乐复用，§35）
  build_semantic_view(context)               为 Planner / LLM 构建任务相关语义视图
  invalidate(dependency)                     分析结果失效（§47）
"""
