# 自然语言驱动视频剪辑 Agent  
## 模块三：剪辑规划模块工程设计文档

## 1. 模块定位

剪辑规划模块（Editing Planner）负责将模块一生成的 `EditingIntent` 与模块二生成的 `SemanticVideoState / SemanticView` 转换为结构化、可验证、软件无关的剪辑计划。

本模块不负责理解用户原始自然语言，也不负责重新分析视频内容，其核心职责是：

> 根据用户已经确认的剪辑需求和视频中已经识别出的事实，决定具体应该怎样剪。

总体转换关系为：

\[
P=f(I,S,M,C)
\]

其中：

- \(I\)：Editing Intent；
- \(S\)：Semantic Video State / Semantic View；
- \(M\)：Mobility / Accessibility Profile；
- \(C\)：Tool Capability Profile；
- \(P\)：Editing Plan。

LaTeX：

```latex
P=f(I,S,M,C)
```

本模块第一阶段重点面向：

> **坐姿、轮椅使用者为主体的单人手势舞短视频。**

因此坐姿/轮椅场景不是通用 Planner 的兼容模式，而是第一阶段一级规划场景。

---

# 2. 第一阶段产品目标

第一阶段不追求通用自动视频剪辑，而聚焦于：

```text
单人
+
短视频
+
固定或轻微移动机位
+
以手势 / 上半身动作作为主要表达
+
坐姿 / 轮椅用户优先
```

重点完成：

- 手势触发贴纸；
- 手势触发图片；
- 手势触发文字；
- 手势触发视觉特效；
- 手部 / 头部跟随；
- 人脸与活跃手势区域避让；
- 背景替换；
- 音乐添加 / 替换；
- 音乐节拍与手势联合卡点；
- 最后动作定格；
- 低幅度动作视觉增强；
- 多轮局部修改所需的 Plan 追踪。

第一阶段暂不追求：

```text
复杂叙事剪辑
多机位
多人关系
电影级调色
复杂三维特效
3D 姿态
复杂镜头语言
自动剧情理解
长视频自动剪辑
```

---

# 3. 无障碍优先设计原则

本模块不得默认：

```text
动作幅度越大
=
表达越强
=
剪辑重点越高
```

尤其对于轮椅用户、低幅度动作用户和单侧上肢用户，视觉表达强度不能直接由物理位移大小决定。

第一阶段遵循：

\[
\text{Visual Energy}
\neq
\text{Physical Motion Magnitude}
\]

LaTeX：

```latex
\text{Visual Energy}
\neq
\text{Physical Motion Magnitude}
```

低幅度动作可以通过：

```text
贴纸反馈
局部发光
轻量缩放
颜色变化
粒子效果
文字反馈
节拍强化
轨迹装饰
```

增强视觉表达，而不是强制模拟更大的身体运动。

---

# 4. 坐姿策略与站姿策略并列

Planner 不采用：

```text
Standing Strategy
    ↓
无法使用
    ↓
Seated Fallback
```

而采用：

```text
General Strategy
├── Seated Strategy
└── Standing Strategy
```

当：

```text
MobilityProfile.posture = seated
```

Planner 直接进入：

```text
Seated Editing Strategy
```

其空间和节奏规划重点为：

```text
face
head
shoulders
upper torso
active hands
gesture region
hand trajectory
```

而不是优先依赖：

```text
jump
squat
leg motion
large whole-body displacement
```

---

# 5. 模块边界

## 5.1 本模块负责

Editing Planner 负责：

- 总体剪辑策略；
- 用户需求展开；
- Semantic Event 实例绑定；
- 时间关系解析；
- 空间布局策略；
- 坐姿/轮椅场景适配；
- 素材需求生成；
- 动画策略选择；
- 逻辑轨道组织；
- 工具能力适配；
- 能力降级；
- 约束校验；
- Accessibility 校验；
- Logical Editing Plan 生成；
- 素材返回后的 Plan Materialization；
- 后续局部重规划接口。

---

## 5.2 本模块不负责

本模块不负责：

