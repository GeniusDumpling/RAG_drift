import { FormEvent, useEffect, useMemo, useState } from 'react';

import { listContents, listJobs, listRuns, listSources } from '../api/client';
import { itemTypeLabel } from '../utils/links';
import type { ContentListItem, CrawlJob, CrawlRun, Page, SourceSite } from '../api/types';
import { formatErrorMessage } from '../utils/errors';

const EMPTY_STATE_COPY = '暂无数据。请先启动后端并运行 demo seed 脚本。';
const LOADING_STATE_COPY = '正在加载总览数据...';

type DashboardData = {
  sources?: Page<SourceSite>;
  jobs?: Page<CrawlJob>;
  runs?: Page<CrawlRun>;
  contents?: Page<ContentListItem>;
};

type DashboardPageProps = {
  onSearch?: (query: string) => void;
};

export function DashboardPage({ onSearch }: DashboardPageProps) {
  const [data, setData] = useState<DashboardData>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    setError('');
    Promise.allSettled([listSources(), listJobs(), listRuns(), listContents({ limit: 10 })]).then(
      ([sourcesResult, jobsResult, runsResult, contentsResult]) => {
        if (ignore) {
          return;
        }
        const firstFailure = [sourcesResult, jobsResult, runsResult, contentsResult].find(
          (result): result is PromiseRejectedResult => result.status === 'rejected',
        );
        setData({
          sources: sourcesResult.status === 'fulfilled' ? sourcesResult.value : undefined,
          jobs: jobsResult.status === 'fulfilled' ? jobsResult.value : undefined,
          runs: runsResult.status === 'fulfilled' ? runsResult.value : undefined,
          contents: contentsResult.status === 'fulfilled' ? contentsResult.value : undefined,
        });
        setError(firstFailure ? formatErrorMessage('无法加载总览数据', firstFailure.reason) : '');
        setLoading(false);
      },
    );
    return () => {
      ignore = true;
    };
  }, []);

  const metrics = useMemo(() => {
    const dayAgo = Date.now() - 24 * 60 * 60 * 1000;
    return {
      activeSources: data.sources?.items.filter((source) => source.active).length,
      enabledJobs: data.jobs?.items.filter((job) => job.enabled).length,
      runs24h: data.runs?.items.filter((run) => Date.parse(run.created_at) >= dayAgo).length,
      recentItems: data.contents?.total,
    };
  }, [data]);

  const hasData = Boolean(
    data.sources?.items.length || data.jobs?.items.length || data.runs?.items.length || data.contents?.items.length,
  );

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (trimmed) {
      onSearch?.(trimmed);
    }
  }

  return (
    <section>
      <div className="page-title">
        <p className="eyebrow">工作台</p>
        <h1>RAG系统总览</h1>
        <p className="muted">观察数据源状态、爬取执行、索引内容和证据。</p>
      </div>

      <div className="grid metric-grid">
        <div className="card metric-card">
          <span className="muted">活跃 Sources</span>
          <strong>{metrics.activeSources ?? '—'}</strong>
        </div>
        <div className="card metric-card">
          <span className="muted">启用 Jobs</span>
          <strong>{metrics.enabledJobs ?? '—'}</strong>
        </div>
        <div className="card metric-card">
          <span className="muted">24h Runs</span>
          <strong>{metrics.runs24h ?? '—'}</strong>
        </div>
        <div className="card metric-card">
          <span className="muted">最近 Items</span>
          <strong>{metrics.recentItems ?? '—'}</strong>
        </div>
      </div>

      <form className="card search-entry" onSubmit={handleSubmit}>
        <label htmlFor="dashboard-query">检索已索引内容</label>
        <div className="inline-form">
          <input
            id="dashboard-query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="telemetry disable procedure"
          />
          <button type="submit">打开检索</button>
        </div>
      </form>

      {error ? (
        <p className="card error-text" role="alert">
          {error}
        </p>
      ) : null}
      {loading ? (
        <p className="card status-line" role="status" aria-live="polite">
          {LOADING_STATE_COPY}
        </p>
      ) : null}

      {!loading && !error && !hasData ? <p className="card empty-state">{EMPTY_STATE_COPY}</p> : null}

      <div className="grid two-column">
        <section className="card">
          <h2>最近运行</h2>
          {data.runs?.items.length ? (
            <ul className="dense-list">
              {data.runs.items.slice(0, 5).map((run) => (
                <li key={run.id}>
                  <span className="badge">{run.status}</span>
                  <span>{run.id}</span>
                  <span className="muted">{new Date(run.created_at).toLocaleString()}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">
              {loading ? LOADING_STATE_COPY : error ? '最近 Runs 暂不可用：API 请求失败。' : EMPTY_STATE_COPY}
            </p>
          )}
        </section>

        <section className="card">
          <h2>最新内容</h2>
          {data.contents?.items.length ? (
            <ul className="dense-list">
              {data.contents.items.slice(0, 5).map((item) => (
                <li key={item.id}>
                  <span className="badge">{itemTypeLabel(item.item_type)}</span>
                  <span>{item.title || item.canonical_url}</span>
                  <span className="muted">{item.author_name || '未知作者'}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">
              {loading
                ? LOADING_STATE_COPY
                : error
                  ? '最近 Content 暂不可用：API 请求失败。'
                  : EMPTY_STATE_COPY}
            </p>
          )}
        </section>
      </div>
    </section>
  );
}
