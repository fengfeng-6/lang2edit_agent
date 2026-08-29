# lang2edit_agent — 自然语言视频剪辑意图理解 Agent

一个用中文自然语言驱动视频剪辑的 Agent 工程。本仓库为 **模块一「自然语言需求理解」**：
将用户的剪辑需求（如"每次比心时出现粉色爱心"）转换为结构化的
`EditingIntent`（初始编辑意图）或 `IntentPatch`（增量补丁），
供下游计划器、视频理解模块执行。

采用 **LLM 优先、确定性规则兜底** 的双抽取策略：默认使用确定性规则解析，
配置 API Key 后自动升级为 OpenAI-compatible Chat Completions，失败时回退到规则。
全链路仅依赖标准库 + Pydantic，兼容 Pydantic 1.10 与 2.x。

---

## 功能特性

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

---

## 架构

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

详细设计见同仓库的《产品总体需求》《模块一：自然语言需求理解模块工程设计文档》。

---

## 安装

```powershell
# 以可编辑方式安装
python -m pip install -e .
```

### 环境要求
- Python 3.9+（代码使用了 `X | Y` 类型标注与 `str | None` 语法）
- Pydantic 1.10 或 2.x
- 无其他第三方依赖

---

## 用法

### 命令行

```powershell
# 从文件读取
gesture-intent parse --input request.json --pretty

# 从 stdin 读取
$env:PYTHONPATH = "src"
$json = '{"user_utterance":"每次比心时出现粉色爱心"}'
$json | python -m gesture_intent parse --input - --pretty
```

### Python API

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

---

## 配置（环境变量）

| 变量 | 说明 | 默认 |
|---|---|---|
| `INTENT_LLM_API_KEY` / `OPENAI_API_KEY` | 设置后才启用 LLM 抽取，否则用规则 | 无 |
| `INTENT_LLM_BASE_URL` / `OPENAI_BASE_URL` | OpenAI-compatible 服务地址 | `https://models.sjtu.edu.cn/api/v1` |
| `INTENT_LLM_MODEL` / `OPENAI_MODEL` | 模型名 | `deepseek-reasoner` |
| `INTENT_LLM_RESPONSE_FORMAT` | `json_object` 或 `json_schema` | `json_object` |
| `INTENT_LLM_TIMEOUT` | 请求超时（秒） | `60` |

> 示例（PowerShell）：`$env:OPENAI_API_KEY="..."`。
> 推理模型（如 `deepseek-reasoner`）会自动省略 `temperature` 参数，
> 因为此类模型常拒绝采样控制参数。

---

## 测试

```powershell
python -m pytest
```

覆盖：初始请求拆分、发生次数（首次/末次/每次/第 N 次/范围）、
时态关系（前/时/过程/后）、姿态与音频事件查询、引用解析与未解析、
冲突检测、LLM 失败回退、OpenAI 适配器、状态应用、JSON 存储往返、CLI 往返。

---

## 已知限制 / 路线图

- 删除"显式操作 / 事件绑定需求"的语义尚未接线：`resolver` 目前只把 remove
  路由到 `remove_object_requirement_ids`，对象删除可用，事件/操作删除为待实现项。
- `resolve_references` 采用轻量名词/关键词匹配，对同义词与对象去重能力有限，
  可结合 LLM 语义解析进一步增强。

---

## 许可证

本项目为个人研究/工程原型，未指定许可证。使用时请遵循仓库所有者要求。
