# 手势舞智能剪辑 Agent

用中文自然语言驱动视频剪辑的 Agent 工程（单仓库多模块）。用户说一句话
（如"每次比心时出现粉色爱心"），系统完成需求理解 → 视频理解 → 剪辑规划 →
素材获取 → 剪辑执行 → 多轮反馈修改的完整链路。

产品总体规划见 [docs/产品总体需求.md](docs/产品总体需求.md)。

## 模块与状态

| 模块 | 包 | 状态 | 设计文档 |
|---|---|---|---|
| 一、自然语言需求理解 | `gesture_intent` | ✅ 已实现 | [docs/模块一-自然语言需求理解.md](docs/模块一-自然语言需求理解.md) |
| 二、视频理解 | `video_understanding` | ✅ 已实现（核心链路 + 规则检测器；CV/音频重模型为可选适配器） | [docs/模块二-视频理解.md](docs/模块二-视频理解.md) |
| 三、剪辑规划 | `editing_planner` | ✅ 已实现（两阶段 Plan + 无障碍校验 + LLM 创意接缝） | [docs/自然语言驱动视频剪辑 Agent——模块三：剪辑规划模块工程设计文档.md](docs/自然语言驱动视频剪辑%20Agent——模块三：剪辑规划模块工程设计文档.md) |
| 四、素材搜索与管理 | `asset_manager` | ✅ 已实现（解析路由 + 三级 Provider + 检测/去重/过滤/排序 + Registry/Binding + LLM 查询改写接缝） | [docs/自然语言驱动视频剪辑 Agent——模块四：素材搜索与管理模块工程设计文档.md](docs/自然语言驱动视频剪辑%20Agent——模块四：素材搜索与管理模块工程设计文档.md) |
| 五、剪辑工具执行 | `edit_executor` | ✅ 已实现（Milestone 5.1 Executor Core + 5.2 Stateful Execution/MemoryBackend；JianYing 5.3 与 FFmpeg 资源 5.4 为接缝占位） | [docs/Module 5 剪辑工具执行模块设计文档.md](docs/Module%205%20剪辑工具执行模块设计文档.md) |
| 六、反馈与持续修改 | `session_feedback`（预留名） | 未开始 | — |

各模块为 `src/` 下的独立顶级包（setuptools 自动发现），模块间单向依赖：
下游可 import 上游包的数据模型（如模块二消费模块一的 `required_video_queries`），
不允许反向依赖。

## 目录结构

```
.
├── docs/                  # 产品需求与各模块工程设计文档
├── examples/              # 示例输入（request.json 等）
├── src/
│   ├── gesture_intent/        # 模块一：自然语言需求理解
│   ├── video_understanding/   # 模块二：视频理解（按 docs/模块二 §5 六能力划分子包）
│   ├── editing_planner/       # 模块三：剪辑规划（两阶段 Plan + 无障碍策略）
│   ├── asset_manager/         # 模块四：素材搜索与管理（Provider/编译/Registry）
│   └── edit_executor/         # 模块五：剪辑工具执行（Desired State + Diff + Revision）
├── tests/
│   └── intent/            # 各模块测试按短名分目录（intent/、video/、planner/、asset_manager/、executor/），文件 basename 保持全局唯一
├── data/                  # 素材/分析缓存（gitignore）
└── workspace/             # 运行产物：state.json / history.jsonl / 输出工程（gitignore）
```

---

## 模块一：自然语言需求理解 `gesture_intent`

将用户的剪辑需求转换为结构化的 `EditingIntent`（初始编辑意图）或
`IntentPatch`（增量补丁），供下游计划器、视频理解模块执行。

采用 **LLM 优先、确定性规则兜底** 的双抽取策略：默认使用确定性规则解析，
配置 API Key 后自动升级为 OpenAI-compatible Chat Completions，失败时回退到规则。
全链路仅依赖标准库 + Pydantic，兼容 Pydantic 1.10 与 2.x。

### 功能特性

- **结构化意图输出**：`EditingIntent` / `IntentPatch`，分别覆盖初始编辑与多轮增删改。
- **四类需求拆分**：全局意图（theme/mood/style/pacing/color/platform）、
  对象需求（背景/音乐/文字/贴纸/特效）、事件绑定需求（手势/身体动作/视频结构/音频事件/姿态条件）、
  显式操作（定格/缩放/音量/替换/删除）。
