# 自然语言驱动视频剪辑 Agent  
## 模块一：自然语言需求理解模块工程设计文档

## 1. 模块定位

自然语言需求理解模块（Intent Understanding / Intent Parser）是整个智能视频剪辑 Agent 的人机语义入口。

该模块负责将用户对视频剪辑效果的自然语言描述转换为机器可处理的结构化 Editing Intent，并在不丢失用户原始创作语义的前提下，为后续视频理解、剪辑规划、素材检索和工具执行模块提供统一的语义接口。

该模块的核心任务是回答：

> 用户希望视频最终呈现什么效果。

而不是回答：

> 剪辑软件具体应该如何执行这些操作。

因此，本模块负责表达用户意图和约束，但不负责决定具体素材、动画参数、时间轴数值、剪辑软件 API 或 UI 操作。

模块基本转换关系为：

\[
\text{User Language}
\rightarrow
\text{Editing Intent}
\]

LaTeX：

```latex
\text{User Language}
\rightarrow
\text{Editing Intent}
```

而不是：

\[
\text{User Language}
\rightarrow
\text{Editing Software Operations}
\]

LaTeX：

```latex
\text{User Language}
\rightarrow
\text{Editing Software Operations}
```

具体剪辑策略由 Editing Planner 决定，剪辑软件操作由 Tool Executor 决定。

---

# 2. 设计目标

本模块需要满足以下工程目标。

### 2.1 支持自然语言创作表达

允许用户使用非专业剪辑语言描述需求，例如：

> 做得可爱一点。

> 整体弄成夏日海边风格。

> 每次比心的时候跳出来一个爱心。

> 音乐欢快一点，但是不要太吵。

系统不能要求用户使用严格的时间轴、参数或剪辑术语。

### 2.2 保留原始语义

对于“高级一点”“青春”“有活力”“不要太花”等无法精确定量的创作表达，应保留用户原始文本，不能过早转换成具体参数。

### 2.3 结构化可计算关系

涉及对象类型、动作触发关系、时间关系、约束关系、对象引用等后续程序必须理解的内容，应转换成明确的结构化字段。

### 2.4 支持语义时间定位

允许用户通过：

- 第一次比心；
- 每次挥手；
- 最后一个动作；
- 转身过程中；
- 音乐重拍时；

等语义方式引用视频时间，而无需提供具体秒数。

### 2.5 支持多轮编辑

第一次生成完成后，用户能够继续提出：

> 第二个爱心小一点。

> 最后的文字换成 Hello Summer。

> 音乐再小一点。

系统能够结合当前工程状态解析具体编辑对象。

### 2.6 暴露不确定性

当系统无法可靠判断：

> “那个爱心”

具体指哪个对象时，不应静默猜测，而应输出 unresolved reference 和候选对象，交由上层 Agent Controller 决定是否自动推断或向用户询问。

### 2.7 支持局部修改

后续用户指令应作为已有 Editing Intent 的增量修改，而不是每次重新生成整个需求。

其状态更新关系为：

\[
I_{t+1}
=
\operatorname{Patch}(I_t,\Delta I_t)
\]

LaTeX：

```latex
I_{t+1}
=
\operatorname{Patch}(I_t,\Delta I_t)
```

其中：

- \(I_t\)：当前有效 Editing Intent；
- \(\Delta I_t\)：本轮用户产生的 Intent Patch；
- \(I_{t+1}\)：更新后的有效 Editing Intent。

---

# 3. 模块边界

## 3.1 本模块负责

自然语言需求理解模块负责：

- 用户指令分类；
- 用户需求提取；
- 创作语义保留；
- 语义标准化；
- 编辑对象识别；
- 视频事件识别需求生成；
- 时间语义关系解析；
- 用户约束解析；
- 约束强度解析；
- 已有工程对象引用解析；
- 视频语义事件引用解析；
- 用户需求冲突检测；
- 不确定性表达；
- 后续修改的 Intent Patch 生成。

---

## 3.2 本模块不负责

本模块不负责：

