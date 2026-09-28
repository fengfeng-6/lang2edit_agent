# Module 5 剪辑工具执行模块设计文档

## 1. 模块定位

Module 5 是整个 `lang2edit_agent` 中第一个真正将上游语义状态转换为真实剪辑工程的模块。

前四个模块分别解决：

```text
Module 1：用户想怎么剪
Module 2：原视频里发生了什么
Module 3：应该怎样剪
Module 4：具体使用什么素材
```

Module 5 负责：

```text
如何把已经确定的剪辑计划，
稳定、可追踪、可恢复地转换为真实可编辑工程。
```

其核心目标不是“自动点击剪映”，而是建立一个稳定的执行基础设施：

```text
ResolvedEditingPlan
        ↓
DesiredProjectGraph
        ↓
Project Diff
        ↓
ExecutionPatch
        ↓
Editing DSL
        ↓
Backend Adapter
        ↓
Editable Project
```

Module 5 必须保持软件无关。

剪映、CapCut、FFmpeg 等仅作为不同 Backend，不得反向污染 Planner 和核心执行模型。

---

# 2. 模块核心原则

Module 5 采用以下原则。

### 2.1 规划与执行严格分离

Module 3 决定：

```text
做什么
```

Module 5 决定：

```text
怎样实现已经确定的计划
```

Module 5 不重新：

- 理解自然语言；
- 分析视频动作；
- 选择创意；
- 搜索素材；
- 决定是否降级；
- 改变 Planner 语义。

---

### 2.2 Desired State，而不是命令脚本

不采用：

```text
Plan
→ 一串命令
→ 剪映
```

而采用：

```text
Resolved Plan
        ↓
Desired Project State
        ↓
Current Project State
        ↓
Diff
        ↓
Execution Patch
```

形式上：

\[
\Delta_t
=
\operatorname{Diff}
(
G_t,
G_t^*
)
\]

其中：

- \(G_t\)：当前已经执行成功的工程状态；
- \(G_t^*\)：最新计划对应的目标工程状态；
- \(\Delta_t\)：本次真正需要执行的变化。

LaTeX：

```latex
\Delta_t
=
\operatorname{Diff}
(
G_t,
G_t^*
)
```

这样才能自然支持：

```text
局部修改
素材替换
对象删除
重复执行
失败恢复
撤销/重做
Module 6 持续反馈
```

---

### 2.3 稳定对象身份

Module 5 必须延续整个系统的稳定 ID 链：

```text
requirement_id
    ↓
event_uid
    ↓
plan_item_uid
    ↓
asset_request_uid
    ↓
asset_uid
    ↓
timeline_object_uid
    ↓
backend_object_ref
```

其中：

```text
timeline_object_uid
```

是 Module 5 的长期稳定对象身份。

而：

```text
backend_object_ref
```

只是某次具体剪辑工程中的软件内部 ID。

例如剪映工程 rebuild 后：

```text
tlobj_heart_02
→ segment_A1
```

下一 revision：

```text
tlobj_heart_02
→ segment_X7
```

是允许的。

Module 6 永远只引用：

```text
tlobj_heart_02
```

---

### 2.4 幂等执行

相同 `ResolvedEditingPlan` 连续执行两次时，第二次必须：

```text
CREATE = 0
UPDATE = 0
DELETE = 0
```

全部为：

```text
NOOP
```

不得重复创建贴纸、音乐、文字或其他对象。

---

### 2.5 Backend 不允许偷偷改变语义

Backend 可以做：

```text
soft_pop
→ scale keyframes
```

因为两者具有同等语义。

但 Backend 不允许：

```text
tracking
→ static
```

因为这是 Planner 层的语义降级。

因此必须区分：

```text
Semantic Degradation
```

和：

```text
Implementation Lowering
```

前者属于 Module 3。

后者属于 Module 5 Backend。

---

# 3. 总体架构

Module 5 完整执行链：

```text
ResolvedEditingPlan
        │
        ├── SourceMedia
        ├── AssetRegistry
        ├── AnalysisArtifacts
        ├── BackendCapabilities
        └── ExistingExecutionState
        │
        ↓
ExecutionPreflight
        │
        ↓
GraphCompiler
        │
        ├── SourceTimelineCompiler
        ├── PlanItemCompiler
        ├── MutationCompiler
        └── ResourceResolver
        │
        ↓
DesiredProjectGraph
        │
        ↓
DiffEngine
        │
        ↓
ExecutionPatch
        │
        ↓
Editing DSL
        │
        ↓
Execution Scheduler
        │
        ↓
Backend Adapter
        │
        ├── MemoryBackend
        ├── JianYingDraftBackend
        └── FFmpegRenderBackend
        │
        ↓
Backend Verification
        │
        ↓
Revision Commit
        │
        ├── ExecutionState
        ├── ExecutionHistory
        ├── ExecutionJournal
        └── ProjectEditView
        │
        ↓
Editable Project / Output Video
```

---

# 4. Module 5 输入

建议统一入口：

```python
class ExecutorInput:
    project_id: str

    resolved_plan: ResolvedEditingPlan

    source_media: SourceMedia

    asset_registry: ProjectAssetState

    analysis_artifacts: AnalysisArtifactRegistry

    backend_config: BackendConfig

    current_state: ProjectExecutionState | None

    export_spec: ExportSpec | None

    execution_options: ExecutionOptions
```

这里需要特别强调：

```text
ResolvedEditingPlan
```

不是 Module 5 的全部输入。

因为它本身不应该承载：

- 原始视频真实文件路径；
- Dense Track 文件；
- 前景蒙版文件；
- Asset Registry；
- Backend 环境信息；
- 当前执行 revision。

这些属于 Execution Context。

---

# 5. SourceMedia

Module 5 需要真实源媒体上下文：

```python
class SourceMedia:
    media_uid: str

    video_id: str
    local_uri: str

    duration: float
    fps: float

    width: int
    height: int
    rotation: int

    has_audio: bool

    content_hash: str
    version: str
```

`media_uid` 是 Module 5 稳定媒体引用。

不要使用：

```text
C:\xxx\video.mp4
```

作为对象身份。

