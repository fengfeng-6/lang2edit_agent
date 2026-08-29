# gesture-intent

模块一“自然语言需求理解”实现：将中文视频剪辑需求转换为 `EditingIntent` 或 `IntentPatch`。

## 本地运行

```powershell
python -m pytest
$env:PYTHONPATH = "src"
$json = '{"user_utterance":"每次比心时出现粉色爱心"}'
$json | python -m gesture_intent parse --input - --pretty
```

也可以安装为命令行工具：

```powershell
python -m pip install -e .
gesture-intent parse --input request.json --pretty
```

默认使用确定性规则解析。配置 `OPENAI_API_KEY`（或模块专用的
`INTENT_LLM_API_KEY`）后，解析器会优先调用 OpenAI-compatible Chat Completions，失败时自动回退到规则解析。
当前默认服务为 `https://models.sjtu.edu.cn/api/v1`，默认模型为 `deepseek-reasoner`。可选覆盖
`INTENT_LLM_BASE_URL`、`INTENT_LLM_MODEL`；将
`INTENT_LLM_RESPONSE_FORMAT=json_schema` 设置为结构化 JSON Schema 模式。
推理请求超时可通过 `INTENT_LLM_TIMEOUT` 设置，默认 60 秒。

包的主要入口：

```python
from gesture_intent import IntentParser, IntentParserInput, IntentStateManager

output = IntentParser().parse(IntentParserInput(user_utterance="背景换成海边"))
```
