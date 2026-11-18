"""Forum gates and source grounding; optional real isolated Neo4j lifecycle tests."""
# ruff: noqa: F811

import copy
import json
import os

import pytest
from app.core.config import Settings
from app.services.forum_graph import extraction, projection, queries
from app.services.forum_graph.pipeline import plan, run_pipeline
from app.services.forum_graph.schema import Assessment, Span
from app.services.forum_graph.source import SourceNotReady, load_material, map_span, validate_span
from app.services.forum_graph.storage import ForumGraphStore
from app.services.forum_graph.windows import aggregate, make_windows
from app.services.video_graph.usage import TokenUsage

from tests.unit.test_discourse_pipeline import (  # noqa: F401
    engine,
    environment,
    filters,
    processor,
    session,
    snapshot,
    storage,
    topic,
)

pytestmark = pytest.mark.no_db


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        LLM_MODEL="fixture-model",
        LLM_API_KEY="not-real",
        FORUM_GRAPH_NEO4J_URI="bolt://localhost:45970",
        FORUM_GRAPH_NEO4J_PASSWORD="forum-graph-test-password",
        FORUM_GRAPH_ARTIFACTS_DIR=tmp_path,
        FORUM_GRAPH_DEBOUNCE_SECONDS=0,
    )


def forum_data():
    data = topic(3)
    for p, text, author in zip(
        data["post_stream"]["posts"],
        [
            "My Pixhawk with PX4 1.14 reports GPS drift.",
            "Try setting GPS_AUTO_CONFIG to 1.",
            "I set GPS_AUTO_CONFIG to 1 and the GPS drift stopped.",
        ],
        [1, 2, 1],
        strict=True,
    ):
        p.update(raw=text, user_id=author, username=f"user{author}")
    return data


def persist(session, source, run, data):
    raw = snapshot(session, source, run, data)
    processor.normalize_topic(session, source, raw, filters.load_policy(), run)
    task = session.get(storage.ForumTopicTask, (source.id, data["id"]))
    if task is None:
        task = storage.ForumTopicTask(source_site_id=source.id, topic_id=data["id"])
        session.add(task)
    task.snapshot_id, task.process_status, task.capture_status = raw.id, "done", "captured"
    session.commit()
    from app.models.content import ContentItem
    from sqlalchemy import select

    item = session.scalar(
        select(ContentItem).where(ContentItem.metadata_json["topic_id"].as_integer() == data["id"])
    )
    return item


@pytest.fixture
def source(session):
    site, _, run = environment(session)
    data = forum_data()
    item = persist(session, site, run, data)
    return site, run, data, item


class FakeClient:
    """Synthetic responses verify mechanics, never stand in for model quality."""

    calls = []
    gate = "extract"
    fail_stage = None

    def __init__(self, settings, cache, *, cache_only=False, output=None, checkpoint=None):
        self.usage = TokenUsage()
        self.output, self.checkpoint = output, checkpoint

    def heartbeat(self):
        if self.checkpoint:
            self.checkpoint(self.output)

    async def request(self, stage, system, payload):
        current = payload["current"]
        pid = current["post_id"]
        self.calls.append((stage, pid))
        if stage == self.fail_stage:
            raise RuntimeError("injected provider failure")
        ref = dict(post_id=pid, quote=current["text"])
        root_ref = (
            ref
            if pid == 101
            else dict(
                post_id=101,
                quote=next(c["text"] for c in payload["context"] if c["post_id"] == 101),
            )
        )
        if stage == "assessment":
            if self.gate != "extract":
                return dict(
                    uav_technical_related=True,
                    has_graph_value=False if self.gate == "skip" else None,
                    decision=self.gate,
                    reason_code="no_usable_information"
                    if self.gate == "skip"
                    else "insufficient_context",
                    reason="fixture gate",
                    value_types=[],
                    supporting_spans=[],
                )
            return dict(
                uav_technical_related=True,
                has_graph_value=True,
                decision="extract",
                reason_code="technical_information",
                reason="fixture technical information",
                value_types=["symptom" if pid == 101 else "feedback"],
                supporting_spans=[ref],
            )
        if stage == "entities":
            if pid == 103:
                return dict(entities=[], cases=[])
            entity = dict(
                key="board",
                surface_text="Pixhawk",
                canonical_name="Pixhawk",
                entity_type="product",
                scope="case",
                case_anchor_post_id=101,
                evidence=[root_ref],
                identity_evidence=[root_ref],
            )
            cases = (
                [
                    dict(
                        anchor_post_id=101,
                        description="Reporter's flight controller",
                        conditions={"firmware": "1.14"},
                        evidence=[root_ref],
                    )
                ]
                if pid == 101
                else []
            )
            if pid == 101:
                entities = [
                    entity,
                    dict(
                        key="fault",
                        surface_text="GPS drift",
                        canonical_name="GPS drift",
                        entity_type="fault",
                        scope="case",
                        case_anchor_post_id=101,
                        evidence=[ref],
                        identity_evidence=[ref],
                    ),
                ]
            else:
                entities = [entity]
            return dict(cases=cases, entities=entities)
        if stage.endswith("review"):
            return dict(
                decisions=[
                    dict(key=k, accept=True, reason="fixture reviewed")
                    for k in payload["expected_keys"]
                ]
            )
        if stage == "claims" and pid == 101:
            return dict(
                claims=[
                    dict(
                        key="symptom",
                        subject="board",
                        predicate="reports_fault",
                        object="fault",
                        kind="relation",
                        assertion="asserted",
                        act="observation",
                        case_anchor_post_id=101,
                        evidence=[ref],
                    )
                ],
                evaluations=[],
            )
        if stage == "claims" and pid == 102:
            return dict(
                claims=[
                    dict(
                        key="action",
                        subject="board",
                        predicate="instruction",
                        value=current["text"],
                        kind="instruction",
                        assertion="asserted",
                        act="recommendation",
                        case_anchor_post_id=101,
                        evidence=[ref],
                    )
                ],
                evaluations=[],
            )
        if stage == "feedback":
            target = next(
                c["id"]
                for c in payload["available_claims"]
                if c["candidate"]["act"] == "recommendation"
            )
            return dict(
                claims=[],
                evaluations=[
                    dict(
                        key="result",
                        targets=[target],
                        case_anchor_post_id=101,
                        outcome="reported_success",
                        evidence=[ref],
                    )
                ],
            )
        raise AssertionError((stage, pid))


@pytest.fixture(autouse=True)
def reset_client():
    FakeClient.calls = []
    FakeClient.gate = "extract"
    FakeClient.fail_stage = None


