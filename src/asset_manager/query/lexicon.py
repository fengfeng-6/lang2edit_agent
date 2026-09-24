"""检索词表（§10/§15/§21）：canonical 术语与中英别名映射。

Query Compiler 与 Candidate Normalization 共用这一份词表，把
中英文自由文本归一到 canonical 英文术语。刻意小而精——第一阶段
本地库本身就是 curated 小集合（§21），词表覆盖高频贴纸/属性/风格。
"""

from __future__ import annotations

from typing import Dict, List

#: canonical object → 别名（含中文）；key 即 §21 高频素材类目
OBJECT_ALIASES: Dict[str, List[str]] = {
    "heart": ["heart", "爱心", "心形", "比心", "心"],
    "star": ["star", "星星", "五角星", "星"],
    "crown": ["crown", "皇冠", "王冠"],
    "sparkle": ["sparkle", "闪光", "亮晶晶", "闪"],
    "bubble": ["bubble", "泡泡", "气泡"],
    "flower": ["flower", "花朵", "花"],
    "butterfly": ["butterfly", "蝴蝶"],
    "shell": ["shell", "贝壳"],
    "rainbow": ["rainbow", "彩虹"],
    "arrow": ["arrow", "箭头"],
    "music_note": ["music note", "music_note", "音符", "音乐符号"],
    "sun": ["sun", "太阳"],
    "cloud": ["cloud", "云朵", "云"],
    "moon": ["moon", "月亮"],
    "beach": ["beach", "海滩", "海边", "沙滩"],
    "sea": ["sea", "ocean", "大海", "海洋"],
    "sky": ["sky", "天空"],
    "grass": ["grass", "草地", "草坪"],
    "tree": ["tree", "树", "树木"],
    "logo": ["logo", "标志", "徽标"],
    "background": ["background", "背景"],
    "confetti": ["confetti", "彩带", "纸屑"],
    "firework": ["firework", "烟花", "焰火"],
    "gift": ["gift", "礼物", "礼盒"],
    "balloon": ["balloon", "气球"],
    "snowflake": ["snowflake", "雪花"],
    "leaf": ["leaf", "叶子", "树叶"],
    "wave": ["wave", "海浪", "浪花"],
    "mountain": ["mountain", "山", "山峰"],
    "city": ["city", "城市", "都市"],
}

#: canonical attribute → 别名（颜色 / 尺寸 / 形态 / 透明度诉求）
ATTRIBUTE_ALIASES: Dict[str, List[str]] = {
    "pink": ["pink", "粉色", "粉红", "粉"],
    "red": ["red", "红色", "红"],
    "blue": ["blue", "蓝色", "蓝"],
    "green": ["green", "绿色", "绿"],
    "yellow": ["yellow", "黄色", "黄"],
    "purple": ["purple", "紫色", "紫"],
    "orange": ["orange", "橙色", "橙", "橘色"],
    "gold": ["gold", "golden", "金色", "金"],
    "white": ["white", "白色", "白"],
    "black": ["black", "黑色", "黑"],
    "small": ["small", "小", "小号", "迷你"],
    "big": ["big", "large", "大", "大号"],
    "compact": ["compact", "紧凑"],
    "transparent": ["transparent", "透明"],
    "round": ["round", "圆", "圆形"],
    "glowing": ["glow", "glowing", "发光", "荧光"],
}

#: canonical style → 别名
STYLE_ALIASES: Dict[str, List[str]] = {
    "cute": ["cute", "可爱", "萌", "卡哇伊"],
    "cartoon": ["cartoon", "卡通", "漫画风"],
    "anime": ["anime", "动漫", "二次元", "日系"],
    "flat": ["flat", "扁平", "平面"],
    "pastel": ["pastel", "粉彩", "少女", "马卡龙"],
    "bright": ["bright", "明亮", "亮色"],
    "soft": ["soft", "柔和", "软"],
    "summer": ["summer", "夏日", "夏天", "夏季"],
    "fresh": ["fresh", "清新", "清爽"],
    "dreamy": ["dreamy", "梦幻"],
    "minimal": ["minimal", "简约", "极简"],
    "retro": ["retro", "复古"],
    "energetic": ["energetic", "upbeat", "轻快", "活力", "动感"],
    "calm": ["calm", "舒缓", "安静", "平静"],
    "warm": ["warm", "温暖", "暖"],
    "cool": ["cool", "酷", "冷色"],
    "realistic": ["realistic", "写实", "真实"],
}

#: 音乐语义标签（§48：semantic tags 参与 ranking）
MUSIC_TAG_ALIASES: Dict[str, List[str]] = {
    "upbeat": ["upbeat", "轻快", "欢快", "明快"],
    "summer": ["summer", "夏日", "夏天"],
    "calm": ["calm", "舒缓", "安静"],
    "energetic": ["energetic", "动感", "激烈"],
    "emotional": ["emotional", "抒情", "感人"],
    "electronic": ["electronic", "电子", "电音"],
    "acoustic": ["acoustic", "原声", "木吉他"],
    "pop": ["pop", "流行"],
    "lofi": ["lofi", "lo-fi", "低保真"],
    "piano": ["piano", "钢琴"],
}


def _invert(table: Dict[str, List[str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for canonical, aliases in table.items():
        out[canonical.lower()] = canonical
        for alias in aliases:
            out[alias.lower()] = canonical
    return out


#: alias（小写）→ canonical；normalizer / compiler 共用
ALIAS_TO_CANONICAL: Dict[str, str] = {}
for _table in (OBJECT_ALIASES, ATTRIBUTE_ALIASES, STYLE_ALIASES, MUSIC_TAG_ALIASES):
    ALIAS_TO_CANONICAL.update(_invert(_table))

OBJECT_CANONICAL = _invert(OBJECT_ALIASES)
ATTRIBUTE_CANONICAL = _invert(ATTRIBUTE_ALIASES)
STYLE_CANONICAL = _invert(STYLE_ALIASES)
MUSIC_TAG_CANONICAL = _invert(MUSIC_TAG_ALIASES)

#: 词表中所有别名按长度降序——中文无分词，靠最长匹配扫串
ALL_ALIASES_LONGEST: List[str] = sorted(
    (a for table in (OBJECT_ALIASES, ATTRIBUTE_ALIASES, STYLE_ALIASES, MUSIC_TAG_ALIASES)
     for aliases in table.values() for a in aliases),
    key=len,
    reverse=True,
)

#: 否定表述（compiler 解析 negative_terms）
NEGATIVE_MARKERS = ["不要", "无", "非", "除去", "without", "no ", "except"]


def canonicalize(term: str) -> str:
    """单词/短语 → canonical；查不到返回小写原词。"""
    return ALIAS_TO_CANONICAL.get(term.strip().lower(), term.strip().lower())
