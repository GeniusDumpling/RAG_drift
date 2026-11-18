"""Compile reviewed windows into immutable native rows and the shared KG contract."""

from app.services.knowledge_graph.schema import Entity, Evidence, Fact, Publication, digest

from .extraction import entity_identity, validate_assessment, validate_result
from .schema import Claim, EntityCandidate, Span
from .source import SOURCE_VERSION, code_ranges, map_span, validate_span

LABELS = ("FGPost", "FGSpan", "FGCase", "FGClaim", "FGEvaluation")


def span_kind(post, span):
    blocks = [*code_ranges(post.text), *[{**q, "kind": "quote"} for q in post.quotes]]
    overlapping = [b for b in blocks if span.start < b["end"] and span.end > b["start"]]
    if not overlapping:
        return "text"
    for block in overlapping:
        if block["start"] <= span.start and span.end <= block["end"]:
            return block["kind"]
    return "mixed"


def compile_output(material, windows, output, policy, build_id):
    from .windows import aggregate

    assessments = output["assessments"]
    summary = aggregate(windows, {k: v["value"] for k, v in assessments.items()})
    if summary["decision"] != "extract":
        raise ValueError("未经前置判断通过，禁止编译业务投影")
    accepted, current = {}, {}
    for window in windows:
        record = assessments[window.id]
        validate_assessment(material, window, policy, record)
        if window.id not in summary["extract_windows"]:
            if window.id in output.get("windows", {}):
                raise ValueError("跳过或待补充窗口不应有后续抽取")
            continue
        if window.id not in output.get("windows", {}):
            raise ValueError("有价值窗口尚未完成抽取")
        current[window.id] = validate_result(
            material, window, policy, output["windows"][window.id], current
        )
        accepted[window.id] = current[window.id]
    nodes = {label: {} for label in LABELS}
    edges = []

    def node(label, key, data):
        nid = digest([build_id, label, key])
        row = dict(id=nid, data=data)
        existing = nodes[label].get(nid)
        if existing is not None and existing != row:
            raise ValueError("同一原生节点身份存在冲突内容")
        nodes[label][nid] = row
        return nid

    def edge(kind, start, end):
        edges.append(dict(id=digest([build_id, kind, start, end]), kind=kind, start=start, end=end))

    post_ids = {}
    for post in material.posts:
        post_ids[post.id] = node("FGPost", post.id, post.model_dump(exclude={"chunks"}))
    by_number = {p.number: p for p in material.posts}
    for post in material.posts:
        if post.reply_to in by_number:
            edge("REPLIES_TO", post_ids[post.id], post_ids[by_number[post.reply_to].id])
        for quote in post.quotes:
            if quote["target_number"] in by_number:
                edge("QUOTES", post_ids[post.id], post_ids[by_number[quote["target_number"]].id])

    all_claims, all_evaluations, all_cases = {}, [], {}
    for result in accepted.values():
        for case in result["cases"]:
            key = case["anchor_post_id"]
            if key in all_cases and all_cases[key] != case:
                raise ValueError("同一案例条件冲突，不能覆盖或混合")
            all_cases[key] = case
        for row in result["claims"]:
            all_claims[row["id"]] = row
        for row in result["evaluations"]:
            all_evaluations.append({**row, "post_id": result["post_id"]})
    cases = {anchor: node("FGCase", anchor, case) for anchor, case in all_cases.items()}
    claim_nodes = {}
    for key, claim in all_claims.items():
        claim_nodes[key] = node("FGClaim", key, claim)
        edge("STATES", post_ids[claim["post_id"]], claim_nodes[key])
        anchor = claim["candidate"]["case_anchor_post_id"]
        if anchor is not None:
            edge("IN_CASE", claim_nodes[key], cases[anchor])
    for row in all_evaluations:
        key = digest(row)
        eid = node("FGEvaluation", key, row)
        edge("STATES", post_ids[row["post_id"]], eid)
        edge("IN_CASE", eid, cases[row["case_anchor_post_id"]])
        for target in row["targets"]:
            if target not in claim_nodes:
                raise ValueError("反馈引用了当前构建之外的主张")
            edge("EVALUATES", eid, claim_nodes[target])

    def evidence(span, review_id, *, claim=None):
        span = validate_span(material, Span.model_validate(span))
        post = material.post(span.post_id)
        mapping = map_span(material, span) if post.indexable else []
        sid = node(
            "FGSpan",
            [span.post_id, span.start, span.end],
            dict(
                **span.model_dump(),
                text_hash=post.text_hash,
                block_kind=span_kind(post, span),
                mapping_version=SOURCE_VERSION,
                mappings=mapping,
                raw_page_id=post.raw_page_id,
                url=post.url,
            ),
        )
        edge("HAS_SPAN", post_ids[post.id], sid)
        if claim is not None:
            edge("HAS_SPAN", claim_nodes[claim["id"]], sid)
        if not mapping:
            return None
        feedback = []
        if claim is not None:
            for row in all_evaluations:
                if (
                    claim["id"] in row["targets"]
                    and row["case_anchor_post_id"] == claim["candidate"]["case_anchor_post_id"]
                ):
                    feedback.append(
                        {
                            **row,
                            "url": material.post(row["post_id"]).url,
                            "author": material.post(row["post_id"]).author,
                        }
                    )
        return Evidence(
            quote=span.quote,
            review_id=review_id,
            locator=dict(
                type="forum",
                block_kind=span_kind(post, span),
                mapping_version=SOURCE_VERSION,
                source_fingerprint=material.fingerprint,
                build_id=build_id,
                review_version=policy,
                source_id=material.source_id,
                content_item_id=material.content_item_id,
                source_site_id=material.source_site_id,
                forum=material.forum,
                topic_id=material.topic_id,
                raw_page_id=post.raw_page_id,
                post_id=post.id,
                post_number=post.number,
                author=post.author,
                author_id=post.author_id,
                published_at=post.published_at,
                updated_at=post.updated_at,
                post_version=post.version,
                post_hash=post.text_hash,
                post_start=span.start,
                post_end=span.end,
                url=post.url,
                mappings=mapping,
                accepted_answer=post.accepted,
                claim_id=claim["id"] if claim else None,
                case_id=(
                    f'{material.external_id}:{claim["candidate"]["case_anchor_post_id"]}'
                    if claim and claim["candidate"]["case_anchor_post_id"] is not None
                    else None
                ),
                act=claim["candidate"]["act"] if claim else "mention",
                source_role=("context" if claim and span.post_id != claim["post_id"] else "claim"),
                claim_post_id=claim["post_id"] if claim else None,
                evaluations=feedback,
            ),
        )

    entities, facts = {}, []
    for result in accepted.values():
        local = {}
        for row in result["entities"]:
            candidate = EntityCandidate.model_validate(row["candidate"])
            identity = entity_identity(material, candidate)
            eid = identity["id"]
            local[candidate.key] = eid
            mentions = [
                e
                for span in candidate.evidence
                if (e := evidence(span, result["receipt"])) is not None
            ]
            if eid in entities:
                mentions = entities[eid].mentions + mentions
            entities[eid] = Entity(**identity, mentions=mentions)
        for row in result["claims"]:
            claim = Claim.model_validate(row["candidate"])
            proof = [
                e
                for span in claim.evidence
                if (e := evidence(span, result["receipt"], claim=row)) is not None
            ]
            # Questions/hypotheses remain native claims, not default technical facts.
            if claim.act in {"question", "hypothesis"} or len(proof) != len(claim.evidence):
                continue
            if claim.predicate == "mitigates":
                targets = claim.details.get("target_claim_ids", [])
                targets_in_case = bool(targets) and all(
                    target in all_claims
                    and all_claims[target]["candidate"]["case_anchor_post_id"]
                    == claim.case_anchor_post_id
                    for target in targets
                )
                if not targets_in_case or not any(
                    set(targets) == set(e["targets"])
                    and e["outcome"] == "reported_success"
                    and e["case_anchor_post_id"] == claim.case_anchor_post_id
                    and e["post_id"] == row["post_id"]
                    for e in all_evaluations
                ):
                    continue
            qualifiers = dict(claim.qualifiers)
            if claim.case_anchor_post_id is not None:
                qualifiers["case_id"] = f"{material.external_id}:{claim.case_anchor_post_id}"
                case = all_cases[claim.case_anchor_post_id]
                for key, value in case["conditions"].items():
                    qualifiers["context:" + key] = value
                for span in case["evidence"]:
                    scoped = evidence(span, result["receipt"], claim=row)
                    if scoped is None:
                        proof = []
                        break
                    if scoped not in proof:
                        proof.append(scoped)
            if not proof:
                continue
            if claim.act == "recommendation":
                qualifiers["modality"] = "recommendation"
            facts.append(
                Fact(
                    subject=local[claim.subject],
                    predicate=claim.predicate,
                    object=local[claim.object] if claim.object else None,
                    value=claim.value,
                    attribute=claim.attribute,
                    kind=claim.kind,
                    assertion=claim.assertion,
                    qualifiers=qualifiers,
                    details=claim.details,
                    evidence=proof,
                )
            )
        # Cases/feedback also retain source spans even when not independently indexed.
        for case in result["cases"]:
            for span in case["evidence"]:
                evidence(span, result["receipt"])
        for evaluation in result["evaluations"]:
            for span in evaluation["evidence"]:
                evidence(span, result["receipt"])
    publication = Publication(
        adapter_version="forum-v1",
        source_type="forum",
        external_id=material.external_id,
        native_build_id=build_id,
        source_fingerprint=material.fingerprint,
        extractor_version=policy,
        title=material.title,
        url=material.url,
        metadata=dict(
            content_item_id=material.content_item_id,
            forum=material.forum,
            topic_id=material.topic_id,
            coverage=summary,
        ),
        entities=list(entities.values()),
        facts=facts,
    )
    rows = dict(
        nodes={label: list(values.values()) for label, values in nodes.items()},
        edges=list({e["id"]: e for e in edges}.values()),
    )
    return rows, publication