- **确定性规范化**：中文同义词 → 规范名（canonical）+ 标签（tags），保留原文 raw。
- **引用解析**：把"第二个爱心 / 最后那个文字 / 当前音乐"解析为具体 `object_id`，
  无法解析时进入 `unresolved` 而非丢弃。
- **后处理检查**：`required_video_queries`（生成姿态/音频/事件检测查询）、
  `conflicts`（时长冲突、原音乐冲突、文字禁用、删改冲突）。
- **状态应用与持久化**：`IntentStateManager.apply_patch` 合并补丁到当前意图；
  `IntentStore` 以 `state.json`（原子写）+ `history.jsonl`（追加式）落盘。
- **命令行接口**：`gesture-intent parse` 读取 JSON（文件或 stdin），输出 JSON。

### 架构

```
输入层     cli.py / __main__.py  ── 读取 JSON（IntentParserInput）
    │
编排层     parser.py  ── classify_request → 抽取回退（LLM → 规则）
    │        ├─ extractors.py   规则抽取器 RuleBasedExtractor / OpenAI-compatible 适配器
    │        ├─ canonicalizer.py 同义词表 + 事件归类 + 分句（确定性规范化）
    │        ├─ models.py       Pydantic 数据模型（v1/v2 兼容）
    ▼
解析后处理  resolver.py  ── 引用解析（对象引用 → object_id，产出 resolved/unresolved/affected）
            checks.py   ── normalized_semantics / required_video_queries / conflicts
    ▼
输出层     IntentParserOutput
            ├─ state.py    apply_patch 应用到当前意图（多轮编辑）
            └─ store.py    state.json / history.jsonl 持久化
```

模块依赖方向（无环）：`models.py` 是唯一底座，其余模块均依赖它；
`canonicalizer.py` 只被抽取器复用；`resolver.py` / `checks.py` 只被 `parser.py` 调用。

### 安装

```powershell
# 以可编辑方式安装（安装全部模块包）
python -m pip install -e .
```

环境要求：Python 3.11+，Pydantic 1.10 或 2.x，无其他第三方依赖。

### 用法

#### 命令行

```powershell
# 从文件读取
gesture-intent parse --input examples/request.json --pretty

# 从 stdin 读取
$env:PYTHONPATH = "src"
$json = '{"user_utterance":"每次比心时出现粉色爱心"}'
$json | python -m gesture_intent parse --input - --pretty
```

#### Python API

```python
from gesture_intent import IntentParser, IntentParserInput, IntentStateManager

# 初始编辑
output = IntentParser().parse(IntentParserInput(user_utterance="背景换成海边"))
print(output.request_type, output.editing_intent)

# 多轮编辑（在已有意图上加补丁）
current = output.editing_intent
patch = IntentParser().parse(IntentParserInput(
    user_utterance="第二个爱心小一点，音乐再小一点",
    current_effective_intent=current,
)).intent_patch
updated = IntentStateManager().apply_patch(current, patch)
```

### 配置（环境变量）

| 变量 | 说明 | 默认 |
|---|---|---|
| `INTENT_LLM_API_KEY` / `OPENAI_API_KEY` | 设置后才启用 LLM 抽取，否则用规则 | 无 |
| `INTENT_LLM_BASE_URL` / `OPENAI_BASE_URL` | OpenAI-compatible 服务地址 | `https://models.sjtu.edu.cn/api/v1` |
| `INTENT_LLM_MODEL` / `OPENAI_MODEL` | 模型名 | `deepseek-reasoner` |
| `INTENT_LLM_RESPONSE_FORMAT` | `json_object` 或 `json_schema` | `json_object` |
| `INTENT_LLM_TIMEOUT` | 请求超时（秒） | `60` |

> 示例（PowerShell）：`$env:OPENAI_API_KEY="..."`。
> 推理模型（如 `deepseek-reasoner`）会自动省略 `temperature` 参数，
> 因为此类模型常拒绝采样控制参数。可复制 `.env.example` 作为起点。

