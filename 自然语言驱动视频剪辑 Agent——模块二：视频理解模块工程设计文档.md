# 自然语言驱动视频剪辑 Agent  
## 模块二：视频理解模块工程设计文档

## 1. 模块定位

视频理解模块（Video Understanding）负责根据自然语言需求理解模块生成的 `required_video_queries`，对原始视频执行面向剪辑任务的定向分析，并将用户语言中的动作、手势、姿态、视频结构和音乐节点等语义概念映射为具有明确时间、空间位置和置信度的视频语义事件。

模块核心解决的问题为：

\[
\text{Semantic Requirement}
\rightarrow
\text{What / When / Where in Video}
\]

LaTeX：

```latex
\text{Semantic Requirement}
\rightarrow
\text{What / When / Where in Video}
```

其中：

- `What`：视频中发生了什么；
- `When`：事件何时开始、何时最典型、何时结束；
- `Where`：人物、手势、面部或相关动作在画面中的空间位置。

本模块不追求对视频进行无目标的全量语义描述，而强调：

> **需求驱动的视频理解（Task-oriented Video Understanding）。**

其核心链路为：

\[
\text{Intent}
\rightarrow
\text{Required Semantic Events}
\rightarrow
\text{Targeted Video Analysis}
\]

LaTeX：

```latex
\text{Intent}
\rightarrow
\text{Required Semantic Events}
\rightarrow
\text{Targeted Video Analysis}
```

---

# 2. 模块设计目标

视频理解模块需要实现以下目标。

### 2.1 将自然语言语义映射到视频时间轴

例如用户提出：

> 第二次比心以后出现一颗星星。

模块应能够获得：

```text
heart_gesture occurrence #2
start_time = 9.82 s
peak_time  = 10.35 s
end_time   = 10.91 s
```

而不是仅给出：

```text
视频里存在比心动作
```

---

### 2.2 提供剪辑所需的空间信息

例如：

> 爱心不要挡脸。

需要提供：

```text
face_region(t)
```

> 皇冠跟着头移动。

需要提供：

```text
head_trajectory(t)
```

> 图片出现在手指指向的位置。

需要提供：

```text
hand_position
pointing_direction
```

本模块只负责提供空间事实，不负责决定素材最终应该放在哪里。

---

### 2.3 支持需求驱动的增量分析

如果用户第一轮只要求：

```text
检测比心
```

则只分析 `heart_gesture`。

用户随后提出：

```text
转身的时候增加旋转效果
```

系统只增加：

```text
turn_body
```

相关分析，而不重新执行全部视频理解。

因此视频语义状态满足：

\[
S_{t+1}=S_t\cup\Delta S_t
\]

LaTeX：

```latex
S_{t+1}=S_t\cup\Delta S_t
```

其中：

- \(S_t\)：已有 Semantic Video State；
- \(\Delta S_t\)：新增需求产生的新分析结果。

---

### 2.4 同时支持标准事件和开放语义事件

系统既需要稳定识别：

```text
heart_gesture
point_left
point_right
turn_body
```

等高频标准动作，也需要处理：

> 手举到头顶。

> 双手在脸旁边。

> 手从左往右划。

甚至：

> 看起来像在施法的那个动作。

因此事件检测不能完全依赖固定动作分类器。

---

### 2.5 提供可缓存、可追踪、可失效的分析状态

所有视频理解结果应明确记录：

- 分析来源；
- 使用模型或规则；
- 模型版本；
- 数据依赖；
- 查询状态；
- 结果置信度；
- 是否仍然有效。

使视频理解从单次模型推理升级为一个可长期维护的视频语义状态系统。

---

# 3. 模块边界

## 3.1 本模块负责

视频理解模块负责：

- 视频基础元数据提取；
- 时间坐标统一；
- 人物检测和跟踪；
- 人脸检测和跟踪；
- 人体关键点分析；
- 手部位置和手部关键点分析；
- 手势事件检测；
- 身体动作检测；
- Pose Condition 检测；
- 开放语义事件检测；
- 视频结构事件分析；
- 音乐节拍、BPM 和重拍分析；
- Semantic Event 时间定位；
- Semantic Event 空间定位；
- 事件聚合和去重；
- Semantic Timeline 管理；
- 视频语义状态缓存；
- 增量视频分析；
- 分析结果失效管理；
- 为 Planner 构建精简 Semantic View。

---

## 3.2 本模块不负责

本模块不负责：

- 理解用户最终剪辑意图；
- 决定采用何种视觉风格；
- 选择具体背景；
- 搜索贴纸；
- 搜索音乐；
- 决定素材大小；
- 决定最终素材位置；
- 决定动画效果；
- 生成 Editing DSL；
- 操作剪映；
- 维护最终 Editing Timeline。

尤其需要明确：

> Video Understanding 描述“原视频中发生了什么”。

而：

> Editing Planner 决定“利用这些信息应该怎么剪”。

---

# 4. 核心设计原则

## 4.1 需求驱动，而不是全量理解

模块输入来自 Intent Parser：

```text
required_video_queries
```

例如：

```json
{
  "type": "event_detection",
  "event": "heart_gesture",
  "required_occurrence": "all"
}
```

Video Understanding 只执行完成当前需求所必须的分析。

不默认执行：

```text
detect all gestures
detect all body actions
detect all objects
detect all scenes
detect all expressions
```

这能够降低：

- 推理成本；
- 处理时间；
- 无关模型输出；
- 后续状态复杂度。