- 原始自然语言解析；
- 手势检测；
- 人体检测；
- 视频事件检测；
- 实际素材搜索；
- 素材下载；
- AI 图片生成；
- 剪映 UI 操作；
- FFmpeg 命令执行；
- 视频最终渲染。

---

# 6. 模块总体输入

第一版定义：

```text
EditingPlannerInput
├── editing_intent
├── semantic_view
├── accessibility_profile
├── tool_capabilities
├── existing_plan
└── planner_context
```

其中：

### editing_intent

直接复用模块一：

```text
EditingIntent
```

### semantic_view

直接复用模块二：

```text
SemanticView
```

### accessibility_profile

建议由模块二的 `MobilityProfile` 转换为 Planner 使用的：

```text
AccessibilityPlanningProfile
```

### tool_capabilities

表示当前执行后端支持哪些能力。

### existing_plan

后续局部重规划时使用。

---

# 7. AccessibilityPlanningProfile

建议定义：

```text
AccessibilityPlanningProfile
├── posture
├── active_hands
├── motion_amplitude
├── amplitude_scale
├── tremor
├── mirrored
├── primary_action_region
├── preserve_mobility_device
└── layout_preferences
```

例如：

```json
{
  "posture": "seated",
  "active_hands": ["right"],
  "motion_amplitude": "low",
  "amplitude_scale": 0.62,
  "tremor": false,
  "mirrored": false,
  "primary_action_region": "upper_body",
  "preserve_mobility_device": true,
  "layout_preferences": {
    "protect_face": true,
    "protect_active_hands": true,
    "protect_gesture_region": true
  }
}
```

需要注意：

```text
posture = seated
```

不等于：

```text
available_hands = one hand
```

这些字段必须正交表达。

---

# 8. 与模块二接口的补充要求

模块二的 `SemanticView` 建议补充：

```text
subject_profile
```

至少包含：

```text
posture
available_hands
amplitude
amplitude_scale
tremor
mirrored
inferred
```

例如：

```json
{
  "subject_profile": {
    "posture": "seated",
    "available_hands": ["left", "right"],
    "amplitude": "low",
    "amplitude_scale": 0.7,
    "tremor": false,
    "mirrored": false,
    "inferred": true
  }
}
```

Planner 不应重新从稠密轨迹推断主体可动性状态。

---

# 9. 模块总体流程

第一阶段流程定义为：

```text
EditingIntent
+
SemanticView
+
AccessibilityProfile
        ↓
Input Normalization
        ↓
Accessibility Context Builder
        ↓
Global Strategy Planner
        ↓
Requirement Expander
        ↓
Creative Planner
        ↓
Temporal Resolver
        ↓
Accessibility-aware Spatial Planner
        ↓
Asset Requirement Builder
        ↓
Timeline Composer
        ↓
Capability Resolver
        ↓
Constraint Validator
        ↓
Accessibility Validator
        ↓
LogicalEditingPlan
```

素材模块完成素材获取后：

```text
LogicalEditingPlan
+
AssetBindings
+
Updated Semantic Data
+
Tool Capabilities
        ↓
Plan Materializer
        ↓
ResolvedEditingPlan
```

---

# 10. 两阶段 Plan 设计

第三模块正式采用：

```text
LogicalEditingPlan
        ↓
Asset Retrieval
        ↓
ResolvedEditingPlan
```

## LogicalEditingPlan

表达：

> 想实现什么。

例如：

```text
使用可爱的粉色爱心
出现在比心动作上方
避开脸和手势区域
使用轻量 pop 动画
```

## ResolvedEditingPlan

表达：

> 用当前素材和当前工具具体怎么实现。

例如：

```text
asset_uid = asset_heart_17
project_start = 10.15
project_end = 10.95
position = [0.51, 0.29]
scale = 0.16
animation = pop
```

两者都保持软件无关。

---

# 11. 核心实体：PlanItem

`PlanItem` 是第三模块最重要的数据对象。

它表示：

> 一个完整、原子、可独立修改的剪辑决策。

建议：

```text
PlanItem
├── plan_item_uid
├── plan_key
├── operation
├── source_requirement_ids
├── constraint_refs
├── target
├── temporal_spec
├── spatial_spec
├── asset_request_ref
├── style_spec
├── parameters
├── timeline_effect
├── capability_requirements
├── degradation_policy
├── provenance
└── status
```

