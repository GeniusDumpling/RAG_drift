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
  mode?: 'search' | 'answer';
};

export function SearchPage({ initialQuery = '', onOpenContent, mode = 'search' }: SearchPageProps) {
  const isAnswerMode = mode === 'answer';
  const [query, setQuery] = useState(initialQuery);
  const [sourceSiteId, setSourceSiteId] = useState('');
  const [itemType, setItemType] = useState('');
  const [topKInput, setTopKInput] = useState('10');
  const [rawEvidence, setRawEvidence] = useState<EvidenceObject[]>([]);
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
    setRawEvidence([]);
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
      setError('结果数必须在 1 到 50 之间。');
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
      setRawEvidence(response.evidence);
      setEvidence(deduplicateVideoEvidence(response.evidence));
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
      setRawEvidence(response.supporting_evidence);
      setEvidence(deduplicateVideoEvidence(response.supporting_evidence));
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
        <p className="eyebrow">{isAnswerMode ? 'AI 问答' : '检索'}</p>
        <h1>{isAnswerMode ? 'AI 问答' : '检索'}</h1>
        <p className="muted">
          {isAnswerMode ? '基于检索证据生成带引用的自然语言答案。' : '执行关键词/向量检索，查看查询追踪与证据。'}
        </p>
      </div>

      <section className="card">
        <div className="form-grid">
          <label htmlFor="query-input">查询</label>
          <input
            id="query-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索关键词"
          />

          <label htmlFor="source-filter">数据源 ID</label>
          <input
            id="source-filter"
            value={sourceSiteId}
            onChange={(event) => setSourceSiteId(event.target.value)}
            placeholder="可选 UUID"
          />

          <label htmlFor="type-filter">内容类型</label>
          <select id="type-filter" value={itemType} onChange={(event) => setItemType(event.target.value)}>
            <option value="">全部</option>
            <option value="doc_page">文档</option>
            <option value="thread">帖子</option>
            <option value="post">评论</option>
            <option value="article">文章</option>
            <option value="comment">回复</option>
            <option value="video_description">视频描述</option>
          </select>

          <label htmlFor="top-k">结果数</label>
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
          <button type="button" onClick={() => void (isAnswerMode ? runAnswer() : runSearch())} disabled={loading}>
            {isAnswerMode ? '生成回答' : '检索'}
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
          <h2>查询追踪</h2>
          <dl className="kv-grid">
            <div>
              <dt>原始查询</dt>
              <dd>{queryRecord.raw_query}</dd>
            </div>
            <div>
              <dt>优化后查询</dt>
              <dd>{queryRecord.optimized_query_text || 'n/a'}</dd>
            </div>
            <div>
              <dt>使用智能体</dt>
              <dd>{queryRecord.used_agent ? '是' : '否'}</dd>
            </div>
            <div>
              <dt>结果数</dt>
              <dd>{queryRecord.result_count ?? evidence.length}</dd>
            </div>
          </dl>
          <pre>{JSON.stringify(queryRecord.result_summary_json, null, 2)}</pre>
        </section>
      ) : null}

      {answerText ? (
        <section className="qa-card" aria-label="问答结果">
          <header className="qa-card-header">
            <div>
              <p className="eyebrow">RAG ANSWER</p>
              <h2>问答</h2>
            </div>
            <span className="qa-evidence-count">引用 {evidence.length} 条证据</span>
          </header>
          <div className="qa-row">
            <span className="qa-mark" aria-hidden="true">问</span>
            <p>{queryRecord?.raw_query || query}</p>
          </div>
          <div className="qa-row qa-answer-row">
            <span className="qa-mark" aria-hidden="true">答</span>
            <p>{answerText}</p>
          </div>
          <p className="qa-footnote">答案基于下方检索证据生成；请以原始证据为准。</p>
        </section>
      ) : null}

      <section>
        <h2>证据</h2>
        {evidence.length ? (
          evidence.map((item) => <EvidenceCard evidence={item} key={item.content_item_id + (item.item_type === 'video_description' ? '_video' : item.chunk_id)} onOpenContent={onOpenContent} />)
        ) : loading || error ? null : (
          <p className="card empty-state">{EMPTY_STATE_COPY}</p>
        )}
      </section>
    </section>
  );
}

function deduplicateVideoEvidence(evidence: EvidenceObject[]): EvidenceObject[] {
  const seen = new Map<string, EvidenceObject>();
  for (const item of evidence) {
    if (item.item_type === 'video_description') {
      const key = item.content_item_id;
      const existing = seen.get(key);
      if (existing) {
        // 合并：保留更高 score、拼接 snippet
        if (item.score > existing.score) {
          existing.score = item.score;
          existing.vector_score = item.vector_score;
          existing.keyword_score = item.keyword_score;
        }
        if (!existing.description_text && item.description_text) {
          existing.description_text = item.description_text;
        }
        if (!existing.video_url && item.video_url) {
          existing.video_url = item.video_url;
        }
        if (existing.snippet !== item.snippet) {
          existing.snippet = existing.snippet + '\n---\n' + item.snippet;
        }
      } else {
        seen.set(key, { ...item });
      }
    } else {
      // 非视频类型，保持原样
      const key = item.content_item_id + '::' + item.chunk_id;
      seen.set(key, item);
    }
  }
  return Array.from(seen.values());
}
