import { ChangeEvent, useEffect, useMemo, useState } from 'react';

import { getContent, listContents } from '../api/client';
import type { ContentDetail, ContentListItem, Page } from '../api/types';
import { formatErrorMessage } from '../utils/errors';
import { safeExternalHref } from '../utils/links';

const EMPTY_STATE_COPY = 'No data loaded yet. Start the backend and run the demo seed script.';
const CONTENT_UNAVAILABLE_COPY = 'Content detail unavailable while the API request is failing.';
const LOADING_CONTENT_COPY = 'Loading content data...';
const LOADING_CONTENT_DETAIL_COPY = 'Loading content detail...';

type ContentDetailPageProps = {
  onOpenRun?: (runId: string) => void;
};

export function ContentDetailPage({ onOpenRun }: ContentDetailPageProps = {}) {
  const [contents, setContents] = useState<Page<ContentListItem>>();
  const [selectedContentId, setSelectedContentId] = useState('');
  const [detail, setDetail] = useState<ContentDetail>();
  const [listError, setListError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    let ignore = false;
    setListError('');
    setListLoading(true);
    listContents({ limit: 25 })
      .then((page) => {
        if (!ignore) {
          setContents(page);
          setSelectedContentId((current) => current || page.items[0]?.id || '');
        }
      })
      .catch((caught) => {
        if (!ignore) {
          setContents(undefined);
          setListError(formatErrorMessage('Unable to load content list', caught));
        }
      })
      .finally(() => {
        if (!ignore) {
          setListLoading(false);
        }
      });
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    setDetailError('');
    if (!selectedContentId) {
      setDetail(undefined);
      setDetailLoading(false);
      return;
    }
    let ignore = false;
    setDetail(undefined);
    setDetailLoading(true);
    getContent(selectedContentId)
      .then((content) => {
        if (!ignore) {
          setDetail(content);
        }
      })
      .catch((caught) => {
        if (!ignore) {
          setDetail(undefined);
          setDetailError(formatErrorMessage('Unable to load content detail', caught));
        }
      })
      .finally(() => {
        if (!ignore) {
          setDetailLoading(false);
        }
      });
    return () => {
      ignore = true;
    };
  }, [selectedContentId]);

  const displayedDetail = detail?.id === selectedContentId ? detail : undefined;
  const indexedTextPreview = useMemo(() => {
    if (!displayedDetail) {
      return '';
    }
    return displayedDetail.chunks.map((chunk) => chunk.display_text).join('\n\n') || displayedDetail.raw_page.raw_text || '';
  }, [displayedDetail]);
  const rawPageUrl = displayedDetail?.raw_page.final_url || displayedDetail?.raw_page.requested_url || '';
  const rawPageHref = safeExternalHref(rawPageUrl);

  function handleContentChange(event: ChangeEvent<HTMLSelectElement | HTMLInputElement>) {
    setSelectedContentId(event.target.value);
  }

  const contentLoadFailed = Boolean(listError || detailError);
  const detailSelectionPending = Boolean(selectedContentId && !displayedDetail && !detailError);
  const loading = listLoading || detailLoading || detailSelectionPending;
  const loadingCopy = detailLoading || detailSelectionPending ? LOADING_CONTENT_DETAIL_COPY : LOADING_CONTENT_COPY;

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

      {listError ? (
        <p className="card error-text" role="alert">
          {listError}
        </p>
      ) : null}
      {detailError ? (
        <p className="card error-text" role="alert">
          {detailError}
        </p>
      ) : null}
      {loading ? (
        <p className="card status-line" role="status" aria-live="polite">
          {loadingCopy}
        </p>
      ) : null}

      {displayedDetail ? (
        <>
          <section className="card">
            <div className="card-header">
              <div>
                <h2>{displayedDetail.title || 'Untitled content item'}</h2>
                <p className="muted compact">{displayedDetail.canonical_url}</p>
              </div>
              <span className="badge">{displayedDetail.item_type}</span>
            </div>
            <dl className="kv-grid">
              <div>
                <dt>Source</dt>
                <dd>{displayedDetail.source.name}</dd>
              </div>
              <div>
                <dt>Author</dt>
                <dd>{displayedDetail.author_name || 'unknown'}</dd>
              </div>
              <div>
                <dt>Fetched</dt>
                <dd>{new Date(displayedDetail.fetched_at).toLocaleString()}</dd>
              </div>
              <div>
                <dt>Published</dt>
                <dd>{displayedDetail.published_at ? new Date(displayedDetail.published_at).toLocaleString() : 'n/a'}</dd>
              </div>
              <div>
                <dt>Extraction confidence</dt>
                <dd>{displayedDetail.extraction_confidence ?? displayedDetail.raw_page.extraction_confidence ?? 'n/a'}</dd>
              </div>
              <div>
                <dt>Parse status</dt>
                <dd>{displayedDetail.raw_page.parse_status}</dd>
              </div>
            </dl>
            <div className="tag-row">
              {displayedDetail.tags.length
                ? displayedDetail.tags.map((tag) => <span className="badge" key={tag}>{tag}</span>)
                : 'No tags'}
            </div>
            <p>{displayedDetail.summary_text || 'No summary available.'}</p>
            <div className="button-row link-row">
              {rawPageHref ? (
                <a href={rawPageHref} target="_blank" rel="noreferrer">
                  Raw page link
                </a>
              ) : (
                <span className="muted" aria-disabled="true">
                  Raw page link unavailable: {rawPageUrl || 'n/a'}
                </span>
              )}
              {onOpenRun ? (
                <button className="link-button" type="button" onClick={() => onOpenRun(displayedDetail.crawl_run.id)}>
                  Run link
                </button>
              ) : (
                <span className="muted" aria-disabled="true">
                  Run link unavailable
                </span>
              )}
            </div>
          </section>

          <section className="card">
            <h2>Indexed Text Preview</h2>
            {indexedTextPreview ? <pre>{indexedTextPreview}</pre> : <p className="muted">{EMPTY_STATE_COPY}</p>}
          </section>

          <section className="card">
            <h2>Chunks</h2>
            {displayedDetail.chunks.length ? (
              <ol className="chunk-list">
                {displayedDetail.chunks.map((chunk) => (
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
      ) : loading ? null : contentLoadFailed ? (
        <p className="card muted">{CONTENT_UNAVAILABLE_COPY}</p>
      ) : (
        <p className="card empty-state">{EMPTY_STATE_COPY}</p>
      )}
    </section>
  );
}