文件路径可以改变，而媒体身份应该稳定。

---

# 6. AnalysisArtifactRegistry

Module 5 不主动调用 Module 2。

但它需要消费 Module 2 已经产生的执行级分析结果。

例如：

```text
foreground_subject_mask
person_mask
head_trajectory
right_hand_trajectory
face_track
```

统一建模：

```python
class AnalysisArtifact:
    artifact_uid: str

    artifact_type: str

    video_id: str
    video_version: str

    semantic_state_version: int

    target: str | None

    local_uri: str | None
    inline_data: dict | None

    coordinate_space: str | None
    time_space: str

    semantic_properties: dict

    producer: str
    producer_version: str

    valid: bool
```

例如轮椅背景替换：

```json
{
  "artifact_type": "foreground_subject_mask",
  "semantic_properties": {
    "contains_person": true,
    "contains_mobility_device": true
  }
}
```

如果 Planner 要求：

```text
preserve_mobility_device = true
```

但没有满足条件的蒙版：

```text
Preflight Failed
```

不得自动改用普通人像抠图。

---

# 7. DesiredProjectGraph

`DesiredProjectGraph` 是 Module 5 最核心的数据模型。

它表达：

> 最新计划对应的完整目标剪辑工程应该是什么样。

建议：

```python
class DesiredProjectGraph:
    graph_uid: str

    project_id: str

    source_plan_uid: str
    source_plan_version: int

    project_spec: ProjectSpec

    media_refs: dict[str, MediaRef]

    tracks: dict[str, TrackSpec]

    objects: dict[str, TimelineObject]

    total_duration: float

    warnings: list[str]
```

核心是：

```text
Tracks
+
TimelineObjects
```

---

# 8. ProjectSpec

```python
class ProjectSpec:
    width: int
    height: int

    fps: float

    aspect_ratio: float

    audio_sample_rate: int | None

    duration: float
```

默认继承原始视频：

```text
1080 × 1920
30 fps
```

则工程默认：

```text
1080 × 1920
30 fps
```

Module 5 不自行决定输出平台格式。

---

# 9. TrackSpec

建议：

```python
class TrackSpec:
    track_uid: str

    logical_name: str
    track_type: str

    z_order: int

    enabled: bool = True
    locked: bool = False

    object_uids: list[str]
```

逻辑轨道可以延续 Module 3：

```text
background
main_video
overlay
effect
text
audio
```

稳定 ID：

```text
trk_background
trk_main_video
trk_overlay
trk_effect
trk_text
trk_audio
```

不要使用：

```text
track_1
track_2
```

这种易变化身份。

---

# 10. Logical Track 与 Physical Track

Planner 只表达：

```text
overlay
z_order = 200
```

具体 Backend 可能需要：

```text
overlay_1
overlay_2
overlay_3
```

因此：

```text
Logical Track
```

属于核心模型。

```text
Physical Track
```

属于 Backend。

如果两个 overlay 时间重叠，而后端不允许同一物理轨道重叠，则 Backend TrackAllocator 再分轨。

Planner 不处理这一问题。

---

# 11. TimelineObject

统一表示剪辑工程中的可编辑实体：

```python
class TimelineObject:
    timeline_object_uid: str
    object_key: str

    origin: str

    source_plan_item_uid: str | None
    source_requirement_ids: list[str]
    source_event_uids: list[str]

    object_type: str
    role: str

    semantic_label: str

    track_uid: str

    media_ref: str | None
    asset_uid: str | None

    project_time: TimeRange

    transform: TransformSpec | None

    animation: AnimationSpec | None

    keyframes: list[KeyframeGroup]

    mask_ref: str | None

    parameters: dict

    relations: list[ObjectRelation]

    provenance: dict

    fingerprint: str
```

---

# 12. ObjectKey

一个 PlanItem 不一定只生成一个 TimelineObject。

因此：

\[
K_{\text{object}}
=
(
\text{plan\_item\_uid},
\text{role}
)
\]

LaTeX：

```latex
K_{\text{object}}
=
(
\text{plan\_item\_uid},
\text{role}
)
```

再生成：

\[
UID_{\text{timeline}}
=
H(K_{\text{object}})
\]

LaTeX：

```latex
UID_{\text{timeline}}
=
H(K_{\text{object}})
```

例如背景替换：

```text
pln_bg_01 + background
→ tlobj_bg_xxx

pln_bg_01 + foreground
→ tlobj_fg_xxx
```

---

# 13. Source Video 也必须进入 Graph

原始视频不能作为 Backend 隐式存在的资源。

它本身必须成为 System Object。

没有结构编辑时可以表示为：

```text
tlobj_main_video
├── origin = system
├── role = main_video
├── media_ref = source_video
└── project_time = [0, duration]
```

原始音频也建议独立建模：

```text
tlobj_original_audio
```

即使 Backend 内部实际使用的是同一个 A/V 文件。

---

# 14. SourceTimelineCompiler

只要存在：

```text
freeze
```

源视频就不能继续作为单一：

```text
0 → duration
```

TimelineObject。

例如：

```text
source duration = 12 s
freeze source time = 8 s
freeze duration = 1 s
```

应构造：

```text
SourceSlice A
source: 0 → 8
project: 0 → 8

FreezeFrame
project: 8 → 9

SourceSlice B
source: 8 → 12
project: 9 → 13
```

建议：

```python
class SourceSlice:
    timeline_object_uid: str

    media_ref: str

    source_start: float
    source_end: float

    project_start: float
    project_end: float
```

Module 3 已经计算：

```text
timeline_mapping
```

Module 5 只消费最终 project time。

不得重新根据 event time 推算。

---

# 15. FreezeFrame

建议：

```text
TimelineObject
├── object_type = freeze
├── role = freeze_frame
├── project_time
├── source_time
└── resource_ref
```

Backend 可以：

```text
使用原生 Freeze Frame
```

也可以：

```text
FFmpeg 提取静态帧
+
插入图片片段
```

两种均属于 Implementation Lowering。

---

# 16. Freeze Audio Policy

当前 freeze 语义还需要上游明确音频行为。

建议补充：

```text
freeze_audio_policy
```

支持：

