import { useEffect, useState } from 'react';

import { answer, search } from '../api/client';
import type { EvidenceObject, SearchQueryRead, SearchRequest } from '../api/types';
import { EvidenceCard } from '../components/EvidenceCard';
import { formatErrorMessage } from '../utils/errors';

const EMPTY_STATE_COPY = 'No data loaded yet. Start the backend and run the demo seed script.';

type SearchPageProps = {
  initialQuery?: string;
};

export function SearchPage({ initialQuery = '' }: SearchPageProps) {
  const [query, setQuery] = useState(initialQuery);
  const [sourceSiteId, setSourceSiteId] = useState('');
  const [itemType, setItemType] = useState('');
  const [topKInput, setTopKInput] = useState('10');
  const [evidence, setEvidence] = useState<EvidenceObject[]>([]);
  const [queryRecord, setQueryRecord] = useState<SearchQueryRead>();
  const [answerText, setAnswerText] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setQuery(initialQuery);
  }, [initialQuery]);

  function buildRequest(): SearchRequest | null {
    const trimmed = query.trim();
    if (!trimmed) {
      setError('Query is required.');
      return null;
    }
    const parsedTopK = Number(topKInput);
    if (!Number.isInteger(parsedTopK) || parsedTopK < 1 || parsedTopK > 50) {
      setError('Top K must be between 1 and 50.');
      return null;
    }
    return {
      query: trimmed,
      filters: {
        source_site_id: sourceSiteId.trim() || undefined,
        item_type: itemType || undefined,
      },
      top_k: parsedTopK,
    };
  }

  async function runSearch() {
    const request = buildRequest();
    if (!request) {
      return;
    }
    setLoading(true);
    setError('');
    setAnswerText('');
    try {
      const response = await search(request);
      setEvidence(response.evidence);
      setQueryRecord(response.query);
    } catch (caught) {
      setEvidence([]);
      setQueryRecord(undefined);
      setError(formatErrorMessage('Search failed', caught));
    } finally {
      setLoading(false);
    }
  }

  async function runAnswer() {
    const request = buildRequest();
    if (!request) {
      return;
    }
    setLoading(true);
    setError('');
    try {
      const response = await answer(request);
      setEvidence(response.supporting_evidence);
      setQueryRecord(response.query);
      setAnswerText(response.answer);
    } catch (caught) {
      setEvidence([]);
      setQueryRecord(undefined);
      setAnswerText('');
      setError(formatErrorMessage('Answer failed', caught));
    } finally {
      setLoading(false);
    }
  }

  function handleTopKChange(value: string) {
    if (value === '') {
      setTopKInput('');
      return;
    }
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) {
      return;
    }
    const clamped = Math.min(50, Math.max(1, Math.trunc(parsed)));
    setTopKInput(String(clamped));
  }

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">Retrieval</p>
        <h1>Search</h1>
        <p className="muted">Run keyword/vector retrieval, inspect optimized query records, and cite evidence.</p>
      </div>

      <section className="card">
        <div className="form-grid">
          <label htmlFor="query-input">Query</label>
          <input
            id="query-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="disable telemetry in device settings"
          />

          <label htmlFor="source-filter">Source Site ID</label>
          <input
            id="source-filter"
            value={sourceSiteId}
            onChange={(event) => setSourceSiteId(event.target.value)}
            placeholder="optional UUID"
          />

          <label htmlFor="type-filter">Item Type</label>
          <select id="type-filter" value={itemType} onChange={(event) => setItemType(event.target.value)}>
            <option value="">Any</option>
            <option value="doc_page">doc_page</option>
            <option value="thread">thread</option>
            <option value="post">post</option>
            <option value="article">article</option>
            <option value="comment">comment</option>
          </select>

          <label htmlFor="top-k">Top K</label>
          <input
            id="top-k"
            type="number"
            min="1"
            max="50"
            value={topKInput}
            onChange={(event) => handleTopKChange(event.target.value)}
          />
        </div>
        <div className="button-row">
          <button type="button" onClick={() => void runSearch()} disabled={loading}>
            Search
          </button>
          <button type="button" onClick={() => void runAnswer()} disabled={loading}>
            Answer
          </button>
        </div>
        {error ? (
          <p className="error-text" role="alert">
            {error}
          </p>
        ) : null}
      </section>

      {queryRecord ? (
        <section className="card">
          <h2>Query Trace</h2>
          <dl className="kv-grid">
            <div>
              <dt>Raw query</dt>
              <dd>{queryRecord.raw_query}</dd>
            </div>
            <div>
              <dt>Optimized</dt>
              <dd>{queryRecord.optimized_query_text || 'n/a'}</dd>
            </div>
            <div>
              <dt>Used agent</dt>
              <dd>{queryRecord.used_agent ? 'yes' : 'no'}</dd>
            </div>
            <div>
              <dt>Results</dt>
              <dd>{queryRecord.result_count ?? evidence.length}</dd>
            </div>
          </dl>
          <pre>{JSON.stringify(queryRecord.result_summary_json, null, 2)}</pre>
        </section>
      ) : null}

      {answerText ? (
        <section className="card answer-card">
          <h2>Answer Draft</h2>
          <p>{answerText}</p>
        </section>
      ) : null}

      <section>
        <h2>Evidence</h2>
        {evidence.length ? (
          evidence.map((item) => <EvidenceCard evidence={item} key={item.chunk_id} />)
        ) : error ? null : (
          <p className="card empty-state">{EMPTY_STATE_COPY}</p>
        )}
      </section>
    </section>
  );
}