### 已知限制 / 路线图

- 删除路由已接通三类目标（对象需求 / 事件绑定需求 / 显式操作）：解析到
  project-view 对象时按 `event_ref` 回查生成它的事件绑定需求——无序号引用
  （"把爱心删掉"）删整个绑定，带序号引用（"把第二个爱心删掉"）只删该实例；
  关键词匹配出现多候选时进入 `unresolved` 而非静默选择。
- `resolve_references` 采用轻量名词/关键词匹配，对同义词与对象去重能力有限，
  可结合 LLM 语义解析进一步增强。
- 全局意图字段基于"残句"扫描（扣除已被对象/事件需求消费的描述语），
  覆盖面之外的措辞仍可能漏检或误归入全局。

---

## 模块二：视频理解 `video_understanding`

需求驱动（task-oriented）的视频分析：消费模块一输出的 `required_video_queries`，
将用户语言中的动作、手势、姿态、视频结构和音乐节点映射为带 What / When / Where
的语义事件（如"第 2 次比心：start 9.82s / peak 10.35s / end 10.91s"）。

**核心链路纯标准库 + Pydantic**（查询路由、时间聚合、规则检测器、语义状态、
缓存覆盖、失效管理、Semantic View 全部可离线运行）；重模型依赖
（MediaPipe 关键点 / PyAV 解码 / librosa 节奏）以可选 extras 懒加载，
缺失时相关查询记 `failed`（技术错误），与 `not_found`（正常分析但无目标）严格区分（§42）。

### 架构

```
api.py            VideoUnderstanding 门面（§52 八个接口）
    │
preprocessing/    metadata.py   ffprobe / 注入两级降级的元数据（§6）
    │
spatial/          loaders.py    DenseSpatialTracks JSON artifact 加载（§48）
                  snapshot.py   事件快照 / Event Anchor / Protected Region（§27-29）
                  mediapipe_backend.py  MediaPipe+PyAV 真实视频后端（可选）
    │
pose/             conditions.py 条件编译：above/near/crossed/left_of/right_of（§21-23）
                  motion.py     位移/速度/运动能量（§22）
    │
events/           router.py     策略路由：dedicated → pose_rule → open_semantic（§14）
                  registry.py   事件注册表：能力 + 事件级 temporal_config（§20/§26）
                  detectors.py  姿态启发式打分器（heart/point/wave/open/close/
                                turn/move/jump/squat/ending_pose…）
                  aggregator.py 平滑→阈值→分段→缺口合并→最小时长→peak（§19）
                  structural.py video_start/end、first/last_action（§39）
                  open_semantic.py  运动能量提案 + 可插拔 verifier（§24）
    │
audio/            analyzer.py   BPM/beat/downbeat/onset（§33-35，§34 统一协议）：
                  注入数据 → librosa（PyAV 抽 PCM 兜底）→ 纯标准库 WAV 三级降级
    │
state/            manager.py    事件物化：event_uid/display_id/occurrence（§37-38）
                  cache.py      Query Coverage：all ⊇ index/first/last/range（§45-46）
                  view.py       SemanticViewBuilder（§49）
                  store.py      state.json + analysis_artifacts/ 分离落盘（§48/§50-51）
```

### 事件覆盖（MVP §59）

- **手势**：heart_gesture、point_left/right、wave_hand、open/close_both_hands；
  thumbs_up/v_sign/ok_sign/finger_heart 依赖 hand landmarks 轨道——
  轨道缺手部数据时自动触发 HandLandmarker 按需补跑再重试（§11 on-demand），
  分析器不支持手部时记 `failed`。
- **无障碍扩展手势**（残障用户重点适配）：single_hand_heart（单手抱心/贴脸比心）、
  finger_heart（手指比心，landmarks）、hand_raise（抬手）、head_tilt（歪头，
  无需手部）、clap（拍手）。
- **身体动作**：turn_body、move_left/right、jump、squat、stand_up、lean_body、
  approach_camera、ending_pose。
- **Pose Condition**：above/below/near/crossed/left_of/right_of + min_duration；
  手-身距离按躯干尺度归一（渐变打分，阈值真实视频校准）。