---

## 4.2 WHAT / WHEN / WHERE 三维结果

视频理解结果统一从三个维度表达：

```text
WHAT
WHEN
WHERE
```

其中：

### WHAT

```text
heart_gesture
turn_body
hand_above_head
ending_pose
downbeat
```

### WHEN

```text
start_time
peak_time
end_time
```

### WHERE

```text
person_bbox
face_bbox
hand_position
event_anchor
direction
trajectory
```

---

## 4.3 Semantic Timeline 与 Editing Timeline 严格分离

Semantic Timeline 描述：

> 原始视频本身什么时候发生什么。

Editing Timeline 描述：

> 最终视频工程中什么时候执行什么编辑。

例如：

```text
source video:
heart_gesture peak = 10.35 s
```

如果前面加入了两秒内容，则最终工程可能为：

```text
project timeline:
heart sticker = 12.35 s
```

因此必须区分：

```text
source_time
project_time
```

两者通过：

\[
t_{\text{project}}
=
M(t_{\text{source}})
\]

进行映射。

LaTeX：

```latex
t_{\text{project}}
=
M(t_{\text{source}})
```

映射 \(M\) 由 Editing Planner / Timeline Manager 维护，而不是本模块维护。

---

# 5. 总体架构

模块内部总体结构定义为：

```text
required_video_queries
          ↓
      Query Router
          ↓
┌────────────────────────────┐
│ Video Preprocessing        │
│                            │
│ Person / Spatial Analysis  │
│                            │
│ Pose / Hand Analysis       │
│                            │
│ Semantic Event Detection   │
│                            │
│ Audio / Rhythm Analysis    │
│                            │
│ Temporal Event Aggregation │
└─────────────┬──────────────┘
              ↓
      Semantic Video State
              ↓
     Semantic View Builder
              ↓
       Editing Planner
```

内部进一步划分为六类核心能力：

```text
Video Understanding
├── Video Preprocessing
├── Person & Spatial Understanding
├── Pose / Hand Representation
├── Semantic Event Detection
├── Audio & Rhythm Understanding
└── Semantic Timeline / State Management
```

---

# 6. Video Preprocessing

Video Preprocessing 负责将输入视频转换为统一、稳定的分析对象。

第一阶段至少提取：

```text
duration
fps
width
height
aspect_ratio
codec
rotation
audio_presence
audio_sample_rate
```

例如：

```json
{
  "video_id": "video_001",
  "duration": 14.82,
  "fps": 30.0,
  "resolution": [1080, 1920],
  "aspect_ratio": "9:16",
  "has_audio": true
}
```

---

## 6.1 时间坐标

上层系统统一使用秒级时间：

```text
timestamp
```

而不是直接依赖帧号。

帧号和时间之间满足：

\[
t_i=\frac{i}{f}
\]

LaTeX：

```latex
t_i=\frac{i}{f}
```

其中：

- \(i\)：帧索引；
- \(f\)：帧率。

底层算法可以同时保存：

```text
frame_index
timestamp
```

但对外接口优先使用秒。

---

## 6.2 时间预处理要求

第一阶段需要正确处理：

- 视频旋转；
- 可变帧率；
- 音视频时间同步；
- 视频抽帧；
- 分析分辨率缩放。

需要保证所有分析结果最终仍可准确映射回原始视频时间轴。

---

# 7. 坐标系统

所有 Video Understanding 产生的空间信息统一绑定：

```text
source_video_normalized
```

坐标系定义为：

```json
{
  "space": "source_video_normalized",
  "origin": "top_left",
  "x_direction": "right",
  "y_direction": "down",
  "range": [0, 1]
}
```

即：

\[
x,y\in[0,1]
\]

LaTeX：

```latex
x,y\in[0,1]
```

Bounding Box 统一表示：

\[
(x_1,y_1,x_2,y_2)
\]

LaTeX：

```latex
(x_1,y_1,x_2,y_2)
```

这样分析结果不绑定 1080p、4K 等具体像素分辨率。

---

# 8. Person & Spatial Understanding

人物是手势舞视频中的核心空间参照。

即使当前用户没有显式提出空间需求，也建议默认执行轻量基础人物分析。

第一阶段默认维护：

```text
person_bbox(t)
face_bbox(t)
body_center(t)
head_center(t)
left_wrist(t)
right_wrist(t)
```

例如：

```json
{
  "timestamp": 10.35,
  "person": {
    "bbox": [0.28, 0.10, 0.74, 0.93],
    "center": [0.51, 0.52]
  },
  "face": {
    "bbox": [0.43, 0.12, 0.59, 0.27],
    "center": [0.51, 0.19]
  }
}
```

---

# 9. Spatial Primitive

第一阶段所有空间信息统一抽象为四类 Spatial Primitive：

```text
SpatialPrimitive
├── Region
├── Point
├── Direction
└── Trajectory
```

---

## 9.1 Region

Region 用于描述需要占据、引用或避让的区域。

包括：

```text
person_bbox
face_bbox
head_region
hand_bbox
body_region
```

例如：

```json
{
  "type": "region",
  "bbox": [0.42, 0.12, 0.59, 0.27]
}
```

未来可以扩展到：

```text
segmentation_mask
```

---

## 9.2 Point

Point 用于素材锚定和跟随。

例如：

```text
body_center
face_center
head_center
left_hand_center
right_hand_center
gesture_center
event_anchor
```

结构：

```json
{
  "type": "point",
  "position": [0.52, 0.35]
}
```