- 视频动作实际检测；
- 视频人物识别；
- 人体关键点检测；
- 手势发生时间计算；
- 背景素材选择；
- 音乐素材搜索；
- 图片素材搜索；
- AI 素材生成；
- 动画参数规划；
- 素材位置规划；
- 关键帧生成；
- 剪映具体操作；
- 视频导出。

例如用户提出：

> 每次比心的时候弹出一个可爱的粉色爱心。

Intent Parser 可以确定：

```text
event = heart_gesture
occurrence = all
asset = heart
color = pink
style = cute
```

但不能在本模块直接决定：

```text
heart_03.png
scale = 0.72
position = (0.73, 0.31)
animation = scale-in
duration = 0.25 s
```

后者属于 Planning 阶段。

---

# 4. 总体设计原则

## 4.1 半结构化 Intent Schema

模块采用半结构化 Schema。

设计原则为：

> 对程序必须依赖的关系进行强结构化，对创作性、模糊性和主观程度较高的内容保留自然语言原始语义。

例如：

```json
{
  "mood": {
    "raw": "欢快一点，但是不要太吵",
    "tags": [
      "upbeat",
      "light",
      "moderate_energy"
    ]
  }
}
```

而不是直接转换为：

```json
{
  "bpm": 128,
  "volume_db": -12
}
```

后者属于 Planner 的决策。

---

## 4.2 原始语义与标准化语义并存

每个重要创作语义允许同时保存：

```text
raw
+
canonical / tags
```

可以表示为：

\[
I =
I_{\text{raw}}
+
I_{\text{normalized}}
\]

LaTeX：

```latex
I =
I_{\text{raw}}
+
I_{\text{normalized}}
```

其中：

- `raw` 用于保留用户真实意图；
- `canonical` 用于标准化动作、对象等概念；
- `tags` 用于搜索、规划和相似性匹配。

---

## 4.3 用户明确要求与系统推断隔离

Intent Parser 不允许自行扩展新的创作需求。

例如：

> 背景换成海边。

只能得到：

```text
background = beach
```

不能自行添加：

```text
sunset
palm_tree
summer_music
blue_filter
```

这些属于 Planner 的创作推导。

因此要求所有核心 Requirement 均保存：

```text
source_text
```

用于需求溯源。

---

# 5. Editing Intent 总体结构

第一版 Editing Intent 定义为：

```text
EditingIntent
├── global_intent
├── object_requirements
├── event_bound_requirements
├── explicit_operations
├── constraints
└── unresolved
```

六个字段分别承担：

| 字段 | 作用 |
|---|---|
| global_intent | 描述整个视频的总体创作方向 |
| object_requirements | 描述用户明确要求存在或修改的编辑对象 |
| event_bound_requirements | 描述与视频动作、音乐等语义事件绑定的需求 |
| explicit_operations | 描述用户明确指定的通用剪辑操作 |
| constraints | 描述禁止条件、保护条件和限制条件 |
| unresolved | 保存无法可靠解析的对象引用或语义 |

每一个 Requirement 原则上还应包含：

```text
id
source_text
constraint_level
confidence
```

---

# 6. Global Intent

Global Intent 表示影响整个视频的总体创作需求。

第一版可包含：

```text
GlobalIntent
├── theme
├── mood
├── style
├── pacing
├── color_preference
├── platform_style
└── autonomy
```

例如用户提出：

> 做成夏日、青春、比较有活力的小红书风格。

可以表示为：

```json
{
  "global_intent": {
    "theme": {
      "raw": "夏日",
      "tags": ["summer"]
    },
    "mood": {
      "raw": "青春、有活力",
      "tags": [
        "youthful",
        "energetic"
      ]
    },
    "style": {
      "raw": "比较有活力的小红书风格",
      "tags": [
        "social_media",
        "lively"
      ]
    },
    "platform_style": "xiaohongshu",
    "pacing": null
  }
}
```

---

# 7. Object Requirements

Object Requirement 描述用户明确要求新增、替换、修改或删除的编辑对象。

第一阶段支持的 Object Type 包括：

```text
background
music
image
sticker
text
effect
sound_effect
overlay
```