- **音频**：bpm、beat、downbeat、music_onset（chorus_start 已注册未实现）。
- **结构**：video_start/end、first_action、last_action。
- **开放语义**：运动能量提案 + 注入式 verifier；未配置 verifier 时 `failed`。

### 无障碍适配（MobilityProfile）

手势舞的重要用户群体包含残障人士（单侧上肢、轮椅坐姿、低幅度运动、
手部震颤等）。`MobilityProfile` 描述主体可动性，输入 `video["profile"]`
显式声明，或由 `spatial.profile.infer_mobility_profile` 从轨道覆盖度
自动推断（`inferred=True`）：

- `available_hands`：单手主体查询双手事件时自动降级为 `single_hand_variant`
  （heart_gesture→single_hand_heart，事件标 `adapted_from`）；无变体时
  `not_found` + note 说明，不误报；
- `posture="seated"`：jump/squat/stand_up 不适用（not_found+note）；
  pose condition 距离归一化改用肩宽（轮椅入框使 person bbox 宽度失真）；
- `amplitude`/`amplitude_scale`：速度与外展阈值按比例缩放，低幅度动作
  不再全员漏检；
- `tremor`：轨迹输出前中值滤波 + 更宽平滑窗；
- `mirrored`：前置镜像自拍 → 轨道加载时互换左右手/腕标签；
- `confidence.sources.data_coverage`：span 内必需部位覆盖率 <0.5 时
  confirmed 自动降为 uncertain——"缺肢体"不等于"没做动作"。

### 用法

```python
from video_understanding import VideoUnderstanding

vu = VideoUnderstanding()
vu.analyze_video({
    "video_id": "v1",
    "metadata": {"duration": 14.82, "fps": 30, "resolution": [1080, 1920]},
    "tracks": {...},   # 稠密轨道 artifact；真实视频路径则走 MediaPipe 后端
    "audio": {"original_audio": {"bpm": 126, "beats": [...], "downbeats": [...]}},
})
results = vu.resolve_queries([
    {"type": "event_detection", "event": "heart_gesture",
     "required_occurrence": {"type": "index", "value": 2}},
])
snap = vu.get_spatial_snapshot(results[0].selected_event_uids[0])
traj = vu.get_spatial_track("head")                      # 平滑轨迹（§9.4）
view = vu.build_semantic_view()                          # 交付 Planner 的精简视图
vu.invalidate({"type": "dependency", "name": "pose_track"})  # §47 失效
# 失效三级：dependency/video → invalidated（事件标记不可用）；
#   model 版本升级 → stale（结果保留可见，不再覆盖新查询，重查重算并取代）；
#   query 单条 / edit 裁剪（no-op）。
# 路径输入的 video_id 为内容哈希（首 256KB+大小），同内容跨路径复用状态。
```

```powershell
# CLI：演示输入（脚本化合成轨道，无 CV 依赖也可跑通）
python examples/make_demo_input.py > examples/demo_input.json
video-understand run --input examples/demo_input.json --pretty
video-understand view --workspace <落盘目录>
```

### 真实视频路径（可选）

```powershell
python -m pip install -e ".[video]"   # av + mediapipe + librosa + scipy
python -m pip install -e ".[audio]"   # 可选：beat_this 真实 downbeat（拉 torch）
python scripts/download_models.py     # MediaPipe .task 模型 → data/models/
```

| 环境变量 | 说明 | 默认 |
|---|---|---|
| `VU_MODEL_DIR` | MediaPipe 模型目录 | `data/models` |
| `VU_WORKSPACE_DIR` | 语义状态/产物工作目录 | `workspace` |
| `VU_TEST_VIDEO` | 集成测试用手势舞视频 | `data/test_video.mp4` |

MVP 范围与验收标准见 [docs/模块二-视频理解.md](docs/模块二-视频理解.md) §59-61。

---

## 模块三：剪辑规划 `editing_planner`

把模块一 `EditingIntent` + 模块二 `SemanticView` + 无障碍画像 + 工具能力
转换为**两阶段、软件无关**的剪辑计划（设计文档 §9-10）：

```text
EditingPlannerInput
    → plan()          → LogicalEditingPlan   （想实现什么）
    → materialize()   → ResolvedEditingPlan  （素材返回后具体怎么实现）
```