---

## 9.3 Direction

Direction 用于描述：

```text
point left
point right
swipe direction
movement direction
```

连续方向使用归一化二维向量：

\[
\mathbf{d}=(d_x,d_y)
\]

且：

\[
\|\mathbf{d}\|_2=1
\]

LaTeX：

```latex
\mathbf{d}=(d_x,d_y)
```

```latex
\|\mathbf{d}\|_2=1
```

同时提供离散方向标签：

```text
left
right
up
down
upper_left
upper_right
lower_left
lower_right
```

例如：

```json
{
  "direction_vector": [0.98, 0.18],
  "direction_label": "right"
}
```

---

## 9.4 Trajectory

Trajectory 用于跟手、跟头、跟人物移动等持续性需求。

定义：

\[
T=
\{
(t_i,x_i,y_i)
\}_{i=1}^{N}
\]

LaTeX：

```latex
T=
\{
(t_i,x_i,y_i)
\}_{i=1}^{N}
```

由于关键点模型会存在抖动，不能将原始轨迹直接转换成剪辑关键帧。

因此应提供：

\[
\hat{T}
=
\operatorname{Smooth}(T)
\]

LaTeX：

```latex
\hat{T}
=
\operatorname{Smooth}(T)
```

对上层优先暴露：

```text
smoothed_trajectory
```

而不是原始轨迹。

---

# 10. Dense Spatial Tracks

底层高频空间数据统一保存为 Dense Spatial Tracks。

包括：

```text
Person Track
Face Track
Body Pose Track
Hand Track
Optional Hand Landmark Track
```

例如某一时刻：

```json
{
  "timestamp": 10.35,

  "person": {
    "bbox": [0.27, 0.08, 0.75, 0.95]
  },

  "face": {
    "bbox": [0.43, 0.11, 0.59, 0.27]
  },

  "pose": {
    "left_shoulder": [0.42, 0.31],
    "right_shoulder": [0.60, 0.31],
    "left_wrist": [0.46, 0.38],
    "right_wrist": [0.55, 0.38]
  }
}
```

这些高密度数据主要供算法读取，不直接提供给 LLM。

---

# 11. 基础空间分析与按需空间分析

空间能力分成两层。

## Base Spatial Analysis

默认分析：

```text
person bbox
face bbox
body pose
head center
left wrist
right wrist
```

这些信息：

- 计算成本相对较低；
- 后续复用率高；
- 对大多数手势舞编辑都有帮助。

---

## On-demand Spatial Analysis

按用户需求启用：

```text
dense hand landmarks
finger direction
precise hand bbox
person segmentation mask
custom object tracking
```

例如：

> 手指指到哪里，图片就从哪里出现。

才需要更精细的：

```text
finger_tip
finger_direction
```

---

# 12. Pose / Hand Representation

Pose / Hand Representation 是 Semantic Event Detection 的底层基础表示。

其目的不是直接给出：

```text
heart_gesture
```

而是提供：

```text
head
shoulders
elbows
wrists
hips
knees
left_hand
right_hand
```

等可计算的几何状态。

这样同一套底层表示既可以服务标准动作分类，也可以处理开放 Pose Condition。

例如：

> 手举到头顶。

若采用左上角为坐标原点，则可转换为：

\[
y_{\text{hand}}
<
y_{\text{head}}-\delta
\]

LaTeX：

```latex
y_{\text{hand}}
<
y_{\text{head}}-\delta
```

并结合持续时间：

\[
t_{\text{end}}-t_{\text{start}}>\tau
\]

LaTeX：

```latex
t_{\text{end}}-t_{\text{start}}>\tau
```

判断是否构成真正事件。

---

# 13. Semantic Event Detection 总体策略

Semantic Event Detection 采用三级体系：

\[
\text{Dedicated Detector}
\rightarrow
\text{Pose / Motion Rule}
\rightarrow
\text{Open Semantic Model}
\]

LaTeX：

```latex
\text{Dedicated Detector}
\rightarrow
\text{Pose / Motion Rule}
\rightarrow
\text{Open Semantic Model}
```

其目标是在：

- 稳定性；
- 可解释性；
- 计算成本；
- 开放性；

之间取得平衡。

---

# 14. Detection Strategy Router

所有 `required_video_queries` 先经过 Detection Strategy Router。

路由逻辑为：

```text
Query
 ↓
Is standard event?
 ├── Yes
 │     ↓
 │ Dedicated Detector
 │
 └── No
       ↓
Can it be represented by pose/motion geometry?
 ├── Yes
 │     ↓
 │ Pose / Motion Rule
 │
 └── No
       ↓
 Open Semantic Detector
```

这套路由原则作为第一阶段固定设计。

---

# 15. Dedicated Event Detector

专用检测器处理高频、固定、值得长期优化的标准动作。

第一阶段 Standard Event Library 建议包括：

### Gesture

```text
heart_gesture
point_left
point_right
wave
hands_open
hands_close
thumb_up
victory
```

### Body Action

```text
turn_body
move_left
move_right
jump
squat
stand_up
ending_pose
```

MVP 实际实现时不要求一次全部上线，可优先选择约 10–15 个事件。

---

# 16. Frame / Window Recognition 与 Temporal Localization

专用检测器不能直接把逐帧分类结果暴露给 Planner。

例如：

```text
4.03  heart 0.62
4.07  heart 0.79
4.10  heart 0.91
4.13  heart 0.96
...
```

