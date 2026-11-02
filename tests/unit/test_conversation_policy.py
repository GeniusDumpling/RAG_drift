import pytest
from app.services.answer_generation import LLMAnswerClient
from app.services.conversation_policy import (
    contextual_followup,
    conversation_intent,
    explicit_topic_switch,
)


@pytest.mark.parametrize(
    "query,intent",
    [
        ("你好", "greeting"),
        (" 您好！ ", "greeting"),
        ("Ｈｉ！", "greeting"),
        ("早上好", "greeting"),
        ("好的，谢谢", "thanks"),
        ("感谢你的解答", "thanks"),
        ("明白了", "acknowledgement"),
        ("再见", "goodbye"),
        ("你能做什么？", "capabilities"),
    ],
)
def test_complete_social_messages_are_routed_without_retrieval(query, intent):
    assert conversation_intent(query) == intent


@pytest.mark.parametrize(
    "query",
    [
        "你好，介绍一下 Mavic 3 Pro",
        "你好\nGPS失效怎么处理？",
        "谢谢，请比较Air 3和Air 3S",
        "你能帮我查一下PX4的GPS配置吗",
        "好的，继续讲参数",
        "它的续航呢？",
        "继续",
        "无人机能做什么",
    ],
)
def test_greeting_or_thanks_with_a_real_question_still_requires_evidence(query):
    assert conversation_intent(query) is None


def test_hello_payload_discards_old_model_topic_and_unrelated_evidence():
    client = LLMAnswerClient(
        base_url="https://test.invalid", api_key="test", model="test", timeout_seconds=5
    )
    payload = client._payload(
        "你好",
        [],
        context="[1] Dragonfly GPS Mavic 3 Pro",
        history=[{"role": "assistant", "content": "Mavic 3 Pro 有三个镜头 [1]"}],
    )
    assert payload["messages"][-1] == {"role": "user", "content": "你好"}
    assert len(payload["messages"]) == 2
    assert all(
        "Mavic" not in m["content"] and "Dragonfly" not in m["content"] for m in payload["messages"]
    )


async def test_rewrite_does_not_call_a_model_for_a_greeting(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Greeting must never invoke rewrite model")

    monkeypatch.setattr("app.services.answer_generation.httpx.AsyncClient", forbidden)
    client = LLMAnswerClient(
        base_url="https://test.invalid", api_key="test", model="test", timeout_seconds=5
    )
    assert (
        await client.rewrite_question("你好", [{"role": "user", "content": "Mavic 3 Pro"}])
        == "你好"
    )


def test_fallback_only_carries_history_for_explicit_followups():
    assert contextual_followup("它有什么限制？")
    assert contextual_followup("续航呢？")
    assert not contextual_followup("Parrot Bebop 2 有什么功能？")
    assert explicit_topic_switch("换个话题，介绍Parrot")
    assert explicit_topic_switch("换一个话题，介绍Parrot")
    assert not explicit_topic_switch("这个版本有什么新问题吗？")
    assert not explicit_topic_switch("它出现了另一个问题，怎么排查？")
