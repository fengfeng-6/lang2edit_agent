# 自然语言驱动视频剪辑 Agent  
## 模块四：素材搜索与管理模块工程设计文档

## 1. 模块定位

素材搜索与管理模块（Asset Search & Management）负责接收剪辑规划模块生成的 `AssetRequest`，在用户素材、系统本地素材库和允许访问的在线素材源中检索符合要求的候选素材，并完成筛选、排序、下载、校验、注册和绑定。

模块核心解决的问题为：

\[
\text{AssetRequest}
\rightarrow
\text{Candidate Assets}
\rightarrow
\text{Selected Asset}
\rightarrow
\text{AssetBinding}
\]

LaTeX：

```latex
\text{AssetRequest}
\rightarrow
\text{Candidate Assets}
\rightarrow
\text{Selected Asset}
\rightarrow
\text{AssetBinding}
```

本模块第一阶段不承担生成式素材生产，只处理：

```text
User Assets
+
Local Asset Library
+
Online Assets
```

其核心定位不是通用互联网搜索，而是：

> 为已经确定的剪辑计划找到真正可用、质量合格、技术规格匹配且来源可追踪的素材，并将其转换为稳定的项目资产。

---

# 2. 第一阶段设计原则

Module 4 第一阶段遵循以下原则：

1. 素材需求必须来自 Module 3 的 `AssetRequest`；
2. Module 4 不重新解释用户剪辑意图；
3. 生成式图片、生成式音乐和其他生成式素材暂不支持；
4. 音乐只有在用户明确提出音乐需求时才进入素材流程；
5. 原视频已有音乐且用户未提出修改时，默认保留；
6. 原视频没有音乐且用户未提出要求时，不主动添加 BGM；
7. 在线搜索是本地素材不足时的补充，而不是所有请求的默认路径；
8. 候选素材与正式项目素材严格分离；
9. 所有正式使用素材都必须进入 Asset Registry；
10. Module 4 不修改 Editing Plan，只返回 `AssetBinding` 和素材状态。

---

# 3. 模块边界

## 3.1 本模块负责

Module 4 负责：

- 用户上传素材解析；
- 用户素材语义检索；
- 系统本地素材库检索；
- 在线图片 / 素材检索；
- 指定素材直接解析；
- 搜索 Query 编译；
- 多 Provider 候选召回；
- Candidate 标准化；
- 素材元数据提取；
- 图片透明背景检查；
- 分辨率和比例检查；
- 素材质量检查；
- 素材去重；
- Hard Requirement 过滤；
- Semantic / Technical / Visual / Usage Ranking；
- 素材下载；
- 素材缓存；
- 项目 Asset Registry；
- AssetBinding 管理；
- Search History 管理；
- 素材使用关系维护；
- 音乐明确需求下的检索与绑定。

---

## 3.2 本模块不负责

Module 4 不负责：

```text
用户意图解析
视频内容理解
剪辑时间规划
素材最终位置计算
动画选择
生成式图片
生成式音乐
生成式视频
剪映工程操作
视频渲染
```

这些分别由其他模块负责。

---

# 4. 第一阶段支持的素材来源

素材来源正式定义为：

```text
AssetSource
├── user
├── local
└── online
```

---

## 4.1 User Assets

表示用户当前项目中明确上传或提供的素材。

例如：

```text
用户自己的图片
Logo
PNG 贴纸
背景图
音乐
音效
```

用户明确指定素材时，应优先使用 User Assets。

---

## 4.2 Local Asset Library

系统维护的精选本地素材库。

主要服务于：

```text
heart
star
crown
sparkle
bubble
flower
butterfly
shell
rainbow
arrow
music_note
```

以及常用：

```text
pop
ding
sparkle
whoosh
beat accent
```

等音效。

本地素材库强调：

```text
精选
稳定
质量可控
元数据可信
版权状态明确
```

而不是无限扩充素材数量。

---

## 4.3 Online Assets

当 User / Local 无法满足需求时，可通过在线素材源继续检索。

第一阶段重点支持：

```text
sticker
image
background image
```

音乐仅在用户明确提出需求时允许访问授权来源。

---

# 5. 不支持生成式素材

第一阶段明确不支持：

```text
generated image
generated sticker
generated music
generated video
```

因此不存在：

```text
GenerationProvider
generation_allowed
generation_preferred
generation fallback
```

相关逻辑。

未来如果需要生成式能力，只作为新的 `AssetProvider` 接入，不改变现有 Asset Registry 和 AssetBinding 协议。

---

# 6. 音乐处理原则

音乐属于用户驱动型素材。

默认规则：

```text
原视频已有音乐
+
用户没有提出音乐需求
→ preserve existing audio
```

如果：

```text
原视频没有音乐
+
用户没有提出音乐需求
→ 不自动添加音乐
```