```text
continue
silence
hold
```

MVP 中至少明确：

```text
continue
silence
```

Module 5 不自行猜测。

---

# 17. Transform

Module 3 当前已经使用归一化坐标。

Module 5 继续保持：

```text
x ∈ [0,1]
y ∈ [0,1]
```

直到 Backend Adapter。

Backend 再映射：

\[
x_b=f_x(x,W)
\]

\[
y_b=f_y(y,H)
\]

LaTeX：

```latex
x_b=f_x(x,W)
```

```latex
y_b=f_y(y,H)
```

核心层不得出现：

```text
剪映坐标
Premiere 坐标
pixel-specific transform
```

---

# 18. Keyframe

建议使用：

```python
class KeyframeGroup:
    time: float

    values: dict[str, Any]

    interpolation: str
```

例如：

```json
{
  "time": 4.3,
  "values": {
    "x": 0.51,
    "y": 0.22,
    "scale": 0.16
  },
  "interpolation": "linear"
}
```

这样后续可统一支持：

```text
position
scale
rotation
opacity
volume
```

---

# 19. Animation

Core 中保留语义动画：

```python
class AnimationSpec:
    semantic_type: str

    duration: float | None

    intensity: float | None

    params: dict
```

例如：

```text
soft_pop
```

Backend 决定：

```text
原生动画
```

还是：

```text
scale keyframes
```

Core 不保存具体剪映动画 ID。

---

# 20. MediaRef

Graph 内的 TimelineObject 不直接保存本地文件路径。

统一：

```python
class MediaRef:
    media_ref_uid: str

    source_type: str
    source_uid: str

    local_uri: str

    media_type: str

    content_hash: str

    technical_metadata: dict
```

支持：

```text
source_video
asset
execution_resource
```

---

# 21. PlanOperation 分类

Module 5 不应把所有 Planner Operation 当成同一类型。

建议分为三类。

## 21.1 Object-Producing Operation

```text
replace_background
add_overlay
track_overlay
add_text
add_effect
add_music
add_sound_effect
freeze
```

它们通常会生成 TimelineObject。

---

## 21.2 Object-Mutation Operation

```text
scale_adjust
position_adjust
volume_adjust
replace_music
```

它们不应该生成新的 TimelineObject。

而是修改已有对象。

例如：

```text
add_overlay
→ 创建 heart_02

scale_adjust
→ 修改 heart_02.scale
```

最终 Desired Graph 中仍然只有一个：

```text
heart_02
```

---

## 21.3 Structural Operation

当前主要：

```text
freeze
```

未来：

```text
trim
split
remove_segment
speed_adjust
```

也属于这一类。

---

# 22. GraphCompiler

定义：

```text
GraphCompiler
```

输入：

```text
ResolvedEditingPlan
+
ExecutionContext
```

输出：

```text
DesiredProjectGraph
```

推荐流程：

```text
Initialize Project
        ↓
Create Source Media
        ↓
Create Logical Tracks
        ↓
Compile Object-Producing Items
        ↓
Apply Mutation Items
        ↓
Resolve Asset MediaRefs
        ↓
Resolve Analysis Artifacts
        ↓
Build Source Timeline
        ↓
Normalize Keyframes
        ↓
Compute Fingerprints
        ↓
Validate Desired Graph
```

整个过程必须确定性。

不调用 LLM。

---

# 23. Operation Compiler Registry

不建议写成大型：

```python
if operation == ...
elif operation == ...
```

建议：

```python
OPERATION_COMPILERS = {
    "replace_background": BackgroundCompiler(),
    "add_overlay": OverlayCompiler(),
    "track_overlay": TrackOverlayCompiler(),
    "add_text": TextCompiler(),
    "add_effect": EffectCompiler(),
    "add_music": MusicCompiler(),
    ...
}
```

统一接口：

```python
class PlanItemCompiler:
    def compile(
        self,
        item: ResolvedPlanItem,
        context: CompileContext,
    ) -> list[TimelineObject]:
        ...
```

允许：

```text
0 / 1 / N
```

个 TimelineObject。

---

# 24. add_overlay

典型一对一编译：

```text
ResolvedPlanItem
├── asset_uid
├── project_time
├── transform
└── animation

        ↓

TimelineObject
├── role = overlay
├── media_ref
├── asset_uid
├── project_time
├── transform
└── animation
```

---

# 25. track_overlay

如果 Planner 最终选择：

```text
resolved_capability = keyframes
```

则：

```text
TimelineObject.keyframes
```

保存跟随轨迹。

如果：

```text
resolved_capability = tracking
```

则可以保存：

```text
TrackingBinding
├── target
├── trajectory_ref
├── smoothing
├── sensitivity
└── dead_zone
```

Backend 再决定是否调用原生 tracking。

---

# 26. add_text

Text 本身是 TimelineObject，不进入 AssetRegistry。

建议：

```text
object_type = text
role = text

parameters:
    content
    font
    size
    alignment
    style
```

MVP 可先保证：

```text
content
position
scale
time
```

其余样式逐步扩展。

---

# 27. add_music

音乐表示为：

```text
TimelineObject
├── object_type = audio
├── role = music
├── asset_uid
├── project_time
└── volume
```

如果原视频已有音频：

```text
add_music
```

默认不等于删除原音频。

只有：

```text
replace_music
```

才修改对应音乐/音轨对象。

---

# 28. replace_background

背景替换在 Desired Graph 中不建议表示成一个简单函数调用。

应构造成：

```text
Background Object
+
Foreground Subject Object
```

例如：

```text
background track
    └── background asset

main video
    └── foreground subject
        └── foreground_subject_mask
```

从而 Backend 可以自由选择：

```text
native external mask
alpha intermediate
其他等价实现
```

---

# 29. ExecutionResourceBuilder

Module 5 允许生成技术执行资源。

例如：

```text
freeze frame image
foreground alpha video
proxy
audio extract
```

这些不是创作素材，也不是生成式素材。

它们完全来自：

```text
原视频
+
已有分析结果
```

统一建模：

```python
class ExecutionResource:
    resource_uid: str

    resource_type: str

    source_refs: list[str]

    local_uri: str

    content_hash: str

    producer: str
    producer_version: str

    reusable: bool
```