例如：

> 背景换成动漫海滩，再加一首轻快的音乐，最后加一个 Summer 文字。

可解析为：

```json
{
  "object_requirements": [
    {
      "id": "req_background_01",
      "object_type": "background",
      "action": "replace",
      "description": {
        "raw": "动漫海滩",
        "tags": [
          "anime",
          "beach",
          "summer"
        ]
      },
      "constraint_level": "hard",
      "source_text": "背景换成动漫海滩"
    },
    {
      "id": "req_music_01",
      "object_type": "music",
      "action": "add",
      "description": {
        "raw": "轻快的音乐",
        "tags": [
          "upbeat",
          "light"
        ]
      },
      "constraint_level": "soft",
      "source_text": "再加一首轻快的音乐"
    },
    {
      "id": "req_text_01",
      "object_type": "text",
      "action": "add",
      "content": "Summer",
      "constraint_level": "hard",
      "source_text": "最后加一个 Summer 文字"
    }
  ]
}
```

每个 Requirement 使用稳定 ID，以支持后续需求溯源：

```text
req_background_01
        ↓
plan_background_01
        ↓
asset_background_03
        ↓
timeline_background_01
```

---

# 8. Event-Bound Requirements

Event-Bound Requirement 用于表达：

> 某个剪辑内容应该与视频中的某个语义事件发生关系。

它是本系统自然语言交互与视频时间轴连接的核心接口。

基本形式为：

\[
\text{Editing Action}
\leftrightarrow
\text{Semantic Video Event}
\]

LaTeX：

```latex
\text{Editing Action}
\leftrightarrow
\text{Semantic Video Event}
```

---

## 8.1 Temporal Reference

所有基于语义的视频时间引用统一抽象为：

\[
\text{Temporal Reference}
=
\text{Event}
+
\text{Occurrence}
+
\text{Relation}
+
\text{Range}
\]

LaTeX：

```latex
\text{Temporal Reference}
=
\text{Event}
+
\text{Occurrence}
+
\text{Relation}
+
\text{Range}
```

其中：

- Event：用户引用的是什么事件；
- Occurrence：事件的第几次；
- Relation：编辑内容与事件的时间关系；
- Range：事件是时间点还是持续区间。

---

# 9. Semantic Event 类型

第一阶段支持五类 Semantic Event。

## 9.1 Gesture Event

用于描述明确的手部动作，例如：

```text
比心
指向左侧
指向右侧
挥手
点赞
V 手势
OK 手势
双手张开
双手合拢
```

标准表示：

```json
{
  "type": "gesture",
  "raw": "手摆成一个心",
  "canonical": "heart_gesture"
}
```

不同自然语言：

```text
比心
做爱心
手摆成一个心
那个爱心动作
```

均可标准化为：

```text
heart_gesture
```

---

## 9.2 Body Action Event

用于描述身体整体动作，例如：

```text
转身
蹲下
起身
跳跃
向左移动
向右移动
身体倾斜
靠近镜头
Ending Pose
```

例如：

```json
{
  "type": "body_action",
  "raw": "转身",
  "canonical": "turn_body"
}
```

---

## 9.3 Pose Condition

用于描述无法简单归入固定动作类别的姿态关系。

例如：

> 手举到头顶的时候。

可表示为：

```json
{
  "type": "pose_condition",
  "subject": "hand",
  "relation": "above",
  "reference": "head",
  "raw": "手举到头顶"
}
```

例如：

> 双手交叉在胸前。

可表示为：

```json
{
  "type": "pose_condition",
  "subject": [
    "left_hand",
    "right_hand"
  ],
  "relation": "crossed",
  "reference": "chest",
  "raw": "双手交叉在胸前"
}
```

该设计使系统不局限于有限类别的动作识别。

后续 Video Understanding 应同时支持：

```text
Standard Event Library
+
Open-vocabulary Event Detection
```

即常用动作使用高精度专用检测，不常见动作根据用户需求进行开放语义判断。

---

## 9.4 Video Structural Event

用于表示视频本身的结构节点，例如：