---

# 12. PlanItem 稳定标识

内部使用：

```text
plan_item_uid
```

例如：

```text
pln_a82f...
```

不使用简单列表序号作为稳定引用。

同时建议定义：

```text
plan_key
```

其逻辑身份由：

\[
K=
(
\text{Requirement ID},
\text{Event UID},
\text{Operation}
)
\]

构成。

LaTeX：

```latex
K=
(
\text{Requirement ID},
\text{Event UID},
\text{Operation}
)
```

例如：

```text
event_req_01:evt_a82f39:add_overlay
```

用于后续局部重规划和 Plan 对齐。

---

# 13. 第一阶段 Operation 范围

MVP 只重点支持：

```text
replace_background
add_overlay
add_text
add_effect
add_music
replace_music
add_sound_effect

freeze

scale_adjust
position_adjust
volume_adjust

track_overlay
```

保留但不重点支持：

```text
trim
split
remove_segment
speed_adjust
```

第一阶段不重点实现复杂结构剪辑，以减少 Timeline Mapping 复杂度。

---

# 14. Requirement Expansion

该步骤使用确定性代码。

例如：

```text
event_req_01

event:
heart_gesture

occurrence:
all
```

Semantic View：

```text
evt_A
evt_B
evt_C
```

展开为：

```text
event_req_01 + evt_A
event_req_01 + evt_B
event_req_01 + evt_C
```

后续 PlanItem 直接绑定：

```text
event_uid
```

而不再保存“第二次”“每次”等自然语言语义作为执行依据。

---

# 15. Event 选择规则

第一阶段确定性支持：

```text
first
last
all
index
range
```

例如：

\[
\operatorname{SelectEvents}
(
\text{heart\_gesture},
\text{index}=2
)
\]

得到：

```text
event_uid = evt_B
```

该过程不得交由 LLM 自由推断。

---

# 16. 缺失事件处理

如果 Required Event 状态为：

```text
not_found
```

则：

### hard

```text
Plan Status = blocked
```

并输出：

```text
unfulfilled_requirement
```

### soft

```text
warning
+
skip
```

第一阶段不自动选择其他动作替代。

### open

允许 Creative Planner 采用其他设计方案。

---

# 17. 低置信度事件处理

若 Semantic Event 状态为：

```text
uncertain
```

且需求：

```text
hard
```

则 Planner 输出：

```text
DependencyRequest
```

要求上层 Agent Controller：

```text
重新分析
更强模型验证
或请求用户确认
```

Planner 本身不判断事件真假。

若需求为 soft：

```text
confirmed events → 使用
uncertain events → 跳过 + warning
```

---

# 18. Global Strategy

首先根据：

```text
global_intent
+
AccessibilityPlanningProfile
+
video summary
```

形成：

```text
GlobalStrategy
```

建议结构：

```text
GlobalStrategy
├── visual_language
├── motion_language
├── pacing_strategy
├── background_strategy
├── music_strategy
├── layout_strategy
├── consistency_strategy
└── accessibility_strategy
```

例如：

```json
{
  "visual_language": ["cute", "bright", "summer"],
  "motion_language": ["soft_pop", "light_glow"],
  "pacing_strategy": "gesture_reactive",
  "background_strategy": "replacement",
  "layout_strategy": "upper_body_aware",

  "accessibility_strategy": {
    "subject_mode": "seated",
    "primary_action_region": "upper_body",
    "gesture_emphasis": "high",
    "camera_motion_intensity": "low_to_medium",
    "protect_active_hands": true,
    "preserve_mobility_device": true,
    "motion_amplitude_independent_energy": true
  }
}
```

---

# 19. Creative Planner 职责

Creative Planner 只负责创作决策。

包括：

```text
素材视觉风格
动画类型
视觉强调程度
大概持续时间
素材相对位置
节奏策略
视觉一致性
```

不负责生成视频事实。

例如允许：

```text
爱心使用 soft_pop
```

不允许：

```text
第二次比心发生在 10.35 秒
```