---

# 30. Execution Resource Cache

资源缓存键：

\[
K_{\text{resource}}
=
H(
\text{input hashes},
\text{parameters},
\text{producer version}
)
\]

LaTeX：

```latex
K_{\text{resource}}
=
H(
\text{input hashes},
\text{parameters},
\text{producer version}
)
```

从而例如：

```text
source_video + mask
```

不变时：

```text
foreground_alpha_video
```

不必重复生成。

---

# 31. Fingerprint

每个 TimelineObject 计算：

\[
F_o
=
H(
\operatorname{CanonicalSerialize}(o)
)
\]

LaTeX：

```latex
F_o
=
H(
\operatorname{CanonicalSerialize}(o)
)
```

Fingerprint 只包含影响最终结果的属性：

```text
asset
media
time
transform
animation
keyframes
mask
parameters
```

不包含：

```text
backend_object_ref
execution timestamp
runtime status
```

---

# 32. DiffEngine

每个对象分为：

```text
CREATE
UPDATE
DELETE
NOOP
```

规则：

\[
D(o)=
\begin{cases}
CREATE, & o \in G^* \land o \notin G \\
DELETE, & o \notin G^* \land o \in G \\
UPDATE, & F_o^* \neq F_o \\
NOOP, & F_o^* = F_o
\end{cases}
\]

LaTeX：

```latex
D(o)=
\begin{cases}
CREATE, & o \in G^* \land o \notin G \\
DELETE, & o \notin G^* \land o \in G \\
UPDATE, & F_o^* \neq F_o \\
NOOP, & F_o^* = F_o
\end{cases}
```

---

# 33. Property-Level Diff

发现 UPDATE 后，不应简单：

```text
delete old
+
create new
```

而继续分析具体属性。

例如：

```text
old:
scale = 0.16
position = [0.5, 0.3]
asset = heart_A

new:
scale = 0.12
position = [0.5, 0.3]
asset = heart_A
```

只生成：

```text
set_transform(scale=0.12)
```

这对于 Module 6 的局部修改非常关键。

---

# 34. 素材替换

Module 4 如果把：

```text
asset_A
→ asset_B
```

而：

```text
plan_item_uid
```

不变，则：

```text
timeline_object_uid
```

也不变。

Diff 只生成：

```text
replace_media
```

其：

```text
时间
位置
动画
关键帧
```

全部保持。

---

# 35. ExecutionPatch

建议：

```python
class ExecutionPatch:
    patch_uid: str

    project_id: str

    base_revision: int
    target_revision: int

    source_plan_uid: str

    operations: list[EditOperation]

    affected_plan_item_uids: list[str]
    affected_timeline_object_uids: list[str]

    summary: DiffSummary

    validation: PatchValidation
```

---

# 36. Editing DSL

DSL 不设计成字符串脚本。

使用强类型结构：

```python
class EditOperation:
    operation_uid: str

    op_type: str

    target_uid: str | None

    arguments: dict

    depends_on: list[str]

    idempotency_key: str

    status: str
```

---

# 37. DSL Operation

第一阶段支持：

```text
create_project
open_project

import_media

ensure_track

create_object
delete_object
replace_media

set_time_range
set_transform
set_animation
set_keyframes
set_mask

set_text
set_volume

freeze_frame

save_project
export_video
```

这里故意没有：

```text
add_overlay
add_sticker
add_image
```

因为这些是 Planner 语义。

在 DSL 层它们都只是：

```text
create_object
```

---

# 38. Operation DAG

操作通过：

```text
depends_on
```

形成依赖图。

例如：

```text
import heart.png
        ↓
create heart object
        ↓
set transform
```

不同对象之间可以并行。

Scheduler 通过拓扑排序执行。

---

# 39. Idempotency Key

每个操作必须具有：

```text
idempotency_key
```

例如：

```text
create:tlobj_heart_02:v3
```

这样程序崩溃后可以判断：

```text
该操作是否已经执行
```

避免重复创建。

---

# 40. Backend 模式

定义：

```text
ProjectMaterializationMode
```

三种模式：

```text
incremental
rebuild
render_only
```

### incremental

直接修改已有工程。

### rebuild

根据完整 DesiredProjectGraph 重新物化工程。

### render_only

只生成扁平视频。

---

# 41. MVP Backend 路线

建议：

```text
MemoryBackend
        ↓
JianYingDraftBackend
        ↓
FFmpegRenderBackend
```

---

# 42. MemoryBackend

用途不是剪视频，而是验证 Module 5 Core。

支持：

```text
CREATE
UPDATE
DELETE
NOOP
```

完整模拟：

```text
tracks
objects
media
revision
```

Module 5 大部分单元测试都应该在 MemoryBackend 上完成。

---

# 43. JianYingDraftBackend

MVP 的第一个真实可编辑工程 Backend。

建议第一版：

```text
materialization_mode = rebuild
```

即：

```text
DesiredProjectGraph
→ 新 revision 剪映草稿
```

而不要求：

```text
读取任意已有新版剪映工程
→ 原位修改
```

逻辑层仍然计算 Diff。

只是物理 Backend 根据自身限制重新物化。

---

# 44. Logical Incremental 与 Physical Rebuild

例如：

```text
第二个爱心小一点
```

逻辑 Diff：

```text
heart_01 = NOOP
heart_02 = UPDATE(scale)
```

即使 JianYing Backend 最终：

```text
rebuild entire draft
```

系统仍然知道真正语义变化只有：

```text
heart_02.scale
```

因此 rebuild 不会破坏 Module 6 的增量语义。

---

# 45. FFmpeg Backend

FFmpeg 不作为主要可编辑 Backend。

因为其最终结果通常是：

```text
flattened video
```

无法保留稳定 TimelineObject。

FFmpeg 主要承担：

```text
ExecutionResourceBuilder
```

和：

```text
ReferenceRenderBackend
```

例如：

```text
提取 freeze frame
生成 alpha foreground
转码
预览渲染
结构验证
```

---

# 46. UI Automation

UI Automation 不用于：