这些属于同一次动作。

因此标准流程为：

```text
Frame / Window Recognition
        ↓
Raw Confidence Sequence
        ↓
Temporal Event Aggregation
        ↓
Semantic Event
```

设事件 \(e\) 在时刻 \(t\) 的置信度为：

\[
p_e(t)=P(e\mid V_t)
\]

LaTeX：

```latex
p_e(t)=P(e\mid V_t)
```

通过时间聚合器得到：

\[
E_e=\mathcal{A}(p_e(t))
\]

LaTeX：

```latex
E_e=\mathcal{A}(p_e(t))
```

---

# 17. Semantic Event 时间定义

每一个事件原则上输出：

\[
E=(t_s,t_p,t_e,c)
\]

LaTeX：

```latex
E=(t_s,t_p,t_e,c)
```

其中：

- \(t_s\)：start_time；
- \(t_p\)：peak_time；
- \(t_e\)：end_time；
- \(c\)：confidence。

例如：

```json
{
  "event_type": "gesture",
  "canonical": "heart_gesture",

  "temporal": {
    "start_time": 9.82,
    "peak_time": 10.35,
    "end_time": 10.91
  }
}
```

---

# 18. 时间语义与 Intent 的对应

Intent Parser 中的时间关系与 Event 时间边界对应如下：

```text
at_event
→ peak_time

during_event
→ [start_time, end_time]

before_event
→ start_time 作为时间锚点

after_event
→ end_time 作为时间锚点
```

因此本模块必须尽量输出时间区间，而不是单一 timestamp。

---

# 19. Temporal Event Aggregator

Temporal Event Aggregator 统一处理：

```text
temporal smoothing
thresholding
candidate segmentation
gap merging
minimum duration filtering
duplicate suppression
peak localization
```

例如平滑：

\[
\bar p_t=
\frac{1}{2k+1}
\sum_{i=-k}^{k}p_{t+i}
\]

LaTeX：

```latex
\bar p_t=
\frac{1}{2k+1}
\sum_{i=-k}^{k}p_{t+i}
```

事件候选条件：

\[
\bar p_t>\theta
\]

LaTeX：

```latex
\bar p_t>\theta
```

标准概率型事件的 Peak 可以定义：

\[
t_p=
\arg\max_t p_t
\]

LaTeX：

```latex
t_p=
\arg\max_t p_t
```

---

# 20. 不同事件允许不同 Temporal Configuration

不能所有事件统一使用相同的：

```text
min_duration
merge_gap
threshold
```

例如：

```text
heart_gesture
    min_duration = 0.15s
    merge_gap = 0.20s

wave
    min_duration = 0.30s
    merge_gap = 0.10s
```

因此这些参数应属于 Event Registry 中的事件级配置。

---

# 21. Pose / Motion Rule Engine

第二层检测机制处理可以通过几何关系和运动轨迹表达的开放动作。

例如：

> 手在脸旁边。

定义：

\[
d(\mathbf p_{\text{hand}},\mathbf p_{\text{face}})
<
\delta
\]

LaTeX：

```latex
d(\mathbf p_{\text{hand}},\mathbf p_{\text{face}})
<
\delta
```

为了避免人物尺度变化，应使用归一化距离，例如：

\[
d_{\text{norm}}
=
\frac{
\|\mathbf p_{\text{hand}}-\mathbf p_{\text{face}}\|_2
}{
w_{\text{person}}
}
\]

LaTeX：

```latex
d_{\text{norm}}
=
\frac{
\|\mathbf p_{\text{hand}}-\mathbf p_{\text{face}}\|_2
}{
w_{\text{person}}
}
```

---

# 22. Motion Rule

例如：

> 手从左边划到右边。

可计算：

\[
\Delta x
=
x_{\text{hand}}(t_2)
-
x_{\text{hand}}(t_1)
\]

LaTeX：

```latex
\Delta x
=
x_{\text{hand}}(t_2)
-
x_{\text{hand}}(t_1)
```

若：

\[
\Delta x>\delta_x
\]

同时：

\[
v_x=
\frac{\Delta x}{t_2-t_1}
>
v_{\min}
\]

LaTeX：

```latex
\Delta x>\delta_x
```

```latex
v_x=
\frac{\Delta x}{t_2-t_1}
>
v_{\min}
```

则可以构成：

```text
hand_swipe_left_to_right
```

事件候选。

---

# 23. Condition Compiler

用户自然语言中的开放姿态条件不应直接转换成模型调用。

建议采用：

```text
Natural-language Event
        ↓
Structured Pose / Motion Condition
        ↓
Condition Compiler
        ↓
Executable Predicate
        ↓
Pose / Motion Track Evaluation
        ↓
Semantic Event
```

例如：

> 手举到头顶的时候。

Intent Parser 输出：

```json
{
  "type": "pose_condition",
  "subject": "hand",
  "relation": "above",
  "reference": "head"
}
```

Condition Compiler 再将其转换为可执行几何条件。

---

# 24. Open Semantic Detector

对于无法通过标准动作或几何规则表达的需求，使用 Open Semantic Detector。

例如：

> 看起来最有力量的那个动作。

> 像在挥舞魔法棒的动作。

> 最可爱的那个动作。

不建议采用：

```text
every frame
→ VLM
```

的高成本方法。

推荐两阶段架构：

\[
C=\operatorname{Proposal}(V)
\]

\[
E=\operatorname{SemanticVerify}(C,q)
\]

LaTeX：