- **LLM 只产创作决策**（§19/§61）：GlobalStrategy / StyleSpec 由
  `StructuredCreativePlanner` Protocol 产出（`PLANNER_LLM_*`/`OPENAI_*`
  环境变量启用 OpenAI 适配器，无 key 用规则实现，失败自动回退并记
  `provenance.planner_mode`）；事件选择、event_uid 绑定、时间/空间
  解析、避让、素材去重、能力检查、校验全部确定性代码。
- **无障碍一级场景**（§3-4）：坐姿/轮椅不降级为"站姿 fallback"，而是
  独立策略——P0 脸 → P1 手势区/活跃手 → P2 上躯干 → P3 轮椅/主体区
  的避让优先级、低幅度语义视觉增强（软 pop/局部光/节拍闪，绝不
  镜头摇晃）、震颤平滑跟随、单侧上肢锚点。
- **PlanItem 可追踪**（§11-12）：`plan_key = req_id:event_uid:operation`
  → `plan_item_uid = pln_<sha1[:8]>`，局部重规划按 plan_key 对齐，
  未变需求原样保留（§52/§60 PlanPatch）。
- **能力降级不静默**（§41）：hard 走等价链（tracking→keyframes，
  耗尽即 blocked）；soft 可简化到 static + warning；
  `degradation_applied` 记录每步降级。
- **缺数据不越界**（§46）：未分析事件/缺分割 mask 输出
  `PlannerDependencyRequest` 交 Agent Controller，Planner 不调模块二。

```powershell
editing-planner plan --input examples/planner_input.json --pretty
editing-planner materialize --plan plan.json --bindings bindings.json --view view.json
editing-planner replan --existing plan.json --intent intent.json --patch patch.json --view view.json
```

| 环境变量 | 说明 | 默认 |
|---|---|---|
| `PLANNER_LLM_API_KEY` / `OPENAI_API_KEY` | 设置后才启用 LLM 创意规划 | 无（规则） |
| `PLANNER_LLM_BASE_URL` / `OPENAI_BASE_URL` | OpenAI-compatible 服务地址 | `https://models.sjtu.edu.cn/api/v1` |
| `PLANNER_LLM_MODEL` / `OPENAI_MODEL` | 模型名 | `deepseek-reasoner` |
| `PLANNER_LLM_TIMEOUT` | 请求超时（秒） | `60` |

验收场景（§56-60，见 `tests/planner/`）：每次比心粉色爱心不挡脸、
轮椅+低幅度"更有活力"、皇冠跟头三级降级、坐姿换背景保护轮椅、
"第二个爱心小一点"只更新对应 PlanItem。

---

## 模块四：素材搜索与管理 `asset_manager`

消费模块三 `LogicalEditingPlan.asset_requests`（`AssetRequest[]`），输出
`AssetResolutionResult`：每条请求解析为一个 `BindingRecord` +
交付模块三 materialize 的 `AssetBinding`，或带原因的 `unresolved`。
模块四**不修改 Plan**（§73）；音乐候选缺 BPM/时长时产出
`AssetDependencyRequest`（`audio_analysis`）交 Agent Controller 调度模块二（§47/§84）。

### 解析链路（§8/§14）

```text
exact_reference  → user/local 直查 → inspect → register → bind        （§82）
semantic_search  → policy 归一 → 有序 Provider 集 + 能力过滤（§12/§18）
                 → Query Compiler → 召回 → normalize → inspect → dedup
                 → Hard Filter → Ranking → 选中 → Import Pipeline
                 → Registry → Binding                                  （§14）
```

- **解析路由**：显式 `resolution_mode` 优先；`source_ref` 或
  `source_policy=user_provided` 走 `exact_reference`（文件名/序号引用直查，
  不做语义搜索），其余走 `semantic_search`。
- **三级 Provider**：`user`（项目 Registry 中已导入的用户素材）、
  `local`（`data/asset_library/manifest.json` 索引的本地库，不运行时扫描）、
  `online`（`ASSET_ONLINE_SOURCES` JSON 配置的通用 HTTP JSON adapter——
  搜索只取 metadata/预览，仅选中候选才下载原文件 §24；
  音乐检索仅走 `authorized_for_music` 的源 §46）。
