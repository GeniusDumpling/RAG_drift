import { useEffect, useState } from 'react';

import { answer, search } from '../api/client';
import type { EvidenceObject, SearchQueryRead, SearchRequest } from '../api/types';
import { EvidenceCard } from '../components/EvidenceCard';
import { formatErrorMessage } from '../utils/errors';

const EMPTY_STATE_COPY = '暂无数据。请先启动后端并运行 demo seed 脚本。';
const SEARCH_LOADING_COPY = '正在检索...';
const ANSWER_LOADING_COPY = '正在生成答案...';

type SearchPageProps = {
  initialQuery?: string;
  onOpenContent?: (contentItemId: string) => void;
};

export function SearchPage({ initialQuery = '', onOpenContent }: SearchPageProps) {
  const [query, setQuery] = useState(initialQuery);
  const [sourceSiteId, setSourceSiteId] = useState('');
  const [itemType, setItemType] = useState('');
  const [topKInput, setTopKInput] = useState('10');
  const [evidence, setEvidence] = useState<EvidenceObject[]>([]);
  const [queryRecord, setQueryRecord] = useState<SearchQueryRead>();
  const [answerText, setAnswerText] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingMessage, setLoadingMessage] = useState('');

  useEffect(() => {
    setQuery(initialQuery);
  }, [initialQuery]);

  function clearResults() {
    setEvidence([]);
    setQueryRecord(undefined);
    setAnswerText('');
  }

  function buildRequest(): SearchRequest | null {
    const trimmed = query.trim();
    if (!trimmed) {
      clearResults();
      setError('请输入查询。');
      return null;
    }
    const parsedTopK = Number(topKInput);
    if (!Number.isInteger(parsedTopK) || parsedTopK < 1 || parsedTopK > 50) {
      clearResults();
      setError('Top K 必须在 1 到 50 之间。');
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
    clearResults();
    setLoading(true);
    setLoadingMessage(SEARCH_LOADING_COPY);
    setError('');
    try {
      const response = await search(request);
      setEvidence(response.evidence);
      setQueryRecord(response.query);
    } catch (caught) {
      setEvidence([]);
      setQueryRecord(undefined);
      setError(formatErrorMessage('检索失败', caught));
    } finally {
      setLoading(false);
      setLoadingMessage('');
    }
  }

  async function runAnswer() {
    const request = buildRequest();
    if (!request) {
      return;
    }
    clearResults();
    setLoading(true);
    setLoadingMessage(ANSWER_LOADING_COPY);
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
      setError(formatErrorMessage('生成答案失败', caught));
    } finally {
      setLoading(false);
      setLoadingMessage('');
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
        <p className="eyebrow">检索 Retrieval</p>
        <h1>检索问答 Search</h1>
        <p className="muted">执行 keyword/vector 检索，检查 Query Trace，并引用 Evidence。</p>
      </div>

      <section className="card">
        <div className="form-grid">
          <label htmlFor="query-input">Query 查询</label>
          <input
            id="query-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="例如：telemetry settings"
          />

          <label htmlFor="source-filter">Source Site ID</label>
          <input
            id="source-filter"
            value={sourceSiteId}
            onChange={(event) => setSourceSiteId(event.target.value)}
            placeholder="可选 UUID"
          />

          <label htmlFor="type-filter">Item Type</label>
          <select id="type-filter" value={itemType} onChange={(event) => setItemType(event.target.value)}>
            <option value="">任意 Any</option>
            <option value="doc_page">doc_page</option>
            <option value="thread">thread</option>
            <option value="post">post</option>
            <option value="article">article</option>
            <option value="comment">comment</option>
            <option value="video_description">video_description</option>
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
            检索 Search
          </button>
          <button type="button" onClick={() => void runAnswer()} disabled={loading}>
            生成 Answer
          </button>
        </div>
        {error ? (
          <p className="error-text" role="alert">
            {error}
          </p>
        ) : null}
      </section>

      {loading ? (
        <p className="card" role="status">
          {loadingMessage}
        </p>
      ) : null}

      {queryRecord ? (
        <section className="card">
          <h2>查询追踪 Query Trace</h2>
          <dl className="kv-grid">
            <div>
              <dt>原始 Query</dt>
              <dd>{queryRecord.raw_query}</dd>
            </div>
            <div>
              <dt>优化后 Query</dt>
              <dd>{queryRecord.optimized_query_text || 'n/a'}</dd>
            </div>
            <div>
              <dt>使用 Agent</dt>
              <dd>{queryRecord.used_agent ? '是' : '否'}</dd>
            </div>
            <div>
              <dt>结果数 Results</dt>
              <dd>{queryRecord.result_count ?? evidence.length}</dd>
            </div>
          </dl>
          <pre>{JSON.stringify(queryRecord.result_summary_json, null, 2)}</pre>
        </section>
      ) : null}

      {answerText ? (
        <section className="card answer-card">
          <h2>答案草稿 Answer</h2>
          <p>{answerText}</p>
        </section>
      ) : null}

      <section>
        <h2>证据 Evidence</h2>
        {evidence.length ? (
          evidence.map((item) => <EvidenceCard evidence={item} key={item.chunk_id} onOpenContent={onOpenContent} />)
        ) : loading || error ? null : (
          <p className="card empty-state">{EMPTY_STATE_COPY}</p>
        )}
      </section>
    </section>
  );
}