只有用户明确提出：

```text
换音乐
加 BGM
降低音乐音量
找一首轻快音乐
使用指定音乐
```

时，Module 3 才生成：

```text
AssetRequest(asset_type = music)
```

因此：

\[
\text{Music Asset Request}
\iff
\text{Explicit Music Requirement}
\]

LaTeX：

```latex
\text{Music Asset Request}
\iff
\text{Explicit Music Requirement}
```

---

# 7. MusicStrategy

Module 3 建议使用：

```text
MusicStrategy
├── preserve_existing
├── none
├── add
├── replace
└── adjust
```

Module 4 只处理：

```text
add
replace
```

对应的音乐素材检索。

`adjust` 属于已有素材调整，不需要重新搜索音乐。

---

# 8. Resolution Mode

所有 AssetRequest 进入 Module 4 后，先判断：

```text
resolution_mode
```

第一阶段支持：

```text
exact_reference
semantic_search
```

---

## 8.1 exact_reference

用户明确指定某个已有素材。

例如：

> 用我上传的第二张图。

> 就用这个 PNG。

> 用这首音乐。

流程：

```text
AssetRequest
→ Reference Resolve
→ Inspect
→ Register
→ Bind
```

不进入候选 Ranking。

---

## 8.2 semantic_search

用户只描述素材需求。

例如：

> 找一个可爱的粉色爱心。

> 找一个动漫海滩背景。

流程：

```text
AssetRequest
→ Query Compiler
→ Candidate Retrieval
→ Hard Filter
→ Ranking
→ Selection
→ Binding
```

---

# 9. AssetRequest

Module 4 直接消费 Module 3 定义的：

```text
AssetRequest
```

建议核心字段包括：

```text
AssetRequest
├── request_uid
├── version
├── asset_type
├── media_type
├── resolution_mode
├── source_ref
├── semantic_query
├── style_context
├── technical_requirements
├── usage_context
├── reuse_policy
├── source_policy
├── licensing_policy
├── search_strategy
├── fallback_policy
└── constraint_level
```

---

# 10. Semantic Query

建议：

```text
SemanticQuery
├── raw
├── object
├── attributes
├── style
└── negative_terms
```

例如：

```json
{
  "raw": "可爱的粉色爱心",
  "object": "heart",
  "attributes": ["pink"],
  "style": ["cute"],
  "negative_terms": []
}
```

`raw` 必须始终保留。

结构化字段主要服务：

```text
Query Compilation
Filtering
Ranking
Debug
```

---

# 11. Technical Requirements

正式区分：

```text
required
preferred
```

例如：

```json
{
  "required": {
    "has_alpha": true,
    "min_width": 512,
    "min_height": 512
  },

  "preferred": {
    "aspect_ratio": 1.0,
    "compact_shape": true
  }
}
```

其中：

```text
required
→ Hard Filter
```

而：

```text
preferred
→ Ranking
```

不得混淆。

---

# 12. Source Policy

第一阶段支持：

```text
user_only
local_only
user_first
local_first
online_allowed
```

普通贴纸 / 图片 / 背景默认：

```text
User
→ Local
→ Online
```

用户明确要求：

> 只用我上传的素材。

则：

```text
user_only
```

离线项目则：

```text
local_only
```

---

# 13. Search Strategy

第一阶段支持：

```text
first_satisfactory
best_available
```

---

## 13.1 first_satisfactory

适合高频标准素材。

例如：

```text
heart
star
arrow
crown
```

Local Library 中已有高质量且符合 Hard Requirement 的素材时，可以直接停止继续搜索。

Sticker 默认使用：

```text
first_satisfactory
```

---

## 13.2 best_available

适合更依赖视觉风格的素材。

例如：

```text
summer beach background
anime background
specific thematic image
```

可以从多个 Provider 召回候选，再统一 Ranking。

Background 默认使用：

```text
best_available
```

---

# 14. Module 4 总体检索链路

第一阶段正式链路：

```text
AssetRequest
        ↓
Resolution Router
        ↓
Exact Reference?
├── Yes
│    ↓
│ Direct Resolver
│    ↓
│ Metadata Inspector
│    ↓
│ Asset Registry
│    ↓
│ AssetBinding
│
└── No
     ↓
Query Compiler
     ↓
Source Policy Resolver
     ↓
Candidate Retrieval
     ↓
Candidate Normalization
     ↓
Deduplication
     ↓
Metadata Inspection
     ↓
Hard Filtering
     ↓
Semantic Ranking
     ↓
Quality / Usage Re-ranking
     ↓
Selection
     ↓
Download / Cache
     ↓
Asset Registry
     ↓
AssetBinding
```

---

# 15. Query Compiler

不同 Provider 不共享唯一字符串 Query。