- **Source Policy**（§12/§46）：`user_only / local_only / user_first /
  local_first / online_allowed`（模块三的 `any`/`user_provided` 自动归一）；
  `generated` 不支持生成式素材（§5）；`offline=True` 收紧为 `local_only`。
- **两种检索策略**（§13）：`first_satisfactory` 逐源检索、首个产出可过
  Hard Filter 的候选即停；`best_available` 全部允许源召回后统一排序。
  缺省按类型：sticker/image → first_satisfactory，其余 → best_available。
- **Query Compiler**（§15-16）：输入仅限 `semantic_query` +
  `style_context` + `technical_requirements`，不重新阅读聊天历史；
  默认中英词表改写（`LexiconQueryRewriter`），LLM 走 `QueryRewriter`
  接缝（`ASSET_LLM_*` 启用）且输出过白名单校验，不允许无依据加词。
- **检测与筛选**：本地可读文件做真实检测（格式/尺寸/alpha/时长/hash），
  在线候选标记未验证；`technical_requirements` 拆 required/preferred——
  required 进 Hard Filter，preferred 只进 Ranking（§11/§37）。
- **透明加权排序**（§32-44）：`S = ws·semantic + wt·technical + wv·visual
  + wl·license + wu·usage`，五维权重按 asset_type 区分；
  坐姿/无障碍画像只改 usage 偏好（紧凑、避让、低杂度），
  不往 Query 里塞 wheelchair/disabled（§40/§71）。
- **Import 复核与重试**：下载后实测复核——声称 alpha 实际无 → 拒收（§42）；
  失败沿 ranked 顺序重试至多 3 次，`fallback_used` 记录是否用了备选。
- **Registry / Binding**：项目级 AssetRegistry 统一登记
  （`scope=project`），`usage_index` 按 plan_item 记素材使用；
  Binding 留存 Top-3 alternatives，`switch_alternative` "换一个"
  直接用候选池切换，不重新联网搜（§50/§85）；在线检索结果进
  Search Cache（§69）。
- **顶层状态**：`resolved / resolved_with_warnings / partial / failed`——
  hard 约束未解才计 failed/partial，soft 未解只 warning（§68）。

### 用法

```powershell
asset-manager resolve --requests reqs.json --project p1 --pretty
asset-manager import --file x.png --project p1 --caption "我的贴纸" --tag 爱心
asset-manager switch --request asset_req_sticker_01 --project p1   # 换备选
asset-manager state --project p1                                   # 项目素材状态
```

```python
from asset_manager import AssetManager

mgr = AssetManager(project_id="p1")
result = mgr.resolve_assets(plan.asset_requests)   # AssetRequest[] → bindings
record = mgr.import_user_asset("x.png", caption="我的贴纸", tags=["爱心"])
binding = mgr.switch_alternative("asset_req_sticker_01")
```

| 环境变量 | 说明 | 默认 |
|---|---|---|
| `ASSET_LLM_API_KEY` / `OPENAI_API_KEY` | 设置后启用 LLM 查询改写 | 无（词表改写） |
| `ASSET_LLM_BASE_URL` / `OPENAI_BASE_URL` | OpenAI-compatible 服务地址 | `https://models.sjtu.edu.cn/api/v1` |
| `ASSET_LLM_MODEL` / `OPENAI_MODEL` | 模型名 | `deepseek-reasoner` |
| `ASSET_LLM_RESPONSE_FORMAT` | `json_object` 或 `json_schema` | `json_object` |
| `ASSET_LLM_TIMEOUT` | 请求超时（秒） | `60` |
| `ASSET_ONLINE_SOURCES` | 在线源 JSON 数组配置（adapter_id/endpoint/asset_types/authorized_for_music/headers…） | 无（不在线搜） |

验收场景（§80-84，见 `tests/asset_manager/test_asset_scenario*.py`）：
本地贴纸 first_satisfactory 链路、背景 best_available 多源合并排序、
exact_reference 直查不触发搜索、无显式音乐需求不搜音乐、
音乐候选缺 metadata 产出 `audio_analysis` 依赖请求。

---

