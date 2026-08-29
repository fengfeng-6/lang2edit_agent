# 自然语言驱动的手势舞智能剪辑 Agent——产品总体需求

## 1. 产品定位

本产品面向已经完成拍摄的手势舞短视频，构建一个能够通过自然语言完成视频剪辑全过程的智能 Agent 系统。

用户无需掌握复杂的视频剪辑操作，只需要上传已经拍摄好的原始视频，并使用自然语言描述期望的视频风格、背景、音乐、图片或贴纸素材、动画、特效以及具体剪辑要求。系统自动理解用户需求和原始视频内容，自主制定剪辑策略，搜索或生成所需素材，并调用剪映等视频剪辑工具完成实际剪辑。

系统同时需要保留视频项目的编辑状态，使用户能够在第一次剪辑完成后继续通过自然语言进行局部修改和多轮迭代，而不需要重新完成整个视频剪辑流程。

产品的核心目标可以概括为：

> 将用户的自然语言创作意图自动转换为可执行的视频剪辑方案，并通过视频理解、素材获取和剪辑工具调用完成实际视频制作。

整体处理链路为：

\[
\text{User Intent}
\rightarrow
\text{Video Understanding}
\rightarrow
\text{Editing Planning}
\rightarrow
\text{Asset Retrieval}
\rightarrow
\text{Tool Execution}
\rightarrow
\text{Feedback Refinement}
\]

LaTeX：

```latex
\text{User Intent}
\rightarrow
\text{Video Understanding}
\rightarrow
\text{Editing Planning}
\rightarrow
\text{Asset Retrieval}
\rightarrow
\text{Tool Execution}
\rightarrow
\text{Feedback Refinement}
```

---

## 2. 目标用户与典型使用场景

### 2.1 目标用户

产品主要面向希望快速制作手势舞、短视频和社交媒体视频，但不希望或不具备能力完成复杂剪辑操作的普通用户和内容创作者。

用户需要具备的最低条件仅包括：

- 已经拍摄完成的原始手势舞视频；
- 对最终视频效果的大致设想；
- 能够通过自然语言描述自己的剪辑需求。

用户不需要了解时间轴、关键帧、蒙版、轨道、转场、素材格式等专业剪辑概念。

### 2.2 典型交互方式

例如用户上传一段手势舞视频，并提出：

> 把这个手势舞做成夏日海边风格，背景换成动漫海滩，音乐找一个轻快一点的。每次手往右指的时候出现一个贝壳，往左指的时候出现一个海星，最后一个动作定格，然后出现“Summer!”。

系统需要自动完成：

1. 分析原始视频；
2. 识别人物及动作；
3. 找到向左、向右指以及结束动作发生的时间；
4. 规划人物抠像和背景替换方案；
5. 搜索或生成动漫海滩背景；
6. 搜索适合的贝壳和海星素材；
7. 搜索合适的背景音乐；
8. 将素材与动作时间点进行匹配；
9. 生成完整剪辑时间轴；
10. 调用剪辑软件完成实际编辑；
11. 输出最终视频以及可继续修改的工程状态。

之后用户还可以继续提出：

> 海星换成椰子树，音乐声音再小一点，最后文字改成“Hello Summer”。

系统只需要修改受到影响的素材和时间轴对象，而无需重新完成全部剪辑。

---

## 3. 当前产品范围

第一阶段产品聚焦于“手势舞短视频自动剪辑”，不直接追求通用视频自动剪辑。

主要处理对象为：

- 单人手势舞；
- 固定机位或轻微镜头运动；
- 时长较短的短视频；
- 人物主体相对清晰；
- 主要剪辑逻辑围绕人物动作、手势和音乐节奏展开。

第一阶段重点支持：

- 人物识别；
- 人物抠像；
- 背景替换；
- 手势和动作识别；
- 动作关键时间点检测；
- BGM 搜索与添加；
- 音乐节奏分析；
- 图片、PNG、贴纸等素材添加；
- 动画与关键帧；
- 基础转场；
- 视频特效；
- 文本添加；
- 视频定格；
- 基础裁剪和调速；
- 自然语言局部修改。

第一阶段暂不将以下能力作为重点目标：