async def extract_all(material, settings, tmp_path):
    policy = extraction.policy_id(settings)
    windows = make_windows(material)
    client = FakeClient(settings, tmp_path)
    output = dict(policy=policy, assessments={}, windows={})
    for window in windows:
        value = await extraction.assess(material, window, client)
        output["assessments"][window.id] = extraction.assessment_record(
            material, window, policy, value
        )
    decision = aggregate(windows, {k: v["value"] for k, v in output["assessments"].items()})
    for window in windows:
        if window.id in decision["extract_windows"]:
            output["windows"][window.id] = await extraction.extract_window(
                material, window, client, policy, output["windows"]
            )
    return windows, output


def test_strict_assessment_rejects_extract_without_both_conditions():
    with pytest.raises(ValueError):
        Assessment(
            uav_technical_related=True,
            has_graph_value=False,
            decision="extract",
            reason_code="technical_information",
            reason="not enough",
            supporting_spans=[],
        )


def test_source_reads_complete_posts_without_embedding_context(session, source):
    _, _, _, item = source
    material = load_material(session, item.id)
    assert len(material.posts) == 3
    assert material.post(103).author_id == "1"
    assert material.post(103).reply_to == 2
    assert "Question:" not in material.post(103).text
    for post in material.posts:
        span = validate_span(material, Span(post_id=post.id, quote=post.text))
        assert map_span(material, span)


def test_pending_latest_capture_is_not_a_current_source(session, source):
    site, _, _, item = source
    task = session.get(storage.ForumTopicTask, (site.id, 7))
    task.process_status = "pending"
    session.commit()
    with pytest.raises(SourceNotReady):
        load_material(session, item.id)


def test_long_topic_assessment_must_cover_tail_before_skip(session, source):
    material = load_material(session, source[3].id)
    windows = make_windows(material, max_chars=100)
    skipped = Assessment(
        uav_technical_related=True,
        has_graph_value=False,
        decision="skip",
        reason_code="no_usable_information",
        reason="no information",
    ).model_dump()
    with pytest.raises(ValueError):
        aggregate(windows, {windows[0].id: skipped})
    values = {w.id: skipped for w in windows}
    assert aggregate(windows, values)["decision"] == "skip"


async def test_reviewed_projection_case_feedback_and_shared_scope(
    session, source, settings, tmp_path
):
    material = load_material(session, source[3].id)
    windows, output = await extract_all(material, settings, tmp_path)
    rows, publication = projection.compile_output(
        material, windows, output, output["policy"], "build"
    )
    assert len(publication.facts) == 2
    assert len(rows["nodes"]["FGPost"]) == 3
    assert len(rows["nodes"]["FGEvaluation"]) == 1
    assert any(e["kind"] == "EVALUATES" for e in rows["edges"])
    action = next(f for f in publication.facts if f.kind == "instruction")
    assert action.evidence[0].locator["block_kind"] == "text"
    assert action.evidence[0].locator["mapping_version"] == "forum-source-v1"
    assert action.evidence[0].locator["build_id"] == "build"
    assert action.evidence[0].locator["review_version"] == output["policy"]
    assert action.qualifiers["modality"] == "recommendation"
    assert "case_id" in action.qualifiers
    assert action.evidence[0].locator["post_id"] == 102
    assert action.evidence[0].locator["evaluations"][0]["outcome"] == "reported_success"


async def test_skip_and_defer_never_extract(session, source, settings, tmp_path):
    material = load_material(session, source[3].id)
    for gate in ("skip", "defer"):
        FakeClient.calls = []
        FakeClient.gate = gate
        _, output = await extract_all(material, settings, tmp_path)
        assert not output["windows"]
        assert all(stage == "assessment" for stage, _ in FakeClient.calls)


async def test_source_edit_invalidates_receipts(session, source, settings, tmp_path):
    material = load_material(session, source[3].id)
    windows, output = await extract_all(material, settings, tmp_path)
    changed = material.model_copy(update={"title": "Other context"})
    with pytest.raises(ValueError):
        projection.compile_output(changed, make_windows(changed), output, output["policy"], "build")


async def test_tampered_review_or_outcome_cannot_publish(session, source, settings, tmp_path):
    material = load_material(session, source[3].id)
    windows, output = await extract_all(material, settings, tmp_path)
    modified = copy.deepcopy(output)
    record = next(v for v in modified["windows"].values() if v["proposal"]["claims"])
    record["proposal"]["claims"][0]["assertion"] = "negated"
    with pytest.raises(ValueError):
        projection.compile_output(material, windows, modified, modified["policy"], "build")