```text
video_start
video_end
first_action
last_action
action_start
action_end
middle_section
```

例如：

> 最后一个动作定格一下。

其中目标可表示为：

```json
{
  "type": "video_structure",
  "canonical": "last_action"
}
```

---

## 9.5 Audio Event

用于描述音乐时间节点。

第一阶段支持：

```text
beat
downbeat
music_onset
chorus_start
chorus_end
music_section_change
```

例如：

> 音乐重拍的时候闪一下。

可解析为：

```json
{
  "type": "audio_event",
  "canonical": "downbeat"
}
```

---

# 10. Occurrence

Occurrence 用于表示一个语义事件的第几次出现。

第一阶段支持：

```text
first
last
all
index
range
```

例如：

> 第二次比心。

```json
{
  "type": "index",
  "value": 2
}
```

> 每次比心。

```json
{
  "type": "all"
}
```

> 第二次到第四次比心。

```json
{
  "type": "range",
  "start": 2,
  "end": 4
}
```

---

# 11. Temporal Relation

Temporal Relation 用于表示编辑对象和语义事件之间的时间关系。

第一阶段支持：

```text
before_event
at_event
during_event
after_event
from_event
until_event
between_events
```

例如：

> 比心的时候出现爱心。

```text
at_event
```

> 转身过程中添加旋转效果。

```text
during_event
```

> 比心之后出现星星。

```text
after_event
```

> 从第一次比心开始一直持续到最后一个动作。

```text
between_events
```

---

# 12. Event-Bound Requirement Schema

完整结构建议为：

```text
EventBoundRequirement
├── id
├── source_text
├── trigger
│   ├── event
│   │   ├── type
│   │   ├── raw
│   │   └── canonical / condition
│   ├── occurrence
│   └── temporal_relation
│
├── requirement
│   ├── object_type / operation
│   └── semantic_description
│
├── constraint_level
└── confidence
```

例如：

> 每次比心的时候出现一个粉色爱心。

```json
{
  "id": "event_req_01",
  "source_text": "每次比心的时候出现一个粉色爱心",
  "trigger": {
    "event": {
      "type": "gesture",
      "raw": "比心",
      "canonical": "heart_gesture"
    },
    "occurrence": {
      "type": "all"
    },
    "temporal_relation": "at_event"
  },
  "requirement": {
    "object_type": "sticker",
    "action": "add",
    "semantic_description": {
      "raw": "粉色爱心",
      "tags": [
        "heart",
        "pink"
      ]
    }
  },
  "constraint_level": "hard",
  "confidence": 0.98
}
```

---

# 13. Explicit Operations

用户有时会明确指定具体的通用剪辑操作。

例如：

> 最后定格一秒。

Intent 中可以表示：

```json
{
  "id": "operation_01",
  "operation": "freeze",
  "target": {
    "type": "semantic_event",
    "value": "ending_pose"
  },
  "parameters": {
    "duration": {
      "value": 1.0,
      "unit": "second"
    }
  },
  "constraint_level": "hard"
}
```

第一阶段可支持的通用操作包括：

```text
freeze
trim
split
remove
scale_adjust
position_adjust
volume_adjust
speed_adjust
replace_text
replace_asset
```

这些均属于与具体软件无关的编辑语义。

---

# 14. Constraints

Constraint 表示用户提出的限制、禁止条件和保护条件。

典型需求包括：

```text
不要挡脸
不要太花
音乐不要太响
保持原视频长度
不要裁掉人物
不要修改原来的音乐
不要添加文字
人物始终保持完整
```

例如：

> 爱心不要挡住脸。

可以表示为：

```json
{
  "id": "constraint_01",
  "scope": {
    "target": "heart_sticker"
  },
  "type": "avoid_overlap",
  "reference": "face",
  "raw": "爱心不要挡住脸",
  "constraint_level": "hard"
}
```

例如：

> 音乐不要太响。

不应直接转换为具体 dB：

```json
{
  "type": "audio_volume",
  "target": "background_music",
  "preference": {
    "raw": "不要太响",
    "tags": [
      "low_volume"
    ]
  },
  "constraint_level": "soft"
}
```