后者必须来自 Module 2。

---

# 20. 低幅度动作增强策略

对于：

```text
motion_amplitude = low
```

Planner 应优先采用：

```text
gesture-triggered overlay
local glow
small pulse
color emphasis
particle accent
text accent
beat flash
```

而不是：

```text
large camera shake
aggressive zoom
large reframing
```

除非用户明确要求。

原则：

```text
low physical amplitude
→ stronger semantic visual support
```

而不是：

```text
low physical amplitude
→ fake larger body movement
```

---

# 21. 坐姿空间规划策略

当：

```text
posture = seated
```

Spatial Planner 优先保护：

```text
face
active hands
gesture region
upper torso
subject region
mobility device region
```

而不是只保护：

```text
face
```

---

# 22. Protected Region 优先级

建议：

```text
P0
face

P1
active gesture region
active hands

P2
upper torso

P3
mobility device / important subject region
```

默认素材布局必须尽量避免：

\[
R_{\text{asset}}
\cap
R_{\text{face}}
\approx
\varnothing
\]

LaTeX：

```latex
R_{\text{asset}}
\cap
R_{\text{face}}
\approx
\varnothing
```

并尽量控制：

\[
\operatorname{IoU}
(
R_{\text{asset}},
R_{\text{gesture}}
)
<
\tau
\]

LaTeX：

```latex
\operatorname{IoU}
(
R_{\text{asset}},
R_{\text{gesture}}
)
<
\tau
```

除非用户明确要求素材覆盖动作区域。

---

# 23. Subject Region

对于轮椅场景，不应只使用：

```text
person_region
```

建议定义：

```text
subject_region
```

其语义是：

> 当前剪辑中需要完整保留的主体区域。

可包含：

```text
body
+
wheelchair / mobility device
```

Module 2 后续应逐步支持：

```text
subject_bbox
foreground_subject_mask
```

而不是仅：

```text
person_bbox
person_mask
```

---

# 24. 背景替换

对于：

```text
replace_background
```

Planner 应要求：

```text
foreground_subject_mask
```

而不是默认只使用：

```text
person_mask
```

第一阶段背景替换原则：

```text
主体
+
必要辅助设备
```

应一同被保留。

如果当前分割模型无法可靠保护轮椅：

```text
hard background replacement
→ needs_dependency / warning
```

不能静默生成明显缺失轮椅的抠像结果。

---

# 25. TemporalSpec

Logical Plan 阶段保留事件相对时间。

建议：

```text
TemporalSpec
├── mode
├── start_anchor
├── end_anchor
├── offset
├── duration
└── source_time_hint
```

例如：

```json
{
  "mode": "event_relative",

  "start_anchor": {
    "type": "semantic_event",
    "event_uid": "evt_a82f39",
    "boundary": "peak"
  },

  "offset": -0.1,

  "duration": {
    "mode": "preferred",
    "value": 0.8
  }
}
```

---

# 26. 时间 Anchor 类型

第一阶段支持：

```text
semantic_event
video_structure
source_time
```

Semantic Event 边界支持：

```text
start
peak
end
```

例如：

```text
at_event
→ peak

during_event
→ [start, end]

before_event
→ start

after_event
→ end
```

---

# 27. Duration

支持：

```text
exact
preferred
range
```

例如：

> 定格一秒

```json
{
  "mode": "exact",
  "value": 1.0
}
```

而：

> 爱心弹一下

可以：

```json
{
  "mode": "range",
  "min": 0.6,
  "preferred": 0.8,
  "max": 1.0
}
```

---

# 28. SpatialSpec

建议：

```text
SpatialSpec
├── coordinate_space
├── anchor
├── relation
├── offset_policy
├── scale_policy
├── avoid_regions
├── follow
├── direction_policy
└── fallback_positions
```

例如：

```json
{
  "coordinate_space": "source_video_normalized",

  "anchor": {
    "type": "event_anchor",
    "event_uid": "evt_a82f39"
  },

  "relation": "above",

  "avoid_regions": [
    "face",
    "active_gesture"
  ],

  "fallback_positions": [
    "upper_right",
    "upper_left",
    "right",
    "left"
  ]
}
```