Query Compiler 输出：

```text
ProviderQuery
├── canonical_terms
├── text_query
├── filters
└── negative_terms
```

例如 Local Library：

```json
{
  "canonical_terms": [
    "heart",
    "pink",
    "cute"
  ],

  "filters": {
    "asset_type": "sticker",
    "media_type": "image",
    "has_alpha": true
  }
}
```

Online Provider：

```json
{
  "text_query": "cute pink heart sticker transparent png",
  "negative_terms": []
}
```

Provider 可根据自身检索能力解释 Query。

---

# 16. Query Compiler 中 LLM 的职责

LLM 只负责语义改写和搜索词补充。

输入来源限制为：

```text
AssetRequest.semantic_query
+
Style Context
+
Technical Requirements
```

LLM 可以将：

```text
少女一点的粉色爱心
```

规范为：

```text
pink
cute
soft
pastel
heart
```

但不能无依据加入：

```text
3D
glitter
metallic
animated
```

Query Compiler 不重新阅读整个聊天历史自由创作。

---

# 17. Provider 架构

Provider 负责：

> 根据 ProviderQuery 返回候选素材。

统一接口概念：

```python
class AssetProvider:
    def search(
        self,
        query: ProviderQuery,
        context: SearchContext,
    ) -> list[AssetCandidate]:
        ...
```

第一阶段包含：

```text
UserAssetProvider
LocalLibraryProvider
OnlineAssetProvider
```

---

# 18. Provider Capability

每个 Provider 声明：

```text
ProviderCapability
├── asset_types
├── media_types
├── supports_semantic_search
├── supports_exact_lookup
├── supports_license_filter
└── supports_metadata_filter
```

Source Policy Resolver 根据 Request 先过滤无法处理该需求的 Provider。

---

# 19. UserAssetProvider

支持：

```text
exact reference
filename lookup
tag lookup
semantic search
embedding search
```

用户上传素材在导入时建议生成：

```text
filename
caption
semantic tags
embedding
technical metadata
```

以支持后续自然语言引用。

---

# 20. LocalLibraryProvider

本地系统素材库以 Manifest 为唯一正式索引，不依赖运行时扫描目录猜测内容。

示例：

```json
{
  "asset_uid": "lib_sticker_heart_003",
  "path": "stickers/heart/pink_03.png",

  "asset_type": "sticker",
  "media_type": "image",

  "semantic_metadata": {
    "object": "heart",
    "attributes": ["pink"],
    "style": ["cute", "cartoon"]
  },

  "usage_tags": [
    "gesture_overlay",
    "compact_overlay"
  ],

  "license_metadata": {
    "status": "cleared"
  }
}
```

---

# 21. 本地素材库策略

第一阶段本地素材库强调：

```text
curated
```

而不是：

```text
massive
```

建议每类高频素材：

```text
5–20 个
```

高质量版本即可。

重点包括：

```text
heart
star
crown
sparkle
bubble
rainbow
flower
butterfly
shell
sun
cloud
arrow
music_note
```

---

# 22. Style Family

为提高多素材同时使用时的视觉一致性，建议 AssetRecord 支持：

```text
style_family
```

例如：

```text
pastel_summer_01
cute_flat_01
sparkle_cartoon_01
```

一个 style family 可以包含：

```text
heart
star
cloud
shell
sun
bubble
```

当 Global Strategy 指定：

```text
cute / bright / summer
```

Asset Manager 可以优先从同一 Style Family 中选择多个素材。

---

# 23. OnlineAssetProvider

OnlineAssetProvider 统一管理多个在线素材源：

```text
OnlineAssetProvider
├── ImageSourceAdapter A
├── ImageSourceAdapter B
└── MusicSourceAdapter
```

不同在线服务通过 Adapter 接入，不把具体网站逻辑写入核心 Asset Manager。

---

# 24. Online Candidate 不立即下载原文件

在线搜索阶段优先获取：

```text
metadata
preview_uri
original_uri
```

流程：

```text
Search
→ Preview
→ Ranking
→ Selection
→ Download Original
```

不对全部候选下载高清原素材。

---

# 25. Candidate 与正式素材分离

必须固定：

```text
AssetCandidate
≠
AssetRecord
```

Candidate：

> 可能会被使用。

AssetRecord：

> 已经正式被项目接纳和管理。

只有最终选中的 Candidate 才进入 Registry。

---

# 26. AssetCandidate

建议：

```text
AssetCandidate
├── candidate_uid
├── provider_id
├── provider_version
├── source_type
├── source_ref
├── asset_type
├── media_type
├── preview_uri
├── original_uri
├── semantic_metadata
├── technical_metadata
├── license_metadata
├── retrieval_metadata
└── provenance
```