总体原则为：

> 结构化关系，保留程度。

---

# 15. Constraint Level

所有重要 Requirement 和 Constraint 可使用统一的约束强度。

第一版定义三个等级：

```text
hard
soft
open
```

## hard

用户明确要求，Planner 原则上不能违反。

例如：

> 背景一定要是海边。

## soft

用户表达偏好，Planner 可在必要时根据整体效果调整。

例如：

> 背景最好是海边。

## open

用户明确授权 Agent 自主决定。

例如：

> 背景你自己挑。

---

# 16. Agent Autonomy

用户可能只对部分内容提出硬约束，对剩余内容开放 Agent 自主设计。

例如：

> 背景一定要是海边，其他你自己发挥。

可以表示为：

```json
{
  "autonomy": {
    "default": "open",
    "protected_requirements": [
      "req_background_01"
    ]
  }
}
```

Planner 可据此判断其决策空间。

---

# 17. Unresolved Reference

当用户引用的对象不能可靠定位时，应显式输出 unresolved。

例如当前工程存在：

```text
heart_01
heart_02
heart_03
```

用户提出：

> 那个爱心大一点。

系统可能输出：

```json
{
  "unresolved": [
    {
      "type": "object_reference",
      "raw": "那个爱心",
      "candidates": [
        "heart_01",
        "heart_02",
        "heart_03"
      ],
      "confidence": 0.42
    }
  ]
}
```

Intent Parser 不负责决定是否询问用户。

后续由 Agent Controller 根据：

```text
confidence
editing_cost
reversibility
error_risk
```

选择：

```text
automatic_resolution
/
request_clarification
/
execute_and_review
```

---

# 18. 模块输入

完整输入建议定义为：

```text
IntentParserInput
├── user_utterance
│
├── request_context
│   ├── request_stage
│   └── conversation_context
│
├── current_effective_intent
│
├── semantic_project_view
│
└── semantic_video_view
```

其中：

### user_utterance

用户当前自然语言输入。

### request_context

当前请求属于第一次生成还是已有项目修改等上下文。

### current_effective_intent

当前有效的整体用户需求。

主要用于多轮修改。

### semantic_project_view

当前剪辑工程的简化语义视图。

### semantic_video_view

当前已经获得的视频语义事件信息。

---

# 19. Semantic Project View

Revision 模式下不建议直接把完整工程文件发送给 LLM。

Project State Manager 应提供简化的 Semantic Project View。

例如：

```text
Background
- background_01: anime beach

Music
- music_01: summer pop

Stickers
- heart_01: first heart gesture
- heart_02: second heart gesture

Text
- text_ending_01: "Summer!", ending
```

用于解析：

> 第二个爱心。

> 最后那个文字。

> 当前背景。

等引用。

---

# 20. Semantic Video View

Semantic Video View 用于解析用户对原始视频事件的引用。

例如：

```text
gesture_heart_01
gesture_heart_02
body_turn_01
ending_pose_01
```

但 Intent Parser 应尽量保留语义引用，而不是直接依赖具体时间戳。

例如：

> 第二次比心以后。

应解析为：

```text
event = heart_gesture
occurrence = 2
relation = after_event
```

具体时间由 Video Understanding / Timeline Resolver 绑定。

---

# 21. 请求类型

Intent Parser 首先需要识别用户当前指令的请求类型。

第一阶段支持：

```text
initial_edit
revision
addition
removal
```

例如：

> 帮我做成夏日海边风格。

```text
initial_edit
```

> 第二个爱心小一点。

```text
revision
```

> 再加一点星星。

```text
addition
```

> 最后那个闪光删掉。

```text
removal
```

未来可以扩展：

```text
query
undo
redo
style_regeneration
```

---

# 22. 运行流程

完整运行过程为：

\[
\text{Utterance}
\rightarrow
\text{Intent Classification}
\rightarrow
\text{Semantic Extraction}
\rightarrow
\text{Normalization}
\rightarrow
\text{Reference Resolution}
\rightarrow
\text{Constraint Analysis}
\rightarrow
\text{Consistency Check}
\rightarrow
\text{Intent Output}
\]