@pytest.fixture
def store(settings):
    uri = os.getenv("FORUM_GRAPH_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("set FORUM_GRAPH_TEST_NEO4J_URI to isolated localhost:45970")
    if uri != "bolt://localhost:45970":
        raise RuntimeError("Refusing non-isolated forum test graph")
    with ForumGraphStore(settings) as graph:
        # Never erase unrelated data even if a test URL has been misconfigured.
        foreign = graph.read(
            "MATCH(n) WHERE NOT any(label IN labels(n) WHERE "
            "label STARTS WITH 'FG' OR label STARTS WITH 'KG') RETURN count(n) AS n"
        )
        if foreign[0]["n"]:
            raise RuntimeError("Refusing to clear a graph containing foreign nodes")
        with graph.transaction() as tx:
            tx.run("MATCH(n) DETACH DELETE n").consume()
        graph.initialize()
        yield graph
        with graph.transaction() as tx:
            tx.run("MATCH(n) DETACH DELETE n").consume()


async def run(settings, store, engine, tmp_path, *, name="run", **kwargs):
    return await run_pipeline(
        settings,
        store,
        engine,
        manifest=tmp_path / f"{name}.plan.json",
        report=tmp_path / f"{name}.report.json",
        cache=tmp_path / "cache",
        client_factory=FakeClient,
        **kwargs,
    )


async def test_real_graph_pipeline_readback_and_idempotence(
    session, source, settings, engine, store, tmp_path
):
    result = await run(settings, store, engine, tmp_path)
    assert result["status"] == "success", result
    sid = load_material(session, source[3].id).source_id
    assert queries.verify(store, session, sid)["verified"]
    items = queries.retrieve(store, session, "GPS")["items"]
    assert items
    first_calls = list(FakeClient.calls)
    again = await run(settings, store, engine, tmp_path)
    assert again["status"] == "success"
    assert FakeClient.calls == first_calls


async def test_skip_clears_old_active_but_preserves_source(
    session, source, settings, engine, store, tmp_path
):
    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    site, event, data, item = source
    data["post_stream"]["posts"][0]["raw"] += " No useful information remains."
    persist(session, site, event, data)
    FakeClient.gate = "skip"
    FakeClient.calls = []
    result = await run(settings, store, engine, tmp_path, name="skip")
    assert result["status"] == "success", result
    assert result["results"][0]["status"] == "skipped"
    assert all(stage == "assessment" for stage, _ in FakeClient.calls)
    assert load_material(session, item.id) is not None
    assert not queries.retrieve(store, session)["items"]


async def test_provider_failure_resumes_only_missing_stage(
    session, source, settings, engine, store, tmp_path
):
    FakeClient.fail_stage = "claims"
    result = await run(settings, store, engine, tmp_path)
    assert result["status"] == "incomplete"
    FakeClient.fail_stage = None
    FakeClient.calls = []
    resumed = await run(settings, store, engine, tmp_path, retry_failed=True)
    assert resumed["status"] == "success", resumed
    assert not any(stage == "assessment" for stage, _ in FakeClient.calls)


async def test_withdraw_restore_requires_reactivation(
    session, source, settings, engine, store, tmp_path
):
    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    sid = load_material(session, source[3].id).source_id
    store.withdraw(sid)
    assert not queries.retrieve(store, session)["items"]
    store.withdraw(sid, restore=True)
    assert not queries.retrieve(store, session)["items"]
    assert (await run(settings, store, engine, tmp_path))["status"] == "success"


async def test_dry_run_leaves_no_plan_or_model_calls(
    session, source, settings, engine, store, tmp_path
):
    result = await run(settings, store, engine, tmp_path, dry_run=True)
    assert result["status"] == "read_only"
    assert not FakeClient.calls
    assert not (tmp_path / "run.plan.json").exists()
    assert not store.known()


def test_cooked_quotes_keep_original_author_boundaries():
    from app.services.forum_graph.source import raw_post_view

    text, quotes = raw_post_view(
        {
            "cooked": (
                '<p>My report</p><aside class="quote" data-post="2" data-topic="7">'
                "<blockquote><p>Try value 1</p></blockquote></aside><p>It failed</p>"
            )
        },
        7,
    )
    assert "Try value 1" in text and "It failed" in text
    assert any(
        q["target_number"] == 2 and "Try value 1" in text[q["start"] : q["end"]] for q in quotes
    )
    assert all("It failed" not in text[q["start"] : q["end"]] for q in quotes)


def test_code_is_not_mistaken_for_a_quoted_reply():
    from app.services.forum_graph.source import quote_ranges

    raw = (
        '```python\n> output\n[quote="user, post:2, topic:7"]example[/quote]\n'
        "```\n\n> real citation"
    )
    quotes = quote_ranges(raw, 7)
    assert len(quotes) == 1
    assert raw[quotes[0]["start"] : quotes[0]["end"]] == "> real citation"


def test_code_evidence_kind_and_mixed_spans_are_preserved():
    from types import SimpleNamespace

    text = "Observation\n```text\nEKF error\n```\nFollow-up"
    post = SimpleNamespace(text=text, quotes=[])
    start = text.index("EKF error")
    code = Span(post_id=1, quote="EKF error", start=start, end=start + 9)
    assert projection.span_kind(post, code) == "code"
    assert projection.span_kind(post, Span(post_id=1, quote=text, end=len(text))) == "mixed"


def test_numeric_grounding_preserves_sign_and_version():
    from app.services.forum_graph.extraction import numeric_support

    evidence = [Span(post_id=1, quote="Firmware 1.14.0 uses value -1.")]
    numeric_support({"firmware": "1.14.0", "value": "-1"}, evidence)
    for wrong in ["1", "1.14", "0", "+1"]:
        with pytest.raises(ValueError):
            numeric_support({"value": wrong}, evidence)


def test_global_alias_is_compatible_with_shared_software_identity():
    from app.services.forum_graph.schema import EntityCandidate
    from app.services.forum_graph.source import Material, Post

    material = Material(
        content_item_id="x",
        source_site_id="s",
        forum="px4",
        base_url="https://discuss.px4.io",
        topic_id=7,
        title="QGC",
        url="x",
        posts=[
            Post(
                id=1,
                number=1,
                author="u",
                author_id="u",
                text="QGC connects.",
                raw_page_id="x",
                url="x",
            )
        ],
    )
    candidate = EntityCandidate(
        key="q",
        surface_text="QGC",
        canonical_name="QGroundControl",
        entity_type="software",
        scope="global",
        evidence=[Span(post_id=1, quote="QGC")],
        identity_evidence=[Span(post_id=1, quote="QGC")],
    )
    identity = extraction.entity_identity(material, candidate)
    import uuid

    from app.services.video_graph.identity import ENTITY_NAMESPACE

    assert identity["id"] == str(uuid.uuid5(ENTITY_NAMESPACE, "v2:global:software:qgroundcontrol"))


async def test_invalid_candidate_is_rejected_without_discarding_good_claims(
    session, source, settings, tmp_path
):
    class WithBadEntity(FakeClient):
        async def request(self, stage, system, payload):
            result = await super().request(stage, system, payload)
            if stage == "entities" and payload["current"]["post_id"] == 101:
                bad = copy.deepcopy(result["entities"][0])
                bad.update(
                    key="hallucinated",
                    surface_text="invented",
                    canonical_name="invented",
                    evidence=[dict(post_id=101, quote="This was never said")],
                )
                result["entities"].append(bad)
            return result

    m = load_material(session, source[3].id)
    windows = make_windows(m)
    client = WithBadEntity(settings, tmp_path)
    output = dict(assessments={}, windows={})
    policy = extraction.policy_id(settings)
    for w in windows:
        assessment = await extraction.assess(m, w, client)
        output["assessments"][w.id] = extraction.assessment_record(m, w, policy, assessment)
        output["windows"][w.id] = await extraction.extract_window(
            m, w, client, policy, output["windows"]
        )
    rows, publication = projection.compile_output(m, windows, output, policy, "build")
    assert len(publication.facts) == 2
    review = output["windows"][windows[0].id]["entity_review"]
    assert any(d["key"] == "entity:hallucinated" and not d["accept"] for d in review["decisions"])
    assert len(rows["nodes"]["FGEvaluation"]) == 1


async def test_assessment_result_tampering_is_detected(session, source, settings, tmp_path):
    m = load_material(session, source[3].id)
    windows, output = await extract_all(m, settings, tmp_path)
    output["assessments"][windows[0].id]["value"]["reason"] = "Changed outside assessment"
    with pytest.raises(ValueError):
        projection.compile_output(m, windows, output, output["policy"], "build")


def test_deep_context_and_long_blocks_remain_bounded(session, source):
    from app.services.forum_graph.source import Post

    m = load_material(session, source[3].id)
    root = m.posts[0].model_copy(update={"text": "Header\n\n" + "x" * 1000})
    posts = [root]
    for n in range(2, 80):
        posts.append(
            Post(
                id=100 + n,
                number=n,
                author="u",
                author_id="u",
                text="PX4 " * 60,
                reply_to=n - 1,
                raw_page_id=root.raw_page_id,
                url="x",
            )
        )
    m = m.model_copy(update={"posts": posts})
    windows = make_windows(m, max_chars=100, context_chars=300)
    assert all(
        len(w.text) <= 100 and sum(len(c["text"]) for c in w.context) <= 300 for w in windows
    )
    assert all(len(w.context) <= 4 for w in windows if w.post_id != root.id)
    assert any(w.context_incomplete for w in windows)
    for p in posts:
        assert "".join(w.text for w in windows if w.post_id == p.id) == p.text


async def test_new_reply_reassesses_only_new_window(
    session, source, settings, engine, store, tmp_path
):
    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    site, event, data, item = source
    added = copy.deepcopy(data["post_stream"]["posts"][2])
    added.update(
        id=104, post_number=4, reply_to_post_number=3, raw="This is useful PX4 information."
    )
    data["post_stream"]["posts"].append(added)
    data["post_stream"]["stream"].append(104)
    data["posts_count"] = 4
    persist(session, site, event, data)
    FakeClient.calls = []
    FakeClient.gate = "skip"  # This applies only to the newly seen reply.
    result = await run(settings, store, engine, tmp_path, name="append")
    assert result["status"] == "success", result
    assert FakeClient.calls == [("assessment", 104)]
    sid = load_material(session, item.id).source_id
    assert queries.verify(store, session, sid)["verified"]


async def test_skipped_topic_with_new_value_is_not_permanently_excluded(
    session, source, settings, engine, store, tmp_path
):
    FakeClient.gate = "skip"
    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    site, event, data, item = source
    data["post_stream"]["posts"][0]["raw"] += " Extra detailed PX4 diagnosis."
    persist(session, site, event, data)
    FakeClient.gate = "extract"
    result = await run(settings, store, engine, tmp_path, name="new-value")
    assert result["status"] == "success", result
    assert queries.retrieve(store, session, "GPS")["items"]


async def test_recapture_identical_body_keeps_immutable_original_evidence(
    session, source, settings, engine, store, tmp_path
):
    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    site, event, data, item = source
    before = load_material(session, item.id)
    # Keep an additional field in the raw archive so the collector stores a new
    # snapshot without changing semantic text or current chunk mappings.
    data["views"] = 99
    persist(session, site, event, data)
    current = load_material(session, item.id)
    assert before.fingerprint == current.fingerprint
    assert queries.verify(store, session, current.source_id)["verified"]
    FakeClient.calls = []
    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    assert not FakeClient.calls


async def test_source_change_and_native_mutation_fail_closed(
    session, source, settings, engine, store, tmp_path
):
    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    m = load_material(session, source[3].id)
    row = store.active(m.source_id)
    with store.transaction() as tx:
        tx.run(
            'MATCH(b:FGBuild {id:$id})-[:HAS_NATIVE]->(p:FGPost) SET p.data_json="{}"',
            id=row["b"]["id"],
        ).consume()
    assert not queries.verify(store, session, m.source_id)["verified"]
    with pytest.raises(ValueError):
        queries.retrieve(store, session, "GPS")


async def test_expired_worker_cannot_checkpoint_or_publish(
    session, source, settings, engine, store, tmp_path
):
    from app.services.video_graph.material import LeaseLostError

    m = load_material(session, source[3].id)
    policy = extraction.policy_id(settings)
    bid = store.enqueue(m, policy)
    _, token = store.claim(m.source_id, bid)
    store.checkpoint(m.source_id, bid, token, {"saved": True})
    with store.transaction() as tx:
        tx.run("MATCH(b:FGBuild {id:$id}) SET b.lease_until=0", id=bid).consume()
    build, new_token = store.claim(m.source_id, bid)
    assert json.loads(build["output_json"]) == {"saved": True}
    with pytest.raises(LeaseLostError):
        store.checkpoint(m.source_id, bid, token, {})
    store.checkpoint(m.source_id, bid, new_token, {"saved": True, "resumed": True})


async def test_shared_write_failure_rolls_back_native_projection(
    session, source, settings, engine, store, tmp_path, monkeypatch
):
    from app.services.knowledge_graph import storage as shared

    def fail(*args, **kwargs):
        raise RuntimeError("injected shared transaction failure")

    monkeypatch.setattr(shared, "publish", fail)
    result = await run(settings, store, engine, tmp_path)
    assert result["status"] == "incomplete"
    assert not store.read("MATCH(n:FGClaim) RETURN n")
    assert not store.read("MATCH(n:FGPost) RETURN n")
    assert not store.read("MATCH(n:KGFact) RETURN n")


async def test_fixed_plan_target_and_content_are_bound(
    session, source, settings, engine, store, tmp_path
):
    from app.services.forum_graph.source import save_json

    selection = plan(settings, store, engine)
    save_json(tmp_path / "run.plan.json", selection)
    wrong = settings.model_copy(update={"forum_graph_neo4j_uri": "bolt://localhost:45971"})
    with pytest.raises(ValueError):
        await run(wrong, store, engine, tmp_path)
    selection["entries"][0]["fingerprint"] = "changed"
    save_json(tmp_path / "run.plan.json", selection)
    with pytest.raises(ValueError):
        await run(settings, store, engine, tmp_path)


class AsyncSourceAdapter:
    def __init__(self, session):
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    async def scalars(self, statement):
        return self.session.scalars(statement)

    async def run_sync(self, fn):
        return fn(self.session)


def source_url(engine):
    # Fresh engines in API/recall must target the same per-test PostgreSQL
    # schema as the fixture, rather than the empty public schema.
    schema = engine.get_execution_options().get("schema_translate_map", {}).get(None)
    url = engine.url
    if schema:
        url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    return url.render_as_string(hide_password=False)


async def test_rag_retains_problem_action_feedback_and_each_floor(
    session, source, settings, engine, store, tmp_path
):
    from app.schemas.search import SearchFilters
    from app.services.answer_context import prepare_answer_context
    from app.services.forum_graph.answer import recall

    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    configured = settings.model_copy(
        update={
            "forum_graph_enabled": True,
            "sync_database_url": source_url(engine),
            "answer_graph_timeout_seconds": 15,
        }
    )
    adapter = AsyncSourceAdapter(session)
    evidence, trace = await recall(adapter, "GPS", SearchFilters(), configured)
    assert trace["status"] == "retrieved", trace
    roles = {e.role for item in evidence for e in item.forum_graph_evidence}
    assert roles == {"context", "claim", "feedback"}
    assert {item.canonical_url.rsplit("/", 1)[-1] for item in evidence} == {"1", "2", "3"}
    context = await prepare_answer_context(adapter, evidence, max_chars=18000)
    assert len(context.evidence) == 3
    assert "reported_success" in context.text
    assert "/t/7/1" in context.text and "/t/7/2" in context.text and "/t/7/3" in context.text
    for item in context.evidence:
        assert all(e.post_url == item.canonical_url for e in item.forum_graph_evidence)


async def test_rag_refuses_stale_material_and_obsolete_chunks(
    session, source, settings, engine, store, tmp_path
):
    from app.schemas.search import SearchFilters
    from app.services.forum_graph.answer import recall

    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    site, event, data, _ = source
    data["post_stream"]["posts"][2]["raw"] = (
        "The GPS drift returned; the prior suggestion did not fix it."
    )
    persist(session, site, event, data)
    configured = settings.model_copy(
        update={
            "forum_graph_enabled": True,
            "sync_database_url": source_url(engine),
            "answer_graph_timeout_seconds": 15,
        }
    )
    evidence, trace = await recall(AsyncSourceAdapter(session), "GPS", SearchFilters(), configured)
    assert not evidence and trace["status"] == "fallback"


async def test_api_returns_current_forum_evidence_and_disabled_is_503(
    session, source, settings, engine, store, tmp_path, monkeypatch
):
    from app.api import forum_graph
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    configured = settings.model_copy(
        update={
            "forum_graph_enabled": True,
            "sync_database_url": source_url(engine),
        }
    )
    monkeypatch.setattr(forum_graph, "get_settings", lambda: configured)
    app = FastAPI()
    app.include_router(forum_graph.router)
    client = TestClient(app)
    result = client.get("/forum-graph/retrieve", params={"q": "GPS"})
    assert result.status_code == 200
    assert result.json()["items"]
    sid = load_material(session, source[3].id).source_id
    assert client.get("/forum-graph/verify", params={"source_id": sid}).json()["verified"]
    monkeypatch.setattr(
        forum_graph,
        "get_settings",
        lambda: configured.model_copy(update={"forum_graph_enabled": False}),
    )
    assert client.get("/forum-graph/retrieve").status_code == 503


async def test_other_authors_cannot_claim_reporters_test_result(
    session, source, settings, tmp_path
):
    from app.services.forum_graph.schema import Claims, Evaluation

    m = load_material(session, source[3].id)
    windows, output = await extract_all(m, settings, tmp_path)
    results = output["windows"]
    previous = {windows[0].id: results[windows[0].id]}
    known, available = extraction.prior_context(m, windows[1], previous)
    ev = Evaluation(
        key="spoof",
        case_anchor_post_id=101,
        targets=list(available),
        outcome="reported_success",
        evidence=[Span(post_id=102, quote=m.post(102).text)],
    )
    with pytest.raises(ValueError):
        extraction.validate_claims(m, windows[1], Claims(evaluations=[ev]), {}, known, available)


def test_case_conditions_reject_only_unsupported_fields_and_are_idempotent(session, source):
    from app.services.forum_graph.schema import Case

    m = load_material(session, source[3].id)
    window = make_windows(m)[0]
    candidate = Case(
        anchor_post_id=101,
        description="GPS drift on user's Pixhawk",
        conditions={"firmware": "PX4 1.14", "invented_hardware": "H743"},
        evidence=[Span(post_id=101, quote="GPS drift")],
    )
    grounded = extraction.ground_case_conditions(m, window, candidate)
    assert grounded.conditions == {"firmware": "PX4 1.14"}
    assert "invented_hardware" in grounded.condition_rejections
    assert any(s.quote == "PX4 1.14" for s in grounded.evidence)
    assert extraction.ground_case_conditions(m, window, grounded) == grounded
    # A version prefix must never become proof of another firmware version.
    shorter = candidate.model_copy(update={"conditions": {"firmware": "PX4 1.1"}})
    assert extraction.ground_case_conditions(m, window, shorter).conditions == {}


async def test_schema_repair_receives_actionable_error_and_runs_once():
    from app.services.forum_graph.schema import Claims

    valid = dict(
        key="observation",
        subject="device",
        predicate="indicates",
        kind="state",
        value="GPS drift",
        assertion="asserted",
        act="observation",
        case_anchor_post_id=1,
        evidence=[dict(post_id=1, quote="GPS drift")],
    )

    class Client:
        calls = []

        async def request(self, stage, system, payload):
            self.calls.append((stage, payload))
            return {"claims": [({**valid, "object": "fault"} if len(self.calls) == 1 else valid)]}

    client = Client()
    result = await extraction.structured(client, "claims", "prompt", {}, Claims)
    assert result.claims[0].kind == "state"
    assert [c[0] for c in client.calls] == ["claims", "claims_repair"]
    assert "主张须且只能" in client.calls[1][1]["schema_errors"][0]["message"]
    assert "repair_instruction" in client.calls[1][1]

    class BrokenClient:
        calls = 0

        async def request(self, *args):
            self.calls += 1
            return {"claims": [{**valid, "object": "fault"}]}

    broken = BrokenClient()
    with pytest.raises(ValueError):
        await extraction.structured(broken, "claims", "prompt", {}, Claims)
    assert broken.calls == 2


async def test_policy_upgrade_failure_keeps_current_source_active_build(
    session, source, settings, engine, store, tmp_path
):
    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    m = load_material(session, source[3].id)
    old = store.status(m.source_id)["s"]["active_build_id"]
    upgraded = settings.model_copy(update={"llm_model": "new-policy-model"})
    FakeClient.fail_stage = "claims"
    result = await run(upgraded, store, engine, tmp_path, name="upgrade")
    assert result["status"] == "incomplete"
    failed = result["results"][0]["build_id"]
    check = queries.verify(store, session, m.source_id)
    assert check["verified"] and check["build_id"] == old
    assert not queries.verify(store, session, m.source_id, build_id=failed)["verified"]
    assert queries.retrieve(store, session, "GPS")["items"]
    assert queries.review_bundle(store, session, m.source_id)["verification"]["build_id"] == old
    summary = queries.status_summary(store, m.source_id)
    assert summary["status"] == "failed" and summary["active_build_id"] == old
    assert "requests" not in summary and "output_json" not in summary


async def test_planner_reclaims_expired_lease_and_retries_deferred_explicitly(
    session, source, settings, engine, store, tmp_path
):
    m = load_material(session, source[3].id)
    bid = store.enqueue(m, extraction.policy_id(settings))
    _, token = store.claim(m.source_id, bid)
    assert not plan(settings, store, engine)["entries"]
    with store.transaction() as tx:
        tx.run("MATCH(b:FGBuild {id:$id}) SET b.lease_until=0", id=bid).consume()
    assert plan(settings, store, engine)["entries"][0]["build_id"] == bid
    _, token = store.claim(m.source_id, bid)
    store.finish(m.source_id, bid, token, {}, "deferred")
    assert not plan(settings, store, engine)["entries"]
    assert plan(settings, store, engine, retry_failed=True)["entries"][0]["build_id"] == bid
    resumed = await run(settings, store, engine, tmp_path, retry_failed=True)
    assert resumed["status"] == "success", resumed


async def test_updates_coalesce_but_max_wait_and_explicit_scope_do_not_starve(
    session, source, settings, engine, store, tmp_path
):
    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    site, event, data, item = source
    data["post_stream"]["posts"][0]["raw"] += " First updated observation."
    persist(session, site, event, data)
    quiet = settings.model_copy(update={"forum_graph_debounce_seconds": 60})
    result = await run(quiet, store, engine, tmp_path, name="quiet")
    assert result["status"] == "success" and not result["results"]
    selection = json.loads((tmp_path / "quiet.plan.json").read_text())
    assert selection["waiting"][0]["reason"] == "coalescing_updates"
    m = load_material(session, item.id)
    first = store.status(m.source_id)["s"]["update_first_seen"]
    assert plan(quiet, store, engine, content_ids=[item.id])["entries"]
    assert not queries.verify(store, session, m.source_id)["verified"]
    data["post_stream"]["posts"][0]["raw"] += " Another update."
    persist(session, site, event, data)
    await run(quiet, store, engine, tmp_path, name="quiet-again")
    assert store.status(m.source_id)["s"]["update_first_seen"] == first
    with store.transaction() as tx:
        tx.run("MATCH(s:FGTopic {id:$id}) SET s.update_first_seen=0", id=m.source_id).consume()
    result = await run(quiet, store, engine, tmp_path, name="deadline")
    assert result["status"] == "success" and result["results"], result
    assert queries.verify(store, session, m.source_id)["verified"]


async def test_reconcile_is_scoped_and_never_runs_in_dry_run(
    session, source, settings, engine, store, tmp_path
):
    from app.models.content import ContentItem
    from app.services.forum_graph.pipeline import reconcile

    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    sid = load_material(session, source[3].id).source_id
    session.delete(session.get(ContentItem, source[3].id))
    session.commit()
    assert not reconcile(store, engine, forums=["ardupilot"])
    assert not store.status(sid)["s"]["withdrawn"]  # Fixture forum is PX4.
    assert (await run(settings, store, engine, tmp_path, name="preview", dry_run=True))[
        "status"
    ] == "read_only"
    assert not store.status(sid)["s"]["withdrawn"]
    assert reconcile(store, engine, forums=["px4"]) == [sid]
    assert store.status(sid)["s"]["withdrawn"]


def test_forum_target_is_explicit_and_never_falls_back(settings, monkeypatch):
    from unittest.mock import Mock

    from app.services.graph_storage import GraphDatabase

    driver = Mock()
    monkeypatch.setattr(GraphDatabase, "driver", driver)
    missing = settings.model_copy(update={"forum_graph_neo4j_uri": None})
    with pytest.raises(ValueError, match="FORUM_GRAPH_NEO4J_URI"):
        ForumGraphStore(missing)
    driver.assert_not_called()
    shared = settings.model_copy(
        update={"unified_graph_enabled": True, "unified_graph_neo4j_uri": None}
    )
    with pytest.raises(ValueError, match="UNIFIED_GRAPH_NEO4J_URI"):
        ForumGraphStore(shared)
    driver.assert_not_called()


async def test_two_users_cases_keep_separate_conditions_and_observations(
    session, source, settings, tmp_path
):
    from app.services.forum_graph.schema import Claims

    site, event, data, item = source
    data["post_stream"]["posts"][2].update(
        raw="My Pixhawk with PX4 1.15 also reports GPS drift.", user_id=3, username="user3"
    )
    persist(session, site, event, data)
    m = load_material(session, item.id)

    class TwoCases(FakeClient):
        async def request(self, stage, system, payload):
            if payload["current"]["post_id"] == 103:
                ref = dict(post_id=103, quote=payload["current"]["text"])
                if stage == "entities":
                    return dict(
                        cases=[
                            dict(
                                anchor_post_id=103,
                                description="Another user's GPS case",
                                conditions={"firmware": "1.15"},
                                evidence=[ref],
                            )
                        ],
                        entities=[
                            dict(
                                key="board",
                                surface_text="Pixhawk",
                                canonical_name="Pixhawk",
                                entity_type="product",
                                scope="case",
                                case_anchor_post_id=103,
                                evidence=[ref],
                                identity_evidence=[ref],
                            )
                        ],
                    )
                if stage == "claims":
                    return dict(
                        claims=[
                            dict(
                                key="own-observation",
                                subject="board",
                                predicate="indicates",
                                kind="state",
                                value="GPS drift",
                                assertion="asserted",
                                act="observation",
                                case_anchor_post_id=103,
                                evidence=[ref],
                            )
                        ]
                    )
            return await super().request(stage, system, payload)

    client = TwoCases(settings, tmp_path)
    policy = extraction.policy_id(settings)
    windows = make_windows(m)
    output = dict(assessments={}, windows={})
    for w in windows:
        a = await extraction.assess(m, w, client)
        output["assessments"][w.id] = extraction.assessment_record(m, w, policy, a)
        output["windows"][w.id] = await extraction.extract_window(
            m, w, client, policy, output["windows"]
        )
    rows, publication = projection.compile_output(m, windows, output, policy, "multi-case")
    cases = {c["data"]["anchor_post_id"]: c["data"]["conditions"] for c in rows["nodes"]["FGCase"]}
    assert cases == {101: {"firmware": "1.14"}, 103: {"firmware": "1.15"}}
    observations = [f for f in publication.facts if f.kind in {"state", "relation"}]
    assert len({f.subject for f in observations}) == 2
    current = output["windows"][windows[2].id]
    wrong = Claims(claims=[{**current["claims"][0]["candidate"], "case_anchor_post_id": 101}])
    known = {c["data"]["anchor_post_id"]: c["data"] for c in rows["nodes"]["FGCase"]}
    entities = {
        e["candidate"]["key"]: extraction.EntityCandidate.model_validate(e["candidate"])
        for e in current["entities"]
    }
    with pytest.raises(ValueError, match="其他作者"):
        extraction.validate_claims(m, windows[2], wrong, entities, known, {})


def test_cli_plan_pipeline_watch_query_review_and_lifecycle(
    session, source, settings, engine, store, tmp_path, monkeypatch, capsys
):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[2] / "tools/forum-knowledge-graph/build_graph.py"
    spec = importlib.util.spec_from_file_location("forum_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    configured = settings.model_copy(update={"sync_database_url": source_url(engine)})
    monkeypatch.setattr(module, "Settings", lambda **kwargs: configured)
    real_run = module.run_pipeline

    async def fixture_run(*args, **kwargs):
        return await real_run(*args, **kwargs, client_factory=FakeClient)

    monkeypatch.setattr(module, "run_pipeline", fixture_run)

    def command(*args, expected=0):
        assert module.main(list(map(str, args))) == expected
        return json.loads(capsys.readouterr().out)

    assert command("inventory")["items"][0]["status"] == "ready"
    assert not FakeClient.calls
    manifest, report = tmp_path / "cli.plan.json", tmp_path / "cli.report.json"
    base = ["--manifest", manifest, "--report", report, "--response-cache", tmp_path / "cache"]
    command("plan", "--manifest", manifest)
    assert command("pipeline", *base)["status"] == "success"
    sid = load_material(session, source[3].id).source_id
    status = command("status", "--source-id", sid)
    assert status["status"] == "success" and "requests" not in status
    assert command("query", "--q", "GPS")["items"]
    output = tmp_path / "review.json"
    assert command("verify", "--source-id", sid, "--output", output)["verified"]
    review = json.loads(output.read_text())
    assert review["native"]["nodes"]["FGClaim"]
    assert "&quot;" in output.with_suffix(".html").read_text()
    # The dry-run watch must not create plans/reports or make model calls.
    before = list(FakeClient.calls), set(tmp_path.rglob("*"))
    assert (
        command("watch", "--once", "--dry-run", "--response-cache", tmp_path / "cache")["status"]
        == "read_only"
    )
    assert (list(FakeClient.calls), set(tmp_path.rglob("*"))) == before
    command("withdraw", "--source-id", sid)
    assert not command("query")["items"]
    command("restore", "--source-id", sid)
    assert not command("query")["items"]
    command(
        "pipeline",
        "--content-id",
        source[3].id,
        "--manifest",
        tmp_path / "restore.plan.json",
        "--report",
        tmp_path / "restore.report.json",
        "--response-cache",
        tmp_path / "cache",
    )
    assert command("query", "--q", "GPS")["items"]
    assert (
        command("--env-file", tmp_path / "missing.env", "inventory", expected=1)["status"]
        == "error"
    )


@pytest.mark.parametrize("outcome", ["reported_success", "reported_failure"])
@pytest.mark.parametrize("foreign_case", [False, True])
async def test_mitigates_requires_same_case_targeted_success(
    session, source, settings, tmp_path, outcome, foreign_case
):
    site, event, data, item = source
    data["post_stream"]["posts"][1]["raw"] = "Try compass calibration."
    data["post_stream"]["posts"][2]["raw"] = (
        "I performed compass calibration and GPS drift stopped."
        if outcome == "reported_success"
        else "I performed compass calibration and GPS drift did not stop."
    )
    if foreign_case:
        data["post_stream"]["posts"][2].update(user_id=3, username="user3")
    anchor = 103 if foreign_case else 101
    persist(session, site, event, data)
    m = load_material(session, item.id)

    class TargetedClient(FakeClient):
        async def request(self, stage, system, payload):
            if payload["current"]["post_id"] == 103:
                ref = dict(post_id=103, quote=payload["current"]["text"])
                if stage == "entities":
                    return dict(
                        cases=[
                            dict(
                                anchor_post_id=103,
                                description="Another user's trial",
                                evidence=[ref],
                            )
                        ]
                        if foreign_case
                        else [],
                        entities=[
                            dict(
                                key=key,
                                surface_text=name,
                                canonical_name=name,
                                entity_type=kind,
                                scope="case",
                                case_anchor_post_id=anchor,
                                evidence=[ref],
                                identity_evidence=[ref],
                            )
                            for key, name, kind in [
                                ("method", "compass calibration", "method"),
                                ("fault", "GPS drift", "fault"),
                            ]
                        ],
                    )
                if stage == "claims":
                    target = next(
                        c["id"]
                        for c in payload["available_claims"]
                        if c["candidate"]["act"] == "recommendation"
                    )
                    # Deliberately overaccept a mitigates candidate for failure;
                    # projection must still enforce a targeted success record.
                    return dict(
                        claims=[
                            dict(
                                key="mitigation",
                                subject="method",
                                predicate="mitigates",
                                object="fault",
                                kind="relation",
                                assertion="asserted",
                                act="test_report",
                                case_anchor_post_id=anchor,
                                details={"target_claim_ids": [target]},
                                evidence=[ref],
                            )
                        ],
                        evaluations=[
                            dict(
                                key="outcome",
                                case_anchor_post_id=anchor,
                                outcome=outcome,
                                targets=[target],
                                evidence=[ref],
                            )
                        ],
                    )
            return await super().request(stage, system, payload)

    client = TargetedClient(settings, tmp_path)
    policy, windows = extraction.policy_id(settings), make_windows(m)
    output = dict(assessments={}, windows={})
    for w in windows:
        a = await extraction.assess(m, w, client)
        output["assessments"][w.id] = extraction.assessment_record(m, w, policy, a)
        output["windows"][w.id] = await extraction.extract_window(
            m, w, client, policy, output["windows"]
        )
    rows, publication = projection.compile_output(m, windows, output, policy, "mitigation")
    assert len(rows["nodes"]["FGEvaluation"]) == 1
    assert any(f.predicate == "mitigates" for f in publication.facts) == (
        outcome == "reported_success" and not foreign_case
    )
    recommendation = next(f for f in publication.facts if f.kind == "instruction")
    assert bool(recommendation.evidence[0].locator["evaluations"]) == (not foreign_case)


async def test_unrelated_changed_topic_does_not_disable_valid_query_candidates(
    session, source, settings, engine, store, tmp_path
):
    from app.services.knowledge_graph import queries as shared_queries

    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    site, event, first, first_item = source
    second = copy.deepcopy(first)
    second["id"] = 8
    second["post_stream"]["stream"] = [p + 1000 for p in second["post_stream"]["stream"]]
    for p in second["post_stream"]["posts"]:
        p["id"] += 1000
        p["topic_id"] = 8
    second["post_stream"]["posts"][0]["raw"] += " Distinct secondary signal."
    second_item = persist(session, site, event, second)

    def shift(value, offset):
        if isinstance(value, list):
            return [shift(v, offset) for v in value]
        if isinstance(value, dict):
            return {
                k: v + offset
                if k in {"post_id", "case_anchor_post_id", "anchor_post_id"} and isinstance(v, int)
                else shift(v, offset)
                for k, v in value.items()
            }
        return value

    class ShiftedClient(FakeClient):
        async def request(self, stage, system, payload):
            result = await super().request(stage, system, shift(payload, -1000))
            return shift(result, 1000)

    report = await run_pipeline(
        settings,
        store,
        engine,
        manifest=tmp_path / "second.plan.json",
        report=tmp_path / "second.report.json",
        cache=tmp_path / "cache",
        content_ids=[second_item.id],
        client_factory=ShiftedClient,
    )
    assert report["status"] == "success", report
    first["post_stream"]["posts"][0]["raw"] += " Newly changed source."
    persist(session, site, event, first)
    assert not queries.verify(store, session, load_material(session, first_item.id).source_id)[
        "verified"
    ]
    expected = load_material(session, second_item.id).source_id
    response = queries.retrieve(store, session, "Distinct secondary signal")
    assert response["items"]
    assert {s["id"] for f in response["items"] for s in f["sources"]} == {expected}
    store.settings = settings.model_copy(update={"sync_database_url": source_url(engine)})
    assert shared_queries.retrieve(store, "Distinct secondary signal")["items"]
    # Inspection of the full scope still detects stale evidence.
    with pytest.raises(ValueError):
        queries.retrieve(store, session)


async def test_forum_withdraw_preserves_another_sources_shared_facts(
    session, source, settings, engine, store, tmp_path
):
    from app.services.knowledge_graph import queries as shared_queries
    from app.services.knowledge_graph.schema import Publication
    from app.services.knowledge_graph.storage import publish

    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    sid = load_material(session, source[3].id).source_id
    original = Publication.model_validate(queries.review_bundle(store, session, sid)["publication"])
    # Synthetic protocol contribution deliberately reuses the exact same fact
    # IDs to exercise deletion isolation, not semantic source independence.
    other = original.model_copy(
        update={
            "source_type": "test_extension",
            "external_id": "independent-source",
            "native_build_id": "other-build",
            "title": "Independent fixture contribution",
        }
    )
    with store.transaction() as tx:
        publish(tx, other)
    store.withdraw(sid)
    assert not queries.retrieve(store, session)["items"]
    assert shared_queries.verify(store, other.source_id)["verified"]
    remaining = shared_queries.retrieve(store, source_id=other.source_id)["items"]
    assert {r["id"] for r in remaining} == {f.id for f in original.facts}
    assert all(len(r["sources"]) == 1 for r in remaining)


def test_hidden_post_is_not_a_valid_evidence_source(session, source):
    material = load_material(session, source[3].id)
    hidden = material.model_copy(
        update={
            "posts": [
                p.model_copy(update={"available": False}) if p.id == 101 else p
                for p in material.posts
            ]
        }
    )
    with pytest.raises(ValueError, match="隐藏或删除"):
        validate_span(hidden, Span(post_id=101, quote=hidden.post(101).text))


async def test_native_feedback_without_indexing_eligibility_is_not_recalled(
    session, source, settings, engine, store, tmp_path
):
    from app.services.forum_graph.answer import collect

    item = source[3]
    metadata = copy.deepcopy(item.metadata_json)
    metadata["posts"]["103"]["rag_eligible"] = False
    item.metadata_json = metadata
    session.commit()
    material = load_material(session, item.id)
    assert not material.post(103).indexable and material.post(103).chunks
    result = await run(settings, store, engine, tmp_path)
    assert result["status"] == "success", result
    native = queries.review_bundle(store, session, material.source_id)
    assert len(native["native"]["nodes"]["FGEvaluation"]) == 1
    records, _ = collect(
        settings.model_copy(update={"sync_database_url": source_url(engine)}), "GPS"
    )
    assert records
    assert all(e.post_id != 103 for entries in records.values() for e in entries.values())


async def test_real_async_chat_preserves_forum_context_and_saved_history(
    session, source, settings, engine, store, tmp_path, monkeypatch
):
    if engine.dialect.name != "postgresql":
        pytest.skip("real async PostgreSQL integration requires FORUM_TEST_DATABASE_URL")
    from app.db.base import Base
    from app.repositories.chat import ChatRepository
    from app.schemas.search import AnswerRequest
    from app.services.answer_generation import LLMAnswerClient
    from app.services.retrieval import RetrievalResult
    from app.services.search import SearchService
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    Base.metadata.create_all(engine)
    assert (await run(settings, store, engine, tmp_path))["status"] == "success"
    config = settings.model_copy(
        update={
            "sync_database_url": source_url(engine),
            "database_url": source_url(engine),
            "forum_graph_enabled": True,
            "answer_graph_enabled": True,
            "answer_graph_timeout_seconds": 15,
            "qdrant_url": "memory://forum-chat",
        }
    )
    captured = []

    async def empty(*args, **kwargs):
        return RetrievalResult([], {})

    async def local_stream(self, query, evidence, **kwargs):
        captured.append(kwargs["context"])
        yield "这是具体案例的问题、建议和作者反馈 [1][2][3]。"

    monkeypatch.setattr("app.services.search.retrieve_evidence", empty)
    monkeypatch.setattr(LLMAnswerClient, "stream", local_stream)
    async_engine = create_async_engine(source_url(engine))
    try:
        async with AsyncSession(async_engine, expire_on_commit=False) as actual:
            chat = await ChatRepository(actual).create_session()
            result = await SearchService(actual, settings=config, embedding=object()).answer(
                AnswerRequest(query="GPS drift", top_k=3, session_id=chat.id)
            )
            assert result.generation.graph_retrieval["forum"]["status"] == "retrieved"
            assert {
                e.post_id for item in result.supporting_evidence for e in item.forum_graph_evidence
            } == {101, 102, 103}
            assert set(result.generation.graph_retrieval["selected_chunk_ids"]) == {
                str(e.chunk_id) for e in result.supporting_evidence
            }
            assert "reported_success" in captured[0]
            assert all(f"/t/7/{n}" in captured[0] for n in [1, 2, 3])
            saved = await ChatRepository(actual).detail(chat.id)
            assert {
                e.role
                for item in saved.turns[0].supporting_evidence
                for e in item.forum_graph_evidence
            } == {"context", "claim", "feedback"}
    finally:
        await async_engine.dispose()
