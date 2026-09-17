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
| 三、剪辑规划 | `editing_planner`（预留名） | 未开始 | — |
| 四、素材搜索与管理 | `asset_manager`（预留名） | 未开始 | — |
| 五、剪辑工具执行 | `edit_executor`（预留名） | 未开始 | — |
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
│   └── video_understanding/   # 模块二：视频理解（按 docs/模块二 §5 六能力划分子包）
├── tests/
│   └── intent/            # 各模块测试按短名分目录（intent/、video/…），文件 basename 保持全局唯一
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
audio/            analyzer.py   BPM/beat/downbeat/onset（§33-35，§34 统一协议）
    │
state/            manager.py    事件物化：event_uid/display_id/occurrence（§37-38）
                  cache.py      Query Coverage：all ⊇ index/first/last/range（§45-46）
                  view.py       SemanticViewBuilder（§49）
                  store.py      state.json + analysis_artifacts/ 分离落盘（§48/§50-51）
```

### 事件覆盖（MVP §59）

- **手势**：heart_gesture、point_left/right、wave_hand、open/close_both_hands；
  thumbs_up/v_sign/ok_sign 已注册，依赖 hand landmarks 轨道（按需 §11）。
- **身体动作**：turn_body、move_left/right、jump、squat、stand_up、lean_body、
  approach_camera、ending_pose。
- **Pose Condition**：above/below/near/crossed/left_of/right_of + min_duration。
- **音频**：bpm、beat、downbeat、music_onset（chorus_start 已注册未实现）。
- **结构**：video_start/end、first_action、last_action。
- **开放语义**：运动能量提案 + 注入式 verifier；未配置 verifier 时 `failed`。

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
失效管理（依赖/视频/裁剪）、状态持久化往返、CLI 往返、模块一查询对接。

新增模块的测试放在 `tests/<模块短名>/` 下（如 `tests/video/`），
各目录内测试文件 basename 需全局唯一（pytest prepend 导入模式要求）。

---

## 许可证

本项目为个人研究/工程原型，未指定许可证。使用时请遵循仓库所有者要求。