LaTeX：

```latex
\text{Utterance}
\rightarrow
\text{Intent Classification}
\rightarrow
\text{Semantic Extraction}
\rightarrow
\text{Normalization}
\rightarrow
\text{Reference Resolution}
\rightarrow
\text{Constraint Analysis}
\rightarrow
\text{Consistency Check}
\rightarrow
\text{Intent Output}
```

---

# 23. Step 1：Intent Classification

判断：

- 初始编辑；
- 修改；
- 新增；
- 删除。

识别当前指令需要依赖：

```text
user_utterance
+
project_state
+
conversation_context
```

---

# 24. Step 2：Semantic Extraction

提取用户明确表达的信息。

例如：

> 背景一定换成海边，音乐欢快一点，每次比心的时候出现粉色爱心，但不要挡脸。

提取：

```text
background = beach
music = upbeat
event = heart_gesture
occurrence = all
asset = pink heart
constraint = avoid face
```

这一阶段禁止添加用户没有表达的新需求。

---

# 25. Step 3：Normalization

对动作、对象、风格等表达进行标准化。

例如：

```text
比个心
手摆成爱心
比心动作
```

统一映射：

```text
heart_gesture
```

与此同时保留：

```text
raw
```

字段。

---

# 26. Step 4：Reference Resolution

Reference Resolution 包括两类。

## 编辑对象引用

例如：

> 第二个爱心。

需要查询 Semantic Project View。

## 视频事件引用

例如：

> 第二次比心。

需要生成 Semantic Event Reference。

两者必须区分。

---

# 27. Step 5：Constraint Analysis

判断：

```text
hard
soft
open
```

同时分析用户是否授权 Agent 自主完成未明确部分。

---

# 28. Step 6：Confidence Evaluation

置信度应尽量细化到具体解析单元，而不是只输出一个全局值。

例如：

```text
event_type = gesture        0.99
canonical = heart_gesture   0.97
occurrence = all            0.99
asset = heart               0.99
```

对于：

> 做得高级一点。

可能为：

```text
style.raw = 高级一点
tags = elegant / premium
confidence = 0.72
```

Confidence 表示：

> 系统对自身解析结果的可信程度。

而不是用户需求的强度。

---

# 29. Step 7：Consistency Check

检测用户需求内部可能存在的冲突。

例如：

> 保持视频长度完全不变，最后再增加两秒定格。

可能产生：

```json
{
  "conflicts": [
    {
      "requirements": [
        "constraint_01",
        "operation_03"
      ],
      "type": "duration_conflict",
      "severity": "medium"
    }
  ]
}
```

Consistency Checker 只负责发现冲突。

冲突如何处理由上层 Agent Controller 决定。

---

# 30. Required Video Queries

Intent Parser 需要进一步生成：

```text
required_video_queries
```

用于告诉 Video Understanding：

> 为了完成当前用户需求，需要从视频中解析什么。

例如：

> 每次比心的时候出现爱心。

输出：

```json
{
  "required_video_queries": [
    {
      "type": "event_detection",
      "event": "heart_gesture",
      "required_occurrence": "all"
    }
  ]
}
```

例如：

> 手举到头顶时出现皇冠。

输出：

```json
{
  "required_video_queries": [
    {
      "type": "pose_condition_detection",
      "condition": {
        "subject": "hand",
        "relation": "above",
        "reference": "head"
      }
    }
  ]
}
```

由此形成：

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

该机制是后续 Video Understanding 模块的核心输入方式。

---

# 31. 需求驱动的视频理解原则

Video Understanding 不应默认执行：

```text
detect all gestures
detect all actions
detect all objects
detect all scenes
detect all poses
```

而应该根据用户需求执行目标检测。

例如：

> 每次比心的时候出现爱心。

只生成：

```text
detect heart_gesture
```

如果用户随后提出：

> 转身的时候加旋转效果。

再增加：

```text
detect turn_body
```