## 模块五：剪辑工具执行 `edit_executor`

把模块三 `ResolvedEditingPlan` 落实为真实可编辑工程。核心模型是
**Desired State + Diff + Revision**（设计文档 §4-§83）：Planner 的产物先编译成
与后端无关的 `DesiredProjectGraph`（期望状态），再与当前已提交工程图做
属性级 Diff，生成结构化 `EditOperation` DAG（不是字符串脚本）交给 Backend
执行——同一份计划重放时全部 NOOP，改一个参数只产生对应的最小操作集。

```text
ExecutorInput → compile_graph → DesiredProjectGraph
    → diff_graphs → build_patch (ExecutionPatch / EditOperation DAG)
    → backend.execute → backend.verify → save → commit_candidate
```

- **编译器**（§22-§31）：InitProject → SourceMedia → 六条逻辑轨道预建
  （`trk_<name>`，防对象悬空轨道引用）→ 各 Operation 编译器
  （overlay/track_overlay/text/music/background/freeze…）→ 源时间线
  （无 freeze 单 `tlobj_main_video`；有 freeze 按 source_time 交错切片 +
  恒发 `tlobj_original_audio`，`freeze_audio_policy=silence` → mute_ranges）
  → mutation 应用（target 解析链：plan_item_ref/event/video/audio_track/track，
  未解析记 warning 不静默）→ asset MediaRef 解析 → keyframe 归一 →
  object/graph fingerprint（排除 uid/provenance/backend ref）。
- **身份稳定性**：`tlobj_<sha8(plan_item_uid, role)>`；切片内容派生
  `tlobj_<sha8(src_slice,index,src_start,src_end)>`；系统对象定 uid
  `tlobj_main_video` / `tlobj_original_audio`；执行号 `exe_<NNNNNN>`
  扫 runs/ 目录分配（崩溃安全）。
- **Revision 级原子性**（§57-§72）：每次 apply 写
  `rev_NNNN/{manifest{candidate},graph,state}.json` → verify →
  manifest 翻 committed → `backend_ref.json` → `current_graph.json` →
  `current.json` → `state.json` 的顺序提交；中途崩溃留下未完成
  candidate，下次启动 recover 隔离记录（不删文件），重试复用同一
  revision 号覆盖提交。
- **幂等与 dry_run**：相同计划再 apply → 全 NOOP →
  `status=completed_noop`，revision 不变、不建 candidate；`dry_run`
  返回完整 patch 但零副作用（不占 exe 号、无 lock/history/state 写入）。
- **Preflight**（§85-87）：plan.validation 门 → source_media →
  pending_dependency 项 → asset 检查 → mask/产物检查
  （replace_background 缺 `foreground_subject_mask` 或
  `preserve_mobility_device` 无主体标注 → blocking dependency）→
  capability 对照 backend manifest——mismatch 直接
  `capability_mismatch` 状态，绝不自动降级。
- **Backend 接缝**（§40-§50）：`BackendProtocol` 全签名
  （probe/describe_capabilities/begin_session/execute/verify/save/export）；
  `MemoryBackend` 全量模拟（sim.json，13 项能力全 supported，
  `capabilities_override` 可翻转任意能力、`fail_plan` 注入失败供
  恢复测试）；`FFmpegBackend`/`jianying` 为 5.4/5.3 占位存根。
- **EditView**（§75-76）：`get_edit_view` 输出面向模块六的编辑视图
  （display_id、editable_properties 按 role 取表、current_properties）。

```powershell
edit-executor compile --input executor_input.json
edit-executor apply --input executor_input.json --workspace ws --backend memory
edit-executor inspect --workspace ws --project p1
edit-executor revisions --workspace ws --project p1
edit-executor edit-view --workspace ws --project p1
```

```python
from edit_executor import EditingExecutor

executor = EditingExecutor(workspace_root="ws")           # 默认 MemoryBackend
graph = executor.compile(executor_input)                  # ExecutorInput → graph
result = executor.apply(executor_input)                   # 全链：preflight→commit
view = executor.get_edit_view("p1")                       # 模块六消费的编辑视图
```