- 长视频自动剪辑；
- 多机位剪辑；
- 复杂剧情理解；
- 多人角色关系分析；
- 电影级镜头语言生成；
- 自动口播剪辑；
- 大规模字幕校正；
- 专业影视后期调色；
- 高复杂度三维特效。

这些能力可以在后续版本中扩展。

---

# 4. 产品核心能力

系统整体划分为六个核心能力层。

## 4.1 自然语言需求理解

系统需要能够将用户自然语言中的创作需求转换为结构化的视频编辑需求。

例如：

> 做得可爱一点，背景换成粉色房间，每次比心的时候跳出来一个爱心，音乐换得欢快一点。

系统应识别出至少以下信息：

- 整体视觉风格：可爱；
- 背景需求：粉色房间；
- 音乐需求：欢快；
- 动作触发条件：比心；
- 需要添加的素材：爱心；
- 素材动画：弹出或类似动画。

需求理解模块需要处理三类信息：

### 全局需求

影响整个视频，例如：

- 视频整体风格；
- 背景；
- 色调；
- 音乐；
- 画幅；
- 整体节奏。

### 局部需求

作用于特定时间、动作或视频片段，例如：

- 比心时添加爱心；
- 指向左侧时出现图片；
- 第 8 秒放大人物；
- 最后一个动作定格。

### 条件触发需求

用户并不知道具体时间，而通过语义事件描述编辑位置，例如：

- 每次比心时；
- 手抬起来的时候；
- 音乐重拍的时候；
- 最后一个动作结束时。

因此自然语言需求理解模块需要建立：

\[
\text{Natural Language}
\rightarrow
\text{Structured Editing Intent}
\]

LaTeX：

```latex
\text{Natural Language}
\rightarrow
\text{Structured Editing Intent}
```

结构化需求将作为后续视频理解和剪辑规划模块的输入。

---

## 4.2 视频理解

视频理解模块负责理解“原视频中发生了什么”，为后续剪辑规划提供语义基础。

需要分析的信息主要包括：

### 基础视频信息

包括：

- 时长；
- 分辨率；
- 帧率；
- 画幅；
- 视频质量；
- 音频信息。

### 人物信息

包括：

- 人物位置；
- 人物轮廓；
- 人体关键点；
- 手部位置；
- 人物运动范围。

这些信息主要服务于：

- 人物抠像；
- 背景替换；
- 素材避让；
- 素材跟随；
- 动作识别。

### 动作与手势

重点识别：

- 比心；
- 指向左侧；
- 指向右侧；
- 张开手；
- 双手合并；
- 抬手；
- 放手；
- Ending Pose；

以及未来可以扩展的自定义动作。

### 时间结构

系统需要建立视频语义时间轴，例如：

```text
00:00–00:02  准备动作
00:02–00:04  双手展开
00:04.2       第一次比心
00:06.5       指向左侧
00:08.1       指向右侧
00:10.3       第二次比心
00:13.8       Ending Pose
```

该时间轴是用户语言中的“比心时”“最后一个动作”“指向右边时”等表达和实际视频时间轴之间的桥梁。

### 音频与节奏

如果原始视频包含音乐，还需要分析：

- BPM；
- beat；
- downbeat；
- 节奏变化；
- 音乐段落。

使后续剪辑能够进行动作和音乐节奏匹配。

---

## 4.3 自动剪辑规划

剪辑规划模块是整个 Agent 的核心决策模块。

它负责综合：

- 用户自然语言需求；
- 视频理解结果；
- 可用素材；
- 剪辑工具能力；

自动生成完整的剪辑策略。

例如用户提出：

> 做得可爱一些。

Planner 可能进一步规划为：

1. 对人物进行抠像；
2. 使用 pastel cartoon room 作为背景；
3. 搜索爱心、星星等可爱风格 PNG；
4. 在比心动作发生时添加爱心；
5. 使用短时 scale-in 动画；
6. 在音乐强拍处添加轻微 zoom；
7. 最后一个动作定格；
8. 加入 sparkle 特效。

因此剪辑规划需要完成：

\[
\text{Intent}
+
\text{Video Semantics}
\rightarrow
\text{Editing Plan}
\]

LaTeX：

```latex
\text{Intent}
+
\text{Video Semantics}
\rightarrow
\text{Editing Plan}
```