```latex
C=\operatorname{Proposal}(V)
```

```latex
E=\operatorname{SemanticVerify}(C,q)
```

其中：

- \(C\)：低成本方法产生的候选动作区间；
- \(q\)：用户自然语言语义描述；
- \(E\)：开放语义模型确认后的事件。

流程：

```text
Cheap Candidate Proposal
        ↓
Candidate Clips
        ↓
Open Semantic Verification
        ↓
Semantic Event
```

---

# 25. Hybrid Event Detection

一个标准事件允许同时结合多条证据。

例如 `heart_gesture` 可以结合：

```text
hand gesture classifier
+
two-hand geometry
+
temporal consistency
```

总体置信度可抽象为：

\[
c=
w_1c_{\text{gesture}}
+
w_2c_{\text{geometry}}
+
w_3c_{\text{temporal}}
\]

LaTeX：

```latex
c=
w_1c_{\text{gesture}}
+
w_2c_{\text{geometry}}
+
w_3c_{\text{temporal}}
```

第一阶段可以使用简单规则或加权机制，不要求立即训练复杂融合模型。

---

# 26. Semantic Event Registry

系统需要维护标准事件能力注册表：

```text
Semantic Event Registry
```

用于描述：

> 系统认识哪些标准事件，以及应该如何检测它们。

例如：

```json
{
  "canonical": "heart_gesture",
  "event_type": "gesture",
  "supported": true,

  "strategy": "hybrid",

  "detectors": [
    "hand_gesture_detector",
    "pose_geometry_validator"
  ],

  "temporal_config": {
    "min_duration": 0.15,
    "merge_gap": 0.20
  }
}
```

Registry 属于能力配置，而不是视频分析结果。

---

# 27. Event-Level Spatial Snapshot

每个 Semantic Event 应能够关联事件发生时的空间快照。

例如：

```json
{
  "spatial_id": "spatial_snapshot_heart_02",
  "timestamp": 10.35,

  "person_bbox": [0.28, 0.09, 0.74, 0.94],
  "face_bbox": [0.43, 0.12, 0.59, 0.27],

  "left_hand": [0.47, 0.38],
  "right_hand": [0.55, 0.38],

  "event_anchor": [0.51, 0.38]
}
```

默认可以使用 `peak_time` 对应的空间状态。

---

# 28. Event Anchor

对于双手比心等不存在单一主体点的事件，应定义事件锚点。

例如：

\[
\mathbf p_{\text{event}}
=
\frac{
\mathbf p_{\text{left hand}}
+
\mathbf p_{\text{right hand}}
}{2}
\]

LaTeX：

```latex
\mathbf p_{\text{event}}
=
\frac{
\mathbf p_{\text{left hand}}
+
\mathbf p_{\text{right hand}}
}{2}
```

后续 Planner 可以基于：

```text
event_anchor
```

进行素材位置规划。

---

# 29. Protected Region

Video Understanding 可以为人脸等重要区域输出：

```text
protected_region
```

但不直接决定素材位置。

例如：

\[
R_{\text{protected}}
=
\operatorname{Expand}(R_{\text{face}},m)
\]

LaTeX：

```latex
R_{\text{protected}}
=
\operatorname{Expand}(R_{\text{face}},m)
```

其中 \(m\) 为推荐安全边距。

Planner 再处理：

\[
R_{\text{asset}}
\cap
R_{\text{protected}}
\approx
\varnothing
\]

LaTeX：

```latex
R_{\text{asset}}
\cap
R_{\text{protected}}
\approx
\varnothing
```

---

# 30. “不要挡脸”的模块职责划分

Intent Parser：

```text
constraint:
avoid_overlap(face)
```

Video Understanding：

```text
face_region(t)
```

Editing Planner：

```text
choose safe placement
```

Tool Executor：

```text
apply final position / keyframes
```

该边界作为固定设计。

---

# 31. Pointing Direction

第一阶段支持简单指向能力：

```text
point_left
point_right
```

并提供：

```text
hand_position
direction_label
direction_vector
```

未来如果需要：

> 手指指到哪里，图片就出现在哪里。

可进一步建立射线：

\[
\mathbf r(\lambda)
=
\mathbf p
+
\lambda\mathbf d
\]

LaTeX：

```latex
\mathbf r(\lambda)
=
\mathbf p
+
\lambda\mathbf d
```

其中：

- \(\mathbf p\)：指尖位置；
- \(\mathbf d\)：手指方向。

第一阶段只要求离散方向和侧边区域定位即可。

---

# 32. Person Segmentation

当用户提出：

> 背景换成海边。

后续执行需要人物 Mask：

\[
M_{\text{person}}(x,y,t)
\]

LaTeX：

```latex
M_{\text{person}}(x,y,t)
```

人物分割结果属于：

```text
Spatial Asset
```

而不是直接塞入 Semantic Video State。

例如：

```json
{
  "asset_id": "person_mask_track_01",
  "type": "segmentation_track",
  "source_video": "video_001"
}
```

Semantic State 仅保存引用。

---

# 33. Audio & Rhythm Understanding

音频理解和视觉理解统一属于 Video Understanding。

第一阶段至少支持：

```text
BPM
beat
downbeat
onset
```

后续可扩展：

```text
chorus_start
chorus_end
music_section_change
```

例如：

```json
{
  "audio_id": "audio_original_01",
  "bpm": 126.4,

  "beats": [
    0.48,
    0.95,
    1.43,
    1.90
  ],

  "downbeats": [
    0.48,
    2.38,
    4.28
  ]
}
```