```text
拖动时间线
创建对象
逐帧打关键帧
设置坐标
```

核心编辑。

它只用于：

```text
打开应用
打开指定工程
触发导出
设置导出参数
处理必要弹窗
```

即：

```text
Project Lifecycle Automation
```

而不是：

```text
Timeline Editing Automation
```

---

# 47. Backend API

建议：

```python
class ExecutorBackend:

    def backend_info(self) -> BackendInfo:
        ...

    def probe_environment(self) -> BackendProbeResult:
        ...

    def describe_capabilities(
        self,
    ) -> BackendCapabilityManifest:
        ...

    def planner_capabilities(
        self,
    ) -> ToolCapabilityProfile:
        ...

    def begin_session(
        self,
        project_context,
    ) -> BackendSession:
        ...

    def execute(
        self,
        session,
        patch,
        desired_graph,
    ) -> BackendExecutionReport:
        ...

    def verify(
        self,
        session,
        desired_graph,
    ) -> BackendVerificationReport:
        ...

    def save(
        self,
        session,
    ) -> BackendProjectRef:
        ...

    def export(
        self,
        session,
        export_spec,
    ) -> ExportResult:
        ...
```

incremental Backend 主要消费：

```text
ExecutionPatch
```

rebuild Backend 可以主要消费：

```text
DesiredProjectGraph
```

---

# 48. Backend Capability

真实 Backend 不适合只有 True / False。

建议：

```text
supported
unsupported
unverified
```

例如：

```python
class CapabilityEntry:
    status: CapabilityStatus

    source: str

    backend_version: str
    app_version: str

    constraints: dict

    note: str
```

Planner 侧保守映射：

```text
supported
→ true

unsupported
→ false

unverified
→ false
```

---

# 49. Capability 闭环

执行流程应形成：

```text
Backend Probe
        ↓
BackendCapabilityManifest
        ↓
PlannerCapabilityAdapter
        ↓
ToolCapabilityProfile
        ↓
EditingPlanner
```

这样 Planner 根据真实后端能力规划。

而不是 Planner 假设 Backend 能做什么。

---

# 50. Implementation Lowering

Backend 需要记录：

```text
ImplementationLowering
```

例如：

```text
semantic_feature = soft_pop

implementation_kind = synthesized_keyframes
```

它和：

```text
degradation_applied
```

严格分离。

---

# 51. BackendResourceCatalog

剪映原生：

```text
动画
效果
滤镜
转场
```

不是 Module 4 的普通 AssetRecord。

建议：

```text
BackendResourceCatalog
```

维护：

```text
soft_pop
→ JianYing native animation X
```

它属于：

```text
Backend Implementation Mapping
```

而不是：

```text
Creative Asset Search
```

---

# 52. TrackAllocator

Backend 根据逻辑轨道分配物理轨道。

若：

\[
[t_i^s,t_i^e)
\cap
[t_j^s,t_j^e)
\neq
\varnothing
\]

LaTeX：

```latex
[t_i^s,t_i^e)
\cap
[t_j^s,t_j^e)
\neq
\varnothing
```

且 Backend 不允许同物理轨道重叠，则分到不同 physical track。

MVP 使用简单贪心区间分配即可。

---

# 53. CoordinateMapper

Backend 独立：

```text
CoordinateMapper
```

负责：

```text
normalized coordinates
→
backend coordinates
```

统一处理：

```text
横竖屏
rotation
anchor
scale convention
```

不要在各个 operation 内重复实现。

---

# 54. TimeMapper

Backend 只负责：

```text
seconds
→
backend time unit
```

例如：

```text
seconds
→
microseconds
```

不能重新：

```text
计算 freeze shift
计算 event time
```

Project Time 已由 Module 3 materialize 完成。

---

# 55. BackendProjectRef

```python
class BackendProjectRef:
    backend_id: str

    project_revision: int

    local_uri: str | None

    native_project_id: str | None

    created_at: str

    valid: bool
```

---

# 56. 状态管理总体模型

Module 5 需要区分：

```text
Desired State
Execution State
Execution Journal
Backend Project
```

分别表示：

```text
应该是什么
已经成功执行到什么
本次执行进行到哪里
真实后端生成了什么
```

---

# 57. ProjectExecutionState

建议：

```python
class ProjectExecutionState:
    schema_version: int

    project_id: str
    revision: int

    backend_id: str
    backend_version: str
    app_version: str

    project_ref: BackendProjectRef

    source_plan_uid: str
    source_plan_version: int

    video_id: str
    video_version: str

    graph_fingerprint: str

    tracks: dict

    timeline_objects: dict

    media_refs: dict

    execution_resources: dict

    plan_item_index: dict[str, list[str]]

    requirement_index: dict[str, list[str]]

    asset_usage_index: dict[str, list[str]]

    total_duration: float

    last_patch_uid: str | None

    status: str
```

---

# 58. Revision

Revision 只表示：

> 已经完整成功提交的工程状态。

例如：

```text
revision 4
```

如果下一次 execution 失败：

```text
execution failed
```

当前 revision 仍然：

```text
4
```

不会产生 revision 5。

---

# 59. Execution UID

每次：

```text
executor.apply()
```

都分配：

```text
execution_uid
```

例如：

```text
exe_000017
```

失败 execution 仍然保留历史。

但不会产生成功 revision。

---

# 60. 状态持久化

沿用模块 1 和模块 4 已有的：

```text
tmp file
↓
os.replace
```

模式。

Module 5 推荐：

```text
execution/
├── state.json
├── current.json
├── current_graph.json
│
├── history.jsonl
├── journal.jsonl
│
├── runs/
│
├── revisions/
│
├── resources/
│
└── backend/
```

---

# 61. history.jsonl

每个成功或失败的 execution 增加一条执行摘要。

例如：

```json
{
  "execution_uid": "exe_0017",
  "patch_uid": "patch_0017",

  "base_revision": 4,
  "target_revision": 5,

  "created": 0,
  "updated": 1,
  "deleted": 0,
  "noop": 7,

  "status": "completed"
}
```

---

# 62. journal.jsonl

记录每一个底层 Operation：

