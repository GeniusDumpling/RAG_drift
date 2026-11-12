import hashlib
import io
import json
from uuid import uuid4

from app.db.base import utcnow
from app.models.content import ContentChunk, ContentItem, RawPage
from app.models.control import CrawlJob, CrawlRun, SourceSite
from app.schemas.search import SearchFilters
from app.services.answer_context import prepare_answer_context
from app.services.retrieval import _hydrate_and_rank_evidence, _SearchHit
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession


async def test_async_hydration_explicitly_loads_repair_metadata(
    db_session: AsyncSession, tmp_path, monkeypatch
):
    monkeypatch.setenv("IFIXIT_MEDIA_DIR", str(tmp_path))
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), "white").save(buffer, "JPEG")
    raw_image = buffer.getvalue()
    sha = hashlib.sha256(raw_image).hexdigest()
    guid = "HB5apup35uvAbdWi"
    (tmp_path / "blobs").mkdir()
    (tmp_path / "blobs" / f"{sha}.jpg").write_bytes(raw_image)
    (tmp_path / "catalog").mkdir()
    source_url = f"https://guide-images.cdn.ifixit.com/igi/{guid}.full"
    (tmp_path / "catalog" / f"{guid}.json").write_text(
        json.dumps(
            {
                "guid": guid,
                "sha256": sha,
                "storage_key": f"blobs/{sha}.jpg",
                "status": "confirmed",
                "source_url": source_url,
                "width": 800,
                "height": 600,
            }
        )
    )
    site = SourceSite(
        name="iFixit",
        site_type="documentation",
        base_url="https://www.ifixit.com",
        fetch_mode="local_file",
    )
    db_session.add(site)
    await db_session.flush()
    job = CrawlJob(
        source_site_id=site.id,
        name="test",
        trigger_mode="manual",
        parser_profile="ifixit",
        max_pages=1,
    )
    db_session.add(job)
    await db_session.flush()
    run = CrawlRun(source_site_id=site.id, crawl_job_id=job.id, trigger_type="manual")
    db_session.add(run)
    await db_session.flush()
    page = RawPage(
        source_site_id=site.id,
        crawl_run_id=run.id,
        requested_url="https://www.ifixit.com/Guide/161708",
        fetched_at=utcnow(),
    )
    db_session.add(page)
    await db_session.flush()
    item = ContentItem(
        source_site_id=site.id,
        raw_page_id=page.id,
        crawl_run_id=run.id,
        item_type="repair_guide",
        canonical_url="https://www.ifixit.com/Guide/161708",
        title="DJI Mavic 3 Pro Chip ID",
        cleaned_text="Likely Leadcore H6MN applications processor",
        content_hash=str(uuid4()),
        dedup_key=str(uuid4()),
        metadata_json={
            "ifixit": {
                "media_refs": [
                    {
                        "media_ref_id": "photo-1",
                        "source_guid": guid,
                        "source_url": source_url,
                        "step_occurrence_id": "step-1",
                    }
                ]
            },
            "steps": [
                {
                    "step_occurrence_id": "step-1",
                    "step_order": 1,
                    "title": "Drone Main Board Side A",
                }
            ],
        },
    )
    db_session.add(item)
    await db_session.flush()
    chunk = ContentChunk(
        content_item_id=item.id,
        chunk_index=0,
        display_text=item.cleaned_text,
        embed_text=item.cleaned_text,
        embed_status="success",
        chunk_metadata_json={"media_ref_ids": ["photo-1"]},
    )
    db_session.add(chunk)
    await db_session.commit()
    chunk_id, item_id = chunk.id, item.id
    db_session.expunge_all()
    evidence = await _hydrate_and_rank_evidence(
        db_session,
        hits=[
            _SearchHit(
                chunk_id=chunk_id,
                content_item_id=item_id,
                score=0.9,
                matched_by="keyword",
                keyword_score=0.9,
            )
        ],
        filters=SearchFilters(),
        top_k=5,
        snippet_terms=["H6MN"],
    )
    assert len(evidence) == 1
    assert evidence[0].images[0].source_guid == guid
    assert evidence[0].images[0].step_order == 1
    context = await prepare_answer_context(db_session, evidence)
    assert "Likely Leadcore H6MN" in context.text
    assert context.evidence[0].images[0].asset_id == sha