这种机制能够降低视频理解计算量，同时减少无意义的识别结果。

---

# 32. Video Understanding 需要满足的事件输出要求

由于 Intent 支持：

```text
before_event
at_event
during_event
after_event
```

因此后续 Video Understanding 不能只输出单个 timestamp。

一个视频事件原则上至少应具有：

```json
{
  "event_id": "gesture_heart_02",
  "event_type": "gesture",
  "canonical": "heart_gesture",
  "start_time": 9.82,
  "peak_time": 10.35,
  "end_time": 10.91,
  "confidence": 0.94
}
```

其中：

```text
at_event
→ peak_time

during_event
→ [start_time, end_time]

before_event
→ start_time

after_event
→ end_time
```

对于不存在明确 Peak 的动作：

```text
peak_time = null
```

---

# 33. Initial Editing Request 流程

用户第一次提出：

> 把视频做成可爱的夏日海边风格，每次比心的时候出现粉色爱心，最后一个动作定格一秒，音乐欢快一点，爱心不要挡脸。

Intent Parser 输出可概括为：

```text
request_type
    initial_edit

global_intent
    theme = summer beach
    style = cute
    mood = lively

object_requirements
    background
    music

event_bound_requirements
    every heart_gesture
        → pink heart

explicit_operations
    last_action
        → freeze 1s

constraints
    heart stickers avoid face

required_video_queries
    detect heart_gesture
    detect last_action
    track face/person

unresolved
    none
```

---

# 34. Revision Request 流程

假设已有：

```text
heart_01
heart_02
text_ending_01
music_01
```

用户提出：

> 第二个爱心小一点，最后那个文字改成 Hello Summer，音乐再小一点。

Parser 输出：

```text
Revision 1
target = heart_02
operation = scale_adjust
direction = smaller

Revision 2
target = text_ending_01
operation = replace_text
value = Hello Summer

Revision 3
target = music_01
operation = volume_adjust
direction = lower
```

并产生：

```json
{
  "affected_objects": [
    "heart_02",
    "text_ending_01",
    "music_01"
  ]
}
```

后续 Planner 仅处理这些对象。

---

# 35. Intent State

多轮编辑过程中必须保存：

```text
Current Effective Intent
```

用户第二轮没有提到的旧需求默认继续有效。

例如：

第一轮：

> 背景用海边。

第二轮：

> 音乐换得欢快一点。

正确状态更新为：

```text
background = beach    保留
music = more upbeat   更新
```

而不是重新生成一个只包含 music 的完整需求。

因此 Revision Parser 输出的本质为：

```text
Intent Patch
```

系统状态更新方式：

```text
Current Effective Intent
        +
Intent Patch
        ↓
Intent State Manager
        ↓
Updated Effective Intent
```

---

# 36. 模块输出

第一版输出结构定义为：

```text
IntentParserOutput
├── request_type
├── editing_intent / intent_patch
├── normalized_semantics
├── resolved_references
├── required_video_queries
├── affected_objects
├── conflicts
├── unresolved
└── confidence
```

第一次编辑主要输出：

```text
editing_intent
```

后续编辑主要输出：

```text
intent_patch
```

---

# 37. 与 Video Understanding 的接口

Intent Parser 主要向 Video Understanding 输出：

```text
required_video_queries
```

Video Understanding 返回：

```text
Semantic Events
+
Spatial Information
+
Confidence
```

例如：

```text
Intent:
每次比心的时候出现爱心
```

转换为：

```text
required_video_query:
heart_gesture / all
```

Video Understanding 返回：

```text
gesture_heart_01
gesture_heart_02
gesture_heart_03
```

再由后续 Planner 完成时间绑定。

---

# 38. 与 Editing Planner 的接口

Planner 接收：

```text
Current Effective Intent
+
Video Semantic State
+
Tool Capabilities
```

其中 Intent Parser 提供的是：

> 用户需要什么。

Planner 决定：

> 具体怎么实现。

例如：

```text
Intent:
可爱的粉色爱心
```

Planner 才进一步决定：

```text
asset type
size
animation
position
duration
```