```json
{
  "execution_uid": "exe_0017",

  "operation_uid": "op_003",

  "operation_type": "set_transform",

  "target_uid": "tlobj_heart_02",

  "status": "completed"
}
```

History 是：

```text
用户可观察版本历史
```

Journal 是：

```text
执行级内部历史
```

---

# 63. Execution State Machine

Execution：

```text
prepared
preflight_passed
compiled
executing
verifying
committing
completed
failed
aborted
```

---

# 64. Operation State Machine

Operation：

```text
pending
ready
executing
completed
failed
skipped
rolled_back
```

如果依赖失败：

```text
dependent operation
→ skipped
```

而不是全部报告 failed。

---

# 65. ExecutionFailure

```python
class ExecutionFailure:
    failure_uid: str

    execution_uid: str
    operation_uid: str | None

    category: str
    code: str

    message: str

    retryable: bool

    affected_objects: list[str]

    caused_by: str | None
```

category：

```text
preflight
resource
backend
capability
validation
filesystem
timeout
internal
```

---

# 66. Candidate Revision

MVP 推荐：

```text
Revision-Level Atomicity
```

即：

```text
Candidate Revision
        ↓
全部生成
        ↓
全部验证
        ↓
Commit
```

如果任何 blocking failure：

```text
Discard Candidate Revision
```

当前可用 revision 不受影响。

---

# 67. JianYing rebuild 事务

例如：

```text
revision_tmp_exe_0017/
        ↓
生成完整草稿
        ↓
verify
        ↓
promote
        ↓
rev_0005/
```

中途失败：

```text
rev_0004
```

继续作为 current。

---

# 68. current pointer

建议：

```text
execution/current.json
```

例如：

```json
{
  "revision": 5,
  "manifest": "revisions/rev_0005/manifest.json"
}
```

通过 atomic replace 修改。

---

# 69. Revision Manifest

每个 revision：

```text
manifest.json
```

例如：

```json
{
  "revision": 5,

  "execution_uid": "exe_0017",

  "state": "committed",

  "graph_fingerprint": "...",

  "backend_project_ref": "..."
}
```

只有：

```text
state = committed
```

才能成为 current。

---

# 70. Crash Recovery

启动时检查：

```text
state.json
current.json
journal.jsonl
runs/
temp revisions
```

对于 rebuild Backend：

如果发现：

```text
unfinished candidate revision
```

直接：

```text
discard / quarantine
```

然后恢复最后一个 committed revision。

---

# 71. needs_reconciliation

如果 Backend 已产生副作用，但系统无法判断真实状态：

```text
status = needs_reconciliation
```

在恢复完成之前：

```text
禁止继续自动执行
```

未来 incremental Backend 可以实现：

```text
inspect_project()
```

重新同步。

MVP JianYing rebuild 基本可以避免这一问题。

---

# 72. Concurrency

同一：

```text
project_id
```

同时只允许一个 active execution。

建议使用：

```text
project.lock
```

并检查：

```text
ExecutionPatch.base_revision
==
current_revision
```

否则返回：

```text
stale_patch
```

重新 Diff。

---

# 73. Verification

分两层。

## Core Verification

软件无关：

```text
所有 active PlanItem 均有对应对象

所有引用有效

时间范围有效

Graph 没有 dangling reference

总时长正确

Asset 存在
```

## Backend Verification

具体 Backend：

```text
工程目录存在

所有对象产生 BackendRef

素材路径有效

轨道生成正确

关键帧有效

工程保存成功
```

---

# 74. Project 与 Export 状态分离

例如：

```text
project_status = completed

export_status = failed
```

代表：

> 剪映工程生成成功，但自动导出失败。

这是有效状态。

ExportStatus：

```text
not_requested
pending
completed
failed
unsupported
requires_user_action
```

---

# 75. ProjectEditView

Module 5 给 Module 6 的最重要接口不是剪映 Backend，而是：

```text
ProjectEditView
```

例如：

```python
class ProjectEditView:
    project_id: str

    revision: int

    objects: list[EditableObjectView]
```

---

# 76. EditableObjectView

```python
class EditableObjectView:
    object_uid: str

    display_id: str

    source_requirement_ids: list[str]

    source_plan_item_uid: str | None

    object_type: str

    role: str

    semantic_label: str

    asset_uid: str | None

    project_time: TimeRange

    editable_properties: list[str]

    current_properties: dict
```

例如：

```json
{
  "object_uid": "tlobj_heart_02",

  "display_id": "overlay_heart_02",

  "semantic_label": "heart",

  "project_time": {
    "start": 10.2,
    "end": 10.9
  },

  "current_properties": {
    "scale": 0.12,
    "position": [0.52, 0.30],
    "asset_uid": "ast_heart_17"
  },

  "editable_properties": [
    "scale",
    "position",
    "asset",
    "animation"
  ]
}
```

---

# 77. Module 6 的正确修改链路

用户：

```text
第二个爱心小一点
```

正确流程：

```text
ProjectEditView
        ↓
Reference Resolution
        ↓
tlobj_heart_02
        ↓
source_plan_item_uid
        ↓
IntentPatch
        ↓
Partial Replanning
        ↓
ResolvedEditingPlan
        ↓
Graph Diff
        ↓
UPDATE heart_02.scale
```

Module 6 不允许直接：

```text
executor.set_scale(...)
```

否则：

```text
Intent
Planner
Execution
```

会失去一致性。

---

# 78. Undo / Redo

必须区分：

```text
Execution Rollback
```

与：

```text
Semantic Undo
```

Execution Rollback：

```text
checkout execution revision
```

主要用于：

```text
Debug
故障恢复
```

真正用户 Undo 应由 Module 6 协调：

```text
Intent
Planner
Assets
Execution
```

整体版本回退。

因此 Module 5 不维护传统：

```text
undo stack / redo stack
```

而维护：

```text
revision history
```

即可。

---

# 79. requirement_index

ExecutionState 维护：

```text
requirement_id
→ timeline_object_uid[]
```

例如：

```text
req_heart_all
→
tlobj_heart_01
tlobj_heart_02
```

方便后续：

```text
所有爱心都小一点
```

---

# 80. plan_item_index