---

# 34. Audio Event 与 Visual Event 使用统一协议

例如：

```text
gesture_heart_01
body_turn_01
audio_downbeat_01
audio_downbeat_02
```

都属于：

```text
SemanticEvent
```

这样 Planner 可以统一处理：

> 比心的时候。

和：

> 音乐重拍的时候。

---

# 35. 外部音乐也复用同一 Audio Analyzer

Audio Understanding 不绑定原始视频音轨。

提供抽象能力：

```text
analyze_audio(asset_id)
```

既可以用于：

```text
original video audio
```

也可以用于：

```text
retrieved BGM
generated music
user supplied audio
```

当用户后续换音乐时，不需要重新设计新的分析协议。

---

# 36. Semantic Video State

模块最终统一维护：

```text
SemanticVideoState
├── video
├── semantic_events
├── spatial_state
├── audio_state
├── structural_state
├── analysis_registry
└── semantic_view
```

其中 `semantic_view` 是根据状态动态生成的上层 View，不是独立事实源。

---

# 37. Semantic Event Schema

建议统一定义：

```json
{
  "event_uid": "evt_a82f39",
  "display_id": "gesture_heart_02",

  "event_type": "gesture",
  "canonical": "heart_gesture",

  "occurrence_index": 2,

  "source_query_ids": [
    "query_heart_001"
  ],

  "temporal": {
    "start_time": 9.82,
    "peak_time": 10.35,
    "end_time": 10.91
  },

  "spatial_ref": "spatial_snapshot_heart_02",

  "confidence": {
    "overall": 0.94,
    "status": "confirmed"
  },

  "detector": {
    "strategy": "hybrid",
    "version": "heart_detector_v1"
  }
}
```

---

# 38. event_uid 与 display_id

事件使用两套 ID。

## event_uid

系统内部稳定且不可变的唯一标识。

例如：

```text
evt_a82f39
```

Timeline Object 和其他持久化对象应优先引用它。

---

## display_id

便于人类和 LLM 理解的时序名称：

```text
gesture_heart_02
```

当事件集合重新排序时：

```text
occurrence_index
display_id
```

可以变化，但 `event_uid` 尽量保持稳定。

这样可以避免重新检测导致已有编辑对象引用错位。

---

# 39. Structural Video State

视频结构信息单独维护。

例如：

```json
{
  "video_start": 0.0,
  "video_end": 14.82,

  "first_action": {
    "event_ref": "evt_first_action",
    "confidence": 0.91
  },

  "last_action": {
    "event_ref": "evt_last_action",
    "confidence": 0.93
  }
}
```

其中：

```text
video_start
video_end
```

为确定性结构信息。

而：

```text
first_action
last_action
```

可能依赖动作分析。

---

# 40. Analysis Registry

所有 `required_video_queries` 都需要保存分析执行记录。

例如：

```json
{
  "query_id": "query_heart_001",

  "query": {
    "type": "event_detection",
    "canonical": "heart_gesture"
  },

  "status": "completed",

  "result_refs": [
    "evt_a",
    "evt_b"
  ],

  "strategy": "dedicated_detector",

  "model_version": "heart_detector_v1",

  "source_video_version": "video_001_v1",

  "dependencies": [
    "pose_track_v1",
    "hand_track_v1"
  ]
}
```

---

# 41. Analysis Query 状态

第一版统一定义：

```text
pending
running
completed
not_found
low_confidence
failed
invalidated
```

其中：

### completed

分析正常完成并获得可靠结果。

### not_found

分析过程正常，但视频中不存在目标事件。

### low_confidence

存在候选结果，但可信度不足。

### failed

模型、输入或依赖发生技术错误。

### invalidated

历史分析结果因相关依赖变化而失效。

---

# 42. not_found 与 failed 必须区分

例如：

```json
{
  "status": "not_found",
  "events": []
}
```

表示：

> 系统正常分析过，但视频中没有找到比心。

而：

```json
{
  "status": "failed",
  "error": "hand landmarks unavailable"
}
```

表示：

> 视频理解技术执行失败。

后续 Agent Controller 对两者应采用不同策略。

---

# 43. Confidence

Semantic Event 不仅保存总体置信度，还应允许保存来源。

例如：

```json
{
  "confidence": {
    "overall": 0.91,

    "status": "confirmed",

    "sources": {
      "gesture_model": 0.94,
      "pose_validator": 0.87
    }
  }
}
```

对于开放语义事件：

```json
{
  "sources": {
    "motion_proposal": 0.82,
    "vlm_verification": 0.73
  }
}
```

以支持后续调试和重新验证。

---

# 44. Low-Confidence Event

低置信度事件不应简单直接删除。

建议支持：

```text
confirmed
candidate
uncertain
```

例如：

```text
heart_gesture_01 = confirmed
heart_gesture_02 = confirmed
heart_gesture_03 = uncertain
```

上层 Agent 可根据：

- 用户约束强度；
- 修改风险；
- 是否容易撤销；

决定是否继续验证。

---

# 45. Query Cache

Video Understanding 必须支持结果缓存。

例如第一次执行：

```text
detect all heart gestures
```

已经获得：

```text
heart_gesture_01
heart_gesture_02
heart_gesture_03
```

后续用户提出：

> 第二次比心后……

不应重新跑检测。

---

# 46. Query Coverage

缓存应基于标准化查询，而不是用户原始字符串。