`candidate_uid` 只需要在当前 Search Record 中稳定。

---

# 27. Candidate Retrieval

多个 Provider 可以独立召回：

```text
User Top-K
Local Top-K
Online Top-K
```

然后统一转换为：

```text
AssetCandidate[]
```

Provider 返回的：

```text
retrieval_score
```

仅作为 Ranking 的一个弱特征，不直接决定最终选择。

---

# 28. Candidate Normalization

不同 Provider 返回字段不同。

必须先标准化为：

```text
object
attributes
style
width
height
aspect_ratio
has_alpha
license
source
```

等统一字段。

后续 Filter / Rank 不应直接处理 Provider 原始响应。

---

# 29. Hard Filtering

Hard Requirement 必须先过滤。

数学上：

\[
C_{\text{valid}}
=
\{a\in C\mid H(a)=1\}
\]

LaTeX：

```latex
C_{\text{valid}}
=
\{a\in C\mid H(a)=1\}
```

只有：

\[
a\in C_{\text{valid}}
\]

才能进入 Ranking。

---

# 30. 第一阶段 Hard Filter

Sticker / Image：

```text
media_type
file decodable
min width
min height
alpha required
license constraints
```

Background：

```text
media_type = image
decodable
min resolution
acceptable aspect ratio
license constraints
```

Music：

```text
audio decodable
duration requirements
license constraints
exact source requirements
```

---

# 31. 背景第一阶段只支持静态图片

第一阶段正式限制：

```text
background media_type = image
```

暂不重点支持：

```text
background video
loop
background animation
```

这样可以减少：

```text
duration
codec
loopability
background beat sync
```

相关复杂度。

---

# 32. Candidate Ranking

第一阶段采用透明的加权排序，而不是复杂学习排序模型。

\[
S(a)
=
w_sS_{\text{semantic}}
+
w_tS_{\text{technical}}
+
w_vS_{\text{visual}}
+
w_lS_{\text{license}}
+
w_uS_{\text{usage}}
\]

LaTeX：

```latex
S(a)
=
w_sS_{\text{semantic}}
+
w_tS_{\text{technical}}
+
w_vS_{\text{visual}}
+
w_lS_{\text{license}}
+
w_uS_{\text{usage}}
```

---

# 33. Semantic Score

建议拆分：

\[
S_{\text{semantic}}
=
\alpha S_{\text{object}}
+
\beta S_{\text{attribute}}
+
\gamma S_{\text{style}}
+
\delta S_{\text{text}}
\]

LaTeX：

```latex
S_{\text{semantic}}
=
\alpha S_{\text{object}}
+
\beta S_{\text{attribute}}
+
\gamma S_{\text{style}}
+
\delta S_{\text{text}}
```

例如：

```text
pink cute heart
```

应优先于：

```text
red realistic heart
```

即使二者 object 都是 heart。

---

# 34. 本地素材语义索引

系统精选本地素材优先使用：

```text
人工 / 半人工标签
+
Embedding
```

而不是完全依赖自动 Caption。

每个素材至少建议具有：

```text
object
attributes
style
usage_tags
style_family
```

本地库数量较少时，这种方式稳定性更高。

---

# 35. 用户素材语义索引

用户上传素材无法要求手动完整标注。

导入时自动建立：

```text
filename
caption
tags
embedding
technical metadata
```

用于：

```text
自然语言引用
semantic search
exact reference resolution
```

---

# 36. 在线素材语义验证

在线搜索结果不能完全相信：

```text
网页标题
网页 tags
搜索排名
```

Top-K Candidate 建议增加：

```text
image-text similarity
```

验证实际图像是否与 AssetRequest 匹配。

Search Engine Score 只能作为弱特征。

---

# 37. Technical Score

Technical Score 主要评价 Preferred Requirements。

例如：

```text
resolution
aspect ratio
alpha quality
file size
format
```

Hard Requirement 已经在 Filter 阶段处理，不应重复靠低分解决。

---

# 38. Visual Score

第一阶段 Visual Score 不做复杂审美评分。

重点评价：

```text
清晰度
边缘质量
主体完整
水印
压缩伪影
透明边缘质量
画面异常
```

即：

> 可用性质量，而不是主观艺术审美。

---

# 39. Usage Score

Usage Score 用于评价：

> 这个素材是否适合当前剪辑用途。

例如小型手势贴纸更偏好：

```text
compact
high foreground occupancy
easy to avoid face
clear visual center
reasonable aspect ratio
```

这对坐姿 / 轮椅手势舞尤其重要，因为画面中可用空间通常更依赖实际主体和活跃手势区域。

---

# 40. Accessibility 在 Module 4 的边界

坐姿 / 轮椅状态不改变素材语义 Query。

例如用户要求：

```text
summer beach
```