---

# 39. 与 Project State Manager 的接口

Project State Manager 需要向 Intent Parser 提供：

```text
Semantic Project View
Current Effective Intent
Recent Edit History
```

Intent Parser 则返回：

```text
Intent Patch
Affected Object IDs
Resolved References
```

以支持多轮视频编辑。

---

# 40. 第一阶段 MVP 范围

Intent Parser 第一阶段至少需要稳定支持以下表达。

### 全局创作

```text
做成夏日风格
整体可爱一点
做得活泼一些
做成小红书风格
```

### 对象需求

```text
背景换成海边
加一首欢快音乐
最后加一个 Summer
```

### 手势事件

```text
第一次比心
第二次比心
最后一次比心
每次比心
```

### 时间关系

```text
比心的时候
比心之前
比心之后
整个转身过程中
```

### 范围关系

```text
从第一次比心开始
一直到最后一个动作
```

### 音乐事件

```text
音乐重拍的时候
```

### 视频结构

```text
视频最后
最后一个动作
最后一个动作结束后
```

### 后续修改

```text
第二个爱心小一点
最后那个文字换掉
音乐小一点
把最后那个效果删掉
```

---

# 41. 第一阶段暂不重点实现的能力

以下需求 Schema 可以预留，但第一阶段不要求完整实现：

```text
next_event
previous_event
complex multi-event reasoning
speech semantic event
complex facial expression
object interaction
multi-person reference
multi-shot narrative reference
```

例如：

> 第一次比心之后的下一个动作。

这类复杂事件关系可以在第二阶段继续扩展。

---

# 42. 工程实现建议

Intent Parser 在第一阶段可以主要由 LLM 承担，但应通过严格的 Structured Output Schema 约束输出。

总体实现结构建议为：

```text
User Request
      ↓
LLM Intent Parser
      ↓
Schema Validator
      ↓
Canonicalizer
      ↓
Reference Resolver
      ↓
Consistency Checker
      ↓
IntentParserOutput
```

其中不建议完全依赖一次 LLM 调用直接完成所有工作。

建议将：

```text
Schema Validation
Reference Resolution
Conflict Detection
```

尽可能独立成程序模块，以提升确定性和可调试性。

---

# 43. 数据持久化要求

至少需要长期保存：

```text
Original User Utterances
Current Effective Intent
Intent History
Intent Patch History
Resolved References
Requirement IDs
Constraint IDs
```

这样可以保证：

- 多轮修改；
- 撤销；
- 需求追踪；
- Planner 可解释性；
- 错误调试。

---

# 44. 模块验收标准

第一阶段自然语言需求理解模块可按照以下标准验收：

1. 能够将初始用户剪辑需求转换为合法 Editing Intent；
2. 能够正确区分全局需求、对象需求、事件绑定需求和约束；
3. 能够将常见手势表达标准化为统一 Semantic Event；
4. 能够正确识别 first、last、all、index 等事件次数；
5. 能够正确表示 before、at、during、after 等时间关系；
6. 能够生成 required_video_queries；
7. 能够结合 Project State 解析“第二个爱心”等已有对象引用；
8. 能够将后续修改转换成 Intent Patch；
9. 不因用户未重复旧需求而丢弃已有需求；
10. 能够检测明显需求冲突；
11. 对无法可靠解析的内容输出 unresolved，而不是静默猜测；
12. 所有重要需求均能够追溯到原始用户输入。

---

# 45. 模块最终定义

自然语言需求理解模块最终承担的是：

```text
用户说了什么
        ↓
用户真正想要什么
        ↓
哪些内容需要结构化
        ↓
哪些创作语义需要原样保留
        ↓
用户引用的是哪个对象或视频事件
        ↓
用户给出了哪些硬约束和软偏好
        ↓
当前需求需要视频理解模块检测什么
        ↓
输出统一 Editing Intent / Intent Patch
```

该模块最终形成整个视频剪辑 Agent 的统一需求语义层，并作为 Video Understanding、Editing Planner、Project State Manager 等后续模块共同依赖的核心协议。