可以定义：

\[
Q_a\supseteq Q_b
\]

LaTeX：

```latex
Q_a\supseteq Q_b
```

表示已有查询 \(Q_a\) 的分析结果足以回答 \(Q_b\)。

例如：

```text
detect all heart gestures
⊇
detect second heart gesture
```

因此：

```text
occurrence = index(2)
```

只需要从现有事件列表中过滤。

---

# 47. 分析结果失效管理

至少定义四类失效来源。

## 47.1 源视频被替换

例如：

```text
video_001
→ video_002
```

原有分析结果原则上失效。

---

## 47.2 编辑工程发生裁剪

如果只是用户在 Editing Timeline 中提出：

> 删除开头两秒。

Semantic Video State 不失效。

仍保留：

```text
heart_gesture peak = source 10.35s
```

仅由 Timeline Mapping 映射到新的 project time。

这是第一阶段推荐原则。

---

## 47.3 模型版本更新

例如：

```text
heart_detector_v1
→ heart_detector_v2
```

旧结果可以标记：

```text
stale
```

而不一定立即删除。

未来可扩展 Cache Validity：

```text
valid
stale
invalidated
```

---

## 47.4 依赖分析结果变化

例如：

```text
hand_above_head
```

依赖：

```text
pose_track_v1
```

如果 Pose Track 被重新生成，则相关事件应失效或重新验证。

因此 Analysis Registry 必须保存：

```text
dependencies
```

---

# 48. Dense Data 与 Semantic State 分离

高密度分析数据不直接写入 Semantic Video State。

建议项目组织：

```text
Project/
├── semantic_video_state.json
│
├── analysis_artifacts/
│   ├── person_track.parquet
│   ├── pose_track.parquet
│   ├── hand_track.parquet
│   ├── face_track.parquet
│   └── segmentation/
│
└── analysis_cache/
```

Semantic Video State 只保存：

```text
artifact_id
```

和必要摘要。

---

# 49. Semantic View

LLM 和 Planner 不直接读取全部 Dense Analysis Data。

应通过：

```text
SemanticViewBuilder
```

根据当前需求动态生成精简语义视图。

可以抽象为：

\[
V_{\text{semantic}}
=
f(S,Q)
\]

LaTeX：

```latex
V_{\text{semantic}}
=
f(S,Q)
```

其中：

- \(S\)：完整 Semantic Video State；
- \(Q\)：当前用户需求；
- \(V_{\text{semantic}}\)：与当前任务相关的精简视频语义。

例如：

```text
Video:
- duration: 14.82s
- vertical 9:16
- single person

Relevant Events:
- heart_gesture_01: 4.01–4.71s, peak 4.23s
- heart_gesture_02: 9.82–10.91s, peak 10.35s

heart_gesture_02:
- hands near upper-center
- face directly above gesture
- person mostly centered
```

无需提供数百帧 Pose 信息。

---

# 50. Semantic Video State 版本

状态应支持版本号。

例如：

```json
{
  "state_id": "semantic_state_video001",
  "version": 5
}
```

状态可按分析过程逐步演化：

```text
v1
metadata

v2
+ person state

v3
+ heart gesture

v4
+ ending pose

v5
+ audio beat
```

Planner 可以记录：

```text
based_on_semantic_state_version = 5
```

以便后续判断是否需要 Partial Replanning。

---

# 51. Analysis History

系统应记录分析历史。

例如：

```json
{
  "analysis_id": "analysis_017",
  "query_id": "query_heart_001",
  "strategy": "hybrid",
  "outputs": [
    "evt_a",
    "evt_b"
  ]
}
```

用于：

- Debug；
- 算法评估；
- 版本对比；
- 缓存追踪；
- Event 来源追踪。

---

# 52. 对外接口

第一阶段 Video Understanding 可抽象提供以下接口。

### 基础视频分析

```text
analyze_video(video)
```

完成基础 Metadata 和 Base Spatial State。

---

### 视频查询解析

```text
resolve_queries(required_video_queries)
```

执行需求驱动的视频分析。

---

### 事件读取

```text
get_semantic_events(query)
```

获得已有语义事件。

---

### 空间快照

```text
get_spatial_snapshot(event_uid)
```

获得事件对应空间信息。

---

### 空间轨迹

```text
get_spatial_track(target, time_range)
```

用于：

```text
跟头
跟手
跟人物
```

等需求。

---

### 音频分析

```text
analyze_audio(asset_id)
```

用于原视频音频和后续音乐素材。

---

### Semantic View

```text
build_semantic_view(context)
```

为 Planner / LLM 构建当前任务相关语义。

---

### 失效接口

```text
invalidate(dependency)
```

处理视频、模型或依赖状态变化。

---

# 53. 与 Intent Parser 的接口

Intent Parser 输出：

```text
required_video_queries
```

例如：

```json
{
  "type": "event_detection",
  "event": "heart_gesture",
  "required_occurrence": "all"
}
```

Video Understanding 返回：

```text
Semantic Events
Spatial State
Query Status
Confidence
```

---

# 54. 与 Editing Planner 的接口

Planner 接收：

```text
Current Effective Intent
+
Semantic View
+
Semantic Event References
+
Spatial Information
+
Tool Capabilities
```

Video Understanding 只提供事实。

例如：

```text
heart_gesture_02
peak = 10.35s
event_anchor = [0.51, 0.38]
face_region = [...]
```

Planner 决定：

```text
使用哪个素材
出现多久
放在哪里
采用什么动画
```

