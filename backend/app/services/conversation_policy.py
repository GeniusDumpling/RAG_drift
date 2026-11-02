"""Conservative routing for complete social messages, never greeting prefixes."""

import re
import unicodedata
from typing import Literal

ConversationIntent = Literal["greeting", "thanks", "acknowledgement", "goodbye", "capabilities"]

CAPABILITIES = "我可以检索已入库的无人机资料、对比型号参数，或结合证据分析技术问题。"


def conversation_intent(query: str) -> ConversationIntent | None:
    normalized = unicodedata.normalize("NFKC", query).casefold()
    normalized = re.sub(r"[\s，。！？、,.!?:：；;～~…]+", "", normalized)
    if re.fullmatch(
        r"(?:你好[呀啊哇]?|您好|哈喽|哈啰|嗨|hello|hi|hey|早上好|早安|下午好|晚上好|在吗|有人吗)+",
        normalized,
    ):
        return "greeting"
    if re.fullmatch(
        r"(?:好的)?(?:谢谢|感谢)(?:你|您)?(?:的(?:回答|帮助|解答))?[啦了啊呀]?", normalized
    ):
        return "thanks"
    if normalized in {"好", "好的", "明白了", "了解了", "知道了", "收到", "ok", "okay"}:
        return "acknowledgement"
    if normalized in {"再见", "拜拜", "下次再聊", "bye", "goodbye"}:
        return "goodbye"
    if normalized in {"你是谁", "你能做什么", "你可以做什么", "你有什么功能", "你能帮我做什么"}:
        return "capabilities"
    return None


def conversation_fallback(intent: ConversationIntent) -> str:
    return {
        "greeting": f"你好！{CAPABILITIES}你想了解哪方面？",
        "thanks": "不客气！有其他问题可以继续问。",
        "acknowledgement": "好的，有需要可以继续问。",
        "goodbye": "再见！有需要时随时来聊。",
        "capabilities": CAPABILITIES + "你可以直接提问，也可以在同一对话里继续追问。",
    }[intent]


def explicit_topic_switch(query: str) -> bool:
    return bool(
        re.match(
            r"^\s*(?:换(?:个|一个)话题|另一个问题|新问题(?=$|[：:，,\s])|先不聊这个|先不说这个)",
            query,
        )
    )


def contextual_followup(query: str) -> bool:
    """Only carry a prior question into the fallback search for clear follow-up cues."""
    return bool(
        re.search(
            r"它|他们|它们|这个|那个|该机型|该型号|上述|刚才|前面|之前|继续|详细一点|展开|呢[？?\s]*$"
            r"|\b(?:it|its|that|those|these|continue|more details)\b",
            query,
            re.IGNORECASE,
        )
    )