即使主体坐轮椅，也不应自动改为：

```text
wheelchair summer beach
disabled summer
```

除非用户明确提出这一主题。

Accessibility 在 Module 4 中主要影响：

```text
usage constraints
shape preference
overlay compactness
background complexity
layout compatibility
```

而不是修改素材内容主题。

---

# 41. Sticker Foreground Occupancy

对于带 Alpha 的素材，可定义：

\[
R_{\text{fg}}
=
\frac{N(\alpha>\tau)}{N_{\text{all}}}
\]

LaTeX：

```latex
R_{\text{fg}}
=
\frac{N(\alpha>\tau)}{N_{\text{all}}}
```

如果：

```text
画布 1024×1024
实际爱心只占中央很小区域
```

则即使分辨率很高，也不适合直接使用。

因此 Sticker Ranking 应考虑：

```text
foreground_occupancy
```

---

# 42. 透明背景验证

不得仅根据：

```text
filename
web description
```

判断透明背景。

应检查真实 Alpha Channel。

定义：

\[
r_{\alpha}
=
\frac{N(\alpha<255)}{N_{\text{pixels}}}
\]

LaTeX：

```latex
r_{\alpha}
=
\frac{N(\alpha<255)}{N_{\text{pixels}}}
```

若：

```text
r_alpha ≈ 0
```

则素材实际上不具有有效透明区域。

---

# 43. 背景 Ranking

背景重点考虑：

```text
semantic match
style match
aspect ratio
resolution
visual complexity
usage compatibility
```

对于：

```text
9:16
```

短视频，应优先选择比例接近的背景。

可抽象：

\[
S_{\text{aspect}}
=
f(r_{\text{asset}},r_{\text{canvas}})
\]

LaTeX：

```latex
S_{\text{aspect}}
=
f(r_{\text{asset}},r_{\text{canvas}})
```

比例差距越大，Technical / Usage Score 越低。

---

# 44. 背景与坐姿主体

Module 3 可以在 `usage_context` 中提供：

```text
subject dominant region
canvas aspect ratio
preferred clutter
```

例如：

```json
{
  "subject_region": "center_lower",
  "preferred_background_clutter": "low",
  "canvas_aspect_ratio": "9:16"
}
```

Module 4 可以据此优先：

```text
主体区域较干净
视觉冲突较少
适合叠加人物
```

的背景。

第一阶段不要求复杂场景美学理解。

---

# 45. 音乐检索

音乐检索只在用户明确需求时执行。

流程：

```text
Explicit Music Requirement
        ↓
Music AssetRequest
        ↓
Exact Reference?
├── Yes
│    ↓
│ Resolve / Inspect / Bind
│
└── No
     ↓
User / Local / Authorized Online
     ↓
Metadata Filter
     ↓
Top-K
     ↓
Audio Analysis
     ↓
Ranking
```

---

# 46. 音乐来源优先级

建议：

```text
用户明确指定
↓
用户上传
↓
本地授权音乐库
↓
明确允许使用的在线音乐源
```

第一阶段不进行：

```text
任意全网音乐搜索
```

---

# 47. 音频分析复用 Module 2

Module 4 不重复实现：

```text
BPM detection
beat detection
downbeat detection
```

需要时产生：

```text
AudioAnalysis Dependency Request
```

由 Orchestrator 调：

```text
VideoUnderstanding.analyze_audio(asset_id)
```

分析结果再参与音乐 Ranking / Planner Materialization。

---

# 48. 音乐 Ranking

MVP 重点考虑：

```text
semantic tags
duration
BPM
energy
license
```

例如：

```text
upbeat
summer
BPM 115–140
duration >= 15s
```

第一阶段不要求复杂音乐 Embedding。

---

# 49. Candidate Selection

最终选择：

\[
a^*
=
\arg\max_{a\in C_{\text{valid}}}
S(a)
\]

LaTeX：

```latex
a^*
=
\arg\max_{a\in C_{\text{valid}}}
S(a)
```

同时建议保留：

```text
Top alternatives
```

而不是只保留最终 Top-1。

---

# 50. Alternatives

AssetBinding 可以保存：

```text
selected
alternatives
```

例如：

```json
{
  "selected": "ast_heart_03",
  "alternatives": [
    "ast_heart_07",
    "ast_heart_11"
  ]
}
```

用户后续：

> 换一个爱心。

可以直接切换 Alternative，而不重新搜索网络。

---

# 51. Candidate Deduplication

第一阶段至少支持两层：

```text
exact hash
perceptual hash
```

Exact Hash 用于完全一致文件。

Perceptual Hash 用于：

```text
同一图片
不同分辨率
重新压缩
轻微格式变化
```

减少重复候选。

---

# 52. Asset Import Pipeline

所有正式素材统一进入：