---

# 29. 第一阶段 Spatial Relation

只支持：

```text
centered_on
above
below
left_of
right_of
upper_left
upper_right
screen_left
screen_right
follow
```

不需要第一阶段实现复杂自由布局。

---

# 30. 跟随素材

例如：

> 皇冠一直跟着头。

建议：

```json
{
  "anchor": {
    "type": "spatial_track",
    "target": "head"
  },

  "relation": "above",

  "follow": {
    "enabled": true,
    "mode": "trajectory",
    "smoothing": "source_smoothed"
  }
}
```

---

# 31. 震颤和轨迹稳定化

对于：

```text
tremor = true
```

Planner 不能直接把人体微小变化转换成大量关键帧。

应采用：

```text
smoothed source trajectory
+
lower follow sensitivity
+
larger dead zone
+
lower keyframe density
```

原则：

\[
T_{\text{asset}}
=
F(T_{\text{subject}},\alpha)
\]

LaTeX：

```latex
T_{\text{asset}}
=
F(T_{\text{subject}},\alpha)
```

其中 \(\alpha\) 控制视觉跟随灵敏度。

目的不是修改用户动作，而是避免视觉素材放大抖动和检测噪声。

---

# 32. 单侧上肢适配

若：

```text
active_hands = ["right"]
```

则 Planner 优先使用：

```text
right_hand
right_wrist
single_hand_anchor
```

不强制计算：

```text
left_hand + right_hand midpoint
```

对于：

```text
single_hand_heart
finger_heart
hand_raise
```

等事件可直接进行正常规划。

坐姿和单侧上肢状态分别处理，不能相互绑定。

---

# 33. 音乐与手势卡点

第一阶段不根据“动作幅度大小”决定 Beat Sync Priority。

建议事件优先级：

```text
heart_gesture        high
single_hand_heart    high
finger_heart         high
clap                 high

point_left/right     medium-high
hand_raise           medium-high

head_tilt            medium
upper_body_lean      medium

minor motion         low
```

Planner 应更关注：

```text
gesture completion
pose change
direction change
semantic action peak
```

而不是纯位移大小。

---

# 34. AssetRequest

模块三到模块四之间使用：

```text
AssetRequest
```

建议：

```text
AssetRequest
├── request_uid
├── asset_type
├── media_type
├── semantic_query
├── style_context
├── technical_requirements
├── usage_context
├── reuse_policy
├── source_policy
├── licensing_policy
├── fallback_policy
└── constraint_level
```

---

# 35. 第一阶段 Asset Type

MVP 只重点支持：

```text
background
sticker
image
music
sound_effect
```

文字不作为外部素材处理。

---

# 36. 素材复用

默认：

```text
每次比心出现同样爱心
```

对应：

```text
reuse_same_asset
```

而不是为每个事件搜索一次。

支持：

```text
reuse_same_asset
allow_variants
```

第一阶段不重点实现：

```text
unique_per_instance
```

---

# 37. AssetBinding

模块四返回：

```text
AssetBinding
```

例如：

```json
{
  "asset_request_uid": "asset_req_heart_01",
  "asset_uid": "asset_72bc",
  "uri": "...",
  "media_type": "image",
  "metadata": {
    "width": 1024,
    "height": 1024,
    "has_alpha": true
  },
  "match_score": 0.91
}
```

模块四不直接修改 Plan。

---

# 38. Global Logical Timeline

Logical Plan 第一阶段只定义：

```text
background
main_video
overlay
text
effect
audio
```

例如：

```text
background      z=0
main_video      z=100
overlay         z=200
effect          z=250
text            z=300
```

具体剪映轨道由 Module 5 决定。

---

# 39. Timeline Effect

PlanItem 建议包含：

```text
timeline_effect
```

第一阶段主要支持：

```text
non_structural
insert_duration
```

其中：

```text
overlay
text
effect
music
```

属于：

```text
non_structural
```

而：

```text
freeze
```

属于：

```text
insert_duration
```

第一阶段暂不重点实现复杂：

```text
remove_duration
time_warp
```

---