系统不能直接让大模型操作剪辑软件，而应首先生成一个与具体剪辑软件无关的结构化剪辑计划。

可以构建统一的 Editing DSL 或 Editing JSON。

例如：

```json
{
  "background": {
    "type": "replace",
    "query": "pastel cartoon room"
  },
  "music": {
    "query": "cute upbeat pop",
    "beat_sync": true
  },
  "events": [
    {
      "trigger": "gesture:heart",
      "effect": "heart_sticker",
      "animation": "pop"
    }
  ],
  "ending": {
    "freeze": 0.8,
    "effect": "flash"
  }
}
```

该结构作为整个系统的核心中间表示。

---

## 4.4 素材智能搜索与管理

当剪辑方案中涉及外部素材时，Agent 需要自动获得这些素材。

素材主要包括：

- 背景图片；
- 背景视频；
- PNG；
- Sticker；
- GIF；
- Overlay；
- 音乐；
- 音效；
- 字体；
- 特效素材。

素材来源至少包括三类。

### 本地素材

用户已有素材库。

### 在线素材

Agent 根据剪辑方案生成搜索关键词，并从允许使用的网络素材来源搜索。

例如：

```text
cute pink heart transparent png

anime beach background

summer upbeat music 125 bpm

sparkle overlay transparent
```

### AI 生成素材

如果搜索不到合适素材，或者用户明确要求自定义视觉内容，可以调用生成模型创建：

- 背景；
- PNG；
- 插画；
- 图标；
- 装饰元素。

素材搜索完成后还需要进行自动筛选。

筛选维度可以包括：

- 内容匹配程度；
- 风格匹配程度；
- 清晰度；
- 分辨率；
- 长宽比；
- 是否透明背景；
- 色调；
- 音乐 BPM；
- 音乐长度；
- 版权许可；
- 与已有素材的一致性。

因此该模块不是简单的搜索模块，而是：

\[
\text{Asset Requirement}
\rightarrow
\text{Search / Generation}
\rightarrow
\text{Ranking}
\rightarrow
\text{Selected Asset}
\]

LaTeX：

```latex
\text{Asset Requirement}
\rightarrow
\text{Search / Generation}
\rightarrow
\text{Ranking}
\rightarrow
\text{Selected Asset}
```

---

## 4.5 剪辑工具执行

该模块负责将规划好的剪辑策略真正转换为视频工程。

系统需要尽量避免：

> LLM 直接通过鼠标和键盘随机操作剪映 UI。

更合理的方式是设计统一的 Tool Executor。

Planner 输出 Editing DSL 后，由 Executor 将其转换为具体剪辑软件能够理解的操作。

例如统一定义：

```text
import_media()

add_track()

split_clip()

remove_background()

replace_background()

add_overlay()

set_position()

set_scale()

add_keyframe()

add_transition()

add_effect()

add_audio()

beat_align()

freeze_frame()

add_text()

export_video()
```

底层可以存在不同 Adapter：

```text
Editing DSL
     ↓
Tool Executor
     ↓
┌──────────┬───────────┬─────────┐
│ 剪映     │ Premiere  │ FFmpeg  │
└──────────┴───────────┴─────────┘
```

第一阶段优先实现剪映相关能力，同时预留其他后端接口。

剪辑软件调用方式的优先级原则为：

1. 官方 API；
2. 插件接口；
3. 脚本接口；
4. 工程文件生成或修改；
5. CLI；
6. UI Automation。

只有在缺乏更稳定接口时，才使用鼠标、键盘、视觉识别等 UI Automation 方式操作剪辑软件。

---

## 4.6 自然语言反馈与持续修改

视频生成后，系统需要支持用户继续通过自然语言修改视频。

例如：

> 第二个爱心太大了。

系统需要识别用户指向：

```text
heart_event_02
```

然后仅修改该素材的：

```text
scale
```

又例如：

> 音乐换一个更欢快的。

系统只需要重新执行：

```text
Music Requirement
→ Asset Search
→ Audio Replacement
```

而无需重新执行视频分析和全部剪辑规划。

因此系统需要维护持久化的视频编辑状态。

例如：