维护：

```text
plan_item_uid
→ timeline_object_uid[]
```

例如：

```text
pln_bg_01
→
tlobj_background
tlobj_foreground
```

这是 Module 5 最核心索引。

---

# 81. asset_usage_index

维护：

```text
asset_uid
→ timeline_object_uid[]
```

便于：

```text
更换所有使用某个素材的对象
```

但 Module 5 不直接修改 Module 4 的 Asset Registry 文件。

而在 ExecutionResult 中返回：

```text
active_asset_usage
```

由 Controller 协调。

---

# 82. Provenance

TimelineObject 建议保留：

```text
requirement_ids
event_uids
plan_item_uid
resolved_plan_uid
asset_uid
semantic_state_version
```

用于：

```text
调试
解释
历史追踪
Module 6 引用
```

但 Executor 不使用 event_uid 重新计算时间。

---

# 83. ExecutionResult

顶层输出：

```text
ExecutionResult
├── execution_uid
├── status
│
├── base_revision
├── revision
│
├── project_ref
│
├── patch_summary
│   ├── created
│   ├── updated
│   ├── deleted
│   └── unchanged
│
├── object_changes
│
├── execution_state
├── edit_view
│
├── verification
├── dependencies
├── warnings
├── errors
│
├── project_status
└── export_status
```

---

# 84. ExecutorDependency

当 Module 5 缺资源时不直接调用其他模块。

返回：

```python
class ExecutorDependency:
    dependency_uid: str

    type: str

    target: str

    required_resource: str

    blocking: bool

    reason: str
```

类型：

```text
analysis_artifact
asset
backend_capability
source_media
```

由 Controller 决定下一步调用哪个模块。

---

# 85. Preflight

执行前检查：

```text
ResolvedPlan.validation

source video

AssetRecord

local_uri

analysis artifact

Backend capability

workspace

current revision
```

Plan 状态建议：

```text
blocked
→ reject

needs_dependency
→ dependency required

valid
→ execute

valid_with_warnings
→ execute
```

---

# 86. Asset Preflight

Executor 只检查：

```text
asset_uid 存在

local_uri 可读

integrity.decodable = true

media_type 合法
```

不负责：

```text
搜索
下载
候选排序
素材选择
```

这些全部属于 Module 4。

---

# 87. Backend Capability Mismatch

如果 Planner 已经规划：

```text
resolved_capability = keyframes
```

执行时发现 Backend：

```text
keyframes = false
```

Module 5 返回：

```text
capability_mismatch
```

不能自行：

```text
keyframes → static
```

Controller 再调用 Planner 重新规划。

---

# 88. Module 5 API

建议高层接口：

```python
executor.compile(...)
executor.diff(...)
executor.apply(...)
executor.verify(...)
executor.export(...)
executor.inspect(...)
```

其中：

```python
executor.apply(...)
```

为主要入口：

```text
Preflight
↓
Compile
↓
Diff
↓
Execute
↓
Verify
↓
Commit
```

---

# 89. dry_run

必须支持：

```python
executor.apply(
    ...,
    dry_run=True,
)
```

只执行：

```text
Preflight
Compile
Diff
Patch Build
```

不修改真实工程。

用于：

```text
Debug
测试
修改预览
Module 6 反馈确认
```

---

# 90. CLI

建议：

```text
python -m edit_executor compile

python -m edit_executor apply

python -m edit_executor inspect

python -m edit_executor revisions
```

MVP 至少：

```text
compile
apply
inspect
```

---

# 91. 推荐代码结构

```text
src/edit_executor/
├── __init__.py
├── api.py
├── models.py
├── cli.py
│
├── context/
│   └── builder.py
│
├── compiler/
│   ├── graph.py
│   ├── source_timeline.py
│   ├── operations.py
│   ├── mutations.py
│   └── fingerprint.py
│
├── diff/
│   ├── engine.py
│   └── properties.py
│
├── dsl/
│   ├── models.py
│   ├── builder.py
│   └── scheduler.py
│
├── resources/
│   ├── builder.py
│   ├── cache.py
│   └── models.py
│
├── validation/
│   ├── preflight.py
│   └── verifier.py
│
├── state/
│   ├── manager.py
│   ├── store.py
│   ├── revision.py
│   └── recovery.py
│
└── backends/
    ├── base.py
    ├── memory.py
    ├── ffmpeg.py
    └── jianying/
        ├── backend.py
        ├── probe.py
        ├── capabilities.py
        ├── lowerer.py
        ├── project_builder.py
        ├── track_allocator.py
        ├── coordinate_mapper.py
        ├── time_mapper.py
        ├── resource_catalog.py
        └── verifier.py
```

---

# 92. Module 3 最小接口调整

正式开始 Module 5 前，建议对 Module 3 做三个最小调整。

## 92.1 resolved_capability

当前：

```text
PlanItem.resolved_capability
```

应继续透传到：

```text
ResolvedPlanItem.resolved_capability
```

避免 Executor 重新推断。

---

## 92.2 target

`ResolvedPlanItem` 应继续保留：

```text
target
```

或：

```text
target_ref
```

用于：

```text
scale_adjust
position_adjust
volume_adjust
replace_music
```

明确修改目标。

---

## 92.3 freeze_audio_policy

为 freeze 明确：

```text
freeze_audio_policy
```

避免 Module 5 猜测音频行为。

除此之外，不建议为了 Module 5 大规模改动 Module 1–4。

---

# 93. Module 5 MVP 实施阶段

建议分四个 Milestone。

## Milestone 5.1 Executor Core

实现：

```text
models
ExecutionContext
DesiredProjectGraph
TimelineObject
GraphCompiler
SourceTimelineCompiler
DiffEngine
Editing DSL
```

验收：

```text
ResolvedEditingPlan
→
ExecutionPatch
```

---

## Milestone 5.2 Stateful Execution

实现：

```text
MemoryBackend
ExecutionState
History
Journal
Revision
Atomic Commit
Recovery
ProjectEditView
```

验收：

```text
首次创建
局部修改
删除
素材替换
重复执行
失败恢复
```

全部正确。

完成这一阶段后，Module 6 即可开始并行实现。