# 40. ToolCapabilityProfile

Module 3 定义能力协议：

```text
ToolCapabilityProfile
```

第一阶段关心：

```text
background_replacement
overlay
text
audio
freeze
keyframes
tracking
opacity
scale_animation
position_animation
```

例如：

```json
{
  "background_replacement": true,
  "overlay": true,
  "text": true,
  "audio": true,
  "freeze": true,
  "keyframes": true,
  "tracking": false
}
```

---

# 41. Capability Degradation

第一阶段允许：

```text
native tracking
→ keyframe following
→ static placement
```

但是否允许继续降级取决于需求强度。

### hard

不允许无法满足的静默降级。

### soft

允许：

```text
tracking
→ keyframes
```

必要时：

```text
→ static
```

并产生 warning。

---

# 42. Degradation Policy

建议：

```text
strict
allow_equivalent
allow_simplification
```

例如：

```text
皇冠必须跟着头
```

对应：

```text
strict
```

而：

```text
皇冠最好跟着头
```

可以：

```text
allow_equivalent
```

---

# 43. Constraint Validator

第一阶段至少检查：

```text
semantic constraints
temporal constraints
spatial constraints
asset constraints
capability constraints
accessibility constraints
```

---

# 44. Accessibility Validator

这是第一阶段必须实现的独立检查项。

至少检查：

```text
关键手势区域是否被遮挡
脸部是否被遮挡
活跃手部是否被遮挡
自动布局是否忽略坐姿构图
背景替换是否错误移除轮椅
裁剪是否破坏动作表达
轨迹跟随是否过度抖动
是否错误依赖不适用的站姿动作
低幅度动作是否被当作无效动作
是否使用不必要的大幅镜头运动
```

---

# 45. ValidationReport

建议：

```text
ValidationReport
├── status
├── semantic
├── temporal
├── spatial
├── capability
├── asset
├── accessibility
├── hard_violations
├── warnings
└── unresolved_dependencies
```

总体状态：

```text
valid
valid_with_warnings
blocked
needs_dependency
```

---

# 46. PlannerDependencyRequest

Planner 若缺少必要上游信息，不直接调用其他模块。

输出：

```text
PlannerDependencyRequest
```

例如：

```json
{
  "type": "video_analysis",
  "required_queries": [
    {
      "type": "event_detection",
      "event": "heart_gesture"
    }
  ]
}
```

由 Agent Controller 再调用 Module 2。

---

# 47. 两阶段 Planner API

第一阶段：

```python
planner.plan(...)
```

输入：

```text
EditingIntent
SemanticView
AccessibilityPlanningProfile
ToolCapabilityProfile
```

输出：

```text
LogicalEditingPlan
DependencyRequests
```

第二阶段：

```python
planner.materialize(...)
```

输入：

```text
LogicalEditingPlan
AssetBindings
SemanticView
ToolCapabilityProfile
```

输出：

```text
ResolvedEditingPlan
```

---

# 48. LogicalEditingPlan

建议正式结构：

```text
LogicalEditingPlan
├── plan_uid
├── version
├── provenance
├── global_strategy
├── accessibility_profile
├── plan_items
├── asset_requests
├── timeline_structure
├── validation
├── dependency_requests
├── warnings
└── unresolved
```

---

# 49. Provenance

至少保存：

```text
video_id
video_version
semantic_state_version
intent_version / intent_hash
planner_version
```

用于：

```text
缓存判断
局部重规划
Plan stale 检测
Debug
```

---

# 50. ResolvedEditingPlan

Materialization 后：

```text
ResolvedEditingPlan
├── plan_uid
├── source_logical_plan_version
├── resolved_items
├── asset_bindings
├── timeline_mapping
├── validation
└── warnings
```

仍保持软件无关。

---

# 51. ResolvedPlanItem

例如：

```json
{
  "plan_item_uid": "pln_heart_a",

  "operation": "add_overlay",

  "asset_uid": "asset_heart_17",

  "project_time": {
    "start": 4.10,
    "end": 4.90
  },

  "transform": {
    "position": [0.52, 0.30],
    "scale": 0.16
  },

  "animation": {
    "type": "soft_pop"
  }
}
```