---

# 55. 典型链路：第二次比心之后出现星星

用户：

> 第二次比心之后出现一颗星星。

Intent Parser：

```text
event = heart_gesture
occurrence = index(2)
relation = after_event
```

Query Router 检查：

```text
heart_gesture/all
```

是否已有缓存。

如果已有：

```text
Cache Hit
```

Semantic Timeline 按 source time 排序：

```text
heart_gesture_01
heart_gesture_02
heart_gesture_03
```

解析：

```text
occurrence_index = 2
→ event_uid = evt_abc
```

Video Understanding 返回：

```text
end_time = 10.91s
```

Planner 根据：

```text
after_event
```

选择：

```text
source anchor = 10.91s
```

再由 Timeline Mapping 转换成 project time。

---

# 56. 典型链路：爱心不要挡脸

Intent Parser：

```text
event:
heart_gesture

constraint:
avoid_overlap(face)
```

Video Understanding：

```text
heart_gesture event
+
face_region
+
event_anchor
```

Planner：

```text
choose asset size
choose asset position
satisfy avoid-overlap constraint
```

Executor：

```text
apply timeline object
```

---

# 57. 典型链路：皇冠一直跟着头

Intent：

```text
asset = crown
tracking_target = head
```

Video Understanding：

```text
head_trajectory(t)
```

并提供：

```text
smoothed_trajectory
```

Planner：

```text
trajectory + relative offset
```

最终 Executor 转换成关键帧或跟踪效果。

---

# 58. 典型链路：背景替换

用户：

> 背景换成动漫海滩。

Video Understanding 需要提供：

```text
person segmentation asset
```

例如：

```text
person_mask_track_01
```

Asset Retrieval 提供：

```text
anime beach background
```

Planner 决定合成关系。

Executor 完成背景替换。

---

# 59. MVP 范围

第一阶段 Video Understanding 不追求通用视频理解。

## 基础视频能力

至少支持：

```text
metadata extraction
time normalization
person detection
person tracking
face detection
```

---

## Pose 能力

支持：

```text
body keypoints
head center
left/right wrist
basic hand position
```

---

## Gesture Event

优先实现：

```text
heart_gesture
point_left
point_right
wave
hands_open
hands_close
```

---

## Body Action

优先支持：

```text
turn_body
move_left
move_right
ending_pose
```

---

## Pose Condition

支持基础关系：

```text
hand above head
hand near face
hands crossed
left/right spatial relationship
```

---

## Audio

支持：

```text
BPM
beat
downbeat
```

---

## Temporal Localization

所有核心事件尽量输出：

```text
start_time
peak_time
end_time
```

---

## Spatial Information

支持：

```text
person_bbox
face_bbox
head_center
left_hand_position
right_hand_position
event_anchor
```

按需支持：

```text
hand landmarks
finger direction
person segmentation
```

---

## 状态管理

支持：

```text
semantic event cache
analysis registry
incremental query
semantic video state
semantic view
```

---

# 60. 第一阶段暂不重点实现

以下能力在 Schema 和总体架构中可以预留，但不作为 MVP 核心目标：

```text
3D pose
depth estimation
multi-person tracking
multi-character reference
complex facial expression recognition
complex object interaction
full-scene semantic understanding
camera motion reconstruction
3D pointing ray
complex video-language temporal grounding
long-form narrative understanding
```

---

# 61. 模块验收标准

第一阶段 Video Understanding 可以按照以下标准验收：

1. 能正确读取和标准化输入视频；
2. 能建立稳定 source timeline；
3. 能输出人物和人脸基础轨迹；
4. 能输出人体基础关键点；
5. 能检测若干预定义手势事件；
6. 能检测若干基础身体动作；
7. 能处理基础 Pose Condition；
8. 能将连续帧识别结果聚合成独立 Semantic Event；
9. 能正确区分同一动作的多次发生；
10. 能输出事件 start / peak / end；
11. 能输出事件相关空间锚点；
12. 能提供 face/person 等保护区域；
13. 能分析 BPM、beat 和 downbeat；
14. 能缓存已完成的语义查询；
15. 新需求只执行增量分析；
16. 能正确区分 `not_found` 与 `failed`；
17. 能保留分析置信度和来源；
18. Dense Analysis Data 与上层 Semantic View 分离；
19. Semantic Timeline 与 Editing Timeline 不混用；
20. 已有分析结果可以通过稳定 `event_uid` 被后续模块引用。

---

# 62. 模块最终定义

Video Understanding 模块最终承担：

```text
用户需求要求理解什么
        ↓
查询已有视频语义状态
        ↓
已有结果直接复用
        ↓
缺失结果选择合适分析策略
        ↓
Dedicated Detector
/
Pose & Motion Rule
/
Open Semantic Detector
        ↓
事件时间聚合
        ↓
空间状态绑定
        ↓
音频语义绑定
        ↓
Semantic Video State
        ↓
构建当前任务所需 Semantic View
        ↓
交付 Editing Planner
```

模块最终形成整个视频剪辑 Agent 中统一的“视频事实层”。

其核心职责不是替用户决定视频应该怎么剪，而是稳定回答：

> 原视频里面到底发生了什么。

> 这些事情什么时候发生。

> 发生时人物、手、脸和动作在哪里。

> 当前系统已经分析过什么。

> 后续新需求还需要补充分析什么。

这些信息将作为下一模块 Editing Planner 制定剪辑方案的事实依据。