```text
Project
├── Original Video
├── Semantic Timeline
├── User Intent
├── Editing Plan
├── Assets
├── Timeline
│   ├── Main Video
│   ├── Background
│   ├── Stickers
│   ├── Effects
│   ├── Text
│   └── Audio
├── Object IDs
└── Edit History
```

所有可编辑对象都应具有稳定 ID，使用户能够通过自然语言引用之前创建的内容。

例如：

```text
heart_01
heart_02
background_01
music_01
text_ending_01
```

这也是系统实现真正多轮 Agent 编辑的基础。

---

# 5. Agent 总体工作流程

整个系统的一次完整任务可以分为以下阶段。

### Stage 1：用户输入

输入：

- 原始视频；
- 自然语言需求；
- 可选用户素材。

### Stage 2：需求解析

将自然语言转换为 Structured Editing Intent。

### Stage 3：视频分析

建立：

- 视频基础信息；
- 人物信息；
- 动作信息；
- 手势事件；
- 音乐节奏；
- Semantic Timeline。

### Stage 4：剪辑规划

Planner 根据：

```text
User Intent
+
Semantic Timeline
+
Tool Capability
```

生成 Editing Plan。

### Stage 5：素材需求分析

从 Editing Plan 中提取：

```text
Asset Requirements
```

### Stage 6：素材获取

依次尝试：

```text
Local Asset
→ Online Retrieval
→ AI Generation
```

### Stage 7：剪辑计划实例化

将抽象剪辑方案和具体素材绑定，形成完整 Timeline Plan。

### Stage 8：剪辑执行

Tool Executor 调用剪映等剪辑工具执行操作。

### Stage 9：结果检查

系统检查：

- 素材是否正确；
- 动作与素材时间是否对齐；
- 是否存在人物遮挡；
- 视频是否完整；
- 音频是否正常；
- 工程是否可以导出。

### Stage 10：结果输出

输出：

- 最终视频；
- 可编辑工程；
- 当前 Editing State。

### Stage 11：用户反馈

用户通过自然语言继续提出修改需求。

Agent 分析修改涉及的对象和影响范围，执行局部重新规划。

---

# 6. 系统总体架构

第一阶段总体架构定义为：

```text
                        ┌──────────────────────┐
                        │      User Input      │
                        │ Video + NL Request   │
                        └──────────┬───────────┘
                                   ↓
                        ┌──────────────────────┐
                        │    Intent Parser     │
                        └──────────┬───────────┘
                                   ↓
                        ┌──────────────────────┐
                        │ Video Understanding  │
                        └──────────┬───────────┘
                                   ↓
                        ┌──────────────────────┐
                        │   Editing Planner    │
                        └──────────┬───────────┘
                                   ↓
                        ┌──────────────────────┐
                        │      Editing DSL     │
                        └──────────┬───────────┘
                                   ↓
                  ┌────────────────┴────────────────┐
                  ↓                                 ↓
        ┌────────────────────┐            ┌───────────────────┐
        │  Asset Retrieval   │            │  Project State    │
        │ Search / Generate  │            │     Manager       │
        └─────────┬──────────┘            └─────────┬─────────┘
                  └────────────────┬────────────────┘
                                   ↓
                        ┌──────────────────────┐
                        │    Tool Executor     │
                        └──────────┬───────────┘
                                   ↓
                    ┌──────────────┼──────────────┐
                    ↓              ↓              ↓
                  剪映          Premiere        FFmpeg
                                   ↓
                        ┌──────────────────────┐
                        │   Final Video /      │
                        │ Editable Project     │
                        └──────────┬───────────┘
                                   ↓
                        ┌──────────────────────┐
                        │ Natural Language     │
                        │     Refinement       │
                        └──────────────────────┘
```

---

# 7. 产品设计原则

## 7.1 规划与执行解耦

Agent 不直接生成鼠标操作，而先生成抽象剪辑计划。

这样可以：

- 提高稳定性；
- 支持不同剪辑软件；
- 方便调试；
- 方便人工检查；
- 支持重新规划。

## 7.2 视频语义与时间轴绑定

所有动作、手势、音乐事件必须最终映射到实际时间轴。

例如：

```text
gesture:heart
→ event_04
→ timestamp = 4.23 s
```

从而使自然语言语义能够真正转换成剪辑操作。