```text
Raw File
    ↓
Decode Validation
    ↓
Metadata Extraction
    ↓
Hash
    ↓
Deduplication
    ↓
Semantic Indexing
    ↓
Registry Insert
```

用户上传和在线下载素材最终使用同一 Import Pipeline。

---

# 53. AssetCandidate 与 AssetRecord

必须严格区分：

```text
AssetCandidate
≠
AssetRecord
```

Candidate 是搜索候选。

AssetRecord 是正式进入项目管理体系的素材。

只有：

```text
Selected Candidate
```

才转换成：

```text
AssetRecord
```

---

# 54. AssetRecord

建议：

```text
AssetRecord
├── asset_uid
├── source_type
├── provider_id
├── asset_type
├── media_type
├── local_uri
├── original_uri
├── semantic_metadata
├── technical_metadata
├── origins
├── integrity
├── registry_scope
├── style_family
└── lifecycle
```

---

# 55. 稳定 Asset UID

Plan 不直接依赖文件路径，而引用：

```text
asset_uid
```

例如：

```text
ast_a81f93c2
```

建议 UID 可以部分基于内容哈希稳定生成，但必须保存完整：

```text
SHA-256
```

进行完整性校验。

---

# 56. Integrity

建议：

```text
Integrity
├── content_hash
├── perceptual_hash
├── file_size
├── mime_type
├── decodable
└── validated_at
```

Executor 只消费已经通过 Integrity Validation 的 AssetRecord。

---

# 57. 多来源同内容

如果：

```text
local library
online source
user upload
```

实际内容完全一致，不建议建立三个 AssetRecord。

可以共享：

```text
asset_uid
```

同时保存：

```text
origins[]
```

用于来源和版权追踪。

---

# 58. License Metadata

License 信息应与 `origin` 绑定。

因为同一文件从不同来源获得，其许可条件可能不同。

例如：

```json
{
  "origin": {
    "provider_id": "online_source_a",
    "license": {
      "type": "CC-BY",
      "attribution_required": true
    }
  }
}
```

AssetBinding 需要记录当前实际采用的来源。

---

# 59. Registry Scope

第一阶段支持：

```text
project
global
user_library
```

---

## 59.1 project

当前项目专用。

包括：

```text
用户上传素材
在线下载素材
项目临时素材
```

默认都进入 Project Scope。

---

## 59.2 global

系统公共精选素材库。

例如：

```text
标准爱心
星星
皇冠
常用背景
标准音效
```

只有系统维护的精选素材进入。

---

## 59.3 user_library

预留给用户长期私人素材库。

第一阶段 Schema 保留，但无需完整实现。

---

# 60. 物理目录建议

系统 Library：

```text
data/
└── asset_library/
    ├── stickers/
    ├── backgrounds/
    ├── images/
    ├── music/
    └── sound_effects/
```

项目素材：

```text
workspace/
└── <project_id>/
    └── assets/
        ├── imported/
        ├── downloaded/
        └── cache/
```

项目状态：

```text
asset_registry.json
search_history.jsonl
```

---

# 61. Project Asset State

建议：

```text
ProjectAssetState
├── registry
├── bindings
├── search_records
└── usage_index
```

---

# 62. Usage Index

维护：

```text
asset_uid
→ plan_item_uid[]
```

例如：

```text
ast_heart_03
→ pln_heart_A
→ pln_heart_B
→ pln_heart_C
```

这样删除一个 PlanItem 不会误删仍被其他 PlanItem 使用的素材。

---

# 63. AssetBinding

建议：

```text
AssetBinding
├── binding_uid
├── asset_request_uid
├── request_version
├── asset_uid
├── selected_origin
├── selection_score
├── score_breakdown
├── alternatives
├── fallback_used
├── active
├── warnings
└── created_at
```

---

# 64. Binding Version

AssetRequest 修改后不能直接覆盖历史 Binding。

例如：

```text
req_heart v1
→ pink heart

req_heart v2
→ blue heart
```

应生成：

```text
binding v1
binding v2
```

当前版本：

```text
active = true
```

方便 Module 6 后续实现：

```text
Undo
Redo
Replace Asset
```

---

# 65. SearchRecord

每一次真实搜索都建议保存：

```text
SearchRecord
├── search_uid
├── request_uid
├── request_version
├── provider_ids
├── queries
├── candidate_uids
├── rejected_candidates
├── ranked_candidates
├── selected_candidate
├── status
└── diagnostics
```

用于定位：

```text
Query 错误
召回不足
Filter 误删
Ranking 错误
Provider 故障
```

---

# 66. Provider 状态

统一定义：

```text
success
no_result
unavailable
failed
```

其中：

`no_result`：

> Provider 正常执行，但未找到素材。

`unavailable`：

> Provider 当前未配置或不可用。

