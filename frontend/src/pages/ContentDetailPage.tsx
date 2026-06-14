import { ChangeEvent, useEffect, useMemo, useState } from 'react';

import { getContent, listContents } from '../api/client';
import type { ContentDetail, ContentListItem, Page } from '../api/types';

const EMPTY_STATE_COPY = 'No data loaded yet. Start the backend and run the demo seed script.';

export function ContentDetailPage() {
  const [contents, setContents] = useState<Page<ContentListItem>>();
  const [selectedContentId, setSelectedContentId] = useState('');
  const [detail, setDetail] = useState<ContentDetail>();

  useEffect(() => {
    let ignore = false;
    listContents({ limit: 25 })
      .then((page) => {
        if (!ignore) {
          setContents(page);
          setSelectedContentId((current) => current || page.items[0]?.id || '');
        }
      })
      .catch(() => {
        if (!ignore) {
          setContents(undefined);
        }
      });
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedContentId) {
      setDetail(undefined);
      return;
    }
    let ignore = false;
    getContent(selectedContentId)
      .then((content) => {
        if (!ignore) {
          setDetail(content);
        }
      })
      .catch(() => {
        if (!ignore) {
          setDetail(undefined);
        }
      });
    return () => {
      ignore = true;
    };
  }, [selectedContentId]);

  const cleanedText = useMemo(() => {
    if (!detail) {
      return '';
    }
    return (
      detail.cleaned_text ||
      detail.chunks.map((chunk) => chunk.display_text).join('\n\n') ||
      detail.raw_page.raw_text ||
      ''
    );
  }, [detail]);

  function handleContentChange(event: ChangeEvent<HTMLSelectElement | HTMLInputElement>) {
    setSelectedContentId(event.target.value);
  }

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">Corpus</p>
        <h1>Content Detail</h1>
        <p className="muted">Hydrated content records with raw snapshot, chunks, run lineage, and extraction metadata.</p>
      </div>

      <div className="card controls-card">
        <label htmlFor="content-selector">Content item</label>
        {contents?.items.length ? (
          <select id="content-selector" value={selectedContentId} onChange={handleContentChange}>
            {contents.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.item_type} · {item.title || item.canonical_url}
              </option>
            ))}
          </select>
        ) : (
          <input
            id="content-selector"
            value={selectedContentId}
            onChange={handleContentChange}
            placeholder="Paste content item id"
          />
        )}
      </div>

      {detail ? (
        <>
          <section className="card">
            <div className="card-header">
              <div>
                <h2>{detail.title || 'Untitled content item'}</h2>
                <p className="muted compact">{detail.canonical_url}</p>
              </div>
              <span className="badge">{detail.item_type}</span>
            </div>
            <dl className="kv-grid">
              <div>
                <dt>Source</dt>
                <dd>{detail.source.name}</dd>
              </div>
              <div>
                <dt>Author</dt>
                <dd>{detail.author_name || 'unknown'}</dd>
              </div>
              <div>
                <dt>Fetched</dt>
                <dd>{new Date(detail.fetched_at).toLocaleString()}</dd>
              </div>
              <div>
                <dt>Published</dt>
                <dd>{detail.published_at ? new Date(detail.published_at).toLocaleString() : 'n/a'}</dd>
              </div>
              <div>
                <dt>Extraction confidence</dt>
                <dd>{detail.extraction_confidence ?? detail.raw_page.extraction_confidence ?? 'n/a'}</dd>
              </div>
              <div>
                <dt>Parse status</dt>
                <dd>{detail.raw_page.parse_status}</dd>
              </div>
            </dl>
            <div className="tag-row">
              {detail.tags.length ? detail.tags.map((tag) => <span className="badge" key={tag}>{tag}</span>) : 'No tags'}
            </div>
            <p>{detail.summary_text || 'No summary available.'}</p>
            <div className="button-row link-row">
              <a href={detail.raw_page.final_url || detail.raw_page.requested_url} target="_blank" rel="noreferrer">
                Raw page link
              </a>
              <a href={`#run-${detail.crawl_run.id}`}>Run link</a>
            </div>
          </section>

          <section className="card">
            <h2>Cleaned Text</h2>
            {cleanedText ? <pre>{cleanedText}</pre> : <p className="muted">{EMPTY_STATE_COPY}</p>}
          </section>

          <section className="card">
            <h2>Chunks</h2>
            {detail.chunks.length ? (
              <ol className="chunk-list">
                {detail.chunks.map((chunk) => (
                  <li key={chunk.id}>
                    <div className="card-header compact-header">
                      <strong>Chunk {chunk.chunk_index}</strong>
                      <span className="badge">{chunk.embed_status}</span>
                    </div>
                    <p>{chunk.display_text}</p>
                    <dl className="kv-grid">
                      <div>
                        <dt>Chars</dt>
                        <dd>
                          {chunk.char_start ?? 'n/a'}–{chunk.char_end ?? 'n/a'}
                        </dd>
                      </div>
                      <div>
                        <dt>Tokens</dt>
                        <dd>{chunk.token_count ?? 'n/a'}</dd>
                      </div>
                      <div>
                        <dt>Vector backend</dt>
                        <dd>{chunk.vector_backend || 'n/a'}</dd>
                      </div>
                      <div>
                        <dt>Vector point</dt>
                        <dd>{chunk.vector_point_id || chunk.qdrant_point_id || 'n/a'}</dd>
                      </div>
                    </dl>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted">{EMPTY_STATE_COPY}</p>
            )}
          </section>
        </>
      ) : (
        <p className="card empty-state">{EMPTY_STATE_COPY}</p>
      )}
    </section>
  );
}