## 7.3 所有编辑对象可寻址

任何由 Agent 创建的：

- 素材；
- 音频；
- 特效；
- 文字；
- 动画；
- 视频片段；

都需要具有稳定 ID。

这是后续自然语言局部修改的基础。

## 7.4 优先局部修改

后续用户修改时，应计算修改需求的影响范围。

原则上：

```text
局部需求
→ 局部重新规划
→ 局部重新执行
```

避免整个工程重新生成。

## 7.5 剪辑软件解耦

Agent 的核心能力不能与剪映的具体 UI 强绑定。

剪映只是当前优先支持的一个执行后端。

---

# 8. 第一阶段最小可用产品目标

第一阶段 MVP 目标是完成一个可以真正跑通的端到端流程。

输入：

```text
一段单人手势舞视频
+
自然语言需求
```

至少实现：

1. 视频载入；
2. 人物检测；
3. 基础人体关键点分析；
4. 若干预定义手势识别；
5. 关键动作时间检测；
6. 自然语言需求解析；
7. 自动生成剪辑计划；
8. 背景素材搜索；
9. PNG / Sticker 素材搜索；
10. BGM 搜索；
11. 将素材绑定到具体时间；
12. 生成 Editing DSL；
13. 调用剪辑工具构建工程；
14. 输出视频；
15. 支持至少一种自然语言局部修改。

第一阶段成功标准不是：

> AI 可以剪任何类型的视频。

而是：

> 对限定范围内的手势舞视频，用户能够只通过自然语言完成从原始视频到完整剪辑结果的端到端流程。

---

# 9. 后续模块细化方向

在总体需求确定后，后续需要分别对以下六个核心模块进行详细设计：

### 模块一：自然语言需求理解

需要进一步确定：

- Intent Schema；
- 用户指令分类；
- 全局需求和局部需求表达；
- 动作触发条件；
- 多轮指令理解；
- 歧义处理；
- LLM Prompt 和 Structured Output。

### 模块二：视频理解

需要进一步确定：

- 视频预处理；
- 人体检测；
- Pose Estimation；
- Hand / Gesture Recognition；
- Action Recognition；
- Event Detection；
- Semantic Timeline 数据结构；
- 音乐节奏分析；
- 模型选择。

### 模块三：剪辑规划

需要进一步确定：

- Planner 输入；
- Planner 输出；
- Editing DSL；
- 剪辑规则；
- Agent Planning；
- 计划检查；
- 重规划机制。

### 模块四：素材搜索与管理

需要进一步确定：

- 素材搜索来源；
- 素材搜索 API；
- 搜索 Query 生成；
- Ranking；
- 版权问题；
- AI Generation；
- Asset Database；
- 素材缓存。

### 模块五：剪辑工具执行

需要进一步确定：

- 剪映可调用能力；
- 剪映工程结构；
- API / 脚本 / UI Automation 可行性；
- Tool Schema；
- Executor；
- Adapter；
- 错误恢复；
- 视频导出。

### 模块六：反馈与持续修改

需要进一步确定：

- Project State；
- Object ID；
- Edit History；
- 用户引用解析；
- 修改影响分析；
- Partial Replanning；
- Undo / Redo；
- 多轮会话状态管理。

---

# 10. 最终产品目标

最终产品希望形成一个真正具备自主执行能力的视频剪辑 Agent。

用户负责表达：

> “我想做成什么样。”

Agent 负责解决：

> “原视频里面发生了什么。”

> “应该怎么剪。”

> “需要哪些素材。”

> “这些素材从哪里获得。”

> “具体应该在什么时间加入。”

> “应该如何调用剪辑软件执行。”

> “用户提出修改后应该修改哪些对象。”

最终将传统的视频剪辑过程从：

```text
用户构思
→ 用户搜索素材
→ 用户理解时间轴
→ 用户手动操作软件
→ 用户反复调整
```

转变为：

```text
用户自然语言描述需求
→ Agent 自主完成视频制作
→ 用户自然语言反馈
→ Agent 自动局部调整
```

产品最终定位为：

> **一个面向手势舞短视频场景，以自然语言作为主要交互方式，具备视频理解、剪辑规划、素材获取、工具调用和持续修改能力的 Agentic Video Editing System。**