九组验收场景（§94，见 `tests/executor/`）：双爱心首跑全 CREATE、
改 scale 单 `set_transform` 其余 NOOP、换素材 `replace_media` uid 不变、
删需求 DELETE、keyframes 能力缺失 → capability_mismatch 不降级、
replace_background 缺蒙版 → blocking dep、freeze 交错切片、
二跑全 NOOP/completed_noop、中败 candidate 隔离后恢复。

真实视频端到端实测（超算 Slurm，2026-09）：`test_video.mp4`（32.5s 手势舞，
MediaPipe+librosa 真实分析）走通模块一~五全链——LLM 意图解析「每次比心
时出现粉色爱心」→ 检出 4 次比心事件（profile 自动推断 seated）→ LLM
规划产出贴纸 PlanItem + 素材请求 → 模块四导入并绑定真实 PNG 贴纸 →
`apply` 提交 rev_0001，贴纸对象精确落在比心事件时间点
（3.27–6.77s / 28.73–32.23s）。冒烟脚本在超算
`it_stu100_home/e2e_real_video.py`，产物在 `workspace/e2e_real/`。

---

## 测试

```powershell
python -m pytest
```

覆盖（模块一）：初始请求拆分、发生次数（首次/末次/每次/第 N 次/范围）、
时态关系（前/时/过程/后）、姿态与音频事件查询、引用解析与未解析、
冲突检测、LLM 失败回退、OpenAI 适配器、状态应用、JSON 存储往返、CLI 往返。

覆盖（模块二，`tests/video/`）：时间聚合器（平滑/缺口合并/最小时长/peak）、
条件编译（above/near/crossed/left_of/right_of）、规则检测器（比心两次发生、
指向左右方向、ending_pose、landmark 依赖）、缓存覆盖与增量分析、
not_found/failed 区分、空间快照与轨迹、音频事件物化、结构化事件、
失效管理（依赖/视频/裁剪）、状态持久化往返、CLI 往返、模块一查询对接、
无障碍适配（profile 推断、单手降级、坐姿归一化、低幅度阈值、震颤平滑、
镜像修正、覆盖度降级）。

覆盖（模块三，`tests/planner/`）：模型双版本往返与校验、occurrence 展开
（全/首末/序号/范围/between）、缺失与 uncertain 事件→依赖请求、
时间锚点与 freeze 移位表、节拍对齐阈值、空间关系偏移与 P0-P3 避让、
跟随/震颤/单手锚点、能力降级矩阵（§58）、无障碍校验十项、
§56-60 五个验收场景端到端、PlanPatch 局部重规划、save/load、CLI 往返、
LLM 越权输出白名单拦截。

覆盖（模块四，`tests/asset_manager/`）：模型往返与校验、Resolution Router
（exact_reference vs semantic_search 路由与暗示）、Source Policy 归一/
别名/offline 收紧/generated 拒绝、Provider 能力过滤、Query Compiler
（词表改写、required/preferred 拆分、负词、LLM 改写接缝与白名单）、
候选管线（normalize/inspect/dedup/hard filter/rank/选用分）、
Registry/Binding/usage_index/alternatives 切换/Search Cache、
Import Pipeline 复核拒收、依赖请求产出、顶层状态聚合、CLI 往返、
§80-84 验收场景端到端、坐姿画像不改语义主题、真实 LLM 端点语料实测。

覆盖（模块五，`tests/executor/`）：模型往返与 fingerprint 排除项、
编译（overlay/text/music/effect、replace_background 双对象、系统对象）、
源时间线（freeze 交错切片、silence→mute_ranges）、mutation 目标解析与
replace_music 兜底、Diff（CREATE/UPDATE/DELETE/NOOP、replace_media
保 uid）、apply 全链（rev/state/lock/索引/history+journal）、幂等重放
completed_noop、崩溃恢复（中败隔离 candidate、重试复用 revision）、
Preflight 各拒绝态、dry_run 零副作用、EditView 字段、跨重启状态一致、
CLI 往返。

新增模块的测试放在 `tests/<模块短名>/` 下（如 `tests/video/`），
各目录内测试文件 basename 需全局唯一（pytest prepend 导入模式要求）。

---

## 许可证

本项目为个人研究/工程原型，未指定许可证。使用时请遵循仓库所有者要求。