不包含：

```text
CapCut animation ID
Premiere API
FFmpeg command
```

这些属于 Module 5。

---

# 52. 局部重规划

第一阶段数据模型必须支持：

```text
PlanPatch
```

即使 MVP 实现初期仍采用部分全量重算，也必须保留：

```text
requirement_id
↔
plan_item_uid
```

映射。

建议：

```text
PlanPatch
├── add_plan_items
├── update_plan_items
├── remove_plan_item_uids
├── add_asset_requests
├── update_asset_requests
├── remove_asset_request_uids
├── global_strategy_updates
└── requires_rematerialization
```

---

# 53. 第一阶段标准事件重点

针对坐姿/轮椅手势舞，Planner 第一阶段重点支持：

```text
heart_gesture
single_hand_heart
finger_heart

point_left
point_right

wave_hand
hand_raise
clap

open_hand
close_hand

head_tilt
upper_body_lean

ending_pose
```

以下不是 Planner MVP 重点：

```text
jump
squat
stand_up
large locomotion
```

即使 Module 2 已支持，也不优先设计对应剪辑模板。

---

# 54. 第一阶段重点编辑模式

优先支持以下模板化能力：

### Gesture Trigger Overlay

```text
gesture
→ sticker / image
```

### Gesture Trigger Effect

```text
gesture peak
→ glow / flash / particle
```

### Hand / Head Follow

```text
trajectory
→ overlay keyframes
```

### Directional Appearance

```text
point_left
→ left-side asset

point_right
→ right-side asset
```

### Gesture-aware Layout

```text
avoid face
+
avoid active hand
+
avoid gesture region
```

### Background Replacement

```text
foreground subject
+
background asset
```

### Music Sync

```text
gesture semantic peak
+
beat/downbeat
```

### Ending Freeze

```text
ending pose
→ freeze
→ text
```

---

# 55. 第一阶段不重点支持的 Planner 能力

暂不重点实现：

```text
复杂自动裁剪
多镜头切换
复杂速度曲线
大量结构剪切
自动 B-roll
字幕系统
说话人识别
镜头美学自动生成
多人物遮挡优化
复杂 3D 贴纸
自动镜头运动生成
```

对于自动裁剪尤其保守：

> 第一阶段宁可少裁，也不能裁掉关键手势、轮椅或主体表达区域。

---

# 56. MVP 验收场景一

用户：

> 每次比心的时候出现粉色爱心，不要挡脸。

系统应：

```text
识别全部 heart_gesture
        ↓
为每个 event_uid 生成 PlanItem
        ↓
统一生成一个 heart AssetRequest
        ↓
位置优先 event_anchor 上方
        ↓
避开 face + active gesture
        ↓
生成 pop 动画策略
```

---

# 57. MVP 验收场景二

用户为坐轮椅、低幅度动作：

> 做得更有活力一点。

Planner 不应自动生成：

```text
大幅镜头摇晃
大幅 zoom
高速 crop
```

而应优先生成：

```text
手势事件视觉反馈
局部光效
轻量 pop
节拍强调
颜色强化
```

---

# 58. MVP 验收场景三

用户：

> 皇冠一直跟着头。

若：

```text
tracking = true
```

使用：

```text
native tracking
```

若：

```text
tracking = false
keyframes = true
```

使用：

```text
smoothed head trajectory
→ keyframes
```

若两者均不支持：

### hard

```text
blocked
```

### soft

```text
static placement
+
warning
```

---

# 59. MVP 验收场景四

用户：

> 背景换成动漫海滩。

如果：

```text
posture = seated
```

Planner 要求：

```text
foreground_subject_mask
```

并明确：

```text
preserve mobility device
```

若分割结果仅可靠覆盖人体、不可靠覆盖轮椅：

```text
needs_dependency / warning
```

不得默认删除轮椅。

---

# 60. MVP 验收场景五

用户：

> 第二个爱心小一点。

系统应通过：

```text
IntentPatch
→ affected requirement/object
→ plan provenance
→ specific plan_item_uid
```

生成：

```text
PlanPatch
```