`failed`：

> Provider 调用出现技术故障。

不能将三者混为一个错误。

---

# 67. Asset Resolution 状态

顶层：

```text
AssetResolutionResult
├── status
├── bindings
├── unresolved_requests
├── provider_warnings
├── search_records
└── dependency_requests
```

状态：

```text
resolved
resolved_with_warnings
partial
failed
```

一个 Provider 失败并不一定代表整个 Asset Request 失败。

---

# 68. Fallback

第一阶段不包含 Generation Fallback。

允许：

```text
User
→ Local
→ Online
```

如果所有允许 Provider 都无法满足 Hard Requirements：

```text
AssetRequest unresolved
```

根据 Module 3 中：

```text
constraint_level
```

决定后续：

```text
hard
→ blocked

soft
→ skip / warning
```

Module 4 不自行修改剪辑计划。

---

# 69. Cache 分类

第一阶段区分：

```text
Search Cache
Preview Cache
Asset Cache
```

### Search Cache

保存：

```text
Query
→ Candidate Metadata
```

### Preview Cache

保存在线候选缩略图。

### Asset Cache

保存正式选中的原始素材。

---

# 70. Project Asset 生命周期

一旦：

```text
Candidate
→ AssetRecord
```

并进入项目 Registry，就不属于普通 Cache。

第一阶段：

```text
Project Asset
→ 项目生命周期内不自动垃圾回收
```

避免 Module 6 后续 Undo / Redo 找不到旧素材。

---

# 71. 无障碍设计边界

Module 4 不根据：

```text
posture = seated
```

改变用户素材主题。

坐轮椅是：

```text
布局条件
主体保护条件
空间使用条件
```

不是：

```text
素材主题标签
```

例如：

> 做成夏日海边风格。

仍搜索：

```text
summer
beach
bright
cute
```

而不是自动加入：

```text
wheelchair
disabled
```

除非用户明确要求相关主题。

---

# 72. 坐姿场景 Usage Preference

Module 3 可以向 Module 4 提供：

```text
preferred_compact_shape
low_visual_clutter
overlay_friendliness
subject_region
gesture_region
```

Module 4 依据这些信息提升：

```text
紧凑
高辨识度
容易避让
主体边界清晰
```

素材的 Usage Score。

---

# 73. 与 Module 3 的接口

Module 3：

```text
AssetRequest[]
```

Module 4：

```text
AssetResolutionResult
```

返回：

```text
AssetBinding[]
```

Module 4 不修改：

```text
PlanItem
TemporalSpec
SpatialSpec
GlobalStrategy
```

---

# 74. 与 Module 2 的接口

Module 4 本身不直接反向调用 Module 2。

音乐需要：

```text
BPM
beat
downbeat
```

时输出：

```text
dependency_request
```

由 Agent Controller 调用：

```text
VideoUnderstanding.analyze_audio(asset_id)
```

然后再继续 Ranking 或 Materialization。

---

# 75. 与 Module 5 的接口

Module 5 不负责：

```text
联网搜索
素材下载
素材质量检查
```

Executor 只消费：

```text
AssetRecord
```

并要求：

```text
asset_uid
→ local readable URI
```

已经成立。

因此 Module 5 可以完全专注于剪辑工程执行。

---

# 76. 推荐目录结构

建议：

```text
src/asset_manager/
├── __init__.py
├── api.py
├── models.py
│
├── resolution/
│   ├── router.py
│   ├── policy.py
│   └── resolver.py
│
├── query/
│   └── compiler.py
│
├── providers/
│   ├── base.py
│   ├── user.py
│   ├── local.py
│   └── online.py
│
├── candidates/
│   ├── normalize.py
│   ├── inspect.py
│   ├── dedup.py
│   ├── filter.py
│   ├── quality.py
│   └── rank.py
│
├── registry/
│   ├── registry.py
│   ├── store.py
│   └── usage.py
│
├── cache/
│   └── manager.py
│
└── bindings/
    └── manager.py
```

---

# 77. 仓库依赖方向

保持现有单向依赖：

```text
gesture_intent
      ↓
video_understanding
      ↓
editing_planner
      ↓
asset_manager
```

Module 4 可以依赖 Module 3 中的：

```text
AssetRequest
AssetBinding
```

Module 3 不依赖 Module 4。

---

# 78. 第一阶段 MVP 范围

正式支持：

```text
素材来源：
User + Local + Online

素材类型：
sticker
image
background image
music
sound effect

背景：
静态图片

音乐：
仅显式需求触发

检索：
exact_reference
semantic_search

Search Strategy：
first_satisfactory
best_available

排序：
Semantic
Technical
Visual
Usage
License

状态：
Asset Registry
Search Record
Asset Binding
Usage Index
```

---

