import { useEffect, useState } from 'react';

import {
  cancelLiteratureRun,
  createLiteratureRun,
  getLiteratureArtifactUrl,
  getLiteratureRun,
  getLiteratureRunEvents,
  getLiteratureRunResults,
} from '../api/client';
import type {
  LiteratureResultItem,
  LiteratureRun,
  LiteratureRunEvent,
  LiteratureRunResults,
} from '../api/types';
import { formatErrorMessage } from '../utils/errors';

const TERMINAL_STATUSES = new Set(['success', 'partial', 'failed', 'cancelled']);

type LiteratureResearchPanelProps = {
  initialQuery?: string;
};

export function LiteratureResearchPanel({ initialQuery = '' }: LiteratureResearchPanelProps) {
  const [query, setQuery] = useState(initialQuery);
  const [yearFrom, setYearFrom] = useState('2018');
  const [directionCount, setDirectionCount] = useState('5');
  const [candidatesPerDirection, setCandidatesPerDirection] = useState('25');
  const [topN, setTopN] = useState('1');
  const [includeFulltext, setIncludeFulltext] = useState(true);
  const [resumeId, setResumeId] = useState('');
  const [run, setRun] = useState<LiteratureRun>();
  const [events, setEvents] = useState<LiteratureRunEvent[]>([]);
  const [results, setResults] = useState<LiteratureRunResults>();
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => setQuery(initialQuery), [initialQuery]);

  useEffect(() => {
    if (!run || TERMINAL_STATUSES.has(run.status)) {
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const [updated, eventPage, updatedResults] = await Promise.all([
          getLiteratureRun(run.id),
          getLiteratureRunEvents(run.id),
          getLiteratureRunResults(run.id),
        ]);
        if (!active) return;
        setRun(updated);
        setEvents(eventPage.items);
        setResults(updatedResults);
        setError('');
        if (!TERMINAL_STATUSES.has(updated.status)) {
          timer = setTimeout(() => void poll(), 1000);
        }
      } catch (caught) {
        if (!active) return;
        setError(formatErrorMessage('刷新文献任务失败', caught));
        timer = setTimeout(() => void poll(), 3000);
      }
    };
    timer = setTimeout(() => void poll(), 500);
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [run?.id, run?.status]);

  async function startRun() {
    const trimmed = query.trim();
    const year = Number(yearFrom);
    const directions = Number(directionCount);
    const candidates = Number(candidatesPerDirection);
    const selected = Number(topN);
    if (!trimmed) {
      setError('请输入无人机研究关键词。');
      return;
    }
    if (
      !Number.isInteger(year) ||
      !Number.isInteger(directions) ||
      !Number.isInteger(candidates) ||
      !Number.isInteger(selected) ||
      directions < 1 ||
      candidates < 1 ||
      selected < 1 ||
      selected > candidates
    ) {
      setError('请检查年份、方向数、候选数和每方向入选数。');
      return;
    }
    setSubmitting(true);
    setError('');
    setEvents([]);
    setResults(undefined);
    try {
      const created = await createLiteratureRun({
        query: trimmed,
        year_from: year,
        direction_count: directions,
        candidates_per_direction: candidates,
        top_n_per_direction: selected,
        include_fulltext: includeFulltext,
      });
      setRun(created);
      setResumeId(created.id);
    } catch (caught) {
      setError(formatErrorMessage('创建文献任务失败', caught));
    } finally {
      setSubmitting(false);
    }
  }

  async function resumeRun() {
    const id = resumeId.trim();
    if (!id) {
      setError('请输入文献任务 ID。');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const [loaded, eventPage, loadedResults] = await Promise.all([
        getLiteratureRun(id),
        getLiteratureRunEvents(id),
        getLiteratureRunResults(id),
      ]);
      setRun(loaded);
      setEvents(eventPage.items);
      setResults(loadedResults);
      setQuery(loaded.query);
    } catch (caught) {
      setError(formatErrorMessage('加载文献任务失败', caught));
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelRun() {
    if (!run) return;
    try {
      const cancelled = await cancelLiteratureRun(run.id);
      setRun(cancelled);
    } catch (caught) {
      setError(formatErrorMessage('取消文献任务失败', caught));
    }
  }

  return (
    <section>
      <section className="card">
        <p className="muted">
          提交后由独立 Worker 执行 IEEE 登录检索、PDF 获取、正文提取和证据分析；关闭或刷新页面不会中断任务。
        </p>
        <div className="form-grid literature-form">
          <label htmlFor="literature-query">无人机关键词</label>
          <input
            id="literature-query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="例如：无人机 多传感器融合 避障 激光雷达 夜间"
          />
          <label htmlFor="literature-year">最早年份</label>
          <input id="literature-year" type="number" value={yearFrom} onChange={(event) => setYearFrom(event.target.value)} />
          <label htmlFor="literature-directions">研究方向数</label>
          <input id="literature-directions" type="number" min="1" max="10" value={directionCount} onChange={(event) => setDirectionCount(event.target.value)} />
          <label htmlFor="literature-candidates">每方向候选数</label>
          <input id="literature-candidates" type="number" min="1" max="100" value={candidatesPerDirection} onChange={(event) => setCandidatesPerDirection(event.target.value)} />
          <label htmlFor="literature-top-n">每方向入选数</label>
          <input id="literature-top-n" type="number" min="1" max="5" value={topN} onChange={(event) => setTopN(event.target.value)} />
          <span>获取全文</span>
          <label className="checkbox-label">
            <input type="checkbox" checked={includeFulltext} onChange={(event) => setIncludeFulltext(event.target.checked)} />
            保存 PDF、按页正文并校验证据
          </label>
        </div>
        <div className="button-row">
          <button type="button" onClick={() => void startRun()} disabled={submitting}>
            开始文献研究
          </button>
        </div>
        <div className="inline-form resume-form">
          <input aria-label="文献任务 ID" value={resumeId} onChange={(event) => setResumeId(event.target.value)} placeholder="粘贴任务 UUID 继续查看" />
          <button type="button" onClick={() => void resumeRun()} disabled={submitting}>加载任务</button>
        </div>
        {error ? <p className="error-text" role="alert">{error}</p> : null}
      </section>

      {run ? (
        <section className="card status-line">
          <div className="card-header">
            <div>
              <h2>任务进度</h2>
              <p className="compact muted">Run ID: {run.id}</p>
            </div>
            <span className={`badge status-${run.status}`}>{run.status}</span>
          </div>
          <progress max={Math.max(1, run.progress_total)} value={run.progress_current} />
          <p>{run.progress_message || run.current_stage}</p>
          <p className="compact muted">阶段：{run.current_stage} · {run.progress_current}/{run.progress_total}</p>
          {run.status === 'waiting_for_login' ? (
            <p className="login-notice">请在 Worker 弹出的 Chromium 窗口完成 IEEE 机构登录，登录成功后任务会自动继续。</p>
          ) : null}
          {run.error_message ? <p className="error-text">{run.error_message}</p> : null}
          {!TERMINAL_STATUSES.has(run.status) ? (
            <button type="button" onClick={() => void cancelRun()}>取消任务</button>
          ) : null}
        </section>
      ) : null}

      {events.length ? (
        <section className="card">
          <h2>进度时间线</h2>
          <ol className="timeline">
            {events.map((event) => (
              <li key={event.id}>
                <span className="timeline-marker" />
                <div>
                  <strong>{event.stage} · {event.event_type}</strong>
                  <p>{event.message}</p>
                  <span className="muted compact">{new Date(event.created_at).toLocaleString()}</span>
                </div>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      {results?.items.map((item) => <LiteratureResultCard item={item} key={item.selection.id} />)}

      {results?.run.final_report_markdown ? (
        <section className="card">
          <h2>最终研究报告</h2>
          <pre>{results.run.final_report_markdown}</pre>
          <ArtifactLinks artifacts={results.run_artifacts} />
        </section>
      ) : null}
    </section>
  );
}

function LiteratureResultCard({ item }: { item: LiteratureResultItem }) {
  return (
    <article className="card literature-result">
      <div className="card-header">
        <div>
          <p className="eyebrow">{item.selection.direction_id} · Rank {item.selection.selected_rank ?? 'n/a'}</p>
          <h2>{item.paper.title}</h2>
        </div>
        <span className="badge">{item.selection.analysis_status}</span>
      </div>
      <dl className="kv-grid">
        <div><dt>出版物</dt><dd>{item.paper.publication_title || '未知'}</dd></div>
        <div><dt>年份</dt><dd>{item.paper.publication_year || '未知'}</dd></div>
        <div><dt>引用</dt><dd>{item.paper.citation_count}</dd></div>
        <div><dt>DOI</dt><dd>{item.paper.doi || '无'}</dd></div>
      </dl>
      {item.paper.document_url ? <p><a href={item.paper.document_url} target="_blank" rel="noreferrer">打开 IEEE 原文</a></p> : null}
      <h3>匹配方式</h3>
      <p>{item.selection.match_how || '分析尚未完成。'}</p>
      <h3>可用价值</h3>
      <p>{item.selection.match_use || '分析尚未完成。'}</p>
      <h3>结论</h3>
      <p>{item.selection.conclusion || '分析尚未完成。'}</p>
      {item.evidence.length ? (
        <div>
          <h3>原文证据</h3>
          <ul className="evidence-list">
            {item.evidence.map((evidence) => (
              <li key={evidence.id}>
                <span className="badge">{evidence.match_level}</span>{' '}
                <strong>{evidence.matched_term}</strong> · {evidence.evidence_source}
                {evidence.page_number ? ` 第 ${evidence.page_number} 页` : ''}
                <p>{evidence.evidence_text || '证据未通过原文子串校验，已降级。'}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <ArtifactLinks artifacts={item.artifacts} />
    </article>
  );
}

function ArtifactLinks({ artifacts }: { artifacts: LiteratureRunResults['run_artifacts'] }) {
  if (!artifacts.length) return null;
  return (
    <div className="artifact-links">
      <h3>可溯源制品</h3>
      {artifacts.map((artifact) => (
        <a href={getLiteratureArtifactUrl(artifact.id)} key={artifact.id}>
          {artifact.artifact_type} {artifact.byte_size ? `(${artifact.byte_size} bytes)` : ''}
        </a>
      ))}
    </div>
  );
}
