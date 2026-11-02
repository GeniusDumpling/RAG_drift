import pytest
from app.services.search import _direct_optimization, should_skip_optimization

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize(
    "query",
    [
        "Mavic 3 Pro 续航",  # 中文短事实查询
        "PX4 EKF failsafe",  # 英文关键词
        "DJI Mini 4 Pro 电池容量",
        "电机坏了",  # 极短
    ],
)
def test_simple_queries_skip_optimization(query: str) -> None:
    assert should_skip_optimization(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "它的续航怎么样",  # 指代
        "这个机型和那个有什么区别",  # 指代 + 对比
        "Mavic 3 Pro 和 Air 3 对比哪个更适合航拍",  # 对比类
        "为什么我的无人机总是向左漂移，该如何排查电机故障并校准指南针",  # 长多子问题
        "",  # 空
        "   ",  # 空白
        # 超长查询
        "请详细介绍一下大疆 Mavic 3 Pro 的全部硬件规格包括主板芯片型号图传系统"
        "相机传感器电池容量以及它在不同飞行模式下的续航表现和抗风能力表现",
    ],
)
def test_complex_queries_still_use_optimization(query: str) -> None:
    assert should_skip_optimization(query) is False


def test_direct_optimization_passes_query_through() -> None:
    result = _direct_optimization("Mavic 3 Pro 续航")

    assert result.optimized_query_text == "Mavic 3 Pro 续航"
    assert result.trace_summary_json == {"skipped": True, "reason": "simple_query"}
    assert result.query_intent == "fact"