# 79. 第一阶段不支持

暂不重点实现：

```text
生成式素材
生成式音乐
背景视频
自动 LUT 搜索
字体在线搜索
3D 模型
复杂模板包
全网任意音乐搜索
自动素材垃圾回收
复杂学习排序模型
复杂视觉美学评分
```

---

# 80. MVP 验收场景一

Planner 请求：

```text
cute pink heart
transparent required
reuse same asset
```

Module 4：

```text
查 User
↓
无匹配

查 Local
↓
得到多个候选

检查 Alpha
↓
过滤无效候选

Semantic / Usage Ranking
↓
选择 ast_heart_03

Registry
↓
AssetBinding
```

如果本地结果已经满足 `first_satisfactory`，不再访问 Online。

---

# 81. MVP 验收场景二

Planner 请求：

```text
anime summer beach background
9:16
best_available
```

Module 4：

```text
User Candidates
+
Local Candidates
+
Online Candidates
↓
统一标准化
↓
Hard Filter
↓
Semantic Ranking
↓
Aspect Ratio / Visual / Usage Ranking
↓
Selected Background
```

最终将在线素材下载并注册到 Project Registry。

---

# 82. MVP 验收场景三

用户：

> 用我上传的第二张图。

AssetRequest：

```text
resolution_mode = exact_reference
```

Module 4：

```text
resolve
→ inspect
→ register
→ bind
```

不得重新进行语义搜索。

---

# 83. MVP 验收场景四

用户没有提出音乐需求。

即使：

```text
原视频无音乐
```

Module 4 也不会：

```text
搜索音乐
推荐音乐
自动添加音乐
```

整个音乐检索链路不执行。

---

# 84. MVP 验收场景五

用户：

> 把原音乐换成一首轻快、有夏日感的。

Module 3 生成 Music AssetRequest。

Module 4：

```text
User Music
↓
Local Licensed Music
↓
Authorized Online Music
```

生成 Top-K Candidate。

需要 BPM 时：

```text
dependency_request
→ Module 2 audio analysis
```

再完成 Ranking。

---

# 85. MVP 验收场景六

用户：

> 这个爱心不好看，换一个。

如果原 Binding 已保存：

```text
alternatives
```

优先：

```text
switch alternative
```

不立即重新执行 Online Search。

只有现有 Candidate Pool 无合适候选时才重新搜索。

---

# 86. 第一阶段验收标准

Module 4 MVP 至少满足：

1. 能消费 `AssetRequest`；
2. 能区分 exact reference 和 semantic search；
3. 能解析用户指定素材；
4. 能检索用户素材；
5. 能检索系统本地素材库；
6. 能通过统一 Provider 接口访问在线素材；
7. 能统一不同 Provider Candidate；
8. 能提取图片基础元数据；
9. 能校验图片是否可解码；
10. 能真实检查 Alpha；
11. 能执行 Hard Requirement Filter；
12. 能执行素材去重；
13. 能进行 Semantic Ranking；
14. 能进行 Technical Ranking；
15. 能进行 Visual Quality Ranking；
16. 能进行 Usage Ranking；
17. 能为 Sticker 考虑 Foreground Occupancy；
18. 能为 Background 考虑 Canvas Ratio；
19. 能保存 Top Alternatives；
20. 能下载最终在线素材；
21. 能建立 AssetRecord；
22. 能维护稳定 asset_uid；
23. 能维护 Asset Registry；
24. 能维护 AssetBinding；
25. 能维护 SearchRecord；
26. 能维护 Usage Index；
27. 能区分 Provider no_result / unavailable / failed；
28. 能返回 partial / resolved_with_warnings；
29. 不支持生成式素材；
30. 没有明确音乐需求时绝不主动搜索音乐；
31. 坐姿 / 轮椅状态不改变素材主题语义；
32. 正式项目素材在项目生命周期内不自动清理。

---

# 87. 模块最终定义

Module 4 最终承担：

```text
Planner 已经决定需要什么素材
        ↓
判断是否已有明确素材
        ↓
如果有
→ Resolve

如果没有
→ Compile Query
        ↓
查询 User / Local / Online
        ↓
统一 Candidate
        ↓
去重
        ↓
检查元数据
        ↓
过滤 Hard Requirement
        ↓
Semantic / Technical / Visual / Usage Ranking
        ↓
选出最佳素材和备选素材
        ↓
下载 / Import
        ↓
Asset Registry
        ↓
AssetBinding
        ↓
交回 Planner Materializer
```

其核心职责不是重新设计剪辑内容，而是：

> **把 Planner 中抽象的素材需求，稳定地解析为真实、可用、可追踪、可复用的项目资产。**

Module 4 是整个系统从“逻辑剪辑计划”进入“可执行剪辑工程”的素材基础层。