---

## Milestone 5.3 JianYing Draft

实现：

```text
JianYingDraftBackend
TrackAllocator
CoordinateMapper
TimeMapper
BackendResourceCatalog
Backend Verification
```

验收：

```text
DesiredProjectGraph
→
可打开、可继续人工编辑的剪映工程
```

---

## Milestone 5.4 Execution Resources & Export

实现：

```text
freeze frame extraction
foreground alpha
FFmpeg reference render
JianYing exporter
```

达到：

```text
可编辑工程
+
自动视频输出
```

---

# 94. 核心验收场景

## 场景 1：每次比心出现爱心

第一次执行：

```text
heart_01 CREATE
heart_02 CREATE
```

时间和位置必须与 ResolvedPlan 一致。

---

## 场景 2：第二个爱心小一点

重新规划：

```text
heart_01 = NOOP

heart_02 = UPDATE(scale)
```

不能重新创建第一个爱心。

---

## 场景 3：换一个爱心

Module 4：

```text
asset_A
→ asset_B
```

Module 5：

```text
timeline_object_uid
不变
```

仅：

```text
replace_media
```

---

## 场景 4：删除第二个爱心

新 Desired Graph 中：

```text
heart_02
```

消失。

Diff：

```text
DELETE heart_02
```

AssetRecord 保留。

---

## 场景 5：皇冠跟随头部

如果 Planner 最终：

```text
resolved_capability = keyframes
```

剪映工程中必须真实产生位置关键帧。

Backend 不允许自行换回：

```text
native tracking
```

或：

```text
static
```

---

## 场景 6：轮椅用户背景替换

必须存在：

```text
foreground_subject_mask
```

并确认：

```text
contains_person = true

contains_mobility_device = true
```

否则：

```text
Preflight Failed
```

---

## 场景 7：最终动作定格

工程必须形成：

```text
source slice A
freeze frame
source slice B
```

最终时长必须与：

```text
ResolvedEditingPlan.timeline_mapping.total_duration
```

一致。

---

## 场景 8：重复执行

完全相同 Plan 第二次执行：

```text
CREATE = 0
UPDATE = 0
DELETE = 0
```

全部：

```text
NOOP
```

---

## 场景 9：执行中失败

模拟 Backend 中途失败。

要求：

```text
current revision 不变化

current graph 不变化

旧工程仍可用

失败 execution 被完整记录
```

---

# 95. Module 5 Definition of Done

Module 5 MVP 完成标准为：

1. 能消费当前 `ResolvedEditingPlan`；

2. 能从 AssetRegistry 根据 `asset_uid` 找到本地素材；

3. 能消费 SourceMedia 和 AnalysisArtifact；

4. 能构建完整 DesiredProjectGraph；

5. 能表达主视频、原音频、贴图、文字、音乐、背景、定格；

6. 能处理 freeze 后的 Source Timeline 切片；

7. 能生成稳定 `timeline_object_uid`；

8. 能正确计算 CREATE / UPDATE / DELETE / NOOP；

9. 能进行 Property-Level Diff；

10. 相同 Plan 重复执行具有幂等性；

11. 能生成强类型 Editing DSL；

12. MemoryBackend 可以完整执行；

13. 执行失败不会污染 current revision；

14. 状态可以跨进程重启恢复；

15. 能生成 ProjectEditView；

16. 用户局部修改后，未受影响对象 UID 不变化；

17. JianYingDraftBackend 可以生成可继续人工编辑的剪映工程；

18. 轮椅背景替换缺少合法前景蒙版时不能静默执行；

19. Planner Capability 与 Backend Capability 可以闭环；

20. Planner 语义降级与 Backend 实现方式严格分离；

21. 工程生成状态与视频导出状态严格分离。

---

# 96. Module 5 与 Module 6 的最终接口

Module 5 向 Module 6 提供：

```text
get_edit_view()

get_revision()

get_diff(old_revision, new_revision)

apply(new_resolved_plan)
```

Module 6 不直接调用：

```text
set_scale
set_position
replace_asset
```

所有用户反馈仍经过：

```text
用户反馈
↓
IntentPatch
↓
Partial Replanning
↓
ResolvedEditingPlan
↓
Module 5
```

保持整个 Agent 状态一致。

---

# 97. 最终模块边界

Module 5 负责：

```text
目标工程构造

源视频时间线构造

轨道映射

媒体引用

执行级关键帧

蒙版绑定

技术执行资源

工程 Diff

Editing DSL

Backend 调用

执行状态持久化

失败恢复

工程版本

工程验证

可编辑对象视图

工程导出
```

Module 5 不负责：

```text
理解自然语言

识别视频动作

决定创意

搜索素材

选择素材

Planner 级语义降级

重新运行视频分析

解释用户反馈
```

---

# 98. 最终结论

Module 5 正式采用：

```text
Desired State
+
Diff
+
Revision
```

模型，而不是简单的顺序脚本执行模型。

真实 Backend 第一阶段采用：

```text
MemoryBackend
+
JianYingDraftBackend
+
FFmpegRenderBackend
```

其中：

```text
MemoryBackend
```

验证核心执行逻辑；

```text
JianYingDraftBackend
```

生成真实可编辑工程；

```text
FFmpeg
```

承担技术资源生成和参考渲染。

JianYing 第一版采用：

```text
逻辑增量
+
物理 rebuild
```

模式。

用户持续修改依赖：

```text
稳定 TimelineObject
```

而不是重新创建全部对象身份。

执行失败依靠：

```text
Candidate Revision
+
Verification
+
Atomic Commit
```

保证最后一个成功工程始终可用。

Module 6 通过：

```text
ProjectEditView
```

理解当前可编辑对象，但任何修改仍回到：

```text
Intent
→ Planner
→ ResolvedPlan
→ Executor
```

完整主链路。

因此 Module 5 的核心价值并不是“控制某个剪辑软件”，而是建立一个：

```text
确定性
可追踪
可恢复
幂等
可增量
后端解耦
```

的剪辑执行层，使整个 `lang2edit_agent` 从“能够理解和规划剪辑”真正进入“能够持续维护真实可编辑工程”的阶段。