只更新第二个爱心对应的 Scale Policy。

不重规划：

```text
background
music
other gesture overlays
```

---

# 61. LLM 与确定性代码边界

## LLM 负责

```text
Global Strategy
视觉风格
动画语义
素材风格描述
创意布局偏好
视觉强调程度
软偏好综合
```

## 确定性代码负责

```text
事件选择
event_uid 绑定
时间边界解析
occurrence 展开
空间坐标计算
Protected Region 避让
AssetRequest 去重
Capability Check
Constraint Validation
Accessibility Validation
Plan Patch 对齐
```

原则：

> LLM 产生设计决策，不产生视频事实。

---

# 62. 推荐内部目录结构

建议第三模块：

```text
src/editing_planner/
├── __init__.py
├── models.py
├── api.py
│
├── accessibility/
│   ├── context.py
│   ├── policies.py
│   └── validator.py
│
├── strategy/
│   ├── global_strategy.py
│   └── creative.py
│
├── expansion/
│   └── requirements.py
│
├── temporal/
│   ├── resolver.py
│   └── timeline.py
│
├── spatial/
│   ├── resolver.py
│   └── placement.py
│
├── assets/
│   ├── requests.py
│   └── dedup.py
│
├── capability/
│   └── resolver.py
│
├── validation/
│   └── validator.py
│
├── materialize/
│   └── materializer.py
│
├── replan/
│   └── patch.py
│
└── llm/
    └── planner.py
```

---

# 63. 与现有仓库的依赖关系

保持：

```text
gesture_intent
      ↓
video_understanding
      ↓
editing_planner
```

Module 3 可以 import：

```python
from gesture_intent.models import EditingIntent
from video_understanding.models import SemanticView
```

Module 3 不应 import：

```text
asset_manager
edit_executor
```

避免反向依赖。

---

# 64. 第一阶段验收标准

模块三 MVP 至少满足：

1. 能消费现有 `EditingIntent`；
2. 能消费现有 `SemanticView`；
3. 能读取 `MobilityProfile / Accessibility Profile`；
4. 坐姿场景作为一级策略；
5. 不默认依赖站姿、大幅动作；
6. 能将 event requirement 展开到稳定 `event_uid`；
7. 能生成稳定 `PlanItem`；
8. 能生成 `AssetRequest`；
9. 同类重复素材可以去重；
10. 能生成事件相对 `TemporalSpec`；
11. 能生成 `SpatialSpec`；
12. 能保护 face；
13. 能保护 active hands；
14. 能保护 gesture region；
15. 能表达 preserve mobility device；
16. 能处理低幅度动作视觉增强；
17. 能处理 tremor 下的平滑跟随；
18. 能支持单侧上肢 Anchor；
19. 能检查工具能力；
20. 能进行合理降级；
21. Hard Requirement 不允许静默降级；
22. 能生成 ValidationReport；
23. 能区分 `valid / blocked / needs_dependency`；
24. 能生成 LogicalEditingPlan；
25. 能结合 AssetBinding 生成 ResolvedEditingPlan；
26. 能保存 Requirement → PlanItem provenance；
27. 能为后续 PlanPatch 留出接口。

---

# 65. 模块最终定义

Editing Planner 最终承担：

```text
用户想要什么
        +
视频真实发生什么
        +
用户当前动作与可动性条件
        +
当前剪辑后端能够做什么
        ↓
形成整体剪辑策略
        ↓
展开具体事件实例
        ↓
生成逐项剪辑决策
        ↓
处理时间与空间关系
        ↓
保护脸、手势区域和主体
        ↓
针对坐姿与轮椅场景调整视觉策略
        ↓
生成素材需求
        ↓
检查工具能力
        ↓
验证用户约束与无障碍约束
        ↓
LogicalEditingPlan
        ↓
结合素材结果实例化
        ↓
ResolvedEditingPlan
```

第三模块的核心不是生成一个“好看的效果列表”，而是建立一个：

> **可追踪、可验证、可局部修改、面向坐姿/轮椅手势舞原生优化的剪辑决策层。**

它是整个 Agent 从“理解”进入“执行”的关键中间层。